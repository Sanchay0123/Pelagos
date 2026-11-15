"""Unit and regression tests for Pelagos Vessel Fuel Consumption Intelligence Model (Stage F1.4.2.2c & F1.4.2.2c.1).

Verifies:
1. Admiralty coefficient calibration and CSR design-point benchmarking.
2. Honest laden vs ballast hydrostatics (no arbitrary percentage scaling, no silent fallback).
3. Weather margin research sensitivity parameter governance (STW vs SOG).
4. Honest engine load and SFOC range handling (overload, within-range, and SFOC_UNAVAILABLE below-range).
5. Auxiliary and port scenario governance (aggregate Fourth IMO GHG Study fleet scenarios).
6. Segregated output breakdown and uncertainty disclaimers.
7. Operational fail-closed safeguards and six-vessel candidate matrix.
"""

import math
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.database import Base, init_db
from pelagos.models import Vessel, VesselHydrostatics
from pelagos.optimization.vessel_fuel_model import (
    AuxiliaryFuelEstimate,
    EngineLoadEstimate,
    EngineLoadUnderflowError,
    EngineOverloadError,
    HydrostaticsUnavailableError,
    InvalidDisplacementError,
    InvalidSpeedError,
    MainEngineFuelEstimate,
    PropulsionPowerEstimate,
    VesselFuelBreakdown,
    VesselFuelModel,
    seed_vessel_hydrostatics,
)
from pelagos.registry.engine_sfoc_registry import (
    CurveApplicabilityScope,
    EngineSfocRegistry,
    SfocPermittedUse,
    VerificationStatus,
)
from pelagos.registry.vessel_registry import VesselRegistry


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def in_memory_db():
    """Create a pristine SQLite in-memory database with all governed schemas."""
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def seeded_db(in_memory_db):
    """Populate database with 6 seed vessels, SFOC curves, and hydrostatic benchmarks."""
    vessel_reg = VesselRegistry(in_memory_db)
    vessel_reg.load_seed_file()

    sfoc_reg = EngineSfocRegistry(in_memory_db)
    sfoc_reg.load_seed_curves()

    seed_vessel_hydrostatics(in_memory_db)
    return in_memory_db


# ==============================================================================
# 1. Admiralty Calibration & Speed-to-Power Tests
# ==============================================================================

