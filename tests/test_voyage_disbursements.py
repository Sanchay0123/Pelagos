"""Automated Test Suite for Pelagos Stage F1.4.2.4: Voyage Disbursement Integration & TCE Gating.

Validates the 16 mandatory verification requirements:
1. Voyage with a registered port tariff.
2. Voyage with multiple port calls.
3. Voyage with a canal transit and port calls.
4. Voyage with no canal transit.
5. Missing tariff for one port (fails closed).
6. Missing required vessel particulars (fails closed).
7. Partial pilotage component propagation (Rotterdam S-rate, Houston pilotage).
8. Agency fee not automatically generated (commercially negotiated / contractual only).
9. Historical tariff rejection for operational use (Level 3 block).
10. Benchmark FX rejection as an official operational fixing (ECB May 1 holiday interpolation).
11. Missing currency conversion (fails closed).
12. Conditional charge unresolved (Panama Gatun Lake level).
13. Incomplete voyage total not presented as complete.
14. Research-only disbursement blocked from commercial TCE.
15. Research-only disbursement blocked from charter recommendation and bidding.
16. Existing Level 3 port and canal restrictions remain intact.
Plus: Database persistence and schema reconciliation for VoyageDisbursementRecord and line items.
"""

import datetime
from typing import Generator
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from pelagos.currency import CurrencyConversionService
from pelagos.database import ensure_schema_compatibility, init_db
from pelagos.models import Base, VoyageDisbursementLineItem, VoyageDisbursementRecord
from pelagos.registry.canal_tariff_registry import CanalTariffRegistry
from pelagos.registry.port_disbursement_registry import PortDisbursementRegistry
from pelagos.registry.voyage_disbursement_service import (
    VoyageCanalTransitRequest,
    VoyageDisbursementAssessmentRequest,
    VoyageDisbursementService,
    VoyageDisbursementStatus,
    VoyageDisbursementSummary,
    VoyagePortCallRequest,
)


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


@pytest.fixture
def currency_service() -> CurrencyConversionService:
    """Provide a seeded CurrencyConversionService with statutory IMF, ECB, and MAS rates."""
    cs = CurrencyConversionService()
    cs.seed_historical_statutory_rates()
    return cs


@pytest.fixture
def port_registry(currency_service: CurrencyConversionService) -> PortDisbursementRegistry:
    """Provide a PortDisbursementRegistry seeded with pilot tariffs."""
    return PortDisbursementRegistry(currency_service=currency_service)


@pytest.fixture
def canal_registry(currency_service: CurrencyConversionService) -> CanalTariffRegistry:
    """Provide a CanalTariffRegistry seeded with Panama and Suez tariffs."""
    return CanalTariffRegistry(currency_service=currency_service)


@pytest.fixture
def voyage_service(
    mem_db: Session,
    port_registry: PortDisbursementRegistry,
    canal_registry: CanalTariffRegistry,
    currency_service: CurrencyConversionService,
) -> VoyageDisbursementService:
    """Provide a VoyageDisbursementService backed by in-memory db and seeded registries."""
    return VoyageDisbursementService(
        db=mem_db,
        port_registry=port_registry,
        canal_registry=canal_registry,
        currency_service=currency_service,
    )


# Standard test vessel particulars: Ultramax / Supramax
VESSEL_GT = 36000.0
VESSEL_DWT = 64000.0
VESSEL_LOA = 199.9
VESSEL_BEAM = 32.26
VESSEL_DRAFT = 13.3
VESSEL_SCNT = 34500.0


# ---------------------------------------------------------------------------
# Test Cases (1-16)
# ---------------------------------------------------------------------------

def test_1_voyage_with_registered_port_tariff(voyage_service: VoyageDisbursementService):
    """1. Voyage with a single registered port tariff (Singapore SGSIN)."""
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-SGSIN-001",
        origin_port="SGSIN",
        destination_port="SGSIN",
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    assert summary.voyage_id == "VOY-SGSIN-001"
    assert len(summary.port_call_results) >= 1
    assert summary.total_port_disbursement_usd is not None
    assert summary.total_port_disbursement_usd > 0
    # Singapore pilotage is not marked partial
    assert summary.completeness_status == VoyageDisbursementStatus.COMPLETE_WITHIN_REGISTERED_SCOPE
    assert summary.is_complete is True
    assert summary.readiness_tier == "LEVEL_2_RESEARCH_ONLY"
    assert summary.is_operational_approved is False


