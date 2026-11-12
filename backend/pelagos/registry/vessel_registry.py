"""Pelagos Reusable Multi-Source Vessel Registry Service (Stage F1.2).

Implements canonical identity resolution (IMO primary lifetime key, MMSI temporal tracking),
field-level provenance, deterministic conflict resolution, and operational estimate segregation.
Traces to REQ-FR-DII-002, REQ-FR-OMR-002, and REQ-NFR-DIP-001.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from pelagos.models import (
    VerificationStatus,
    Vessel,
    VesselAttributeProvenance,
    VesselMmsiHistory,
    VesselOperationalEstimates,
    validate_imo,
)

logger = logging.getLogger(__name__)

# Verification rank hierarchy for deterministic conflict resolution
VERIFICATION_RANKS: Dict[str, int] = {
    VerificationStatus.DIRECTLY_VERIFIED: 40,
    VerificationStatus.SOURCE_REPORTED: 30,
    VerificationStatus.INFERRED: 20,
    VerificationStatus.UNVERIFIED: 10,
    VerificationStatus.CONFLICTING: -1,
    VerificationStatus.UNAVAILABLE: 0,
}

# Domain-specific source preference weights when verification ranks are tied
DOMAIN_SOURCE_PREFERENCES: Dict[str, Set[str]] = {
    "gross_tonnage": {"eu_thetis_mrv", "classnk", "dnv", "lloyds_register", "class survey"},
    "deadweight_tonnage": {"classnk", "dnv", "lloyds_register", "class survey", "balticshipping"},
    "length_meters": {"uscg_psix", "classnk", "dnv", "lloyds_register", "class survey"},
    "width_meters": {"uscg_psix", "classnk", "dnv", "lloyds_register", "class survey"},
    "draft_max_meters": {"classnk", "dnv", "lloyds_register", "class survey"},
}


def classify_attribute_difference(
    attribute_name: str,
    val1: Any,
    val2: Any,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Attribute-aware classification of difference between two observations.

    Distinguishes between:
    - None: values are identical or equivalent.
    - 'rounding_difference': small numerical deviation from truncation, float precision, or metric/long ton conversion.
    - 'measurement_definition_difference': differences arising from distinct engineering definitions (e.g. LOA vs LBP,
      summer vs winter/tropical loadline DWT, ITC 1969 vs national tonnage, scantling vs summer draft).
    - 'reporting_period_difference': valid temporal changes over vessel lifecycle (e.g. re-flagging, renaming,
      keel-laying vs delivery year, lightweight alteration post-scrubber drydock).
    - 'genuine_conflict': irreconcilable divergence exceeding permissible physical bounds.

    Returns:
        (is_conflict, difference_category, reason)
    """
    if val1 is None or val2 is None:
        return False, None, None

    attr = attribute_name.lower().strip()
    s1 = str(val1).strip()
    s2 = str(val2).strip()

    if s1.lower() == s2.lower():
        return False, None, None

    # Numeric physical particulars
    if attr in {
        "dwt_tonnage",
        "gross_tonnage",
        "net_tonnage",
        "length_meters",
        "width_meters",
        "draft_max_meters",
        "air_draft_meters",
        "grain_cubic_meters",
    }:
        try:
            n1 = float(val1)
            n2 = float(val2)
        except (ValueError, TypeError):
            return True, "genuine_conflict", f"Unparseable numeric comparison for {attribute_name}: '{val1}' vs '{val2}'"

        diff = abs(n1 - n2)
        max_val = max(abs(n1), abs(n2), 1e-6)
        rel_diff = diff / max_val

        if attr == "dwt_tonnage":
            # DWT: <= 0.1% is rounding (e.g. 50-80 MT out of 70k MT)
            if rel_diff <= 0.001:
                return False, "rounding_difference", f"DWT diff ({diff:.1f} MT, {rel_diff*100:.3f}%) within rounding/tonnage conversion tolerance."
            # 0.1% - 1.5% is measurement definition (summer loadline vs tropical/winter, or survey lightweight recalibration)
            if rel_diff <= 0.015:
                return False, "measurement_definition_difference", f"DWT diff ({diff:.1f} MT, {rel_diff*100:.2f}%) within standard loadline definition variance."
            # > 1.5% is genuine conflict
            return True, "genuine_conflict", f"DWT divergence ({diff:.1f} MT, {rel_diff*100:.2f}%) exceeds 1.5% threshold."

        if attr in {"gross_tonnage", "net_tonnage"}:
            if rel_diff <= 0.001:
                return False, "rounding_difference", f"GT diff ({diff:.0f}, {rel_diff*100:.3f}%) within rounding tolerance."
            if rel_diff <= 0.02:
                return False, "measurement_definition_difference", f"GT diff ({diff:.0f}, {rel_diff*100:.2f}%) reflects ITC 1969 vs canal/national survey definition."
            return True, "genuine_conflict", f"GT divergence ({diff:.0f}, {rel_diff*100:.2f}%) exceeds 2.0% threshold."

        if attr == "length_meters":
            # LOA: <= 0.1m is rounding
            if diff <= 0.1:
                return False, "rounding_difference", f"Length diff ({diff:.2f}m) within rounding tolerance."
            # LBP is typically 3-6% less than LOA (e.g. 180m LBP vs 190m LOA)
            if diff <= 12.0 and rel_diff <= 0.06:
                return False, "measurement_definition_difference", f"Length diff ({diff:.2f}m, {rel_diff*100:.2f}%) aligns with LOA vs LBP definition."
            return True, "genuine_conflict", f"Length divergence ({diff:.2f}m, {rel_diff*100:.2f}%) exceeds LBP/LOA envelope."

        if attr == "width_meters":
            if diff <= 0.1:
                return False, "rounding_difference", f"Beam diff ({diff:.2f}m) within rounding tolerance."
            if diff <= 0.5 and rel_diff <= 0.02:
                return False, "measurement_definition_difference", f"Beam diff ({diff:.2f}m) aligns with molded breadth vs extreme breadth."
            return True, "genuine_conflict", f"Beam divergence ({diff:.2f}m) exceeds 0.5m."

        if attr == "draft_max_meters":
            if diff <= 0.05:
                return False, "rounding_difference", f"Draft diff ({diff:.2f}m) within rounding tolerance."
            if diff <= 0.4:
                return False, "measurement_definition_difference", f"Draft diff ({diff:.2f}m) aligns with design draft vs scantling draft."
            return True, "genuine_conflict", f"Draft divergence ({diff:.2f}m) exceeds 0.4m."

        # Generic numeric
        if rel_diff <= 0.01:
            return False, "rounding_difference", f"Numeric diff ({diff:.2f}, {rel_diff*100:.2f}%) within 1% tolerance."
        return True, "genuine_conflict", f"Numeric divergence ({diff:.2f}, {rel_diff*100:.2f}%) exceeds 1%."

    # Built year integer
    if attr == "built_year":
        try:
            y1 = int(val1)
            y2 = int(val2)
            y_diff = abs(y1 - y2)
            if y_diff == 0:
                return False, None, None
            if y_diff == 1:
                return False, "reporting_period_difference", f"Build year diff ({y1} vs {y2}) aligns with keel-laid vs delivery year."
            return True, "genuine_conflict", f"Build year divergence ({y1} vs {y2}) exceeds 1 year."
        except (ValueError, TypeError):
            return True, "genuine_conflict", f"Unparseable build year: '{val1}' vs '{val2}'"

    # Flag country
    if attr in {"flag_country", "flag_code"}:
        return False, "reporting_period_difference", f"Flag change ('{s1}' vs '{s2}') reflects re-flagging event across reporting periods."

    # Vessel name
    if attr == "vessel_name":
        return False, "reporting_period_difference", f"Vessel name change ('{s1}' vs '{s2}') reflects renaming event."

    # Vessel type / class
    if attr in {"vessel_type", "vessel_class"}:
        s1_low = s1.lower()
        s2_low = s2.lower()
        bulk_tokens = {"bulk", "carrier", "supramax", "panamax", "kamsarmax", "handysize", "capesize", "newcastlemax", "ultramax"}
        if any(t in s1_low for t in bulk_tokens) and any(t in s2_low for t in bulk_tokens):
            return False, "measurement_definition_difference", f"Bulk subtype classification variance ('{s1}' vs '{s2}')."
        return True, "genuine_conflict", f"Contradictory vessel type classification ('{s1}' vs '{s2}')."

    # Default fallback
    return True, "genuine_conflict", f"Contradictory attribute value for {attribute_name}: '{s1}' vs '{s2}'"


