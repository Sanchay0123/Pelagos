"""tests/test_p2a_commercial_fixture.py
Phase 2-A: Commercial Fixture Validation Engine Tests
Verification 1: Commercial Correctness Gate
"""

import datetime
from decimal import Decimal
from unittest.mock import MagicMock
import pytest

from pelagos.readiness.fixture_validation import (
    CommercialFixtureValidationEngine,
    CommercialFixturePayload,
    CommissionGovernanceTier,
    DurationStatus,
    FixtureLineItemInput,
    VoyageDurationInput,
    ValidatedFixtureResult,
)
from pelagos.models import Vessel


@pytest.fixture
def base_duration():
    return VoyageDurationInput(
        laden_sea_days=15.0,
        ballast_sea_days=12.0,
        loading_days=2.5,
        discharge_days=2.5,
        port_waiting_days=1.0,
        canal_waiting_days=0.5,
        canal_transit_days=0.5,
        duration_status=DurationStatus.CONTRACTUAL_AGREED,
        contractual_laytime_days=5.0,
        demurrage_rate_usd_per_day=25000.0,
        despatch_rate_usd_per_day=12500.0,
    )


@pytest.fixture
def valid_payload(base_duration):
    return CommercialFixturePayload(
        fixture_id="FIX-2026-001",
        vessel_imo=9593256,  # Valid IMO check digit: (9*7 + 5*6 + 9*5 + 3*4 + 2*3 + 5*2) = 166 -> last digit 6
        vessel_name="PACIFIC VOYAGER",
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="NLRTM",
        laycan_start=datetime.date(2024, 4, 1),
        laycan_end=datetime.date(2024, 4, 5),
        transit_date=datetime.date(2024, 4, 10),
        cargo_tonnes=75000.0,
        cargo_commodity="Bulk Iron Ore",
        total_port_allowance_usd=65000.0,
        total_canal_allowance_usd=0.0,
        commission_tier=CommissionGovernanceTier.CONTRACT_VERIFIED,
        contractual_commission_rate=0.025,
        commission_contract_id="CP-2026-001-SGSIN",
        duration_input=base_duration,
        source_organization="Clarksons Research",
        broker_reference="BRK-9921",
        provenance_classification="DIRECTLY_VERIFIED",
    )


def test_imo_syntax_and_authoritative_identity_separation(valid_payload):
    """Test separation of IMO syntax validation vs authoritative registry lookup."""
    engine = CommercialFixtureValidationEngine(db=None)

    # 1. Valid IMO syntax with no DB -> SYNTAX_VALID, IDENTITY_UNMATCHED
    res_no_db = engine.validate_vessel(vessel_imo=9593256, vessel_name="PACIFIC VOYAGER")
    assert res_no_db["syntax_status"] == "SYNTAX_VALID"
    assert res_no_db["identity_status"] == "IDENTITY_UNMATCHED"
    assert res_no_db["is_verified"] is False

    # 2. Invalid IMO syntax (check digit fail) -> SYNTAX_INVALID, IDENTITY_REJECTED
    res_bad_imo = engine.validate_vessel(vessel_imo=9593257, vessel_name="PACIFIC VOYAGER")
    assert res_bad_imo["syntax_status"] == "SYNTAX_INVALID"
    assert res_bad_imo["identity_status"] == "IDENTITY_REJECTED"
    assert res_bad_imo["is_verified"] is False
    assert "Invalid IMO check digit" in res_bad_imo["error"]

    # 3. Valid IMO with mocked DB match -> SYNTAX_VALID, IDENTITY_VERIFIED
    mock_db = MagicMock()
    mock_vessel = Vessel(
        imo=9593256,
        vessel_name="PACIFIC VOYAGER",
        vessel_class="Capesize",
        dwt_tonnage=Decimal("180000.00"),
        gross_tonnage=Decimal("95000.00"),
    )
    mock_db.query.return_value.filter.return_value.first.return_value = mock_vessel

    engine_with_db = CommercialFixtureValidationEngine(db=mock_db)
    res_matched = engine_with_db.validate_vessel(vessel_imo=9593256, vessel_name="PACIFIC VOYAGER")
    assert res_matched["syntax_status"] == "SYNTAX_VALID"
    assert res_matched["identity_status"] == "IDENTITY_VERIFIED"
    assert res_matched["is_verified"] is True
    assert res_matched["matched_vessel_name"] == "PACIFIC VOYAGER"

    # 4. Name mismatch with registry
    res_mismatch = engine_with_db.validate_vessel(vessel_imo=9593256, vessel_name="ATLANTIC VOYAGER")
    assert res_mismatch["syntax_status"] == "SYNTAX_VALID"
    assert res_mismatch["identity_status"] == "IDENTITY_NAME_MISMATCH"
    assert res_mismatch["is_verified"] is False
    assert res_mismatch["name_mismatch"] is True


