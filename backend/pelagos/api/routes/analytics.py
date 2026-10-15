"""FastAPI router for analytical DuckDB queries and Parquet partition catalog."""

from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from pelagos.database import get_db
from pelagos.storage.analytics import get_analytics_summary, get_vessel_track_from_parquet
from pelagos.storage.catalog import get_published_partition_catalog

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/summary")
def get_analytics_summary_endpoint(db: Session = Depends(get_db)):
    """
    Returns high-performance analytical aggregates across all published Parquet partitions
    computed by DuckDB (REQ-NFR-ABM-002, ADR-0002).
    """
    return get_analytics_summary(db)


@router.get("/partitions")
def get_published_partitions_endpoint(db: Session = Depends(get_db)):
    """
    Returns the catalog of published Parquet partitions, structural statistics,
    and spatial bounding boxes extracted from Parquet footers.
    """
    return get_published_partition_catalog(db)


@router.get("/vessels/{mmsi}/track")
def get_vessel_track_endpoint(mmsi: str, db: Session = Depends(get_db)):
    """
    Extracts time-series trajectory points for an MMSI from columnar Parquet storage.
    """
    track = get_vessel_track_from_parquet(db, mmsi=mmsi)
    if not track:
        # Check if exists in operational database to distinguish 404 from empty parquet
        from pelagos.models import VesselPosition
        exists_in_sql = db.query(VesselPosition.id).filter(VesselPosition.mmsi == str(mmsi)).first()
        if not exists_in_sql:
            raise HTTPException(status_code=404, detail=f"Vessel with MMSI '{mmsi}' not found")
        return {
            "mmsi": str(mmsi),
            "status": "pending_export",
            "message": "Observations exist in operational SQL store but have not been exported to Parquet yet.",
            "points_count": 0,
            "track": []
        }

    return {
        "mmsi": str(mmsi),
        "status": "published",
        "points_count": len(track),
        "track": track
    }
