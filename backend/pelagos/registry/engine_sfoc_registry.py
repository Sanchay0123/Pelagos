"""Pelagos Engine SFOC Library & Load-Curve Parameterization (Stages F1.4.2.2b & F1.4.2.2b.1).

Provides a variant-aware library of Specific Fuel Oil Consumption (SFOC) data
and controlled piecewise-linear interpolation between documented manufacturer load points.

Three Independent Dimensional Boundaries (Stage F1.4.2.2b.1):
1. Source Authenticity: whether source and values are traceable to primary document (DIRECTLY_VERIFIED, SOURCE_REPORTED, UNVERIFIED).
2. Technical Applicability: whether curve applies to an exact engine variant, an engine family, or aggregate fleet (EXACT_VARIANT, FAMILY_REFERENCE, AGGREGATE_RESEARCH, UNAVAILABLE).
3. Permitted Use: whether source permits operational use, research use, internal retention, or redistribution (OPERATIONAL_PERMITTED, RESEARCH_ONLY, RESTRICTED_PENDING_PERMISSION, AGGREGATE_RESEARCH_ONLY).

Operational Invariants & Safety Guarantees:
- Safe Default: operational_mode=True fail-closed; research-only and restricted curves cannot enter operational calculations.
- Aggregate Data Isolation: IMO fleet statistical averages are kept separate and never matched as manufacturer engine curves.
- Zero Extrapolation: queries outside [min_load_percent, max_load_percent] raise SfocLoadOutOfRangeError.
- SFOC is NOT speed-to-power: maps engine load % -> SFOC (g/kWh). Does not estimate required power from vessel speed.
- Displacement is NOT DWT: no resistance or naval architecture modeling in this registry.
- THETIS-MRV separation: annual empirical observations remain decoupled from main-engine test bench figures.
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from pelagos.models import (
    CurveApplicabilityScope,
    EngineSfocCurve,
    EngineSfocLoadPoint,
    SfocPermittedUse,
    SourceLicensingStatus,
    VerificationStatus,
    Vessel,
)


class EngineSfocError(Exception):
    """Base exception for Engine SFOC operations."""
    pass


class SfocCurveNotFoundError(EngineSfocError):
    """Raised when an engine SFOC curve is requested but does not exist."""
    pass


class SfocLoadOutOfRangeError(EngineSfocError):
    """Raised when an engine load falls outside documented manufacturer load boundaries (no extrapolation)."""
    pass


class InvalidLoadPercentError(EngineSfocError):
    """Raised when an engine load percentage is non-positive, non-finite, or malformed."""
    pass


class SfocRestrictedUseError(EngineSfocError):
    """Raised when an operationally restricted SFOC curve is evaluated in operational mode."""
    pass


@dataclass(frozen=True)
class SfocInterpolationResult:
    """Immutable result of an engine SFOC evaluation or piecewise interpolation."""
    curve_code: str
    engine_manufacturer: str
    engine_model: str
    engine_variant: Optional[str]
    authenticity_status: str
    applicability_scope: str
    permitted_use: str
    operational_mode: bool
    load_percent: float
    sfoc_g_kwh: float
    is_interpolated: bool
    is_guaranteed: bool
    tolerance_pct: Optional[float]
    lower_bound_point: Tuple[float, float]
    upper_bound_point: Tuple[float, float]
    method: str
    fuel_type: str
    fuel_lcv_mj_kg: float
    reference_conditions: Optional[str]
    licensing_status: str
    source_citation_details: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EngineSfocRegistry:
    """Registry and calculation engine for marine engine SFOC curves and piecewise interpolation."""

    def __init__(self, db: Session):
        self.db = db

    def load_seed_curves(self, seed_path: Optional[Union[str, Path]] = None) -> Dict[str, int]:
        """Idempotently seed verified manufacturer SFOC curves and documented load points.

        Returns:
            Dict[str, int]: {"curves_created": int, "curves_updated": int, "load_points_created": int}
        """
        if seed_path is None:
            seed_path = Path(__file__).resolve().parent.parent / "data" / "seed_engine_sfoc.json"
        else:
            seed_path = Path(seed_path)

        if not seed_path.exists():
            raise FileNotFoundError(f"Engine SFOC seed file not found: {seed_path}")

        with open(seed_path, "r", encoding="utf-8") as f:
            raw_curves = json.load(f)

        curves_created = 0
        curves_updated = 0
        points_created = 0
        now_utc = datetime.datetime.now(datetime.timezone.utc)

        for c_data in raw_curves:
            curve_code = str(c_data["curve_code"]).strip().upper()
            curve = (
                self.db.query(EngineSfocCurve)
                .filter(EngineSfocCurve.curve_code == curve_code)
                .first()
            )

            auth_status = str(c_data.get("authenticity_status", VerificationStatus.DIRECTLY_VERIFIED)).strip()
            perm_use = str(c_data.get("permitted_use", SfocPermittedUse.RESEARCH_ONLY)).strip()
            citation = c_data.get("source_citation_details")
            is_agg = bool(c_data.get("is_aggregate_research", False))

            if curve is None:
                curve = EngineSfocCurve(
                    curve_code=curve_code,
                    engine_manufacturer=str(c_data["engine_manufacturer"]).strip(),
                    engine_model=str(c_data["engine_model"]).strip(),
                    engine_variant=str(c_data["engine_variant"]).strip() if c_data.get("engine_variant") else None,
                    engine_family=str(c_data["engine_family"]).strip(),
                    authenticity_status=auth_status,
                    applicability_scope=str(c_data.get("applicability_scope", CurveApplicabilityScope.FAMILY_REFERENCE)).strip(),
                    permitted_use=perm_use,
                    is_aggregate_research=is_agg,
                    cylinder_count=c_data.get("cylinder_count"),
                    rated_power_kw=c_data.get("rated_power_kw"),
                    rated_rpm=c_data.get("rated_rpm"),
                    tuning_strategy=c_data.get("tuning_strategy"),
                    fuel_type=c_data.get("fuel_type", "ISO_DMA_DIESEL"),
                    fuel_lcv_mj_kg=float(c_data.get("fuel_lcv_mj_kg", 42.7)),
                    reference_conditions=c_data.get("reference_conditions"),
                    tolerance_pct=float(c_data["tolerance_pct"]) if c_data.get("tolerance_pct") is not None else 5.0,
                    source_name=str(c_data["source_name"]).strip(),
                    source_document=str(c_data["source_document"]).strip(),
                    source_document_url=c_data.get("source_document_url"),
                    source_revision=c_data.get("source_revision"),
                    source_citation_details=citation,
                    retrieval_date=c_data.get("retrieval_date"),
                    licensing_status=str(c_data.get("licensing_status", SourceLicensingStatus.OPEN_RESEARCH_ONLY)).strip(),
                    min_load_percent=float(c_data.get("min_load_percent", 50.0)),
                    max_load_percent=float(c_data.get("max_load_percent", 100.0)),
                    notes=c_data.get("notes"),
                    created_at=now_utc,
                    updated_at=now_utc,
                )
                self.db.add(curve)
                self.db.flush()
                curves_created += 1
            else:
                curve.engine_manufacturer = str(c_data["engine_manufacturer"]).strip()
                curve.engine_model = str(c_data["engine_model"]).strip()
                curve.engine_variant = str(c_data["engine_variant"]).strip() if c_data.get("engine_variant") else None
                curve.engine_family = str(c_data["engine_family"]).strip()
                curve.authenticity_status = auth_status
                curve.applicability_scope = str(c_data.get("applicability_scope", curve.applicability_scope)).strip()
                curve.permitted_use = perm_use
                curve.is_aggregate_research = is_agg
                curve.cylinder_count = c_data.get("cylinder_count")
                curve.rated_power_kw = c_data.get("rated_power_kw")
                curve.rated_rpm = c_data.get("rated_rpm")
                curve.tuning_strategy = c_data.get("tuning_strategy")
                curve.fuel_type = c_data.get("fuel_type", curve.fuel_type)
                curve.fuel_lcv_mj_kg = float(c_data.get("fuel_lcv_mj_kg", curve.fuel_lcv_mj_kg))
                curve.reference_conditions = c_data.get("reference_conditions", curve.reference_conditions)
                curve.tolerance_pct = float(c_data["tolerance_pct"]) if c_data.get("tolerance_pct") is not None else curve.tolerance_pct
                curve.source_name = str(c_data["source_name"]).strip()
                curve.source_document = str(c_data["source_document"]).strip()
                curve.source_document_url = c_data.get("source_document_url")
                curve.source_revision = c_data.get("source_revision")
                curve.source_citation_details = citation
                curve.licensing_status = str(c_data.get("licensing_status", curve.licensing_status)).strip()
                curve.min_load_percent = float(c_data.get("min_load_percent", curve.min_load_percent))
                curve.max_load_percent = float(c_data.get("max_load_percent", curve.max_load_percent))
                curve.notes = c_data.get("notes", curve.notes)
                curve.updated_at = now_utc
                curves_updated += 1

            # Seed load points
            existing_pts = {
                round(pt.load_percent, 2): pt
                for pt in self.db.query(EngineSfocLoadPoint).filter(EngineSfocLoadPoint.curve_id == curve.id).all()
            }

            for pt_data in c_data.get("load_points", []):
                lp = float(pt_data["load_percent"])
                lp_key = round(lp, 2)
                sfoc_val = float(pt_data["sfoc_g_kwh"])
                tol = float(pt_data["tolerance_pct"]) if pt_data.get("tolerance_pct") is not None else curve.tolerance_pct
                is_g = bool(pt_data.get("is_guaranteed", False))
                notes = pt_data.get("notes")

                if lp_key in existing_pts:
                    pt_obj = existing_pts[lp_key]
                    pt_obj.sfoc_g_kwh = sfoc_val
                    pt_obj.tolerance_pct = tol
                    pt_obj.is_guaranteed = is_g
                    pt_obj.notes = notes
                else:
                    new_pt = EngineSfocLoadPoint(
                        curve_id=curve.id,
                        load_percent=lp,
                        sfoc_g_kwh=sfoc_val,
                        tolerance_pct=tol,
                        is_guaranteed=is_g,
                        is_interpolated=False,
                        notes=notes,
                    )
                    self.db.add(new_pt)
                    points_created += 1

        self.db.commit()
        return {
            "curves_created": curves_created,
            "curves_updated": curves_updated,
            "load_points_created": points_created,
        }

    def load_aggregate_research_dataset(self, dataset_path: Optional[Union[str, Path]] = None) -> Dict[str, int]:
        """Idempotently seed separate research-only aggregate datasets (e.g. Fourth IMO GHG Study).

        Isolates aggregate fleet research data from primary manufacturer engine curves.
        """
        if dataset_path is None:
            dataset_path = Path(__file__).resolve().parent.parent / "data" / "research_aggregate_sfoc.json"
        else:
            dataset_path = Path(dataset_path)

        if not dataset_path.exists():
            raise FileNotFoundError(f"Aggregate research dataset file not found: {dataset_path}")

        with open(dataset_path, "r", encoding="utf-8") as f:
            raw_datasets = json.load(f)

        curves_created = 0
        points_created = 0
        now_utc = datetime.datetime.now(datetime.timezone.utc)

        for d in raw_datasets:
            code = str(d["dataset_code"]).strip().upper()
            curve = (
                self.db.query(EngineSfocCurve)
                .filter(EngineSfocCurve.curve_code == code)
                .first()
            )

            if curve is None:
                curve = EngineSfocCurve(
                    curve_code=code,
                    engine_manufacturer=str(d["publisher"]).strip(),
                    engine_model=str(d["dataset_title"]).strip(),
                    engine_variant=None,
                    engine_family="IMO_FLEET_STATISTICAL_AGGREGATE",
                    authenticity_status=str(d.get("authenticity_status", VerificationStatus.DIRECTLY_VERIFIED)).strip(),
                    applicability_scope=CurveApplicabilityScope.AGGREGATE_RESEARCH,
                    permitted_use=SfocPermittedUse.AGGREGATE_RESEARCH_ONLY,
                    is_aggregate_research=True,
                    cylinder_count=None,
                    rated_power_kw=None,
                    rated_rpm=None,
                    tuning_strategy="Global Fleet Statistical Average",
                    fuel_type=d.get("fuel_type", "ISO_DMA_DIESEL"),
                    fuel_lcv_mj_kg=float(d.get("fuel_lcv_mj_kg", 42.7)),
                    reference_conditions=d.get("reference_conditions"),
                    tolerance_pct=float(d.get("tolerance_pct", 5.0)),
                    source_name=str(d["publisher"]).strip(),
                    source_document=str(d["source_document"]).strip(),
                    source_document_url=d.get("source_document_url"),
                    source_revision=d.get("source_revision"),
                    source_citation_details=d.get("source_citation_details"),
                    retrieval_date=d.get("retrieval_date"),
                    licensing_status=SourceLicensingStatus.AGGREGATE_ONLY,
                    min_load_percent=float(d.get("min_load_percent", 25.0)),
                    max_load_percent=float(d.get("max_load_percent", 100.0)),
                    notes=d.get("notes"),
                    created_at=now_utc,
                    updated_at=now_utc,
                )
                self.db.add(curve)
                self.db.flush()
                curves_created += 1

            # Seed load points
            existing_pts = {
                round(pt.load_percent, 2): pt
                for pt in self.db.query(EngineSfocLoadPoint).filter(EngineSfocLoadPoint.curve_id == curve.id).all()
            }

            for pt_data in d.get("load_points", []):
                lp = float(pt_data["load_percent"])
                lp_key = round(lp, 2)
                sfoc_val = float(pt_data["sfoc_g_kwh"])
                tol = float(pt_data["tolerance_pct"]) if pt_data.get("tolerance_pct") is not None else 5.0

                if lp_key not in existing_pts:
                    new_pt = EngineSfocLoadPoint(
                        curve_id=curve.id,
                        load_percent=lp,
                        sfoc_g_kwh=sfoc_val,
                        tolerance_pct=tol,
                        is_guaranteed=False,
                        is_interpolated=False,
                        notes=pt_data.get("notes"),
                    )
                    self.db.add(new_pt)
                    points_created += 1

        self.db.commit()
        return {"aggregate_curves_created": curves_created, "aggregate_load_points_created": points_created}

    def get_curve(self, curve_code: str) -> Optional[EngineSfocCurve]:
        """Retrieve an SFOC curve by unique code with load points eager-loaded."""
        return (
            self.db.query(EngineSfocCurve)
            .options(joinedload(EngineSfocCurve.load_points))
            .filter(EngineSfocCurve.curve_code == curve_code.strip().upper())
            .first()
        )

    def get_curve_by_id(self, curve_id: int) -> Optional[EngineSfocCurve]:
        """Retrieve an SFOC curve by internal ID with load points eager-loaded."""
        return (
            self.db.query(EngineSfocCurve)
            .options(joinedload(EngineSfocCurve.load_points))
            .filter(EngineSfocCurve.id == curve_id)
            .first()
        )

    def get_aggregate_research_benchmark(self, dataset_code: str = "IMO4_2STROKE_SSD_POST2010") -> Optional[EngineSfocCurve]:
        """Retrieve a separate aggregate research benchmark dataset (research/comparative use only)."""
        return (
            self.db.query(EngineSfocCurve)
            .options(joinedload(EngineSfocCurve.load_points))
            .filter(
                EngineSfocCurve.curve_code == dataset_code.strip().upper(),
                EngineSfocCurve.is_aggregate_research == True,  # noqa: E712
            )
            .first()
        )

    def resolve_curve_for_engine(
        self,
        manufacturer: str,
        model: str,
        variant: Optional[str] = None,
        rated_power_kw: Optional[float] = None,
        operational_mode: bool = True,
    ) -> Tuple[Optional[EngineSfocCurve], str, str]:
        """Resolve applicable SFOC curve for engine particulars with dimensional separation and operational safeguards.

        Dimensional Enforcement:
        1. Dimension 1 (Authenticity): Authenticity status is preserved from primary document verification.
        2. Dimension 2 (Technical Applicability): Matches EXACT_VARIANT only when exact variant matches; else FAMILY_REFERENCE.
           Aggregate research datasets (IMO GHG study) are strictly excluded from engine resolution.
        3. Dimension 3 (Permitted Use): In operational_mode=True, curves lacking OPERATIONAL_PERMITTED permission
           fail-closed (returns None, UNAVAILABLE). In operational_mode=False (research/audit), research curves are returned.

        Returns:
            (curve, applicability_scope, message)
        """
        if not manufacturer or not model:
            return None, CurveApplicabilityScope.UNAVAILABLE, "Manufacturer and model are required."

        m_clean = manufacturer.strip().lower()
        mod_clean = model.strip().upper().replace(" ", "").replace("-", "")
        v_clean = variant.strip().upper().replace(" ", "").replace("-", "") if variant else ""

        # Fetch only manufacturer engine curves (strictly exclude aggregate research data)
        all_curves = (
            self.db.query(EngineSfocCurve)
            .options(joinedload(EngineSfocCurve.load_points))
            .filter(EngineSfocCurve.is_aggregate_research == False)  # noqa: E712
            .all()
        )

        matched_curve: Optional[EngineSfocCurve] = None
        matched_scope: str = CurveApplicabilityScope.UNAVAILABLE
        diagnostic_msg: str = ""

        # 1. Exact variant match check (only for certified EXACT_VARIANT curves)
        if v_clean:
            for c in all_curves:
                if c.applicability_scope != CurveApplicabilityScope.EXACT_VARIANT:
                    continue
                c_var = (c.engine_variant or "").strip().upper().replace(" ", "").replace("-", "")
                c_mod = c.engine_model.strip().upper().replace(" ", "").replace("-", "")
                if c_var and (c_var == v_clean or v_clean in c_var):
                    if c_mod in mod_clean or mod_clean in c_mod:
                        matched_curve = c
                        matched_scope = CurveApplicabilityScope.EXACT_VARIANT
                        diagnostic_msg = f"Exact engine variant match ({c.engine_variant})."
                        break

        # 2. Family / model reference match check (if exact variant not matched)
        if not matched_curve:
            for c in all_curves:
                c_mod = c.engine_model.strip().upper().replace(" ", "").replace("-", "")
                c_fam = c.engine_family.strip().upper().replace(" ", "").replace("-", "")
                if c_mod in mod_clean or mod_clean in c_mod or c_fam in mod_clean:
                    matched_curve = c
                    matched_scope = CurveApplicabilityScope.FAMILY_REFERENCE
                    diagnostic_msg = f"Family reference curve ({c.curve_code}) matched for model {model}."
                    if variant:
                        diagnostic_msg += f" Note: Variant '{variant}' lacks specific shop-test sheet; applying family-level reference."
                    break

        if not matched_curve:
            return None, CurveApplicabilityScope.UNAVAILABLE, f"No SFOC curve available for {manufacturer} {model}."

        # 3. Permitted Use / Operational Safeguard Enforcement
        if operational_mode and matched_curve.permitted_use != SfocPermittedUse.OPERATIONAL_PERMITTED:
            return (
                None,
                CurveApplicabilityScope.UNAVAILABLE,
                f"Curve '{matched_curve.curve_code}' is classified as {matched_curve.permitted_use} "
                f"and is not permitted for operational calculations without manufacturer license. "
                f"Pass operational_mode=False for research/audit access.",
            )

        return matched_curve, matched_scope, diagnostic_msg

    def resolve_curve_for_vessel(self, imo_or_mmsi: str, operational_mode: bool = True) -> Dict[str, Any]:
        """Resolve applicable engine SFOC curve for a vessel from the machinery registry.

        Strict Fail-Closed Invariants:
        1. If engine particulars are missing or UNAVAILABLE, returns UNAVAILABLE without guessing.
        2. In operational_mode=True, research-only or restricted curves return status UNAVAILABLE (operational_availability: RESTRICTED).
        3. In operational_mode=False, returns full research metadata including authenticity and permitted use.
        """
        clean_key = str(imo_or_mmsi).strip()
        vessel = (
            self.db.query(Vessel)
            .filter((Vessel.imo == clean_key) | (Vessel.mmsi == clean_key))
            .first()
        )

        if not vessel:
            return {
                "vessel_identifier": clean_key,
                "status": VerificationStatus.UNAVAILABLE,
                "operational_availability": "UNAVAILABLE",
                "curve": None,
                "message": f"Vessel '{clean_key}' not found in registry.",
            }

        if not vessel.engine_model or not vessel.engine_manufacturer:
            return {
                "vessel_identifier": clean_key,
                "vessel_name": vessel.vessel_name,
                "imo": vessel.imo,
                "status": VerificationStatus.UNAVAILABLE,
                "operational_availability": "UNAVAILABLE",
                "curve": None,
                "message": f"Engine particulars are UNAVAILABLE for vessel '{vessel.vessel_name}' (IMO {vessel.imo}). Ingestion fail-closed.",
            }

        # Resolve in research mode first to inspect technical applicability and permitted use
        curve_research, scope_research, msg_research = self.resolve_curve_for_engine(
            manufacturer=vessel.engine_manufacturer,
            model=vessel.engine_model,
            variant=vessel.engine_variant,
            rated_power_kw=vessel.rated_power_kw,
            operational_mode=False,
        )

        if not curve_research:
            return {
                "vessel_identifier": clean_key,
                "vessel_name": vessel.vessel_name,
                "imo": vessel.imo,
                "status": VerificationStatus.UNAVAILABLE,
                "operational_availability": "UNAVAILABLE",
                "curve": None,
                "message": msg_research,
            }

        # Operational mode check
        if operational_mode and curve_research.permitted_use != SfocPermittedUse.OPERATIONAL_PERMITTED:
            return {
                "vessel_identifier": clean_key,
                "vessel_name": vessel.vessel_name,
                "imo": vessel.imo,
                "status": VerificationStatus.UNAVAILABLE,
                "operational_availability": "RESTRICTED",
                "permitted_use": curve_research.permitted_use,
                "curve_code": curve_research.curve_code,
                "applicability_scope": scope_research,
                "authenticity_status": curve_research.authenticity_status,
                "curve": None,
                "message": (
                    f"Engine SFOC curve '{curve_research.curve_code}' is classified as {curve_research.permitted_use} "
                    f"and is restricted from operational voyage/chartering calculations. Pass operational_mode=False for research/audit access."
                ),
            }

        return {
            "vessel_identifier": clean_key,
            "vessel_name": vessel.vessel_name,
            "imo": vessel.imo,
            "status": VerificationStatus.DIRECTLY_VERIFIED if scope_research == CurveApplicabilityScope.EXACT_VARIANT else VerificationStatus.SOURCE_REPORTED,
            "operational_availability": "PERMITTED" if curve_research.permitted_use == SfocPermittedUse.OPERATIONAL_PERMITTED else "RESEARCH_ONLY",
            "curve_code": curve_research.curve_code,
            "authenticity_status": curve_research.authenticity_status,
            "applicability_scope": scope_research,
            "permitted_use": curve_research.permitted_use,
            "licensing_status": curve_research.licensing_status,
            "source_citation_details": curve_research.source_citation_details,
            "tuning_strategy": curve_research.tuning_strategy,
            "min_load_percent": curve_research.min_load_percent,
            "max_load_percent": curve_research.max_load_percent,
            "tolerance_pct": curve_research.tolerance_pct,
            "message": msg_research,
        }

    def interpolate_sfoc(
        self,
        curve_identifier: Union[str, int, EngineSfocCurve],
        load_percent: float,
        operational_mode: bool = True,
    ) -> SfocInterpolationResult:
        """Evaluate or interpolate engine SFOC at a specified documented load percentage.

        Mathematical Invariants & Safety Guarantees:
        1. Operational Safeguard: if operational_mode=True, restricted or research-only curves raise SfocRestrictedUseError.
        2. Original manufacturer load points are preserved exactly (is_interpolated = False).
        3. Intermediate points use piecewise linear interpolation (is_interpolated = True).
        4. ZERO EXTRAPOLATION: load < min_load or load > max_load raises SfocLoadOutOfRangeError.
        5. Non-finite or non-positive load values raise InvalidLoadPercentError.
        6. Deterministic outputs with preserved authenticity, tolerance, and reference conditions.

        Args:
            curve_identifier: Curve code string, curve ID integer, or EngineSfocCurve instance.
            load_percent: Target engine load percentage (e.g. 70.0 for 70% MCR).
            operational_mode: Set True (default) for fail-closed operational calculations; False for research/audit.

        Returns:
            SfocInterpolationResult: Immutable calculation result.
        """
        # 1. Validate load percentage
        if not isinstance(load_percent, (int, float)) or not math.isfinite(load_percent):
            raise InvalidLoadPercentError(f"Engine load must be a finite numeric value, got '{load_percent}'")
        if load_percent <= 0:
            raise InvalidLoadPercentError(f"Engine load must be strictly positive, got {load_percent}%")

        # 2. Resolve curve
        if isinstance(curve_identifier, EngineSfocCurve):
            curve = curve_identifier
        elif isinstance(curve_identifier, int):
            curve = self.get_curve_by_id(curve_identifier)
        elif isinstance(curve_identifier, str):
            curve = self.get_curve(curve_identifier)
        else:
            raise ValueError(f"Unsupported curve identifier type: {type(curve_identifier)}")

        if not curve:
            raise SfocCurveNotFoundError(f"SFOC curve not found: '{curve_identifier}'")

        # 3. Operational Safeguard Enforcement
        if operational_mode and curve.permitted_use != SfocPermittedUse.OPERATIONAL_PERMITTED:
            raise SfocRestrictedUseError(
                f"SFOC curve '{curve.curve_code}' has permitted_use='{curve.permitted_use}'. "
                f"Operational evaluation is restricted without commercial licensing. "
                f"Pass operational_mode=False for research/audit evaluation."
            )

        # Sort documented load points
        pts = sorted(curve.load_points, key=lambda p: p.load_percent)
        if not pts:
            raise EngineSfocError(f"SFOC curve '{curve.curve_code}' contains no documented load points.")

        min_load = curve.min_load_percent
        max_load = curve.max_load_percent

        # 4. Strict out-of-range enforcement (no extrapolation)
        if load_percent < min_load:
            raise SfocLoadOutOfRangeError(
                f"Engine load {load_percent:.1f}% is below minimum documented load point {min_load:.1f}% "
                f"for curve '{curve.curve_code}'. Extrapolation is strictly prohibited."
            )
        if load_percent > max_load:
            raise SfocLoadOutOfRangeError(
                f"Engine load {load_percent:.1f}% is above maximum documented load point {max_load:.1f}% "
                f"for curve '{curve.curve_code}'. Extrapolation is strictly prohibited."
            )

        # 5. Check for exact match with documented load point
        for pt in pts:
            if math.isclose(load_percent, pt.load_percent, abs_tol=1e-5):
                return SfocInterpolationResult(
                    curve_code=curve.curve_code,
                    engine_manufacturer=curve.engine_manufacturer,
                    engine_model=curve.engine_model,
                    engine_variant=curve.engine_variant,
                    authenticity_status=curve.authenticity_status,
                    applicability_scope=curve.applicability_scope,
                    permitted_use=curve.permitted_use,
                    operational_mode=operational_mode,
                    load_percent=float(pt.load_percent),
                    sfoc_g_kwh=float(pt.sfoc_g_kwh),
                    is_interpolated=False,
                    is_guaranteed=bool(pt.is_guaranteed),
                    tolerance_pct=pt.tolerance_pct if pt.tolerance_pct is not None else curve.tolerance_pct,
                    lower_bound_point=(float(pt.load_percent), float(pt.sfoc_g_kwh)),
                    upper_bound_point=(float(pt.load_percent), float(pt.sfoc_g_kwh)),
                    method="exact_manufacturer_point",
                    fuel_type=curve.fuel_type,
                    fuel_lcv_mj_kg=float(curve.fuel_lcv_mj_kg),
                    reference_conditions=curve.reference_conditions,
                    licensing_status=curve.licensing_status,
                    source_citation_details=curve.source_citation_details,
                )

        # 6. Piecewise linear interpolation between adjacent documented points
        p_lower: Optional[EngineSfocLoadPoint] = None
        p_upper: Optional[EngineSfocLoadPoint] = None

        for i in range(len(pts) - 1):
            if pts[i].load_percent < load_percent < pts[i + 1].load_percent:
                p_lower = pts[i]
                p_upper = pts[i + 1]
                break

        if not p_lower or not p_upper:
            raise SfocLoadOutOfRangeError(
                f"Load {load_percent:.1f}% cannot be bracketed by documented points for '{curve.curve_code}'."
            )

        l_span = p_upper.load_percent - p_lower.load_percent
        if l_span <= 0:
            raise EngineSfocError(f"Invalid load point spacing on curve '{curve.curve_code}'")

        fraction = (load_percent - p_lower.load_percent) / l_span
        interp_sfoc = p_lower.sfoc_g_kwh + fraction * (p_upper.sfoc_g_kwh - p_lower.sfoc_g_kwh)

        tol_lower = p_lower.tolerance_pct if p_lower.tolerance_pct is not None else curve.tolerance_pct
        tol_upper = p_upper.tolerance_pct if p_upper.tolerance_pct is not None else curve.tolerance_pct
        if tol_lower is not None and tol_upper is not None:
            interp_tol = tol_lower + fraction * (tol_upper - tol_lower)
        else:
            interp_tol = tol_lower or tol_upper or curve.tolerance_pct

        return SfocInterpolationResult(
            curve_code=curve.curve_code,
            engine_manufacturer=curve.engine_manufacturer,
            engine_model=curve.engine_model,
            engine_variant=curve.engine_variant,
            authenticity_status=curve.authenticity_status,
            applicability_scope=curve.applicability_scope,
            permitted_use=curve.permitted_use,
            operational_mode=operational_mode,
            load_percent=round(load_percent, 4),
            sfoc_g_kwh=round(interp_sfoc, 4),
            is_interpolated=True,
            is_guaranteed=False,
            tolerance_pct=round(interp_tol, 2) if interp_tol is not None else None,
            lower_bound_point=(float(p_lower.load_percent), float(p_lower.sfoc_g_kwh)),
            upper_bound_point=(float(p_upper.load_percent), float(p_upper.sfoc_g_kwh)),
            method="piecewise_linear_interpolation",
            fuel_type=curve.fuel_type,
            fuel_lcv_mj_kg=float(curve.fuel_lcv_mj_kg),
            reference_conditions=curve.reference_conditions,
            licensing_status=curve.licensing_status,
            source_citation_details=curve.source_citation_details,
        )
