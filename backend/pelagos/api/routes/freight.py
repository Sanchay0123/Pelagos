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


@router.get("/index")
def get_freight_index_endpoint(
    start_date: Optional[date] = Query(None, description="Start observation date (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="End observation date (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
):
    """
    Returns the Pelagos Experimental Grain Index (U.S.–Japan) (PEGI-USJPN) series,
    incorporating the primary arithmetic mean rate, base-100 normalized index points,
    USG-minus-PNW spread indicator, and exploratory distance-weighted scenario.
    """
    from pelagos.forecasting.service import get_freight_index
    return get_freight_index(db=db, start_date=start_date, end_date=end_date)


@router.get("/forecast")
@router.get("/forecast/baseline")
def get_freight_forecast_endpoint(
    route_code: str = Query(..., description="Corridor route code (USG-JPN-BULK or PNW-JPN-BULK)"),
    model_type: Optional[str] = Query(None, description="Baseline model type (default: persistence). Options: persistence, rolling_ma_3, rolling_ma_6, linear_trend_6"),
    origin_date: Optional[date] = Query(None, description="Forecast origin date (default: latest observed month)"),
    horizon_months: int = Query(1, ge=1, le=6, description="Discrete forecast horizon in months (default: 1)"),
    db: Session = Depends(get_db),
):
    """
    Returns an experimental point forecast for a directly observed maritime corridor.
    Employs deterministic, explainable baseline models (Persistence, MA-3, MA-6, Trend-6).
    If model_type is omitted, defaults to 'persistence' (primary naive benchmark).
    If origin_date is specified, history is strictly fenced to observations on or before origin.
    """
    from pelagos.forecasting.schemas import (
        SUPPORTED_DIRECT_ROUTES,
        DISALLOWED_DERIVED_ROUTES,
        SUPPORTED_BASELINE_MODELS,
    )
    from pelagos.forecasting.service import forecast_next_route_rate

    # Validate route conditioning
    if route_code in DISALLOWED_DERIVED_ROUTES:
        raise HTTPException(
            status_code=400,
            detail=f"Route '{route_code}' is a derived spread indicator and cannot be forecast directly as a corridor rate."
        )

    if route_code not in SUPPORTED_DIRECT_ROUTES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported route code '{route_code}'. Supported direct routes: {list(SUPPORTED_DIRECT_ROUTES)}"
        )

    # Validate model type if provided
    if model_type is not None:
        normalized_model = model_type.lower().strip()
        if normalized_model not in SUPPORTED_BASELINE_MODELS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported baseline model '{model_type}'. Supported models: {list(SUPPORTED_BASELINE_MODELS)}"
            )

    try:
        return forecast_next_route_rate(
            db=db,
            route_code=route_code,
            model_type=model_type,
            origin_date=origin_date,
            horizon_months=horizon_months,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/forecast/evaluate")
def get_freight_forecast_evaluation_endpoint(
    route_code: str = Query(..., description="Corridor route code (USG-JPN-BULK or PNW-JPN-BULK)"),
    burn_in_months: int = Query(12, ge=6, description="Burn-in warm-up training period in months (default: 12)"),
    include_steps: bool = Query(False, description="Whether to include step-by-step out-of-sample comparisons"),
    db: Session = Depends(get_db),
):
    """
    Returns chronological out-of-sample walk-forward evaluation metrics (MAE, RMSE, MAPE)
    across all baseline models for a directly observed freight corridor.
    Includes persistence-relative comparative ratios and mandatory preliminary disclaimers.
    """
    from pelagos.forecasting.schemas import (
        SUPPORTED_DIRECT_ROUTES,
        DISALLOWED_DERIVED_ROUTES,
    )
    from pelagos.forecasting.service import evaluate_route_baseline_models

    if route_code in DISALLOWED_DERIVED_ROUTES:
        raise HTTPException(
            status_code=400,
            detail=f"Route '{route_code}' is a derived spread indicator and cannot be evaluated directly as a corridor rate."
        )

    if route_code not in SUPPORTED_DIRECT_ROUTES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported route code '{route_code}'. Supported direct routes: {list(SUPPORTED_DIRECT_ROUTES)}"
        )

    try:
        return evaluate_route_baseline_models(
            db=db,
            route_code=route_code,
            burn_in_months=burn_in_months,
            include_steps=include_steps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
