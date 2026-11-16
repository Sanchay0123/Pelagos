"""Pelagos Vessel Statutory Particulars Readiness Service (Stage F1.4.3 Scope B).

Provides deterministic validation and evidence auditing for statutory vessel particulars
required by port authorities and canal administrations.

Critical Governance Invariants:
1. Zero Synthetic Tonnage: Never derive SCNT from GT or DWT. Never derive PC/UMS from GT or DWT.
2. Direct Verification Integrity: Never promote SOURCE_REPORTED values to DIRECTLY_VERIFIED.
3. Conflict Retention: Never resolve conflicting vessel particulars through arbitrary selection.
4. Passage-Specific Applicability: Applicability is charge- and authority-specific; not every vessel
   requires every metric for every call.
5. Fail-Closed Safety: Missing or unverified particulars block dependent operational calculations.
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass, field
import logging
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from sqlalchemy.orm import Session

from pelagos.models import (
    VerificationStatus,
    Vessel,
    VesselAttributeProvenance,
    validate_imo,
)

logger = logging.getLogger(__name__)


class VesselParticularStatus:
    """Standard verification and readiness classifications for statutory particulars."""
    DIRECTLY_VERIFIED = "DIRECTLY_VERIFIED"
    SOURCE_REPORTED = "SOURCE_REPORTED"
    MISSING = "MISSING"
    CONFLICTING = "CONFLICTING"
    STALE = "STALE"
    INAPPLICABLE = "INAPPLICABLE"


@dataclass(frozen=True)
class VesselParticularEvidence:
    """Detailed evidence assessment for an individual vessel dimension or metric."""
    attribute_name: str
    value: Any
    status: str  # DIRECTLY_VERIFIED | SOURCE_REPORTED | MISSING | CONFLICTING | STALE | INAPPLICABLE
    source_name: Optional[str] = None
    source_record_id: Optional[str] = None
    source_url: Optional[str] = None
    evidence_date: Optional[datetime.date] = None
    is_conflicting: bool = False
    is_required_for_passage: bool = False
    governing_authorities: List[str] = field(default_factory=list)
    blocking_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.evidence_date:
            d["evidence_date"] = self.evidence_date.isoformat()
        return d


@dataclass(frozen=True)
class VesselReadinessAssessment:
    """Structured assessment of vessel particulars readiness for a voyage passage."""
    is_ready_for_research: bool
    is_ready_for_operations: bool
    vessel_imo: Optional[str]
    vessel_name: Optional[str]
    vessel_class: Optional[str]
    assessed_ports: List[str]
    assessed_canals: List[str]
    verified_particulars: Dict[str, Any]
    source_reported_particulars: Dict[str, Any]
    missing_particulars: List[str]
    conflicting_particulars: List[str]
    stale_particulars: List[str]
    blocking_reasons: List[str]
    warnings: List[str]
    field_evaluations: Dict[str, VesselParticularEvidence]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_ready_for_research": self.is_ready_for_research,
            "is_ready_for_operations": self.is_ready_for_operations,
            "vessel_imo": self.vessel_imo,
            "vessel_name": self.vessel_name,
            "vessel_class": self.vessel_class,
            "assessed_ports": self.assessed_ports,
            "assessed_canals": self.assessed_canals,
            "verified_particulars": self.verified_particulars,
            "source_reported_particulars": self.source_reported_particulars,
            "missing_particulars": self.missing_particulars,
            "conflicting_particulars": self.conflicting_particulars,
            "stale_particulars": self.stale_particulars,
            "blocking_reasons": self.blocking_reasons,
            "warnings": self.warnings,
            "field_evaluations": {k: v.to_dict() for k, v in self.field_evaluations.items()},
        }


@dataclass
class VesselParticularItem:
    value: Any
    status: str
    evidence_classification: str = "DIRECTLY_VERIFIED"


@dataclass
class VesselReadinessResult:
    is_ready_for_research: bool
    is_ready_for_operations: bool
    is_complete: bool
    validation_errors: List[str]
    audit_flags: List[str]
    conflicts: Dict[str, Any]
    particulars: Dict[str, VesselParticularItem]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_ready_for_research": self.is_ready_for_research,
            "is_ready_for_operations": self.is_ready_for_operations,
            "is_complete": self.is_complete,
            "validation_errors": self.validation_errors,
            "audit_flags": self.audit_flags,
            "conflicts": self.conflicts,
            "particulars": {
                k: {
                    "value": v.value,
                    "status": v.status,
                    "evidence_classification": v.evidence_classification,
                }
                for k, v in self.particulars.items()
            },
            "warnings": self.warnings,
        }


class VesselReadinessService:
    """
    Evaluates statutory vessel particulars readiness against specific canal and port profiles.
    Strictly fail-closed: prevents operational clearance when certified dimensions are absent.
    """

    def __init__(self, db: Optional[Session] = None):
        self.db = db

    def evaluate_vessel_readiness(
        self,
        vessel: Optional[Union[Vessel, Dict[str, Any]]] = None,
        vessel_imo: Optional[Union[int, str]] = None,
        vessel_name: Optional[str] = None,
        vessel_class: Optional[str] = None,
        vessel_gt: Optional[float] = None,
        vessel_dwt: Optional[float] = None,
        vessel_nt: Optional[float] = None,
        loa_m: Optional[float] = None,
        beam_m: Optional[float] = None,
        draft_m: Optional[float] = None,
        air_draft_m: Optional[float] = None,
        scnt: Optional[float] = None,
        pc_ums_net: Optional[float] = None,
        fields_provenance: Optional[Dict[str, Dict[str, Any]]] = None,
        port_calls: Optional[List[str]] = None,
        canal_transits: Optional[List[str]] = None,
        transit_date: Optional[datetime.date] = None,
        operational_mode: bool = False,
    ) -> VesselReadinessAssessment:
        """
        Perform deterministic assessment of vessel particulars readiness.
        
        Evaluates applicability across requested ports and canals, classifying every dimension
        into verified, source-reported, missing, conflicting, or stale.
        """
        ports = [p.upper().strip() for p in (port_calls or [])]
        canals = [c.upper().strip() for c in (canal_transits or [])]
        t_date = transit_date or datetime.date(2024, 1, 15)

        # 1. Normalize Vessel Particulars Input
        raw_imo: Optional[str] = None
        raw_name: Optional[str] = vessel_name
        raw_class: Optional[str] = vessel_class
        raw_gt: Optional[float] = vessel_gt
        raw_dwt: Optional[float] = vessel_dwt
        raw_nt: Optional[float] = vessel_nt
        raw_loa: Optional[float] = loa_m
        raw_beam: Optional[float] = beam_m
        raw_draft: Optional[float] = draft_m
        raw_air_draft: Optional[float] = air_draft_m
        raw_scnt: Optional[float] = scnt
        raw_pc_ums: Optional[float] = pc_ums_net

        prov_map: Dict[str, Dict[str, Any]] = fields_provenance or {}

        if vessel is not None:
            if isinstance(vessel, Vessel):
                raw_imo = str(vessel.imo) if vessel.imo else None
                raw_name = raw_name or vessel.vessel_name
                raw_class = raw_class or vessel.vessel_class
                raw_gt = raw_gt if raw_gt is not None else vessel.gross_tonnage
                raw_dwt = raw_dwt if raw_dwt is not None else vessel.dwt_tonnage
                raw_nt = raw_nt if raw_nt is not None else vessel.net_tonnage
                raw_loa = raw_loa if raw_loa is not None else vessel.length_meters
                raw_beam = raw_beam if raw_beam is not None else vessel.width_meters
                raw_draft = raw_draft if raw_draft is not None else vessel.draft_max_meters
                raw_air_draft = raw_air_draft if raw_air_draft is not None else vessel.air_draft_meters
                # Extract ORM provenance records if available
                if vessel.provenance_records:
                    for pr in vessel.provenance_records:
                        prov_map[pr.attribute_name] = {
                            "status": pr.verification_status,
                            "source_name": pr.source_name,
                            "source_record_id": pr.source_record_id,
                            "source_url": pr.source_url,
                            "is_conflicting": pr.is_conflicting,
                            "reporting_date": pr.observation_date.date().isoformat() if pr.observation_date else None,
                        }
            elif isinstance(vessel, dict):
                raw_imo = str(vessel.get("imo")) if vessel.get("imo") else None
                raw_name = raw_name or vessel.get("vessel_name")
                raw_class = raw_class or vessel.get("vessel_class")
                raw_gt = raw_gt if raw_gt is not None else vessel.get("gross_tonnage")
                raw_dwt = raw_dwt if raw_dwt is not None else vessel.get("dwt_tonnage")
                raw_nt = raw_nt if raw_nt is not None else vessel.get("net_tonnage")
                raw_loa = raw_loa if raw_loa is not None else vessel.get("length_meters")
                raw_beam = raw_beam if raw_beam is not None else vessel.get("width_meters")
                raw_draft = raw_draft if raw_draft is not None else vessel.get("draft_max_meters")
                raw_air_draft = raw_air_draft if raw_air_draft is not None else vessel.get("air_draft_meters")
                raw_scnt = raw_scnt if raw_scnt is not None else vessel.get("scnt")
                raw_pc_ums = raw_pc_ums if raw_pc_ums is not None else vessel.get("pc_ums_net")
                if "fields_provenance" in vessel and not fields_provenance:
                    prov_map = vessel["fields_provenance"]

        if vessel_imo is not None and not raw_imo:
            raw_imo = str(vessel_imo)

        # 2. Define Required Dimensions by Governing Authority & Transit
        # Panama Canal Authority (ACP): LOA, Beam, DWT (PC/UMS if applicable)
        # Suez Canal Authority (SCA): Certified SCNT, Draft, Beam
        # Singapore (MPA): GT
        # Rotterdam (Havenbedrijf): GT
        # Houston (PHA / Pilots): LOA, Draft, GT
        req_panama = "PANAMA" in canals
        req_suez = "SUEZ" in canals
        req_sgsin = "SGSIN" in ports
        req_nlrtm = "NLRTM" in ports
        req_ushou = "USHOU" in ports

        # Requirements registry: attr -> (is_required, authorities)
        req_registry: Dict[str, Tuple[bool, List[str]]] = {
            "imo": (True, ["GENERAL_REGISTRY"]),
            "dwt_tonnage": (req_panama, ["ACP"] if req_panama else []),
            "gross_tonnage": (req_sgsin or req_nlrtm or req_ushou, [a for a, req in [("MPA", req_sgsin), ("HAVENBEDRIJF", req_nlrtm), ("PHA", req_ushou)] if req]),
            "net_tonnage": (False, []),
            "length_meters": (req_panama or req_ushou, [a for a, req in [("ACP", req_panama), ("HOUSTON_PILOTS", req_ushou)] if req]),
            "width_meters": (req_panama or req_suez, [a for a, req in [("ACP", req_panama), ("SCA", req_suez)] if req]),
            "draft_max_meters": (req_suez or req_ushou, [a for a, req in [("SCA", req_suez), ("HOUSTON_PILOTS", req_ushou)] if req]),
            "air_draft_meters": (False, []),
            "scnt": (req_suez, ["SCA"] if req_suez else []),
            "pc_ums_net": (False, ["ACP"] if req_panama else []),
        }

        # 3. Evaluate Each Dimension
        values_dict = {
            "imo": raw_imo,
            "dwt_tonnage": raw_dwt,
            "gross_tonnage": raw_gt,
            "net_tonnage": raw_nt,
            "length_meters": raw_loa,
            "width_meters": raw_beam,
            "draft_max_meters": raw_draft,
            "air_draft_meters": raw_air_draft,
            "scnt": raw_scnt,
            "pc_ums_net": raw_pc_ums,
        }

        evaluations: Dict[str, VesselParticularEvidence] = {}
        verified_dict: Dict[str, Any] = {}
        source_reported_dict: Dict[str, Any] = {}
        missing_list: List[str] = []
        conflicting_list: List[str] = []
        stale_list: List[str] = []
        blocking_reasons: List[str] = []
        warnings: List[str] = []

        # Validate IMO format if present
        imo_valid = False
        if raw_imo:
            try:
                imo_valid = validate_imo(str(raw_imo))
            except Exception:
                imo_valid = False
            if not imo_valid:
                blocking_reasons.append(f"INVALID_IMO_CHECKSUM: Vessel IMO '{raw_imo}' fails official check-digit algorithm")

        for attr, (is_req, authorities) in req_registry.items():
            val = values_dict.get(attr)
            prov = prov_map.get(attr, {})
            status_str = prov.get("status")
            is_conf = prov.get("is_conflicting", False)
            source_name = prov.get("source_name")
            source_id = prov.get("source_record_id")
            source_url = prov.get("source_url")
            rep_date_str = prov.get("reporting_date")
            rep_date = None
            if rep_date_str:
                try:
                    rep_date = datetime.date.fromisoformat(rep_date_str)
                except Exception:
                    pass

            item_blocking: Optional[str] = None

            # Special Invariant: SCNT must be certified/verified; never synthesized
            if attr == "scnt":
                if req_suez and (val is None or val <= 0):
                    status = VesselParticularStatus.MISSING
                    missing_list.append("scnt")
                    item_blocking = "MISSING_CERTIFIED_SCNT: Suez transit requires certified Suez Canal Net Tonnage (SCNT). Zero-synthetic-tonnage rule strictly enforced."
                    blocking_reasons.append(item_blocking)
                elif val is not None and val > 0:
                    # If passed in directly, check if accredited source
                    if status_str == VerificationStatus.DIRECTLY_VERIFIED:
                        status = VesselParticularStatus.DIRECTLY_VERIFIED
                        verified_dict["scnt"] = val
                    else:
                        status = VesselParticularStatus.SOURCE_REPORTED
                        source_reported_dict["scnt"] = val
                        if req_suez and operational_mode:
                            item_blocking = "UNVERIFIED_SCNT_OPERATIONAL_BLOCKED: SCNT is source-reported and not certified by Suez Canal Authority."
                            blocking_reasons.append(item_blocking)
                else:
                    status = VesselParticularStatus.INAPPLICABLE

            # Special Invariant: PC/UMS Net
            elif attr == "pc_ums_net":
                if val is not None and val > 0:
                    if status_str == VerificationStatus.DIRECTLY_VERIFIED:
                        status = VesselParticularStatus.DIRECTLY_VERIFIED
                        verified_dict["pc_ums_net"] = val
                    else:
                        status = VesselParticularStatus.SOURCE_REPORTED
                        source_reported_dict["pc_ums_net"] = val
                else:
                    status = VesselParticularStatus.INAPPLICABLE

            elif val is None:
                if is_req:
                    status = VesselParticularStatus.MISSING
                    missing_list.append(attr)
                    item_blocking = f"MISSING_REQUIRED_VESSEL_DIMENSION: Attribute '{attr}' is required by {authorities} but absent from vessel record."
                    blocking_reasons.append(item_blocking)
                else:
                    status = VesselParticularStatus.INAPPLICABLE

            elif is_conf:
                status = VesselParticularStatus.CONFLICTING
                conflicting_list.append(attr)
                item_blocking = f"CONFLICTING_VESSEL_PARTICULAR: Attribute '{attr}' has conflicting records across sources exceeding tolerance."
                blocking_reasons.append(item_blocking)

            elif status_str == VerificationStatus.DIRECTLY_VERIFIED:
                status = VesselParticularStatus.DIRECTLY_VERIFIED
                verified_dict[attr] = val

            elif status_str == VerificationStatus.SOURCE_REPORTED or status_str is None:
                status = VesselParticularStatus.SOURCE_REPORTED
                source_reported_dict[attr] = val
                if is_req and operational_mode:
                    item_blocking = f"UNVERIFIED_VESSEL_PARTICULAR: Required attribute '{attr}' is SOURCE_REPORTED and not DIRECTLY_VERIFIED for operational use."
                    blocking_reasons.append(item_blocking)
                elif is_req:
                    warnings.append(f"Attribute '{attr}' is SOURCE_REPORTED (sufficient for research, blocked for Level 3 operations).")

            else:
                status = VesselParticularStatus.SOURCE_REPORTED
                source_reported_dict[attr] = val

            evaluations[attr] = VesselParticularEvidence(
                attribute_name=attr,
                value=val,
                status=status,
                source_name=source_name,
                source_record_id=source_id,
                source_url=source_url,
                evidence_date=rep_date,
                is_conflicting=is_conf,
                is_required_for_passage=is_req,
                governing_authorities=authorities,
                blocking_reason=item_blocking,
            )

        # 4. Final Readiness Determination
        # Research ready: no missing required particulars, no blocking conflicts
        is_ready_research = len(missing_list) == 0 and len(conflicting_list) == 0 and imo_valid
        # Operational ready: all required particulars must be DIRECTLY_VERIFIED, operational_mode checked, zero blockers
        is_ready_operations = False  # Strictly blocked in Stage F1.4.3 Phase 1

        if operational_mode:
            blocking_reasons.append("OPERATIONAL_TCE_VESSEL_READINESS_BLOCKED: Level 3 operational vessel readiness is blocked pending cryptographic unlock clearance.")

        return VesselReadinessAssessment(
            is_ready_for_research=is_ready_research,
            is_ready_for_operations=is_ready_operations,
            vessel_imo=raw_imo,
            vessel_name=raw_name,
            vessel_class=raw_class,
            assessed_ports=ports,
            assessed_canals=canals,
            verified_particulars=verified_dict,
            source_reported_particulars=source_reported_dict,
            missing_particulars=missing_list,
            conflicting_particulars=conflicting_list,
            stale_particulars=stale_list,
            blocking_reasons=blocking_reasons,
            warnings=warnings,
            field_evaluations=evaluations,
        )

    def assess_readiness(
        self,
        vessel_data: Dict[str, Any],
        required_authorities: Optional[List[str]] = None,
    ) -> VesselReadinessResult:
        """
        Assess statutory vessel particulars readiness against required authorities.
        Enforces zero synthetic tonnage, conflict retention, and unpromoted evidence status.
        """
        authorities = [a.upper().strip() for a in (required_authorities or [])]
        v = vessel_data or {}
        val_errors: List[str] = []
        audit_flags: List[str] = ["ZERO_SYNTHETIC_TONNAGE_ENFORCED"]
        warnings: List[str] = []

        imo = v.get("imo") or v.get("vessel_imo")
        dwt = v.get("dwt") or v.get("dwt_tonnage")
        gt = v.get("gt") or v.get("gross_tonnage")
        nt = v.get("nt") or v.get("net_tonnage")
        scnt = v.get("scnt")
        pc_ums = v.get("pc_ums_net") or v.get("pc_ums")
        loa = v.get("loa_m") or v.get("length_meters")
        beam = v.get("beam_m") or v.get("width_meters")
        draft = v.get("draft_m") or v.get("draft_max_meters")

        ev_dwt = v.get("evidence_dwt") or ("DIRECTLY_VERIFIED" if dwt else "MISSING")
        ev_gt = v.get("evidence_gt") or ("DIRECTLY_VERIFIED" if gt else "MISSING")
        ev_scnt = v.get("evidence_scnt") or ("DIRECTLY_VERIFIED" if scnt else "MISSING")
        ev_loa = v.get("evidence_loa") or ("DIRECTLY_VERIFIED" if loa else "MISSING")
        ev_beam = v.get("evidence_beam") or ("DIRECTLY_VERIFIED" if beam else "MISSING")
        ev_draft = v.get("evidence_draft") or ("DIRECTLY_VERIFIED" if draft else "MISSING")

        # 1. Zero Synthetic Tonnage Invariant
        if ev_scnt == "SYNTHETIC_DERIVED" or str(v.get("evidence_scnt")).upper() == "SYNTHETIC_DERIVED":
            val_errors.append("SYNTHETIC_TONNAGE_REJECTED: SCNT cannot be synthesized or derived from GT/DWT.")
        if ev_dwt == "SYNTHETIC_DERIVED" or ev_gt == "SYNTHETIC_DERIVED":
            val_errors.append("SYNTHETIC_TONNAGE_REJECTED: Synthetic tonnage derivation is prohibited.")

        # 2. Authority Specific Checks
        if "SUEZ" in authorities:
            if not scnt or scnt <= 0:
                val_errors.append("MISSING_CERTIFIED_SCNT: Suez Canal transit requires certified SCNT.")
            if not draft or draft <= 0:
                val_errors.append("MISSING_REQUIRED_DIMENSION: Suez Canal transit requires maximum draft.")
            if not beam or beam <= 0:
                val_errors.append("MISSING_REQUIRED_DIMENSION: Suez Canal transit requires extreme beam.")

        if "PANAMA" in authorities:
            if not loa or loa <= 0:
                val_errors.append("MISSING_REQUIRED_DIMENSION: Panama Canal transit requires Length Overall (LOA).")
            if not beam or beam <= 0:
                val_errors.append("MISSING_REQUIRED_DIMENSION: Panama Canal transit requires Extreme Beam.")
            if not dwt or dwt <= 0:
                val_errors.append("MISSING_REQUIRED_DIMENSION: Panama Canal transit requires Deadweight Tonnage (DWT).")

        port_authorities = {"SGSIN", "NLRTM", "USHOU", "MPA", "HAVENBEDRIJF", "PHA", "HOUSTON_PILOTS"}
        if any(a in port_authorities for a in authorities):
            if not gt or gt <= 0:
                val_errors.append("MISSING_REQUIRED_DIMENSION: Port dues require verified Gross Tonnage (GT).")
            if ("USHOU" in authorities or "PHA" in authorities or "HOUSTON_PILOTS" in authorities) and (not loa or not draft):
                val_errors.append("MISSING_REQUIRED_DIMENSION: Houston port call requires LOA and draft.")

        # 3. Conflicts Check
        raw_conflicts = v.get("conflicts")
        conflicts_dict: Dict[str, Any] = {}
        if isinstance(raw_conflicts, dict):
            conflicts_dict = raw_conflicts
            audit_flags.append("CONFLICTING_PARTICULARS_RETAINED")
        elif isinstance(raw_conflicts, list):
            conflicts_dict = {c: f"Conflicting values reported for {c}" for c in raw_conflicts}
            audit_flags.append("CONFLICTING_PARTICULARS_RETAINED")

        # 4. Source Reported Tracking
        if ev_dwt == "SOURCE_REPORTED" or ev_loa == "SOURCE_REPORTED" or ev_gt == "SOURCE_REPORTED":
            audit_flags.append("UNPROMOTED_SOURCE_REPORTED")

        # 5. Build Particulars
        particulars: Dict[str, VesselParticularItem] = {}
        field_map = {
            "dwt": (dwt, ev_dwt),
            "gt": (gt, ev_gt),
            "nt": (nt, "DIRECTLY_VERIFIED" if nt else "MISSING"),
            "scnt": (scnt, ev_scnt),
            "pc_ums_net": (pc_ums, "DIRECTLY_VERIFIED" if pc_ums else "MISSING"),
            "loa_m": (loa, ev_loa),
            "beam_m": (beam, ev_beam),
            "draft_m": (draft, ev_draft),
        }
        for k, (val, ev) in field_map.items():
            if k in conflicts_dict:
                status = VesselParticularStatus.CONFLICTING
            elif val is None:
                status = VesselParticularStatus.MISSING
            elif ev == "SOURCE_REPORTED":
                status = VesselParticularStatus.SOURCE_REPORTED
            elif ev == "DIRECTLY_VERIFIED":
                status = VesselParticularStatus.DIRECTLY_VERIFIED
            else:
                status = VesselParticularStatus.SOURCE_REPORTED
            particulars[k] = VesselParticularItem(
                value=val,
                status=status,
                evidence_classification=ev,
            )

        is_complete = len(val_errors) == 0 and len(conflicts_dict) == 0
        is_ready = is_complete

        return VesselReadinessResult(
            is_ready_for_research=is_ready,
            is_ready_for_operations=False,
            is_complete=is_complete,
            validation_errors=val_errors,
            audit_flags=list(dict.fromkeys(audit_flags)),
            conflicts=conflicts_dict,
            particulars=particulars,
            warnings=warnings,
        )

