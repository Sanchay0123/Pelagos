"""Maritime routing API endpoints for operational sea-lane calculation.

Provides REST interfaces for the Pelagos maritime routing subsystem, supporting
both prototype synthetic fixtures and locally compiled Eurostat SeaRoute MARNET
networks. Traces to REQ-FR-OMR-001, REQ-FR-OMR-002, and ROUT-004.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from pelagos.config import settings
from pelagos.routing.schemas import PortRecord, RoutingRequest, RoutingResult, RoutingStatus
from pelagos.routing.service import MaritimeRoutingEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/routing", tags=["Routing"])

_engine_instance: Optional[MaritimeRoutingEngine] = None


def get_routing_engine() -> MaritimeRoutingEngine:
    """Dependency provider returning the MaritimeRoutingEngine singleton.

    Graph initialization is deferred until explicitly requested by routing queries,
    preventing heavy file I/O and graph compilation at application import time.
    """
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = MaritimeRoutingEngine()
    return _engine_instance


@router.post("/calculate", response_model=RoutingResult)
def calculate_route(
    request: RoutingRequest,
    engine: MaritimeRoutingEngine = Depends(get_routing_engine),
) -> RoutingResult:
    """Compute an operational maritime route between origin and destination.

    Supports explicit `routing_mode="prototype"` (synthetic 57-node fixture) and
    `routing_mode="marnet"` (50 km generalized Eurostat network). Dispatches calculation
    and preserves fail-closed status semantics with strictly empty geometry on failure.
    """
    try:
        return engine.calculate_route(request)
    except Exception as exc:
        logger.exception("Unexpected error during route calculation: %s", exc)
        return RoutingResult(
            route_id="error",
            status=RoutingStatus.CONFIGURATION_ERROR,
            route_method="unknown",
            routing_mode=request.routing_mode or "unknown",
            geometry=[],
            geometry_segments=[],
            waypoints=[],
            limitations=[settings.safety_disclaimer],
            data_provenance={},
            error_message=f"Routing service encountered an unexpected error: {exc}",
        )


@router.get("/ports", response_model=List[PortRecord])
def list_ports(
    engine: MaritimeRoutingEngine = Depends(get_routing_engine),
) -> List[PortRecord]:
    """Return all traceable ports registered in the hydrographic fixture."""
    return engine.port_repo.list_ports()


@router.get("/health")
def routing_health() -> Dict[str, Any]:
    """Report routing subsystem readiness without triggering heavy graph loading.

    Verifies availability of the local MARNET cache artifact without loading the
    full graph into memory at startup time.
    """
    cache_dir = Path(".cache/pelagos/routing")
    manifest_path = cache_dir / "manifest.json"
    artifact_path = cache_dir / "marnet_network.json"
    marnet_cached = manifest_path.is_file() and artifact_path.is_file()

    return {
        "status": "ok",
        "service": "pelagos-routing",
        "version": settings.app_version,
        "prototype_ready": True,
        "marnet_cache_available": marnet_cached,
        "safety_disclaimer": settings.safety_disclaimer,
    }
