"""Vessel snapshot and detail endpoints integrated with God's Eye View 3D frontend."""

from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from pelagos.database import get_db
from pelagos.models import VesselPosition
from pelagos.config import settings
from pelagos.ingestion.schemas import (
    VesselSnapshotResponse, 
    VesselSnapshotRow
)

router = APIRouter(tags=["Vessels"])


@router.get("/vessels/snapshot", response_model=VesselSnapshotResponse)
def get_vessels_snapshot(
    limit: int = Query(500, ge=1, le=5000, description="Maximum vessel entities to return"),
    vessel_type: Optional[str] = Query(None, description="Filter by vessel category (Cargo, Tanker, Tug, etc.)"),
    min_lat: Optional[float] = Query(None, description="South bounding box latitude"),
    max_lat: Optional[float] = Query(None, description="North bounding box latitude"),
    min_lon: Optional[float] = Query(None, description="West bounding box longitude"),
    max_lon: Optional[float] = Query(None, description="East bounding box longitude"),
    db: Session = Depends(get_db)
):
    """
    Returns verified vessel positions formatted for God's Eye View vesselSnapshot parser.
    Provides clear empty-state and full provenance metadata (REQ-FR-DII-003, REQ-NFR-SBL-001).
    Explicitly distinguishes historical observations from live AIS telemetry.
    """
    query = db.query(VesselPosition)

    if isinstance(vessel_type, str) and vessel_type.strip():
        query = query.filter(VesselPosition.vessel_type.ilike(f"%{vessel_type.strip()}%"))

    if isinstance(min_lat, (int, float)):
        query = query.filter(VesselPosition.latitude >= min_lat)
    if isinstance(max_lat, (int, float)):
        query = query.filter(VesselPosition.latitude <= max_lat)
    if isinstance(min_lon, (int, float)):
        query = query.filter(VesselPosition.longitude >= min_lon)
    if isinstance(max_lon, (int, float)):
        query = query.filter(VesselPosition.longitude <= max_lon)

    total_count = query.count()
    records = query.order_by(VesselPosition.observation_time.desc()).limit(limit).all()

    rows: List[VesselSnapshotRow] = []
    newest_time = None

    for r in records:
        obs_utc = r.observation_time.isoformat()
        if newest_time is None or obs_utc > newest_time:
            newest_time = obs_utc

    # Determine historical vs live status based on observation timestamps
    is_live = False
    data_mode = "historical"
    freshness = "historical"
    status = "historical"
    live_ais_connected = False
    backend_connected = True
    obs_date = None

    if total_count == 0:
        status = "historical"
        freshness = "unknown"
    elif newest_time:
        obs_date = newest_time[:10]
        try:
            newest_dt = datetime.fromisoformat(newest_time.replace("Z", "+00:00"))
            now_utc = datetime.now(timezone.utc)
            # If observation is within recent live window (2 hours) and from a verified live provider
            if (now_utc - newest_dt).total_seconds() < 7200 and "live" in settings.default_source_name.lower():
                is_live = True
                data_mode = "live"
                freshness = "current"
                live_ais_connected = True
                status = "live"
            else:
                is_live = False
                data_mode = "historical"
                freshness = "historical"
                live_ais_connected = False
                status = "historical"
        except Exception:
            is_live = False
            data_mode = "historical"
            freshness = "historical"
            status = "historical"

    is_historical = not is_live

    for r in records:
        obs_utc = r.observation_time.isoformat()
        rows.append(
            VesselSnapshotRow(
                mmsi=r.mmsi,
                imo=r.imo or "",
                name=r.vessel_name or r.mmsi,
                type_specific=r.vessel_type,
                lat=r.latitude,
                lon=r.longitude,
                speed=r.speed_knots,
                course=r.course_degrees,
                heading=r.heading_degrees,
                destination=r.destination or "",
                last_position_UTC=obs_utc,
                last_position_epoch=int(r.observation_time.timestamp()),
                historical=is_historical,
            )
        )

    return VesselSnapshotResponse(
        rows=rows,
        source=settings.default_source_name,
        license=settings.default_license,
        coverage=settings.default_coverage,
        status=status,
        data_mode=data_mode,
        is_live=is_live,
        live_ais_connected=live_ais_connected,
        backend_connected=backend_connected,
        freshness=freshness,
        refreshing=False,
        newestPositionAt=newest_time,
        observation_date=obs_date,
        rawRowCount=len(rows),
        total_records=total_count,
        disclaimer=settings.safety_disclaimer,
    )


@router.get("/vessels/{mmsi}")
def get_vessel_detail(mmsi: str, db: Session = Depends(get_db)):
    """Fetch individual vessel particulars and latest telemetry."""
    vessel = db.query(VesselPosition).filter(VesselPosition.mmsi == mmsi).first()
    if not vessel:
        raise HTTPException(status_code=404, detail=f"Vessel with MMSI '{mmsi}' not found")

    return {
        "mmsi": vessel.mmsi,
        "imo": vessel.imo,
        "vessel_name": vessel.vessel_name,
        "vessel_type": vessel.vessel_type,
        "latitude": vessel.latitude,
        "longitude": vessel.longitude,
        "speed_knots": vessel.speed_knots,
        "course_degrees": vessel.course_degrees,
        "heading_degrees": vessel.heading_degrees,
        "draft_meters": vessel.draft_meters,
        "length_meters": vessel.length_meters,
        "width_meters": vessel.width_meters,
        "destination": vessel.destination,
        "observation_time": vessel.observation_time.isoformat(),
        "source_name": vessel.source_name,
        "license": vessel.license_type,
        "data_mode": "historical",
        "is_live": False,
        "freshness": "historical",
        "safety_disclaimer": settings.safety_disclaimer,
    }
