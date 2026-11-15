"""Comprehensive tests for Stage F1.4.2.2b.1: SFOC Source Licensing & Evidence Closure.

Tests include:
- Separation of three independent dimensions (Authenticity, Technical Applicability, Permitted Use)
- Fail-closed operational safeguards: research-only and restricted curves rejected in operational_mode=True
- Full-fidelity evaluation in research/audit mode (operational_mode=False)
- Aggregate research data isolation: IMO Fourth GHG study kept separate and never matched as a manufacturer engine curve
- Exact manufacturer load-point preservation
- Controlled piecewise linear interpolation
- Strict zero-extrapolation enforcement
- Invalid load input rejection
- Unavailable vessel fail-closed behavior without synthetic guessing
- Architectural isolation: no speed-to-power, no TCE change, physical particulars untouched
"""

from __future__ import annotations

import math
from typing import Generator
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from pelagos.database import Base, ensure_schema_compatibility, init_db
from pelagos.models import (
    CurveApplicabilityScope,
    EngineSfocCurve,
    EngineSfocLoadPoint,
    SfocPermittedUse,
    SourceLicensingStatus,
    VerificationStatus,
    Vessel,
)
from pelagos.registry.engine_sfoc_registry import (
    EngineSfocError,
    EngineSfocRegistry,
    InvalidLoadPercentError,
    SfocCurveNotFoundError,
    SfocInterpolationResult,
    SfocLoadOutOfRangeError,
    SfocRestrictedUseError,
)
from pelagos.registry.vessel_registry import VesselRegistry


