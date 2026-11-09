"""
FastAPI router for freight routes, rate observations, and DuckDB analytics.
Traces to REQ-FR-DII-005, REQ-NFR-ABM-002, and ADR-0002.
"""

import logging
from typing import List, Dict, Any, Optional, Union
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pelagos.database import get_db
from pelagos.models import FreightRoute, FreightObservation
from pelagos.storage.freight_analytics import get_freight_summary, get_freight_route_history
from pelagos.routing.corridors import (
    CorridorStatus,
    resolve_freight_corridor,
    resolve_endpoint_to_port,
)
from pelagos.routing.ports import PortRepository
from pelagos.routing.environmental import (
    EnvironmentalConditionsSummary,
    resolve_corridor_environmental_conditions,
)
from pelagos.routing.bunker import (
    BunkerFuelSummary,
    resolve_corridor_bunker_fuel_references,
)

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Route-Level Freight Intelligence (Pelagos D9.5)
# ---------------------------------------------------------------------------

class CorridorInfo(BaseModel):
    """Metadata describing a monitored maritime freight trade corridor."""

    route_code: str
    route_name: str
    origin_region: str
    destination_region: str
    vessel_class: Optional[str] = None
    cargo_type: Optional[str] = None
    unit: str = "USD/MT"
    currency: str = "USD"
    benchmark_distance_nm: Optional[float] = None
    description: Optional[str] = None


class LatestObservationInfo(BaseModel):
    """Latest historical rate observation for a monitored corridor."""

    observation_date: str
    rate_value: float
    currency: str = "USD"
    unit: str = "USD/MT"
    source_name: str
    source_dataset_id: Optional[str] = None
    is_derived: bool = False


class BaselineForecastInfo(BaseModel):
    """Point forecast result for the next horizon."""

    model_config = {"protected_namespaces": ()}

    model_type: str
    origin_date: str
    target_date: str
    horizon_months: int
    predicted_value: Optional[float]
    unit: str = "USD/MT"
    status: str
    flags: List[str] = Field(default_factory=list)
    training_data_cutoff: Optional[str] = None
    is_experimental: bool = False
    model_family: str = "deterministic_baseline"
    fitted_parameters: Optional[Dict[str, float]] = None
    notes: Optional[str] = None
    warning: Optional[str] = None


class VesselAvailabilityInfo(BaseModel):
    """Operational vessel availability and fleet supply status."""

    status: str = "unavailable"
    reason: str = "Live AIS fleet telemetry and commercial tonnage lists are not connected (pelagos live_ais_connected: false)."
    available_vessels_count: Optional[int] = None


class DomainCoverageStatus(BaseModel):
    """Coverage status and audit metadata for a specific domain facet."""

    status: str  # "active" | "partial" | "unavailable" | "unmonitored"
    details: Optional[str] = None
    source: Optional[str] = None
    record_count: Optional[int] = None
    frequency: Optional[str] = None
    as_of_date: Optional[str] = None


class DataCoverageSummary(BaseModel):
    """Consolidated multi-domain data coverage audit summary."""

    status: str  # "active" | "partial" | "unmonitored"
    corridor_code: Optional[str] = None
    freight_market: DomainCoverageStatus
    forecasts: DomainCoverageStatus
    environmental: DomainCoverageStatus
    bunker_fuel: DomainCoverageStatus
    vessel_fleet: DomainCoverageStatus


