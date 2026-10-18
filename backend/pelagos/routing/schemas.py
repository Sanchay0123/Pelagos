"""Pelagos Maritime Routing - Pydantic Schemas and Data Contracts.

Defines deterministic data structures for port resolution, routing requests,
ordered polyline geometry, and status reporting.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Union
from pydantic import BaseModel, Field, model_validator


class RoutingStatus(str, Enum):
    """Standardized operational routing outcome statuses."""

    CALCULATED = "calculated"
    UNSUPPORTED_ROUTE = "unsupported_route"
    INVALID_INPUT = "invalid_input"
    CONFIGURATION_ERROR = "configuration_error"


class PortIdentifierOrCoords(BaseModel):
    """Specification of an origin or destination via identifier or explicit coordinates."""

    unlocode: Optional[str] = Field(
        default=None,
        description="5-character UN/LOCODE trade code (e.g. 'USMSY', 'JPYOK')",
    )
    port_id: Optional[str] = Field(
        default=None,
        description="Stable internal port identifier (e.g. 'USMSY')",
    )
    name: Optional[str] = Field(
        default=None,
        description="Optional descriptive port name",
    )
    coordinates: Optional[List[float]] = Field(
        default=None,
        description="Explicit WGS 84 coordinate pair [longitude, latitude]",
    )
    longitude: Optional[float] = Field(
        default=None,
        description="Longitude in degrees (-180 to 180)",
    )
    latitude: Optional[float] = Field(
        default=None,
        description="Latitude in degrees (-90 to 90)",
    )

    @model_validator(mode="after")
    def normalize_coordinates(self) -> PortIdentifierOrCoords:
        """Ensure coordinate pair and individual lat/lon are synchronized."""
        if self.coordinates is not None and len(self.coordinates) == 2:
            if self.longitude is None:
                self.longitude = self.coordinates[0]
            if self.latitude is None:
                self.latitude = self.coordinates[1]
        elif self.longitude is not None and self.latitude is not None:
            if self.coordinates is None:
                self.coordinates = [self.longitude, self.latitude]
        return self


class PortRecord(BaseModel):
    """Auditable reference record for a port in the local port fixture."""

    port_id: str
    source_id: str
    name: str
    country: str
    country_code: str
    coordinates: List[float] = Field(
        ...,
        description="[longitude, latitude] in EPSG:4326",
    )
    crs: str = "EPSG:4326"
    coordinate_type: str = Field(
        ...,
        description="'verified_marine_access_point', 'approximate_marine_access_point', 'unverified_marine_access_point', 'port_point', or 'administrative_point'",
    )
    source_url: str
    provenance: str
    license: str
    access_limitations: str
    nearest_graph_node: Optional[str] = None


class PortSnappingDetail(BaseModel):
    """Detailed metadata for endpoint-to-graph grid snapping."""

    port_id: Optional[str] = None
    input_coordinates: List[float]
    snapped_node_id: str
    snapped_node_coordinates: List[float]
    snap_distance_nm: float
    coordinate_confidence: str
    connector_type: str = "geometric_straight_line_approximation"


class RoutingRequest(BaseModel):
    """Input parameters for a deterministic maritime route calculation."""

    origin: Union[PortIdentifierOrCoords, str] = Field(
        ...,
        description="Origin port identifier or coordinate specification",
    )
    destination: Union[PortIdentifierOrCoords, str] = Field(
        ...,
        description="Destination port identifier or coordinate specification",
    )
    canal_policy: str = Field(
        default="allow",
        description="Chokepoint bypass policy: 'allow', 'avoid_panama', 'avoid_suez', or 'avoid_all'",
    )
    routing_mode: Optional[str] = Field(
        default=None,
        description="Routing engine mode: 'prototype' (synthetic fixture) or 'marnet' (locally compiled 50 km network). Defaults to PELAGOS_ROUTING_MODE.",
    )
    max_snap_distance_nm: Optional[float] = Field(
        default=50.0,
        description="Provisional maximum permissible snapping distance from coordinates to network grid in NM.",
    )
    supports_segments: bool = Field(
        default=False,
        description="Explicit capability assertion declaring that caller supports disjoint geometry_segments rendering.",
    )


class RouteWaypoint(BaseModel):
    """A single navigational waypoint along the calculated sea route."""

    node_id: str
    name: str
    longitude: float
    latitude: float
    waypoint_type: str = Field(
        ...,
        description="'fairway_gate', 'sea_lane', 'canal_gate', 'chokepoint', 'strait', 'port_connector_approximation', 'antimeridian_bridge', etc.",
    )
    cumulative_distance_nm: float


class RouteSummary(BaseModel):
    """Summary statistics for a computed maritime route."""

    origin_id: Optional[str] = None
    origin_name: str
    origin_coordinates: List[float]
    destination_id: Optional[str] = None
    destination_name: str
    destination_coordinates: List[float]
    total_distance_nm: Optional[float] = None
    total_distance_km: Optional[float] = None
    graph_distance_nm: float = 0.0
    origin_snap_distance_nm: float = 0.0
    destination_snap_distance_nm: float = 0.0
    distance_nm: Optional[float] = None
    distance_km: Optional[float] = None
    canals_transited: List[str] = Field(default_factory=list)
    canal_policy_applied: str

    @model_validator(mode="after")
    def sync_distance_fields(self) -> RouteSummary:
        """Ensure backward compatibility between distance_nm and total_distance_nm."""
        if self.total_distance_nm is None and self.distance_nm is not None:
            self.total_distance_nm = self.distance_nm
        elif self.distance_nm is None and self.total_distance_nm is not None:
            self.distance_nm = self.total_distance_nm

        if self.total_distance_km is None and self.distance_km is not None:
            self.total_distance_km = self.distance_km
        elif self.distance_km is None and self.total_distance_km is not None:
            self.distance_km = self.total_distance_km

        if self.total_distance_nm is None:
            self.total_distance_nm = 0.0
            self.distance_nm = 0.0
        if self.total_distance_km is None:
            self.total_distance_km = 0.0
            self.distance_km = 0.0

        return self


class RoutingResult(BaseModel):
    """Comprehensive, deterministic result of a maritime route query."""

    route_id: str = Field(
        ...,
        description="Deterministic SHA-256 hash derived from canonical route inputs and graph version",
    )
    status: RoutingStatus = Field(
        ...,
        description="'calculated', 'unsupported_route', 'invalid_input', or 'configuration_error'",
    )
    route_method: str = "prototype_test_network_graph"
    routing_mode: str = "prototype"
    summary: Optional[RouteSummary] = None
    geometry: List[List[float]] = Field(
        default_factory=list,
        description="Continuous ordered list of [lon, lat] coordinate pairs. Strictly empty when status is not 'calculated' or when crossing antimeridian under unsegmented caller.",
    )
    geometry_segments: List[List[List[float]]] = Field(
        default_factory=list,
        description="Antimeridian-safe disjoint polylines. Crossing 180 deg splits geometry into separate segments to prevent visual wrap artifacts in CesiumJS and GeoJSON MultiLineString.",
    )
    waypoints: List[RouteWaypoint] = Field(default_factory=list)
    snapping_details: Optional[Dict[str, PortSnappingDetail]] = None
    limitations: List[str] = Field(default_factory=list)
    data_provenance: Dict[str, str] = Field(default_factory=dict)
    error_message: Optional[str] = None
