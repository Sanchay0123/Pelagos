"""Pelagos Optimization & Commercial Chartering - Schemas and Data Contracts.

Defines deterministic data structures for the Voyage Freight-Cost Calculator (OPT-001).
Traces to REQ-FR-DII-005, REQ-FR-FRF-001, and Pelagos Milestone D10.
"""

from __future__ import annotations

from enum import Enum
from datetime import date
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, model_validator


class CalculationStatus(str, Enum):
    """Standardized outcome status of a voyage cost calculation."""

    CALCULATED = "calculated"
    UNAVAILABLE = "unavailable"
    INVALID_INPUT = "invalid_input"


class VoyageCalculationRequest(BaseModel):
    """Input payload for calculating voyage gross freight cost."""

    cargo_tonnes: float = Field(
        ...,
        description="Cargo volume in metric tonnes (MT). Must be strictly positive (> 0).",
    )
    cargo_unit: str = Field(
        default="MT",
        description="Cargo volume unit. Must be metric tonnes ('MT').",
    )
    origin: Optional[Union[str, List[float], Dict[str, Any]]] = Field(
        default=None,
        description="Origin port UN/LOCODE (e.g. 'USSEA'), port ID, or [lon, lat] coordinates.",
    )
    destination: Optional[Union[str, List[float], Dict[str, Any]]] = Field(
        default=None,
        description="Destination port UN/LOCODE (e.g. 'JPYOK'), port ID, or [lon, lat] coordinates.",
    )
    origin_lon: Optional[float] = Field(
        default=None,
        description="Explicit origin longitude in decimal degrees (-180 to 180).",
    )
    origin_lat: Optional[float] = Field(
        default=None,
        description="Explicit origin latitude in decimal degrees (-90 to 90).",
    )
    dest_lon: Optional[float] = Field(
        default=None,
        description="Explicit destination longitude in decimal degrees (-180 to 180).",
    )
    dest_lat: Optional[float] = Field(
        default=None,
        description="Explicit destination latitude in decimal degrees (-90 to 90).",
    )
    route_code: Optional[str] = Field(
        default=None,
        description="Direct standard corridor code (e.g. 'PNW-JPN-BULK', 'USG-JPN-BULK').",
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description="Point-in-time date for rate observation lookup (defaults to latest available).",
    )
    canal_policy: str = Field(
        default="allow",
        description="Chokepoint routing policy for operational distance ('allow', 'avoid_panama', 'avoid_suez').",
    )

    @model_validator(mode="after")
    def validate_inputs(self) -> VoyageCalculationRequest:
        """Validate positive cargo tonnage and input presence."""
        if self.cargo_tonnes is not None and self.cargo_tonnes <= 0:
            raise ValueError(f"cargo_tonnes must be strictly positive (> 0), got {self.cargo_tonnes}")

        # Normalize individual coordinate floats into origin / destination lists if provided
        if self.origin is None and self.origin_lon is not None and self.origin_lat is not None:
            self.origin = [self.origin_lon, self.origin_lat]
        if self.destination is None and self.dest_lon is not None and self.dest_lat is not None:
            self.destination = [self.dest_lon, self.dest_lat]

        return self


class ApplicableRateInfo(BaseModel):
    """Detailed audit metadata for the freight market rate applied to the calculation."""

    rate_value: float = Field(
        ...,
        description="Freight rate value in USD per metric tonne.",
    )
    currency: str = Field(
        default="USD",
        description="Rate currency ISO-4217 code.",
    )
    unit: str = Field(
        default="USD/MT",
        description="Rate unit of measure (e.g. 'USD/MT').",
    )
    rate_type: str = Field(
        default="observed",
        description="Nature of the rate: 'observed' (historical benchmark) or 'forecast'.",
    )
    observation_date: Optional[str] = Field(
        default=None,
        description="Date of the rate observation (YYYY-MM-DD).",
    )
    observation_period: Optional[str] = Field(
        default="monthly",
        description="Observation cadence / frequency (e.g. 'monthly', 'weekly').",
    )
    source_name: str = Field(
        ...,
        description="Authoritative data publisher / provider name.",
    )
    source_dataset_id: Optional[str] = Field(
        default=None,
        description="Identifier of the source dataset (e.g. 'USDA-GTR-TABLE-2').",
    )
    license_type: Optional[str] = Field(
        default="U.S. Public Domain",
        description="Data licensing governing the observation.",
    )
    is_derived: bool = Field(
        default=False,
        description="True if rate is derived (spreads/indices); False for direct market rates.",
    )
    provenance_validated: bool = Field(
        default=True,
        description="Assertion that rate unit, positive value, and source metadata have been verified.",
    )