def test_2_voyage_with_multiple_port_calls(voyage_service: VoyageDisbursementService):
    """2. Voyage with multiple port calls (origin USHOU, intermediate SGSIN, destination NLRTM)."""
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-MULTI-002",
        origin_port="USHOU",
        intermediate_ports=["SGSIN"],
        destination_port=VoyagePortCallRequest(
            un_locode="NLRTM",
            cargo_handled_tons=60000.0,
        ),
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    assert len(summary.port_call_results) == 3
    port_codes = [p.un_locode for p in summary.port_call_results]
    assert port_codes == ["USHOU", "SGSIN", "NLRTM"]
    assert summary.total_port_disbursement_usd is not None
    assert summary.total_port_disbursement_usd > 0
    # Because NLRTM has partial S-rate pilotage and USHOU has partial pilotage:
    assert summary.has_partial_components is True
    assert summary.completeness_status == VoyageDisbursementStatus.PARTIAL_COMPONENTS
    assert summary.is_complete is False


def test_3_voyage_with_canal_transit_and_port_calls(voyage_service: VoyageDisbursementService):
    """3. Voyage with a canal transit (Panama) and port calls."""
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-PANAMA-003",
        origin_port="USHOU",
        destination_port="SGSIN",
        canal_transits=[
            VoyageCanalTransitRequest(
                canal_name="PANAMA",
                loading_condition="LADEN",
                gatun_elevation_ft=84.0,
            )
        ],
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    assert len(summary.canal_transit_results) == 1
    assert summary.total_canal_transit_usd is not None
    assert summary.total_canal_transit_usd > 100000.0  # Panama toll for Ultramax > $100k
    assert summary.total_port_disbursement_usd is not None
    assert summary.total_disbursement_usd == round(
        summary.total_port_disbursement_usd + summary.total_canal_transit_usd, 2
    )
    # Line items contain both canal and port charges
    types = {item["charge_type"] for item in summary.all_line_items}
    assert "CANAL_TRANSIT" in types
    assert "PORT_DISBURSEMENT" in types


def test_4_voyage_with_no_canal_transit(voyage_service: VoyageDisbursementService):
    """4. Voyage with no canal transit (e.g. Cape route)."""
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-DIRECT-004",
        origin_port="SGSIN",
        destination_port="SGSIN",
        canal_transits=[],
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    assert len(summary.canal_transit_results) == 0
    assert summary.total_canal_transit_usd == 0.0
    assert summary.total_disbursement_usd == summary.total_port_disbursement_usd


def test_5_missing_tariff_for_one_port(voyage_service: VoyageDisbursementService):
    """5. Missing tariff for one port (fails closed)."""
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-UNREG-005",
        origin_port="SGSIN",
        destination_port="USOAK",  # Unregistered port
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    assert summary.is_complete is False
    assert summary.completeness_status == VoyageDisbursementStatus.MISSING_TARIFF
    assert summary.total_disbursement_usd is None
    assert any("PORT_NOT_REGISTERED" in r for r in summary.blocked_reasons)


def test_6_missing_required_vessel_particulars(voyage_service: VoyageDisbursementService):
    """6. Missing required vessel particulars (GT missing for Singapore port dues)."""
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-NOGT-006",
        origin_port="SGSIN",
        destination_port="SGSIN",
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=None,  # Missing GT
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    assert summary.is_complete is False
    assert summary.completeness_status == VoyageDisbursementStatus.MISSING_REQUIRED_INPUT
    assert summary.total_disbursement_usd is None
    assert any("MISSING_VESSEL_GT" in r for r in summary.blocked_reasons)


