"""Coverage computation and honest geographic/temporal boundary reporting."""

from datetime import timezone
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import func

from pelagos.models import VesselPosition
from pelagos.config import settings


def compute_coverage_summary(db: Session) -> Dict[str, Any]:
    """
    Computes reliable spatial, temporal, and partition coverage metadata
    from actual ingested records in the database.
    Adheres to REQ-FR-DII-003, REQ-NFR-ARG-001, and REQ-NFR-DIP-003.
    """
    total_positions = db.query(func.count(VesselPosition.id)).scalar() or 0
    distinct_vessels = db.query(func.count(func.distinct(VesselPosition.mmsi))).scalar() or 0

    base_disclaimers = {
        "coverage_assessment": "Discrete historical observations in US Coastal and Territorial Waters.",
        "terrestrial_limitation": "Terrestrial receiver observations only; open-ocean satellite AIS is absent.",
        "safety_disclaimer": settings.safety_disclaimer,
        "source_attribution": settings.default_source_attribution,
        "statutory_license": settings.default_license,
    }

    if total_positions == 0:
        base_disclaimers["coverage_assessment"] = "No observations present in database. Telemetry ingestion required."
        return {
            "status": "empty",
            "total_observations": 0,
            "distinct_vessels": 0,
            "spatial_extent": None,
            "temporal_extent": None,
            "partitions_present": [],
            "disclaimers": base_disclaimers,
        }

    stats = db.query(
        func.min(VesselPosition.latitude).label("min_lat"),
        func.max(VesselPosition.latitude).label("max_lat"),
        func.min(VesselPosition.longitude).label("min_lon"),
        func.max(VesselPosition.longitude).label("max_lon"),
        func.min(VesselPosition.observation_time).label("min_obs"),
        func.max(VesselPosition.observation_time).label("max_obs"),
        func.max(VesselPosition.ingestion_time).label("max_ingest"),
    ).first()

    raw_partitions = db.query(func.distinct(VesselPosition.source_partition)).all()
    partitions_present: List[str] = [
        str(p[0]) for p in raw_partitions if p[0] is not None
    ]

    min_obs = stats.min_obs if stats else None
    max_obs = stats.max_obs if stats else None
    max_ingest = stats.max_ingest if stats else None

    span_hours = None
    if min_obs and max_obs:
        span_hours = round(max((max_obs - min_obs).total_seconds(), 0.0) / 3600.0, 4)

    spatial_extent = {
        "min_latitude": round(float(stats.min_lat), 5) if stats and stats.min_lat is not None else None,
        "max_latitude": round(float(stats.max_lat), 5) if stats and stats.max_lat is not None else None,
        "min_longitude": round(float(stats.min_lon), 5) if stats and stats.min_lon is not None else None,
        "max_longitude": round(float(stats.max_lon), 5) if stats and stats.max_lon is not None else None,
        "spatial_notice": (
            "Spatial bounding box represents observed ping coordinates only, "
            "not continuous geographic coverage or unbroken corridor monitoring."
        ),
    }

    def to_iso_utc(dt):
        if dt is None:
            return None
        if isinstance(dt, str):
            return dt if (dt.endswith("Z") or "+" in dt) else f"{dt}+00:00"
        if hasattr(dt, "tzinfo") and dt.tzinfo:
            return dt.astimezone(timezone.utc).isoformat()
        return dt.replace(tzinfo=timezone.utc).isoformat()

    temporal_extent = {
        "earliest_observation_utc": to_iso_utc(min_obs),
        "latest_observation_utc": to_iso_utc(max_obs),
        "observation_span_hours": span_hours,
        "temporal_mode": "historical",
        "last_ingested_at_utc": to_iso_utc(max_ingest),
        "temporal_notice": (
            "Observation time denotes historical AIS radio broadcast time. "
            "Ingestion time denotes when data was admitted to Pelagos. "
            "Telemetry is purely historical and must not be used for real-time navigation."
        ),
    }

    return {
        "status": "available",
        "total_observations": total_positions,
        "distinct_vessels": distinct_vessels,
        "spatial_extent": spatial_extent,
        "temporal_extent": temporal_extent,
        "partitions_present": sorted(partitions_present),
        "disclaimers": base_disclaimers,
    }