@pytest.fixture
def mem_db() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database initialized with Pelagos schema."""
    eng = create_engine("sqlite:///:memory:")
    init_db(eng)
    SessionLocal = sessionmaker(bind=eng)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 1. SFOC Curve Ingestion & Three-Dimensional Provenance Integrity
# ---------------------------------------------------------------------------

def test_sfoc_seed_ingestion_and_dimensional_integrity(mem_db: Session):
    """Verify loading seed SFOC curves and independent preservation of the 3 dimensions."""
    sfoc_reg = EngineSfocRegistry(mem_db)

    metrics = sfoc_reg.load_seed_curves()
    assert metrics["curves_created"] == 4  # 4 manufacturer curves
    assert metrics["curves_updated"] == 0
    assert metrics["load_points_created"] >= 18

    # WinGD 5RT-flex50D Tier II Standard
    wingd_std = sfoc_reg.get_curve("WINGD_RT_FLEX50D_TII_STD")
    assert wingd_std is not None
    # Dimension 1: Source Authenticity
    assert wingd_std.authenticity_status == VerificationStatus.DIRECTLY_VERIFIED
    assert "Section 1.03" in wingd_std.source_citation_details
    assert "Table 1-2" in wingd_std.source_citation_details
    # Dimension 2: Technical Applicability
    assert wingd_std.applicability_scope == CurveApplicabilityScope.EXACT_VARIANT
    assert wingd_std.engine_variant == "RT-flex50D Tier II"
    # Dimension 3: Permitted Use
    assert wingd_std.permitted_use == SfocPermittedUse.RESEARCH_ONLY
    assert wingd_std.is_aggregate_research is False

    # MAN B&W S60ME-C8.2 Tier II Nominal
    man_nom = sfoc_reg.get_curve("MAN_S60ME_C8_2_TII_NOMINAL")
    assert man_nom is not None
    # Dimension 1: Source Authenticity
    assert man_nom.authenticity_status == VerificationStatus.SOURCE_REPORTED
    assert "Section 2.09" in man_nom.source_citation_details
    assert "Diagram 2.09.01" in man_nom.source_citation_details
    # Dimension 2: Technical Applicability
    assert man_nom.applicability_scope == CurveApplicabilityScope.FAMILY_REFERENCE
    # Dimension 3: Permitted Use
    assert man_nom.permitted_use == SfocPermittedUse.RESTRICTED_PENDING_PERMISSION
    assert man_nom.is_aggregate_research is False

    # Test Idempotency
    idemp_metrics = sfoc_reg.load_seed_curves()
    assert idemp_metrics["curves_created"] == 0
    assert idemp_metrics["curves_updated"] == 4
    assert idemp_metrics["load_points_created"] == 0


# ---------------------------------------------------------------------------
# 2. Aggregate Research Dataset Isolation
# ---------------------------------------------------------------------------

def test_aggregate_research_data_isolation(mem_db: Session):
    """Verify that aggregate fleet research data (IMO GHG study) is kept isolated from manufacturer curves."""
    sfoc_reg = EngineSfocRegistry(mem_db)
    sfoc_reg.load_seed_curves()
    agg_metrics = sfoc_reg.load_aggregate_research_dataset()
    assert agg_metrics["aggregate_curves_created"] == 1

    # Aggregate curve is retrievable via dedicated research benchmark method
    imo_bench = sfoc_reg.get_aggregate_research_benchmark("IMO4_2STROKE_SSD_POST2010")
    assert imo_bench is not None
    assert imo_bench.authenticity_status == VerificationStatus.DIRECTLY_VERIFIED
    assert imo_bench.applicability_scope == CurveApplicabilityScope.AGGREGATE_RESEARCH
    assert imo_bench.permitted_use == SfocPermittedUse.AGGREGATE_RESEARCH_ONLY
    assert imo_bench.is_aggregate_research is True

    # Crucial Invariant: resolve_curve_for_engine MUST NEVER match an aggregate curve
    c_match, scope, _ = sfoc_reg.resolve_curve_for_engine(
        manufacturer="International Maritime Organization (IMO)",
        model="Generic 2-Stroke Slow Speed Diesel",
        operational_mode=False,
    )
    assert c_match is None
    assert scope == CurveApplicabilityScope.UNAVAILABLE


# ---------------------------------------------------------------------------
# 3. Operational Safeguard (Fail-Closed Default)
# ---------------------------------------------------------------------------

def test_operational_mode_safeguards_fail_closed(mem_db: Session):
    """Verify operational_mode=True fail-closed behavior for research-only and restricted curves."""
    v_reg = VesselRegistry(mem_db)
    v_reg.load_seed_file()

    sfoc_reg = EngineSfocRegistry(mem_db)
    sfoc_reg.load_seed_curves()

    # 1. Berge Galdhopiggen (WinGD RT-flex50D is RESEARCH_ONLY)
    # In operational mode, resolution must return UNAVAILABLE / RESTRICTED
    res_op = sfoc_reg.resolve_curve_for_vessel("9753284", operational_mode=True)
    assert res_op["status"] == VerificationStatus.UNAVAILABLE
    assert res_op["operational_availability"] == "RESTRICTED"
    assert res_op["permitted_use"] == SfocPermittedUse.RESEARCH_ONLY
    assert res_op["curve"] is None
    assert "restricted from operational" in res_op["message"]

    # In operational mode, interpolate_sfoc must raise SfocRestrictedUseError
    with pytest.raises(SfocRestrictedUseError) as exc_info:
        sfoc_reg.interpolate_sfoc("WINGD_RT_FLEX50D_TII_STD", 75.0, operational_mode=True)
    assert "Operational evaluation is restricted" in str(exc_info.value)
    assert "RESEARCH_ONLY" in str(exc_info.value)

    # 2. Iris Bliss (MAN S60ME-C is RESTRICTED_PENDING_PERMISSION)
    res_iris_op = sfoc_reg.resolve_curve_for_vessel("9766944", operational_mode=True)
    assert res_iris_op["status"] == VerificationStatus.UNAVAILABLE
    assert res_iris_op["operational_availability"] == "RESTRICTED"
    assert res_iris_op["permitted_use"] == SfocPermittedUse.RESTRICTED_PENDING_PERMISSION

    with pytest.raises(SfocRestrictedUseError):
        sfoc_reg.interpolate_sfoc("MAN_S60ME_C8_2_TII_NOMINAL", 75.0, operational_mode=True)


# ---------------------------------------------------------------------------
# 4. Research/Audit Mode Evaluation (operational_mode=False)
# ---------------------------------------------------------------------------

def test_research_mode_evaluation_with_full_provenance(mem_db: Session):
    """Verify research/audit mode enables legitimate research benchmarking with full provenance metadata."""
    v_reg = VesselRegistry(mem_db)
    v_reg.load_seed_file()

    sfoc_reg = EngineSfocRegistry(mem_db)
    sfoc_reg.load_seed_curves()

    # 1. Berge Galdhopiggen in research mode
    res_res = sfoc_reg.resolve_curve_for_vessel("9753284", operational_mode=False)
    assert res_res["status"] == VerificationStatus.DIRECTLY_VERIFIED
    assert res_res["operational_availability"] == "RESEARCH_ONLY"
    assert res_res["curve_code"] == "WINGD_RT_FLEX50D_TII_STD"
    assert res_res["authenticity_status"] == VerificationStatus.DIRECTLY_VERIFIED
    assert res_res["applicability_scope"] == CurveApplicabilityScope.EXACT_VARIANT
    assert res_res["permitted_use"] == SfocPermittedUse.RESEARCH_ONLY
    assert "Table 1-2" in res_res["source_citation_details"]

    # Exact load point evaluation in research mode
    interp_100 = sfoc_reg.interpolate_sfoc("WINGD_RT_FLEX50D_TII_STD", 100.0, operational_mode=False)
    assert interp_100.load_percent == 100.0
    assert interp_100.sfoc_g_kwh == 171.8
    assert interp_100.is_interpolated is False
    assert interp_100.is_guaranteed is True
    assert interp_100.operational_mode is False
    assert interp_100.authenticity_status == VerificationStatus.DIRECTLY_VERIFIED
    assert interp_100.applicability_scope == CurveApplicabilityScope.EXACT_VARIANT
    assert interp_100.permitted_use == SfocPermittedUse.RESEARCH_ONLY

    # Intermediate piecewise interpolation in research mode
    # Bracket: 65% (161.9 g/kWh) and 75% (162.2 g/kWh) -> Midpoint 70% = 162.05 g/kWh
    interp_70 = sfoc_reg.interpolate_sfoc("WINGD_RT_FLEX50D_TII_STD", 70.0, operational_mode=False)
    assert interp_70.load_percent == 70.0
    assert math.isclose(interp_70.sfoc_g_kwh, 162.05, abs_tol=1e-4)
    assert interp_70.is_interpolated is True
    assert interp_70.tolerance_pct == 6.0


# ---------------------------------------------------------------------------
# 5. Zero-Extrapolation Enforcement
# ---------------------------------------------------------------------------

def test_zero_extrapolation_enforcement(mem_db: Session):
    """Verify that loads outside documented manufacturer boundaries raise SfocLoadOutOfRangeError."""
    sfoc_reg = EngineSfocRegistry(mem_db)
    sfoc_reg.load_seed_curves()

    # Documented range for WINGD_RT_FLEX50D_TII_STD is [50.0%, 100.0%]
    # 1. Below minimum documented load (49.9%)
    with pytest.raises(SfocLoadOutOfRangeError) as exc_info:
        sfoc_reg.interpolate_sfoc("WINGD_RT_FLEX50D_TII_STD", 49.9, operational_mode=False)
    assert "below minimum documented load point 50.0%" in str(exc_info.value)
    assert "Extrapolation is strictly prohibited" in str(exc_info.value)

    # 2. Above maximum documented load (100.5%)
    with pytest.raises(SfocLoadOutOfRangeError) as exc_info:
        sfoc_reg.interpolate_sfoc("WINGD_RT_FLEX50D_TII_STD", 100.5, operational_mode=False)
    assert "above maximum documented load point 100.0%" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 6. Invalid Input Rejection
# ---------------------------------------------------------------------------

def test_invalid_load_inputs_rejected(mem_db: Session):
    """Verify non-positive, non-finite, and malformed inputs are strictly rejected."""
    sfoc_reg = EngineSfocRegistry(mem_db)
    sfoc_reg.load_seed_curves()

    with pytest.raises(InvalidLoadPercentError):
        sfoc_reg.interpolate_sfoc("WINGD_RT_FLEX50D_TII_STD", -10.0, operational_mode=False)

    with pytest.raises(InvalidLoadPercentError):
        sfoc_reg.interpolate_sfoc("WINGD_RT_FLEX50D_TII_STD", 0.0, operational_mode=False)

    with pytest.raises(InvalidLoadPercentError):
        sfoc_reg.interpolate_sfoc("WINGD_RT_FLEX50D_TII_STD", float("nan"), operational_mode=False)

    with pytest.raises(InvalidLoadPercentError):
        sfoc_reg.interpolate_sfoc("WINGD_RT_FLEX50D_TII_STD", float("inf"), operational_mode=False)

    with pytest.raises(SfocCurveNotFoundError):
        sfoc_reg.interpolate_sfoc("NON_EXISTENT_CURVE", 75.0, operational_mode=False)


# ---------------------------------------------------------------------------
# 7. Unavailable Vessel Fail-Closed Behavior
# ---------------------------------------------------------------------------

def test_vessel_resolution_fail_closed_for_unavailable_machinery(mem_db: Session):
    """Verify unindexed vessels fail closed in both operational and research modes."""
    v_reg = VesselRegistry(mem_db)
    v_reg.load_seed_file()

    sfoc_reg = EngineSfocRegistry(mem_db)
    sfoc_reg.load_seed_curves()

    for imo in ["9441295", "9406087", "9591301"]:  # EGRET BULKER, HADEEL, BULK UNITY
        # Operational mode: fail-closed UNAVAILABLE
        res_op = sfoc_reg.resolve_curve_for_vessel(imo, operational_mode=True)
        assert res_op["status"] == VerificationStatus.UNAVAILABLE
        assert res_op["curve"] is None

        # Research mode: also fail-closed UNAVAILABLE (no synthetic guessing permitted)
        res_res = sfoc_reg.resolve_curve_for_vessel(imo, operational_mode=False)
        assert res_res["status"] == VerificationStatus.UNAVAILABLE
        assert res_res["curve"] is None


# ---------------------------------------------------------------------------
# 8. Independence of the Three Governance Dimensions
# ---------------------------------------------------------------------------

def test_three_governance_dimensions_independence(mem_db: Session):
    """Verify that authenticity, technical applicability, and permitted use remain independent."""
    sfoc_reg = EngineSfocRegistry(mem_db)
    sfoc_reg.load_seed_curves()
    sfoc_reg.load_aggregate_research_dataset()

    wingd = sfoc_reg.get_curve("WINGD_RT_FLEX50D_TII_STD")
    man = sfoc_reg.get_curve("MAN_S60ME_C8_2_TII_NOMINAL")
    imo = sfoc_reg.get_aggregate_research_benchmark("IMO4_2STROKE_SSD_POST2010")

    # Invariant 1: Authenticity status does NOT imply operational licensing
    # WinGD and IMO are DIRECTLY_VERIFIED, yet neither is OPERATIONAL_PERMITTED
    assert wingd.authenticity_status == VerificationStatus.DIRECTLY_VERIFIED
    assert wingd.permitted_use == SfocPermittedUse.RESEARCH_ONLY

    assert imo.authenticity_status == VerificationStatus.DIRECTLY_VERIFIED
    assert imo.permitted_use == SfocPermittedUse.AGGREGATE_RESEARCH_ONLY

    # Invariant 2: Permitted use does NOT dictate technical applicability
    # Both WinGD (EXACT_VARIANT) and MAN (FAMILY_REFERENCE) are non-operational
    assert wingd.applicability_scope == CurveApplicabilityScope.EXACT_VARIANT
    assert man.applicability_scope == CurveApplicabilityScope.FAMILY_REFERENCE
    assert wingd.permitted_use != SfocPermittedUse.OPERATIONAL_PERMITTED
    assert man.permitted_use != SfocPermittedUse.OPERATIONAL_PERMITTED


# ---------------------------------------------------------------------------
# 9. Architectural Isolation
# ---------------------------------------------------------------------------

def test_architectural_isolation_main_engine_sfoc(mem_db: Session):
    """Verify that SFOC operations do NOT modify physical particulars or compute speed-to-power."""
    v_reg = VesselRegistry(mem_db)
    v_reg.load_seed_file()

    sfoc_reg = EngineSfocRegistry(mem_db)
    sfoc_reg.load_seed_curves()

    vessel = v_reg.resolve_canonical_vessel(imo="9753284")
    original_dwt = vessel.dwt_tonnage
    original_power = vessel.rated_power_kw

    # Evaluate SFOC in research mode
    res = sfoc_reg.interpolate_sfoc("WINGD_RT_FLEX50D_TII_STD", 85.0, operational_mode=False)
    assert res.sfoc_g_kwh > 0

    # Verify vessel record is untouched
    mem_db.refresh(vessel)
    assert vessel.dwt_tonnage == original_dwt
    assert vessel.rated_power_kw == original_power

    # Result strictly provides g/kWh; it DOES NOT provide fuel burn mt/day or speed-to-power
    assert hasattr(res, "sfoc_g_kwh")
    assert not hasattr(res, "fuel_burn_mt_per_day")
    assert not hasattr(res, "vessel_speed_knots")