def test_7_partial_pilotage_component_propagation(voyage_service: VoyageDisbursementService):
    """7. Partial pilotage component propagation (Rotterdam S-rate and Houston draft/unit)."""
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-PARTIAL-007",
        origin_port="USHOU",
        destination_port=VoyagePortCallRequest(
            un_locode="NLRTM",
            cargo_handled_tons=50000.0,
        ),
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    assert summary.has_partial_components is True
    assert summary.completeness_status == VoyageDisbursementStatus.PARTIAL_COMPONENTS
    assert summary.is_complete is False

    # Check that individual line items carry is_partial_component=True
    partial_items = [item for item in summary.all_line_items if item.get("is_partial_component") is True]
    assert len(partial_items) >= 2
    tids = {item["tariff_identifier"] for item in partial_items}
    assert "NLRTM-LOODS-PILOTAGE-S-2024" in tids
    assert "USHOU-PILOT-DRAFT-2024" in tids or "USHOU-PILOT-UNIT-2024" in tids


def test_8_agency_fee_not_automatically_generated(voyage_service: VoyageDisbursementService):
    """8. Agency fee not automatically generated unless backed by contractual appointment."""
    # 8a: Without contractual fee
    req_no_fee = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-NOAGENCY-008A",
        origin_port="SGSIN",
        destination_port="SGSIN",
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
        contractual_agency_fee_usd=None,
    )
    summary_no_fee = voyage_service.evaluate_voyage_disbursements(req_no_fee)
    agency_items = [i for i in summary_no_fee.all_line_items if i["charge_category"] == "AGENCY_FEE"]
    assert len(agency_items) == 0

    # 8b: With explicit contractual fee
    req_with_fee = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-WITHAGENCY-008B",
        origin_port="SGSIN",
        destination_port="SGSIN",
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
        contractual_agency_fee_usd=3200.0,
    )
    summary_with_fee = voyage_service.evaluate_voyage_disbursements(req_with_fee)
    agency_items_with = [i for i in summary_with_fee.all_line_items if i["charge_category"] == "AGENCY_FEE"]
    assert len(agency_items_with) == 1
    assert agency_items_with[0]["amount_usd"] == 3200.0
    assert agency_items_with[0]["applicability_type"] == "COMMERCIALLY_NEGOTIATED / CONTRACTUAL"


def test_9_historical_tariff_rejection_for_operational_use(voyage_service: VoyageDisbursementService):
    """9. Historical tariff rejection when operational mode is requested."""
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-OP-BLOCKED-009",
        origin_port="SGSIN",
        destination_port="SGSIN",
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
        operational_mode=True,  # Operational mode requested!
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    assert summary.is_operational_approved is False
    assert summary.is_complete is False
    assert summary.completeness_status == VoyageDisbursementStatus.OPERATIONAL_BLOCKED
    assert summary.readiness_tier == "LEVEL_3_OPERATIONAL_BLOCKED"
    assert summary.total_disbursement_usd is None
    assert any("OPERATIONAL_TCE_DISBURSEMENT_BLOCKED" in r for r in summary.blocked_reasons)


def test_10_benchmark_fx_rejection_as_official_operational_fixing(currency_service: CurrencyConversionService):
    """10. Benchmark FX rejection as an official operational fixing (ECB Target2 holiday May 1)."""
    # 2024-05-01 is European Labor Day (Target2 closed)
    # Research conversion succeeds:
    res_research = currency_service.convert(
        amount=1000.0,
        from_currency="EUR",
        to_currency="USD",
        effective_date=datetime.date(2024, 5, 1),
        operational_mode=False,
    )
    assert res_research.is_available is True
    assert res_research.evidence_status == "PARTIALLY_VERIFIED"
    assert "Benchmark" in res_research.rate_provider

    # Operational conversion fails closed:
    res_op = currency_service.convert(
        amount=1000.0,
        from_currency="EUR",
        to_currency="USD",
        effective_date=datetime.date(2024, 5, 1),
        operational_mode=True,
    )
    assert res_op.is_available is False
    assert "BENCHMARK_EXCHANGE_RATE_REJECTED" in (res_op.failure_reason or "")

    # Genuine official observation on 2024-01-15 succeeds in operational mode:
    res_official = currency_service.convert(
        amount=1000.0,
        from_currency="EUR",
        to_currency="USD",
        effective_date=datetime.date(2024, 1, 15),
        operational_mode=True,
    )
    assert res_official.is_available is True
    assert res_official.evidence_status == "DIRECTLY_VERIFIED"


