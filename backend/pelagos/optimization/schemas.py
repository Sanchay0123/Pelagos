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
    UNVERIFIED = "unverified"
    INSUFFICIENT_DATA = "insufficient_data"
    DIMENSION_MISMATCH = "dimension_mismatch"
    UNSUITABLE_TYPE = "unsuitable_type"


class ParticularsCompletenessSummary(BaseModel):
    """Explicit accounting of verified vs. missing physical and commercial particulars."""

    completeness_status: str = Field(
        ...,
        description="Particulars readiness: 'complete', 'partial', or 'insufficient_data'.",
    )
    verified_particulars: List[str] = Field(
        default_factory=list,
        description="Physical or identity attributes verified in telemetry or canonical registry.",
    )
    missing_particulars: List[str] = Field(
        default_factory=list,
        description="Required physical, cargo-capacity, or commercial attributes currently missing.",
    )
    registry_vessel_class: Optional[str] = Field(
        default=None,
        description="Canonical registry vessel_class if persisted on Vessel record (separate from AIS estimate).",
    )
    registry_commercial_status: Optional[str] = Field(
        default=None,
        description="Canonical Vessel.commercial_status field (registry sync indicator, NOT spot charter availability).",
    )
    data_sources: List[str] = Field(
        default_factory=list,
        description="Source datasets contributing particulars.",
    )


class CargoCompatibilityAssessment(BaseModel):
    """Transparent vessel-to-cargo compatibility evaluation."""

    status: str = Field(
        ...,
        description="'compatible', 'potentially_compatible_unverified_capacity', 'incompatible', or 'insufficient_data'.",
    )
    cargo_tonnes: float
    commodity: str = Field(default="Agricultural Bulk Grain")
    verified_dwt_tonnes: Optional[float] = None
    capacity_check_basis: str = Field(
        default="ais_dimensional_envelope_only",
        description="'verified_dwt' when canonical DWT exists, otherwise 'ais_dimensional_envelope_only'.",
    )
    reasons: List[str] = Field(default_factory=list)
    missing_requirements: List[str] = Field(default_factory=list)


class PortConstraintEvaluation(BaseModel):
    """Load or discharge port physical access compatibility check."""

    port_id: Optional[str] = None
    port_name: Optional[str] = None
    role: str = Field(..., description="'origin_load_port' or 'destination_discharge_port'.")
    status: str = Field(
        ...,
        description="'compatible', 'incompatible', or 'insufficient_data'.",
    )
    documented_draft_limit_meters: Optional[float] = Field(
        default=None,
        description="Explicit channel/project draft limit extracted strictly from verified port records (null if unverified).",
    )
    limit_source_note: Optional[str] = Field(
        default=None,
        description="Source clause in port metadata documenting any numeric limit.",
    )
    vessel_draft_meters: Optional[float] = None
    access_limitations: Optional[str] = None
    channel_status: Optional[str] = Field(default=None, description="'SUITABLE', 'INSUITABLE', or 'UNVERIFIED'.")
    berth_status: Optional[str] = Field(default=None, description="'SUITABLE', 'INSUITABLE', or 'UNVERIFIED'.")
    air_draft_status: Optional[str] = Field(default=None, description="'SUITABLE', 'INSUITABLE', or 'UNVERIFIED'.")
    channel_depth_meters: Optional[float] = None
    berth_depth_meters: Optional[float] = None
    air_gap_meters: Optional[float] = None
    governing_restriction_type: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)
    missing_requirements: List[str] = Field(default_factory=list)


