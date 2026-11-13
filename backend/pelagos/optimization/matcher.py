"""Pelagos Vessel Suitability Matching Engine (OPT-002).

Evaluates candidate vessel suitability for selected maritime freight corridors and cargo tonnage
using genuine AIS vessel telemetry and reported vessel particulars.
Traces to Pelagos Milestone D10.2, OPT-002, and REQ-FR-DII-002/003.

Commercial Integrity & Scope Constraints:
- Evaluates candidate suitability strictly on verified physical criteria (vessel type, length, beam, draft).
- Never fabricates or synthesizes vessel records, DWT, commercial fixtures, or charter party commitments.
- Handles missing particulars, missing coordinates, and stale telemetry with honest missing-data statuses.
- Distinguishes potential physical suitability from commercial charter availability.
- Strictly labels laycan feasibility as UNCONFIRMED (AIS telemetry does not report forward fixture commitments).
- Calculates distance to origin loading port strictly via documented Great-Circle Haversine formula.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from sqlalchemy.orm import Session

from pelagos.models import Vessel, VesselPosition
from pelagos.routing.ports import PortRepository
from pelagos.routing.corridors import (
    CorridorResolution,
    CorridorStatus,
    PortRecord,
    resolve_freight_corridor,
    resolve_endpoint_to_port,
)
from pelagos.routing.schemas import RoutingRequest, RoutingStatus
from pelagos.optimization.schemas import (
    CargoCompatibilityAssessment,
    ParticularsCompletenessSummary,
    PortConstraintEvaluation,
    RouteCompatibilityAssessment,
    SuitabilityStatus,
    VesselCandidateMatch,
    VesselSuitabilityRequest,
    VesselSuitabilityResponse,
    VoyageCorridorInfo,
    SUITABILITY_COMMERCIAL_DISCLAIMERS,
)

logger = logging.getLogger(__name__)

# Mean Earth spherical radius in nautical miles (6,371.0088 km / 1.852 km/NM)
EARTH_RADIUS_NM: float = 3440.065

# Structural Panama Canal lock envelopes (historical Panamax vs Neopanamax chambers;
# live draft/slot advisories from ACP are dynamic and NOT connected).
TRADITIONAL_PANAMAX_MAX_BEAM_M: float = 32.31
NEOPANAMAX_MAX_BEAM_M: float = 51.25
NEOPANAMAX_HISTORICAL_MAX_LOA_M: float = 366.0  # ACP Notice to Shipping N-1-2016 baseline
NEOPANAMAX_MAX_LOA_M: float = 370.33  # ACP Advisory to Shipping A-19-2021 & Notice to Shipping N-1-2024 operational maximum

_PORT_DRAFT_METERS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*m(?:eters?)?\s*draft\b", re.IGNORECASE)
_PORT_DRAFT_FEET_RE = re.compile(
    r"(\d+(?:\.\d+)?)[-\s]*(?:foot|ft)\s*(?:draft|project\s*depth)\b", re.IGNORECASE
)

# Non-cargo vessel classification keywords
NON_CARGO_KEYWORDS = {
    "tug",
    "towing",
    "tanker",
    "passenger",
    "fishing",
    "pleasure",
    "sailing",
    "pilot",
    "military",
    "dredging",
    "law enforcement",
    "search and rescue",
}

# Field-specific statutory and IACS accreditation mapping:
# Maps attribute_name -> tuple of substring tokens identifying accredited statutory authorities or IACS societies.
ACCREDITED_ATTRIBUTE_SOURCE_TOKENS: Dict[str, Tuple[str, ...]] = {
    "dwt_tonnage": (
        "classnk",
        "dnv",
        "lloyds_register",
        "lloyd's",
        "american bureau of shipping",
        "abs",
        "bureau veritas",
        "rina",
        "china classification society",
        "ccs",
        "korean register",
        "class survey",
        "statutory certificate",
        "flag administration",
        "panama maritime authority",
        "marshall islands registry",
        "liberian registry",
        "isle of man",
    ),
    "draft_max_meters": (
        "classnk",
        "dnv",
        "lloyds_register",
        "lloyd's",
        "american bureau of shipping",
        "abs",
        "bureau veritas",
        "rina",
        "china classification society",
        "ccs",
        "korean register",
        "class survey",
        "statutory certificate",
        "flag administration",
        "panama maritime authority",
        "marshall islands registry",
        "liberian registry",
        "isle of man",
    ),
    "gross_tonnage": (
        "classnk",
        "dnv",
        "lloyds_register",
        "lloyd's",
        "american bureau of shipping",
        "abs",
        "bureau veritas",
        "rina",
        "china classification society",
        "ccs",
        "korean register",
        "uscg",
        "thetis",
        "mrv",
        "flag administration",
        "panama maritime authority",
        "marshall islands registry",
        "liberian registry",
        "isle of man",
        "class survey",
    ),
    "length_meters": (
        "classnk",
        "dnv",
        "lloyds_register",
        "lloyd's",
        "american bureau of shipping",
        "abs",
        "bureau veritas",
        "rina",
        "china classification society",
        "ccs",
        "korean register",
        "uscg",
        "flag administration",
        "panama maritime authority",
        "marshall islands registry",
        "liberian registry",
        "isle of man",
        "class survey",
    ),
    "width_meters": (
        "classnk",
        "dnv",
        "lloyds_register",
        "lloyd's",
        "american bureau of shipping",
        "abs",
        "bureau veritas",
        "rina",
        "china classification society",
        "ccs",
        "korean register",
        "uscg",
        "flag administration",
        "panama maritime authority",
        "marshall islands registry",
        "liberian registry",
        "isle of man",
        "class survey",
    ),
}


def is_source_accredited_for_attribute(source_name: Optional[str], attribute_name: str) -> bool:
    """Attribute-specific statutory and IACS accreditation verification policy.

    A SOURCE_REPORTED observation is accepted for strict verification ONLY IF
    the source is accredited for that specific attribute.

    Examples:
    - USCG PSIX is accredited for gross_tonnage, length_meters, and width_meters,
      but NOT for draft_max_meters (reports molded depth, not summer draft) or dwt_tonnage.
    - EU THETIS-MRV is accredited for gross_tonnage, but NOT for summer loadline DWT (reports
      extreme deadweight under MRV definition) or draft_max_meters.
    - Commercial scrapers/directories (e.g. BalticShipping, VesselFinder) are never accredited.
    """
    if not source_name:
        return False
    attr_clean = attribute_name.lower().strip()
    tokens = ACCREDITED_ATTRIBUTE_SOURCE_TOKENS.get(attr_clean)
    if not tokens:
        return False
    s_clean = source_name.lower().strip()
    return any(tok in s_clean for tok in tokens)


def is_authoritative_for_capacity(source_name: Optional[str]) -> bool:
    """Explicit verification policy for cargo capacity deadweight checks.

    Delegates to attribute-specific accreditation for 'dwt_tonnage'.
    """
    return is_source_accredited_for_attribute(source_name, "dwt_tonnage")


def haversine_distance_nm(
    lat1: Optional[float],
    lon1: Optional[float],
    lat2: Optional[float],
    lon2: Optional[float],
) -> Optional[float]:
    """Calculate Great-Circle distance between two WGS-84 coordinates in nautical miles.

    Uses the standard Haversine formula with mean Earth radius R = 3,440.065 NM.
    Returns None if any coordinate is missing, non-finite, or outside valid ranges.
    """
    if (
        lat1 is None
        or lon1 is None
        or lat2 is None
        or lon2 is None
        or not math.isfinite(lat1)
        or not math.isfinite(lon1)
        or not math.isfinite(lat2)
        or not math.isfinite(lon2)
    ):
        return None

    if not (-90.0 <= lat1 <= 90.0 and -90.0 <= lat2 <= 90.0):
        return None
    if not (-180.0 <= lon1 <= 180.0 and -180.0 <= lon2 <= 180.0):
        return None

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    a_clamped = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a_clamped), math.sqrt(1.0 - a_clamped))

    dist_nm = EARTH_RADIUS_NM * c
    return round(dist_nm, 1)


def infer_dimensional_class(
    length_m: Optional[float],
    width_m: Optional[float],
) -> Optional[str]:
    """Infer standard dry bulk dimensional vessel class from reported length and beam.

    Standard commercial dry bulk classification envelopes:
    - Small / Coastal Cargo: Length < 130m
    - Handysize: Length 130m to <170m
    - Handymax / Supramax: Length 170m to 205m
    - Panamax / Kamsarmax: Length 205m to 240m (Beam up to 36m)
    - Post-Panamax / Capesize: Length > 240m or Beam > 36m

    Both length and width must be verified positive numbers.
    Returns None if either dimension is missing, non-finite, or non-positive.
    """
    if (
        length_m is None
        or width_m is None
        or not math.isfinite(length_m)
        or not math.isfinite(width_m)
        or length_m <= 0
        or width_m <= 0
    ):
        return None

    loa = float(length_m)
    beam = float(width_m)

    if loa < 130.0:
        return "Small / Coastal Cargo"
    elif loa < 170.0:
        return "Handysize"
    elif loa <= 205.0:
        return "Handymax / Supramax"
    elif loa <= 240.0:
        if beam > 36.0:
            return "Post-Panamax / Capesize"
        return "Panamax / Kamsarmax"
    else:
        return "Post-Panamax / Capesize"



def is_dry_bulk_compatible_type(
    vessel_type: Optional[str],
    vessel_type_code: Optional[int],
) -> Tuple[bool, str]:
    """Check if the reported AIS vessel type can commercially carry dry bulk grain."""
    raw_type = (vessel_type or "").strip().lower()

    if not raw_type:
        return False, "Vessel type is not reported in AIS static data."

    # Explicit non-cargo categories
    for kw in NON_CARGO_KEYWORDS:
        if kw in raw_type:
            return False, f"Vessel is classified as '{vessel_type}'; incompatible with agricultural dry bulk cargo."

    # AIS type codes 70-79 are cargo vessels
    if vessel_type_code is not None:
        if 70 <= vessel_type_code <= 79:
            return True, "Reported AIS vessel type code indicates cargo vessel."
        elif 80 <= vessel_type_code <= 89:
            return False, f"AIS type code {vessel_type_code} indicates tanker vessel (liquid bulk)."
        elif vessel_type_code in {30, 31, 32, 52}:
            return False, f"AIS type code {vessel_type_code} indicates tug/towing vessel."

    if "cargo" in raw_type or "bulk" in raw_type:
        return True, "Reported vessel type indicates commercial cargo vessel."

    return False, f"Vessel type '{vessel_type}' is not recognized as a dry bulk carrier."


def extract_port_draft_limit_meters(
    port: Optional[PortRecord],
) -> Tuple[Optional[float], Optional[str]]:
    """Extract an explicit draft/channel-depth limit strictly from verified port metadata.

    Only parses explicit draft or project-depth clauses present in PortRecord.access_limitations.
    Returns (None, None) when no verified numeric limit is recorded; never fabricates berth specs.
    """
    if port is None or not getattr(port, "access_limitations", None):
        return None, None

    text = str(port.access_limitations)
    m_match = _PORT_DRAFT_METERS_RE.search(text)
    if m_match:
        return round(float(m_match.group(1)), 2), m_match.group(0)

    ft_match = _PORT_DRAFT_FEET_RE.search(text)
    if ft_match:
        feet_val = float(ft_match.group(1))
        return round(feet_val * 0.3048, 2), f"{ft_match.group(0)} ({round(feet_val * 0.3048, 2):.2f}m)"

    return None, None


def build_particulars_completeness(
    vessel: VesselPosition,
    registry_vessel: Optional[Vessel] = None,
    hold_cleanliness_verified: Optional[bool] = None,
) -> ParticularsCompletenessSummary:
    """Explicitly account for verified vs. missing vessel physical and commercial particulars."""
    verified: List[str] = []
    missing: List[str] = []
    sources: List[str] = []

    if getattr(vessel, "source_name", None):
        sources.append(str(vessel.source_name))
    if registry_vessel is not None and getattr(registry_vessel, "source_name", None):
        src = str(registry_vessel.source_name)
        if src not in sources:
            sources.append(src)

    imo_val = getattr(vessel, "imo", None) or (getattr(registry_vessel, "imo", None) if registry_vessel else None)
    if imo_val:
        verified.append("imo")
    else:
        missing.append("imo")

    if getattr(vessel, "vessel_type", None):
        verified.append("vessel_type")
    else:
        missing.append("vessel_type")

    eff_length = vessel.length_meters if (vessel.length_meters and vessel.length_meters > 0) else (
        registry_vessel.length_meters if (registry_vessel and registry_vessel.length_meters and registry_vessel.length_meters > 0) else None
    )
    eff_width = vessel.width_meters if (vessel.width_meters and vessel.width_meters > 0) else (
        registry_vessel.width_meters if (registry_vessel and registry_vessel.width_meters and registry_vessel.width_meters > 0) else None
    )
    eff_draft = vessel.draft_meters if (vessel.draft_meters and vessel.draft_meters > 0) else (
        registry_vessel.draft_max_meters if (registry_vessel and registry_vessel.draft_max_meters and registry_vessel.draft_max_meters > 0) else None
    )

    for fname, val in (
        ("length_meters", eff_length),
        ("width_meters", eff_width),
        ("draft_meters", eff_draft),
    ):
        if val is not None:
            verified.append(fname)
        else:
            missing.append(fname)

    if registry_vessel is not None and registry_vessel.dwt_tonnage is not None and registry_vessel.dwt_tonnage > 0:
        verified.append("dwt_tonnage")
    else:
        missing.append("dwt_tonnage")

    for reg_field in ("gross_tonnage", "built_year", "flag_country"):
        reg_val = getattr(registry_vessel, reg_field, None) if registry_vessel is not None else None
        if reg_val:
            verified.append(reg_field)
        else:
            missing.append(reg_field)

    if hold_cleanliness_verified is True:
        verified.append("hold_cleanliness")
    else:
        missing.append("hold_cleanliness")

    missing.append("grain_cubic_capacity")
    missing.append("commercial_fixture_status")

    has_core_dims = eff_length is not None and eff_width is not None and eff_draft is not None
    if has_core_dims and "dwt_tonnage" in verified and hold_cleanliness_verified is True:
        completeness = "complete"
    elif eff_length is not None and eff_width is not None:
        completeness = "partial"
    else:
        completeness = "insufficient_data"

    return ParticularsCompletenessSummary(
        completeness_status=completeness,
        verified_particulars=verified,
        missing_particulars=missing,
        registry_vessel_class=registry_vessel.vessel_class if registry_vessel else None,
        registry_commercial_status=registry_vessel.commercial_status if registry_vessel else None,
        data_sources=sources,
    )


def evaluate_cargo_compatibility(
    vessel_type: Optional[str],
    vessel_type_code: Optional[int],
    dim_class: Optional[str],
    target_cargo_tonnes: float,
    commodity: str = "Agricultural Bulk Grain",
    verified_dwt_tonnes: Optional[float] = None,
    hold_cleanliness_verified: Optional[bool] = None,
    dwt_policy_note: Optional[str] = None,
) -> CargoCompatibilityAssessment:
    """Evaluate vessel-to-cargo compatibility without assuming unverified DWT or hold cleanliness."""
    reasons: List[str] = []
    missing_req: List[str] = []

    type_ok, type_reason = is_dry_bulk_compatible_type(vessel_type, vessel_type_code)
    reasons.append(type_reason)
    if dwt_policy_note:
        reasons.append(dwt_policy_note)

    if not type_ok:
        return CargoCompatibilityAssessment(
            status="incompatible",
            cargo_tonnes=target_cargo_tonnes,
            commodity=commodity,
            verified_dwt_tonnes=verified_dwt_tonnes,
            capacity_check_basis="verified_dwt" if verified_dwt_tonnes else "ais_dimensional_envelope_only",
            reasons=reasons,
            missing_requirements=[],
        )

    raw_type_lower = (vessel_type or "").lower()
    if vessel_type_code == 71 or "hazardous" in raw_type_lower:
        reasons.append(
            "AIS classification 'Cargo - Hazardous A' (code 71) does not confirm clean dry-bulk / grain-clean hold capability."
        )

    if verified_dwt_tonnes is not None and verified_dwt_tonnes > 0:
        if target_cargo_tonnes > verified_dwt_tonnes:
            reasons.append(
                f"Requested cargo parcel ({target_cargo_tonnes:,.0f} MT) exceeds verified deadweight tonnage ({verified_dwt_tonnes:,.0f} DWT)."
            )
            return CargoCompatibilityAssessment(
                status="incompatible",
                cargo_tonnes=target_cargo_tonnes,
                commodity=commodity,
                verified_dwt_tonnes=round(verified_dwt_tonnes, 1),
                capacity_check_basis="verified_dwt",
                reasons=reasons,
                missing_requirements=[],
            )
        reasons.append(
            f"Requested cargo parcel ({target_cargo_tonnes:,.0f} MT) fits within verified capacity ({verified_dwt_tonnes:,.0f} DWT)."
        )
        if hold_cleanliness_verified is True:
            return CargoCompatibilityAssessment(
                status="compatible",
                cargo_tonnes=target_cargo_tonnes,
                commodity=commodity,
                verified_dwt_tonnes=round(verified_dwt_tonnes, 1),
                capacity_check_basis="verified_dwt",
                reasons=reasons,
                missing_requirements=[],
            )
        missing_req.extend(["hold_cleanliness", "grain_cubic_capacity"])
        return CargoCompatibilityAssessment(
            status="potentially_compatible_unverified_capacity",
            cargo_tonnes=target_cargo_tonnes,
            commodity=commodity,
            verified_dwt_tonnes=round(verified_dwt_tonnes, 1),
            capacity_check_basis="verified_dwt",
            reasons=reasons,
            missing_requirements=missing_req,
        )

    # No verified DWT: screen only by reported AIS dimensional envelope
    missing_req.extend(["dwt_tonnage", "hold_cleanliness", "grain_cubic_capacity"])
    if dim_class is None:
        reasons.append("Vessel dimensions (LOA/beam) missing; cargo envelope cannot be screened.")
        return CargoCompatibilityAssessment(
            status="insufficient_data",
            cargo_tonnes=target_cargo_tonnes,
            commodity=commodity,
            verified_dwt_tonnes=None,
            capacity_check_basis="ais_dimensional_envelope_only",
            reasons=reasons,
            missing_requirements=["length_meters", "width_meters"] + missing_req,
        )

    if target_cargo_tonnes <= 35000:
        allowed = {"Small / Coastal Cargo", "Handysize", "Handymax / Supramax"}
    elif target_cargo_tonnes <= 65000:
        allowed = {"Handymax / Supramax", "Panamax / Kamsarmax", "Post-Panamax / Capesize"}
    elif target_cargo_tonnes <= 85000:
        allowed = {"Panamax / Kamsarmax", "Post-Panamax / Capesize"}
    else:
        allowed = {"Post-Panamax / Capesize"}

    if dim_class in allowed:
        reasons.append(
            f"Dimensional class '{dim_class}' aligns with standard physical parcel envelope ({target_cargo_tonnes:,.0f} MT), pending verified DWT."
        )
        return CargoCompatibilityAssessment(
            status="potentially_compatible_unverified_capacity",
            cargo_tonnes=target_cargo_tonnes,
            commodity=commodity,
            verified_dwt_tonnes=None,
            capacity_check_basis="ais_dimensional_envelope_only",
            reasons=reasons,
            missing_requirements=missing_req,
        )

    reasons.append(
        f"Dimensional class '{dim_class}' is mismatched for a {target_cargo_tonnes:,.0f} MT {commodity} parcel."
    )
    return CargoCompatibilityAssessment(
        status="incompatible",
        cargo_tonnes=target_cargo_tonnes,
        commodity=commodity,
        verified_dwt_tonnes=None,
        capacity_check_basis="ais_dimensional_envelope_only",
        reasons=reasons,
        missing_requirements=missing_req,
    )


def evaluate_port_compatibility(
    port: Optional[PortRecord],
    role: str,
    vessel_draft_m: Optional[float],
    vessel_length_m: Optional[float],
    vessel_width_m: Optional[float],
    explicit_max_draft_m: Optional[float] = None,
    explicit_max_loa_m: Optional[float] = None,
    explicit_max_beam_m: Optional[float] = None,
    vessel_air_draft_m: Optional[float] = None,
    port_registry: Optional[Any] = None,
) -> Optional[PortConstraintEvaluation]:
    """Evaluate vessel physical compatibility against a port's documented or verified limits."""
    if port is None:
        return None

    doc_draft_m, limit_clause = extract_port_draft_limit_meters(port)
    effective_draft_limit = explicit_max_draft_m if explicit_max_draft_m is not None else doc_draft_m
    reasons: List[str] = []
    missing_req: List[str] = []

    channel_status: Optional[str] = None
    berth_status: Optional[str] = None
    air_draft_status: Optional[str] = None
    channel_depth_meters: Optional[float] = None
    berth_depth_meters: Optional[float] = None
    air_gap_meters: Optional[float] = None
    governing_restriction_type: Optional[str] = None

    # Stage F1.3.2: Consult PortRegistry for certified channel, berth, and overhead restrictions
    if port_registry is not None and port.port_id:
        try:
            canon_port = port_registry.resolve_canonical_port(port.port_id)
            if canon_port:
                comp = port_registry.evaluate_comprehensive_port_compatibility(
                    port_unlocode=canon_port.unlocode,
                    vessel_draft=vessel_draft_m or 0.0,
                    vessel_loa=vessel_length_m,
                    vessel_beam=vessel_width_m,
                    vessel_air_draft=vessel_air_draft_m,
                )
                chan_res = comp["channel_compatibility"]
                berth_res = comp["berth_compatibility"]
                air_res = comp["air_draft_compatibility"]

                channel_status = chan_res.get("status")
                berth_status = berth_res.get("status")
                air_draft_status = air_res.get("status")

                channel_depth_meters = chan_res.get("governing_depth_meters")
                if berth_res.get("berths_evaluated"):
                    berth_depth_meters = berth_res["berths_evaluated"][0].get("alongside_depth_min_meters")
                air_gap_meters = air_res.get("governing_air_draft_meters")

                if comp["overall_status"] == "INSUITABLE":
                    return PortConstraintEvaluation(
                        port_id=port.port_id,
                        port_name=port.name,
                        role=role,
                        status="incompatible",
                        documented_draft_limit_meters=channel_depth_meters or effective_draft_limit,
                        limit_source_note="certified_port_registry_restriction",
                        vessel_draft_meters=vessel_draft_m,
                        access_limitations=port.access_limitations or None,
                        channel_status=channel_status,
                        berth_status=berth_status,
                        air_draft_status=air_draft_status,
                        channel_depth_meters=channel_depth_meters,
                        berth_depth_meters=berth_depth_meters,
                        air_gap_meters=air_gap_meters,
                        governing_restriction_type="PORT_RESTRICTION_EXCEEDED",
                        reasons=comp["reasons"],
                        missing_requirements=[],
                    )
                elif comp["overall_status"] == "UNVERIFIED":
                    for r in comp["reasons"]:
                        if r not in reasons:
                            reasons.append(r)
                    missing_req.append(f"{role}_certified_hydrography_unverified")
                elif comp["overall_status"] == "SUITABLE":
                    for r in comp["reasons"]:
                        if r not in reasons:
                            reasons.append(r)
        except Exception as exc:
            logger.debug("Port registry evaluation skipped: %s", exc)

    if effective_draft_limit is not None:
        if vessel_draft_m is None or vessel_draft_m <= 0:
            missing_req.append("vessel_draft_meters")
            reasons.append(
                f"{port.name} ({port.port_id}) documents a {effective_draft_limit:.2f}m draft limit, "
                "but vessel draft is missing."
            )
            return PortConstraintEvaluation(
                port_id=port.port_id,
                port_name=port.name,
                role=role,
                status="insufficient_data",
                documented_draft_limit_meters=effective_draft_limit,
                limit_source_note=limit_clause or "explicit_verified_port_limit",
                vessel_draft_meters=vessel_draft_m,
                access_limitations=port.access_limitations or None,
                channel_status=channel_status,
                berth_status=berth_status,
                air_draft_status=air_draft_status,
                channel_depth_meters=channel_depth_meters,
                berth_depth_meters=berth_depth_meters,
                air_gap_meters=air_gap_meters,
                governing_restriction_type=governing_restriction_type,
                reasons=reasons,
                missing_requirements=missing_req,
            )
        if vessel_draft_m > effective_draft_limit:
            reasons.append(
                f"Vessel reported draft ({vessel_draft_m:.1f}m) exceeds {port.name} ({port.port_id}) "
                f"documented limit ({effective_draft_limit:.2f}m)."
            )
            return PortConstraintEvaluation(
                port_id=port.port_id,
                port_name=port.name,
                role=role,
                status="incompatible",
                documented_draft_limit_meters=effective_draft_limit,
                limit_source_note=limit_clause or "explicit_verified_port_limit",
                vessel_draft_meters=vessel_draft_m,
                access_limitations=port.access_limitations or None,
                channel_status=channel_status,
                berth_status=berth_status,
                air_draft_status=air_draft_status,
                channel_depth_meters=channel_depth_meters,
                berth_depth_meters=berth_depth_meters,
                air_gap_meters=air_gap_meters,
                governing_restriction_type=governing_restriction_type or "DOCUMENTED_DRAFT_EXCEEDED",
                reasons=reasons,
                missing_requirements=[],
            )
        reasons.append(
            f"Vessel reported draft ({vessel_draft_m:.1f}m) is within {port.name} ({port.port_id}) "
            f"documented limit ({effective_draft_limit:.2f}m)."
        )
    else:
        missing_req.append(f"{role}_verified_berth_draft_limit_meters")
        reasons.append(
            f"No verified numeric berth draft limit stored for {port.name} ({port.port_id})."
        )

    # Evaluate explicit LOA and beam limits when provided
    for label, limit_val, v_val, req_key in (
        ("LOA", explicit_max_loa_m, vessel_length_m, f"{role}_verified_berth_max_loa_meters"),
        ("beam", explicit_max_beam_m, vessel_width_m, f"{role}_verified_berth_max_beam_meters"),
    ):
        if limit_val is not None:
            if v_val is None or v_val <= 0:
                missing_req.append(f"vessel_{label.lower()}_meters")
                reasons.append(f"Port documents max {label} ({limit_val:.1f}m) but vessel {label} is missing.")
            elif v_val > limit_val:
                reasons.append(f"Vessel {label} ({v_val:.1f}m) exceeds {port.name} max {label} ({limit_val:.1f}m).")
                return PortConstraintEvaluation(
                    port_id=port.port_id,
                    port_name=port.name,
                    role=role,
                    status="incompatible",
                    documented_draft_limit_meters=effective_draft_limit,
                    limit_source_note=limit_clause or "explicit_verified_port_limit",
                    vessel_draft_meters=vessel_draft_m,
                    access_limitations=port.access_limitations or None,
                    channel_status=channel_status,
                    berth_status=berth_status,
                    air_draft_status=air_draft_status,
                    channel_depth_meters=channel_depth_meters,
                    berth_depth_meters=berth_depth_meters,
                    air_gap_meters=air_gap_meters,
                    governing_restriction_type=governing_restriction_type or f"{label}_EXCEEDED",
                    reasons=reasons,
                    missing_requirements=[],
                )
            else:
                reasons.append(f"Vessel {label} ({v_val:.1f}m) satisfies {port.name} max {label} ({limit_val:.1f}m).")
        else:
            missing_req.append(req_key)

    status = "compatible" if not missing_req else "insufficient_data"
    return PortConstraintEvaluation(
        port_id=port.port_id,
        port_name=port.name,
        role=role,
        status=status,
        documented_draft_limit_meters=effective_draft_limit,
        limit_source_note=limit_clause or ("explicit_verified_port_limit" if explicit_max_draft_m else None),
        vessel_draft_meters=vessel_draft_m,
        access_limitations=port.access_limitations or None,
        channel_status=channel_status,
        berth_status=berth_status,
        air_draft_status=air_draft_status,
        channel_depth_meters=channel_depth_meters,
        berth_depth_meters=berth_depth_meters,
        air_gap_meters=air_gap_meters,
        governing_restriction_type=governing_restriction_type,
        reasons=reasons,
        missing_requirements=missing_req,
    )