def test_11_missing_currency_conversion(voyage_service: VoyageDisbursementService):
    """11. Missing currency conversion fails closed."""
    # Date 2020-01-01 is outside seeded fixings
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-NOFX-011",
        origin_port="NLRTM",  # EUR dues
        destination_port=VoyagePortCallRequest(
            un_locode="NLRTM",
            cargo_handled_tons=50000.0,
        ),
        transit_date=datetime.date(2020, 1, 1),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    assert summary.is_complete is False
    # If date is outside tariff window or missing FX, fails closed
    assert summary.completeness_status in (
        VoyageDisbursementStatus.MISSING_CURRENCY_RATE,
        VoyageDisbursementStatus.MISSING_TARIFF,
    )
    assert summary.total_disbursement_usd is None


def test_12_conditional_charge_unresolved(voyage_service: VoyageDisbursementService):
    """12. Conditional charge unresolved (Panama Gatun Lake level missing)."""
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-NOGATUN-012",
        origin_port="USHOU",
        destination_port="SGSIN",
        canal_transits=[
            VoyageCanalTransitRequest(
                canal_name="PANAMA",
                loading_condition="LADEN",
                gatun_elevation_ft=None,  # Missing Gatun level
            )
        ],
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    assert summary.is_complete is False
    assert summary.completeness_status == VoyageDisbursementStatus.CONDITIONAL_CHARGE_UNRESOLVED
    assert summary.total_disbursement_usd is None
    assert any("GATUN" in r or "UNRESOLVED_FRESHWATER_SURCHARGE" in r for r in summary.blocked_reasons)


def test_13_incomplete_voyage_total_not_presented_as_complete(voyage_service: VoyageDisbursementService):
    """13. Incomplete voyage total is never presented as complete."""
    # When partial components are present
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-INCOMPLETE-013",
        origin_port=VoyagePortCallRequest(
            un_locode="NLRTM",
            cargo_handled_tons=50000.0,
        ),
        destination_port="SGSIN",
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    assert summary.has_partial_components is True
    assert summary.is_complete is False
    assert summary.completeness_status == VoyageDisbursementStatus.PARTIAL_COMPONENTS


def test_14_research_only_disbursement_blocked_from_commercial_tce(voyage_service: VoyageDisbursementService):
    """14. Research-only disbursement blocked from commercial TCE."""
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-TCEBLOCK-014",
        origin_port="SGSIN",
        destination_port="SGSIN",
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    allowed, reason = voyage_service.check_commercial_tce_eligibility(summary)
    assert allowed is False
    assert "COMMERCIAL_TCE_GATE_BLOCKED" in reason


def test_15_research_only_disbursement_blocked_from_charter_bidding(voyage_service: VoyageDisbursementService):
    """15. Research-only disbursement blocked from charter recommendation and commercial bidding."""
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-BIDBLOCK-015",
        origin_port="SGSIN",
        destination_port="SGSIN",
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    allowed, reason = voyage_service.check_charter_bidding_eligibility(summary)
    assert allowed is False
    assert "CHARTER_BIDDING_GATE_BLOCKED" in reason


