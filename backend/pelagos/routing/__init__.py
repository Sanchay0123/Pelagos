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
from pelagos.routing.graph import (
    ANTARCTIC_CIRCLE_LATITUDE,
    ARCTIC_CIRCLE_LATITUDE,
    MarnetCacheError,
    MarnetMaritimeGraph,
    MaritimeGraph,
    POLAR_CIRCLE_LATITUDE,
    PrototypeMaritimeGraph,
    is_arctic_edge,
    is_polar_edge,
)
from pelagos.routing.ports import PortRepository
from pelagos.routing.schemas import (
    PortIdentifierOrCoords,
    PortRecord,
    PortSnappingDetail,
    RouteSummary,
    RouteWaypoint,
    RoutingRequest,
    RoutingResult,
    RoutingStatus,
)
from pelagos.routing.service import MaritimeRoutingEngine

__all__ = [
    "MaritimeRoutingEngine",
    "MaritimeGraph",
    "PrototypeMaritimeGraph",
    "MarnetMaritimeGraph",
    "MarnetCacheError",
    "PortRepository",
    "RoutingRequest",
    "RoutingResult",
    "RoutingStatus",
    "RouteSummary",
    "RouteWaypoint",
    "PortRecord",
    "PortIdentifierOrCoords",
    "PortSnappingDetail",
    "is_polar_edge",
    "is_arctic_edge",
    "POLAR_CIRCLE_LATITUDE",
    "ARCTIC_CIRCLE_LATITUDE",
    "ANTARCTIC_CIRCLE_LATITUDE",
    "geodesic_distance_nm",
    "nm_to_km",
    "km_to_nm",
    "validate_coordinates",
    "NAV_SAFETY_DISCLAIMER",
    "KM_PER_NAUTICAL_MILE",
    "METERS_PER_NAUTICAL_MILE",
]