class VesselRegistryError(Exception):
    """Base exception for vessel registry operations."""
    pass


class InvalidImoError(VesselRegistryError):
    """Raised when an IMO number fails format or modulo-10 check-digit validation."""
    pass


class OverlappingMmsiError(VesselRegistryError):
    """Raised when conflicting temporal intervals exist for an MMSI assignment."""
    pass


class MalformedVesselRecordError(VesselRegistryError):
    """Raised when a vessel payload fails structural consistency requirements."""
    pass


def _ensure_utc(dt: Optional[Union[datetime, str]]) -> Optional[datetime]:
    """Ensure datetime is UTC-aware for safe cross-temporal comparisons.

    Parses ISO-8601 strings if provided, converts naive datetimes to UTC.
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        s = dt.strip()
        if not s:
            return None
        try:
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class VesselRegistry:
    """Enterprise multi-source maritime vessel registry connector."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def check_imo(imo: Optional[Union[str, int]]) -> Tuple[bool, Optional[str], Optional[str]]:
        """Validate IMO format and checksum."""
        return validate_imo(imo)

    def resolve_canonical_vessel(
        self,
        imo: Optional[Union[str, int]] = None,
        mmsi: Optional[Union[str, int]] = None,
    ) -> Optional[Vessel]:
        """Resolve canonical Vessel record by verified IMO first, then by primary/historical MMSI.

        Args:
            imo: Candidate IMO number.
            mmsi: Candidate MMSI number.

        Returns:
            Resolved Vessel entity if matched, else None.
        """
        clean_imo: Optional[str] = None
        if imo is not None:
            is_valid, c_imo, err = validate_imo(imo)
            if not is_valid:
                raise InvalidImoError(f"Cannot resolve vessel with invalid IMO: {err}")
            clean_imo = c_imo

        if clean_imo:
            vessel = self.db.query(Vessel).filter(Vessel.imo == clean_imo).first()
            if vessel:
                return vessel

        clean_mmsi = str(mmsi).strip() if mmsi is not None and str(mmsi).strip() else None
        if clean_mmsi:
            # 1. Match on current primary MMSI
            vessel = self.db.query(Vessel).filter(Vessel.mmsi == clean_mmsi).first()
            if vessel and (clean_imo is None or vessel.imo is None or vessel.imo == clean_imo):
                return vessel

            # 2. Match on historical MMSI tracking table
            hist = (
                self.db.query(VesselMmsiHistory)
                .filter(VesselMmsiHistory.mmsi == clean_mmsi)
                .order_by(desc(VesselMmsiHistory.retrieved_at))
                .first()
            )
            if hist and hist.vessel and (clean_imo is None or hist.vessel.imo is None or hist.vessel.imo == clean_imo):
                return hist.vessel

        return None

    def validate_mmsi_overlap(
        self,
        vessel_id: int,
        mmsi: str,
        valid_from: Optional[Union[datetime, str]],
        valid_to: Optional[Union[datetime, str]],
    ) -> None:
        """Ensure no conflicting active assignments exist for an MMSI.

        Enforces two inviolable invariants:
        1. Same-Vessel Integrity:
           A single vessel cannot have multiple active MMSI assignments during overlapping intervals.
        2. Cross-Vessel Integrity:
           The same MMSI cannot be actively assigned to two distinct vessels during overlapping intervals.
           Legitimate sequential reassignment (Vessel A terminates before Vessel B commences) is supported.
           Unbounded/unknown intervals across distinct vessels fail closed to prevent silent telemetry aliasing.
        """
        clean_mmsi = str(mmsi).strip()
        vf = _ensure_utc(valid_from)
        vt = _ensure_utc(valid_to)

        if vf and vt and vf >= vt:
            raise OverlappingMmsiError(
                f"Invalid temporal interval for MMSI {clean_mmsi}: valid_from ({vf}) must precede valid_to ({vt})."
            )

        # 1. Same-vessel check: two different MMSIs on this vessel cannot overlap
        same_vessel_histories = (
            self.db.query(VesselMmsiHistory)
            .filter(VesselMmsiHistory.vessel_id == vessel_id)
            .all()
        )
        for h in same_vessel_histories:
            if h.mmsi == clean_mmsi:
                continue

            hf = _ensure_utc(h.valid_from)
            ht = _ensure_utc(h.valid_to)

            # If both are unbounded open-ended current assignments, conflict exists
            if vt is None and ht is None:
                raise OverlappingMmsiError(
                    f"Vessel {vessel_id} already has an active open-ended MMSI assignment ({h.mmsi}). "
                    f"Cannot assign concurrent open-ended MMSI ({clean_mmsi}) without explicit validity bounds."
                )

            # If bounded intervals overlap
            if vf and vt and hf and ht:
                if max(vf, hf) < min(vt, ht):
                    raise OverlappingMmsiError(
                        f"MMSI {clean_mmsi} interval [{valid_from}, {valid_to}] overlaps with "
                        f"existing MMSI {h.mmsi} interval [{h.valid_from}, {h.valid_to}] on vessel {vessel_id}."
                    )

        # 2. Cross-vessel check: the same MMSI cannot be actively assigned to two different vessels with overlapping intervals
        cross_vessel_histories = (
            self.db.query(VesselMmsiHistory)
            .filter(
                VesselMmsiHistory.mmsi == clean_mmsi,
                VesselMmsiHistory.vessel_id != vessel_id,
            )
            .all()
        )
        for ch in cross_vessel_histories:
            chf = _ensure_utc(ch.valid_from)
            cht = _ensure_utc(ch.valid_to)

            # Both open-ended -> conflict
            if vt is None and cht is None:
                raise OverlappingMmsiError(
                    f"Cross-vessel MMSI conflict: MMSI {clean_mmsi} is actively assigned to vessel {ch.vessel_id} "
                    f"without termination date. Concurrent assignment to vessel {vessel_id} is rejected."
                )

            # Both bounded -> check interval overlap
            if vf and vt and chf and cht:
                if max(vf, chf) < min(vt, cht):
                    raise OverlappingMmsiError(
                        f"Cross-vessel MMSI conflict: MMSI {clean_mmsi} interval [{valid_from}, {valid_to}] on vessel {vessel_id} "
                        f"overlaps with interval [{ch.valid_from}, {ch.valid_to}] on vessel {ch.vessel_id}."
                    )

            # Incoming is open-ended, existing is bounded
            elif vt is None and cht and vf:
                if vf < cht:
                    raise OverlappingMmsiError(
                        f"Cross-vessel MMSI conflict: MMSI {clean_mmsi} on vessel {vessel_id} commences ({valid_from}) "
                        f"prior to release by vessel {ch.vessel_id} ({ch.valid_to})."
                    )

            # Incoming is bounded, existing is open-ended
            elif cht is None and vt and chf:
                if chf < vt:
                    raise OverlappingMmsiError(
                        f"Cross-vessel MMSI conflict: MMSI {clean_mmsi} on prior vessel {ch.vessel_id} commences ({ch.valid_from}) "
                        f"prior to termination by vessel {vessel_id} ({valid_to})."
                    )

            # Unbounded/unrecorded dates on either side -> fail closed
            elif (vf is None and vt is None) or (chf is None and cht is None):
                raise OverlappingMmsiError(
                    f"Cross-vessel MMSI conflict: MMSI {clean_mmsi} is assigned to both vessel {vessel_id} and vessel {ch.vessel_id} "
                    "with unresolved temporal validity intervals. Unbounded dates cannot be assumed non-overlapping."
                )

    def record_mmsi(
        self,
        vessel_id: int,
        mmsi: str,
        flag_country: Optional[str] = None,
        valid_from: Optional[Union[datetime, str]] = None,
        valid_to: Optional[Union[datetime, str]] = None,
        source_name: str = "registry",
        source_record_id: Optional[str] = None,
        retrieved_at: Optional[datetime] = None,
        commit: bool = False,
    ) -> VesselMmsiHistory:
        """Add or update an MMSI association in the temporal history table."""
        clean_mmsi = str(mmsi).strip()
        if not clean_mmsi:
            raise ValueError("MMSI must not be empty.")

        self.validate_mmsi_overlap(vessel_id, clean_mmsi, valid_from, valid_to)

        now_utc = retrieved_at or datetime.now(timezone.utc)
        vf_dt = _ensure_utc(valid_from)
        vt_dt = _ensure_utc(valid_to)

        existing = (
            self.db.query(VesselMmsiHistory)
            .filter(
                VesselMmsiHistory.vessel_id == vessel_id,
                VesselMmsiHistory.mmsi == clean_mmsi,
            )
            .first()
        )
        if existing:
            if flag_country:
                existing.flag_country = flag_country
            if vf_dt:
                existing.valid_from = vf_dt
            if vt_dt:
                existing.valid_to = vt_dt
            existing.retrieved_at = now_utc
            if commit:
                self.db.commit()
            return existing

        hist = VesselMmsiHistory(
            vessel_id=vessel_id,
            mmsi=clean_mmsi,
            flag_country=flag_country,
            valid_from=vf_dt,
            valid_to=vt_dt,
            source_name=source_name,
            source_record_id=source_record_id,
            retrieved_at=now_utc,
        )
        self.db.add(hist)
        if commit:
            self.db.commit()
            self.db.refresh(hist)
        return hist

    def normalize_attribute(
        self, attribute_name: str, value: Any
    ) -> Tuple[Optional[str], Any, Optional[str]]:
        """Normalize raw attribute value into string payload and typed value."""
        if value is None:
            return None, None, None

        raw_str = str(value)
        attr = attribute_name.lower().strip()

        if attr in {
            "dwt_tonnage",
            "gross_tonnage",
            "net_tonnage",
            "length_meters",
            "width_meters",
            "draft_max_meters",
            "air_draft_meters",
            "grain_cubic_meters",
        }:
            try:
                num = float(value)
                if not math.isfinite(num) or num < 0:
                    return raw_str, None, "numeric_invalid_finite"
                return raw_str, round(num, 2), "numeric_float"
            except (ValueError, TypeError):
                return raw_str, None, "numeric_parse_failed"

        if attr in {"built_year", "vessel_type_code", "holds_count", "hatches_count"}:
            try:
                num_int = int(value)
                return raw_str, num_int, "numeric_int"
            except (ValueError, TypeError):
                return raw_str, None, "int_parse_failed"

        if attr in {"vessel_name", "vessel_type", "vessel_class", "flag_country", "flag_code"}:
            s = str(value).strip()
            return raw_str, s, "string_strip"

        return raw_str, value, "verbatim"

    def record_provenance(
        self,
        vessel_id: int,
        attribute_name: str,
        raw_value: Any,
        normalized_value: Any,
        source_name: str,
        source_record_id: Optional[str] = None,
        source_url: Optional[str] = None,
        verification_status: str = VerificationStatus.SOURCE_REPORTED,
        observation_date: Optional[datetime] = None,
        retrieved_at: Optional[datetime] = None,
        normalization_method: Optional[str] = None,
        commit: bool = False,
    ) -> Tuple[VesselAttributeProvenance, bool]:
        """Record an immutable field-level provenance observation and audit conflicts."""
        if verification_status not in VerificationStatus.ALL:
            verification_status = VerificationStatus.UNVERIFIED

        now_utc = retrieved_at or datetime.now(timezone.utc)
        raw_s = str(raw_value) if raw_value is not None else None
        norm_s = str(normalized_value) if normalized_value is not None else None

        existing = (
            self.db.query(VesselAttributeProvenance)
            .filter(
                VesselAttributeProvenance.vessel_id == vessel_id,
                VesselAttributeProvenance.attribute_name == attribute_name,
                VesselAttributeProvenance.source_name == source_name,
                VesselAttributeProvenance.source_record_id == source_record_id,
            )
            .first()
        )

        # Check existing observations from other sources for conflict
        prior_observations = (
            self.db.query(VesselAttributeProvenance)
            .filter(
                VesselAttributeProvenance.vessel_id == vessel_id,
                VesselAttributeProvenance.attribute_name == attribute_name,
            )
            .all()
        )

        is_conflict = False
        difference_category = None
        conflict_detail = None
        if normalized_value is not None:
            for po in prior_observations:
                if po.source_name == source_name and po.source_record_id == source_record_id:
                    continue
                if po.normalized_value is not None:
                    c_flag, c_cat, c_reason = classify_attribute_difference(
                        attribute_name, po.normalized_value, normalized_value
                    )
                    if c_flag:
                        is_conflict = True
                        po.is_conflicting = True
                        difference_category = c_cat
                        conflict_detail = c_reason

        if existing:
            existing.raw_value = raw_s
            existing.normalized_value = norm_s
            existing.verification_status = verification_status
            existing.source_url = source_url
            existing.retrieved_at = now_utc
            existing.is_conflicting = is_conflict or existing.is_conflicting
            if normalization_method:
                existing.normalization_method = normalization_method
            if commit:
                self.db.commit()
            return existing, is_conflict

        prov = VesselAttributeProvenance(
            vessel_id=vessel_id,
            attribute_name=attribute_name,
            raw_value=raw_s,
            normalized_value=norm_s,
            source_name=source_name,
            source_record_id=source_record_id,
            source_url=source_url,
            verification_status=verification_status,
            observation_date=observation_date,
            retrieved_at=now_utc,
            is_conflicting=is_conflict,
            normalization_method=normalization_method,
        )
        self.db.add(prov)
        if commit:
            self.db.commit()
            self.db.refresh(prov)
        return prov, is_conflict

    def select_canonical_value(
        self,
        vessel_id: int,
        attribute_name: str,
        incoming_value: Any,
        incoming_status: str,
        incoming_source: str,
    ) -> Tuple[Any, str]:
        """Deterministically select canonical value across conflicting provenance records.

        Priority rule:
        1. Higher verification status rank wins (DIRECTLY_VERIFIED > SOURCE_REPORTED > INFERRED > UNVERIFIED).
        2. If ranks are tied, check domain source preferences.
        3. If unresolved genuine conflict exists without authoritative tie-breaker, leave canonical value unresolved (None)
           and return VerificationStatus.CONFLICTING.
        4. If still tied, the incoming value is used if newer or equal.
        """
        all_provs = (
            self.db.query(VesselAttributeProvenance)
            .filter(
                VesselAttributeProvenance.vessel_id == vessel_id,
                VesselAttributeProvenance.attribute_name == attribute_name,
            )
            .order_by(desc(VesselAttributeProvenance.retrieved_at))
            .all()
        )

        if not all_provs:
            return incoming_value, incoming_status

        # If a directly verified observation exists, it wins authoritatively over lower-ranked conflicts
        directly_verified = [
            p for p in all_provs
            if p.verification_status == VerificationStatus.DIRECTLY_VERIFIED
        ]
        if directly_verified:
            best_p = directly_verified[0]
            return best_p.normalized_value, VerificationStatus.DIRECTLY_VERIFIED

        # Check for active unresolved conflicts
        conflicts = [p for p in all_provs if p.is_conflicting]
        if conflicts:
            # Check domain source preference tie-breaker
            pref_sources = DOMAIN_SOURCE_PREFERENCES.get(attribute_name.lower(), set())
            pref_matches = [
                p for p in all_provs
                if any(pref in (p.source_name or "").lower() for pref in pref_sources)
            ]
            if len(pref_matches) == 1:
                best_p = pref_matches[0]
                return best_p.normalized_value, best_p.verification_status

            # Contradictory evidence cannot be resolved authoritatively: leave canonical value None
            logger.warning(
                "Unresolved conflicting observations for vessel %s attribute '%s'. Leaving canonical value unresolved.",
                vessel_id,
                attribute_name,
            )
            return None, VerificationStatus.CONFLICTING

        inc_rank = VERIFICATION_RANKS.get(incoming_status, 0)
        best_val = incoming_value
        best_rank = inc_rank
        best_source = incoming_source

        pref_sources = DOMAIN_SOURCE_PREFERENCES.get(attribute_name.lower(), set())

        for p in all_provs:
            p_rank = VERIFICATION_RANKS.get(p.verification_status, 0)
            if p_rank > best_rank:
                best_rank = p_rank
                best_val = p.normalized_value
                best_source = p.source_name
            elif p_rank == best_rank:
                # Rank tie: check source preference
                p_src_lower = p.source_name.lower()
                best_src_lower = best_source.lower()
                p_pref = any(pref in p_src_lower for pref in pref_sources)
                b_pref = any(pref in best_src_lower for pref in pref_sources)
                if p_pref and not b_pref:
                    best_rank = p_rank
                    best_val = p.normalized_value
                    best_source = p.source_name

        return best_val, incoming_status

    def upsert_vessel(
        self,
        vessel_data: Dict[str, Any],
        default_source_name: str = "manual_entry",
        default_source_url: Optional[str] = None,
        default_verification_status: str = VerificationStatus.SOURCE_REPORTED,
        commit: bool = True,
    ) -> Tuple[Vessel, bool, List[VesselAttributeProvenance]]:
        """Upsert a vessel entity with full provenance, validation, and deterministic conflict resolution.

        Args:
            vessel_data: Dictionary containing vessel attributes.
            default_source_name: Fallback source label.
            default_source_url: Fallback source URL.
            default_verification_status: Default verification status.
            commit: Whether to commit the transaction.

        Returns:
            Tuple of (Vessel entity, created_flag, list of recorded provenance entries).
        """
        raw_imo = vessel_data.get("imo")
        raw_mmsi = vessel_data.get("mmsi")

        # 1. Strict IMO validation
        clean_imo: Optional[str] = None
        if raw_imo:
            is_valid, c_imo, err = validate_imo(raw_imo)
            if not is_valid:
                raise InvalidImoError(f"Validation failed for IMO '{raw_imo}': {err}")
            clean_imo = c_imo

        clean_mmsi = str(raw_mmsi).strip() if raw_mmsi else None
        if not clean_imo and not clean_mmsi:
            raise MalformedVesselRecordError("Vessel record must possess at least one of valid IMO or MMSI.")

        # 2. Canonical entity resolution
        vessel = self.resolve_canonical_vessel(imo=clean_imo, mmsi=clean_mmsi)
        created = False
        now_utc = datetime.now(timezone.utc)

        fields_prov_cfg = vessel_data.get("fields_provenance", {})

        if not vessel:
            # Create new canonical entity
            vessel = Vessel(
                mmsi=clean_mmsi or f"UNKNOWN_{clean_imo}",
                imo=clean_imo,
                vessel_name=str(vessel_data.get("vessel_name") or f"IMO {clean_imo}").strip(),
                vessel_type=str(vessel_data.get("vessel_type") or "Bulk Carrier").strip(),
                vessel_type_code=vessel_data.get("vessel_type_code", 70),
                vessel_class=vessel_data.get("vessel_class"),
                dwt_tonnage=vessel_data.get("dwt_tonnage"),
                gross_tonnage=vessel_data.get("gross_tonnage"),
                net_tonnage=vessel_data.get("net_tonnage"),
                length_meters=vessel_data.get("length_meters"),
                width_meters=vessel_data.get("width_meters"),
                draft_max_meters=vessel_data.get("draft_max_meters"),
                air_draft_meters=vessel_data.get("air_draft_meters"),
                grain_cubic_meters=vessel_data.get("grain_cubic_meters"),
                holds_count=vessel_data.get("holds_count"),
                hatches_count=vessel_data.get("hatches_count"),
                built_year=vessel_data.get("built_year"),
                flag_country=vessel_data.get("flag_country"),
                flag_code=vessel_data.get("flag_code"),
                source_name=vessel_data.get("source_name") or default_source_name,
                commercial_status=vessel_data.get("commercial_status") or "unknown",
                created_at=now_utc,
                updated_at=now_utc,
            )
            self.db.add(vessel)
            self.db.flush()
            created = True

        # Update primary MMSI / IMO linkages if new authentic keys are supplied
        if clean_imo and not vessel.imo:
            vessel.imo = clean_imo
        if clean_mmsi and vessel.mmsi.startswith("UNKNOWN_"):
            vessel.mmsi = clean_mmsi

        # Record MMSI history entry
        if clean_mmsi:
            self.record_mmsi(
                vessel_id=vessel.id,
                mmsi=clean_mmsi,
                flag_country=vessel_data.get("flag_country") or vessel.flag_country,
                valid_from=vessel_data.get("valid_from"),
                valid_to=vessel_data.get("valid_to"),
                source_name=vessel_data.get("source_name") or default_source_name,
                source_record_id=vessel_data.get("source_record_id"),
                retrieved_at=now_utc,
                commit=False,
            )

        # Record any additional historical MMSIs
        if "historical_mmsis" in vessel_data and isinstance(vessel_data["historical_mmsis"], list):
            for h_item in vessel_data["historical_mmsis"]:
                h_mmsi = str(h_item.get("mmsi", "")).strip()
                if h_mmsi and h_mmsi != clean_mmsi:
                    try:
                        self.record_mmsi(
                            vessel_id=vessel.id,
                            mmsi=h_mmsi,
                            flag_country=h_item.get("flag_country") or vessel.flag_country,
                            source_name=h_item.get("source_name") or default_source_name,
                            source_record_id=h_item.get("source_record_id"),
                            valid_from=h_item.get("valid_from"),
                            valid_to=h_item.get("valid_to"),
                            retrieved_at=now_utc,
                            commit=False,
                        )
                    except OverlappingMmsiError:
                        pass

        recorded_provenance: List[VesselAttributeProvenance] = []

        # Audit and record provenance for each physical/statutory attribute
        prov_attrs = [
            "vessel_name",
            "vessel_type",
            "vessel_class",
            "dwt_tonnage",
            "gross_tonnage",
            "net_tonnage",
            "length_meters",
            "width_meters",
            "draft_max_meters",
            "air_draft_meters",
            "grain_cubic_meters",
            "holds_count",
            "hatches_count",
            "built_year",
            "flag_country",
            "flag_code",
        ]

        for attr in prov_attrs:
            if attr not in vessel_data or vessel_data[attr] is None:
                continue

            val = vessel_data[attr]
            raw_s, norm_val, norm_method = self.normalize_attribute(attr, val)

            field_cfg = fields_prov_cfg.get(attr, {})
            v_status = field_cfg.get("status") or default_verification_status
            s_name = field_cfg.get("source_name") or vessel_data.get("source_name") or default_source_name
            s_rec_id = field_cfg.get("source_record_id") or vessel_data.get("source_record_id")
            s_url = field_cfg.get("source_url") or default_source_url or vessel_data.get("source_url")

            prov, is_conflict = self.record_provenance(
                vessel_id=vessel.id,
                attribute_name=attr,
                raw_value=raw_s,
                normalized_value=norm_val,
                source_name=s_name,
                source_record_id=s_rec_id,
                source_url=s_url,
                verification_status=v_status,
                retrieved_at=now_utc,
                normalization_method=norm_method,
                commit=False,
            )
            recorded_provenance.append(prov)

            # Resolve canonical field value deterministically
            canonical_val, _ = self.select_canonical_value(
                vessel_id=vessel.id,
                attribute_name=attr,
                incoming_value=norm_val,
                incoming_status=v_status,
                incoming_source=s_name,
            )

            # Apply resolved canonical value to vessel attribute
            if hasattr(vessel, attr):
                # Ensure correct type cast before setting on model
                if attr in {"dwt_tonnage", "gross_tonnage", "net_tonnage", "length_meters", "width_meters", "draft_max_meters", "air_draft_meters", "grain_cubic_meters"}:
                    setattr(vessel, attr, float(canonical_val) if canonical_val is not None else None)
                elif attr in {"built_year", "vessel_type_code", "holds_count", "hatches_count"}:
                    setattr(vessel, attr, int(canonical_val) if canonical_val is not None else None)
                else:
                    setattr(vessel, attr, canonical_val)

        vessel.updated_at = now_utc
        if commit:
            self.db.commit()
            self.db.refresh(vessel)

        return vessel, created, recorded_provenance

    def record_operational_estimate(
        self,
        vessel_id: int,
        model_name: str,
        model_version: Optional[str] = None,
        eco_speed_knots: Optional[float] = None,
        laden_consumption_mt_per_day: Optional[float] = None,
        ballast_consumption_mt_per_day: Optional[float] = None,
        port_idle_consumption_mt_per_day: Optional[float] = None,
        formula_reference: Optional[str] = None,
        assumptions: Optional[str] = None,
        commit: bool = True,
    ) -> VesselOperationalEstimates:
        """Record an operational engineering estimate without modifying verified statutory fields."""
        vessel = self.db.query(Vessel).filter(Vessel.id == vessel_id).first()
        if not vessel:
            raise VesselRegistryError(f"Cannot record operational estimate for non-existent vessel {vessel_id}.")

        est = VesselOperationalEstimates(
            vessel_id=vessel_id,
            model_name=model_name,
            model_version=model_version,
            eco_speed_knots=eco_speed_knots,
            laden_consumption_mt_per_day=laden_consumption_mt_per_day,
            ballast_consumption_mt_per_day=ballast_consumption_mt_per_day,
            port_idle_consumption_mt_per_day=port_idle_consumption_mt_per_day,
            formula_reference=formula_reference,
            assumptions=assumptions,
            computed_at=datetime.now(timezone.utc),
        )
        self.db.add(est)
        if commit:
            self.db.commit()
            self.db.refresh(est)
        return est

    def load_seed_file(
        self,
        seed_path: Optional[str] = None,
        commit: bool = True,
    ) -> Dict[str, Any]:
        """Ingest the verified 6-vessel seed file idempotently.

        Args:
            seed_path: Path to seed_bulk_vessels.json. Defaults to standard package location.
            commit: Whether to commit changes.

        Returns:
            Metrics dictionary: total_seeded, created_count, updated_count, total_provenance_records.
        """
        if not seed_path:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            seed_path = os.path.join(base_dir, "data", "seed_bulk_vessels.json")

        if not os.path.exists(seed_path):
            raise FileNotFoundError(f"Seed file not found at: {seed_path}")

        with open(seed_path, "r", encoding="utf-8") as f:
            vessels_data = json.load(f)

        created_cnt = 0
        updated_cnt = 0
        prov_cnt = 0

        for v_data in vessels_data:
            v, created, provs = self.upsert_vessel(
                vessel_data=v_data,
                default_source_name=v_data.get("source_name", "seed_file"),
                default_source_url=v_data.get("source_url"),
                default_verification_status=VerificationStatus.SOURCE_REPORTED,
                commit=False,
            )
            if created:
                created_cnt += 1
            else:
                updated_cnt += 1
            prov_cnt += len(provs)

        if commit:
            self.db.commit()

        return {
            "total_candidates": len(vessels_data),
            "created_count": created_cnt,
            "updated_count": updated_cnt,
            "provenance_records_recorded": prov_cnt,
        }
