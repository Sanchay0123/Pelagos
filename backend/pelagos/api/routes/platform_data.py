"""FastAPI router for Stage E1 Platform Data Overview & Detailed Data Explorer."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from pelagos.database import get_db
from pelagos.storage.platform_data import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    get_platform_data_overview,
    query_explorer_domain,
)

router = APIRouter(prefix="/platform-data", tags=["Platform Data Explorer"])


@router.get("/overview")
def get_platform_overview_endpoint(
    use_cache: bool = Query(True, description="Allow 60-second in-memory optimization cache"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Return read-only multi-domain overview metrics, health, and methodology from active database."""
    return get_platform_data_overview(db=db, use_cache=use_cache)


@router.get("/explorer/{domain}")
def get_platform_explorer_endpoint(
    domain: str,
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Records per page (max 100)"),
    sort_by: Optional[str] = Query(None, description="Domain-specific allowlisted sort column"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$", description="Sort direction: asc or desc"),
    search: Optional[str] = Query(None, max_length=120, description="Case-insensitive search term"),
    # Freight & Datasets filters
    route_code: Optional[str] = Query(None, description="Route/corridor code filter"),
    series_type: Optional[str] = Query(None, pattern="^(all|underlying|spread)$", description="Freight series classification"),
    reconstructed_only: Optional[bool] = Query(None, description="Filter to reconstructed freight observations"),
    date_from: Optional[str] = Query(None, description="Inclusive start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Inclusive end date (YYYY-MM-DD)"),
    # Trade filters
    reporter_iso2: Optional[str] = Query(None, description="2-letter reporter ISO code"),
    partner_iso2: Optional[str] = Query(None, description="2-letter partner ISO code"),
    commodity_code: Optional[str] = Query(None, description="HS commodity classification code"),
    trade_flow: Optional[str] = Query(None, description="Trade flow (Export / Import)"),
    # Environmental filters
    station_id: Optional[str] = Query(None, description="Buoy station identifier"),
    sea_area: Optional[str] = Query(None, description="Sea area substring filter"),
    quality_status: Optional[str] = Query(None, description="Observation quality status"),
    # Bunker filters
    port_code: Optional[str] = Query(None, description="UN/LOCODE port filter"),
    fuel_grade: Optional[str] = Query(None, description="Fuel grade filter (VLSFO, HSFO, MGO)"),
    pricing_type: Optional[str] = Query(None, description="Pricing qualification type"),
    # Vessel filters
    vessel_type: Optional[str] = Query(None, description="Vessel category"),
    vessel_class: Optional[str] = Query(None, description="Vessel commercial class"),
    has_imo: Optional[bool] = Query(None, description="Filter by presence of IMO number"),
    has_dwt: Optional[bool] = Query(None, description="Filter by presence of DWT tonnage"),
    # Port filters
    country_code: Optional[str] = Query(None, description="2-letter country code"),
    port_region: Optional[str] = Query(None, description="Port geographic region"),
    corridor_eligible: Optional[str] = Query(None, pattern="^(all|true|false)$", description="Monitored corridor eligibility"),
    graph_connected: Optional[str] = Query(None, pattern="^(all|true|false)$", description="MARNET graph snap status"),
    # Forecasting Dataset filters
    horizon: Optional[int] = Query(None, ge=1, description="Forecast horizon in months (1, 3, or 6)"),
    regime_id: Optional[str] = Query(None, description="Macroeconomic regime identifier"),
    is_labeled: Optional[str] = Query(None, pattern="^(all|true|false)$", description="Dataset label availability"),
    # Source Operations filters
    view: Optional[str] = Query("runs", pattern="^(runs|quarantine)$", description="Audit view: runs or quarantine"),
    run_status: Optional[str] = Query(None, description="Ingestion run status filter"),
    run_type: Optional[str] = Query(None, description="Ingestion run type filter"),
    source_name: Optional[str] = Query(None, description="Source/provider name filter"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Query server-side paginated, filtered, and sorted records for an explorer domain."""
    try:
        return query_explorer_domain(
            db=db,
            domain=domain,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
            search=search,
            route_code=route_code,
            series_type=series_type,
            reconstructed_only=reconstructed_only,
            date_from=date_from,
            date_to=date_to,
            reporter_iso2=reporter_iso2,
            partner_iso2=partner_iso2,
            commodity_code=commodity_code,
            trade_flow=trade_flow,
            station_id=station_id,
            sea_area=sea_area,
            quality_status=quality_status,
            port_code=port_code,
            fuel_grade=fuel_grade,
            pricing_type=pricing_type,
            vessel_type=vessel_type,
            vessel_class=vessel_class,
            has_imo=has_imo,
            has_dwt=has_dwt,
            country_code=country_code,
            port_region=port_region,
            corridor_eligible=corridor_eligible,
            graph_connected=graph_connected,
            horizon=horizon,
            regime_id=regime_id,
            is_labeled=is_labeled,
            view=view,
            run_status=run_status,
            run_type=run_type,
            source_name=source_name,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
