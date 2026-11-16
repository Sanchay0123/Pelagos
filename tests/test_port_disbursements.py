"""Automated Test Suite for Pelagos Stage F1.4.2.3b: Global Port Disbursement Intelligence Foundation.

Validates:
1. Port disbursement taxonomy and charge categorization (mandatory, conditional, usage-based, optional).
2. Deterministic calculations across pilot ports (Singapore SGSIN, Rotterdam NLRTM, Houston USHOU).
3. Unit calculations: per-100-GT per day, composite GT + cargo handled, LOA dockage, draft pilotage.
4. Mandatory vs conditional logic (e.g. 50% bunkering port dues concession, anchorage grace period).
5. Strict fail-closed boundaries:
   - Unregistered UN/LOCODE fails closed (zero synthetic regional averages).
   - Missing required vessel dimensions (GT, LOA, draft) fails closed.
   - Missing currency exchange rates fails closed.
   - Expired tariff schedules fail closed.
6. Evidence classification propagation (DIRECTLY_VERIFIED, PARTIALLY_VERIFIED).
7. Operational gating: Level 3 operational calculations strictly blocked from commercial TCE workflows.
8. Database persistence and schema auto-creation for PortTariffRecord.
"""

import datetime
from typing import Generator
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from pelagos.currency import CurrencyConversionService
from pelagos.database import ensure_schema_compatibility, init_db
from pelagos.models import Base, PortTariffRecord
from pelagos.registry.port_disbursement_registry import (
    PortDisbursementCalculationResult,
    PortDisbursementLineItem,
    PortDisbursementRegistry,
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
    """Provide a PortDisbursementRegistry seeded with pilot tariffs and currency service."""
    return PortDisbursementRegistry(currency_service=currency_service)


# ---------------------------------------------------------------------------
# 1. Singapore (SGSIN) Deterministic Rules & Bunkering Concessions
# ---------------------------------------------------------------------------

class TestSingaporePortDisbursement:

    def test_singapore_standard_cargo_call(self, port_registry: PortDisbursementRegistry):
        """Verify standard dry bulk cargo call at Singapore (e.g. 82,103 DWT / 43,300 GT Kamsarmax)."""
        gt = 43300.0
        transit_dt = datetime.date(2024, 5, 1)

        res = port_registry.calculate_port_disbursement(
            un_locode="SGSIN",
            vessel_gt=gt,
            vessel_dwt=82103.0,
            loa_m=228.99,
            beam_m=32.26,
            draft_m=14.50,
            purpose_of_call="CARGO_OPERATIONS",
            stay_duration_hours=48.0,  # 2 days
            transit_date=transit_dt,
        )

        assert res.is_complete is True
        assert res.port_name == "Port of Singapore"
        assert res.un_locode == "SGSIN"

        # 43,300 GT -> 433 units of 100 GT
        # Rate: SGD 4.50 per 100 GT per 24 hrs
        # 48 hrs = 2 days -> 433 * 4.50 * 2 = SGD 3,897.00
        port_dues_item = next(i for i in res.items if i.tariff_identifier == "SGSIN-PORT-DUES-CARGO-2023")
        assert port_dues_item.amount_local == 3897.00
        assert port_dues_item.local_currency == "SGD"
        assert port_dues_item.applicability_type == "MANDATORY"
        assert port_dues_item.evidence_classification == "DIRECTLY_VERIFIED"

        # Converted to USD at MAS rate (0.7350 on 2024-05-01): 3,897 * 0.735 = $2,864.30
        assert port_dues_item.amount_usd == round(3897.00 * 0.7350, 2)

        # Pilotage: SGD 420/hr * 2 hrs = SGD 840.00 -> 840 * 0.735 = $617.40
        pilot_item = next(i for i in res.items if i.tariff_identifier == "SGSIN-PSA-PILOTAGE-2023")
        assert pilot_item.amount_local == 840.00
        assert pilot_item.evidence_classification == "PARTIALLY_VERIFIED"

        assert res.total_port_costs_usd == round((3897.00 + 840.00) * 0.7350, 2)

    def test_singapore_bunkering_only_concession(self, port_registry: PortDisbursementRegistry):
        """Verify 50% statutory port dues concession for bunkering-only calls <= 24 hours."""
        gt = 43300.0
        transit_dt = datetime.date(2024, 5, 1)

        res = port_registry.calculate_port_disbursement(
            un_locode="SGSIN",
            vessel_gt=gt,
            vessel_dwt=82103.0,
            loa_m=228.99,
            beam_m=32.26,
            draft_m=14.50,
            purpose_of_call="BUNKERING_ONLY",
            stay_duration_hours=18.0,  # <= 24 hrs
            transit_date=transit_dt,
        )

        assert res.is_complete is True
        # Standard cargo tariff should NOT be applied
        assert not any(i.tariff_identifier == "SGSIN-PORT-DUES-CARGO-2023" for i in res.items)

        # Concession tariff applies: SGD 2.25 per 100 GT for 1 day = 433 * 2.25 = SGD 974.25
        bunker_item = next(i for i in res.items if i.tariff_identifier == "SGSIN-PORT-DUES-BUNKER-2023")
        assert bunker_item.amount_local == 974.25
        assert bunker_item.applicability_type == "CONDITIONAL"

    def test_singapore_anchorage_grace_period(self, port_registry: PortDisbursementRegistry):
        """Verify anchorage charges trigger only when duration exceeds 24-hr grace period."""
        gt = 43300.0
        transit_dt = datetime.date(2024, 5, 1)

        # Call within 24 hrs grace period -> no anchorage dues
        res_short = port_registry.calculate_port_disbursement(
            un_locode="SGSIN",
            vessel_gt=gt,
            vessel_dwt=82103.0,
            loa_m=228.99,
            beam_m=32.26,
            draft_m=14.50,
            purpose_of_call="ANCHORAGE_WAITING",
            stay_duration_hours=20.0,
            transit_date=transit_dt,
        )
        assert not any(i.tariff_identifier == "SGSIN-ANCHORAGE-DUES-2023" for i in res_short.items)

        # Stay of 72 hrs (3 days) -> 1 day free, 2 days chargeable at SGD 1.50 / 100 GT
        res_long = port_registry.calculate_port_disbursement(
            un_locode="SGSIN",
            vessel_gt=gt,
            vessel_dwt=82103.0,
            loa_m=228.99,
            beam_m=32.26,
            draft_m=14.50,
            purpose_of_call="ANCHORAGE_WAITING",
            stay_duration_hours=72.0,
            transit_date=transit_dt,
        )
        anc_item = next(i for i in res_long.items if i.tariff_identifier == "SGSIN-ANCHORAGE-DUES-2023")
        # 433 * 1.50 * 2 = SGD 1,299.00
        assert anc_item.amount_local == 1299.00


# ---------------------------------------------------------------------------
# 2. Rotterdam (NLRTM) Composite Tonnage + Cargo Formulas
# ---------------------------------------------------------------------------

class TestRotterdamPortDisbursement:

    def test_rotterdam_composite_seaport_dues(self, port_registry: PortDisbursementRegistry):
        """Verify Rotterdam composite tariff (GT component + cargo handled component + waste fee)."""
        gt = 43300.0
        cargo_tons = 65000.0
        transit_dt = datetime.date(2024, 5, 1)

        res = port_registry.calculate_port_disbursement(
            un_locode="NLRTM",
            vessel_gt=gt,
            vessel_dwt=82103.0,
            loa_m=228.99,
            beam_m=32.26,
            draft_m=14.50,
            purpose_of_call="CARGO_OPERATIONS",
            stay_duration_hours=72.0,
            cargo_handled_tons=cargo_tons,
            transit_date=transit_dt,
        )

        assert res.is_complete is True
        assert res.port_name == "Port of Rotterdam"

        # 1. GT Component: 43,300 * EUR 0.320 = EUR 13,856.00
        gt_item = next(i for i in res.items if i.tariff_identifier == "NLRTM-SEAPORT-DUES-GT-2024")
        assert gt_item.amount_local == 13856.00
        assert gt_item.local_currency == "EUR"
        assert gt_item.applicability_type == "MANDATORY"

        # 2. Cargo Component: 65,000 * EUR 0.450 = EUR 29,250.00
        cargo_item = next(i for i in res.items if i.tariff_identifier == "NLRTM-SEAPORT-DUES-CARGO-2024")
        assert cargo_item.amount_local == 29250.00
        assert cargo_item.applicability_type == "USAGE_BASED"

        # 3. Fixed SAB Waste Fee: EUR 450.00
        waste_item = next(i for i in res.items if i.tariff_identifier == "NLRTM-SAB-WASTE-FEE-2024")
        assert waste_item.amount_local == 450.00

        # 4. Pilotage S-Component: EUR 380.00
        pilot_item = next(i for i in res.items if i.tariff_identifier == "NLRTM-LOODS-PILOTAGE-S-2024")
        assert pilot_item.amount_local == 380.00

        # Sum of converted line items:
        expected_usd = round(gt_item.amount_usd + cargo_item.amount_usd + waste_item.amount_usd + pilot_item.amount_usd, 2)
        assert res.total_port_costs_usd == expected_usd

    def test_rotterdam_missing_cargo_handled_fails_closed(self, port_registry: PortDisbursementRegistry):
        """Verify Rotterdam calculation fails closed if cargo handled is omitted."""
        res = port_registry.calculate_port_disbursement(
            un_locode="NLRTM",
            vessel_gt=43300.0,
            vessel_dwt=82103.0,
            loa_m=228.99,
            beam_m=32.26,
            draft_m=14.50,
            cargo_handled_tons=None,  # Missing cargo volume
            transit_date=datetime.date(2024, 5, 1),
        )
        assert res.is_complete is False
        assert any("MISSING_CARGO_HANDLED" in r for r in res.blocked_reasons)
        assert res.total_port_costs_usd is None


# ---------------------------------------------------------------------------
# 3. Houston (USHOU) LOA Dockage & Draft Pilotage Formulas
# ---------------------------------------------------------------------------

class TestHoustonPortDisbursement:

    def test_houston_loa_and_draft_rules(self, port_registry: PortDisbursementRegistry):
        """Verify Houston Harbor fee, dockage per foot, security surcharge, and draft pilotage."""
        loa_m = 224.98  # Navios Victory LOA = 738.12 ft
        beam_m = 32.24  # Navios Victory Beam = 105.77 ft
        draft_m = 14.20  # Draft = 46.59 ft
        transit_dt = datetime.date(2024, 5, 1)

        res = port_registry.calculate_port_disbursement(
            un_locode="USHOU",
            vessel_gt=41342.0,
            vessel_dwt=77095.0,
            loa_m=loa_m,
            beam_m=beam_m,
            draft_m=draft_m,
            stay_duration_hours=48.0,  # 2 days
            transit_date=transit_dt,
        )

        assert res.is_complete is True
        assert res.port_name == "Port of Houston"

        loa_ft = loa_m * 3.28084  # 738.123
        # 1. Harbor Fee: LOA > 500 ft -> $550.00
        harbor_item = next(i for i in res.items if i.tariff_identifier == "USHOU-PHA-HARBOR-FEE-2024")
        assert harbor_item.amount_local == 550.00
        assert harbor_item.amount_usd == 550.00

        # 2. Dockage: LOA_ft * $3.85 * 2 days
        expected_dockage = round(loa_ft * 3.85 * 2, 2)
        dockage_item = next(i for i in res.items if i.tariff_identifier == "USHOU-PHA-DOCKAGE-2024")
        assert dockage_item.amount_local == expected_dockage

        # 3. Security: 10% of dockage
        expected_security = round(expected_dockage * 0.10, 2)
        sec_item = next(i for i in res.items if i.tariff_identifier == "USHOU-PHA-SECURITY-2024")
        assert sec_item.amount_local == expected_security

        # 4. Pilot Draft Charge: Draft_ft * $85.50
        draft_ft = draft_m * 3.28084
        expected_draft_charge = round(draft_ft * 85.50, 2)
        pilot_draft_item = next(i for i in res.items if i.tariff_identifier == "USHOU-PILOT-DRAFT-2024")
        assert pilot_draft_item.amount_local == expected_draft_charge

        # 5. Pilot Unit Charge: ((LOA * Beam) / 100) * $12.50
        beam_ft = beam_m * 3.28084
        pilot_units = (loa_ft * beam_ft) / 100.0
        expected_unit_charge = round(pilot_units * 12.50, 2)
        pilot_unit_item = next(i for i in res.items if i.tariff_identifier == "USHOU-PILOT-UNIT-2024")
        assert pilot_unit_item.amount_local == expected_unit_charge


# ---------------------------------------------------------------------------
# 4. Governance & Fail-Closed Boundaries
# ---------------------------------------------------------------------------

class TestPortDisbursementGovernance:

    def test_unregistered_port_fails_closed(self, port_registry: PortDisbursementRegistry):
        """Verify querying an unregistered port fails closed with PORT_NOT_REGISTERED."""
        res = port_registry.calculate_port_disbursement(
            un_locode="BRSSZ",  # Port of Santos (unregistered in pilot)
            vessel_gt=40000.0,
            vessel_dwt=75000.0,
            loa_m=220.0,
            beam_m=32.0,
            draft_m=13.0,
            transit_date=datetime.date(2024, 5, 1),
        )
        assert res.is_complete is False
        assert any("PORT_NOT_REGISTERED" in r for r in res.blocked_reasons)
        assert res.total_port_costs_usd is None

    def test_missing_required_vessel_dimension_fails_closed(self, port_registry: PortDisbursementRegistry):
        """Verify missing GT fails closed for GT-based ports (Singapore)."""
        res = port_registry.calculate_port_disbursement(
            un_locode="SGSIN",
            vessel_gt=None,  # Missing GT
            vessel_dwt=75000.0,
            loa_m=220.0,
            beam_m=32.0,
            draft_m=13.0,
            transit_date=datetime.date(2024, 5, 1),
        )
        assert res.is_complete is False
        assert any("MISSING_VESSEL_GT" in r for r in res.blocked_reasons)

    def test_missing_currency_rate_fails_closed(self, port_registry: PortDisbursementRegistry):
        """Verify unseeded currency date fails closed."""
        res = port_registry.calculate_port_disbursement(
            un_locode="SGSIN",
            vessel_gt=40000.0,
            vessel_dwt=75000.0,
            loa_m=220.0,
            beam_m=32.0,
            draft_m=13.0,
            transit_date=datetime.date(2024, 11, 20),  # No exchange rate seeded for this date
        )
        assert res.is_complete is False
        assert any("MISSING_CURRENCY_EXCHANGE_RATE" in r for r in res.blocked_reasons)

    def test_operational_mode_blocks_preliminary_estimates(self, port_registry: PortDisbursementRegistry):
        """Verify Level 3 operational mode strictly blocks preliminary disbursement estimates."""
        res = port_registry.calculate_port_disbursement(
            un_locode="SGSIN",
            vessel_gt=40000.0,
            vessel_dwt=75000.0,
            loa_m=220.0,
            beam_m=32.0,
            draft_m=13.0,
            transit_date=datetime.date(2024, 5, 1),
            operational_mode=True,  # Operational mode requested
        )
        assert res.is_complete is False
        assert any("OPERATIONAL_PORT_DISBURSEMENT_BLOCKED" in r for r in res.blocked_reasons)
        assert res.total_port_costs_usd is None

    def test_database_persistence_and_retrieval(self, mem_db: Session):
        """Verify PortTariffRecord model persists to database and initializes cleanly."""
        reg = PortDisbursementRegistry(db=mem_db)
        inserted = reg.seed_statutory_tariffs_to_db()
        assert inserted > 0

        # Query database directly
        stmt = select(PortTariffRecord).where(PortTariffRecord.un_locode == "SGSIN")
        records = mem_db.scalars(stmt).all()
        assert len(records) > 0
        assert any(r.tariff_identifier == "SGSIN-PORT-DUES-CARGO-2023" for r in records)
