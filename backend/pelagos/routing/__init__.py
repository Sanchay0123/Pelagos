"""Pelagos Maritime Routing Engine.

Provides deterministic, locally executable sea-lane routing, traceable port
resolution, and geodesic distance calculations.
"""

from pelagos.routing.geometry import (
    KM_PER_NAUTICAL_MILE,
    METERS_PER_NAUTICAL_MILE,
    NAV_SAFETY_DISCLAIMER,
    geodesic_distance_nm,
    km_to_nm,
    nm_to_km,
    validate_coordinates,
)
from pelagos.routing.graph import MaritimeGraph
from pelagos.routing.ports import PortRepository
from pelagos.routing.schemas import (
    PortIdentifierOrCoords,
    PortRecord,
    RouteSummary,
    RouteWaypoint,
    RoutingRequest,
    RoutingResult,
)
from pelagos.routing.service import MaritimeRoutingEngine

__all__ = [
    "MaritimeRoutingEngine",
    "MaritimeGraph",
    "PortRepository",
    "RoutingRequest",
    "RoutingResult",
    "RouteSummary",
    "RouteWaypoint",
    "PortRecord",
    "PortIdentifierOrCoords",
    "geodesic_distance_nm",
    "nm_to_km",
    "km_to_nm",
    "validate_coordinates",
    "NAV_SAFETY_DISCLAIMER",
    "KM_PER_NAUTICAL_MILE",
    "METERS_PER_NAUTICAL_MILE",
]