def test_statutory_floor_enforcement_and_unallocated_overage(valid_payload):
    """Test one-way statutory floor enforcement and unallocated overage quarantine."""
    mock_disb_service = MagicMock()
    mock_disb_service.evaluate_voyage_disbursements.return_value.total_disbursement_usd = 60000.0
    mock_disb_service.evaluate_voyage_disbursements.return_value.has_partial_components = False

    engine = CommercialFixtureValidationEngine(disbursement_service=mock_disb_service)

    # 1. Sub-floor valuation ($45,000 allowance vs $60,000 statutory floor)
    subfloor_payload = valid_payload.model_copy(
        update={"total_port_allowance_usd": 45000.0, "total_canal_allowance_usd": 0.0}
    )
    res_subfloor = engine.validate_fixture(subfloor_payload)
    assert res_subfloor.is_valid is False
    assert res_subfloor.is_operational_permitted is False
    assert any("FIXTURE_VALUATION_BELOW_STATUTORY_FLOOR" in err for err in res_subfloor.validation_errors)

    # 2. Exact match ($60,000 allowance vs $60,000 statutory floor)
    exact_payload = valid_payload.model_copy(
        update={"total_port_allowance_usd": 60000.0, "total_canal_allowance_usd": 0.0}
    )
    res_exact = engine.validate_fixture(exact_payload)
    assert res_exact.is_valid is True
    assert res_exact.statutory_floor_usd == 60000.0
    assert res_exact.total_fixture_usd == 60000.0
    assert res_exact.unallocated_overage_usd == 0.0

    # 3. Above floor without itemized breakdown ($85,000 vs $60,000 floor -> $25,000 unallocated overage)
    overage_payload = valid_payload.model_copy(
        update={"total_port_allowance_usd": 85000.0, "total_canal_allowance_usd": 0.0}
    )
    res_overage = engine.validate_fixture(overage_payload)
    assert res_overage.is_valid is True
    assert res_overage.unallocated_overage_usd == 25000.0
    assert res_overage.status == "QUARANTINED_UNALLOCATED_OVERAGE"
    assert res_overage.is_operational_permitted is False
    assert any("UNALLOCATED_COMMERCIAL_OVERAGE" in w for w in res_overage.warnings)


def test_three_tier_commission_governance(valid_payload):
    """Test three-tier commission governance behavior without implicit fallbacks."""
    engine = CommercialFixtureValidationEngine()

    # 1. Tier 1: Contractual Verified
    res_t1 = engine.validate_fixture(valid_payload)
    assert res_t1.commission_summary["tier"] == CommissionGovernanceTier.CONTRACT_VERIFIED.value
    assert res_t1.commission_summary["rate"] == 0.025
    assert res_t1.commission_summary["is_operational_permitted"] is True

    # Tier 1 missing contract_id
    bad_t1 = valid_payload.model_copy(update={"commission_contract_id": None})
    res_bad_t1 = engine.validate_fixture(bad_t1)
    assert res_bad_t1.is_valid is False
    assert any("MISSING_COMMISSION_CONTRACT_ID" in err for err in res_bad_t1.validation_errors)

    # 2. Tier 2: User Research Assumption
    t2_payload = valid_payload.model_copy(
        update={
            "commission_tier": CommissionGovernanceTier.USER_RESEARCH_ASSUMPTION,
            "contractual_commission_rate": None,
            "commission_contract_id": None,
            "user_research_commission_rate": 0.0375,
        }
    )
    res_t2 = engine.validate_fixture(t2_payload)
    assert res_t2.is_valid is True
    assert res_t2.commission_summary["tier"] == CommissionGovernanceTier.USER_RESEARCH_ASSUMPTION.value
    assert res_t2.commission_summary["rate"] == 0.0375
    assert res_t2.commission_summary["is_operational_permitted"] is False
    assert any("RESEARCH ASSUMPTION" in d for d in res_t2.disclaimers)

    # Tier 2 in operational mode fails closed
    res_t2_op = engine.validate_fixture(t2_payload, operational_mode=True)
    assert res_t2_op.is_valid is False
    assert any("RESEARCH_COMMISSION_BLOCKED" in err for err in res_t2_op.validation_errors)

    # 3. Tier 3: Missing Commission Rate
    t3_payload = valid_payload.model_copy(
        update={
            "commission_tier": CommissionGovernanceTier.MISSING,
            "contractual_commission_rate": None,
            "commission_contract_id": None,
            "user_research_commission_rate": None,
        }
    )
    res_t3 = engine.validate_fixture(t3_payload)
    assert res_t3.is_valid is True
    assert res_t3.commission_summary["rate"] == 0.0
    assert res_t3.commission_summary["is_operational_permitted"] is False
    assert any("COMMISSION_UNSPECIFIED" in w for w in res_t3.warnings)

    # Tier 3 in operational mode fails closed
    res_t3_op = engine.validate_fixture(t3_payload, operational_mode=True)
    assert res_t3_op.is_valid is False
    assert any("MISSING_COMMERCIAL_COMMISSION_CONTRACT" in err for err in res_t3_op.validation_errors)