class TestAdmiraltyCalibrationAudit:
    """Verifies Layer A hydrodynamic power estimation, CSR calibration, and physical invariants."""

    def test_admiralty_power_calculation_exact(self):
        """Verify calm-water power calculation adheres to Delta^(2/3) * V^3 / C_adm."""
        displacement = 95303.0  # MT
        speed = 14.2  # knots
        c_adm = 727.4

        estimate = VesselFuelModel.calculate_propulsion_power(
            displacement_mt=displacement,
            speed_knots=speed,
            admiralty_coefficient=c_adm,
            weather_margin_pct=0.0,
            dwt_tonnage=82103.0,
            condition="laden",
        )

        expected_calm = (math.pow(95303.0, 2.0 / 3.0) * math.pow(14.2, 3.0)) / 727.4
        assert math.isclose(estimate.calm_water_power_kw, expected_calm, rel_tol=1e-3)
        assert estimate.design_power_definition == "CSR_BRAKE_POWER"
        assert estimate.power_type == "ESTIMATED_ENGINE_BRAKE_POWER"
        assert "Continuous Service Rating engine brake power (P_B,CSR)" in estimate.power_definition_governance
        assert "UNVERIFIED_SECONDARY_RELATIONSHIP" in estimate.power_definition_governance
        assert "first-order research approximation" in estimate.model_limitations
        assert "NOT constitute an independent validation of the full speed-power curve" in estimate.model_limitations
        assert estimate.weather_margin_pct == 0.0
        assert math.isclose(estimate.total_propulsion_power_kw, expected_calm, rel_tol=1e-3)

    def test_weather_margin_research_sensitivity_governance(self):
        """Verify research weather margin is documented as sensitivity, not dynamic hindcast."""
        displacement = 90010.0
        speed = 12.0
        c_adm = 794.9
        margin = 15.0  # 15% standard research scenario

        estimate = VesselFuelModel.calculate_propulsion_power(
            displacement_mt=displacement,
            speed_knots=speed,
            admiralty_coefficient=c_adm,
            weather_margin_pct=margin,
        )

        expected_calm = (math.pow(90010.0, 2.0 / 3.0) * math.pow(12.0, 3.0)) / 794.9
        expected_total = expected_calm * 1.15
        assert math.isclose(estimate.calm_water_power_kw, expected_calm, rel_tol=1e-3)
        assert math.isclose(estimate.total_propulsion_power_kw, expected_total, rel_tol=1e-3)
        assert estimate.provenance_status == "RESEARCH_SENSITIVITY_PARAMETER"
        assert "NOT a validated dynamic weather correction" in estimate.weather_sensitivity_disclaimer
        assert "Speed Through Water (STW) differs from Speed Over Ground (SOG)" in estimate.weather_sensitivity_disclaimer

    def test_invariant_dwt_is_not_displacement_rejection(self):
        """Strict Invariant: Passing DWT <= displacement in laden condition must be rejected."""
        dwt = 82103.0
        with pytest.raises(InvalidDisplacementError) as exc_info:
            VesselFuelModel.calculate_propulsion_power(
                displacement_mt=dwt,  # Passing DWT as displacement
                speed_knots=14.0,
                admiralty_coefficient=700.0,
                dwt_tonnage=dwt,
                condition="laden",
            )
        assert "Naval Architecture Invariant Violation" in str(exc_info.value)
        assert "Silent substitution of DWT for displacement is strictly prohibited" in str(exc_info.value)

    def test_invalid_speed_and_displacement_rejections(self):
        """Zero or unphysical speeds and displacements raise explicit errors."""
        with pytest.raises(InvalidSpeedError):
            VesselFuelModel.calculate_propulsion_power(95000.0, 0.0, 700.0)
        with pytest.raises(InvalidSpeedError):
            VesselFuelModel.calculate_propulsion_power(95000.0, 30.0, 700.0)
        with pytest.raises(InvalidDisplacementError):
            VesselFuelModel.calculate_propulsion_power(0.0, 12.0, 700.0)
        with pytest.raises(HydrostaticsUnavailableError):
            VesselFuelModel.calculate_propulsion_power(95000.0, 12.0, 0.0)


# ==============================================================================
# 2. Laden and Ballast Hydrostatics Governance Tests
# ==============================================================================

class TestLadenAndBallastHydrostatics:
    """Verifies that laden and ballast hydrostatics are segregated without synthetic derivations."""

    def test_missing_ballast_hydrostatics_fails_closed(self, seeded_db):
        """
        Requesting condition='ballast' for IRIS BLISS must fail closed to UNAVAILABLE
        because ballast displacement and ballast Admiralty coefficient are unindexed.
        """
        model = VesselFuelModel(seeded_db)
        breakdown = model.estimate_vessel_fuel_consumption(
            imo_or_mmsi="9766944",
            speed_knots=12.0,
            loading_condition="ballast",
            operational_mode=False,
        )

        assert breakdown.status == "UNAVAILABLE"
        assert breakdown.operational_availability == "UNAVAILABLE"
        assert "loading condition 'ballast' are UNAVAILABLE" in breakdown.message
        assert "arbitrary percentage scaling" in breakdown.message
        assert breakdown.combined_sea_research_consumption_mt_per_day is None

    def test_no_silent_fallback_to_laden_coefficient_for_ballast(self, seeded_db):
        """Ensure a vessel with valid laden hydrostatics does not silently use them for ballast."""
        model = VesselFuelModel(seeded_db)
        for imo in ["9766944", "9713430", "9753284"]:
            res_ballast = model.estimate_vessel_fuel_consumption(
                imo_or_mmsi=imo,
                speed_knots=12.0,
                loading_condition="ballast",
                operational_mode=False,
            )
            assert res_ballast.status == "UNAVAILABLE"
            assert "ballast" in res_ballast.message.lower()

    def test_unsupported_loading_condition_rejection(self, seeded_db):
        """Passing an unrecognized loading condition returns UNAVAILABLE."""
        model = VesselFuelModel(seeded_db)
        breakdown = model.estimate_vessel_fuel_consumption(
            imo_or_mmsi="9766944",
            speed_knots=12.0,
            loading_condition="semi_laden_intermediate",
            operational_mode=False,
        )
        assert breakdown.status == "UNAVAILABLE"
        assert "Unsupported loading condition" in breakdown.message


