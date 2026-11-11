"""FastAPI router for Stage D Vessel Assessment and Chartering Recommendation Gate.

Exposes REST endpoints for vessel-to-cargo / vessel-to-route compatibility assessment
and the ARCH-001 chartering recommendation readiness gate (`/api/v1/charter/recommend`).
Traces to Milestone D10, D10.2, REQ-FR-CDV-002, REQ-FR-CPO-001/002/003, and ARCH-001.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from pelagos.api.routes.calculator import get_routing_engine
from pelagos.database import get_db
from pelagos.optimization.chartering import (
    assess_vessel_for_voyage,
    recommend_charter_strategy,
)
from pelagos.optimization.schemas import (
    CharterRecommendRequest,
    CharterRecommendationResponse,
    VesselCompatibilityRequest,
    VesselCompatibilityResult,
)
from pelagos.routing.service import MaritimeRoutingEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/charter", tags=["Chartering & Vessel Compatibility"])


@router.post("/assess", response_model=VesselCompatibilityResult)
def assess_vessel_compatibility_post(
    request: VesselCompatibilityRequest,
    db: Session = Depends(get_db),
    routing_engine: MaritimeRoutingEngine = Depends(get_routing_engine),
) -> VesselCompatibilityResult:
    """Assess a specific vessel against cargo tonnage/commodity, port limits, and route connectivity."""
    return assess_vessel_for_voyage(db=db, request=request, routing_engine=routing_engine)


@router.get("/assess", response_model=VesselCompatibilityResult)
def assess_vessel_compatibility_get(
    mmsi: str = Query(..., description="Vessel MMSI identifier in Pelagos database"),
    cargo_tonnes: float = Query(52000.0, gt=0, description="Cargo tonnage in metric tonnes (MT)"),
    commodity: str = Query("Agricultural Bulk Grain", description="Cargo commodity type"),
    origin: Optional[str] = Query(None, description="Origin loading port UN/LOCODE (e.g. USSEA)"),
    destination: Optional[str] = Query(None, description="Destination discharging port UN/LOCODE (e.g. JPYOK)"),
    route_code: Optional[str] = Query(None, description="Standard corridor code (e.g. PNW-JPN-BULK)"),
    canal_policy: str = Query("allow", description="Canal transit policy ('allow', 'avoid_panama', 'avoid_suez', 'avoid_all')"),
    db: Session = Depends(get_db),
    routing_engine: MaritimeRoutingEngine = Depends(get_routing_engine),
) -> VesselCompatibilityResult:
    """Assess a specific database vessel against cargo and route criteria via query parameters."""
    req = VesselCompatibilityRequest(
        mmsi=mmsi,
        cargo_tonnes=cargo_tonnes,
        commodity=commodity,
        origin=origin,
        destination=destination,
        route_code=route_code,
        canal_policy=canal_policy,
    )
    return assess_vessel_for_voyage(db=db, request=req, routing_engine=routing_engine)


@router.post("/recommend", response_model=CharterRecommendationResponse)
def recommend_charter_post(
    request: CharterRecommendRequest,
    db: Session = Depends(get_db),
    routing_engine: MaritimeRoutingEngine = Depends(get_routing_engine),
) -> CharterRecommendationResponse:
    """Evaluate commercial/physical data readiness gates and issue chartering recommendations only when supported."""
    return recommend_charter_strategy(db=db, request=request, routing_engine=routing_engine)


@router.get("/recommend", response_model=CharterRecommendationResponse)
def recommend_charter_get(
    cargo_tonnes: float = Query(..., gt=0, description="Cargo tonnage in metric tonnes (MT)"),
    commodity: str = Query("Agricultural Bulk Grain", description="Cargo commodity"),
    origin: Optional[str] = Query(None, description="Origin loading port UN/LOCODE"),
    destination: Optional[str] = Query(None, description="Destination discharging port UN/LOCODE"),
    route_code: Optional[str] = Query(None, description="Direct corridor code (e.g. PNW-JPN-BULK)"),
    canal_policy: str = Query("allow", description="Canal routing policy"),
    laycan_start: Optional[date] = Query(None, description="Laycan start date (YYYY-MM-DD)"),
    laycan_end: Optional[date] = Query(None, description="Laycan cancelling date (YYYY-MM-DD)"),
    max_vessels: int = Query(20, ge=1, le=200, description="Maximum vessel candidates to include"),
    db: Session = Depends(get_db),
    routing_engine: MaritimeRoutingEngine = Depends(get_routing_engine),
) -> CharterRecommendationResponse:
    """Evaluate chartering decision readiness gates and supporting analytics via query parameters."""
    req = CharterRecommendRequest(
        cargo_tonnes=cargo_tonnes,
        commodity=commodity,
        origin=origin,
        destination=destination,
        route_code=route_code,
        canal_policy=canal_policy,
        laycan_start=laycan_start,
        laycan_end=laycan_end,
        max_vessels=max_vessels,
    )
    return recommend_charter_strategy(db=db, request=req, routing_engine=routing_engine)

