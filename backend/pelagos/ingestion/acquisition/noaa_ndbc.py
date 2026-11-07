"""Pelagos NOAA National Data Buoy Center (NDBC) Automated Acquisition Adapter.

Connects to official public NOAA NDBC standard meteorological realtime endpoints
(e.g. https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt).
Parses observation lines, converts units, validates physical plausibility,
separates observation time from retrieval time, and idempotently persists records.

Traces to DATA-001 Section 5.2, REQ-NFR-DIP-001, and Stage B Acquisition Mandates.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from pelagos.ingestion.acquisition.client import (
    AcquisitionError,
    AcquisitionHTTPError,
    AcquisitionResponse,
    ResilientHttpClient,
)
from pelagos.models import EnvironmentalObservation, IngestionRun, QuarantinedRecord

logger = logging.getLogger(__name__)

NDBC_REALTIME_URL_TEMPLATE = "https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt"
METERS_PER_SEC_TO_KNOTS = 1.943844

# Authoritative station geographic metadata with documented provenance.
# Source: National Oceanic and Atmospheric Administration (NOAA) National Data Buoy Center (NDBC)
# Verified against official station owner metadata pages: https://www.ndbc.noaa.gov/station_page.php?station={station_id}
KNOWN_NDBC_STATIONS: Dict[str, Dict[str, Any]] = {
    # Provenance: NOAA NDBC Moored Buoy 46041 (Cape Elizabeth), 3-meter discus buoy operated by NDBC.
    "46041": {
        "station_name": "Cape Elizabeth - 45NM NW of Aberdeen, WA",
        "latitude": 47.351,
        "longitude": -124.741,
        "sea_area": "North Pacific / Washington Coast",
        "provenance": "NOAA NDBC Station 46041 official mooring coordinates (47.351 N, 124.741 W); Source: https://www.ndbc.noaa.gov/station_page.php?station=46041",
    },
    # Provenance: NOAA NDBC Moored Buoy 42001 (Mid Gulf), 3-meter foam buoy operated by NDBC.
    "42001": {
        "station_name": "Mid Gulf - 180 NM South of Southwest Pass LA",
        "latitude": 25.897,
        "longitude": -89.663,
        "sea_area": "Gulf of Mexico / Mississippi River Approach",
        "provenance": "NOAA NDBC Station 42001 official mooring coordinates (25.897 N, 89.663 W)",
    },
    # Provenance: NOAA NDBC Moored Buoy 46029 (Columbia River Bar), 3-meter discus buoy operated by NDBC.
    "46029": {
        "station_name": "Columbia River Bar - 9 NM West of Columbia River Mouth",
        "latitude": 46.144,
        "longitude": -124.511,
        "sea_area": "North Pacific / Columbia River Approach",
        "provenance": "NOAA NDBC Station 46029 official mooring coordinates (46.144 N, 124.511 W)",
    },
    # Provenance: NOAA NDBC Moored Buoy 41009 (Cape Canaveral), 3-meter discus buoy operated by NDBC.
    "41009": {
        "station_name": "Canaveral 20 NM East of Cape Canaveral, FL",
        "latitude": 28.508,
        "longitude": -80.185,
        "sea_area": "Western North Atlantic",
        "provenance": "NOAA NDBC Station 41009 official mooring coordinates (28.508 N, 80.185 W)",
    },
    # Provenance: NOAA NDBC Moored Buoy 41002 (South Hatteras), 3-meter discus buoy operated by NDBC.
    "41002": {
        "station_name": "South Hatteras - 225 NM South of Cape Hatteras, NC",
        "latitude": 31.759,
        "longitude": -74.936,
        "sea_area": "Western North Atlantic",
        "provenance": "NOAA NDBC Station 41002 official mooring coordinates (31.759 N, 74.936 W)",
    },
    # Provenance: NOAA NDBC Moored Buoy 44025 (Long Island), 3-meter foam buoy operated by NDBC.
    "44025": {
        "station_name": "Long Island - 33 NM South of Islip, NY",
        "latitude": 40.251,
        "longitude": -73.164,
        "sea_area": "Western North Atlantic / New York Bight",
        "provenance": "NOAA NDBC Station 44025 official mooring coordinates (40.251 N, 73.164 W)",
    },
    # Provenance: NOAA NDBC Moored Buoy 46050 (Stonewall Bank), 3-meter foam buoy operated by NDBC.
    "46050": {
        "station_name": "Stonewall Bank - 20 NM West of Newport, OR",
        "latitude": 44.656,
        "longitude": -124.526,
        "sea_area": "North Pacific",
        "provenance": "NOAA NDBC Station 46050 official mooring coordinates (44.656 N, 124.526 W)",
    },
    # Provenance: NOAA NDBC Moored Buoy 51001 (Northwest Hawaii), 3-meter discus buoy operated by NDBC.
    "51001": {
        "station_name": "Northwest Hawaii - 170 NM NW of Kauai, HI",
        "latitude": 24.444,
        "longitude": -162.054,
        "sea_area": "Central Pacific Ocean",
        "provenance": "NOAA NDBC Station 51001 official mooring coordinates (24.444 N, 162.054 W)",
    },
}

DEFAULT_ENV_FIXTURE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "genuine_environmental_observations.json"
)


def _safe_float(val: Optional[str]) -> Optional[float]:
    """Parse string float, returning None if missing, empty, or 'MM'."""
    if not val or val.strip() in ("MM", "N/A", "null", ""):
        return None
    try:
        return float(val.strip())
    except (ValueError, TypeError):
        return None


def parse_ndbc_realtime_text(
    text: str,
    station_id: str,
    retrieval_time_utc: datetime.datetime,
    max_observations: int = 24,
) -> Tuple[List[Dict[str, Any]], int, List[str]]:
    """Parse whitespace-delimited NOAA NDBC standard meteorological realtime stream.

    Args:
        text: Raw text body from NOAA NDBC endpoint.
        station_id: 5-character NDBC station identifier.
        retrieval_time_utc: Timestamp when HTTP payload was received.
        max_observations: Maximum recent observations to parse.

    Returns:
        Tuple of (valid_records, unparseable_count, quarantine_reasons).
    """
    station_meta = KNOWN_NDBC_STATIONS.get(station_id)
    if not station_meta:
        logger.warning(
            f"NOAA NDBC station '{station_id}' coordinates are unavailable in verified station registry; "
            "coordinates will not be fabricated to (0.0, 0.0)."
        )
        station_name = f"NOAA NDBC Buoy {station_id}"
        latitude = None
        longitude = None
        sea_area = "Unknown / Unresolved Coordinates"
    else:
        station_name = station_meta["station_name"]
        latitude = station_meta["latitude"]
        longitude = station_meta["longitude"]
        sea_area = station_meta["sea_area"]

    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) < 3:
        return [], 0, [f"Payload too short ({len(lines)} lines), expected header + units + observations"]

    # Header line
    header_tokens = lines[0].lstrip("#").split()
    # Units line is lines[1]

    records: List[Dict[str, Any]] = []
    unparseable_count = 0
    quarantine_reasons: List[str] = []

    for line in lines[2 : 2 + max_observations]:
        tokens = line.split()
        if len(tokens) < 5:
            unparseable_count += 1
            quarantine_reasons.append(f"Observation line has insufficient tokens ({len(tokens)})")
            continue

        token_map = dict(zip(header_tokens, tokens))

        try:
            year = int(token_map["YY"])
            month = int(token_map["MM"])
            day = int(token_map["DD"])
            hour = int(token_map["hh"])
            minute = int(token_map["mm"])
            obs_dt = datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.timezone.utc)
        except (KeyError, ValueError) as exc:
            unparseable_count += 1
            quarantine_reasons.append(f"Invalid timestamp components: {exc}")
            continue

        # Wave metrics
        wave_height = _safe_float(token_map.get("WVHT"))
        wave_period_sec = _safe_float(token_map.get("APD")) or _safe_float(token_map.get("DPD"))

        # Wind metrics
        wspd_ms = _safe_float(token_map.get("WSPD"))
        wind_speed_knots = round(wspd_ms * METERS_PER_SEC_TO_KNOTS, 2) if wspd_ms is not None else None
        wind_dir = _safe_float(token_map.get("WDIR"))

        # Thermodynamic metrics
        air_pressure_hpa = _safe_float(token_map.get("PRES"))
        sea_surface_temp_c = _safe_float(token_map.get("WTMP"))

        # Physical plausibility validation
        if wave_height is not None and (wave_height < 0.0 or wave_height > 35.0):
            unparseable_count += 1
            quarantine_reasons.append(f"Unphysical wave height {wave_height} m for station {station_id}")
            continue

        if wind_speed_knots is not None and (wind_speed_knots < 0.0 or wind_speed_knots > 180.0):
            unparseable_count += 1
            quarantine_reasons.append(f"Unphysical wind speed {wind_speed_knots} kts for station {station_id}")
            continue

        if air_pressure_hpa is not None and (air_pressure_hpa < 850.0 or air_pressure_hpa > 1090.0):
            unparseable_count += 1
            quarantine_reasons.append(f"Unphysical pressure {air_pressure_hpa} hPa for station {station_id}")
            continue

        idempotency_key = f"NOAA:NDBC:{station_id}:{obs_dt.isoformat()}"

        record = {
            "station_id": station_id,
            "station_name": station_name,
            "latitude": latitude,
            "longitude": longitude,
            "sea_area": sea_area,
            "observation_time": obs_dt,
            "retrieval_time_utc": retrieval_time_utc,
            "wave_height_meters": wave_height,
            "wave_period_seconds": wave_period_sec,
            "wind_speed_knots": wind_speed_knots,
            "wind_direction_degrees": wind_dir,
            "sea_surface_temp_c": sea_surface_temp_c,
            "air_pressure_hpa": air_pressure_hpa,
            "source_name": "NOAA National Data Buoy Center (NDBC)",
            "license_type": "U.S. Federal Government Public Domain (17 U.S.C. § 105)",
            "quality_status": "verified",
            "idempotency_key": idempotency_key,
        }
        records.append(record)

    return records, unparseable_count, quarantine_reasons


def fetch_noaa_ndbc_station_data(
    station_id: str,
    client: Optional[ResilientHttpClient] = None,
    max_observations: int = 24,
) -> Tuple[List[Dict[str, Any]], int, datetime.datetime]:
    """Fetch and parse live observation records for a specific NOAA NDBC buoy station."""
    http_client = client or ResilientHttpClient()
    url = NDBC_REALTIME_URL_TEMPLATE.format(station_id=station_id)

    resp: AcquisitionResponse = http_client.get(url)
    records, unparseable, _ = parse_ndbc_realtime_text(
        resp.text,
        station_id=station_id,
        retrieval_time_utc=resp.retrieval_time_utc,
        max_observations=max_observations,
    )
    return records, unparseable, resp.retrieval_time_utc


def refresh_environmental_observations(
    db: Session,
    station_ids: Optional[List[str]] = None,
    max_per_station: int = 12,
    client: Optional[ResilientHttpClient] = None,
    offline: bool = False,
    fixture_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Acquire, validate, and idempotently persist environmental buoy observations.

    Args:
        db: Active SQLAlchemy Session.
        station_ids: Target buoy station IDs (defaults to 46041 and 42001).
        max_per_station: Max observations per station to ingest.
        client: Optional ResilientHttpClient instance.
        offline: If True, bypasses remote network calls and ingests from verified fixture.
        fixture_path: Optional custom fixture path for offline fallback.

    Returns:
        Structured audit dictionary reporting counts and integration status.
    """
    stations = station_ids or ["46041", "42001"]
    start_time = datetime.datetime.now(datetime.timezone.utc)
    run_type = "bundled" if offline else "remote"

    try:
        ingestion_run = IngestionRun(
            run_type=run_type,
            source_name="NOAA National Data Buoy Center (NDBC)",
            source_partition="realtime_buoy_stream" if not offline else "offline_fixture",
            started_at=start_time,
            status="running",
        )
        db.add(ingestion_run)
        db.flush()

        total_records_read = 0
        accepted_count = 0
        duplicate_count = 0
        unparseable_count = 0
        quarantined_count = 0
        retrieval_timestamp: Optional[datetime.datetime] = None
        station_results: Dict[str, Any] = {}
        errors: List[str] = []

        if offline:
            # Load from verified offline reference fixture
            fpath = fixture_path or DEFAULT_ENV_FIXTURE
            if not fpath.is_file():
                ingestion_run.status = "failed"
                ingestion_run.error_details = f"Offline fixture not found at {fpath}"
                db.flush()
                return {"status": "failed", "error": ingestion_run.error_details}

            with open(fpath, "r", encoding="utf-8") as f:
                fixture_data = json.load(f)

            raw_obs = fixture_data.get("observations", [])
            total_records_read = len(raw_obs)
            retrieval_timestamp = start_time

            for item in raw_obs:
                obs_dt = datetime.datetime.fromisoformat(item["observation_time"].replace("Z", "+00:00"))
                key = f"{item.get('source_name', 'ENV')}:{item['station_id']}:{obs_dt.isoformat()}"
                existing = db.query(EnvironmentalObservation).filter(
                    EnvironmentalObservation.idempotency_key == key
                ).first()

                if existing:
                    duplicate_count += 1
                else:
                    entity = EnvironmentalObservation(
                        station_id=item["station_id"],
                        station_name=item["station_name"],
                        latitude=float(item["latitude"]),
                        longitude=float(item["longitude"]),
                        sea_area=item["sea_area"],
                        observation_time=obs_dt,
                        retrieval_time_utc=None,
                        wave_height_meters=item.get("wave_height_meters"),
                        wave_period_seconds=item.get("wave_period_seconds"),
                        wind_speed_knots=item.get("wind_speed_knots"),
                        wind_direction_degrees=item.get("wind_direction_degrees"),
                        sea_surface_temp_c=item.get("sea_surface_temp_c"),
                        air_pressure_hpa=item.get("air_pressure_hpa"),
                        source_name=item.get("source_name", "NOAA National Data Buoy Center (NDBC)"),
                        license_type=item.get("license_type", "Public Domain"),
                        quality_status=item.get("quality_status", "verified"),
                        idempotency_key=key,
                    )
                    db.add(entity)
                    accepted_count += 1

        else:
            # Automated remote acquisition
            for station_id in stations:
                try:
                    records, unparse, ret_time = fetch_noaa_ndbc_station_data(
                        station_id,
                        client=client,
                        max_observations=max_per_station,
                    )
                    if retrieval_timestamp is None:
                        retrieval_timestamp = ret_time

                    station_records_read = len(records)
                    station_accepted = 0
                    station_duplicates = 0
                    station_quarantined = 0
                    total_records_read += station_records_read
                    unparseable_count += unparse

                    for rec in records:
                        if rec["latitude"] is None or rec["longitude"] is None:
                            quarantined_count += 1
                            station_quarantined += 1
                            q_record = QuarantinedRecord(
                                source_name=rec["source_name"],
                                source_partition="realtime_buoy_stream",
                                ingestion_run_id=ingestion_run.id,
                                raw_record_id=f"{rec['station_id']}:{rec['observation_time'].isoformat()}",
                                error_code="UNAVAILABLE_STATION_COORDINATES",
                                error_details=f"Station {rec['station_id']} has no verified coordinates; not fabricated to (0.0, 0.0)",
                                raw_payload=json.dumps(rec, default=str),
                            )
                            db.add(q_record)
                            continue

                        key = rec["idempotency_key"]
                        existing = db.query(EnvironmentalObservation).filter(
                            EnvironmentalObservation.idempotency_key == key
                        ).first()

                        if existing:
                            station_duplicates += 1
                            duplicate_count += 1
                        else:
                            entity = EnvironmentalObservation(
                                station_id=rec["station_id"],
                                station_name=rec["station_name"],
                                latitude=rec["latitude"],
                                longitude=rec["longitude"],
                                sea_area=rec["sea_area"],
                                observation_time=rec["observation_time"],
                                retrieval_time_utc=rec.get("retrieval_time_utc"),
                                wave_height_meters=rec["wave_height_meters"],
                                wave_period_seconds=rec["wave_period_seconds"],
                                wind_speed_knots=rec["wind_speed_knots"],
                                wind_direction_degrees=rec["wind_direction_degrees"],
                                sea_surface_temp_c=rec["sea_surface_temp_c"],
                                air_pressure_hpa=rec["air_pressure_hpa"],
                                source_name=rec["source_name"],
                                license_type=rec["license_type"],
                                quality_status=rec["quality_status"],
                                idempotency_key=key,
                            )
                            db.add(entity)
                            station_accepted += 1
                            accepted_count += 1

                    station_status = "success"
                    if station_quarantined > 0 and station_accepted == 0:
                        station_status = "quarantined"
                    elif station_quarantined > 0 or unparse > 0:
                        station_status = "partial"

                    station_result_entry: Dict[str, Any] = {
                        "records_read": station_records_read,
                        "accepted": station_accepted,
                        "duplicates": station_duplicates,
                        "quarantined": station_quarantined,
                        "unparseable": unparse,
                        "status": station_status,
                    }
                    if station_quarantined > 0:
                        station_result_entry["warning"] = "Station coordinates unavailable; records quarantined"
                    station_results[station_id] = station_result_entry

                except Exception as exc:
                    logger.error(f"Failed to acquire NOAA NDBC station {station_id}: {exc}")
                    errors.append(f"Station {station_id}: {exc}")
                    station_results[station_id] = {"status": "error", "error": str(exc)}

            if errors and accepted_count == 0 and duplicate_count == 0 and quarantined_count == 0:
                # Complete failure of all stations
                ingestion_run.status = "failed"
                ingestion_run.error_details = "; ".join(errors)
                ingestion_run.completed_at = datetime.datetime.now(datetime.timezone.utc)
                db.flush()
                return {
                    "status": "failed",
                    "run_id": ingestion_run.id,
                    "records_read": 0,
                    "accepted_count": 0,
                    "duplicate_count": 0,
                    "unparseable_count": unparseable_count,
                    "quarantined_count": 0,
                    "error": ingestion_run.error_details,
                    "station_results": station_results,
                }

        completed_time = datetime.datetime.now(datetime.timezone.utc)
        status = "completed" if (unparseable_count == 0 and not errors and quarantined_count == 0) else "partial"
        ingestion_run.records_read = total_records_read
        ingestion_run.accepted_count = accepted_count
        ingestion_run.duplicate_count = duplicate_count
        ingestion_run.unparseable_count = unparseable_count
        ingestion_run.quarantined_count = quarantined_count
        ingestion_run.status = status
        if errors:
            ingestion_run.error_details = "; ".join(errors)
        ingestion_run.completed_at = completed_time

        db.flush()

        return {
            "status": status,
            "run_id": ingestion_run.id,
            "mode": run_type,
            "records_read": total_records_read,
            "accepted_count": accepted_count,
            "duplicate_count": duplicate_count,
            "unparseable_count": unparseable_count,
            "quarantined_count": quarantined_count,
            "retrieval_timestamp": retrieval_timestamp.isoformat() if retrieval_timestamp else None,
            "source": "NOAA National Data Buoy Center (NDBC)",
            "station_results": station_results,
            **({"error_details": ingestion_run.error_details} if ingestion_run.error_details else {}),
        }

    except Exception:
        raise
