"""Pelagos Vessel-Level Fuel Consumption Intelligence Model (Stage F1.4.2.2c / F1.4.2.2c.1).

Establishes a rigorous, traceable, naval-architecture-based calculation foundation
linking vessel speed, hydrodynamic resistance, propulsion power, engine load,
Specific Fuel Oil Consumption (SFOC), and auxiliary electrical consumers.

Naval Architecture Principles & Strict Invariants:
1. SFOC is NOT speed-to-power: SFOC specifies fuel mass per unit mechanical energy (g/kWh).
   Speed-to-power requires hydrodynamic resistance / Admiralty formulation.
2. DWT is NOT displacement: Deadweight Tonnage (DWT) is the carrying capacity of cargo,
   fuel, fresh water, and ballast. Displacement (Delta) is the total immersed mass
   (Delta = DWT + Lightweight Tonnage LDT). Substituting DWT for displacement is strictly
   prohibited as it introduces systematic 8-15% underestimation of power and fuel rates.
3. Loading Condition Separation: Laden and ballast hydrostatics are distinct.
   Ballast displacement and ballast Admiralty coefficients cannot be derived using arbitrary
   percentages. Silent substitution of laden coefficients for ballast conditions is prohibited.
4. Weather Margin Governance: The weather margin is an explicit research sensitivity parameter,
   NOT a validated dynamic weather correction derived from NOAA wind/wave models.
   Speed Through Water (STW) differs physically from Speed Over Ground (SOG).
5. Engine Load & SFOC Range Boundaries:
   - Operating load % is calculated relative to the engine's verified rating definition (e.g. MCR).
   - If load exceeds 100% MCR, calculation fails with ENGINE_OVERLOAD.
   - If load is below the documented SFOC curve minimum (e.g. 50% MCR), the engine can physically
     operate (e.g. harbor maneuvering, slow steaming with auxiliary blowers), but the model
     returns SFOC_UNAVAILABLE because zero-extrapolation governance prevents guessing unverified rates.
6. Auxiliary Scenario Governance: Auxiliary and port consumption figures are aggregate fleet-average
   research scenarios derived from the Fourth IMO GHG Study 2020 (Table 23), NOT observed vessel telemetry.
   The geared deck-crane operational increment is an engineering scenario model, not a statutory table.
7. Fail-Closed Source Governance: Operational calculations (operational_mode=True) fail closed
   when certified hydrostatics or SFOC source permissions are insufficient.
   Research calculations (operational_mode=False) retain provenance, limitations, and citations.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy.orm import Session

from pelagos.models import (
    EngineSfocCurve,
    Vessel,
    VesselHydrostatics,
)
from pelagos.registry.engine_sfoc_registry import (
    CurveApplicabilityScope,
    EngineSfocRegistry,
    InvalidLoadPercentError,
    SfocCurveNotFoundError,
    SfocLoadOutOfRangeError,
    SfocPermittedUse,
    SfocRestrictedUseError,
    VerificationStatus,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# Model Exceptions
# ==============================================================================

class VesselFuelModelError(Exception):
    """Base exception for vessel fuel consumption calculation errors."""
    pass


class HydrostaticsUnavailableError(VesselFuelModelError):
    """Raised when certified vessel hydrostatics or Admiralty benchmarks are unavailable."""
    pass


class InvalidDisplacementError(VesselFuelModelError):
    """Raised when displacement is non-positive or violates physical invariants (e.g. DWT substitution)."""
    pass


class InvalidSpeedError(VesselFuelModelError):
    """Raised when speed is non-positive or outside the vessel's physical operating envelope."""
    pass


class EngineOverloadError(VesselFuelModelError):
    """Raised when required propulsion power exceeds the engine's continuous rated-power boundary (> 100% MCR)."""
    pass


class EngineLoadUnderflowError(VesselFuelModelError):
    """Raised when required power drops below the minimum continuous operational load (< 50% MCR)."""
    pass


class FuelModelRestrictedError(VesselFuelModelError):
    """Raised when operational calculation is attempted with research-only or restricted inputs."""
    pass


# ==============================================================================
# Domain Dataclasses
# ==============================================================================

@dataclass
class PropulsionPowerEstimate:
    """Layer A: Estimated required main-engine brake power to maintain vessel speed through water."""
    speed_knots: float
    loading_condition: str
    displacement_mt: float
    calibrated_admiralty_coeff: float
    calm_water_power_kw: float
    weather_margin_pct: float
    total_propulsion_power_kw: float
    design_power_definition: str = "CSR_BRAKE_POWER"
    power_type: str = "ESTIMATED_ENGINE_BRAKE_POWER"
    power_units: str = "kW (Brake power delivered at engine flywheel coupling)"
    method_name: str = "Calibrated Admiralty Coefficient Method"
    formula: str = "P = (Delta^(2/3) * V^3) / C_adm * (1 + margin/100)"
    provenance_status: str = "RESEARCH_SENSITIVITY_PARAMETER"
    power_definition_governance: str = (
        "Shipyard benchmark power is Continuous Service Rating engine brake power (P_B,CSR). "
        "Calculated propulsion power (total_propulsion_power_kw) represents estimated required engine brake power "
        "at the engine output coupling. It is neither delivered shaft power (P_S) nor propeller power (P_D). "
        "The engine-load calculation directly compares required brake power against verified continuous rated brake power (MCR) "
        "without unverified shaft transmission efficiency assumptions. "
        "Investigation note on 85% CSR/MCR coincidence: All candidate vessel CSR values correspond to approximately 85% of MCR "
        "(IRIS BLISS: 8210/9660 ≈ 84.990%, NAVIOS VICTORY: 7080/8330 ≈ 84.994%, BERGE GALDHOPIGGEN: 5185/6100 = 85.000%). "
        "Because primary statutory registries do not publish CSR and secondary delivery sheets are commercial summaries, "
        "whether these values represent shipyard contract figures or nominal 85% MCR rule-of-thumb derivations is unverified. "
        "The relationship is strictly classified as UNVERIFIED_SECONDARY_RELATIONSHIP."
    )
    model_limitations: str = (
        "Single-point Admiralty calibration is a first-order research approximation assuming constant C_adm. "
        "It does NOT constitute an independent validation of the full speed-power curve across varying speeds or loading conditions."
    )
    weather_sensitivity_disclaimer: str = (
        "Weather margin is an explicit research sensitivity parameter (e.g. 0.0% calm water, 15.0% standard research allowance). "
        "It is NOT a validated dynamic weather correction derived from NOAA/Copernicus wind or wave telemetry. "
        "Physical distinction: Speed Through Water (STW) differs from Speed Over Ground (SOG). Ocean currents directly impart "
        "vector drift on SOG, while aerodynamic wind resistance and wave added resistance increase required shaft power at STW."
    )


