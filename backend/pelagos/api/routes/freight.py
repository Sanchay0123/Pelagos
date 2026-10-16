"""
FastAPI router for freight routes, rate observations, and DuckDB analytics.
Traces to REQ-FR-DII-005, REQ-NFR-ABM-002, and ADR-0002.
"""

from typing import List, Dict, Any, Optional
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from pelagos.database import get_db
from pelagos.models import FreightRoute, FreightObservation
from pelagos.storage.freight_analytics import get_freight_summary, get_freight_route_history

router = APIRouter(prefix="/freight", tags=["Freight Market Data"])


@router.get("/routes")
def list_freight_routes(db: Session = Depends(get_db)):
    """Returns all registered standard maritime freight corridors and metadata."""
    routes = db.query(FreightRoute).order_by(FreightRoute.route_code.asc()).all()
    return [
        {
            "route_code": r.route_code,
            "route_name": r.route_name,
            "origin_region": r.origin_region,
            "destination_region": r.destination_region,
            "vessel_class": r.vessel_class,
            "cargo_type": r.cargo_type,
            "unit_of_measure": r.unit_of_measure,
            "currency": r.currency,
            "benchmark_distance_nm": r.benchmark_distance_nm,
            "is_active": r.is_active,
        }
        for r in routes
    ]


@router.get("/observations")
def list_freight_observations(
    route_code: Optional[str] = Query(None, description="Filter by route code (e.g. USG-JPN-BULK)"),
    start_date: Optional[date] = Query(None, description="Start observation date (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="End observation date (YYYY-MM-DD)"),
    limit: int = Query(100, ge=1, le=5000, description="Max observations to return"),
    db: Session = Depends(get_db),
):
    """Returns filtered point-in-time freight rate observations."""
    query = db.query(FreightObservation)
    if route_code:
        query = query.filter(FreightObservation.route_code == route_code)
    if start_date:
        query = query.filter(FreightObservation.observation_date >= start_date)
    if end_date:
        query = query.filter(FreightObservation.observation_date <= end_date)

    query = query.order_by(FreightObservation.observation_date.desc()).limit(limit)
    rows = query.all()

    return [
        {
            "id": r.id,
            "route_code": r.route_code,
            "observation_date": str(r.observation_date),
            "rate_value": r.rate_value,
            "currency": r.currency,
            "unit": r.unit_of_measure,
            "is_derived": r.is_derived,
            "source_name": r.source_name,
            "source_dataset_id": r.source_dataset_id,
            "idempotency_key": r.idempotency_key,
        }
        for r in rows
    ]


@router.get("/summary")
def get_freight_summary_endpoint(db: Session = Depends(get_db)):
    """
    Returns high-performance analytical aggregates across all published freight
    partitions computed by DuckDB (or SQL relational fallback).
    """
    return get_freight_summary(db)


@router.get("/routes/{route_code}/history")
def get_route_history_endpoint(
    route_code: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    """Returns chronological observation series for a specific route."""
    route_exists = db.query(FreightRoute.id).filter(FreightRoute.route_code == route_code).first()
    if not route_exists:
        # Check if observations exist even if route not formally in registry
        obs_exists = db.query(FreightObservation.id).filter(FreightObservation.route_code == route_code).first()
        if not obs_exists:
            raise HTTPException(status_code=404, detail=f"Freight route '{route_code}' not found")

    history = get_freight_route_history(db, route_code=route_code, start_date=start_date, end_date=end_date, limit=limit)
    return {
        "route_code": route_code,
        "observations_count": len(history),
        "history": history,
    }