def test_16_existing_level_3_port_and_canal_restrictions_remain_intact(
    port_registry: PortDisbursementRegistry,
    canal_registry: CanalTariffRegistry,
):
    """16. Existing Level 3 port and canal restrictions remain intact."""
    # Port registry operational mode returns blocked result
    port_res = port_registry.calculate_port_disbursement(
        un_locode="SGSIN",
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
        operational_mode=True,
    )
    assert port_res.is_complete is False
    assert port_res.total_port_costs_usd is None
    assert any("OPERATIONAL_PORT_DISBURSEMENT_BLOCKED" in r for r in port_res.blocked_reasons)

    # Panama operational mode returns blocked result
    panama_res = canal_registry.calculate_panama_toll(
        beam_m=VESSEL_BEAM,
        loa_m=VESSEL_LOA,
        dwt_mt=VESSEL_DWT,
        loading_condition="LADEN",
        transit_date=datetime.date(2024, 1, 15),
        gatun_elevation_ft=84.0,
        operational_mode=True,
    )
    assert panama_res.is_complete is False
    assert panama_res.total_toll_usd is None
    assert any("OPERATIONAL_CANAL_TOLL_BLOCKED" in r for r in panama_res.blocked_reasons)

    # Suez operational mode returns blocked result
    suez_res = canal_registry.calculate_suez_toll(
        scnt=VESSEL_SCNT,
        draft_m=VESSEL_DRAFT,
        beam_m=VESSEL_BEAM,
        loading_condition="LADEN",
        transit_date=datetime.date(2024, 1, 15),
        operational_mode=True,
    )
    assert suez_res.is_complete is False
    assert suez_res.total_toll_usd is None
    assert any("OPERATIONAL_CANAL_TOLL_BLOCKED" in r for r in suez_res.blocked_reasons)


# ---------------------------------------------------------------------------
# Database Persistence Test
# ---------------------------------------------------------------------------

def test_17_database_persistence_and_line_items(
    voyage_service: VoyageDisbursementService,
    mem_db: Session,
):
    """17. Persist VoyageDisbursementRecord and VoyageDisbursementLineItem to DB."""
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-DB-017",
        origin_port="USHOU",
        destination_port="SGSIN",
        canal_transits=[
            VoyageCanalTransitRequest(
                canal_name="PANAMA",
                loading_condition="LADEN",
                gatun_elevation_ft=84.0,
            )
        ],
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    # Persist
    rec = voyage_service.persist_voyage_disbursement(
        summary=summary,
        vessel_imo=9876543,
        vessel_name="PACIFIC TRADER",
        vessel_class="ULTRAMAX",
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )

    assert rec.id is not None
    assert rec.voyage_id == "VOY-DB-017"
    assert rec.vessel_name == "PACIFIC TRADER"

    # Query from DB session to verify foreign keys and line items
    db_rec = mem_db.scalar(select(VoyageDisbursementRecord).where(VoyageDisbursementRecord.id == rec.id))
    assert db_rec is not None
    assert len(db_rec.line_items) == len(summary.all_line_items)
    assert db_rec.line_items[0].voyage_disbursement_record_id == db_rec.id

    # Verify query on line items
    items = mem_db.scalars(
        select(VoyageDisbursementLineItem).where(
            VoyageDisbursementLineItem.voyage_disbursement_record_id == db_rec.id
        )
    ).all()
    assert len(items) > 0
    assert any(i.charge_type == "CANAL_TRANSIT" for i in items)
    assert any(i.charge_type == "PORT_DISBURSEMENT" for i in items)


# ---------------------------------------------------------------------------
# FINDING-F1424-M01 & L01 Remediation Tests
# ---------------------------------------------------------------------------

def test_18_standalone_currency_service_initialization():
    """18. FINDING-F1424-M01: Standalone VoyageDisbursementService() auto-seeds statutory rates."""
    # When both currency_service and db are omitted, statutory fixings must be auto-seeded
    standalone_service = VoyageDisbursementService()
    assert standalone_service.currency_service is not None
    assert len(standalone_service.currency_service._memory_rates) > 0

    # Ensure evaluation does not fail with MISSING_CURRENCY_RATE
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-STANDALONE-018",
        origin_port="SGSIN",
        destination_port="SGSIN",
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
    )
    summary = standalone_service.evaluate_voyage_disbursements(req)
    assert summary.completeness_status != VoyageDisbursementStatus.MISSING_CURRENCY_RATE
    assert not any("MISSING_CURRENCY_EXCHANGE_RATE" in r for r in summary.blocked_reasons)
    assert summary.total_disbursement_usd is not None


def test_19_custom_currency_service_preserved():
    """19. FINDING-F1424-M01: Custom caller-provided currency_service is preserved and not overwritten."""
    custom_cs = CurrencyConversionService()
    assert len(custom_cs._memory_rates) == 0

    # Caller passes custom empty currency service
    service = VoyageDisbursementService(currency_service=custom_cs)
    assert service.currency_service is custom_cs
    # Should NOT have auto-seeded statutory rates because caller provided custom_cs
    assert len(custom_cs._memory_rates) == 0