class RouteIntelligenceResponse(BaseModel):
    """Comprehensive route-level commercial freight intelligence."""

    status: str  # "monitored" | "unmonitored" | "unsupported_input"
    corridor: Optional[CorridorInfo] = None
    origin_port: Optional[Dict[str, Any]] = None
    destination_port: Optional[Dict[str, Any]] = None
    latest_observation: Optional[LatestObservationInfo] = None
    baseline_forecast: Optional[BaselineForecastInfo] = None
    forecasts: Optional[List[BaselineForecastInfo]] = None
    multi_horizon_forecasts: Optional[Dict[str, BaselineForecastInfo]] = None
    environmental_conditions: Optional[EnvironmentalConditionsSummary] = None
    bunker_fuel_references: Optional[BunkerFuelSummary] = None
    bunker_fuel: Optional[BunkerFuelSummary] = None
    data_coverage: Optional[DataCoverageSummary] = None
    vessel_availability: VesselAvailabilityInfo = Field(default_factory=VesselAvailabilityInfo)
    disclaimers: List[str] = Field(default_factory=list)
    message: Optional[str] = None


class RouteIntelligenceRequest(BaseModel):
    """Request payload for route intelligence calculation."""

    model_config = {"protected_namespaces": ()}

    origin: Optional[Union[str, List[float], Dict[str, Any]]] = None
    destination: Optional[Union[str, List[float], Dict[str, Any]]] = None
    route_code: Optional[str] = None
    model_type: Optional[str] = None
    horizon: Optional[int] = None
    origin_lon: Optional[float] = None
    origin_lat: Optional[float] = None
    dest_lon: Optional[float] = None
    dest_lat: Optional[float] = None
    as_of: Optional[str] = None