# ==============================================================================
# 3. Engine Load and SFOC Range Handling Tests
# ==============================================================================

class TestEngineLoadAndSfocRangeHandling:
    """Verifies overload, within-range, and below-range SFOC handling."""

    def test_within_range_sfoc_request_succeeds(self, seeded_db):
        """At 12 knots, IRIS BLISS engine load is ~51.3% MCR, within documented [50%, 100%]."""
        model = VesselFuelModel(seeded_db)
        breakdown = model.estimate_vessel_fuel_consumption(
            imo_or_mmsi="9766944",
            speed_knots=12.0,
            loading_condition="laden",
            operational_mode=False,
        )
        assert breakdown.status == "RESEARCH"
        assert breakdown.engine_load is not None
        assert 50.0 <= breakdown.engine_load.engine_load_percent <= 55.0
        assert breakdown.main_engine_estimate is not None
        assert breakdown.main_engine_estimate.sfoc_g_per_kwh > 0.0

    def test_below_range_returns_sfoc_unavailable_without_claiming_impossible_operation(self, seeded_db):
        """
        At 8 knots, required power for IRIS BLISS is ~1,468 kW (~15.2% MCR), which is below
        the 50% minimum documented point of curve MAN_S60ME_C8_2_TII_NOMINAL.
        The model must return SFOC_UNAVAILABLE, documenting that while the engine can physically
        operate, the manufacturer provides no verified SFOC points below 50%.
        """
        model = VesselFuelModel(seeded_db)
        breakdown = model.estimate_vessel_fuel_consumption(
            imo_or_mmsi="9766944",
            speed_knots=8.0,
            loading_condition="laden",
            operational_mode=False,
        )

        assert breakdown.status == "UNAVAILABLE"
        assert breakdown.operational_availability == "SFOC_UNAVAILABLE"
        assert breakdown.power_estimate is not None
        assert breakdown.engine_load is not None
        assert breakdown.engine_load.engine_load_percent < 50.0
        assert "SFOC_UNAVAILABLE" in breakdown.message
        assert "can physically operate at low load" in breakdown.message
        assert "Zero-extrapolation governance prevents estimating fuel rate" in breakdown.message
        assert breakdown.main_engine_estimate is None

    def test_above_range_retains_engine_overload_failure(self, seeded_db):
        """
        At 18 knots, BERGE GALDHOPIGGEN requires ~11,020 kW, which exceeds its 6,100 kW MCR (180.7% load).
        The model must retain an explicit continuous rated-power boundary failure without claiming overload
        is physically impossible.
        """
        model = VesselFuelModel(seeded_db)
        breakdown = model.estimate_vessel_fuel_consumption(
            imo_or_mmsi="9753284",
            speed_knots=18.0,
            loading_condition="laden",
            operational_mode=False,
        )

        assert breakdown.status == "UNAVAILABLE"
        assert breakdown.engine_load is not None
        assert breakdown.engine_load.is_overloaded
        assert breakdown.engine_load.load_boundary_status == "CONTINUOUS_RATED_POWER_BOUNDARY_EXCEEDED"
        assert "CONTINUOUS_RATED_POWER_BOUNDARY_EXCEEDED" in breakdown.message
        assert "ENGINE_OVERLOAD" in breakdown.message
        assert "exceeds verified engine continuous rated capacity" in breakdown.message
        assert "continuous rated-power boundary" in breakdown.message

    def test_engine_load_denominator_uses_verified_rating_definition(self, seeded_db):
        """Verify denominator uses rated_power_kw and power_rating_definition correctly."""
        vessel = seeded_db.query(Vessel).filter(Vessel.imo == "9766944").first()
        assert vessel.rated_power_kw == 9660.0
        load_est = VesselFuelModel.calculate_engine_load(
            propulsion_power_kw=4830.0,
            rated_power_kw=vessel.rated_power_kw,
            power_rating_definition="MCR",
        )
        assert load_est.engine_load_percent == 50.0
        assert load_est.power_rating_definition == "MCR"


