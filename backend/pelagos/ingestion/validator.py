"""Telemetry validation pipeline enforcing kinematic, coordinate, temporal, and provenance rules."""

import math
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
from pelagos.ingestion.schemas import TelemetryRecord


@dataclass
class TelemetryValidationResult:
    is_valid: bool
    record: Optional[TelemetryRecord] = None
    error_code: Optional[str] = None
    error_details: Optional[str] = None


# Provisional performance envelopes per vessel category (REQ-FR-DII-002)
ABSOLUTE_MAX_SPEED_KNOTS: float = 70.0
STANDARD_DISPLACEMENT_BASELINE_KNOTS: float = 25.0

# Provisional operational envelopes based on naval architecture baselines (pending empirical calibration)
VESSEL_SPEED_ENVELOPES: Dict[str, float] = {
    "Bulk Carrier": 22.0,       # Capesize/Panamax/Supramax: 10-15 kts operational, max physical ~18-20 kts (provisional)
    "Tanker": 22.0,             # VLCC/Suezmax/Aframax: 11-16 kts operational, max physical ~18-20 kts (provisional)
    "Tug": 20.0,                # Tug / Towing: 6-13 kts operational, max transit ~18 kts (provisional)
    "Fishing": 20.0,            # Trawlers / Longliners: 8-12 kts, max transit ~16 kts (provisional)
    "Container": 32.0,          # Container liners: 18-26 kts operational, max fast container ~28-30 kts (provisional)
    "Cargo": 30.0,              # General cargo / multi-purpose: 12-18 kts, max ~25 kts (provisional)
    "Passenger": 32.0,          # Conventional passenger / cruise: 18-24 kts, max ~28 kts (provisional)
    "Sailing": 40.0,            # Cruising sail 8-12 kts; foiling racing multihulls up to ~40 kts (provisional)
    "High Speed Craft": 60.0,   # HSC / Catamaran ferries / Hydrofoils: 35-55 kts operational (provisional)
    "Fast Craft": 50.0,         # Pilot / Patrol / SAR / Law Enforcement / Fast Pleasure: 30-45 kts (provisional)
}


def classify_vessel_category(
    vessel_type: Optional[str] = None, 
    vessel_type_code: Optional[int] = None
) -> Optional[str]:
    """
    Resolve vessel classification into provisional operational categories
    for kinematic screening (REQ-FR-DII-002).
    """
    if vessel_type_code is not None:
        try:
            code = int(vessel_type_code)
            if 40 <= code <= 49 or 20 <= code <= 29:
                return "High Speed Craft"
            if 80 <= code <= 89:
                return "Tanker"
            if code == 30:
                return "Fishing"
            if code in (31, 32, 52):
                return "Tug"
            if code == 36:
                return "Sailing"
            if code in (37, 50, 51, 55):
                return "Fast Craft"
            if 60 <= code <= 69:
                return "Passenger"
            if 70 <= code <= 79:
                return "Cargo"
        except (ValueError, TypeError):
            pass

    if vessel_type:
        text = str(vessel_type).lower().strip()
        if any(hsc in text for hsc in ("high speed", "hsc", "hydrofoil", "fast ferry", "catamaran ferry")):
            return "High Speed Craft"
        if any(b in text for b in ("bulk", "capesize", "panamax", "supramax", "handysize", "ore carrier")):
            return "Bulk Carrier"
        if any(t in text for t in ("tanker", "lng", "lpg", "crude", "chemical")):
            return "Tanker"
        if "container" in text:
            return "Container"
        if any(p in text for p in ("pilot", "patrol", "search and rescue", "sar", "law enforcement", "military", "pleasure craft", "speedboat", "yacht")):
            return "Fast Craft"
        if any(tg in text for tg in ("tug", "towing")):
            return "Tug"
        if any(f in text for f in ("fishing", "trawler")):
            return "Fishing"
        if "cargo" in text:
            return "Cargo"
        if any(ps in text for ps in ("passenger", "cruise", "ferry")):
            return "Passenger"
        if "sailing" in text:
            return "Sailing"

    return None