def _build_route_intelligence(
    db: Session,
    origin: Optional[Union[str, List[float], Dict[str, Any]]] = None,
    destination: Optional[Union[str, List[float], Dict[str, Any]]] = None,
    origin_lon: Optional[float] = None,
    origin_lat: Optional[float] = None,
    dest_lon: Optional[float] = None,
    dest_lat: Optional[float] = None,
    route_code: Optional[str] = None,
    model_type: Optional[str] = None,
    horizon: Optional[int] = None,
    as_of: Optional[str] = None,
) -> RouteIntelligenceResponse:
    """Internal resolver producing RouteIntelligenceResponse without duplicating logic."""
    from pelagos.forecasting.schemas import SUPPORTED_DIRECT_ROUTES
    from pelagos.forecasting.service import forecast_next_route_rate

    # If coordinates are provided as individual floats, wrap them into coordinate lists
    if origin is None and origin_lon is not None and origin_lat is not None:
        origin = [origin_lon, origin_lat]
    if destination is None and dest_lon is not None and dest_lat is not None:
        destination = [dest_lon, dest_lat]

    # Standard commercial integrity disclaimers
    commercial_disclaimers = [
        "Benchmark freight rate applies specifically to Agricultural Bulk Grain in Panamax/Supramax bulk carriers.",
        "Does NOT represent general containerized cargo, tanker rates, or spot vessel charter agreements.",
        "Pelagos freight intelligence is an analytical research benchmark and must not be used as a certified trade clearing price or navigation instruction.",
    ]

    vessel_info = VesselAvailabilityInfo(
        status="unavailable",
        reason="Live AIS fleet telemetry and commercial open tonnage lists are not connected (pelagos live_ais_connected: false). Static sample only covers coastal U.S. waters.",
        available_vessels_count=None,
    )

    # Path 1: Direct route code requested
    if route_code:
        clean_code = route_code.strip().upper()
        if clean_code not in SUPPORTED_DIRECT_ROUTES:
            return RouteIntelligenceResponse(
                status="unmonitored",
                corridor=None,
                latest_observation=None,
                baseline_forecast=None,
                forecasts=None,
                multi_horizon_forecasts=None,
                environmental_conditions=None,
                bunker_fuel_references=None,
                data_coverage=DataCoverageSummary(
                    status="unmonitored",
                    corridor_code=None,
                    freight_market=DomainCoverageStatus(status="unmonitored", details=f"Route code '{route_code}' is not monitored."),
                    forecasts=DomainCoverageStatus(status="unmonitored", details="Forecasting unavailable for unmonitored routes."),
                    environmental=DomainCoverageStatus(status="unmonitored", details="Environmental buoy fusion unavailable for unmonitored routes."),
                    bunker_fuel=DomainCoverageStatus(status="unmonitored", details="Bunker references unavailable for unmonitored routes."),
                    vessel_fleet=DomainCoverageStatus(status="unmonitored", details="AIS fleet supply tracking unavailable for unmonitored routes."),
                ),
                vessel_availability=vessel_info,
                disclaimers=["Freight market intelligence is unavailable for unmonitored trade routes."],
                message=f"Route code '{route_code}' is not a monitored direct freight corridor. Supported direct corridors: {list(SUPPORTED_DIRECT_ROUTES)}.",
            )

        route_record = db.query(FreightRoute).filter(FreightRoute.route_code == clean_code).first()
        corridor_code = clean_code
        origin_port_dict = None
        dest_port_dict = None
        corridor_desc = route_record.route_name if route_record else clean_code
    else:
        # Path 2: Endpoint resolution
        if origin is None or destination is None:
            return RouteIntelligenceResponse(
                status="unsupported_input",
                corridor=None,
                origin_port=None,
                destination_port=None,
                latest_observation=None,
                baseline_forecast=None,
                forecasts=None,
                multi_horizon_forecasts=None,
                environmental_conditions=None,
                bunker_fuel_references=None,
                data_coverage=None,
                vessel_availability=vessel_info,
                disclaimers=["Both origin and destination endpoints (or a valid route_code) are required."],
                message="Missing required origin and destination parameters.",
            )

        resolution = resolve_freight_corridor(origin=origin, destination=destination, db=db)

        origin_port_dict = (
            {
                "port_id": resolution.origin_port_id,
                "name": resolution.origin_port_name,
                "region": resolution.origin_region,
            }
            if resolution.origin_port_id
            else None
        )
        dest_port_dict = (
            {
                "port_id": resolution.destination_port_id,
                "name": resolution.destination_port_name,
                "region": resolution.destination_region,
            }
            if resolution.destination_port_id
            else None
        )

        if resolution.status == CorridorStatus.UNMONITORED:
            return RouteIntelligenceResponse(
                status="unmonitored",
                corridor=None,
                origin_port=origin_port_dict,
                destination_port=dest_port_dict,
                latest_observation=None,
                baseline_forecast=None,
                forecasts=None,
                multi_horizon_forecasts=None,
                environmental_conditions=None,
                bunker_fuel_references=None,
                data_coverage=DataCoverageSummary(
                    status="unmonitored",
                    corridor_code=None,
                    freight_market=DomainCoverageStatus(status="unmonitored", details="Trade corridor is not monitored for agricultural bulk grain rates."),
                    forecasts=DomainCoverageStatus(status="unmonitored", details="Forecasting unavailable for unmonitored routes."),
                    environmental=DomainCoverageStatus(status="unmonitored", details="Environmental buoy fusion is restricted to monitored freight corridors."),
                    bunker_fuel=DomainCoverageStatus(status="unmonitored", details="Bunker fuel references are restricted to monitored corridors."),
                    vessel_fleet=DomainCoverageStatus(status="unmonitored", details="AIS fleet supply tracking unavailable for unmonitored routes."),
                ),
                vessel_availability=vessel_info,
                disclaimers=[
                    "Freight market intelligence is unavailable for unmonitored maritime trade routes.",
                    "Pelagos freight monitoring is currently limited to U.S.-Japan dry bulk grain export benchmarks.",
                ],
                message=resolution.reason,
            )

        corridor_code = resolution.route_code
        assert corridor_code is not None
        route_record = db.query(FreightRoute).filter(FreightRoute.route_code == corridor_code).first()
        corridor_desc = resolution.reason

    # Query latest observation from database
    latest_obs_record = (
        db.query(FreightObservation)
        .filter(
            FreightObservation.route_code == corridor_code,
            FreightObservation.is_derived.is_(False),
        )
        .order_by(FreightObservation.observation_date.desc())
        .first()
    )

    obs_info = None
    if latest_obs_record:
        obs_info = LatestObservationInfo(
            observation_date=str(latest_obs_record.observation_date),
            rate_value=round(float(latest_obs_record.rate_value), 2),
            currency=latest_obs_record.currency or "USD",
            unit=latest_obs_record.unit_of_measure or "USD/MT",
            source_name=latest_obs_record.source_name,
            source_dataset_id=latest_obs_record.source_dataset_id,
            is_derived=bool(latest_obs_record.is_derived),
        )

    # Validate horizon if explicitly specified
    supported_horizons = (1, 3, 6)
    if horizon is not None and horizon not in supported_horizons:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported forecast horizon '{horizon}'. Supported horizons: {list(supported_horizons)} months.",
        )

    # Generate deterministic baseline forecasts across horizons
    forecasts_list: List[BaselineForecastInfo] = []
    multi_horizon_map: Dict[str, BaselineForecastInfo] = {}
    primary_fc_info: Optional[BaselineForecastInfo] = None

    target_horizons = [horizon] if horizon is not None else [1, 3, 6]

    for h in target_horizons:
        try:
            fc_dict = forecast_next_route_rate(
                db=db,
                route_code=corridor_code,
                model_type=model_type or "persistence",
                horizon_months=h,
            )
            item = BaselineForecastInfo(
                model_type=fc_dict["model_type"],
                origin_date=str(fc_dict["origin_date"]),
                target_date=str(fc_dict["target_date"]),
                horizon_months=int(fc_dict["horizon_months"]),
                predicted_value=round(float(fc_dict["predicted_value"]), 2) if fc_dict.get("predicted_value") is not None else None,
                unit=fc_dict.get("unit_of_measure", "USD/MT"),
                status=fc_dict.get("status", "forecast_generated"),
                flags=fc_dict.get("flags", []),
                training_data_cutoff=str(fc_dict.get("training_data_cutoff")) if fc_dict.get("training_data_cutoff") else None,
                is_experimental=bool(fc_dict.get("is_experimental", False)),
                model_family=fc_dict.get("model_family", "deterministic_baseline"),
                fitted_parameters=fc_dict.get("fitted_parameters"),
                notes=fc_dict.get("notes"),
                warning=fc_dict.get("notes") if fc_dict.get("is_experimental") else None,
            )
            forecasts_list.append(item)
            multi_horizon_map[f"T+{h}"] = item
            if h == (horizon if horizon is not None else 1):
                primary_fc_info = item
        except Exception as exc:
            logger.warning("Could not generate T+%s forecast for corridor '%s': %s", h, corridor_code, exc)

    if primary_fc_info is None and forecasts_list:
        primary_fc_info = forecasts_list[0]

    # Resolve origin coordinates for local station distance calculation
    origin_coords = None
    if origin is not None and isinstance(origin, (list, tuple)) and len(origin) == 2:
        try:
            origin_coords = (float(origin[0]), float(origin[1]))
        except (ValueError, TypeError):
            pass
    elif origin_port_dict and origin_port_dict.get("port_id"):
        try:
            port_repo = PortRepository()
            port_rec = port_repo.get_port(origin_port_dict["port_id"])
            if port_rec and port_rec.coordinates:
                origin_coords = (float(port_rec.coordinates[0]), float(port_rec.coordinates[1]))
        except Exception as exc:
            logger.debug("Failed to lookup origin port coordinates: %s", exc)

    # Resolve genuine environmental buoy observations
    try:
        env_summary = resolve_corridor_environmental_conditions(
            db=db,
            corridor_code=corridor_code,
            origin_coords=origin_coords,
            as_of=as_of,
        )
    except Exception as exc:
        logger.warning("Could not resolve environmental conditions for corridor '%s': %s", corridor_code, exc)
        env_summary = None

    # Resolve bunker fuel reference boundary
    try:
        bunker_summary = resolve_corridor_bunker_fuel_references(
            corridor_code=corridor_code,
            db=db,
            as_of=as_of,
        )
    except Exception as exc:
        logger.warning("Could not resolve bunker fuel references for corridor '%s': %s", corridor_code, exc)
        bunker_summary = None

    # Construct multi-domain data coverage audit summary
    bunker_cov_status = "unavailable"
    bunker_cov_details = "Commercial spot bunker price benchmark feeds connected (Bunker Fuel Prices); corridor hubs currently unquoted/withheld; synthesis strictly disallowed"
    bunker_as_of = None
    bunker_record_count = 0

    if bunker_summary:
        if bunker_summary.status in ("available", "partial"):
            bunker_cov_status = "active" if bunker_summary.status == "available" else "partial"
            bunker_cov_details = f"Bunker Fuel Prices: {bunker_summary.hubs_with_prices_count}/{len(bunker_summary.bunkering_hubs_in_scope)} corridor hubs quoted ({bunker_summary.status})"
            if bunker_summary.latest_quotes:
                bunker_record_count = len(bunker_summary.latest_quotes)
                bunker_as_of = bunker_summary.latest_quotes[0].observation_time
                if any(q.freshness == "stale" for q in bunker_summary.latest_quotes):
                    bunker_cov_status = "stale"
                    bunker_cov_details += " [stale observations]"
        elif bunker_summary.regional_benchmarks:
            bunker_cov_details = (
                f"Bunker Fuel Prices connected ({len(bunker_summary.regional_benchmarks)} regional benchmark(s) available; "
                f"corridor hubs unquoted/withheld; synthesis strictly disallowed)"
            )

    coverage_summary = DataCoverageSummary(
        status="partial",
        corridor_code=corridor_code,
        freight_market=DomainCoverageStatus(
            status="active" if latest_obs_record else "unavailable",
            details="Canonical monthly ocean freight rates for agricultural dry bulk grain (Panamax/Supramax)",
            source=latest_obs_record.source_name if latest_obs_record else "USDA Agricultural Marketing Service / O'Neil Commodity Consulting",
            frequency="monthly",
            as_of_date=str(latest_obs_record.observation_date) if latest_obs_record else None,
        ),
        forecasts=DomainCoverageStatus(
            status="active" if forecasts_list else "unavailable",
            details=f"Deterministic baseline forecasts across {len(forecasts_list)} horizon(s) ({', '.join('T+' + str(f.horizon_months) for f in forecasts_list)})",
            source="Pelagos Baseline Forecasting Engine",
            frequency="monthly",
            as_of_date=primary_fc_info.origin_date if primary_fc_info else None,
        ),
        environmental=DomainCoverageStatus(
            status=(
                "active"
                if (env_summary and env_summary.stations_count > 0 and env_summary.stations[0].freshness in ("fresh", "recent"))
                else (env_summary.stations[0].freshness if (env_summary and env_summary.stations) else "unavailable")
            ),
            details=(
                f"NOAA NDBC moored ocean buoys ({env_summary.coverage_status}, freshness: {env_summary.stations[0].freshness})"
                if (env_summary and env_summary.stations)
                else "No active moored ocean buoys"
            ),
            source="NOAA National Data Buoy Center (NDBC)",
            record_count=env_summary.stations_count if env_summary else 0,
            as_of_date=env_summary.stations[0].observation_time if (env_summary and env_summary.stations) else None,
        ),
        bunker_fuel=DomainCoverageStatus(
            status=bunker_cov_status,
            details=bunker_cov_details,
            source="Bunker Fuel Prices (bunkerfuelprices.com)",
            record_count=bunker_record_count,
            as_of_date=bunker_as_of,
        ),
        vessel_fleet=DomainCoverageStatus(
            status="unavailable",
            details="Live AIS fleet telemetry and commercial tonnage lists are not connected (pelagos live_ais_connected: false)",
            source=None,
        ),
    )

    corridor_info = CorridorInfo(
        route_code=corridor_code,
        route_name=route_record.route_name if route_record else corridor_code,
        origin_region=route_record.origin_region if route_record else "Unknown",
        destination_region=route_record.destination_region if route_record else "Japan",
        vessel_class=route_record.vessel_class if route_record else "Panamax / Supramax",
        cargo_type=route_record.cargo_type if route_record else "Agricultural Bulk Grain",
        unit=route_record.unit_of_measure if route_record else "USD/MT",
        currency=route_record.currency if route_record else "USD",
        benchmark_distance_nm=route_record.benchmark_distance_nm if route_record else None,
        description=corridor_desc,
    )

    return RouteIntelligenceResponse(
        status="monitored",
        corridor=corridor_info,
        origin_port=origin_port_dict,
        destination_port=dest_port_dict,
        latest_observation=obs_info,
        baseline_forecast=primary_fc_info,
        forecasts=forecasts_list if forecasts_list else None,
        multi_horizon_forecasts=multi_horizon_map if multi_horizon_map else None,
        environmental_conditions=env_summary,
        bunker_fuel_references=bunker_summary,
        bunker_fuel=bunker_summary,
        data_coverage=coverage_summary,
        vessel_availability=vessel_info,
        disclaimers=commercial_disclaimers,
        message=f"Monitored freight intelligence retrieved for corridor {corridor_code}.",
    )