class RouteDistanceInfo(BaseModel):
    """Navigational or benchmark distance metadata applied to the voyage."""

    distance_nm: float = Field(
        ...,
        description="Voyage distance in nautical miles (NM).",
    )
    distance_km: Optional[float] = Field(
        default=None,
        description="Voyage distance in kilometers (KM).",
    )
    distance_source: str = Field(
        ...,
        description="Source methodology: 'corridor_benchmark_distance', 'eurostat_marnet_network', or 'prototype_sea_lane_graph'.",
    )
    distance_unit: str = Field(
        default="NM",
        description="Distance measurement unit ('NM').",
    )
    provenance: Dict[str, str] = Field(
        default_factory=dict,
        description="Full auditable provenance metadata for the distance calculation.",
    )


class VoyageCorridorInfo(BaseModel):
    """Metadata describing the commercial trade corridor."""

    route_code: str = Field(..., description="Unique corridor identifier (e.g. 'PNW-JPN-BULK').")
    route_name: str = Field(..., description="Human-readable corridor title.")
    origin_region: str = Field(..., description="Geographic trade origin region.")
    destination_region: str = Field(..., description="Geographic trade destination region.")
    commodity: Optional[str] = Field(default=None, description="Cargo commodity classification.")
    vessel_class: Optional[str] = Field(default=None, description="Benchmark vessel class.")


class VoyageCalculationResult(BaseModel):
    """Deterministic output of the Voyage Freight-Cost Calculator (OPT-001)."""

    status: CalculationStatus = Field(
        ...,
        description="Calculation outcome: 'calculated', 'unavailable', or 'invalid_input'.",
    )
    cargo_tonnes: Optional[float] = Field(
        default=None,
        description="Validated cargo volume in metric tonnes.",
    )
    cargo_unit: str = Field(
        default="MT",
        description="Cargo unit of measure ('MT').",
    )
    route: Optional[VoyageCorridorInfo] = Field(
        default=None,
        description="Resolved commercial corridor information.",
    )
    origin_port: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Resolved origin port identification and coordinates if applicable.",
    )
    destination_port: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Resolved destination port identification and coordinates if applicable.",
    )
    applicable_rate: Optional[ApplicableRateInfo] = Field(
        default=None,
        description="Authoritative rate used for cost calculation.",
    )
    distance: Optional[RouteDistanceInfo] = Field(
        default=None,
        description="Navigational or benchmark distance with validated units and provenance.",
    )
    gross_freight_cost: Optional[float] = Field(
        default=None,
        description="Gross freight cost in USD (cargo tonnes * rate per tonne).",
    )
    gross_freight_cost_currency: str = Field(
        default="USD",
        description="Currency code for gross freight cost.",
    )
    calculation_formula: Optional[str] = Field(
        default="gross_freight_cost = cargo_tonnes * rate_per_tonne",
        description="Deterministic formula applied for the gross freight cost.",
    )
    disclaimers: List[str] = Field(
        default_factory=list,
        description="Authoritative commercial and navigational disclaimers.",
    )
    message: Optional[str] = Field(
        default=None,
        description="Explanatory or diagnostic message accompanying the status.",
    )


class SuitabilityStatus(str, Enum):
    """Standardized suitability classification for vessel candidate matching."""

    POTENTIALLY_SUITABLE = "potentially_suitable"
    INSUFFICIENT_DATA = "insufficient_data"
    DIMENSION_MISMATCH = "dimension_mismatch"
    UNSUITABLE_TYPE = "unsuitable_type"