def validate_vessel_speed(
    speed_knots: float,
    vessel_type: Optional[str] = None,
    vessel_type_code: Optional[int] = None
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Vessel-type-aware kinematic plausibility screening (REQ-FR-DII-002):
    - Confident rejection for negative speeds or speeds violating physical envelopes (ANOMALY_KINEMATIC_OUTLIER).
    - Quarantines unverified high speeds where particulars are insufficient to evaluate envelope (ANOMALY_INSUFFICIENT_PARTICULARS).
    - Accepts plausible high speeds for High-Speed Craft, pilot vessels, and fast craft.
    """
    if speed_knots < 0.0:
        return False, "ANOMALY_KINEMATIC_OUTLIER", f"Negative speed over ground reported: {speed_knots} kts."

    if speed_knots > ABSOLUTE_MAX_SPEED_KNOTS:
        return (
            False, 
            "ANOMALY_KINEMATIC_OUTLIER", 
            f"Speed {speed_knots} kts exceeds absolute physical ceiling for marine craft ({ABSOLUTE_MAX_SPEED_KNOTS} kts)."
        )

    category = classify_vessel_category(vessel_type, vessel_type_code)

    if category is not None:
        max_speed = VESSEL_SPEED_ENVELOPES[category]
        if speed_knots > max_speed:
            return (
                False,
                "ANOMALY_KINEMATIC_OUTLIER",
                f"Speed {speed_knots} kts exceeds maximum plausible threshold ({max_speed} kts) for vessel category '{category}'."
            )
        return True, None, None

    # Insufficient particulars: vessel category is unclassified or generic
    if speed_knots > STANDARD_DISPLACEMENT_BASELINE_KNOTS:
        return (
            False,
            "ANOMALY_INSUFFICIENT_PARTICULARS",
            f"Speed {speed_knots} kts exceeds standard displacement baseline ({STANDARD_DISPLACEMENT_BASELINE_KNOTS} kts), "
            f"but vessel category/particulars are insufficient to evaluate kinematic envelope. Quarantined for verification."
        )

    return True, None, None


def validate_telemetry_record(
    data: Dict[str, Any],
    default_source_name: str = "NOAA MarineCadastre / USCG NAIS",
    default_license: str = "CC0 1.0 Universal",
) -> TelemetryValidationResult:
    """
    Validate a raw telemetry dictionary against Pelagos data integrity rules:
    - REQ-NFR-DIP-001: Zero unlabelled synthetic data in production.
    - REQ-FR-DII-002: Spatial bounds and kinematic plausibility filtering.
    - REQ-FR-DII-003: Mandatory provenance and license metadata.
    - REQ-NFR-DIP-003: Multi-dimensional UTC temporal tracking and timezone verification.
    """
    # 1. Zero synthetic observation check (REQ-NFR-DIP-001)
    if data.get("is_synthetic") is True or data.get("synthetic") is True or data.get("simulation") is True:
        return TelemetryValidationResult(
            is_valid=False,
            error_code="ANOMALY_SYNTHETIC_DATA_REJECTED",
            error_details="Synthetic or simulated observations are strictly prohibited in production ingestion."
        )

    # 2. Identifier validation
    raw_mmsi = str(data.get("mmsi") or "").strip()
    if not raw_mmsi or len(raw_mmsi) < 5 or not raw_mmsi.isdigit():
        return TelemetryValidationResult(
            is_valid=False,
            error_code="ANOMALY_MISSING_IDENTIFIER",
            error_details=f"Invalid or missing MMSI: '{raw_mmsi}'. Must be a 5-9 digit numeric string."
        )

    # 3. Coordinate sanity checks (REQ-FR-DII-002)
    lat_val = data.get("latitude")
    lon_val = data.get("longitude")
    
    try:
        lat = float(lat_val)
        lon = float(lon_val)
    except (TypeError, ValueError):
        return TelemetryValidationResult(
            is_valid=False,
            error_code="ANOMALY_INVALID_COORDINATES",
            error_details=f"Non-numeric coordinates received: lat={lat_val}, lon={lon_val}."
        )
        
    if math.isnan(lat) or math.isnan(lon) or math.isinf(lat) or math.isinf(lon):
        return TelemetryValidationResult(
            is_valid=False,
            error_code="ANOMALY_INVALID_COORDINATES",
            error_details=f"NaN or Inf coordinates received: lat={lat}, lon={lon}."
        )
        
    if not (-90.0 <= lat <= 90.0):
        return TelemetryValidationResult(
            is_valid=False,
            error_code="ANOMALY_INVALID_COORDINATES",
            error_details=f"Latitude out of bounds: {lat}. Must be in [-90.0, 90.0]."
        )
        
    if not (-180.0 <= lon <= 180.0):
        return TelemetryValidationResult(
            is_valid=False,
            error_code="ANOMALY_INVALID_COORDINATES",
            error_details=f"Longitude out of bounds: {lon}. Must be in [-180.0, 180.0]."
        )

    # 4. Kinematic plausibility checks (REQ-FR-DII-002)
    speed_val = data.get("speed_knots")
    speed_knots = None
    if speed_val is not None:
        try:
            speed_knots = float(speed_val)
            if math.isnan(speed_knots) or math.isinf(speed_knots):
                speed_knots = None
            else:
                is_speed_valid, err_code, err_details = validate_vessel_speed(
                    speed_knots=speed_knots,
                    vessel_type=data.get("vessel_type"),
                    vessel_type_code=data.get("vessel_type_code")
                )
                if not is_speed_valid:
                    return TelemetryValidationResult(
                        is_valid=False,
                        error_code=err_code,
                        error_details=err_details
                    )
        except (ValueError, TypeError):
            speed_knots = None

    draft_val = data.get("draft_meters")
    draft_meters = None
    if draft_val is not None:
        try:
            draft_meters = float(draft_val)
            if math.isnan(draft_meters) or math.isinf(draft_meters):
                draft_meters = None
            elif draft_meters < 0.0 or draft_meters > 30.0:
                return TelemetryValidationResult(
                    is_valid=False,
                    error_code="ANOMALY_KINEMATIC_OUTLIER",
                    error_details=f"Implausible vessel draft reported: {draft_meters} m."
                )
        except (ValueError, TypeError):
            draft_meters = None

    # 5. Temporal governance and timezone verification (REQ-NFR-DIP-003)
    raw_obs_time = data.get("observation_time")
    if not raw_obs_time:
        return TelemetryValidationResult(
            is_valid=False,
            error_code="ANOMALY_MISSING_TIMESTAMP",
            error_details="Mandatory observation_time is missing."
        )

    source_name = str(data.get("source_name") or default_source_name).strip()
    source_verified = data.get("source_timezone_verified")
    if source_verified is None:
        source_verified = "NOAA" in source_name or "MarineCadastre" in source_name or "USCG" in source_name

    obs_time, tz_error = parse_and_verify_timestamp(
        raw_obs_time, 
        source_timezone_verified=bool(source_verified)
    )
    if tz_error:
        return TelemetryValidationResult(
            is_valid=False,
            error_code="ANOMALY_AMBIGUOUS_TIMEZONE",
            error_details=tz_error
        )

    pub_time = None
    raw_pub_time = data.get("publication_time")
    if raw_pub_time:
        pub_time, _ = parse_and_verify_timestamp(raw_pub_time, source_timezone_verified=bool(source_verified))

    # 6. Provenance & licensing metadata (REQ-FR-DII-003)
    source_name = str(data.get("source_name") or default_source_name).strip()
    license_type = str(data.get("license") or data.get("license_type") or default_license).strip()
    
    if not source_name:
        return TelemetryValidationResult(
            is_valid=False,
            error_code="ANOMALY_MISSING_PROVENANCE",
            error_details="Record lacks verifiable source_name."
        )

    # Build validated record
    record = TelemetryRecord(
        mmsi=raw_mmsi,
        imo=str(data.get("imo") or "").strip() or None,
        vessel_name=str(data.get("vessel_name") or raw_mmsi).strip(),
        vessel_type=str(data.get("vessel_type") or "Commercial").strip(),
        vessel_type_code=data.get("vessel_type_code"),
        latitude=round(lat, 5),
        longitude=round(lon, 5),
        speed_knots=round(speed_knots, 1) if speed_knots is not None else None,
        course_degrees=float(data.get("course_degrees")) if data.get("course_degrees") is not None else None,
        heading_degrees=float(data.get("heading_degrees")) if data.get("heading_degrees") is not None else None,
        draft_meters=round(draft_meters, 1) if draft_meters is not None else None,
        length_meters=float(data.get("length_meters")) if data.get("length_meters") is not None else None,
        width_meters=float(data.get("width_meters")) if data.get("width_meters") is not None else None,
        destination=str(data.get("destination") or "").strip() or None,
        navigational_status=str(data.get("navigational_status") or "").strip() or None,
        observation_time=obs_time,
        publication_time=pub_time,
        source_timezone_verified=True,
        source_name=source_name,
        license=license_type,
        raw_record_id=str(data.get("raw_record_id") or "").strip() or None,
    )

    return TelemetryValidationResult(is_valid=True, record=record)


def parse_and_verify_timestamp(
    raw_val: Any, 
    source_timezone_verified: bool = True
) -> Tuple[Optional[datetime], Optional[str]]:
    """
    Parse timestamp into UTC. If timezone-naive and source_timezone_verified is False,
    returns an error to trigger quarantine (REQ-NFR-DIP-003).
    """
    if isinstance(raw_val, datetime):
        dt = raw_val
    elif isinstance(raw_val, (int, float)):
        # Epoch seconds or milliseconds
        epoch = raw_val if raw_val < 1e11 else raw_val / 1000.0
        return datetime.fromtimestamp(epoch, tz=timezone.utc), None
    elif isinstance(raw_val, str):
        clean_str = raw_val.strip()
        try:
            # Check if explicit UTC or offset present
            if clean_str.endswith("Z"):
                dt = datetime.fromisoformat(clean_str[:-1] + "+00:00")
            elif "+" in clean_str or "-" in clean_str[10:]:
                dt = datetime.fromisoformat(clean_str)
            else:
                # Timezone-naive string
                if not source_timezone_verified:
                    return None, f"Timestamp '{raw_val}' is timezone-naive and source contract does not verify UTC."
                dt = datetime.fromisoformat(clean_str).replace(tzinfo=timezone.utc)
        except ValueError as err:
            return None, f"Invalid ISO 8601 timestamp '{raw_val}': {err}"
    else:
        return None, f"Unsupported timestamp format: {type(raw_val)}"

    if dt.tzinfo is None:
        if not source_timezone_verified:
            return None, "Timestamp lacks timezone information."
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return dt, None
