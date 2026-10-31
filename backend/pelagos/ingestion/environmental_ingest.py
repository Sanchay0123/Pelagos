"""Pelagos Oceanographic and Environmental Observation Ingestion Connector.

Parses, validates, and idempotently ingests genuine marine meteorological and
oceanographic buoy observations from verified sources (NOAA NDBC and INCOIS).

Traces to DATA-001 Section 5.2 and REQ-NFR-DIP-001.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from pelagos.models import EnvironmentalObservation, IngestionRun, QuarantinedRecord

logger = logging.getLogger(__name__)

DEFAULT_ENV_FIXTURE = (
    Path(__file__).resolve().parent.parent / "data" / "genuine_environmental_observations.json"
)


def validate_env_record(item: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate oceanographic buoy observation attributes and physical plausibility."""
    station_id = item.get("station_id")
    if not station_id or not isinstance(station_id, str):
        return False, "Missing or invalid station_id"

    obs_time = item.get("observation_time")
    if not obs_time:
        return False, f"Missing observation_time for station '{station_id}'"

    try:
        datetime.datetime.fromisoformat(obs_time.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False, f"Invalid ISO observation_time format '{obs_time}'"

    lat = item.get("latitude")
    lon = item.get("longitude")
    if lat is None or lon is None:
        return False, f"Missing coordinates for station '{station_id}'"

    try:
        lat_f = float(lat)
        lon_f = float(lon)
        if not (-90.0 <= lat_f <= 90.0) or not (-180.0 <= lon_f <= 180.0):
            return False, f"Coordinates out of bounds [{lon_f}, {lat_f}]"
    except (ValueError, TypeError):
        return False, f"Non-numeric coordinates [{lon}, {lat}]"

    wave_height = item.get("wave_height_meters")
    if wave_height is not None:
        try:
            wh = float(wave_height)
            if wh < 0.0 or wh > 35.0:  # Physical plausibility threshold
                return False, f"Unphysical significant wave height: {wh} m"
        except (ValueError, TypeError):
            return False, f"Non-numeric wave height: {wave_height}"

    return True, None


def ingest_environmental_observations(
    db: Session,
    fixture_path: Optional[Path] = None,
    source_name: str = "NOAA NDBC / INCOIS Buoy Network",
) -> Dict[str, Any]:
    """Ingest oceanographic buoy observations idempotently into environmental_observations table.

    Args:
        db: Active SQLAlchemy Session.
        fixture_path: Path to environmental observations JSON fixture.
        source_name: Source attribution label.

    Returns:
        Summary dict containing reconciliation metrics.
    """
    path = fixture_path or DEFAULT_ENV_FIXTURE
    if not path.is_file():
        raise FileNotFoundError(f"Environmental fixture not found at {path}")

    start_time = datetime.datetime.now(datetime.timezone.utc)
    ingestion_run = IngestionRun(
        run_type="bundled",
        source_name=source_name,
        source_partition=path.name,
        started_at=start_time,
        status="running",
    )
    db.add(ingestion_run)
    db.flush()

    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    raw_records: List[Dict[str, Any]] = payload.get("observations", [])
    records_read = len(raw_records)
    accepted_count = 0
    quarantined_count = 0
    duplicate_count = 0

    for item in raw_records:
        valid, err_msg = validate_env_record(item)
        if not valid:
            quarantined = QuarantinedRecord(
                source_name=source_name,
                source_partition=path.name,
                ingestion_run_id=ingestion_run.id,
                raw_record_id=str(item.get("station_id") or "unknown"),
                error_code="ENV_VALIDATION_FAILED",
                error_details=err_msg or "Unknown validation failure",
                raw_payload=json.dumps(item),
            )
            db.add(quarantined)
            quarantined_count += 1
            continue

        station_id = item["station_id"].strip()
        obs_time = datetime.datetime.fromisoformat(item["observation_time"].replace("Z", "+00:00"))
        idempotency_key = f"{source_name}:{station_id}:{obs_time.isoformat()}"

        existing = (
            db.query(EnvironmentalObservation)
            .filter(EnvironmentalObservation.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            existing.wave_height_meters = float(item["wave_height_meters"]) if item.get("wave_height_meters") is not None else None
            existing.wave_period_seconds = float(item["wave_period_seconds"]) if item.get("wave_period_seconds") is not None else None
            existing.wind_speed_knots = float(item["wind_speed_knots"]) if item.get("wind_speed_knots") is not None else None
            existing.wind_direction_degrees = float(item["wind_direction_degrees"]) if item.get("wind_direction_degrees") is not None else None
            existing.sea_surface_temp_c = float(item["sea_surface_temp_c"]) if item.get("sea_surface_temp_c") is not None else None
            existing.air_pressure_hpa = float(item["air_pressure_hpa"]) if item.get("air_pressure_hpa") is not None else None
            existing.quality_status = item.get("quality_status", "verified")
            duplicate_count += 1
        else:
            obs = EnvironmentalObservation(
                station_id=station_id,
                station_name=item.get("station_name", station_id),
                latitude=float(item["latitude"]),
                longitude=float(item["longitude"]),
                sea_area=item.get("sea_area", "Open Ocean"),
                observation_time=obs_time,
                wave_height_meters=float(item["wave_height_meters"]) if item.get("wave_height_meters") is not None else None,
                wave_period_seconds=float(item["wave_period_seconds"]) if item.get("wave_period_seconds") is not None else None,
                wind_speed_knots=float(item["wind_speed_knots"]) if item.get("wind_speed_knots") is not None else None,
                wind_direction_degrees=float(item["wind_direction_degrees"]) if item.get("wind_direction_degrees") is not None else None,
                sea_surface_temp_c=float(item["sea_surface_temp_c"]) if item.get("sea_surface_temp_c") is not None else None,
                air_pressure_hpa=float(item["air_pressure_hpa"]) if item.get("air_pressure_hpa") is not None else None,
                source_name=item.get("source_name", source_name),
                license_type=item.get("license_type", "Public Domain / Open Government Data"),
                quality_status=item.get("quality_status", "verified"),
                idempotency_key=idempotency_key,
            )
            db.add(obs)
            accepted_count += 1

    completed_time = datetime.datetime.now(datetime.timezone.utc)
    ingestion_run.records_read = records_read
    ingestion_run.accepted_count = accepted_count
    ingestion_run.quarantined_count = quarantined_count
    ingestion_run.duplicate_count = duplicate_count
    ingestion_run.status = "completed" if quarantined_count == 0 else "partial"
    ingestion_run.completed_at = completed_time

    db.commit()

    return {
        "run_id": ingestion_run.id,
        "records_read": records_read,
        "accepted_count": accepted_count,
        "quarantined_count": quarantined_count,
        "duplicate_count": duplicate_count,
        "status": ingestion_run.status,
    }