# ==============================================================================
# 4. Auxiliary and Port Scenario Governance Tests
# ==============================================================================

class TestAuxiliaryScenarioGovernance:
    """Verifies that auxiliary and port rates are classified as aggregate fleet scenarios."""

    def test_auxiliary_estimate_metadata_and_provenance(self):
        """Verify scenario naming, aggregate disclaimer, and primary source citation."""
        aux = VesselFuelModel.estimate_auxiliary_consumption(is_geared=False, crane_count=0)
        assert aux.is_aggregate_fleet_scenario is True
        assert aux.scenario_name == "IMO_GHG4_FLEET_AVERAGE_SCENARIO"
        assert aux.provenance_status == "AGGREGATE_RESEARCH_SCENARIO"
        assert "Fourth IMO GHG Study 2020 (MEPC 75/7/15), Annex 1, Table 23" in aux.reference_source
        assert "Aggregate fleet average scenario" in aux.scenario_disclaimer
        assert "NOT an observed or verified vessel-specific telemetry attribute" in aux.scenario_disclaimer

    def test_geared_deck_crane_scenario_distinction(self):
        """Verify geared crane increment is documented as an engineering research scenario."""
        aux_geared = VesselFuelModel.estimate_auxiliary_consumption(is_geared=True, crane_count=4)
        assert aux_geared.port_working_rate_mt_per_day == 3.20
        assert "300 kW additional auxiliary electrical load" in aux_geared.geared_crane_assumption_notes
        assert "NOT a primary tabular distinction published in the Fourth IMO GHG Study" in aux_geared.geared_crane_assumption_notes


# ==============================================================================
# 5. Output Breakdown & Uncertainty Semantics Tests
# ==============================================================================

class TestOutputBreakdownAndUncertaintySemantics:
    """Verifies that breakdown outputs preserve separated components and disclaimers."""

    def test_breakdown_component_separation_and_disclaimer(self, seeded_db):
        """Verify that breakdown fields remain cleanly separated and disclaim observed status."""
        model = VesselFuelModel(seeded_db)
        breakdown = model.estimate_vessel_fuel_consumption(
            imo_or_mmsi="9766944",
            speed_knots=12.0,
            loading_condition="laden",
            operational_mode=False,
        )

        assert breakdown.status == "RESEARCH"
        assert breakdown.output_classification == "CONSTRUCTED_RESEARCH_SCENARIO"
        assert breakdown.is_constructed_research_scenario is True
        assert breakdown.is_observed_vessel_telemetry is False
        assert breakdown.main_engine_consumption_mt_per_day is not None
        assert breakdown.auxiliary_scenario_consumption_mt_per_day == 1.848
        assert breakdown.port_idle_scenario_mt_per_day == 1.20
        assert breakdown.port_working_scenario_mt_per_day == 1.20  # Gearless Kamsarmax
        assert breakdown.combined_sea_research_consumption_mt_per_day is not None
        assert "NEVER observed, measured, verified, or actual vessel telemetry" in breakdown.uncertainty_disclaimer
        assert "speed_to_power_method" in breakdown.governance_details
        assert "admiralty_coefficient" in breakdown.governance_details
        assert "sfoc_curve_code" in breakdown.governance_details
        assert breakdown.governance_details["output_classification"] == "CONSTRUCTED_RESEARCH_SCENARIO"
        assert breakdown.governance_details["design_power_definition"] == "CSR_BRAKE_POWER"
        assert "Single-point Admiralty calibration is a first-order research approximation" in breakdown.governance_details["research_assumptions_and_limitations"]

    def test_hydrostatics_csr_power_definition_and_unverified_sea_margin(self, seeded_db):
        """Verify seeded hydrostatics explicitly define CSR_BRAKE_POWER and document unverified 15% sea margin."""
        for imo in ["9766944", "9713430", "9753284"]:
            vessel = seeded_db.query(Vessel).filter(Vessel.imo == imo).first()
            hydro = seeded_db.query(VesselHydrostatics).filter(VesselHydrostatics.vessel_id == vessel.id).first()
            assert hydro is not None
            assert hydro.design_power_definition == "CSR_BRAKE_POWER"
            assert "UNVERIFIED_SECONDARY_RELATIONSHIP" in hydro.notes
            assert "Continuous Service Rating engine brake power (P_B,CSR)" in hydro.notes
            assert "CSR Provenance and Derivation Investigation" in hydro.notes
            assert any(pct in hydro.notes for pct in ["84.990%", "84.994%", "85.000%", "85%"])
            assert "first-order research" in hydro.notes

    def test_overload_boundary_status_semantics(self):
        """Verify load_boundary_status classifies normal, overload, and underload properly."""
        normal = VesselFuelModel.calculate_engine_load(5000.0, 10000.0)
        assert normal.load_boundary_status == "WITHIN_CONTINUOUS_RATED_BOUNDARY"
        assert not normal.is_overloaded

        over = VesselFuelModel.calculate_engine_load(10500.0, 10000.0)
        assert over.load_boundary_status == "CONTINUOUS_RATED_POWER_BOUNDARY_EXCEEDED"
        assert over.is_overloaded
        assert "exceeds the model's continuous rated-power boundary" in over.overload_governance_note

        under = VesselFuelModel.calculate_engine_load(4000.0, 10000.0)
        assert under.load_boundary_status == "BELOW_VERIFIED_SFOC_BOUNDARY"
        assert under.is_underloaded



