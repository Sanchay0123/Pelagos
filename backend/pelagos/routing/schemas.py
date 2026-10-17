"""Pelagos Maritime Routing - Pydantic Schemas and Data Contracts.

Defines deterministic data structures for port resolution, routing requests,
ordered polyline geometry, and status reporting.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Union
from pydantic import BaseModel, Field, model_validator


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


class RouteWaypoint(BaseModel):
    """A single navigational waypoint along the calculated sea route."""

    node_id: str
    name: str
    longitude: float
    latitude: float
    waypoint_type: str = Field(
        ...,
        description="'fairway_gate', 'sea_lane', 'canal_gate', 'chokepoint', 'strait', etc.",
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
    distance_nm: float
    distance_km: float
    canals_transited: List[str] = Field(default_factory=list)
    canal_policy_applied: str


class RoutingResult(BaseModel):
    """Comprehensive, deterministic result of a maritime route query."""

    route_id: str = Field(
        ...,
        description="Deterministic SHA-256 hash derived from canonical route inputs and graph version",
    )
    status: str = Field(
        ...,
        description="'calculated', 'unsupported_route', or 'invalid_input'",
    )
    route_method: str = "prototype_test_network_graph"
    summary: Optional[RouteSummary] = None
    geometry: List[List[float]] = Field(
        default_factory=list,
        description="Ordered list of [longitude, latitude] pairs in EPSG:4326; strictly empty when status is unsupported",
    )
    waypoints: List[RouteWaypoint] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    data_provenance: Dict[str, str] = Field(default_factory=dict)
    error_message: Optional[str] = None