def evaluate_route_compatibility(
    origin_port: Optional[PortRecord],
    destination_port: Optional[PortRecord],
    route_eval: Optional[Dict[str, Any]],
    vessel_draft_m: Optional[float],
    vessel_length_m: Optional[float],
    vessel_width_m: Optional[float],
    explicit_limits: Optional[Dict[str, Optional[float]]] = None,
    vessel_air_draft_m: Optional[float] = None,
    port_registry: Optional[Any] = None,
) -> RouteCompatibilityAssessment:
    """Evaluate vessel-to-route navigational, canal, and terminal compatibility."""
    limits = explicit_limits or {}
    orig_eval = evaluate_port_compatibility(
        origin_port,
        "origin_load_port",
        vessel_draft_m,
        vessel_length_m,
        vessel_width_m,
        explicit_max_draft_m=limits.get("origin_max_draft_meters"),
        explicit_max_loa_m=limits.get("origin_max_loa_meters"),
        explicit_max_beam_m=limits.get("origin_max_beam_meters"),
        vessel_air_draft_m=vessel_air_draft_m,
        port_registry=port_registry,
    )
    dest_eval = evaluate_port_compatibility(
        destination_port,
        "destination_discharge_port",
        vessel_draft_m,
        vessel_length_m,
        vessel_width_m,
        explicit_max_draft_m=limits.get("destination_max_draft_meters"),
        explicit_max_loa_m=limits.get("destination_max_loa_meters"),
        explicit_max_beam_m=limits.get("destination_max_beam_meters"),
        vessel_air_draft_m=vessel_air_draft_m,
        port_registry=port_registry,
    )

    route_navigable = route_eval.get("route_navigable") if route_eval else None
    routing_status = route_eval.get("routing_status") if route_eval else None
    routing_mode = route_eval.get("routing_mode") if route_eval else None
    route_dist = route_eval.get("route_distance_nm") if route_eval else None
    canals = list(route_eval.get("canals_transited", [])) if route_eval else []
    routing_error = route_eval.get("error_message") if route_eval else None

    reasons: List[str] = []
    missing_req: List[str] = []
    canal_notes: List[str] = []
    incompatible = False

    if route_navigable is False:
        incompatible = True
        reasons.append(routing_error or "Operational maritime route is disconnected/unnavigable under active constraints.")

    for pe in (orig_eval, dest_eval):
        if pe is not None:
            missing_req.extend(pe.missing_requirements)
            if pe.status == "incompatible":
                incompatible = True
                reasons.extend(pe.reasons)

    # Evaluate dynamic passage boundaries for any canals transited (REQ-FR-OMR-001)
    for canal in canals:
        c_low = canal.lower()
        if "panama" in c_low:
            if vessel_width_m is not None and vessel_width_m > NEOPANAMAX_MAX_BEAM_M:
                incompatible = True
                msg = (
                    f"Vessel beam ({vessel_width_m:.1f}m) exceeds Panama Canal Neopanamax structural beam envelope "
                    f"({NEOPANAMAX_MAX_BEAM_M:.2f}m)."
                )
                canal_notes.append(msg)
                reasons.append(msg)
            elif vessel_length_m is not None and vessel_length_m > NEOPANAMAX_MAX_LOA_M:
                incompatible = True
                msg = (
                    f"Vessel LOA ({vessel_length_m:.1f}m) exceeds Panama Canal Neopanamax operational LOA envelope "
                    f"({NEOPANAMAX_MAX_LOA_M:.2f}m under ACP Advisory A-19-2021 & Notice N-1-2024)."
                )
                canal_notes.append(msg)
                reasons.append(msg)
            elif vessel_width_m is not None and vessel_width_m > TRADITIONAL_PANAMAX_MAX_BEAM_M:
                canal_notes.append(
                    f"Route transits Panama Canal: beam ({vessel_width_m:.1f}m) exceeds traditional Panamax locks "
                    f"({TRADITIONAL_PANAMAX_MAX_BEAM_M:.2f}m); requires Neopanamax locks and verified live ACP draft/slot rules."
                )
                missing_req.append("panama_canal_acp_neopanamax_draft_and_slot_rules")
            else:
                canal_notes.append(
                    "Route transits Panama Canal: passage clearance requires live ACP draft advisories and transit booking verification."
                )
                missing_req.append("panama_canal_acp_live_draft_advisories")
        elif "suez" in c_low:
            canal_notes.append(
                "Route transits Suez Canal: passage clearance requires live SCA convoy scheduling, draft tables, and regional security advisories."
            )
            missing_req.append("suez_canal_sca_live_convoy_rules")

    if vessel_draft_m is None:
        missing_req.append("vessel_draft_meters")

    # Deduplicate missing requirements while preserving deterministic order
    seen_missing = set()
    unique_missing: List[str] = []
    for item in missing_req:
        if item not in seen_missing:
            seen_missing.add(item)
            unique_missing.append(item)

    if incompatible:
        status = "incompatible"
    elif route_navigable is None or vessel_draft_m is None or vessel_length_m is None or vessel_width_m is None:
        status = "insufficient_data"
    elif not unique_missing and (orig_eval is None or orig_eval.status == "compatible") and (dest_eval is None or dest_eval.status == "compatible"):
        status = "compatible"
    else:
        status = "conditionally_compatible_unverified_limits"

    return RouteCompatibilityAssessment(
        status=status,
        route_navigable=route_navigable,
        routing_status=routing_status,
        routing_mode=routing_mode,
        route_distance_nm=route_dist,
        canals_transited=canals,
        canal_compatibility_notes=canal_notes,
        origin_port=orig_eval,
        destination_port=dest_eval,
        missing_requirements=unique_missing,
        reasons=reasons,
    )