def test_20_fx_audit_provenance_line_items(
    voyage_service: VoyageDisbursementService,
    mem_db: Session,
):
    """20. FINDING-F1424-L01: FX audit provenance fields on line items in memory and persisted in DB."""
    req = VoyageDisbursementAssessmentRequest(
        voyage_id="VOY-FXAUDIT-020",
        origin_port="SGSIN",
        destination_port=VoyagePortCallRequest(
            un_locode="NLRTM",
            cargo_handled_tons=50000.0,
        ),
        canal_transits=[
            VoyageCanalTransitRequest(
                canal_name="SUEZ",
                loading_condition="LADEN",
            )
        ],
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
        scnt=VESSEL_SCNT,
        contractual_agency_fee_usd=1200.0,
    )
    summary = voyage_service.evaluate_voyage_disbursements(req)

    # 1. Verify in-memory line items contain fx_rate_provider and fx_observation_date
    sgsin_items = [
        i for i in summary.all_line_items
        if i["location_code"] == "SGSIN" and i["tariff_identifier"] != "CONTRACTUAL-AGENCY-FEE"
    ]
    assert len(sgsin_items) > 0
    for item in sgsin_items:
        assert item["fx_rate_provider"] == "Monetary Authority of Singapore (MAS) Daily Rates"
        assert item["fx_observation_date"] == datetime.date(2024, 1, 15)

    nlrtm_items = [i for i in summary.all_line_items if i["location_code"] == "NLRTM"]
    assert len(nlrtm_items) > 0
    for item in nlrtm_items:
        assert item["fx_rate_provider"] == "European Central Bank (ECB) Reference Rate"
        assert item["fx_observation_date"] == datetime.date(2024, 1, 15)

    suez_base_items = [i for i in summary.all_line_items if i["tariff_identifier"] == "SCA-TOLL-BASE"]
    assert len(suez_base_items) == 1
    assert suez_base_items[0]["fx_rate_provider"] == "IMF Daily SDR Valuation"
    assert suez_base_items[0]["fx_observation_date"] == datetime.date(2024, 1, 15)

    agency_items = [i for i in summary.all_line_items if i["tariff_identifier"] == "CONTRACTUAL-AGENCY-FEE"]
    assert len(agency_items) == 1
    assert agency_items[0]["fx_rate_provider"] == "IDENTITY"
    assert agency_items[0]["fx_observation_date"] is None

    # 2. Persist to DB and verify fields are stored in VoyageDisbursementLineItem
    rec = voyage_service.persist_voyage_disbursement(
        summary=summary,
        vessel_imo=9876543,
        vessel_name="PACIFIC TRADER",
        vessel_class="ULTRAMAX",
        vessel_gt=VESSEL_GT,
        vessel_dwt=VESSEL_DWT,
        loa_m=VESSEL_LOA,
        beam_m=VESSEL_BEAM,
        draft_m=VESSEL_DRAFT,
        scnt=VESSEL_SCNT,
    )

    persisted_items = mem_db.scalars(
        select(VoyageDisbursementLineItem).where(
            VoyageDisbursementLineItem.voyage_disbursement_record_id == rec.id
        )
    ).all()

    assert len(persisted_items) == len(summary.all_line_items)

    persisted_sgsin = [
        i for i in persisted_items
        if i.location_code == "SGSIN" and i.tariff_identifier != "CONTRACTUAL-AGENCY-FEE"
    ]
    assert all(i.fx_rate_provider == "Monetary Authority of Singapore (MAS) Daily Rates" for i in persisted_sgsin)
    assert all(i.fx_observation_date == datetime.date(2024, 1, 15) for i in persisted_sgsin)

    persisted_suez = [i for i in persisted_items if i.tariff_identifier == "SCA-TOLL-BASE"]
    assert len(persisted_suez) == 1
    assert persisted_suez[0].fx_rate_provider == "IMF Daily SDR Valuation"
    assert persisted_suez[0].fx_observation_date == datetime.date(2024, 1, 15)