# ==============================================================================
# 6. Operational vs Research Mode Governance Tests
# ==============================================================================

class TestOperationalVsResearchModeGovernance:
    """Verifies fail-closed operational mode and research mode candidate evaluation."""

    def test_operational_mode_fails_closed_for_restricted_hydrostatics(self, seeded_db):
        """In operational_mode=True, hydrostatics with RESTRICTED_PENDING_PERMISSION fail closed."""
        model = VesselFuelModel(seeded_db)
        breakdown = model.estimate_vessel_fuel_consumption(
            imo_or_mmsi="9766944",
            speed_knots=12.0,
            loading_condition="laden",
            operational_mode=True,
        )
        assert breakdown.status == "UNAVAILABLE"
        assert breakdown.operational_availability == "RESTRICTED"
        assert "restricted pending provider/yard permission" in breakdown.message

    def test_research_mode_succeeds_for_three_candidate_vessels(self, seeded_db):
        """IRIS BLISS, NAVIOS VICTORY, and BERGE GALDHOPIGGEN succeed with research status."""
        model = VesselFuelModel(seeded_db)
        for imo, expected_curve in [
            ("9766944", "MAN_S60ME_C8_2_TII_NOMINAL"),
            ("9713430", "MAN_S60ME_C8_2_TII_NOMINAL"),
            ("9753284", "WINGD_RT_FLEX50D_TII_STD"),
        ]:
            breakdown = model.estimate_vessel_fuel_consumption(
                imo_or_mmsi=imo,
                speed_knots=12.0,
                loading_condition="laden",
                operational_mode=False,
            )
            assert breakdown.status == "RESEARCH"
            assert breakdown.operational_availability == "RESEARCH_ONLY"
            assert breakdown.governance_details["sfoc_curve_code"] == expected_curve
            assert breakdown.combined_sea_research_consumption_mt_per_day > 0.0

    def test_unindexed_vessels_fail_closed_in_both_modes(self, seeded_db):
        """EGRET BULKER, HADEEL, BULK UNITY fail closed in both operational and research modes."""
        model = VesselFuelModel(seeded_db)
        for imo in ["9441295", "9406087", "9591301"]:
            res_op = model.estimate_vessel_fuel_consumption(imo, 12.0, operational_mode=True)
            assert res_op.status == "UNAVAILABLE"
            res_res = model.estimate_vessel_fuel_consumption(imo, 12.0, operational_mode=False)
            assert res_res.status == "UNAVAILABLE"