@dataclass
class EngineLoadEstimate:
    """
    Layer B: Main engine operating load relative to rated continuous capacity.
    Operating above 100% MCR exceeds the continuous rated-power boundary of the engine;
    does not claim temporary operation above MCR is physically impossible.
    """
    propulsion_power_kw: float
    rated_power_kw: float
    power_rating_definition: str
    engine_load_percent: float
    is_overloaded: bool = False
    is_underloaded: bool = False
    load_boundary_status: str = "WITHIN_CONTINUOUS_RATED_BOUNDARY"
    min_supported_load_percent: float = 50.0
    max_supported_load_percent: float = 100.0
    overload_governance_note: str = (
        "Operation above 100% MCR exceeds the model's continuous rated-power boundary. The model strictly evaluates "
        "continuous commercial operation (<= 100% rated MCR) and does not evaluate operating regimes beyond continuous rated power."
    )


@dataclass
class MainEngineFuelEstimate:
    """Layer D: Main engine fuel consumption rate at resolved load and SFOC."""
    propulsion_power_kw: float
    engine_load_percent: float
    sfoc_g_per_kwh: float
    sfoc_curve_code: str
    applicability_scope: str
    fuel_rate_mt_per_hour: float
    fuel_rate_mt_per_day: float
    unit_conversion_proof: str = "P [kW] * SFOC [g/kWh] = g/h; g/h / 1,000,000 g/MT = MT/h; MT/h * 24 h/day = MT/day"


@dataclass
class AuxiliaryFuelEstimate:
    """Layer E: Auxiliary generator and boiler consumption research scenario."""
    sea_cruise_aux_load_kw: float
    sea_cruise_sfoc_g_kwh: float
    sea_auxiliary_rate_mt_per_day: float
    port_idle_rate_mt_per_day: float
    port_working_rate_mt_per_day: float
    is_geared: bool
    crane_count: int
    is_aggregate_fleet_scenario: bool = True
    scenario_name: str = "IMO_GHG4_FLEET_AVERAGE_SCENARIO"
    reference_source: str = "Fourth IMO GHG Study 2020 (MEPC 75/7/15), Annex 1, Table 23"
    provenance_status: str = "AGGREGATE_RESEARCH_SCENARIO"
    scenario_disclaimer: str = (
        "Aggregate fleet average scenario derived from Fourth IMO GHG Study 2020 (Table 23). "
        "Applying this statistic to an individual vessel represents a research scenario assumption, "
        "NOT an observed or verified vessel-specific telemetry attribute."
    )
    geared_crane_assumption_notes: str = (
        "300 kW additional auxiliary electrical load (approx 1.35 MT/day) for 4 electro-hydraulic deck cranes "
        "operating during cargo discharge. This operational increment is an engineering research scenario assumption, "
        "NOT a primary tabular distinction published in the Fourth IMO GHG Study."
    )