def test_voyage_duration_seven_components(valid_payload, base_duration):
    """Test seven-component duration accounting and arithmetic consistency."""
    engine = CommercialFixtureValidationEngine()

    # Sum of components = 15 + 12 + 2.5 + 2.5 + 1.0 + 0.5 + 0.5 = 34.0 days
    assert base_duration.total_voyage_days == 34.0

    res = engine.validate_fixture(valid_payload)
    assert res.duration_summary["total_voyage_days"] == 34.0
    assert res.duration_summary["laden_sea_days"] == 15.0
    assert res.duration_summary["ballast_sea_days"] == 12.0
    assert res.duration_summary["loading_days"] == 2.5
    assert res.duration_summary["discharge_days"] == 2.5
    assert res.duration_summary["port_waiting_days"] == 1.0
    assert res.duration_summary["canal_waiting_days"] == 0.5
    assert res.duration_summary["canal_transit_days"] == 0.5

    # Estimated routed duration is blocked in operational mode
    est_dur = base_duration.model_copy(update={"duration_status": DurationStatus.ESTIMATED_ROUTED})
    est_payload = valid_payload.model_copy(update={"duration_input": est_dur})
    res_est_op = engine.validate_fixture(est_payload, operational_mode=True)
    assert res_est_op.is_valid is False
    assert any("ESTIMATED_DURATION_BLOCKED" in err for err in res_est_op.validation_errors)


def test_tier3_contract_attribution_check(valid_payload):
    """Verify that Tier 3 contractual items lacking bilateral attribution are rejected."""
    engine = CommercialFixtureValidationEngine()

    line_item_missing_attr = FixtureLineItemInput(
        description="Private Terminal Mooring",
        charge_category="TERMINAL_CONTRACT",
        amount_usd=12000.0,
        applicability_type="CONTRACTUAL",
        contract_attribution=None,  # Missing contract attribution
    )

    payload = valid_payload.model_copy(
        update={
            "itemized_charges": [line_item_missing_attr],
            "total_port_allowance_usd": 12000.0,
        }
    )

    res = engine.validate_fixture(payload)
    assert res.is_valid is False
    assert any("MISSING_CONTRACTUAL_EVIDENCE" in err for err in res.validation_errors)


def test_vessel_readiness_integration_suez_missing_scnt(valid_payload):
    """
    P2-001 Remediation: Verify that evaluate_readiness() is invoked during fixture validation
    and fails closed if required statutory canal transit certificates (e.g. SCNT for Suez) are absent.
    """
    engine = CommercialFixtureValidationEngine(db=None)

    # Fixture includes Suez Canal transit but vessel has no certified SCNT
    suez_payload = valid_payload.model_copy(
        update={
            "canal_transits": ["SUEZ"],
            "total_canal_allowance_usd": 250000.0,
        }
    )

    res = engine.validate_fixture(suez_payload)
    assert res.is_valid is False
    assert res.is_operational_permitted is False
    assert any("MISSING_CERTIFIED_SCNT" in err for err in res.validation_errors)
    assert res.vessel_readiness_summary is not None
    assert "scnt" in res.vessel_readiness_summary["missing_particulars"]


def test_vessel_readiness_integration_suez_with_certified_vessel(valid_payload):
    """
    P2-001 Remediation: Verify that fixture validation succeeds when vessel readiness service
    confirms certified statutory canal transit certificates.
    """
    from pelagos.readiness.vessel_readiness import VesselReadinessAssessment

    mock_vessel_service = MagicMock()
    mock_assessment = VesselReadinessAssessment(
        is_ready_for_research=True,
        is_ready_for_operations=False,
        vessel_imo="9593256",
        vessel_name="PACIFIC VOYAGER",
        vessel_class="Capesize",
        assessed_ports=["SGSIN", "NLRTM"],
        assessed_canals=["SUEZ"],
        verified_particulars={"scnt": 92000.0, "width_meters": 45.0, "draft_max_meters": 18.2},
        source_reported_particulars={},
        missing_particulars=[],
        conflicting_particulars=[],
        stale_particulars=[],
        blocking_reasons=[],
        warnings=[],
        field_evaluations={},
    )
    mock_vessel_service.evaluate_readiness.return_value = mock_assessment

    mock_disb_service = MagicMock()
    mock_disb_service.evaluate_voyage_disbursements.return_value.total_disbursement_usd = 65000.0
    mock_disb_service.evaluate_voyage_disbursements.return_value.has_partial_components = False

    engine = CommercialFixtureValidationEngine(
        vessel_service=mock_vessel_service,
        disbursement_service=mock_disb_service,
    )

    suez_payload = valid_payload.model_copy(
        update={
            "canal_transits": ["SUEZ"],
            "total_canal_allowance_usd": 250000.0,
            "total_port_allowance_usd": 65000.0,
        }
    )

    res = engine.validate_fixture(suez_payload)
    assert res.is_valid is True
    assert res.vessel_readiness_summary is not None
    assert res.vessel_readiness_summary["is_ready_for_research"] is True
    assert res.vessel_readiness_summary["verified_particulars"]["scnt"] == 92000.0