class RouteCompatibilityAssessment(BaseModel):
    """Vessel-to-route navigational, canal, and port compatibility evaluation."""

    status: str = Field(
        ...,
        description="'compatible', 'conditionally_compatible_unverified_limits', 'incompatible', or 'insufficient_data'.",
    )
    route_navigable: Optional[bool] = None
    routing_status: Optional[str] = None
    routing_mode: Optional[str] = None
    route_distance_nm: Optional[float] = None
    canals_transited: List[str] = Field(default_factory=list)
    canal_compatibility_notes: List[str] = Field(default_factory=list)
    origin_port: Optional[PortConstraintEvaluation] = None
    destination_port: Optional[PortConstraintEvaluation] = None
    missing_requirements: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)


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
    is_physically_compatible: bool = Field(
        default=False,
        description="Physical compatibility based on LOA, beam, operational draft, and port/canal envelope.",
    )
    # Stage F1.3.2: Dimensional separation of port hydrography criteria
    channel_compatibility_passed: Optional[bool] = Field(
        default=None,
        description="True if vessel clears certified channel reaches; False if exceeded; None if unverified.",
    )
    berth_compatibility_passed: Optional[bool] = Field(
        default=None,
        description="True if vessel clears certified berths; False if exceeded; None if unverified.",
    )
    air_draft_compatibility_passed: Optional[bool] = Field(
        default=None,
        description="True if vessel clears overhead bridge clearances; False if exceeded; None if unverified.",
    )
    cargo_capacity_passed: Optional[bool] = Field(
        default=None,
        description="True if verified DWT from accredited source covers cargo parcel; False if exceeded or unverified.",
    )
    overall_suitability_passed: bool = Field(
        default=False,
        description="True ONLY when both physical compatibility AND accredited cargo capacity verification pass.",
    )
    overall_suitability: SuitabilityStatus = Field(
        default=SuitabilityStatus.UNVERIFIED,
        description="Fail-closed overall suitability reflecting both physical compatibility and verified cargo capacity.",
    )
    overall_suitability_status: str = Field(
        default="insufficient_data",
        description="Granular 5-way classification of physical compatibility vs cargo capacity verification.",
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
    missing_particulars: List[str] = Field(
        default_factory=list,
        description="Explicit list of missing physical, capacity, or commercial particulars.",
    )
    registry_vessel_class: Optional[str] = Field(
        default=None,
        description="Vessel class persisted in canonical Vessel registry (distinct from AIS estimated class).",
    )
    registry_commercial_status: Optional[str] = Field(
        default=None,
        description="Canonical Vessel.commercial_status indicator (AIS registry sync presence only, NOT charter openness).",
    )
    particulars_completeness: Optional[ParticularsCompletenessSummary] = Field(
        default=None,
        description="Structured breakdown of verified vs. missing vessel particulars.",
    )
    cargo_compatibility: Optional[CargoCompatibilityAssessment] = Field(
        default=None,
        description="Explicit vessel-to-cargo compatibility evaluation.",
    )
    route_compatibility: Optional[RouteCompatibilityAssessment] = Field(
        default=None,
        description="Explicit vessel-to-route, canal, and port compatibility evaluation.",
    )


class VesselSuitabilityRequest(BaseModel):
    """Input payload for matching candidate vessels to a corridor and cargo tonnage."""

    cargo_tonnes: float = Field(
        default=52000.0,
        description="Cargo volume in metric tonnes (MT). Must be strictly positive (> 0).",
    )
    commodity: str = Field(
        default="Agricultural Bulk Grain",
        description="Cargo commodity type being evaluated.",
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
    canal_policy: str = Field(
        default="allow",
        description="Chokepoint canal routing policy ('allow', 'avoid_panama', 'avoid_suez', 'avoid_all').",
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
        description="Response status: 'matched', 'unmonitored_corridor_matched', 'unavailable', or 'invalid_input'.",
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
    destination_port_id: Optional[str] = Field(
        default=None,
        description="Destination discharging port UN/LOCODE (e.g. 'JPYOK').",
    )
    destination_port_name: Optional[str] = Field(
        default=None,
        description="Destination discharging port name (e.g. 'Yokohama').",
    )
    destination_port_coordinates: Optional[List[float]] = Field(
        default=None,
        description="Coordinates of the destination port [lon, lat].",
    )
    cargo_tonnes: float = Field(
        ...,
        description="Cargo tonnage evaluated.",
    )
    commodity: str = Field(
        default="Agricultural Bulk Grain",
        description="Cargo commodity evaluated.",
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
    route_compatibility_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Corridor routing navigability, canal transit, and port limit audit summary.",
    )
    ais_vs_commercial_distinction: str = Field(
        default=(
            "AIS position records and Vessel.commercial_status registry sync markers do NOT represent "
            "commercial charter open-tonnage availability, laycan readiness, or broker fixture confirmation."
        ),
        description="Explicit distinction between AIS/registry observations and commercial charter availability.",
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


# ---------------------------------------------------------------------------
# Stage D / Milestone D10 & D10.2 Chartering Intelligence & Vessel Assessment
# ---------------------------------------------------------------------------


class VesselParticularsInput(BaseModel):
    """Explicit vessel particulars input for standalone/unit-level suitability verification."""

    mmsi: str = Field(..., description="Maritime Mobile Service Identity.")
    imo: Optional[str] = Field(default=None, description="IMO vessel identifier.")
    vessel_name: Optional[str] = Field(default=None, description="Vessel name.")
    vessel_type: Optional[str] = Field(default=None, description="Reported vessel type.")
    vessel_type_code: Optional[int] = Field(default=None, description="AIS vessel type code.")
    length_meters: Optional[float] = Field(default=None, description="Verified length overall (LOA) in meters.")
    width_meters: Optional[float] = Field(default=None, description="Verified beam in meters.")
    draft_meters: Optional[float] = Field(default=None, description="Verified operating or max draft in meters.")
    dwt_tonnage: Optional[float] = Field(default=None, description="Verified deadweight tonnage (DWT).")
    hold_cleanliness_verified: Optional[bool] = Field(
        default=None,
        description="True only when grain-clean/hold inspection is commercially verified.",
    )
    latitude: Optional[float] = Field(default=None, description="Observed latitude in WGS84 degrees.")
    longitude: Optional[float] = Field(default=None, description="Observed longitude in WGS84 degrees.")
    speed_knots: Optional[float] = Field(default=None, description="Observed or service speed in knots.")
    observation_time: Optional[str] = Field(default=None, description="UTC timestamp (ISO-8601) of observation.")


class VesselCompatibilityRequest(BaseModel):
    """Evaluate a specific vessel against cargo and route compatibility criteria."""

    mmsi: Optional[str] = Field(
        default=None,
        description="Canonical vessel MMSI in Pelagos database to assess.",
    )
    vessel: Optional[VesselParticularsInput] = Field(
        default=None,
        description="Explicit vessel particulars (required only when assessing an unregistered candidate).",
    )
    cargo_tonnes: float = Field(
        default=52000.0,
        description="Requested cargo quantity in metric tonnes (MT). Must be > 0.",
    )
    commodity: str = Field(
        default="Agricultural Bulk Grain",
        description="Requested cargo commodity.",
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
        description="Standard corridor code (e.g. 'PNW-JPN-BULK', 'USG-JPN-BULK').",
    )
    canal_policy: str = Field(
        default="allow",
        description="Chokepoint canal routing policy ('allow', 'avoid_panama', 'avoid_suez', 'avoid_all').",
    )

    @model_validator(mode="after")
    def validate_request(self) -> VesselCompatibilityRequest:
        if self.cargo_tonnes is None or self.cargo_tonnes <= 0:
            raise ValueError(f"cargo_tonnes must be strictly positive (> 0), got {self.cargo_tonnes}")
        if not self.mmsi and self.vessel is None:
            raise ValueError("Either 'mmsi' (database lookup) or 'vessel' particulars must be provided.")
        return self


class VesselCompatibilityResult(BaseModel):
    """Explicit single-vessel suitability, particulars, cargo, and route compatibility assessment."""

    status: str = Field(
        ...,
        description="Assessment status: 'assessed', 'vessel_not_found', 'unavailable', or 'invalid_input'.",
    )
    overall_suitability: Optional[SuitabilityStatus] = None
    corridor: Optional[VoyageCorridorInfo] = None
    candidate: Optional[VesselCandidateMatch] = None
    ais_observation_status: Dict[str, Any] = Field(default_factory=dict)
    commercial_availability_status: Dict[str, Any] = Field(default_factory=dict)
    disclaimers: List[str] = Field(default_factory=lambda: list(SUITABILITY_COMMERCIAL_DISCLAIMERS))
    message: Optional[str] = None


class VerifiedVesselFixtureInput(BaseModel):
    """Verified commercial broker open-tonnage & operational particulars (never synthesized)."""

    mmsi: str
    vessel_name: str
    vessel_type: str = "Dry Bulk Carrier"
    vessel_type_code: Optional[int] = 70
    length_meters: float = Field(..., gt=0)
    width_meters: float = Field(..., gt=0)
    draft_meters: float = Field(..., gt=0)
    dwt_tonnage: float = Field(..., gt=0)
    commercial_status: str = Field(
        ...,
        description="Verified broker status: 'open', 'laden', 'drydock', 'fixed', or 'unavailable'.",
    )
    hold_cleanliness_verified: bool = Field(
        default=False,
        description="True when hold inspection/cleanliness is verified for cargo.",
    )
    current_latitude: float
    current_longitude: float
    service_speed_knots: float = Field(..., gt=0)
    daily_fuel_consumption_mt: float = Field(..., gt=0)
    spot_charter_rate_usd_per_mt: Optional[float] = Field(default=None, gt=0)
    time_charter_daily_hire_usd: Optional[float] = Field(default=None, gt=0)
    coa_consecutive_voyage_rate_usd_per_mt: Optional[float] = Field(default=None, gt=0)
    observation_time: str = Field(..., description="UTC timestamp of verified position report.")


class VerifiedCharterInputs(BaseModel):
    """Explicit bundle of verified commercial inputs required to authorize chartering recommendations."""

    source_provenance: str = Field(..., description="Source attribution for verified commercial fixture/quote inputs.")
    bunker_price_usd_per_mt: float = Field(..., gt=0, description="Verified delivered bunker price at corridor hub.")
    bunker_fuel_grade: str = Field(default="VLSFO")
    port_disbursements_usd: float = Field(default=0.0, ge=0.0)
    canal_tolls_usd: float = Field(default=0.0, ge=0.0)
    voyage_loading_discharge_days: float = Field(..., gt=0)
    consecutive_voyages_count: int = Field(default=2, ge=2)
    origin_max_draft_meters: Optional[float] = Field(default=None, gt=0)
    destination_max_draft_meters: Optional[float] = Field(default=None, gt=0)
    origin_max_loa_meters: Optional[float] = Field(default=None, gt=0)
    destination_max_loa_meters: Optional[float] = Field(default=None, gt=0)
    origin_max_beam_meters: Optional[float] = Field(default=None, gt=0)
    destination_max_beam_meters: Optional[float] = Field(default=None, gt=0)
    verified_future_rate_usd_per_mt: Optional[float] = Field(
        default=None,
        gt=0,
        description="Verified directional forward/forecast rate signal if a non-flat validated signal exists.",
    )
    vessel_fixtures: List[VerifiedVesselFixtureInput] = Field(default_factory=list)


class DataGateStatus(BaseModel):
    """Readiness audit status for a single prerequisite domain of chartering optimization."""

    domain: str
    status: str = Field(
        ...,
        description="'available', 'partial', 'insufficient_data', or 'unavailable'.",
    )
    details: str
    missing_items: List[str] = Field(default_factory=list)


class ContractCostComparison(BaseModel):
    """Deterministic procurement cost comparison across charter structures (REQ-FR-CPO-002)."""

    contract_structure: str
    total_cost_usd: float
    unit_cost_usd_per_mt: float
    voyages_evaluated: int = 1
    assumptions_used: Dict[str, Any] = Field(default_factory=dict)


class CharterRecommendRequest(BaseModel):
    """Input request for Chartering Recommendation & Readiness Gate (REQ-FR-CPO-001/002/003, CDV-002)."""

    cargo_tonnes: float = Field(
        ...,
        description="Requested cargo volume in metric tonnes (MT). Must be > 0.",
    )
    commodity: str = Field(
        default="Agricultural Bulk Grain",
        description="Cargo commodity specification.",
    )
    origin: Optional[Union[str, List[float], Dict[str, Any]]] = Field(
        default=None,
        description="Origin loading port UN/LOCODE or coordinates.",
    )
    destination: Optional[Union[str, List[float], Dict[str, Any]]] = Field(
        default=None,
        description="Destination discharging port UN/LOCODE or coordinates.",
    )
    route_code: Optional[str] = Field(
        default=None,
        description="Direct corridor code (e.g. 'PNW-JPN-BULK', 'USG-JPN-BULK').",
    )
    canal_policy: str = Field(
        default="allow",
        description="Chokepoint canal routing policy ('allow', 'avoid_panama', 'avoid_suez', 'avoid_all').",
    )
    laycan_start: Optional[date] = Field(
        default=None,
        description="Start of target cargo laycan window (YYYY-MM-DD).",
    )
    laycan_end: Optional[date] = Field(
        default=None,
        description="Laycan cancelling date (YYYY-MM-DD).",
    )
    max_vessels: int = Field(default=20, ge=1, le=200)
    verified_commercial_inputs: Optional[VerifiedCharterInputs] = Field(
        default=None,
        description="Optional caller-supplied verified commercial fixtures and bunker/port inputs; strictly None when unconnected.",
    )

    @model_validator(mode="after")
    def validate_inputs(self) -> CharterRecommendRequest:
        if self.cargo_tonnes is None or self.cargo_tonnes <= 0:
            raise ValueError(f"cargo_tonnes must be strictly positive (> 0), got {self.cargo_tonnes}")
        if self.laycan_start and self.laycan_end and self.laycan_end < self.laycan_start:
            raise ValueError("laycan_end must not precede laycan_start")
        return self


class CharterRecommendationResponse(BaseModel):
    """Deterministic chartering decision gate & recommendation output (ARCH-001 / REQ-FR-CPO-001)."""

    status: str = Field(
        ...,
        description="Outcome: 'recommended', 'insufficient_data', 'unavailable', or 'invalid_input'.",
    )
    recommendation_issued: bool = Field(
        default=False,
        description="True ONLY when all required authentic data gates pass and a valid procurement action is supported.",
    )
    recommended_action: Optional[str] = Field(
        default=None,
        description="Action Type ('Fix Now', 'Wait', 'Hedge') when issued; strictly null when insufficient_data/unavailable.",
    )
    recommended_contract_structure: Optional[str] = Field(
        default=None,
        description="Contract structure ('Spot Single Voyage', 'Consecutive Voyage Charter (COA)', 'Time Charter (TC)') when issued.",
    )
    corridor: Optional[VoyageCorridorInfo] = None
    cargo_tonnes: Optional[float] = None
    commodity: str = "Agricultural Bulk Grain"
    data_readiness_gates: Dict[str, DataGateStatus] = Field(default_factory=dict)
    missing_decision_inputs: List[str] = Field(default_factory=list)
    verified_supporting_analytics: Dict[str, Any] = Field(default_factory=dict)
    contract_comparisons: List[ContractCostComparison] = Field(default_factory=list)
    ranked_vessel_candidates: List[VesselCandidateMatch] = Field(default_factory=list)
    ais_vs_commercial_summary: str = Field(
        default=(
            "Historical AIS observations in Pelagos demonstrate dimensional screening only and do NOT "
            "establish open-tonnage availability, laycan readiness, or commercial fixture status."
        )
    )
    disclaimers: List[str] = Field(default_factory=lambda: list(SUITABILITY_COMMERCIAL_DISCLAIMERS))
    message: str = ""