@dataclass
class VesselFuelBreakdown:
    """Layer F: Complete vessel fuel consumption intelligence breakdown."""
    vessel_identifier: str
    vessel_name: str
    imo: Optional[str]
    vessel_class: Optional[str]
    status: str  # OPERATIONAL, RESEARCH, UNAVAILABLE
    operational_availability: str  # PERMITTED, RESTRICTED, UNAVAILABLE, SFOC_UNAVAILABLE
    calculation_mode: str  # operational vs research
    speed_knots: float
    loading_condition: str
    power_estimate: Optional[PropulsionPowerEstimate] = None
    engine_load: Optional[EngineLoadEstimate] = None
    main_engine_estimate: Optional[MainEngineFuelEstimate] = None
    auxiliary_estimate: Optional[AuxiliaryFuelEstimate] = None
    main_engine_consumption_mt_per_day: Optional[float] = None
    auxiliary_scenario_consumption_mt_per_day: Optional[float] = None
    port_idle_scenario_mt_per_day: Optional[float] = None
    port_working_scenario_mt_per_day: Optional[float] = None
    combined_sea_research_consumption_mt_per_day: Optional[float] = None
    total_at_sea_consumption_mt_per_day: Optional[float] = None  # Backward-compatible alias
    total_port_idle_consumption_mt_per_day: Optional[float] = None
    total_port_working_consumption_mt_per_day: Optional[float] = None
    output_classification: str = "CONSTRUCTED_RESEARCH_SCENARIO"
    is_constructed_research_scenario: bool = True
    is_observed_vessel_telemetry: bool = False
    uncertainty_disclaimer: str = (
        "Combined sea consumption is a constructed research scenario combining calibrated main-engine Admiralty power "
        "with aggregate IMO fleet-average auxiliary scenario assumptions. It is NEVER observed, measured, verified, "
        "or actual vessel telemetry."
    )
    governance_details: Dict[str, Any] = field(default_factory=dict)
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert breakdown to serializable dictionary."""
        return asdict(self)


# ==============================================================================
# Vessel Fuel Consumption Model Implementation
# ==============================================================================

class VesselFuelModel:
    """
    Vessel-Level Fuel Consumption Estimation Engine.
    Implements multi-layer calculation pipeline adhering to naval architecture principles
    and fail-closed source governance.
    """

    def __init__(self, db: Optional[Session] = None):
        self.db = db
        self.sfoc_registry = EngineSfocRegistry(db) if db is not None else None

    # --------------------------------------------------------------------------
    # Layer A: Speed-to-Power
    # --------------------------------------------------------------------------
    @staticmethod
    def calculate_propulsion_power(
        displacement_mt: float,
        speed_knots: float,
        admiralty_coefficient: float,
        weather_margin_pct: float = 0.0,
        dwt_tonnage: Optional[float] = None,
        condition: str = "laden",
    ) -> PropulsionPowerEstimate:
        """
        Estimate required main-engine brake power using the Calibrated Admiralty Coefficient Method.

        Formula:
            P_calm = (Delta^(2/3) * V^3) / C_adm  [kW Brake Power]
            P_total = P_calm * (1 + weather_margin_pct / 100)  [kW Brake Power]

        Physical Definition:
            Because C_adm is calibrated against Continuous Service Rating engine brake power (P_B,CSR),
            the calculated power represents required engine brake power delivered at the flywheel coupling.
            It is neither delivered shaft power (P_S) nor thrust power (P_D). No unverified transmission
            efficiency conversion is applied.

        Constraints & Invariants:
        1. Delta > 0, V > 0, C_adm > 0.
        2. DWT is NEVER substituted for displacement. For laden condition, Delta must strictly
           exceed DWT (Delta = DWT + Lightweight).
        3. Speed must be within physically valid regime (0 < V <= 25.0 knots).
        4. Weather margin is an explicit research sensitivity parameter, NOT a dynamic hindcast.
        """
        if speed_knots <= 0.0:
            raise InvalidSpeedError(f"Speed must be strictly positive: {speed_knots} knots.")
        if speed_knots > 25.0:
            raise InvalidSpeedError(f"Speed {speed_knots} knots exceeds physical merchant bulk carrier envelope (max 25.0 kts).")

        if displacement_mt <= 0.0:
            raise InvalidDisplacementError(f"Displacement must be strictly positive: {displacement_mt} MT.")

        if admiralty_coefficient <= 0.0:
            raise HydrostaticsUnavailableError(f"Admiralty coefficient must be strictly positive: {admiralty_coefficient}.")

        if weather_margin_pct < 0.0 or weather_margin_pct > 100.0:
            raise ValueError(f"Weather margin percentage must be between 0.0 and 100.0%: {weather_margin_pct}.")

        # Invariant check: DWT is not displacement
        if condition.lower() == "laden" and dwt_tonnage is not None and dwt_tonnage > 0.0:
            if displacement_mt <= dwt_tonnage:
                raise InvalidDisplacementError(
                    f"Naval Architecture Invariant Violation: Laden displacement ({displacement_mt:.1f} MT) "
                    f"cannot be less than or equal to Summer Deadweight ({dwt_tonnage:.1f} MT). "
                    f"Displacement must equal DWT + Lightweight Tonnage (Delta = DWT + LDT). "
                    f"Silent substitution of DWT for displacement is strictly prohibited."
                )

        calm_power_kw = (math.pow(displacement_mt, 2.0 / 3.0) * math.pow(speed_knots, 3.0)) / admiralty_coefficient
        total_power_kw = calm_power_kw * (1.0 + (weather_margin_pct / 100.0))

        return PropulsionPowerEstimate(
            speed_knots=round(speed_knots, 2),
            loading_condition=condition.lower(),
            displacement_mt=round(displacement_mt, 1),
            calibrated_admiralty_coeff=round(admiralty_coefficient, 2),
            calm_water_power_kw=round(calm_power_kw, 2),
            weather_margin_pct=round(weather_margin_pct, 2),
            total_propulsion_power_kw=round(total_power_kw, 2),
            design_power_definition="CSR_BRAKE_POWER",
            method_name="Calibrated Admiralty Coefficient Method",
            provenance_status="RESEARCH_SENSITIVITY_PARAMETER",
        )

    # --------------------------------------------------------------------------
    # Layer B: Engine Load
    # --------------------------------------------------------------------------
    @staticmethod
    def calculate_engine_load(
        propulsion_power_kw: float,
        rated_power_kw: float,
        power_rating_definition: str = "MCR",
        min_load_percent: float = 50.0,
        max_load_percent: float = 100.0,
    ) -> EngineLoadEstimate:
        """
        Calculate main engine load percentage relative to rated continuous capacity.

        Formula:
            Load% = (P_req / P_rated) * 100

        Operating bounds:
            min_load_percent (typically 50.0%) to max_load_percent (100.0% MCR).
        """
        if propulsion_power_kw <= 0.0:
            raise ValueError(f"Propulsion power must be positive: {propulsion_power_kw} kW.")
        if rated_power_kw <= 0.0:
            raise ValueError(f"Engine rated power must be positive: {rated_power_kw} kW.")

        load_pct = (propulsion_power_kw / rated_power_kw) * 100.0

        is_over = load_pct > max_load_percent
        is_under = load_pct < min_load_percent

        if is_over:
            boundary_status = "CONTINUOUS_RATED_POWER_BOUNDARY_EXCEEDED"
        elif is_under:
            boundary_status = "BELOW_VERIFIED_SFOC_BOUNDARY"
        else:
            boundary_status = "WITHIN_CONTINUOUS_RATED_BOUNDARY"

        return EngineLoadEstimate(
            propulsion_power_kw=round(propulsion_power_kw, 2),
            rated_power_kw=round(rated_power_kw, 2),
            power_rating_definition=power_rating_definition.upper(),
            engine_load_percent=round(load_pct, 2),
            is_overloaded=is_over,
            is_underloaded=is_under,
            load_boundary_status=boundary_status,
            min_supported_load_percent=min_load_percent,
            max_supported_load_percent=max_load_percent,
        )

    # --------------------------------------------------------------------------
    # Layer D: Main Engine Fuel Rate
    # --------------------------------------------------------------------------
    @staticmethod
    def calculate_main_engine_fuel_rate(
        propulsion_power_kw: float,
        sfoc_g_kwh: float,
        curve_code: str = "BENCHMARK",
        applicability_scope: str = "EXACT_VARIANT",
    ) -> MainEngineFuelEstimate:
        """
        Calculate main engine fuel consumption rate from power and SFOC.

        Unit Conversion Proof:
            Power: [kW]
            SFOC:  [g / (kW * h)]
            P * SFOC = kW * (g / (kW * h)) = g / h
            1 Metric Tonne (MT) = 1,000,000 g
            Fuel rate (MT/h) = (P * SFOC) / 1,000,000
            Fuel rate (MT/day) = Fuel rate (MT/h) * 24.0
        """
        if propulsion_power_kw <= 0.0:
            raise ValueError(f"Propulsion power must be positive: {propulsion_power_kw} kW.")
        if sfoc_g_kwh <= 0.0:
            raise ValueError(f"SFOC must be positive: {sfoc_g_kwh} g/kWh.")

        rate_mt_per_hour = (propulsion_power_kw * sfoc_g_kwh) / 1_000_000.0
        rate_mt_per_day = rate_mt_per_hour * 24.0

        return MainEngineFuelEstimate(
            propulsion_power_kw=round(propulsion_power_kw, 2),
            engine_load_percent=0.0,  # Filled by caller
            sfoc_g_per_kwh=round(sfoc_g_kwh, 2),
            sfoc_curve_code=curve_code,
            applicability_scope=applicability_scope,
            fuel_rate_mt_per_hour=round(rate_mt_per_hour, 5),
            fuel_rate_mt_per_day=round(rate_mt_per_day, 3),
        )

    # --------------------------------------------------------------------------
    # Layer E: Auxiliary and Port Consumption (Aggregate Fleet Scenario)
    # --------------------------------------------------------------------------
    @staticmethod
    def estimate_auxiliary_consumption(
        is_geared: bool = False,
        crane_count: int = 0,
    ) -> AuxiliaryFuelEstimate:
        """
        Estimate auxiliary generator and boiler consumption research scenario based on Fourth IMO GHG Study 2020.

        Literature Reference Standards:
        - Sea Cruise Aux Electrical Load: ~350 kW average for Handysize/Panamax/Kamsarmax bulk carriers (Table 23).
        - Aux Engine SFOC: 220.0 g/kWh (IMO 4th GHG Study Table 23 reference for medium-speed aux diesel).
        - Sea Cruise Aux Daily Rate: (350 * 220 / 1e6) * 24 = 1.848 MT/day.
        - Port Idle Rate: 1.20 MT/day (hotel load + auxiliary boiler).
        - Port Working Rate:
            - Geared (cranes active): 3.20 MT/day (hotel + 4x cargo cranes working).
              Note: The 300 kW (approx 1.35 MT/day) crane working increment is an engineering research scenario,
              not a primary tabular category in the Fourth IMO GHG Study.
            - Gearless: 1.20 MT/day (shore cranes handle cargo; ship remains at idle load).
        """
        sea_load_kw = 350.0
        sea_sfoc = 220.0
        sea_rate_daily = (sea_load_kw * sea_sfoc / 1_000_000.0) * 24.0  # 1.848 MT/day
        port_idle_daily = 1.20

        if is_geared and crane_count > 0:
            port_working_daily = 3.20
        else:
            port_working_daily = 1.20

        return AuxiliaryFuelEstimate(
            sea_cruise_aux_load_kw=sea_load_kw,
            sea_cruise_sfoc_g_kwh=sea_sfoc,
            sea_auxiliary_rate_mt_per_day=round(sea_rate_daily, 3),
            port_idle_rate_mt_per_day=round(port_idle_daily, 3),
            port_working_rate_mt_per_day=round(port_working_daily, 3),
            is_geared=is_geared,
            crane_count=crane_count,
            is_aggregate_fleet_scenario=True,
            scenario_name="IMO_GHG4_FLEET_AVERAGE_SCENARIO",
            reference_source="Fourth IMO GHG Study 2020 (MEPC 75/7/15), Annex 1, Table 23",
            provenance_status="AGGREGATE_RESEARCH_SCENARIO",
        )

    # --------------------------------------------------------------------------
    # Layer F: Full Pipeline Orchestration
    # --------------------------------------------------------------------------
    def estimate_vessel_fuel_consumption(
        self,
        imo_or_mmsi: str,
        speed_knots: float,
        loading_condition: str = "laden",
        weather_margin_pct: float = 0.0,
        operational_mode: bool = True,
    ) -> VesselFuelBreakdown:
        """
        Estimate vessel fuel consumption with strict fail-closed operational governance.

        Execution Layers:
        1. Registry Lookup: Resolve Vessel and certified VesselHydrostatics.
        2. Governance Gate:
           - In operational_mode=True: Hydrostatics and SFOC curve MUST be OPERATIONAL_PERMITTED.
             Fails closed if restricted or research-only.
           - In operational_mode=False: Allows research-licensed sources, retaining provenance.
        3. Condition & Hydrostatics Gate:
           - Ballast condition requires verified ballast displacement and ballast Admiralty coefficient.
             Fails closed to UNAVAILABLE without arbitrary percentage assumptions.
           - Laden condition requires verified laden displacement and calibrated Admiralty coefficient.
        4. Layer A: Speed-to-Power via Calibrated Admiralty Coefficient.
        5. Layer B: Main engine load relative to rated MCR/CSR.
           - If load > 100% MCR: fails closed with ENGINE_OVERLOAD.
           - If load < curve min load: fails closed with SFOC_UNAVAILABLE.
        6. Layer C: Engine SFOC curve resolution and interpolation.
        7. Layer D: Main engine fuel consumption rate.
        8. Layer E: Auxiliary and port consumption research scenario.
        9. Layer F: Total consumption breakdown.
        """
        clean_key = str(imo_or_mmsi).strip()
        calc_mode = "operational" if operational_mode else "research"

        if self.db is None:
            return VesselFuelBreakdown(
                vessel_identifier=clean_key,
                vessel_name="UNKNOWN",
                imo=None,
                vessel_class=None,
                status="UNAVAILABLE",
                operational_availability="UNAVAILABLE",
                calculation_mode=calc_mode,
                speed_knots=speed_knots,
                loading_condition=loading_condition,
                message="Database session is required for vessel fuel estimation.",
            )

        # 1. Lookup Vessel
        vessel = (
            self.db.query(Vessel)
            .filter((Vessel.imo == clean_key) | (Vessel.mmsi == clean_key))
            .first()
        )
        if not vessel:
            return VesselFuelBreakdown(
                vessel_identifier=clean_key,
                vessel_name="UNKNOWN",
                imo=None,
                vessel_class=None,
                status="UNAVAILABLE",
                operational_availability="UNAVAILABLE",
                calculation_mode=calc_mode,
                speed_knots=speed_knots,
                loading_condition=loading_condition,
                message=f"Vessel '{clean_key}' not found in registry.",
            )

        # 2. Check Machinery Particulars
        if not vessel.engine_model or not vessel.rated_power_kw:
            return VesselFuelBreakdown(
                vessel_identifier=clean_key,
                vessel_name=vessel.vessel_name,
                imo=vessel.imo,
                vessel_class=vessel.vessel_class,
                status="UNAVAILABLE",
                operational_availability="UNAVAILABLE",
                calculation_mode=calc_mode,
                speed_knots=speed_knots,
                loading_condition=loading_condition,
                message=f"Machinery particulars UNAVAILABLE for '{vessel.vessel_name}' (IMO {vessel.imo}). Fail-closed (zero synthetic inference).",
            )

        # 3. Lookup Hydrostatics
        hydro = (
            self.db.query(VesselHydrostatics)
            .filter(VesselHydrostatics.vessel_id == vessel.id)
            .first()
        )
        if not hydro or hydro.verification_status == "UNAVAILABLE" or not hydro.calibrated_admiralty_coefficient:
            return VesselFuelBreakdown(
                vessel_identifier=clean_key,
                vessel_name=vessel.vessel_name,
                imo=vessel.imo,
                vessel_class=vessel.vessel_class,
                status="UNAVAILABLE",
                operational_availability="UNAVAILABLE",
                calculation_mode=calc_mode,
                speed_knots=speed_knots,
                loading_condition=loading_condition,
                message=f"Certified hydrostatics and Admiralty benchmarks UNAVAILABLE for '{vessel.vessel_name}'. Fail-closed.",
            )

        # 4. Check Operational Permission for Hydrostatics
        if operational_mode and hydro.permitted_use != "OPERATIONAL_PERMITTED":
            return VesselFuelBreakdown(
                vessel_identifier=clean_key,
                vessel_name=vessel.vessel_name,
                imo=vessel.imo,
                vessel_class=vessel.vessel_class,
                status="UNAVAILABLE",
                operational_availability="RESTRICTED",
                calculation_mode=calc_mode,
                speed_knots=speed_knots,
                loading_condition=loading_condition,
                governance_details={
                    "hydrostatics_permitted_use": hydro.permitted_use,
                    "hydrostatics_source": hydro.source_name,
                },
                message=(
                    f"Vessel hydrostatics for '{vessel.vessel_name}' are classified as {hydro.permitted_use}. "
                    f"Operational voyage predictions are restricted pending provider/yard permission. "
                    f"Set operational_mode=False for research evaluation."
                ),
            )

        # 5. Resolve Displacement & Admiralty Coefficient for Condition (Honest Condition Handling)
        cond_clean = loading_condition.strip().lower()
        if cond_clean == "ballast":
            if hydro.ballast_displacement_mt is None or hydro.ballast_admiralty_coefficient is None:
                return VesselFuelBreakdown(
                    vessel_identifier=clean_key,
                    vessel_name=vessel.vessel_name,
                    imo=vessel.imo,
                    vessel_class=vessel.vessel_class,
                    status="UNAVAILABLE",
                    operational_availability="UNAVAILABLE",
                    calculation_mode=calc_mode,
                    speed_knots=speed_knots,
                    loading_condition=loading_condition,
                    message=(
                        f"Hydrostatics and calibrated Admiralty coefficient for loading condition 'ballast' "
                        f"are UNAVAILABLE for '{vessel.vessel_name}'. "
                        f"Pelagos strictly prohibits arbitrary percentage scaling of displacement or "
                        f"silent substitution of laden coefficients for ballast conditions."
                    ),
                )
            displacement = hydro.ballast_displacement_mt
            admiralty_coeff = hydro.ballast_admiralty_coefficient
        elif cond_clean == "laden":
            if hydro.laden_displacement_mt is None or hydro.calibrated_admiralty_coefficient is None:
                return VesselFuelBreakdown(
                    vessel_identifier=clean_key,
                    vessel_name=vessel.vessel_name,
                    imo=vessel.imo,
                    vessel_class=vessel.vessel_class,
                    status="UNAVAILABLE",
                    operational_availability="UNAVAILABLE",
                    calculation_mode=calc_mode,
                    speed_knots=speed_knots,
                    loading_condition=loading_condition,
                    message=f"Laden displacement or calibrated Admiralty coefficient is UNAVAILABLE for '{vessel.vessel_name}'.",
                )
            displacement = hydro.laden_displacement_mt
            admiralty_coeff = hydro.calibrated_admiralty_coefficient
        else:
            return VesselFuelBreakdown(
                vessel_identifier=clean_key,
                vessel_name=vessel.vessel_name,
                imo=vessel.imo,
                vessel_class=vessel.vessel_class,
                status="UNAVAILABLE",
                operational_availability="UNAVAILABLE",
                calculation_mode=calc_mode,
                speed_knots=speed_knots,
                loading_condition=loading_condition,
                message=f"Unsupported loading condition '{loading_condition}'. Supported conditions are 'laden' and 'ballast'.",
            )

        # 6. Layer A: Speed-to-Power
        try:
            power_est = self.calculate_propulsion_power(
                displacement_mt=displacement,
                speed_knots=speed_knots,
                admiralty_coefficient=admiralty_coeff,
                weather_margin_pct=weather_margin_pct,
                dwt_tonnage=vessel.dwt_tonnage,
                condition=loading_condition,
            )
        except (InvalidSpeedError, InvalidDisplacementError, HydrostaticsUnavailableError) as e:
            return VesselFuelBreakdown(
                vessel_identifier=clean_key,
                vessel_name=vessel.vessel_name,
                imo=vessel.imo,
                vessel_class=vessel.vessel_class,
                status="UNAVAILABLE",
                operational_availability="UNAVAILABLE",
                calculation_mode=calc_mode,
                speed_knots=speed_knots,
                loading_condition=loading_condition,
                message=str(e),
            )

        # 7. Layer B: Engine Load
        rating_def = getattr(vessel, "power_rating_definition", "MCR") or "MCR"
        load_est = self.calculate_engine_load(
            propulsion_power_kw=power_est.total_propulsion_power_kw,
            rated_power_kw=vessel.rated_power_kw,
            power_rating_definition=rating_def,
            min_load_percent=50.0,
            max_load_percent=100.0,
        )

        if load_est.is_overloaded:
            return VesselFuelBreakdown(
                vessel_identifier=clean_key,
                vessel_name=vessel.vessel_name,
                imo=vessel.imo,
                vessel_class=vessel.vessel_class,
                status="UNAVAILABLE",
                operational_availability="UNAVAILABLE",
                calculation_mode=calc_mode,
                speed_knots=speed_knots,
                loading_condition=loading_condition,
                power_estimate=power_est,
                engine_load=load_est,
                output_classification="CONSTRUCTED_RESEARCH_SCENARIO",
                is_constructed_research_scenario=True,
                is_observed_vessel_telemetry=False,
                message=(
                    f"CONTINUOUS_RATED_POWER_BOUNDARY_EXCEEDED (ENGINE_OVERLOAD): Required engine brake power ({power_est.total_propulsion_power_kw:.1f} kW) "
                    f"exceeds verified engine continuous rated capacity ({vessel.rated_power_kw:.1f} kW {rating_def}, "
                    f"{load_est.engine_load_percent:.1f}% load). The vessel fuel model strictly evaluates continuous "
                    f"rated power (<= 100% MCR) and fails closed when the continuous rated-power boundary is exceeded."
                ),
            )

        # 8. Layer C: Resolve Engine SFOC Curve
        sfoc_res = self.sfoc_registry.resolve_curve_for_vessel(
            imo_or_mmsi=clean_key,
            operational_mode=operational_mode,
        )

        if sfoc_res["status"] == VerificationStatus.UNAVAILABLE or sfoc_res.get("curve_code") is None:
            return VesselFuelBreakdown(
                vessel_identifier=clean_key,
                vessel_name=vessel.vessel_name,
                imo=vessel.imo,
                vessel_class=vessel.vessel_class,
                status="UNAVAILABLE",
                operational_availability=sfoc_res.get("operational_availability", "UNAVAILABLE"),
                calculation_mode=calc_mode,
                speed_knots=speed_knots,
                loading_condition=loading_condition,
                power_estimate=power_est,
                engine_load=load_est,
                governance_details=sfoc_res,
                message=sfoc_res.get("message", "SFOC curve resolution failed."),
            )

        # Load Curve Object from DB
        curve_obj = (
            self.db.query(EngineSfocCurve)
            .filter(EngineSfocCurve.curve_code == sfoc_res["curve_code"])
            .first()
        )
        if not curve_obj:
            return VesselFuelBreakdown(
                vessel_identifier=clean_key,
                vessel_name=vessel.vessel_name,
                imo=vessel.imo,
                vessel_class=vessel.vessel_class,
                status="UNAVAILABLE",
                operational_availability="UNAVAILABLE",
                calculation_mode=calc_mode,
                speed_knots=speed_knots,
                loading_condition=loading_condition,
                power_estimate=power_est,
                engine_load=load_est,
                message=f"SFOC curve '{sfoc_res['curve_code']}' record not found.",
            )

        # Honest Engine Load & SFOC Range Check
        if load_est.engine_load_percent < curve_obj.min_load_percent:
            return VesselFuelBreakdown(
                vessel_identifier=clean_key,
                vessel_name=vessel.vessel_name,
                imo=vessel.imo,
                vessel_class=vessel.vessel_class,
                status="UNAVAILABLE",
                operational_availability="SFOC_UNAVAILABLE",
                calculation_mode=calc_mode,
                speed_knots=speed_knots,
                loading_condition=loading_condition,
                power_estimate=power_est,
                engine_load=load_est,
                governance_details={
                    "sfoc_curve_code": curve_obj.curve_code,
                    "curve_min_load_percent": curve_obj.min_load_percent,
                    "curve_max_load_percent": curve_obj.max_load_percent,
                    "calculated_engine_load_percent": load_est.engine_load_percent,
                },
                message=(
                    f"SFOC_UNAVAILABLE: Calculated engine load ({load_est.engine_load_percent:.1f}%) is below the "
                    f"minimum documented SFOC curve point ({curve_obj.min_load_percent:.1f}% {rating_def} for '{curve_obj.curve_code}'). "
                    f"While marine diesel engines can physically operate at low load (e.g., slow steaming, harbor maneuvering "
                    f"with auxiliary blowers active), the manufacturer documentation provides no verified SFOC load points "
                    f"below {curve_obj.min_load_percent:.1f}%. Zero-extrapolation governance prevents estimating fuel rate "
                    f"outside documented curve boundaries."
                ),
            )

        # Interpolate SFOC at load
        try:
            interp_res = self.sfoc_registry.interpolate_sfoc(
                curve_identifier=curve_obj,
                load_percent=load_est.engine_load_percent,
                operational_mode=operational_mode,
            )
        except (SfocRestrictedUseError, SfocLoadOutOfRangeError, InvalidLoadPercentError) as e:
            return VesselFuelBreakdown(
                vessel_identifier=clean_key,
                vessel_name=vessel.vessel_name,
                imo=vessel.imo,
                vessel_class=vessel.vessel_class,
                status="UNAVAILABLE",
                operational_availability="RESTRICTED" if isinstance(e, SfocRestrictedUseError) else "UNAVAILABLE",
                calculation_mode=calc_mode,
                speed_knots=speed_knots,
                loading_condition=loading_condition,
                power_estimate=power_est,
                engine_load=load_est,
                message=str(e),
            )

        # 9. Layer D: Main Engine Fuel Rate
        me_fuel = self.calculate_main_engine_fuel_rate(
            propulsion_power_kw=power_est.total_propulsion_power_kw,
            sfoc_g_kwh=interp_res.sfoc_g_kwh,
            curve_code=curve_obj.curve_code,
            applicability_scope=sfoc_res.get("applicability_scope", curve_obj.applicability_scope),
        )
        me_fuel.engine_load_percent = load_est.engine_load_percent

        # 10. Layer E: Auxiliary and Port Consumption (Aggregate Scenario)
        is_geared = bool(getattr(vessel, "is_geared", False))
        cranes = getattr(vessel, "crane_count", 0) or 0
        aux_fuel = self.estimate_auxiliary_consumption(
            is_geared=is_geared,
            crane_count=cranes,
        )

        # 11. Layer F: Total Breakdown & Uncertainty Disclaimers
        total_at_sea = round(me_fuel.fuel_rate_mt_per_day + aux_fuel.sea_auxiliary_rate_mt_per_day, 3)
        total_port_idle = aux_fuel.port_idle_rate_mt_per_day
        total_port_working = aux_fuel.port_working_rate_mt_per_day

        calc_status = "OPERATIONAL" if operational_mode else "RESEARCH"
        op_avail = "PERMITTED" if operational_mode else "RESEARCH_ONLY"

        return VesselFuelBreakdown(
            vessel_identifier=clean_key,
            vessel_name=vessel.vessel_name,
            imo=vessel.imo,
            vessel_class=vessel.vessel_class,
            status=calc_status,
            operational_availability=op_avail,
            calculation_mode=calc_mode,
            speed_knots=speed_knots,
            loading_condition=loading_condition,
            power_estimate=power_est,
            engine_load=load_est,
            main_engine_estimate=me_fuel,
            auxiliary_estimate=aux_fuel,
            main_engine_consumption_mt_per_day=me_fuel.fuel_rate_mt_per_day,
            auxiliary_scenario_consumption_mt_per_day=aux_fuel.sea_auxiliary_rate_mt_per_day,
            port_idle_scenario_mt_per_day=aux_fuel.port_idle_rate_mt_per_day,
            port_working_scenario_mt_per_day=aux_fuel.port_working_rate_mt_per_day,
            combined_sea_research_consumption_mt_per_day=total_at_sea,
            total_at_sea_consumption_mt_per_day=total_at_sea,
            total_port_idle_consumption_mt_per_day=total_port_idle,
            total_port_working_consumption_mt_per_day=total_port_working,
            output_classification="CONSTRUCTED_RESEARCH_SCENARIO",
            is_constructed_research_scenario=True,
            is_observed_vessel_telemetry=False,
            governance_details={
                "speed_to_power_method": "Calibrated Admiralty Coefficient Method",
                "admiralty_coefficient": admiralty_coeff,
                "hydrostatics_source": hydro.source_name,
                "hydrostatics_permitted_use": hydro.permitted_use,
                "hydrostatics_verification": hydro.verification_status,
                "loading_condition": loading_condition,
                "power_rating_definition": rating_def,
                "power_physical_definition": "ESTIMATED_ENGINE_BRAKE_POWER (flywheel coupling kW)",
                "engine_load_calculation_basis": "Direct comparison of required brake power (kW) against continuous rated brake power MCR (kW)",
                "design_power_definition": hydro.design_power_definition or "CSR_BRAKE_POWER",
                "engine_rated_power_kw": vessel.rated_power_kw,
                "sfoc_curve_code": curve_obj.curve_code,
                "sfoc_source_document": curve_obj.source_document,
                "sfoc_authenticity_status": curve_obj.authenticity_status,
                "sfoc_permitted_use": curve_obj.permitted_use,
                "sfoc_applicability_scope": sfoc_res.get("applicability_scope"),
                "is_interpolated": interp_res.is_interpolated,
                "bounding_points": [interp_res.lower_bound_point, interp_res.upper_bound_point],
                "auxiliary_scenario": aux_fuel.scenario_name,
                "output_classification": "CONSTRUCTED_RESEARCH_SCENARIO",
                "is_constructed_research_scenario": True,
                "is_observed_vessel_telemetry": False,
                "research_assumptions_and_limitations": (
                    "Main-engine consumption is derived from certified shipyard delivery CSR benchmark and manufacturer SFOC curve. "
                    "Single-point Admiralty calibration is a first-order research approximation, NOT an independent validation of the full speed-power curve. "
                    "Auxiliary power demand is an aggregate fleet-average scenario from Fourth IMO GHG Study 2020 (Table 23). "
                    "Weather margin is a research sensitivity parameter. Not for operational voyage execution."
                ),
            },
            message="Vessel fuel consumption calculation successful (constructed research scenario).",
        )


# ==============================================================================
# Hydrostatics Seed Loader Utility
# ==============================================================================

def seed_vessel_hydrostatics(
    session: Session,
    seed_path: Optional[Union[str, Path]] = None,
) -> Dict[str, int]:
    """
    Idempotently seed verified vessel hydrostatics and Admiralty benchmarks.

    Returns:
        Dict[str, int]: {"hydrostatics_created": int, "hydrostatics_updated": int}
    """
    if seed_path is None:
        seed_path = Path(__file__).resolve().parent.parent / "data" / "seed_vessel_hydrostatics.json"
    else:
        seed_path = Path(seed_path)

    if not seed_path.exists():
        raise FileNotFoundError(f"Vessel hydrostatics seed file not found: {seed_path}")

    with open(seed_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    created_cnt = 0
    updated_cnt = 0
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    for item in records:
        imo_val = str(item["imo"]).strip()
        vessel = session.query(Vessel).filter(Vessel.imo == imo_val).first()
        if not vessel:
            continue

        hydro = (
            session.query(VesselHydrostatics)
            .filter(VesselHydrostatics.vessel_id == vessel.id)
            .first()
        )

        if hydro is None:
            hydro = VesselHydrostatics(
                vessel_id=vessel.id,
                lightweight_tonnage_mt=item.get("lightweight_tonnage_mt"),
                laden_displacement_mt=item.get("laden_displacement_mt"),
                ballast_displacement_mt=item.get("ballast_displacement_mt"),
                design_speed_knots=item.get("design_speed_knots"),
                design_draft_meters=item.get("design_draft_meters"),
                design_power_kw=item.get("design_power_kw"),
                design_power_definition=item.get("design_power_definition", "CSR_BRAKE_POWER"),
                calibrated_admiralty_coefficient=item.get("calibrated_admiralty_coefficient"),
                ballast_admiralty_coefficient=item.get("ballast_admiralty_coefficient"),
                source_name=str(item.get("source_name", "Shipyard Specification")).strip(),
                source_record_id=item.get("source_record_id"),
                source_url=item.get("source_url"),
                retrieval_date=item.get("retrieval_date"),
                verification_status=str(item.get("verification_status", "SOURCE_REPORTED")).strip(),
                permitted_use=str(item.get("permitted_use", "RESEARCH_ONLY")).strip(),
                notes=item.get("notes"),
                created_at=now_utc,
            )
            session.add(hydro)
            created_cnt += 1
        else:
            hydro.lightweight_tonnage_mt = item.get("lightweight_tonnage_mt")
            hydro.laden_displacement_mt = item.get("laden_displacement_mt")
            hydro.ballast_displacement_mt = item.get("ballast_displacement_mt")
            hydro.design_speed_knots = item.get("design_speed_knots")
            hydro.design_draft_meters = item.get("design_draft_meters")
            hydro.design_power_kw = item.get("design_power_kw")
            hydro.design_power_definition = item.get("design_power_definition", hydro.design_power_definition or "CSR_BRAKE_POWER")
            hydro.calibrated_admiralty_coefficient = item.get("calibrated_admiralty_coefficient")
            hydro.ballast_admiralty_coefficient = item.get("ballast_admiralty_coefficient")
            hydro.source_name = str(item.get("source_name", hydro.source_name)).strip()
            hydro.source_record_id = item.get("source_record_id")
            hydro.source_url = item.get("source_url")
            hydro.retrieval_date = item.get("retrieval_date")
            hydro.verification_status = str(item.get("verification_status", hydro.verification_status)).strip()
            hydro.permitted_use = str(item.get("permitted_use", hydro.permitted_use)).strip()
            hydro.notes = item.get("notes")
            updated_cnt += 1

    session.commit()
    return {
        "hydrostatics_created": created_cnt,
        "hydrostatics_updated": updated_cnt,
    }