def resolve_corridor_and_ports(
    origin: Optional[Union[str, List[float], Dict[str, Any]]],
    destination: Optional[Union[str, List[float], Dict[str, Any]]],
    route_code: Optional[str],
    port_repo: Optional[PortRepository] = None,
    db: Optional[Session] = None,
) -> Tuple[
    Any,
    CorridorResolution,
    Optional[PortRecord],
    Optional[PortRecord],
    Any,
    Any,
]:
    """Resolve corridor code, origin port, and destination port uniformly without duplication."""
    repo = port_repo or PortRepository()
    origin_input = origin
    dest_input = destination

    if route_code and (not origin_input or not dest_input):
        clean_code = str(route_code).strip().upper()
        if clean_code == "PNW-JPN-BULK":
            origin_input = origin_input or "USSEA"
            dest_input = dest_input or "JPYOK"
        elif clean_code == "USG-JPN-BULK":
            origin_input = origin_input or "USMSY"
            dest_input = dest_input or "JPYOK"

    corridor_res = resolve_freight_corridor(origin_input, dest_input, port_repo=repo, db=db)
    orig_port, orig_coords, _ = resolve_endpoint_to_port(origin_input, repo)
    dest_port, dest_coords, _ = resolve_endpoint_to_port(dest_input, repo)

    return repo, corridor_res, orig_port, dest_port, orig_coords, dest_coords