class VesselCandidateMatch(BaseModel):
    """Candidate vessel evaluation record based on verified physical criteria."""

    mmsi: str = Field(..., description="Maritime Mobile Service Identity.")
    imo: Optional[str] = Field(default=None, description="International Maritime Organization number if reported.")
    vessel_name: str = Field(..., description="Reported vessel name.")
    vessel_type: str = Field(..., description="Reported AIS vessel type string from broadcast.")
    vessel_type_code: Optional[int] = Field(default=None, description="Reported numeric AIS vessel type code.")
    suitability_status: SuitabilityStatus = Field(
        ...,
        description="Categorical suitability assessment.",
    )
    suitability_reason: str = Field(
        ...,
        description="Explainable, transparent rationale for candidate classification and ranking.",
    )
    distance_to_load_port_nm: Optional[float] = Field(
        default=None,
        description="Great-Circle distance to origin loading port in nautical miles (NM) at observation fix.",
    )
    distance_calculation_method: str = Field(
        default="Haversine Great-Circle formula (mean radius 3,440.065 NM)",
        description="Documented methodology for loading-port distance.",
    )
    distance_qualification: str = Field(
        default="HISTORICAL_OBSERVATION_ONLY",
        description="Explicit qualification of distance: historical observation vs current proximity.",
    )
    latitude: Optional[float] = Field(default=None, description="WGS84 latitude of last position fix.")
    longitude: Optional[float] = Field(default=None, description="WGS84 longitude of last position fix.")
    speed_knots: Optional[float] = Field(default=None, description="Reported speed over ground in knots.")
    course_degrees: Optional[float] = Field(default=None, description="Reported course over ground in degrees.")
    draft_meters: Optional[float] = Field(default=None, description="Reported present static draft in meters.")
    length_meters: Optional[float] = Field(default=None, description="Reported overall vessel length in meters.")
    width_meters: Optional[float] = Field(default=None, description="Reported vessel beam in meters.")
    estimated_dimensional_class: Optional[str] = Field(
        default=None,
        description="Estimated bulk carrier dimensional class based on length and beam.",
    )
    verified_cargo_capacity_dwt: Optional[float] = Field(
        default=None,
        description="Verified deadweight tonnage (DWT). Null when unverified; AIS broadcasts do not provide DWT.",
    )
    cargo_capacity_status: str = Field(
        default="UNVERIFIED",
        description="Verification status of vessel cargo capacity: 'UNVERIFIED' from AIS telemetry.",
    )
    screening_stage: str = Field(
        default="dimensionally_screened",
        description="Indicates candidate is dimensionally screened only, not confirmed cargo-capable.",
    )
    observation_time: Optional[str] = Field(
        default=None,
        description="UTC timestamp of the AIS observation (ISO-8601).",
    )
    observation_age_seconds: Optional[float] = Field(
        default=None,
        description="Age of AIS observation in seconds at time of evaluation.",
    )
    is_stale: bool = Field(
        default=False,
        description="True if observation is older than 30 minutes or from a historical dataset.",
    )
    data_mode: str = Field(
        default="historical",
        description="Data mode: 'historical' (archived sample) or 'live' (real-time stream).",
    )
    laycan_feasibility: str = Field(
        default="UNCONFIRMED",
        description="Laycan status: strictly UNCONFIRMED without commercial broker fixture lists.",
    )
    laycan_feasibility_note: str = Field(
        default="AIS telemetry does not report commercial employment, charter party commitments, fixture status, or holds cleanliness. Laycan arrival feasibility cannot be claimed without commercial broker confirmation.",
        description="Transparent notice regarding laycan and commercial fixture boundaries.",
    )
    commercial_availability: str = Field(
        default="UNCONFIRMED",
        description="Commercial fixture availability: strictly UNCONFIRMED from AIS telemetry.",
    )
    commercial_availability_note: str = Field(
        default="Position fixes and AIS static broadcasts do not establish open-tonnage status, spot charter availability, or charter party commitments.",
        description="Transparent notice regarding commercial availability.",
    )


