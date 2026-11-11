"""FastAPI router for Voyage Freight-Cost Calculator (OPT-001).

Exposes deterministic voyage cost calculation endpoints conforming to Pelagos REST conventions.
Traces to REQ-FR-DII-005, REQ-FR-FRF-001, and Pelagos Milestone D10.
"""

from __future__ import annotations

import logging
from typing import Optional
from datetime import date
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from pelagos.database import get_db
from pelagos.routing.service import MaritimeRoutingEngine
from pelagos.optimization.schemas import (
    VoyageCalculationRequest,
    VoyageCalculationResult,
    VesselSuitabilityRequest,
    VesselSuitabilityResponse,
)
from pelagos.optimization.calculator import calculate_voyage_freight
from pelagos.optimization.matcher import match_suitable_vessels

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calculator", tags=["Voyage Freight Calculator"])

# Lazy-loaded default routing engine singleton for optional distance routing
_routing_engine: Optional[MaritimeRoutingEngine] = None


def get_routing_engine() -> MaritimeRoutingEngine:
    """Retrieve or lazily initialize the routing engine dependency."""
    global _routing_engine
    if _routing_engine is None:
        try:
            _routing_engine = MaritimeRoutingEngine()
        except Exception as exc:
            logger.warning("Could not initialize MaritimeRoutingEngine in calculator router: %s", exc)
            return None  # type: ignore
    return _routing_engine


@router.post("/voyage-freight", response_model=VoyageCalculationResult)
def calculate_voyage_freight_post(
    request: VoyageCalculationRequest,
    db: Session = Depends(get_db),
    routing_engine: MaritimeRoutingEngine = Depends(get_routing_engine),
) -> VoyageCalculationResult:
    """Calculate deterministic gross voyage freight cost from JSON request body.

    Calculates gross freight cost as cargo tonnes multiplied by the applicable verified
    freight rate in USD per metric tonne. Enforces strict unit validation, provenance tracking,
    and returns explicit UNAVAILABLE status for unsupported routes without fabricating rates.
    """
    return calculate_voyage_freight(db=db, request=request, routing_engine=routing_engine)


@router.get("/voyage-freight", response_model=VoyageCalculationResult)
def calculate_voyage_freight_get(
    cargo_tonnes: float = Query(..., gt=0, description="Cargo volume in metric tonnes (MT)"),
    origin: Optional[str] = Query(None, description="Origin port UN/LOCODE (e.g. USSEA) or 'lon,lat'"),
    destination: Optional[str] = Query(None, description="Destination port UN/LOCODE (e.g. JPYOK) or 'lon,lat'"),
    route_code: Optional[str] = Query(None, description="Direct corridor code (e.g. PNW-JPN-BULK, USG-JPN-BULK)"),
    as_of_date: Optional[date] = Query(None, description="Point-in-time observation date (YYYY-MM-DD)"),
    canal_policy: str = Query("allow", description="Routing canal policy ('allow', 'avoid_panama', 'avoid_suez')"),
    db: Session = Depends(get_db),
    routing_engine: MaritimeRoutingEngine = Depends(get_routing_engine),
) -> VoyageCalculationResult:
    """Calculate deterministic gross voyage freight cost via query parameters."""
    req = VoyageCalculationRequest(
        cargo_tonnes=cargo_tonnes,
        origin=origin,
        destination=destination,
        route_code=route_code,
        as_of_date=as_of_date,
        canal_policy=canal_policy,
    )
    return calculate_voyage_freight(db=db, request=req, routing_engine=routing_engine)


@router.post("/vessel-suitability", response_model=VesselSuitabilityResponse)
def match_vessel_suitability_post(
    request: VesselSuitabilityRequest,
    db: Session = Depends(get_db),
    routing_engine: MaritimeRoutingEngine = Depends(get_routing_engine),
) -> VesselSuitabilityResponse:
    """Evaluate candidate vessel suitability for corridor and cargo tonnage from JSON body.

    Filters genuine vessel observations from database, matches physical dimensions against
    cargo parcel requirements, calculates Great-Circle distance to origin loading port,
    and returns transparent, evidence-backed candidate rankings.
    """
    return match_suitable_vessels(db=db, request=request, routing_engine=routing_engine)


@router.get("/vessel-suitability", response_model=VesselSuitabilityResponse)
def match_vessel_suitability_get(
    cargo_tonnes: float = Query(52000.0, gt=0, description="Cargo volume in metric tonnes (MT)"),
    origin: Optional[str] = Query(None, description="Origin port UN/LOCODE (e.g. USSEA)"),
    destination: Optional[str] = Query(None, description="Destination port UN/LOCODE (e.g. JPYOK)"),
    route_code: Optional[str] = Query(None, description="Direct corridor code (e.g. PNW-JPN-BULK)"),
    canal_policy: str = Query("allow", description="Routing canal policy ('allow', 'avoid_panama', 'avoid_suez', 'avoid_all')"),
    max_vessels: int = Query(50, ge=1, le=500, description="Maximum candidates to return"),
    db: Session = Depends(get_db),
    routing_engine: MaritimeRoutingEngine = Depends(get_routing_engine),
) -> VesselSuitabilityResponse:
    """Evaluate candidate vessel suitability via query parameters."""
    req = VesselSuitabilityRequest(
        cargo_tonnes=cargo_tonnes,
        origin=origin,
        destination=destination,
        route_code=route_code,
        canal_policy=canal_policy,
        max_vessels=max_vessels,
    )
    return match_suitable_vessels(db=db, request=req, routing_engine=routing_engine)