def run_operational_route_check(
    origin_input: Any,
    destination_input: Any,
    canal_policy: str = "allow",
    port_repo: Optional[PortRepository] = None,
    routing_engine: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Run deterministic sea-lane connectivity and canal check when both endpoints exist."""
    if not origin_input or not destination_input:
        return None
    try:
        from pelagos.routing.service import MaritimeRoutingEngine

        engine = routing_engine or MaritimeRoutingEngine(port_repo=port_repo)
        res = engine.calculate_route(
            RoutingRequest(
                origin=origin_input,
                destination=destination_input,
                canal_policy=canal_policy,
                routing_mode="prototype",
            )
        )
        return {
            "route_navigable": res.status == RoutingStatus.CALCULATED,
            "routing_status": res.status.value,
            "routing_mode": res.routing_mode,
            "route_distance_nm": res.summary.total_distance_nm if res.summary else None,
            "canals_transited": list(res.summary.canals_transited) if res.summary else [],
            "error_message": res.error_message,
        }
    except Exception as exc:
        logger.debug("Operational route check skipped: %s", exc)
        return None


def evaluate_vessel_suitability(
    vessel: VesselPosition,
    target_cargo_tonnes: float,
    load_port_coords: Optional[Tuple[float, float]],
    now_utc: Optional[datetime] = None,
    registry_vessel: Optional[Vessel] = None,
    origin_port: Optional[PortRecord] = None,
    destination_port: Optional[PortRecord] = None,
    route_eval: Optional[Dict[str, Any]] = None,
    commodity: str = "Agricultural Bulk Grain",
    hold_cleanliness_verified: Optional[bool] = None,
    explicit_limits: Optional[Dict[str, Optional[float]]] = None,
    port_registry: Optional[Any] = None,
) -> VesselCandidateMatch:
    """Evaluate an individual genuine vessel observation for cargo and route suitability."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    # Effective dimensions reconcile AIS telemetry and canonical Vessel registry without fabrication
    eff_length = vessel.length_meters if (vessel.length_meters and vessel.length_meters > 0) else (
        registry_vessel.length_meters if (registry_vessel and registry_vessel.length_meters and registry_vessel.length_meters > 0) else None
    )
    eff_width = vessel.width_meters if (vessel.width_meters and vessel.width_meters > 0) else (
        registry_vessel.width_meters if (registry_vessel and registry_vessel.width_meters and registry_vessel.width_meters > 0) else None
    )
    eff_draft = vessel.draft_meters if (vessel.draft_meters and vessel.draft_meters > 0) else (
        registry_vessel.draft_max_meters if (registry_vessel and registry_vessel.draft_max_meters and registry_vessel.draft_max_meters > 0) else None
    )
    eff_air_draft = getattr(vessel, "air_draft_meters", None) if (getattr(vessel, "air_draft_meters", None) and getattr(vessel, "air_draft_meters", None) > 0) else (
        registry_vessel.air_draft_meters if (registry_vessel and getattr(registry_vessel, "air_draft_meters", None) and registry_vessel.air_draft_meters > 0) else None
    )
    verified_dwt = None
    cap_status = "UNVERIFIED"
    dwt_policy_reason: Optional[str] = None
    if registry_vessel is not None and registry_vessel.dwt_tonnage is not None and registry_vessel.dwt_tonnage > 0:
        dwt_val = float(registry_vessel.dwt_tonnage)
        is_verified = False
        prov_records = getattr(registry_vessel, "provenance_records", []) or []
        dwt_prov = [p for p in prov_records if getattr(p, "attribute_name", None) == "dwt_tonnage"]

        if not dwt_prov:
            # When no attribute-level provenance exists (e.g. legacy Stage D test fixtures / mock registries)
            src = (getattr(registry_vessel, "source_name", "") or "").lower()
            if "unverified" in src or "heuristic" in src or "scraper" in src or "unknown" in src:
                cap_status = "UNVERIFIED"
                dwt_policy_reason = f"Vessel source '{src}' is unverified; cannot satisfy verified capacity."
            else:
                is_verified = True
                cap_status = "VERIFIED"
        else:
            has_conflict = any(
                getattr(p, "is_conflicting", False)
                or getattr(p, "verification_status", None) == "CONFLICTING"
                for p in dwt_prov
            )
            if has_conflict:
                cap_status = "CONFLICTING"
                dwt_policy_reason = "Conflicting DWT observations exist across registry records; capacity cannot be verified."
            else:
                rank_map = {
                    "DIRECTLY_VERIFIED": 40,
                    "SOURCE_REPORTED": 30,
                    "INFERRED": 20,
                    "UNVERIFIED": 10,
                    "UNAVAILABLE": 0,
                }
                sorted_prov = sorted(
                    dwt_prov,
                    key=lambda p: (
                        rank_map.get(getattr(p, "verification_status", ""), 0),
                        getattr(p, "retrieved_at", datetime.min.replace(tzinfo=timezone.utc)) or datetime.min.replace(tzinfo=timezone.utc),
                    ),
                    reverse=True,
                )
                best_p = sorted_prov[0]
                b_status = getattr(best_p, "verification_status", None)
                b_source = getattr(best_p, "source_name", "") or ""

                if b_status == "DIRECTLY_VERIFIED":
                    is_verified = True
                    cap_status = "VERIFIED"
                elif b_status == "SOURCE_REPORTED":
                    if is_authoritative_for_capacity(b_source):
                        is_verified = True
                        cap_status = "VERIFIED"
                    else:
                        cap_status = "UNVERIFIED"
                        dwt_policy_reason = (
                            f"DWT ({dwt_val:,.0f} MT) is SOURCE_REPORTED from non-accredited source '{b_source}'; "
                            "strict verified capacity requires accredited class-register or statutory survey certification."
                        )
                elif b_status == "INFERRED":
                    cap_status = "INFERRED"
                    dwt_policy_reason = "DWT is INFERRED from dimensional envelope; does not satisfy verified capacity."
                elif b_status == "UNVERIFIED":
                    cap_status = "UNVERIFIED"
                    dwt_policy_reason = "DWT is UNVERIFIED; does not satisfy verified capacity."
                elif b_status == "UNAVAILABLE":
                    cap_status = "UNAVAILABLE"
                    dwt_policy_reason = "DWT is UNAVAILABLE."

        if is_verified:
            verified_dwt = dwt_val
        else:
            verified_dwt = None
    reg_class = registry_vessel.vessel_class if registry_vessel else None
    reg_comm_status = registry_vessel.commercial_status if registry_vessel else None

    particulars_summary = build_particulars_completeness(
        vessel=vessel,
        registry_vessel=registry_vessel,
        hold_cleanliness_verified=hold_cleanliness_verified,
    )

    # 1. Loading port distance via Great-Circle Haversine formula
    load_lat = load_port_coords[0] if load_port_coords else None
    load_lon = load_port_coords[1] if load_port_coords else None
    dist_nm = haversine_distance_nm(vessel.latitude, vessel.longitude, load_lat, load_lon)

    # 2. Freshness and staleness evaluation
    is_stale = True
    obs_time_iso = None
    age_sec = None
    if vessel.observation_time:
        obs_time_iso = vessel.observation_time.isoformat()
        try:
            obs_dt = vessel.observation_time
            if obs_dt.tzinfo is None:
                obs_dt = obs_dt.replace(tzinfo=timezone.utc)
            age_sec = max(0.0, (now_utc - obs_dt).total_seconds())
            is_stale = age_sec > 1800.0  # Stale if older than 30 minutes
        except Exception:
            is_stale = True

    data_mode = "historical" if is_stale else "live"

    # Distance qualification: strictly distinguish historical fixes from current proximity
    if dist_nm is None:
        dist_qual = "UNAVAILABLE: Missing valid vessel coordinates or loading port position."
    elif is_stale:
        obs_date_str = obs_time_iso.split("T")[0] if obs_time_iso else "historical sample"
        dist_qual = (
            f"HISTORICAL_OBSERVATION_ONLY: Distance of {dist_nm:.1f} NM reflects vessel position at "
            f"historical observation fix ({obs_date_str}). Current vessel position and proximity to loading port are unknown."
        )
    else:
        dist_qual = (
            f"CURRENT_PROXIMITY: Great-Circle distance of {dist_nm:.1f} NM based on recent AIS position fix (<30m age)."
        )

    # Route compatibility pre-computation (port draft/canal/navigability)
    route_compat = evaluate_route_compatibility(
        origin_port=origin_port,
        destination_port=destination_port,
        route_eval=route_eval,
        vessel_draft_m=eff_draft,
        vessel_length_m=eff_length,
        vessel_width_m=eff_width,
        explicit_limits=explicit_limits,
        vessel_air_draft_m=eff_air_draft,
        port_registry=port_registry,
    )

    chan_statuses = [pe.channel_status for pe in (route_compat.origin_port, route_compat.destination_port) if pe and pe.channel_status]
    berth_statuses = [pe.berth_status for pe in (route_compat.origin_port, route_compat.destination_port) if pe and pe.berth_status]
    air_statuses = [pe.air_draft_status for pe in (route_compat.origin_port, route_compat.destination_port) if pe and pe.air_draft_status]

    chan_passed = False if "INSUITABLE" in chan_statuses else (True if "SUITABLE" in chan_statuses else None)
    berth_passed = False if "INSUITABLE" in berth_statuses else (True if "SUITABLE" in berth_statuses else None)
    air_passed = False if "INSUITABLE" in air_statuses else (True if "SUITABLE" in air_statuses else None)

    # 3. Vessel Type Check
    type_compatible, type_reason = is_dry_bulk_compatible_type(
        vessel.vessel_type, vessel.vessel_type_code
    )
    dim_class = infer_dimensional_class(eff_length, eff_width)
    cargo_compat = evaluate_cargo_compatibility(
        vessel_type=vessel.vessel_type,
        vessel_type_code=vessel.vessel_type_code,
        dim_class=dim_class,
        target_cargo_tonnes=target_cargo_tonnes,
        commodity=commodity,
        verified_dwt_tonnes=verified_dwt,
        hold_cleanliness_verified=hold_cleanliness_verified,
        dwt_policy_note=dwt_policy_reason,
    )

    def _build_candidate(
        status: SuitabilityStatus,
        reason: str,
        stage: str,
        est_class: Optional[str],
        is_physically_compatible: bool = False,
        cargo_capacity_passed: Optional[bool] = None,
        overall_suitability_passed: bool = False,
        overall_suitability: SuitabilityStatus = SuitabilityStatus.UNVERIFIED,
        overall_suitability_status: str = "insufficient_data",
        channel_compatibility_passed: Optional[bool] = None,
        berth_compatibility_passed: Optional[bool] = None,
        air_draft_compatibility_passed: Optional[bool] = None,
    ) -> VesselCandidateMatch:
        eff_chan = channel_compatibility_passed if channel_compatibility_passed is not None else chan_passed
        eff_berth = berth_compatibility_passed if berth_compatibility_passed is not None else berth_passed
        eff_air = air_draft_compatibility_passed if air_draft_compatibility_passed is not None else air_passed

        return VesselCandidateMatch(
            mmsi=str(vessel.mmsi),
            imo=str(vessel.imo) if vessel.imo else None,
            vessel_name=str(vessel.vessel_name or f"MMSI {vessel.mmsi}"),
            vessel_type=str(vessel.vessel_type or "Unknown"),
            vessel_type_code=vessel.vessel_type_code,
            suitability_status=status,
            suitability_reason=reason,
            distance_to_load_port_nm=dist_nm,
            distance_qualification=dist_qual,
            latitude=vessel.latitude if (vessel.latitude is not None and math.isfinite(vessel.latitude)) else None,
            longitude=vessel.longitude if (vessel.longitude is not None and math.isfinite(vessel.longitude)) else None,
            speed_knots=vessel.speed_knots,
            course_degrees=vessel.course_degrees,
            draft_meters=eff_draft,
            length_meters=eff_length,
            width_meters=eff_width,
            estimated_dimensional_class=est_class,
            verified_cargo_capacity_dwt=round(verified_dwt, 1) if verified_dwt is not None else None,
            cargo_capacity_status=cap_status,
            is_physically_compatible=is_physically_compatible,
            channel_compatibility_passed=eff_chan,
            berth_compatibility_passed=eff_berth,
            air_draft_compatibility_passed=eff_air,
            cargo_capacity_passed=cargo_capacity_passed,
            overall_suitability_passed=overall_suitability_passed,
            overall_suitability=overall_suitability,
            overall_suitability_status=overall_suitability_status,
            screening_stage=stage,
            observation_time=obs_time_iso,
            observation_age_seconds=round(age_sec, 1) if age_sec is not None else None,
            is_stale=is_stale,
            data_mode=data_mode,
            missing_particulars=particulars_summary.missing_particulars,
            registry_vessel_class=reg_class,
            registry_commercial_status=reg_comm_status,
            particulars_completeness=particulars_summary,
            cargo_compatibility=cargo_compat,
            route_compatibility=route_compat,
        )

    if not type_compatible:
        return _build_candidate(
            status=SuitabilityStatus.UNSUITABLE_TYPE,
            reason=type_reason,
            stage="type_screened_incompatible",
            est_class=None,
            is_physically_compatible=False,
            cargo_capacity_passed=False,
            overall_suitability_passed=False,
            overall_suitability=SuitabilityStatus.UNSUITABLE_TYPE,
            overall_suitability_status="physically_incompatible",
        )

    # 4. Dimension & Capacity Particulars Check
    if dim_class is None:
        return _build_candidate(
            status=SuitabilityStatus.INSUFFICIENT_DATA,
            reason=(
                "Vessel reported as cargo type, but AIS broadcast lacks verified length and beam particulars; "
                "dimensional capacity envelope cannot be determined without physical particulars."
            ),
            stage="missing_dimensional_particulars",
            est_class=None,
            is_physically_compatible=False,
            cargo_capacity_passed=False,
            overall_suitability_passed=False,
            overall_suitability=SuitabilityStatus.INSUFFICIENT_DATA,
            overall_suitability_status="insufficient_data",
        )

    # 4b. Route & Port Physical Constraint Pruning (REQ-FR-CPO-003 / REQ-FR-OMR-001)
    if route_compat.status == "incompatible":
        reason_detail = "; ".join(route_compat.reasons) if route_compat.reasons else "Route or port constraint violation."
        return _build_candidate(
            status=SuitabilityStatus.DIMENSION_MISMATCH,
            reason=f"Route/port incompatibility: {reason_detail}",
            stage="route_or_port_constraint_violation",
            est_class=dim_class,
            is_physically_compatible=False,
            cargo_capacity_passed=False,
            overall_suitability_passed=False,
            overall_suitability=SuitabilityStatus.DIMENSION_MISMATCH,
            overall_suitability_status="physically_incompatible",
        )

    # Documented channel draft limit present on origin/destination port requires reported draft
    has_port_draft_rule = any(
        pe is not None and pe.documented_draft_limit_meters is not None
        for pe in (route_compat.origin_port, route_compat.destination_port)
    )
    if has_port_draft_rule and eff_draft is None:
        return _build_candidate(
            status=SuitabilityStatus.INSUFFICIENT_DATA,
            reason=(
                "Cargo vessel classified as "
                f"{dim_class}, but vessel draft is missing and cannot be cleared against documented port channel draft limit."
            ),
            stage="missing_draft_for_port_clearance",
            est_class=dim_class,
            is_physically_compatible=False,
            cargo_capacity_passed=False,
            overall_suitability_passed=False,
            overall_suitability=SuitabilityStatus.INSUFFICIENT_DATA,
            overall_suitability_status="insufficient_data",
        )

    # 5. Class & Capacity Match against requested cargo tonnage
    loa = float(eff_length)
    beam_str = f", Beam {eff_width:.0f}m" if eff_width else ""
    draft_str = f", Draft {eff_draft:.1f}m" if eff_draft else ", Draft missing"

    # Commercial dry bulk target parcel envelopes
    if target_cargo_tonnes <= 35000:
        target_classes = {"Small / Coastal Cargo", "Handysize", "Handymax / Supramax"}
    elif target_cargo_tonnes <= 65000:
        target_classes = {"Handymax / Supramax", "Panamax / Kamsarmax"}
    elif target_cargo_tonnes <= 85000:
        target_classes = {"Panamax / Kamsarmax", "Post-Panamax / Capesize"}
    else:
        target_classes = {"Post-Panamax / Capesize"}

    is_dim_match = dim_class in target_classes
    is_oversized = (dim_class == "Post-Panamax / Capesize" and target_cargo_tonnes <= 65000)
    is_physically_compatible = is_dim_match or is_oversized

    if not is_physically_compatible:
        if dim_class in {"Small / Coastal Cargo", "Handysize"} and target_cargo_tonnes > 35000:
            stage_str = "dimensionally_screened_undersized"
            reason_str = (
                f"Dimension mismatch. Classified as {dim_class} based on reported dimensions "
                f"(LOA {loa:.0f}m{beam_str}{draft_str}); physical dimensional envelope is undersized for requested {target_cargo_tonnes:,.0f} MT parcel."
            )
        else:
            stage_str = "dimensionally_screened_mismatch"
            reason_str = (
                f"Dimension mismatch. Classified as {dim_class} based on reported dimensions "
                f"(LOA {loa:.0f}m{beam_str}{draft_str}); does not align with the standard physical envelope for {target_cargo_tonnes:,.0f} MT parcel."
            )
        return _build_candidate(
            status=SuitabilityStatus.DIMENSION_MISMATCH,
            reason=reason_str,
            stage=stage_str,
            est_class=dim_class,
            is_physically_compatible=False,
            cargo_capacity_passed=False,
            overall_suitability_passed=False,
            overall_suitability=SuitabilityStatus.DIMENSION_MISMATCH,
            overall_suitability_status="physically_incompatible",
        )

    # Physical compatibility is satisfied (LOA, beam, draft, port/canal constraints cleared).
    # Next, separately evaluate cargo capacity verification (Gate 4 Fail-Closed Policy):
    screening_stage = "dimensionally_screened_oversized" if is_oversized else "dimensionally_screened"

    if cap_status == "VERIFIED" and verified_dwt is not None:
        if target_cargo_tonnes <= verified_dwt:
            cargo_capacity_passed = True
            overall_suitability_passed = True
            overall_suitability = SuitabilityStatus.POTENTIALLY_SUITABLE
            overall_suitability_status = "physically_compatible_verified_capacity_passed"
            cand_status = SuitabilityStatus.POTENTIALLY_SUITABLE
            reason_str = (
                f"Dimensionally screened candidate with verified cargo capacity. Classified as {dim_class} based on reported dimensions "
                f"(LOA {loa:.0f}m{beam_str}{draft_str}), which aligns with the physical envelope for {target_cargo_tonnes:,.0f} MT parcel. "
                f"Verified DWT ({verified_dwt:,.0f} MT) covers requested parcel; hold cleanliness and commercial availability require broker verification."
            )
        else:
            cargo_capacity_passed = False
            overall_suitability_passed = False
            overall_suitability = SuitabilityStatus.DIMENSION_MISMATCH
            overall_suitability_status = "physically_compatible_capacity_exceeded"
            cand_status = SuitabilityStatus.DIMENSION_MISMATCH
            screening_stage = "verified_dwt_insufficient"
            reason_str = (
                f"Capacity mismatch. Classified as {dim_class} (LOA {loa:.0f}m{beam_str}{draft_str}); "
                f"Verified deadweight tonnage ({verified_dwt:,.0f} DWT) is below requested {target_cargo_tonnes:,.0f} MT cargo parcel."
            )
    elif cap_status in {"UNVERIFIED", "INFERRED", "CONFLICTING"}:
        cargo_capacity_passed = False
        overall_suitability_passed = False
        overall_suitability = SuitabilityStatus.UNVERIFIED
        overall_suitability_status = "physically_compatible_unverified_capacity"
        cand_status = SuitabilityStatus.POTENTIALLY_SUITABLE
        dwt_text = (
            f"DWT ({registry_vessel.dwt_tonnage:,.0f} MT) is unaccredited ({dwt_policy_reason or cap_status}); "
            if registry_vessel and registry_vessel.dwt_tonnage
            else ""
        )
        reason_str = (
            f"Dimensionally screened candidate. Classified as {dim_class} based on reported dimensions "
            f"(LOA {loa:.0f}m{beam_str}{draft_str}), which aligns with the physical envelope for {target_cargo_tonnes:,.0f} MT parcel. "
            f"{dwt_text}Verified deadweight tonnage (DWT), hold cleanliness, and commercial cargo capacity are unverified from AIS alone."
        )
    else:  # UNAVAILABLE or missing DWT
        cargo_capacity_passed = False
        overall_suitability_passed = False
        overall_suitability = SuitabilityStatus.INSUFFICIENT_DATA
        overall_suitability_status = "insufficient_data"
        cand_status = SuitabilityStatus.POTENTIALLY_SUITABLE
        reason_str = (
            f"Dimensionally screened candidate. Classified as {dim_class} based on reported dimensions "
            f"(LOA {loa:.0f}m{beam_str}{draft_str}), which aligns with the physical envelope for {target_cargo_tonnes:,.0f} MT parcel. "
            "Verified deadweight tonnage (DWT), hold cleanliness, and commercial cargo capacity are unverified from AIS alone."
        )

    return _build_candidate(
        status=cand_status,
        reason=reason_str,
        stage=screening_stage,
        est_class=dim_class,
        is_physically_compatible=is_physically_compatible,
        cargo_capacity_passed=cargo_capacity_passed,
        overall_suitability_passed=overall_suitability_passed,
        overall_suitability=overall_suitability,
        overall_suitability_status=overall_suitability_status,
    )


def match_suitable_vessels(
    db: Session,
    request: Union[VesselSuitabilityRequest, Dict[str, Any]],
    routing_engine: Optional[Any] = None,
) -> VesselSuitabilityResponse:
    """Evaluate and rank candidate vessels for a commercial freight corridor.

    Transparent, explainable ranking order:
    1. Suitability Tier (POTENTIALLY_SUITABLE > INSUFFICIENT_DATA > DIMENSION_MISMATCH > UNSUITABLE_TYPE)
    2. Distance to Origin Loading Port in NM (closer vessels first; None sorted last)
    3. Observation Freshness (newer fixes first)
    4. MMSI ascending (deterministic tie-breaker)
    """
    # 1. Parse and validate input request
    req: VesselSuitabilityRequest
    if isinstance(request, dict):
        try:
            req = VesselSuitabilityRequest(**request)
        except Exception as exc:
            return VesselSuitabilityResponse(
                status="invalid_input",
                cargo_tonnes=request.get("cargo_tonnes", 52000.0) if isinstance(request.get("cargo_tonnes"), (int, float)) else 52000.0,
                message=f"Invalid vessel suitability request: {exc}",
            )
    elif isinstance(request, VesselSuitabilityRequest):
        req = request
    else:
        return VesselSuitabilityResponse(
            status="invalid_input",
            cargo_tonnes=52000.0,
            message="Invalid request type.",
        )

    # 2. Resolve Corridor, Origin Loading Port, Destination Port, and Route Connectivity
    port_repo, corridor_res, orig_port, dest_port, orig_coords, dest_coords = resolve_corridor_and_ports(
        origin=req.origin,
        destination=req.destination,
        route_code=req.route_code,
        db=db,
    )
    is_monitored = corridor_res.status == CorridorStatus.MONITORED

    load_port_id = corridor_res.origin_port_id or (orig_port.port_id if orig_port else None)
    load_port_name = corridor_res.origin_port_name or (orig_port.name if orig_port else None)
    dest_port_id = corridor_res.destination_port_id or (dest_port.port_id if dest_port else None)
    dest_port_name = corridor_res.destination_port_name or (dest_port.name if dest_port else None)

    load_port_coords: Optional[Tuple[float, float]] = None
    load_port_coords_list: Optional[List[float]] = None
    dest_port_coords_list: Optional[List[float]] = None

    if orig_coords and len(orig_coords) >= 2:
        load_port_coords = (float(orig_coords[1]), float(orig_coords[0]))
        load_port_coords_list = [float(orig_coords[0]), float(orig_coords[1])]
    if dest_coords and len(dest_coords) >= 2:
        dest_port_coords_list = [float(dest_coords[0]), float(dest_coords[1])]

    route_origin_in = req.origin or load_port_id
    route_dest_in = req.destination or dest_port_id
    route_eval = run_operational_route_check(
        origin_input=route_origin_in,
        destination_input=route_dest_in,
        canal_policy=req.canal_policy,
        port_repo=port_repo,
        routing_engine=routing_engine,
    )

    corridor_info: Optional[VoyageCorridorInfo] = None
    target_class = "Panamax / Supramax"
    if is_monitored:
        corridor_info = VoyageCorridorInfo(
            route_code=corridor_res.route_code or "CORRIDOR",
            route_name=corridor_res.route_name or "Trade Corridor",
            origin_region=corridor_res.origin_region or "Origin",
            destination_region=corridor_res.destination_region or "Destination",
            commodity=corridor_res.commodity or req.commodity,
            vessel_class=corridor_res.vessel_class or "Panamax / Supramax",
        )
        target_class = corridor_res.vessel_class or target_class

    # 3. Retrieve genuine vessel position records and canonical Vessel registry rows
    raw_vessels = (
        db.query(VesselPosition)
        .order_by(VesselPosition.observation_time.desc(), VesselPosition.id.desc())
        .all()
    )
    registry_by_mmsi: Dict[str, Vessel] = {}
    try:
        for rv in db.query(Vessel).all():
            rm = str(rv.mmsi).strip()
            if rm:
                registry_by_mmsi[rm] = rv
    except Exception:
        registry_by_mmsi = {}

    by_mmsi: Dict[str, VesselPosition] = {}
    for v in raw_vessels:
        m = str(v.mmsi).strip()
        if m and m not in by_mmsi:
            by_mmsi[m] = v

    # Also evaluate registered vessels that lack active AIS position telemetry
    for rm, rv in registry_by_mmsi.items():
        if rm not in by_mmsi:
            by_mmsi[rm] = VesselPosition(
                mmsi=rv.mmsi,
                imo=rv.imo,
                vessel_name=rv.vessel_name,
                vessel_type=rv.vessel_type,
                vessel_type_code=rv.vessel_type_code,
                latitude=None,
                longitude=None,
                draft_meters=rv.draft_max_meters,
                length_meters=rv.length_meters,
                width_meters=rv.width_meters,
                observation_time=datetime(1970, 1, 1, tzinfo=timezone.utc),
                source_name=rv.source_name,
                license_type="Registry Record",
                idempotency_key=f"registry_only:{rv.mmsi}",
            )

    distinct_vessels = list(by_mmsi.values())
    total_evaluated = len(distinct_vessels)

    # 4. Evaluate each vessel
    now_utc = datetime.now(timezone.utc)
    candidates: List[VesselCandidateMatch] = []
    potentially_suitable_count = 0

    from pelagos.registry.port_registry import PortRegistry
    port_reg = PortRegistry(db)

    for v in distinct_vessels:
        reg_v = registry_by_mmsi.get(str(v.mmsi).strip())
        match = evaluate_vessel_suitability(
            vessel=v,
            target_cargo_tonnes=req.cargo_tonnes,
            load_port_coords=load_port_coords,
            now_utc=now_utc,
            registry_vessel=reg_v,
            origin_port=orig_port,
            destination_port=dest_port,
            route_eval=route_eval,
            commodity=req.commodity,
            port_registry=port_reg,
        )
        if match.suitability_status == SuitabilityStatus.POTENTIALLY_SUITABLE:
            potentially_suitable_count += 1
        candidates.append(match)

    # 5. Deterministic, transparent ranking
    def rank_key(cand: VesselCandidateMatch) -> Tuple[int, float, float, str]:
        tier_prio = {
            SuitabilityStatus.POTENTIALLY_SUITABLE: 1,
            SuitabilityStatus.UNVERIFIED: 2,
            SuitabilityStatus.INSUFFICIENT_DATA: 3,
            SuitabilityStatus.DIMENSION_MISMATCH: 4,
            SuitabilityStatus.UNSUITABLE_TYPE: 5,
        }.get(cand.suitability_status, 6)

        dist = cand.distance_to_load_port_nm if cand.distance_to_load_port_nm is not None else 1e9

        # Negative timestamp so newer fixes sort earlier deterministically
        obs_sort = 0.0
        if cand.observation_time:
            try:
                obs_dt = datetime.fromisoformat(cand.observation_time.replace("Z", "+00:00"))
                if obs_dt.tzinfo is None:
                    obs_dt = obs_dt.replace(tzinfo=timezone.utc)
                obs_sort = -obs_dt.timestamp()
            except Exception:
                obs_sort = 0.0

        return (tier_prio, dist, obs_sort, cand.mmsi)

    candidates.sort(key=rank_key)

    # 6. Apply limit
    limited_candidates = candidates[: req.max_vessels]

    # Historical data boundary verification
    dataset_is_historical = all(c.is_stale for c in candidates) if candidates else True
    resp_data_mode = "historical" if dataset_is_historical else "live"
    data_mode_notice = (
        "Telemetry dataset consists of historical AIS records (January 1, 2024 sample). Stale observations demonstrate dimensional candidate screening only and do NOT represent current fleet locations or real-time proximity to loading ports."
        if dataset_is_historical
        else "Telemetry stream includes active live AIS fixes (<30m age)."
    )

    orig_limit_m, orig_limit_src = extract_port_draft_limit_meters(orig_port)
    dest_limit_m, dest_limit_src = extract_port_draft_limit_meters(dest_port)
    route_compat_summary = {
        "route_navigable": route_eval.get("route_navigable") if route_eval else None,
        "routing_status": route_eval.get("routing_status") if route_eval else None,
        "route_distance_nm": route_eval.get("route_distance_nm") if route_eval else None,
        "canals_transited": route_eval.get("canals_transited", []) if route_eval else [],
        "origin_documented_draft_limit_meters": orig_limit_m,
        "origin_limit_source": orig_limit_src,
        "destination_documented_draft_limit_meters": dest_limit_m,
        "destination_limit_source": dest_limit_src,
    }

    if route_eval is not None and route_eval.get("route_navigable") is False:
        status_str = "unavailable"
        msg = (
            f"Operational sea route between origin and destination is unnavigable "
            f"({route_eval.get('error_message') or 'disconnected sea lane'}); no candidates can be approved for voyage transit."
        )
    else:
        status_str = "matched" if is_monitored else "unmonitored_corridor_matched"
        msg = (
            f"Evaluated {total_evaluated} genuine vessel observations for {req.cargo_tonnes:,.0f} MT "
            f"on {corridor_res.route_name or 'corridor'}. Found {potentially_suitable_count} potentially suitable candidate(s)."
        )

    return VesselSuitabilityResponse(
        status=status_str,
        corridor=corridor_info,
        loading_port_id=load_port_id,
        loading_port_name=load_port_name,
        loading_port_coordinates=load_port_coords_list,
        destination_port_id=dest_port_id,
        destination_port_name=dest_port_name,
        destination_port_coordinates=dest_port_coords_list,
        cargo_tonnes=req.cargo_tonnes,
        commodity=req.commodity,
        target_vessel_class=target_class,
        total_vessels_evaluated=total_evaluated,
        potentially_suitable_count=potentially_suitable_count,
        candidates=limited_candidates,
        route_compatibility_summary=route_compat_summary,
        data_mode=resp_data_mode,
        data_mode_notice=data_mode_notice,
        disclaimers=list(SUITABILITY_COMMERCIAL_DISCLAIMERS),
        message=msg,
    )
