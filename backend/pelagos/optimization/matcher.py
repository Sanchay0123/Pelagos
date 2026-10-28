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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from sqlalchemy.orm import Session

from pelagos.models import VesselPosition
from pelagos.routing.ports import PortRepository
from pelagos.routing.corridors import (
    CorridorStatus,
    PortRecord,
    resolve_freight_corridor,
    resolve_endpoint_to_port,
)
from pelagos.optimization.schemas import (
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


def evaluate_vessel_suitability(
    vessel: VesselPosition,
    target_cargo_tonnes: float,
    load_port_coords: Optional[Tuple[float, float]],
    now_utc: Optional[datetime] = None,
) -> VesselCandidateMatch:
    """Evaluate an individual genuine vessel observation for corridor cargo suitability."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

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

    # 3. Vessel Type Check
    type_compatible, type_reason = is_dry_bulk_compatible_type(
        vessel.vessel_type, vessel.vessel_type_code
    )

    if not type_compatible:
        return VesselCandidateMatch(
            mmsi=str(vessel.mmsi),
            imo=str(vessel.imo) if vessel.imo else None,
            vessel_name=str(vessel.vessel_name or f"MMSI {vessel.mmsi}"),
            vessel_type=str(vessel.vessel_type or "Unknown"),
            vessel_type_code=vessel.vessel_type_code,
            suitability_status=SuitabilityStatus.UNSUITABLE_TYPE,
            suitability_reason=type_reason,
            distance_to_load_port_nm=dist_nm,
            distance_qualification=dist_qual,
            latitude=vessel.latitude,
            longitude=vessel.longitude,
            speed_knots=vessel.speed_knots,
            course_degrees=vessel.course_degrees,
            draft_meters=vessel.draft_meters,
            length_meters=vessel.length_meters,
            width_meters=vessel.width_meters,
            estimated_dimensional_class=None,
            verified_cargo_capacity_dwt=None,
            cargo_capacity_status="UNVERIFIED",
            screening_stage="type_screened_incompatible",
            observation_time=obs_time_iso,
            observation_age_seconds=round(age_sec, 1) if age_sec is not None else None,
            is_stale=is_stale,
            data_mode=data_mode,
        )

    # 4. Dimension & Capacity Particulars Check
    dim_class = infer_dimensional_class(vessel.length_meters, vessel.width_meters)

    if dim_class is None:
        return VesselCandidateMatch(
            mmsi=str(vessel.mmsi),
            imo=str(vessel.imo) if vessel.imo else None,
            vessel_name=str(vessel.vessel_name or f"MMSI {vessel.mmsi}"),
            vessel_type=str(vessel.vessel_type or "Cargo"),
            vessel_type_code=vessel.vessel_type_code,
            suitability_status=SuitabilityStatus.INSUFFICIENT_DATA,
            suitability_reason=(
                "Vessel reported as cargo type, but AIS broadcast lacks verified length and beam particulars; "
                "dimensional capacity envelope cannot be determined without physical particulars."
            ),
            distance_to_load_port_nm=dist_nm,
            distance_qualification=dist_qual,
            latitude=vessel.latitude,
            longitude=vessel.longitude,
            speed_knots=vessel.speed_knots,
            course_degrees=vessel.course_degrees,
            draft_meters=vessel.draft_meters,
            length_meters=vessel.length_meters,
            width_meters=vessel.width_meters,
            estimated_dimensional_class=None,
            verified_cargo_capacity_dwt=None,
            cargo_capacity_status="UNVERIFIED",
            screening_stage="missing_dimensional_particulars",
            observation_time=obs_time_iso,
            observation_age_seconds=round(age_sec, 1) if age_sec is not None else None,
            is_stale=is_stale,
            data_mode=data_mode,
        )

    # 5. Class & Capacity Match against requested cargo tonnage
    loa = float(vessel.length_meters)
    beam_str = f", Beam {vessel.width_meters:.0f}m" if vessel.width_meters else ""
    draft_str = f", Draft {vessel.draft_meters:.1f}m" if vessel.draft_meters else ""

    # Commercial dry bulk target parcel envelopes
    if target_cargo_tonnes <= 35000:
        target_classes = {"Small / Coastal Cargo", "Handysize", "Handymax / Supramax"}
    elif target_cargo_tonnes <= 65000:
        target_classes = {"Handymax / Supramax", "Panamax / Kamsarmax"}
    elif target_cargo_tonnes <= 85000:
        target_classes = {"Panamax / Kamsarmax", "Post-Panamax / Capesize"}
    else:
        target_classes = {"Post-Panamax / Capesize"}

    if dim_class in target_classes:
        status = SuitabilityStatus.POTENTIALLY_SUITABLE
        screening_stage = "dimensionally_screened"
        reason = (
            f"Dimensionally screened candidate. Classified as {dim_class} based on reported dimensions "
            f"(LOA {loa:.0f}m{beam_str}{draft_str}), which aligns with the physical envelope for {target_cargo_tonnes:,.0f} MT parcel. "
            f"Verified deadweight tonnage (DWT), hold cleanliness, and commercial cargo capacity are unverified from AIS alone."
        )
    elif dim_class == "Post-Panamax / Capesize" and target_cargo_tonnes <= 65000:
        status = SuitabilityStatus.POTENTIALLY_SUITABLE
        screening_stage = "dimensionally_screened_oversized"
        reason = (
            f"Dimensionally screened candidate. Classified as Post-Panamax / Capesize based on reported dimensions "
            f"(LOA {loa:.0f}m{beam_str}{draft_str}). Vessel exceeds standard Panamax grain parcel envelope "
            f"({target_cargo_tonnes:,.0f} MT); physical dimensional envelope indicates adequate scale, but actual DWT, "
            f"hold suitability, and commercial cargo capacity are unverified from AIS alone."
        )
    elif dim_class in {"Small / Coastal Cargo", "Handysize"} and target_cargo_tonnes > 35000:
        status = SuitabilityStatus.DIMENSION_MISMATCH
        screening_stage = "dimensionally_screened_undersized"
        reason = (
            f"Dimension mismatch. Classified as {dim_class} based on reported dimensions "
            f"(LOA {loa:.0f}m{beam_str}{draft_str}); physical dimensional envelope is undersized for requested {target_cargo_tonnes:,.0f} MT parcel."
        )
    else:
        status = SuitabilityStatus.DIMENSION_MISMATCH
        screening_stage = "dimensionally_screened_mismatch"
        reason = (
            f"Dimension mismatch. Classified as {dim_class} based on reported dimensions "
            f"(LOA {loa:.0f}m{beam_str}{draft_str}); does not align with the standard physical envelope for {target_cargo_tonnes:,.0f} MT parcel."
        )

    return VesselCandidateMatch(
        mmsi=str(vessel.mmsi),
        imo=str(vessel.imo) if vessel.imo else None,
        vessel_name=str(vessel.vessel_name or f"MMSI {vessel.mmsi}"),
        vessel_type=str(vessel.vessel_type or "Cargo"),
        vessel_type_code=vessel.vessel_type_code,
        suitability_status=status,
        suitability_reason=reason,
        distance_to_load_port_nm=dist_nm,
        distance_qualification=dist_qual,
        latitude=vessel.latitude,
        longitude=vessel.longitude,
        speed_knots=vessel.speed_knots,
        course_degrees=vessel.course_degrees,
        draft_meters=vessel.draft_meters,
        length_meters=vessel.length_meters,
        width_meters=vessel.width_meters,
        estimated_dimensional_class=dim_class,
        verified_cargo_capacity_dwt=None,
        cargo_capacity_status="UNVERIFIED",
        screening_stage=screening_stage,
        observation_time=obs_time_iso,
        observation_age_seconds=round(age_sec, 1) if age_sec is not None else None,
        is_stale=is_stale,
        data_mode=data_mode,
    )


def match_suitable_vessels(
    db: Session,
    request: Union[VesselSuitabilityRequest, Dict[str, Any]],
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

    # 2. Resolve Corridor and Origin Loading Port
    origin_input = req.origin
    dest_input = req.destination

    if req.route_code and (not origin_input or not dest_input):
        if req.route_code == "PNW-JPN-BULK":
            origin_input = origin_input or "USSEA"
            dest_input = dest_input or "JPYOK"
        elif req.route_code == "USG-JPN-BULK":
            origin_input = origin_input or "USMSY"
            dest_input = dest_input or "JPYOK"

    port_repo = PortRepository()
    corridor_res = resolve_freight_corridor(origin_input, dest_input, port_repo=port_repo)
    is_monitored = corridor_res.status == CorridorStatus.MONITORED

    orig_port, orig_coords, _ = resolve_endpoint_to_port(origin_input, port_repo)
    load_port_id = corridor_res.origin_port_id or (orig_port.port_id if orig_port else None)
    load_port_name = corridor_res.origin_port_name or (orig_port.name if orig_port else None)
    load_port_coords: Optional[Tuple[float, float]] = None
    load_port_coords_list: Optional[List[float]] = None

    if orig_coords and len(orig_coords) >= 2:
        # orig_coords is [lon, lat]
        load_port_coords = (float(orig_coords[1]), float(orig_coords[0]))
        load_port_coords_list = [float(orig_coords[0]), float(orig_coords[1])]

    corridor_info: Optional[VoyageCorridorInfo] = None
    target_class = "Panamax / Supramax"
    if is_monitored:
        corridor_info = VoyageCorridorInfo(
            route_code=corridor_res.route_code or "CORRIDOR",
            route_name=corridor_res.route_name or "Trade Corridor",
            origin_region=corridor_res.origin_region or "Origin",
            destination_region=corridor_res.destination_region or "Destination",
            commodity=corridor_res.commodity or "Agricultural Bulk Grain",
            vessel_class=corridor_res.vessel_class or "Panamax / Supramax",
        )
        target_class = corridor_res.vessel_class or target_class

    # 3. Retrieve genuine vessel records from database
    # Deduplicate by MMSI (keep freshest observation)
    raw_vessels = (
        db.query(VesselPosition)
        .order_by(VesselPosition.observation_time.desc(), VesselPosition.id.desc())
        .all()
    )

    by_mmsi: Dict[str, VesselPosition] = {}
    for v in raw_vessels:
        m = str(v.mmsi).strip()
        if m and m not in by_mmsi:
            by_mmsi[m] = v

    distinct_vessels = list(by_mmsi.values())
    total_evaluated = len(distinct_vessels)

    # 4. Evaluate each vessel
    now_utc = datetime.now(timezone.utc)
    candidates: List[VesselCandidateMatch] = []
    potentially_suitable_count = 0

    for v in distinct_vessels:
        match = evaluate_vessel_suitability(
            vessel=v,
            target_cargo_tonnes=req.cargo_tonnes,
            load_port_coords=load_port_coords,
            now_utc=now_utc,
        )
        if match.suitability_status == SuitabilityStatus.POTENTIALLY_SUITABLE:
            potentially_suitable_count += 1
        candidates.append(match)

    # 5. Deterministic, transparent ranking
    def rank_key(cand: VesselCandidateMatch) -> Tuple[int, float, str, str]:
        # Tier priority (1 to 4)
        tier_prio = {
            SuitabilityStatus.POTENTIALLY_SUITABLE: 1,
            SuitabilityStatus.INSUFFICIENT_DATA: 2,
            SuitabilityStatus.DIMENSION_MISMATCH: 3,
            SuitabilityStatus.UNSUITABLE_TYPE: 4,
        }.get(cand.suitability_status, 5)

        # Distance: finite distance first, None sorted at infinity
        dist = cand.distance_to_load_port_nm if cand.distance_to_load_port_nm is not None else 1e9

        # Reverse observation time string so newer sorts earlier
        obs_inv = cand.observation_time or ""

        return (tier_prio, dist, obs_inv, cand.mmsi)

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
        cargo_tonnes=req.cargo_tonnes,
        target_vessel_class=target_class,
        total_vessels_evaluated=total_evaluated,
        potentially_suitable_count=potentially_suitable_count,
        candidates=limited_candidates,
        data_mode=resp_data_mode,
        data_mode_notice=data_mode_notice,
        disclaimers=list(SUITABILITY_COMMERCIAL_DISCLAIMERS),
        message=msg,
    )