@router.get("/route-intelligence", response_model=RouteIntelligenceResponse)
def get_route_intelligence_endpoint(
    origin: Optional[str] = Query(None, description="Origin port code or 'lon,lat'"),
    destination: Optional[str] = Query(None, description="Destination port code or 'lon,lat'"),
    origin_lon: Optional[float] = Query(None, description="Explicit origin longitude"),
    origin_lat: Optional[float] = Query(None, description="Explicit origin latitude"),
    dest_lon: Optional[float] = Query(None, description="Explicit destination longitude"),
    dest_lat: Optional[float] = Query(None, description="Explicit destination latitude"),
    route_code: Optional[str] = Query(None, description="Direct corridor route code (e.g. PNW-JPN-BULK)"),
    model_type: Optional[str] = Query(None, description="Baseline forecast model (default: persistence)"),
    horizon: Optional[int] = Query(None, description="Forecast horizon in months: 1, 3, or 6 (default: returns all supported horizons)"),
    as_of: Optional[str] = Query(None, description="Optional evaluation timestamp (ISO 8601) for deterministic freshness evaluation"),
    db: Session = Depends(get_db),
) -> RouteIntelligenceResponse:
    """Retrieve commercial freight intelligence, environmental buoy observations, and multi-horizon forecasts.

    Resolves origin and destination endpoints to monitored freight corridors (PNW-JPN-BULK, USG-JPN-BULK)
    using the canonical port directory and aliases.
    Enriches freight market observations with genuine NOAA NDBC moored buoy observations, explicit bunker fuel
    integration boundaries, and multi-horizon forecasts (T+1, T+3, T+6).
    """
    return _build_route_intelligence(
        db=db,
        origin=origin,
        destination=destination,
        origin_lon=origin_lon,
        origin_lat=origin_lat,
        dest_lon=dest_lon,
        dest_lat=dest_lat,
        route_code=route_code,
        model_type=model_type,
        horizon=horizon,
        as_of=as_of,
    )


@router.post("/route-intelligence", response_model=RouteIntelligenceResponse)
def post_route_intelligence_endpoint(
    request: RouteIntelligenceRequest,
    db: Session = Depends(get_db),
) -> RouteIntelligenceResponse:
    """Retrieve commercial freight intelligence via JSON POST body."""
    return _build_route_intelligence(
        db=db,
        origin=request.origin,
        destination=request.destination,
        origin_lon=request.origin_lon,
        origin_lat=request.origin_lat,
        dest_lon=request.dest_lon,
        dest_lat=request.dest_lat,
        route_code=request.route_code,
        model_type=request.model_type,
        horizon=request.horizon,
        as_of=request.as_of,
    )