class VesselSuitabilityRequest(BaseModel):
    """Input payload for matching candidate vessels to a corridor and cargo tonnage."""

    cargo_tonnes: float = Field(
        default=52000.0,
        description="Cargo volume in metric tonnes (MT). Must be strictly positive (> 0).",
    )
    origin: Optional[Union[str, List[float], Dict[str, Any]]] = Field(
        default=None,
        description="Origin port UN/LOCODE (e.g. 'USSEA') or coordinates.",
    )
    destination: Optional[Union[str, List[float], Dict[str, Any]]] = Field(
        default=None,
        description="Destination port UN/LOCODE (e.g. 'JPYOK') or coordinates.",
    )
    route_code: Optional[str] = Field(
        default=None,
        description="Direct corridor code (e.g. 'PNW-JPN-BULK', 'USG-JPN-BULK').",
    )
    max_vessels: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum candidates to return.",
    )

    @model_validator(mode="after")
    def validate_cargo(self) -> VesselSuitabilityRequest:
        if self.cargo_tonnes is not None and self.cargo_tonnes <= 0:
            raise ValueError(f"cargo_tonnes must be strictly positive (> 0), got {self.cargo_tonnes}")
        return self


SUITABILITY_COMMERCIAL_DISCLAIMERS: List[str] = [
    "Potential vessel suitability is evaluated strictly as a dimensional screening (AIS vessel type and reported dimensions).",
    "Verified deadweight tonnage (DWT), hold cleanliness, and cubic capacity are NOT verified from AIS broadcast telemetry.",
    "Historical AIS observations (including January 1, 2024 sample records) demonstrate dimensional screening only and do NOT represent current fleet locations or real-time proximity to loading ports.",
    "Commercial open-tonnage status, charter party commitments, vessel employment, and fixture lists are NOT reported in AIS broadcasts.",
    "A vessel appearing near the loading port in historical data may be laden, committed to another charterer, under repair, or in ballast for a different cargo.",
    "Laycan arrival feasibility cannot be claimed without verified commercial fixture data and vessel itinerary confirmations.",
    "Distance to loading port represents straight-line Great-Circle distance (Haversine formula) at the time of observation, not an unobstructed navigable route or current vessel proximity.",
]


class VesselSuitabilityResponse(BaseModel):
    """Structured outcome of candidate vessel suitability evaluation."""

    status: str = Field(
        ...,
        description="Response status: 'matched', 'unavailable', or 'invalid_input'.",
    )
    corridor: Optional[VoyageCorridorInfo] = Field(
        default=None,
        description="Resolved freight corridor details.",
    )
    loading_port_id: Optional[str] = Field(
        default=None,
        description="Origin loading port UN/LOCODE (e.g. 'USSEA').",
    )
    loading_port_name: Optional[str] = Field(
        default=None,
        description="Origin loading port name (e.g. 'Seattle').",
    )
    loading_port_coordinates: Optional[List[float]] = Field(
        default=None,
        description="Coordinates of the loading port [lon, lat].",
    )
    cargo_tonnes: float = Field(
        ...,
        description="Cargo tonnage evaluated.",
    )
    target_vessel_class: Optional[str] = Field(
        default=None,
        description="Target vessel class for corridor/cargo (e.g. 'Panamax / Supramax').",
    )
    total_vessels_evaluated: int = Field(
        default=0,
        description="Total distinct vessels retrieved and evaluated from database.",
    )
    potentially_suitable_count: int = Field(
        default=0,
        description="Count of vessels matching potential physical suitability criteria.",
    )
    candidates: List[VesselCandidateMatch] = Field(
        default_factory=list,
        description="Ranked list of candidate vessels.",
    )
    data_mode: str = Field(
        default="historical",
        description="Data mode of underlying telemetry: 'historical' (archived sample) or 'live' (real-time stream).",
    )
    data_mode_notice: str = Field(
        default="Telemetry dataset consists of historical AIS records (January 1, 2024 sample). Stale observations demonstrate dimensional candidate screening only and do NOT represent current fleet locations or real-time proximity to loading ports.",
        description="Authoritative notice regarding data mode and observation freshness.",
    )
    disclaimers: List[str] = Field(
        default_factory=lambda: list(SUITABILITY_COMMERCIAL_DISCLAIMERS),
        description="Authoritative commercial and AIS telemetry disclaimers.",
    )
    message: Optional[str] = Field(
        default=None,
        description="Explanatory or diagnostic message.",
    )

