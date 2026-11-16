"""
Tests for Pelagos Stage F1.4.3 Phase 1: Readiness & Evidence Validation Foundation.

Covers:
  - Scope A: Canonical FX valuation, provider-specific lookback horizons, future/stale rejection,
            identity provenance, benchmark rejection in operational mode.
  - Scope B: Multi-dimensional vessel particulars readiness, authority-specific requirements,
            zero synthetic tonnage enforcement, conflict retention, evidence classifications.
  - Scope C: Port & canal tariff applicability validation (10 criteria), effective dates,
            conditional charges, partial components, permission restrictions.
  - Scope D: Four-tier charge decomposition (Tier 1 Statutory, Tier 2 Regulated, Tier 3 Contractual,
            Tier 4 Provisional) and one-way statutory floor enforcement.
  - Scope E: Unified voyage readiness assessment (Research Ready, Incomplete, Partial Components,
            Qualified Estimate, Operational Blocked).
  - Scope F: Inviolate operational boundary: is_ready_for_operations strictly False under all conditions.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.currency import (
    CurrencyConversionService,
    CurrencyEvidenceClassification,
    CurrencyExchangeRate,
    PROVIDER_MAX_LOOKBACK_CALENDAR_DAYS,
)
from pelagos.database import Base, ensure_schema_compatibility
from pelagos.readiness.charge_classifier import (
    ChargeClassificationTier,
    ChargeClassifier,
    ClassifiedChargeItem,
    FloorCheckResult,
)
from pelagos.readiness.tariff_validation import (
    PortCanalTariffValidationSummary,
    TariffValidationResult,
    TariffValidationService,
)
from pelagos.readiness.vessel_readiness import (
    VesselParticularEvidence,
    VesselParticularStatus,
    VesselReadinessAssessment,
    VesselReadinessService,
)
from pelagos.readiness.voyage_readiness_service import (
    VoyageReadinessAssessmentResult,
    VoyageReadinessService,
    VoyageReadinessStatus,
)


@pytest.fixture
def in_memory_db():
    """In-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    ensure_schema_compatibility(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def seeded_currency_service(in_memory_db):
    """Currency conversion service with seeded central bank fixings and benchmarks."""
    service = CurrencyConversionService(db=in_memory_db)
    
    # IMF SDR fixings
    # 2024-01-12 (Friday), 2024-01-15 (Monday), 2024-01-16 (Tuesday)
    sdr_rates = [
        CurrencyExchangeRate(
            base_currency="SDR",
            quote_currency="USD",
            rate=1.3340,
            effective_date=datetime.date(2024, 1, 12),
            rate_provider="IMF",
            source_reference="IMF Daily SDR Valuation (2024-01-12)",
            evidence_status="DIRECTLY_VERIFIED",
        ),
        CurrencyExchangeRate(
            base_currency="SDR",
            quote_currency="USD",
            rate=1.3355,
            effective_date=datetime.date(2024, 1, 15),
            rate_provider="IMF",
            source_reference="IMF Daily SDR Valuation (2024-01-15)",
            evidence_status="DIRECTLY_VERIFIED",
        ),
    ]
    
    # ECB EUR fixings
    eur_rates = [
        CurrencyExchangeRate(
            base_currency="EUR",
            quote_currency="USD",
            rate=1.0950,
            effective_date=datetime.date(2024, 1, 12),
            rate_provider="ECB",
            source_reference="ECB Euro Foreign Exchange Reference Rate (2024-01-12)",
            evidence_status="DIRECTLY_VERIFIED",
        ),
        CurrencyExchangeRate(
            base_currency="EUR",
            quote_currency="USD",
            rate=1.0920,
            effective_date=datetime.date(2024, 1, 15),
            rate_provider="ECB",
            source_reference="ECB Euro Foreign Exchange Reference Rate (2024-01-15)",
            evidence_status="DIRECTLY_VERIFIED",
        ),
    ]

    # MAS SGD fixings
    sgd_rates = [
        CurrencyExchangeRate(
            base_currency="SGD",
            quote_currency="USD",
            rate=0.7510,
            effective_date=datetime.date(2024, 1, 10),
            rate_provider="MAS",
            source_reference="MAS Domestic Interest and Exchange Rates (2024-01-10)",
            evidence_status="DIRECTLY_VERIFIED",
        ),
        CurrencyExchangeRate(
            base_currency="SGD",
            quote_currency="USD",
            rate=0.7525,
            effective_date=datetime.date(2024, 1, 15),
            rate_provider="MAS",
            source_reference="MAS Domestic Interest and Exchange Rates (2024-01-15)",
            evidence_status="DIRECTLY_VERIFIED",
        ),
    ]

    # Benchmark rate (interpolated / mid-market)
    benchmark_rates = [
        CurrencyExchangeRate(
            base_currency="SDR",
            quote_currency="USD",
            rate=1.3300,
            effective_date=datetime.date(2024, 5, 1),
            rate_provider="MARKET_BENCHMARK",
            source_reference="Synthetic Mid-Market Benchmark",
            evidence_status="PARTIALLY_VERIFIED",
        )
    ]

    for r in sdr_rates + eur_rates + sgd_rates + benchmark_rates:
        in_memory_db.add(r)
    in_memory_db.commit()

    return service


# =============================================================================
# Scope A: Canonical FX Valuation & Lookback Provenance
# =============================================================================

def test_fx_identity_conversion_provenance(seeded_currency_service):
    """Identity conversion must be 1.0, provider IDENTITY, observation date None."""
    res = seeded_currency_service.convert(
        amount=1000.0,
        from_currency="USD",
        to_currency="USD",
        valuation_date=datetime.date(2024, 1, 15),
    )
    assert res.is_valid is True
    assert res.converted_amount == 1000.0
    assert res.exchange_rate == 1.0
    assert res.rate_provider == "IDENTITY"
    assert res.actual_observation_date is None
    assert res.effective_date is None
    assert res.observation_lag_days == 0
    assert res.conversion_evidence_classification == CurrencyEvidenceClassification.IDENTITY


def test_fx_provider_lookback_within_limit(seeded_currency_service):
    """IMF SDR observation on Friday (Jan 12) is valid for Sunday (Jan 14) transit (lag=2 <= 4)."""
    res = seeded_currency_service.convert(
        amount=100.0,
        from_currency="SDR",
        to_currency="USD",
        valuation_date=datetime.date(2024, 1, 14),
    )
    assert res.is_valid is True
    assert res.exchange_rate == 1.3340
    assert res.rate_provider == "IMF"
    assert res.actual_observation_date == datetime.date(2024, 1, 12)
    assert res.requested_valuation_date == datetime.date(2024, 1, 14)
    assert res.observation_lag_days == 2
    assert res.conversion_evidence_classification == CurrencyEvidenceClassification.OFFICIAL_FIXING


def test_fx_mas_lookback_5_days_permitted(seeded_currency_service):
    """MAS SGD observation on Jan 10 is valid for Jan 14 transit (lag=4 <= 5)."""
    res = seeded_currency_service.convert(
        amount=1000.0,
        from_currency="SGD",
        to_currency="USD",
        valuation_date=datetime.date(2024, 1, 14),
    )
    assert res.is_valid is True
    assert res.exchange_rate == 0.7510
    assert res.rate_provider == "MAS"
    assert res.actual_observation_date == datetime.date(2024, 1, 10)
    assert res.observation_lag_days == 4


def test_fx_future_observation_rejected(seeded_currency_service):
    """Observation date after valuation date is strictly rejected fail-closed."""
    # Requesting on Jan 11 when earliest rate in DB is Jan 12 should not use future Jan 12!
    res = seeded_currency_service.convert(
        amount=100.0,
        from_currency="SDR",
        to_currency="USD",
        valuation_date=datetime.date(2024, 1, 11),
    )
    assert res.is_valid is False
    assert res.converted_amount is None
    assert "FUTURE_EXCHANGE_RATE_OBSERVATION_REJECTED" in res.failure_reason


def test_fx_stale_observation_exceeding_lookback_rejected(seeded_currency_service):
    """Observation lag exceeding provider horizon (e.g. IMF lag=5 > 4) fails closed."""
    # Jan 12 fixing requested on Jan 18 is 6 days lag -> exceeds 4d limit
    res = seeded_currency_service.convert(
        amount=100.0,
        from_currency="SDR",
        to_currency="USD",
        valuation_date=datetime.date(2024, 1, 18),
    )
    # Note: Jan 15 fixing is also in DB, so lag from Jan 15 to Jan 18 is 3 days (within 4d limit).
    # To test stale rejection, request for Jan 25 (lag from Jan 15 is 10 days > 4 days)
    res_stale = seeded_currency_service.convert(
        amount=100.0,
        from_currency="SDR",
        to_currency="USD",
        valuation_date=datetime.date(2024, 1, 25),
    )
    assert res_stale.is_valid is False
    assert res_stale.converted_amount is None
    assert "EXPIRED_OR_STALE_CURRENCY_OBSERVATION" in res_stale.failure_reason


def test_fx_benchmark_rejected_in_operational_mode(seeded_currency_service):
    """Benchmark rate is permitted for research, but rejected with BENCHMARK_EXCHANGE_RATE_REJECTED in operational mode."""
    # Research mode
    res_research = seeded_currency_service.convert(
        amount=100.0,
        from_currency="SDR",
        to_currency="USD",
        valuation_date=datetime.date(2024, 5, 1),
        operational_mode=False,
    )
    assert res_research.is_valid is True
    assert res_research.conversion_evidence_classification == CurrencyEvidenceClassification.BENCHMARK

    # Operational mode
    res_op = seeded_currency_service.convert(
        amount=100.0,
        from_currency="SDR",
        to_currency="USD",
        valuation_date=datetime.date(2024, 5, 1),
        operational_mode=True,
    )
    assert res_op.is_valid is False
    assert "BENCHMARK_EXCHANGE_RATE_REJECTED" in res_op.failure_reason


# =============================================================================
# Scope B: Vessel Statutory Particulars Readiness
# =============================================================================

def test_vessel_readiness_complete_capesize():
    """Vessel with directly verified particulars passes all required authorities."""
    service = VesselReadinessService()
    vessel = {
        "imo": 9876543,
        "vessel_name": "CAPESIZE TESTER",
        "dwt": 180000.0,
        "gt": 95000.0,
        "nt": 62000.0,
        "scnt": 92000.0,
        "pc_ums_net": 94000.0,
        "loa_m": 292.0,
        "beam_m": 45.0,
        "draft_m": 18.2,
        "evidence_dwt": "DIRECTLY_VERIFIED",
        "evidence_gt": "DIRECTLY_VERIFIED",
        "evidence_scnt": "DIRECTLY_VERIFIED",
        "evidence_loa": "DIRECTLY_VERIFIED",
        "evidence_beam": "DIRECTLY_VERIFIED",
        "evidence_draft": "DIRECTLY_VERIFIED",
    }
    assessment = service.assess_readiness(vessel, required_authorities=["SUEZ", "PANAMA", "SGSIN", "NLRTM", "USHOU"])
    assert assessment.is_ready_for_research is True
    assert assessment.is_complete is True
    assert len(assessment.validation_errors) == 0
    assert "ZERO_SYNTHETIC_TONNAGE_ENFORCED" in assessment.audit_flags


def test_vessel_readiness_suez_missing_scnt_fails_closed():
    """Suez Canal transit without certified SCNT fails readiness check."""
    service = VesselReadinessService()
    vessel = {
        "imo": 9876543,
        "dwt": 180000.0,
        "gt": 95000.0,
        "loa_m": 292.0,
        "beam_m": 45.0,
        "draft_m": 18.2,
        # scnt is omitted!
    }
    assessment = service.assess_readiness(vessel, required_authorities=["SUEZ"])
    assert assessment.is_ready_for_research is False
    assert assessment.is_complete is False
    assert any("MISSING_CERTIFIED_SCNT" in err or "scnt" in err.lower() for err in assessment.validation_errors)


def test_vessel_readiness_zero_synthetic_tonnage_enforcement():
    """Attempting to use synthetic or estimated SCNT is rejected."""
    service = VesselReadinessService()
    vessel = {
        "imo": 9876543,
        "gt": 95000.0,
        "scnt": 92000.0,
        "beam_m": 45.0,
        "draft_m": 18.2,
        "evidence_scnt": "SYNTHETIC_DERIVED",  # Violation of zero synthetic tonnage!
    }
    assessment = service.assess_readiness(vessel, required_authorities=["SUEZ"])
    assert assessment.is_ready_for_research is False
    assert any("SYNTHETIC_TONNAGE_REJECTED" in err for err in assessment.validation_errors)


def test_vessel_readiness_conflicting_particulars_retained():
    """Conflicting particulars are retained as conflicts and never resolved arbitrarily."""
    service = VesselReadinessService()
    vessel = {
        "imo": 9876543,
        "gt": 95000.0,
        "dwt": 180000.0,
        "loa_m": 292.0,
        "beam_m": 45.0,
        "draft_m": 18.2,
        "scnt": 92000.0,
        "conflicts": {"dwt": "Source A reports 180,000 MT, Source B reports 175,000 MT"},
    }
    assessment = service.assess_readiness(vessel, required_authorities=["PANAMA"])
    assert "dwt" in assessment.conflicts
    assert assessment.particulars["dwt"].status == VesselParticularStatus.CONFLICTING
    assert "CONFLICTING_PARTICULARS_RETAINED" in assessment.audit_flags


def test_vessel_readiness_source_reported_never_promoted():
    """Source-reported values (e.g. Berge Galdhopiggen DWT/LOA) remain SOURCE_REPORTED."""
    service = VesselReadinessService()
    vessel = {
        "imo": 9327891,
        "vessel_name": "BERGE GALDHOPIGGEN",
        "dwt": 177000.0,
        "loa_m": 289.0,
        "beam_m": 45.0,
        "draft_m": 18.0,
        "evidence_dwt": "SOURCE_REPORTED",
        "evidence_loa": "SOURCE_REPORTED",
    }
    assessment = service.assess_readiness(vessel, required_authorities=["PANAMA"])
    assert assessment.particulars["dwt"].evidence_classification == "SOURCE_REPORTED"
    assert assessment.particulars["loa_m"].evidence_classification == "SOURCE_REPORTED"
    assert assessment.is_ready_for_research is True
    assert "UNPROMOTED_SOURCE_REPORTED" in assessment.audit_flags


# =============================================================================
# Scope C: Port & Canal Tariff Applicability Validation
# =============================================================================

def test_tariff_validation_valid_itinerary():
    """Valid registered ports and canals pass 10-criteria applicability checks."""
    service = TariffValidationService()
    summary = service.validate_itinerary(
        port_unlocodes=["SGSIN", "NLRTM"],
        canal_ids=["SUEZ"],
        transit_date=datetime.date(2024, 1, 15),
        vessel_gt=95000.0,
        cargo_type="CONTAINER",
        vessel_dimensions={"beam_m": 45.0, "draft_m": 18.0, "scnt": 92000.0},
    )
    assert summary.all_tariffs_valid is True
    assert "SGSIN" in summary.port_results
    assert "NLRTM" in summary.port_results
    assert "SUEZ" in summary.canal_results


def test_tariff_validation_unregistered_port_fails():
    """Unregistered port (e.g. JPKAW) fails tariff applicability."""
    service = TariffValidationService()
    summary = service.validate_itinerary(
        port_unlocodes=["JPKAW"],
        transit_date=datetime.date(2024, 6, 15),
    )
    assert summary.all_tariffs_valid is False
    assert "JPKAW" in summary.missing_tariffs
    assert not summary.port_results["JPKAW"].is_valid


def test_tariff_validation_conditional_freshwater_surcharge():
    """Panama Canal Gatun elevation is correctly flagged as conditional."""
    service = TariffValidationService()
    res = service.validate_canal_tariff(
        canal_id="PANAMA",
        transit_date=datetime.date(2024, 6, 15),
        vessel_dimensions={"beam_m": 32.2, "loa_m": 225.0, "dwt": 75000.0},
    )
    assert res.is_valid is True
    assert res.is_conditional is True
    assert any("Gatun lake elevation" in c for c in res.conditional_components)


def test_tariff_validation_partial_pilotage_components():
    """Rotterdam and Houston identify known partial tariff components."""
    service = TariffValidationService()
    rtm_res = service.validate_port_tariff("NLRTM", transit_date=datetime.date(2024, 6, 15), vessel_gt=90000.0)
    assert rtm_res.has_partial_components is True
    assert any("River Pilotage" in p for p in rtm_res.partial_components)

    hou_res = service.validate_port_tariff("USHOU", transit_date=datetime.date(2024, 6, 15), vessel_gt=50000.0)
    assert hou_res.has_partial_components is True
    assert any("Draft overage" in p for p in hou_res.partial_components)


# =============================================================================
# Scope D: Four-Tier Charge Classification & Floor Enforcement
# =============================================================================

def test_charge_tier_decomposition():
    """All four tiers are correctly categorized per governance specifications."""
    classifier = ChargeClassifier()

    # Tier 1
    t1 = classifier.classify_line_item({
        "tariff_identifier": "MPA-HARBOUR-DUES-2024",
        "description": "Port Dues for General Cargo Vessel",
        "charge_category": "HARBOUR_DUES",
        "amount_usd": 4200.0,
    })
    assert t1.charge_tier == ChargeClassificationTier.TIER_1_STATUTORY_DUES
    assert t1.is_operational_permitted is True

    # Tier 2
    t2 = classifier.classify_line_item({
        "tariff_identifier": "NLRTM-PILOT-SEAWARD-2024",
        "description": "Seaward Pilotage Tariff",
        "charge_category": "PILOTAGE",
        "amount_usd": 3800.0,
    })
    assert t2.charge_tier == ChargeClassificationTier.TIER_2_REGULATED_SERVICES
    assert t2.is_operational_permitted is True

    # Tier 3 (With contract)
    t3_valid = classifier.classify_line_item({
        "tariff_identifier": "COMMERCIAL-AGENCY-FEE",
        "description": "Port Agency Fee",
        "charge_category": "AGENCY_FEE",
        "contract_id": "AGY-SGSIN-2024-001",
        "amount_usd": 2500.0,
    })
    assert t3_valid.charge_tier == ChargeClassificationTier.TIER_3_CONTRACTUAL_REMUNERATION
    assert t3_valid.is_operational_permitted is True

    # Tier 3 (Missing contract)
    t3_missing = classifier.classify_line_item({
        "tariff_identifier": "COMMERCIAL-AGENCY-FEE",
        "description": "Port Agency Fee",
        "charge_category": "AGENCY_FEE",
        "amount_usd": 2500.0,
    })
    assert t3_missing.charge_tier == ChargeClassificationTier.TIER_3_CONTRACTUAL_REMUNERATION
    assert t3_missing.is_operational_permitted is False
    assert "MISSING_CONTRACTUAL_EVIDENCE" in t3_missing.flags

    # Tier 4 (Provisional estimate)
    t4 = classifier.classify_line_item({
        "tariff_identifier": "PROVISIONAL-CONTINGENCY",
        "description": "Port Contingency Reserve",
        "charge_category": "CONTINGENCY_RESERVE",
        "amount_usd": 5000.0,
    })
    assert t4.charge_tier == ChargeClassificationTier.TIER_4_PROVISIONAL_ESTIMATES
    assert t4.is_provisional is True
    assert t4.is_operational_permitted is False


def test_statutory_floor_below_floor_fails_closed():
    """Valuation below statutory floor fails closed with FIXTURE_VALUATION_BELOW_STATUTORY_FLOOR."""
    classifier = ChargeClassifier()
    # Baseline statutory canal toll is $450,000. Caller supplies $400,000.
    res = classifier.evaluate_statutory_floor(
        user_valuation_usd=400000.0,
        statutory_floor_usd=450000.0,
    )
    assert res.is_floor_satisfied is False
    assert res.status == "REJECTED_BELOW_STATUTORY_FLOOR"
    assert res.is_operational_permitted is False
    assert any("FIXTURE_VALUATION_BELOW_STATUTORY_FLOOR" in r for r in res.failure_reasons)


def test_statutory_floor_zero_tolerance_on_subfloor():
    """Even a 1 dollar deficit fails closed (zero tolerance, no percentage bands)."""
    classifier = ChargeClassifier()
    res = classifier.evaluate_statutory_floor(
        user_valuation_usd=449999.0,
        statutory_floor_usd=450000.0,
    )
    assert res.is_floor_satisfied is False
    assert res.variance_usd == -1.0


def test_statutory_floor_unallocated_overage_flagged():
    """Surplus above floor is not automatically validated; unallocated overage is flagged."""
    classifier = ChargeClassifier()
    # Statutory is $450,000. Caller supplies $500,000 without line item allocations.
    res = classifier.evaluate_statutory_floor(
        user_valuation_usd=500000.0,
        statutory_floor_usd=450000.0,
    )
    assert res.is_floor_satisfied is True
    assert res.variance_usd == 50000.0
    assert res.unallocated_overage_usd == 50000.0
    assert "UNALLOCATED_COMMERCIAL_OVERAGE" in res.flags
    assert res.is_operational_permitted is False


def test_statutory_floor_surplus_itemized_reconciled():
    """Surplus accounted for by itemized Tier 2/3/4 charges passes reconciliation."""
    classifier = ChargeClassifier()
    itemized = [
        {"charge_category": "PILOTAGE_OVERTIME", "amount_usd": 15000.0},
        {"charge_category": "AGENCY_FEE", "amount_usd": 5000.0, "contract_id": "AGY-001"},
        {"charge_category": "CONTINGENCY_RESERVE", "amount_usd": 30000.0},
    ]
    res = classifier.evaluate_statutory_floor(
        user_valuation_usd=500000.0,
        statutory_floor_usd=450000.0,
        itemized_charges=itemized,
    )
    assert res.is_floor_satisfied is True
    assert res.unallocated_overage_usd == 0.0
    assert res.status == "SURPLUS_ITEMIZED_RECONCILED"


# =============================================================================
# Scope E & F: Unified Voyage Readiness & Operational Block
# =============================================================================

def test_voyage_readiness_clean_research_ready(seeded_currency_service):
    """Complete, valid voyage assessment achieves LEVEL_2_RESEARCH_READY."""
    service = VoyageReadinessService(currency_service=seeded_currency_service)
    vessel = {
        "imo": 9876543,
        "vessel_name": "PACIFIC STAR",
        "gt": 40000.0,
        "dwt": 65000.0,
        "loa_m": 199.0,
        "beam_m": 32.2,
        "draft_m": 12.5,
    }
    # Singapore to Houston via Panama with valid Gatun level
    res = service.assess_voyage_readiness(
        voyage_id="VOY-2024-TEST-01",
        transit_date="2024-01-15",
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="USHOU",
        canal_transits=["PANAMA"],
        vessel_particulars=vessel,
        contractual_agency_fee_usd=3000.0,
        agency_contract_id="AGY-CONTR-999",
        operational_mode=False,
    )
    assert res.is_ready_for_research is True
    assert res.is_ready_for_operations is False  # Inviolate Phase 1 operational lock!
    assert res.readiness_tier == "LEVEL_2_RESEARCH_ONLY"


def test_voyage_readiness_missing_vessel_scnt_incomplete(seeded_currency_service):
    """Suez route without SCNT yields LEVEL_2_RESEARCH_INCOMPLETE."""
    service = VoyageReadinessService(currency_service=seeded_currency_service)
    vessel = {
        "imo": 9876543,
        "gt": 95000.0,
        "dwt": 180000.0,
        "loa_m": 292.0,
        "beam_m": 45.0,
        "draft_m": 18.0,
        # SCNT missing!
    }
    res = service.assess_voyage_readiness(
        voyage_id="VOY-2024-TEST-02",
        transit_date="2024-01-15",
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="NLRTM",
        canal_transits=["SUEZ"],
        vessel_particulars=vessel,
        operational_mode=False,
    )
    assert res.readiness_status == VoyageReadinessStatus.LEVEL_2_RESEARCH_INCOMPLETE
    assert res.is_ready_for_research is False
    assert any("scnt" in err.lower() for err in res.blocked_reasons)


def test_voyage_readiness_partial_components_status_and_disclaimer(seeded_currency_service):
    """Route with partial components carries LEVEL_2_PARTIAL_COMPONENTS and disclaimer."""
    service = VoyageReadinessService(currency_service=seeded_currency_service)
    vessel = {
        "imo": 9876543,
        "gt": 95000.0,
        "dwt": 180000.0,
        "loa_m": 292.0,
        "beam_m": 45.0,
        "draft_m": 18.0,
        "scnt": 92000.0,
    }
    # NLRTM has partial river pilotage component
    res = service.assess_voyage_readiness(
        voyage_id="VOY-2024-TEST-03",
        transit_date="2024-01-15",
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="NLRTM",
        canal_transits=["SUEZ"],
        vessel_particulars=vessel,
        operational_mode=False,
    )
    assert res.readiness_status == VoyageReadinessStatus.LEVEL_2_PARTIAL_COMPONENTS
    assert res.has_partial_components is True
    assert res.disclaimer == (
        "QUALIFIED COMMERCIAL ESTIMATE: Contains unverified or partial statutory "
        "port/canal charges. Not certified for contractual final disbursement settlement."
    )


def test_voyage_readiness_operational_mode_strictly_blocked(seeded_currency_service):
    """Passing operational_mode=True triggers LEVEL_3_OPERATIONAL_BLOCKED and fails closed."""
    service = VoyageReadinessService(currency_service=seeded_currency_service)
    vessel = {
        "imo": 9876543,
        "gt": 40000.0,
        "dwt": 65000.0,
        "loa_m": 199.0,
        "beam_m": 32.2,
        "draft_m": 12.5,
    }
    res = service.assess_voyage_readiness(
        voyage_id="VOY-2024-TEST-04",
        transit_date="2024-01-15",
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="USHOU",
        canal_transits=["PANAMA"],
        vessel_particulars=vessel,
        operational_mode=True,  # Phase 1 lock!
    )
    assert res.readiness_status == VoyageReadinessStatus.LEVEL_3_OPERATIONAL_BLOCKED
    assert res.is_ready_for_operations is False
    assert any("LEVEL_3_OPERATIONAL_BLOCKED" in r for r in res.blocked_reasons)


def test_voyage_readiness_serialization_to_dict(seeded_currency_service):
    """Readiness result dictionary serialization is complete and well-structured."""
    service = VoyageReadinessService(currency_service=seeded_currency_service)
    vessel = {
        "imo": 9876543,
        "gt": 40000.0,
        "dwt": 65000.0,
        "loa_m": 199.0,
        "beam_m": 32.2,
        "draft_m": 12.5,
    }
    res = service.assess_voyage_readiness(
        voyage_id="VOY-2024-TEST-05",
        transit_date="2024-01-15",
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="USHOU",
        vessel_particulars=vessel,
    )
    d = res.to_dict()
    assert d["voyage_id"] == "VOY-2024-TEST-05"
    assert d["readiness_tier"] == "LEVEL_2_RESEARCH_ONLY"
    assert d["is_ready_for_operations"] is False
    assert "vessel_assessment" in d
    assert "tariff_summary" in d
    assert "charge_summary" in d
