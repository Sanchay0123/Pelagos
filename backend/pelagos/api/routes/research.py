"""FastAPI router for Stage F1.5.3-B Dedicated Research Voyage Assessment API.

Exposes controlled Level 2 research voyage assessments through a dedicated endpoint,
strictly decoupled from commercial charter recommendation and operational fixture matching.

Traces to:
- Milestone F1.5.2-B (Controlled Research Pilot Integration)
- Milestone F1.5.3-A (Research Assessment Remediation & Test Baseline Restoration)
- Milestone F1.5.3-B (Dedicated Research Voyage Assessment API)
- Governance Tier: LEVEL_2_RESEARCH_ONLY (is_ready_for_operations = False)
"""

from __future__ import annotations

import datetime
from datetime import date
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from pelagos.api.routes.calculator import get_routing_engine
from pelagos.database import get_db
from pelagos.optimization.research_voyage_assessment import (
    ResearchVoyageAssessmentService,
    ResearchVoyageInput,
)
from pelagos.optimization.schemas import (
    ResearchVoyageAssessmentRequest,
    ResearchVoyageAssessmentResponse,
)
from pelagos.routing.bunker import UserSuppliedBunkerAssumption
from pelagos.routing.service import MaritimeRoutingEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research", tags=["Research Voyage Assessment"])


def get_research_assessment_service(
    db: Session = Depends(get_db),
    routing_engine: MaritimeRoutingEngine = Depends(get_routing_engine),
) -> ResearchVoyageAssessmentService:
    """Dependency provider injecting database session and routing engine into the research service."""
    return ResearchVoyageAssessmentService(
        db=db,
        routing_engine=routing_engine,
    )


@router.post(
    "/voyage-assessment",
    response_model=ResearchVoyageAssessmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute Controlled Level 2 Research Voyage Assessment",
    description=(
        "Executes a deterministic, evidence-labelled Level 2 research voyage assessment "
        "for the qualified US Gulf–Japan dry bulk corridor (USHOU -> Panama -> JPYOK) "
        "using flagship pilot vessel NAVIOS VICTORY (IMO 9713430).\n\n"
        "**Strict Governance Invariant:** Level 3 operational activation remains permanently locked "
        "(`is_ready_for_operations = False`). This endpoint is strictly decoupled from commercial "
        "charter recommendations and production trading bidding."
    ),
)
def assess_research_voyage_post(
    request: ResearchVoyageAssessmentRequest,
    service: ResearchVoyageAssessmentService = Depends(get_research_assessment_service),
) -> ResearchVoyageAssessmentResponse:
    """Execute controlled research assessment from JSON request payload."""
    # 1. Strict corridor qualification validation
    if request.origin_unlocode != "USHOU" or request.destination_unlocode != "JPYOK":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"UNSUPPORTED_RESEARCH_CORRIDOR: Level 2 research assessment is qualified exclusively "
                f"for the US Gulf–Japan dry bulk corridor (origin 'USHOU' -> destination 'JPYOK'). "
                f"Corridor '{request.origin_unlocode}' -> '{request.destination_unlocode}' has not "
                f"completed source qualification (F1.5.1 / F1.5.2-A)."
            ),
        )

    # 2. Strict vessel qualification validation
    if request.vessel_imo != "9713430":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"UNQUALIFIED_RESEARCH_VESSEL: Level 2 research assessment is qualified exclusively "
                f"for flagship NAVIOS VICTORY (IMO 9713430). Vessel IMO '{request.vessel_imo}' "
                f"has not completed ClassNK survey verification or SFOC load curve calibration."
            ),
        )

    # 3. Execute controlled research assessment via service
    try:
        service_input = request.to_service_input()
        result = service.evaluate_research_voyage(service_input)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Service error during research voyage assessment: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "RESEARCH_ASSESSMENT_EXECUTION_ERROR: An internal service error occurred "
                "during research voyage calculation. Please refer to server logs."
            ),
        )

    return ResearchVoyageAssessmentResponse(**result.model_dump())


@router.get(
    "/voyage-assessment",
    response_model=ResearchVoyageAssessmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Controlled Level 2 Research Voyage Assessment (Query Params)",
    description=(
        "Convenience GET endpoint executing the Level 2 USG-Japan research voyage assessment "
        "with default or query-supplied parameters."
    ),
)
def assess_research_voyage_get(
    origin: str = Query("USHOU", description="Origin port UN/LOCODE (default: 'USHOU')"),
    destination: str = Query("JPYOK", description="Destination port UN/LOCODE (default: 'JPYOK')"),
    vessel_imo: str = Query("9713430", description="Research vessel IMO (default: '9713430')"),
    transit_date: date = Query(date(2024, 5, 1), description="Transit date (default: 2024-05-01)"),
    cargo_tonnes: float = Query(60000.0, gt=0.0, description="Cargo intake in MT"),
    freight_rate_override: Optional[float] = Query(None, gt=0.0, description="Freight rate override in USD/MT"),
    user_bunker_price: Optional[float] = Query(None, gt=0.0, description="User-supplied bunker price in USD/MT"),
    steaming_speed_knots: float = Query(12.5, gt=0.0, le=25.0, description="Steaming speed in knots"),
    ballast_sea_days: float = Query(0.0, ge=0.0, description="Prior ballast sea days"),
    operational_mode: bool = Query(False, description="Operational mode flag"),
    service: ResearchVoyageAssessmentService = Depends(get_research_assessment_service),
) -> ResearchVoyageAssessmentResponse:
    """Execute controlled research assessment via HTTP GET query parameters."""
    user_bunker = None
    if user_bunker_price is not None:
        user_bunker = UserSuppliedBunkerAssumption(
            price_usd_per_mt=user_bunker_price,
            fuel_grade="VLSFO",
            as_of_date=transit_date,
            port_unlocode=origin.upper().strip(),
            user_label="Query Parameter Bunker Assumption",
        )

    req = ResearchVoyageAssessmentRequest(
        origin_unlocode=origin,
        destination_unlocode=destination,
        vessel_imo=vessel_imo,
        transit_date=transit_date,
        cargo_tonnes=cargo_tonnes,
        freight_rate_override_usd_per_mt=freight_rate_override,
        user_bunker_assumption=user_bunker,
        steaming_speed_knots=steaming_speed_knots,
        ballast_sea_days=ballast_sea_days,
        operational_mode=operational_mode,
    )
    return assess_research_voyage_post(request=req, service=service)
