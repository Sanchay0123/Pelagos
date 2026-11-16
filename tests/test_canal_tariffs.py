"""Comprehensive test suite for Stage F1.4.2.3a: Canal Tariff Registry & Deterministic Rule Foundation.

Tests cover:
- Panama tariff tier boundaries (Regular, Super, Neopanamax)
- Panama 2024 vs 2025 rate versions
- Panama laden and ballast treatment (85% on fixed + capacity)
- Panama FWS dynamic-input requirement & sigmoidal response
- Panama expired/unsupported tariff version handling (2026+ fails closed)
- Suez progressive bracket boundaries (Tiers 1 through 7)
- Individual laden and ballast bracket preservation (no blanket 85%)
- Suez Circular No. 7/2023 surcharge separation (+5% dry bulk)
- Suez Circular No. 1/2024 mooring fee ($3,500 USD)
- Suez Article 58 escort tug rule evaluation
- Missing certified tonnage fail-closed behavior (no synthetic tonnage)
- Missing currency conversion rates fail-closed behavior
- Partially verified source evidence propagation
- Operational mode strict fail-closed gating
- Database persistence and schema compatibility
"""

from __future__ import annotations

import datetime
from typing import Generator
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from pelagos.currency import CurrencyConversionResult, CurrencyConversionService
from pelagos.database import Base, ensure_schema_compatibility, init_db
from pelagos.models import CanalTariffRecord, CurrencyExchangeRate
from pelagos.registry.canal_tariff_registry import (
    CanalTariffRegistry,
    PanamaTollCalculationResult,
    SuezTollCalculationResult,
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
    """Provide a seeded CurrencyConversionService."""
    cs = CurrencyConversionService()
    cs.seed_historical_statutory_rates()
    return cs


@pytest.fixture
def registry(currency_service: CurrencyConversionService) -> CanalTariffRegistry:
    """Provide a CanalTariffRegistry with seeded currency service."""
    return CanalTariffRegistry(currency_service=currency_service)


# ---------------------------------------------------------------------------
# 1. Panama Canal Tariff Tier Boundaries & Versioning
# ---------------------------------------------------------------------------

class TestPanamaCanalRules:

    def test_panama_tier_boundaries(self, registry: CanalTariffRegistry):
        """Verify dimensional thresholds for Regular, Super, and Neopanamax."""
        # Regular: Beam < 27.74 m, LOA <= 294.44 m
        tier, fixed, cap = registry.resolve_panama_vessel_tier(beam_m=25.0, loa_m=175.0)
        assert tier == "Regular"
        assert fixed == "1010.FR03"
        assert cap == "1010.DR01"

        # Super: 27.74 m <= Beam < 32.61 m, LOA <= 294.44 m
        tier, fixed, cap = registry.resolve_panama_vessel_tier(beam_m=32.24, loa_m=224.98)  # Navios Victory
        assert tier == "Super"
        assert fixed == "1010.FS01"
        assert cap == "1010.DS01"

        tier, fixed, cap = registry.resolve_panama_vessel_tier(beam_m=32.26, loa_m=228.99)  # Iris Bliss
        assert tier == "Super"
        assert fixed == "1010.FS01"
        assert cap == "1010.DS01"

        # Neopanamax: Beam >= 32.61 m
        tier, fixed, cap = registry.resolve_panama_vessel_tier(beam_m=35.0, loa_m=250.0)
        assert tier == "Neopanamax"
        assert fixed == "1010.FN02"
        assert cap == "1010.DN01"

        # Neopanamax: LOA > 294.44 m (even if Beam < 32.61 m)
        tier, fixed, cap = registry.resolve_panama_vessel_tier(beam_m=32.0, loa_m=299.9)
        assert tier == "Neopanamax"
        assert fixed == "1010.FN02"
        assert cap == "1010.DN01"

    def test_panama_2024_vs_2025_rates(self, registry: CanalTariffRegistry):
        """Verify distinct 2024 and 2025 rate schedules."""
        # 2024 Regular Laden
        res_2024 = registry.calculate_panama_toll(
            beam_m=26.0,
            loa_m=180.0,
            dwt_mt=35000.0,
            loading_condition="LADEN",
            transit_date=datetime.date(2024, 6, 1),
            gatun_elevation_ft=82.0,
            measurement_datetime=datetime.datetime(2024, 5, 31, 12, 0),
        )
        assert res_2024.is_complete is True
        assert res_2024.vessel_tier == "Regular"
        assert res_2024.fixed_fee_usd == 60000.0
        assert res_2024.capacity_fee_usd == 35000.0 * 1.25  # $43,750

        # 2025 Regular Laden
        res_2025 = registry.calculate_panama_toll(
            beam_m=26.0,
            loa_m=180.0,
            dwt_mt=35000.0,
            loading_condition="LADEN",
            transit_date=datetime.date(2025, 6, 1),
            gatun_elevation_ft=82.0,
            measurement_datetime=datetime.datetime(2025, 5, 31, 12, 0),
        )
        assert res_2025.is_complete is True
        assert res_2025.vessel_tier == "Regular"
        assert res_2025.fixed_fee_usd == 60000.0
        assert res_2025.capacity_fee_usd == 35000.0 * 1.65  # $57,750 (rate increased from 1.25 to 1.65)

    def test_panama_ballast_treatment(self, registry: CanalTariffRegistry):
        """Verify that 85% ballast factor applies to BOTH fixed fee and capacity fee under Item 1010."""
        # Super Bulker (e.g. Navios Victory 77,095 DWT)
        dwt = 77095.0
        beam = 32.24
        loa = 224.98

        # Laden
        res_laden = registry.calculate_panama_toll(
            beam_m=beam,
            loa_m=loa,
            dwt_mt=dwt,
            loading_condition="LADEN",
            transit_date=datetime.date(2024, 4, 1),
            gatun_elevation_ft=80.0,
            measurement_datetime=datetime.datetime(2024, 3, 31, 12, 0),
        )
        assert res_laden.fixed_fee_usd == 100000.0
        assert res_laden.capacity_fee_usd == round(dwt * 1.15, 2)  # 88,659.25

        # Ballast
        res_ballast = registry.calculate_panama_toll(
            beam_m=beam,
            loa_m=loa,
            dwt_mt=dwt,
            loading_condition="BALLAST",
            transit_date=datetime.date(2024, 4, 1),
            gatun_elevation_ft=80.0,
            measurement_datetime=datetime.datetime(2024, 3, 31, 12, 0),
        )
        # Fixed fee discounted to 85%
        assert res_ballast.fixed_fee_usd == 85000.0
        # Capacity rate discounted to 85% (1.15 * 0.85 = 0.9775)
        expected_cap = round(dwt * (1.15 * 0.85), 2)
        assert res_ballast.capacity_fee_usd == expected_cap

    def test_panama_fws_fixed_fee_tiers(self, registry: CanalTariffRegistry):
        """Verify Freshwater Surcharge Item 1065.0001 flat fee tiers."""
        # Vessel <= 38.10 m (125 ft) -> $0
        res_small = registry.calculate_panama_toll(
            beam_m=10.0,
            loa_m=35.0,
            dwt_mt=1000.0,
            loading_condition="LADEN",
            transit_date=datetime.date(2024, 4, 1),
        )
        assert res_small.freshwater_fixed_usd == 0.0

        # Vessel 38.10 m < LOA <= 91.44 m -> $4,000
        res_med = registry.calculate_panama_toll(
            beam_m=20.0,
            loa_m=75.0,
            dwt_mt=5000.0,
            loading_condition="LADEN",
            transit_date=datetime.date(2024, 4, 1),
        )
        assert res_med.freshwater_fixed_usd == 4000.0

        # Vessel > 91.44 m -> $10,000 (all seed bulkers)
        res_large = registry.calculate_panama_toll(
            beam_m=32.26,
            loa_m=228.99,
            dwt_mt=82103.0,
            loading_condition="LADEN",
            transit_date=datetime.date(2024, 4, 1),
        )
        assert res_large.freshwater_fixed_usd == 10000.0

    def test_panama_fws_dynamic_input_requirement(self, registry: CanalTariffRegistry):
        """Verify that FWS variable component requires Gatun level and uses sigmoidal response."""
        # 1. Missing Gatun level -> incomplete calculation
        res_no_gatun = registry.calculate_panama_toll(
            beam_m=32.24,
            loa_m=224.98,
            dwt_mt=77095.0,
            loading_condition="LADEN",
            transit_date=datetime.date(2024, 4, 1),
            gatun_elevation_ft=None,
        )
        assert res_no_gatun.is_complete is False
        assert any("MISSING_OPERATIONAL_GATUN_LEVEL_INPUT" in r for r in res_no_gatun.blocked_reasons)
        assert res_no_gatun.freshwater_variable_usd is None

        # 2. Exact curve center (79.0 ft) -> exactly 5.0%
        # Formula: 0.10 / (1 + exp(0.6 * (79 - 79))) = 0.10 / 2 = 0.05 (5.0%)
        pct_79, err = registry.calculate_panama_freshwater_variable_pct(79.0)
        assert err is None
        assert pct_79 == 5.0

        # 3. High water level (88.5 ft) -> approaching 0%
        pct_high, _ = registry.calculate_panama_freshwater_variable_pct(88.5)
        assert pct_high is not None
        assert pct_high < 0.1  # Very low surcharge at high water level

        # 4. Critical low water level (72.0 ft) -> approaching 10.0%
        pct_low, _ = registry.calculate_panama_freshwater_variable_pct(72.0)
        assert pct_low is not None
        assert pct_low > 9.8  # Near maximum 10% surcharge at extreme drought

    def test_panama_historical_version_expired(self, registry: CanalTariffRegistry):
        """Verify that transit dates in 2026 or later fail closed as historical/unsupported."""
        res_2026 = registry.calculate_panama_toll(
            beam_m=32.24,
            loa_m=224.98,
            dwt_mt=77095.0,
            loading_condition="LADEN",
            transit_date=datetime.date(2026, 3, 1),
        )
        assert res_2026.is_complete is False
        assert any("EXPIRED_OR_UNSUPPORTED_TARIFF_VERSION" in r for r in res_2026.blocked_reasons)


# ---------------------------------------------------------------------------
# 2. Suez Canal Tariff Rules & SCNT Progression
# ---------------------------------------------------------------------------

class TestSuezCanalRules:

    def test_suez_progressive_bracket_boundaries(self, registry: CanalTariffRegistry):
        """Verify progressive tier evaluation across all 7 SCNT tiers for dry bulk."""
        transit_dt = datetime.date(2024, 5, 1)

        # Vessel with SCNT = 150,000 (crosses all 7 tiers)
        res = registry.calculate_suez_toll(
            scnt=150000.0,
            draft_m=16.0,
            beam_m=48.0,
            loading_condition="LADEN",
            transit_date=transit_dt,
        )
        assert res.is_complete is True
        assert len(res.bracket_breakdown) == 7

        b = res.bracket_breakdown
        assert b[0].applicable_scnt == 5000.0
        assert b[0].rate_sdr_per_ton == 10.13
        assert b[0].amount_sdr == round(5000.0 * 10.13, 2)  # 50,650

        assert b[1].applicable_scnt == 5000.0
        assert b[1].rate_sdr_per_ton == 7.74
        assert b[1].amount_sdr == round(5000.0 * 7.74, 2)  # 38,700

        assert b[2].applicable_scnt == 10000.0
        assert b[2].rate_sdr_per_ton == 6.12
        assert b[2].amount_sdr == round(10000.0 * 6.12, 2)  # 61,200

        assert b[3].applicable_scnt == 20000.0
        assert b[3].rate_sdr_per_ton == 2.24
        assert b[3].amount_sdr == round(20000.0 * 2.24, 2)  # 44,800

        assert b[4].applicable_scnt == 30000.0
        assert b[4].rate_sdr_per_ton == 1.97
        assert b[4].amount_sdr == round(30000.0 * 1.97, 2)  # 59,100

        assert b[5].applicable_scnt == 50000.0
        assert b[5].rate_sdr_per_ton == 1.85
        assert b[5].amount_sdr == round(50000.0 * 1.85, 2)  # 92,500

        assert b[6].applicable_scnt == 30000.0  # 150,000 - 120,000 = 30,000
        assert b[6].rate_sdr_per_ton == 1.77
        assert b[6].amount_sdr == round(30000.0 * 1.77, 2)  # 53,100

        expected_base_sdr = 50650 + 38700 + 61200 + 44800 + 59100 + 92500 + 53100  # 400,050
        assert res.base_dues_sdr == expected_base_sdr

    def test_suez_individual_ballast_rates_preserved(self, registry: CanalTariffRegistry):
        """Verify that official individual published ballast bracket rates are preserved exactly."""
        transit_dt = datetime.date(2024, 5, 1)

        res_ballast = registry.calculate_suez_toll(
            scnt=150000.0,
            draft_m=10.0,
            beam_m=48.0,
            loading_condition="BALLAST",
            transit_date=transit_dt,
        )
        assert res_ballast.is_complete is True
        b = res_ballast.bracket_breakdown

        # Individual published ballast rates
        assert b[0].rate_sdr_per_ton == 8.62
        assert b[1].rate_sdr_per_ton == 6.58
        assert b[2].rate_sdr_per_ton == 5.21
        assert b[3].rate_sdr_per_ton == 1.89
        assert b[4].rate_sdr_per_ton == 1.68
        assert b[5].rate_sdr_per_ton == 1.58
        assert b[6].rate_sdr_per_ton == 1.50

        # Verify exact sum without blanket 85% multiplication
        expected_ballast_sdr = (
            5000 * 8.62 + 5000 * 6.58 + 10000 * 5.21 +
            20000 * 1.89 + 30000 * 1.68 + 50000 * 1.58 +
            30000 * 1.50
        )
        assert res_ballast.base_dues_sdr == round(expected_ballast_sdr, 2)

    def test_suez_surcharge_and_mooring_separation(self, registry: CanalTariffRegistry):
        """Verify Circular No. 7/2023 (+5%) and Circular No. 1/2024 ($3,500 USD) are decoupled."""
        transit_dt = datetime.date(2024, 5, 1)

        res = registry.calculate_suez_toll(
            scnt=50000.0,
            draft_m=12.0,
            beam_m=32.0,
            loading_condition="LADEN",
            transit_date=transit_dt,
        )
        assert res.is_complete is True
        # Base dues for 50,000 SCNT:
        # Tier 1: 5000 * 10.13 = 50650
        # Tier 2: 5000 * 7.74  = 38700
        # Tier 3: 10000 * 6.12 = 61200
        # Tier 4: 20000 * 2.24 = 44800
        # Tier 5: 10000 * 1.97 = 19700
        # Total Base SDR = 215,050.00
        assert res.base_dues_sdr == 215050.0
        assert res.surcharge_pct == 5.0
        assert res.surcharge_amount_sdr == 215050.0 * 0.05  # 10,752.50
        assert res.total_dues_sdr == 225802.50
        assert res.mooring_service_usd == 3500.0

        # USD Total: 225,802.50 * 1.3182 + 3500 = 297,652.86 + 3500 = 301,152.86
        expected_usd = round(225802.50 * 1.3182 + 3500.0, 2)
        assert res.total_toll_usd == expected_usd

    def test_suez_escort_tug_rules(self, registry: CanalTariffRegistry):
        """Verify Article 58 escort tug requirements."""
        # Laden SCNT > 90,000 -> 2 tugs
        esc1 = registry.evaluate_suez_escort_rules(scnt=100000.0, draft_m=15.0, beam_m=48.0, loading_condition="LADEN")
        assert esc1["required_tugs_count"] == 2

        # Laden SCNT 75,000 -> 1 tug
        esc2 = registry.evaluate_suez_escort_rules(scnt=75000.0, draft_m=13.0, beam_m=40.0, loading_condition="LADEN")
        assert esc2["required_tugs_count"] == 1

        # Laden SCNT 40,000 with deep draft > 47 ft (15.0 m = 49.2 ft) -> 1 tug
        esc3 = registry.evaluate_suez_escort_rules(scnt=40000.0, draft_m=15.0, beam_m=32.0, loading_condition="LADEN")
        assert esc3["required_tugs_count"] == 1

        # Laden SCNT 40,000 with shallow draft <= 47 ft (12.0 m = 39.4 ft) -> 0 tugs
        esc4 = registry.evaluate_suez_escort_rules(scnt=40000.0, draft_m=12.0, beam_m=32.0, loading_condition="LADEN")
        assert esc4["required_tugs_count"] == 0

        # Ballast Beam > 233 ft (72.0 m = 236.2 ft) -> 2 tugs
        esc5 = registry.evaluate_suez_escort_rules(scnt=80000.0, draft_m=8.0, beam_m=72.0, loading_condition="BALLAST")
        assert esc5["required_tugs_count"] == 2

    def test_suez_missing_scnt_fails_closed(self, registry: CanalTariffRegistry):
        """Verify calculation fails closed if certified SCNT is missing (zero synthetic tonnage)."""
        res = registry.calculate_suez_toll(
            scnt=None,
            draft_m=12.0,
            beam_m=32.0,
            loading_condition="LADEN",
            transit_date=datetime.date(2024, 5, 1),
        )
        assert res.is_complete is False
        assert any("MISSING_CERTIFIED_SCNT" in r for r in res.blocked_reasons)
        assert res.base_dues_sdr is None
        assert res.total_toll_usd is None

    def test_suez_missing_currency_rate_fails_closed(self, registry: CanalTariffRegistry):
        """Verify calculation fails closed if transit-date conversion rate is unavailable."""
        # Date with no registered rate
        res = registry.calculate_suez_toll(
            scnt=40000.0,
            draft_m=12.0,
            beam_m=32.0,
            loading_condition="LADEN",
            transit_date=datetime.date(2024, 9, 15),
        )
        assert res.is_complete is False
        assert any("MISSING_CURRENCY_EXCHANGE_RATE" in r for r in res.blocked_reasons)
        assert res.total_toll_usd is None
        assert res.total_dues_sdr is not None  # SDR calculated, but USD conversion blocked

    def test_suez_partially_verified_evidence_propagation(self, registry: CanalTariffRegistry):
        """Verify that partially verified status is visibly propagated on all Suez outputs."""
        res = registry.calculate_suez_toll(
            scnt=40000.0,
            draft_m=12.0,
            beam_m=32.0,
            loading_condition="LADEN",
            transit_date=datetime.date(2024, 5, 1),
        )
        assert res.evidence_classification == "PARTIALLY_VERIFIED"
        for b in res.bracket_breakdown:
            assert b.evidence_classification == "PARTIALLY_VERIFIED"


# ---------------------------------------------------------------------------
# 3. Currency Architecture Tests
# ---------------------------------------------------------------------------

class TestCurrencyArchitecture:

    def test_currency_conversion_identity(self, currency_service: CurrencyConversionService):
        """Verify identity conversion (USD to USD)."""
        res = currency_service.convert(
            amount=100000.0,
            from_currency="USD",
            to_currency="USD",
            effective_date=datetime.date(2024, 5, 1),
        )
        assert res.is_available is True
        assert res.converted_amount == 100000.0
        assert res.exchange_rate == 1.0

    def test_currency_conversion_sdr_to_usd(self, currency_service: CurrencyConversionService):
        """Verify source-backed conversion with audit trail."""
        res = currency_service.convert(
            amount=10000.0,
            from_currency="SDR",
            to_currency="USD",
            effective_date=datetime.date(2024, 1, 15),
        )
        assert res.is_available is True
        assert res.exchange_rate == 1.3345
        assert res.converted_amount == 13345.0
        assert res.rate_provider == "IMF Daily SDR Valuation"
        assert res.evidence_status == "DIRECTLY_VERIFIED"

    def test_currency_conversion_missing_rate_fails_closed(self, currency_service: CurrencyConversionService):
        """Verify that missing exchange rates do not invent a default value."""
        res = currency_service.convert(
            amount=10000.0,
            from_currency="SDR",
            to_currency="USD",
            effective_date=datetime.date(2024, 2, 28),
        )
        assert res.is_available is False
        assert res.converted_amount is None
        assert "MISSING_EXCHANGE_RATE_FOR_DATE" in (res.failure_reason or "")


# ---------------------------------------------------------------------------
# 4. Database Persistence and Seeding
# ---------------------------------------------------------------------------

class TestDatabasePersistence:

    def test_database_seeding_and_retrieval(self, mem_db: Session):
        """Verify canal tariff records and exchange rates persist to database."""
        cs = CurrencyConversionService(db=mem_db)
        cs.seed_historical_statutory_rates()

        reg = CanalTariffRegistry(db=mem_db, currency_service=cs)
        inserted = reg.seed_statutory_tariffs_to_db()
        assert inserted > 0

        # Query from database
        fixed_record = reg._get_record("ACP", "1010.FS01", datetime.date(2024, 6, 1))
        assert fixed_record is not None
        assert fixed_record["rate_value"] == 100000.0
        assert fixed_record["evidence_classification"] == "DIRECTLY_VERIFIED"
