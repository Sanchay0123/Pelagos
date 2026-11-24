"""
Unit and Integration Tests for Pelagos Stage F1.5.2-B: Controlled Research Pilot Integration.

Covers the six authorized research pilot workstreams:
  - Workstream A: NAVIOS VICTORY controlled persistence (IMO 9713430), ClassNK provenance,
                  verified hydrostatics (ballast displacement strictly None), SFOC curve association.
                  Verifies Iris Bliss and Berge Galdhopiggen are NOT seeded.
  - Workstream B: JPY historical currency support (Bank of Japan preferred, Fed H.10 corroborating,
                  4-calendar-day lookback, Golden Week handling, stale-rate safety, Decimal precision).
  - Workstream C: Yokohama port tariff registry (Statutory Ordinance No. 37 port dues ¥2.70/GT,
                  public wharfage ¥7.10/GT/12h, Tokyo Bay statutory pilotage formula, separate harbor
                  docking pilotage formula; towage/lines excluded from statutory dues).
  - Workstream D: Antimeridian-safe routing (Decouple nautical distance from 2D GeoJSON rendering;
                  supports antimeridian crossing via MultiLineString while preserving ~9,363 NM distance;
                  legacy fail-closed safety for supports_segments=False).
  - Workstream E: Explicit research bunker assumptions (4-tier classification: OBSERVED_PORT_PRICE,
                  USER_SUPPLIED_ASSUMPTION, RESEARCH_COMPOSITE_BENCHMARK, UNAVAILABLE; zero silent fallback;
                  operational mode lock).
  - Workstream F: Controlled research assessment (End-to-end USHOU -> Panama -> JPYOK assessment,
                  freight, disbursements, fuel, conventional TCE; is_fully_validated=False and
                  is_ready_for_operations=False strictly enforced; ballast & operational blocking).
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any, Dict, List

from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from pelagos.currency import (
    CurrencyConversionService,
    CurrencyEvidenceClassification,
    CurrencyExchangeRate,
    PROVIDER_BOJ,
    PROVIDER_FED,
    PROVIDER_MAX_LOOKBACK_CALENDAR_DAYS,
    resolve_provider_max_lookback,
)
from pelagos.database import Base, ensure_schema_compatibility
from pelagos.models import Vessel, VesselAttributeProvenance, VesselHydrostatics
from pelagos.optimization.research_voyage_assessment import (
    EpistemicEvidenceClassification,
    ResearchVoyageAssessmentService,
    ResearchVoyageInput,
)
from pelagos.registry.engine_sfoc_registry import EngineSfocRegistry
from pelagos.registry.port_disbursement_registry import PortDisbursementRegistry
from pelagos.registry.vessel_registry import VesselRegistry
from pelagos.routing.bunker import (
    BunkerEvidenceClassification,
    ResearchCompositeBenchmark,
    UserSuppliedBunkerAssumption,
    resolve_research_bunker_price,
)
from pelagos.routing.service import MaritimeRoutingEngine
from pelagos.routing.schemas import RoutingRequest, RoutingStatus


@pytest.fixture
def isolated_db():
    """Isolated in-memory SQLite database for test execution."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    ensure_schema_compatibility(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


# ===========================================================================
# WORKSTREAM A: NAVIOS VICTORY Controlled Persistence & Hydrostatics
# ===========================================================================

class TestWorkstreamAVesselPersistence:
    """Verifies NAVIOS VICTORY controlled persistence and unapproved vessel blocking."""

    def test_navios_victory_seeding_and_provenance(self, isolated_db):
        """Verify NAVIOS VICTORY is seeded with ClassNK provenance and verified hydrostatics."""
        reg = VesselRegistry(db=isolated_db)
        res = reg.seed_navios_victory_pilot()

        assert res["imo"] == "9713430"
        assert res["vessel_name"] == "NAVIOS VICTORY"
        assert res["provenance_records_count"] >= 20
        assert res["hydrostatics_created"] is True
        assert res["ballast_displacement_available"] is False

        # Verify DB vessel record
        stmt = select(Vessel).where(Vessel.imo == "9713430")
        vessel = isolated_db.scalars(stmt).first()
        assert vessel is not None
        assert vessel.vessel_class == "Panamax"
        assert vessel.flag_country == "Panama"
        assert vessel.gross_tonnage == 40937.0
        assert vessel.dwt_tonnage == 77095.0
        assert vessel.length_meters == 224.98
        assert vessel.width_meters == 32.24
        assert vessel.draft_max_meters == 14.47
        assert vessel.engine_model == "MAN B&W 6S60ME-C"

        # Verify Hydrostatics
        h_stmt = select(VesselHydrostatics).where(VesselHydrostatics.vessel_id == vessel.id)
        hydro = isolated_db.scalars(h_stmt).first()
        assert hydro is not None
        assert hydro.lightweight_tonnage_mt == 12915.0
        assert hydro.laden_displacement_mt == 90010.0
        assert hydro.ballast_displacement_mt is None  # Strictly uncertified / missing

        # Verify Ballast Eligibility Check
        eligible, reason = reg.is_vessel_eligible_for_ballast_assessment("9713430")
        assert eligible is False
        assert "MISSING_BALLAST_HYDROSTATICS" in reason

    def test_unapproved_vessels_not_seeded(self, isolated_db):
        """Verify that candidate vessels with unverified particulars are NOT seeded."""
        reg = VesselRegistry(db=isolated_db)
        reg.seed_navios_victory_pilot()

        # Iris Bliss (IMO 9849186) and Berge Galdhopiggen (IMO 9700378)
        unapproved_imos = ["9849186", "9700378"]
        for imo in unapproved_imos:
            stmt = select(Vessel).where(Vessel.imo == imo)
            v = isolated_db.scalars(stmt).first()
            assert v is None, f"Unapproved vessel IMO {imo} must NOT be seeded"

    def test_sfoc_curve_association(self, isolated_db):
        """Verify MAN B&W 6S60ME-C SFOC curves are correctly associated."""
        v_reg = VesselRegistry(db=isolated_db)
        v_reg.seed_navios_victory_pilot()

        sfoc_reg = EngineSfocRegistry(db=isolated_db)
        # In research mode, resolves the nominal curve for Navios Victory
        curve_match = sfoc_reg.resolve_curve_for_vessel("9713430", operational_mode=False)
        assert curve_match is not None
        assert "MAN_S60ME" in curve_match["curve_code"]


# ===========================================================================
# WORKSTREAM B: JPY Historical Currency Support
# ===========================================================================

class TestWorkstreamBCurrencyJPY:
    """Verifies Bank of Japan & Fed H.10 JPY currency rates, lookbacks, and safety gates."""

    def test_jpy_direct_and_inverse_conversion(self, isolated_db):
        """Verify authentic USD/JPY conversion at 157.80 on 2024-05-01 with Decimal precision."""
        curr_svc = CurrencyConversionService(db=isolated_db)
        curr_svc.seed_historical_statutory_rates()

        # USD -> JPY: 1,000 USD = 157,800.0 JPY
        res_jpy = curr_svc.convert(
            amount=1000.0,
            from_currency="USD",
            to_currency="JPY",
            effective_date=datetime.date(2024, 5, 1),
            rate_provider=PROVIDER_BOJ,
        )
        assert res_jpy.is_available is True
        assert res_jpy.converted_amount == 157800.0
        assert res_jpy.exchange_rate == 157.80
        assert res_jpy.rate_provider == PROVIDER_BOJ

        # JPY -> USD: 157,800 JPY ≈ 1,000.00 USD
        res_usd = curr_svc.convert(
            amount=157800.0,
            from_currency="JPY",
            to_currency="USD",
            effective_date=datetime.date(2024, 5, 1),
            rate_provider=PROVIDER_BOJ,
        )
        assert res_usd.is_available is True
        assert abs(res_usd.converted_amount - 1000.0) < 0.01

    def test_jpy_golden_week_lookback(self, isolated_db):
        """Verify Japanese Golden Week holiday dates resolve to May 2 fixing within 4-day limit."""
        curr_svc = CurrencyConversionService(db=isolated_db)
        curr_svc.seed_historical_statutory_rates()

        # May 3, 2024 (Constitution Memorial Day) looks back 1 calendar day to May 2 (153.60)
        res_may3 = curr_svc.convert(
            amount=1000.0,
            from_currency="USD",
            to_currency="JPY",
            effective_date=datetime.date(2024, 5, 3),
            rate_provider=PROVIDER_BOJ,
        )
        assert res_may3.is_available is True
        assert res_may3.converted_amount == 153600.0
        assert res_may3.actual_observation_date == datetime.date(2024, 5, 2)
        assert res_may3.observation_lag_days == 1

        # May 6, 2024 (Children's Day Observed) looks back 4 calendar days to May 2 (153.60)
        res_may6 = curr_svc.convert(
            amount=1000.0,
            from_currency="USD",
            to_currency="JPY",
            effective_date=datetime.date(2024, 5, 6),
            rate_provider=PROVIDER_BOJ,
        )
        assert res_may6.is_available is True
        assert res_may6.converted_amount == 153600.0
        assert res_may6.actual_observation_date == datetime.date(2024, 5, 2)
        assert res_may6.observation_lag_days == 4

        # May 7, 2024 has its own official BOJ fixing (154.10)
        res_may7 = curr_svc.convert(
            amount=1000.0,
            from_currency="USD",
            to_currency="JPY",
            effective_date=datetime.date(2024, 5, 7),
            rate_provider=PROVIDER_BOJ,
        )
        assert res_may7.is_available is True
        assert res_may7.converted_amount == 154100.0
        assert res_may7.actual_observation_date == datetime.date(2024, 5, 7)
        assert res_may7.observation_lag_days == 0

    def test_jpy_stale_rate_rejection(self, isolated_db):
        """Verify queries exceeding 4 calendar days fail closed."""
        curr_svc = CurrencyConversionService(db=isolated_db)
        curr_svc.seed_historical_statutory_rates()

        # 2024-05-15 is 8 days after 2024-05-07; exceeds 4-day lookback limit
        res_stale = curr_svc.convert(
            amount=1000.0,
            from_currency="USD",
            to_currency="JPY",
            effective_date=datetime.date(2024, 5, 15),
            rate_provider=PROVIDER_BOJ,
        )
        assert res_stale.is_available is False
        assert "EXPIRED_OR_STALE_CURRENCY_OBSERVATION" in res_stale.failure_reason

    def test_lookback_registry_configuration(self):
        """Verify BOJ and Fed H.10 max lookbacks are strictly 4 calendar days."""
        assert PROVIDER_MAX_LOOKBACK_CALENDAR_DAYS[PROVIDER_BOJ] == 4
        assert PROVIDER_MAX_LOOKBACK_CALENDAR_DAYS[PROVIDER_FED] == 4
        assert resolve_provider_max_lookback(PROVIDER_BOJ, "JPY") == 4
        assert resolve_provider_max_lookback(PROVIDER_FED, "JPY") == 4


# ===========================================================================
# WORKSTREAM C: Yokohama Port Tariff Registry
# ===========================================================================

class TestWorkstreamCYokohamaTariffs:
    """Verifies City of Yokohama Ordinance No. 37 and Tokyo Bay pilotage formula evaluation."""

    def test_yokohama_full_statutory_disbursement(self, isolated_db):
        """Verify Port of Yokohama disbursement evaluation for NAVIOS VICTORY."""
        curr_svc = CurrencyConversionService(db=isolated_db)
        curr_svc.seed_historical_statutory_rates()

        port_reg = PortDisbursementRegistry(db=isolated_db, currency_service=curr_svc)
        port_reg.seed_statutory_tariffs_to_db()

        # Navios Victory: GT=40,937, Draft=14.50m, Stay=72 hours (3 days)
        res = port_reg.calculate_port_disbursement(
            un_locode="JPYOK",
            vessel_gt=40937.0,
            vessel_dwt=77095.0,
            loa_m=224.98,
            beam_m=32.24,
            draft_m=14.50,
            purpose_of_call="CARGO_OPERATIONS",
            stay_duration_hours=72.0,
            transit_date=datetime.date(2024, 5, 1),
            operational_mode=False,
        )

        assert res.is_complete is True
        assert res.total_port_costs_usd is not None
        assert len(res.items) == 4

        # 1. Port Entry Dues: 40,937 GT * 2.70 JPY = 110,529.90 JPY -> $700.44 USD (at 157.80)
        item_dues = next(i for i in res.items if i.tariff_identifier == "JPYOK-PORT-DUES-2024")
        assert item_dues.amount_local == 110529.90
        assert item_dues.local_currency == "JPY"
        assert item_dues.amount_usd == 700.44

        # 2. Public Wharfage: 40,937 GT * 7.10 JPY * 6 blocks (72h) = 1,743,916.20 JPY -> $11,051.43 USD
        item_wharf = next(i for i in res.items if i.tariff_identifier == "JPYOK-PUBLIC-WHARFAGE-2024")
        assert item_wharf.amount_local == 1743916.20
        assert item_wharf.amount_usd == 11051.43

        # 3. Tokyo Bay Pilotage Formula:
        # Base: 45,800 + 4,580 * (ceil((40937 - 1000)/1000) [40] + ceil((14.5 - 3.0)/0.30) [39])
        # Total units: 79 units -> 45,800 + 4,580 * 79 = 407,620 JPY -> $2,583.14 USD
        item_bay = next(i for i in res.items if i.tariff_identifier == "JPYOK-TOKYO-BAY-PILOT-2024")
        assert item_bay.amount_local == 407620.0
        assert item_bay.amount_usd == 2583.14

        # 4. Yokohama Harbor Pilotage Formula:
        # Base: 27,500 + 2,750 * 79 units = 244,750 JPY -> $1,551.01 USD
        item_harbor = next(i for i in res.items if i.tariff_identifier == "JPYOK-HARBOR-PILOT-2024")
        assert item_harbor.amount_local == 244750.0
        assert item_harbor.amount_usd == 1551.01

        # Sum of items: 700.44 + 11051.43 + 2583.14 + 1551.01 = $15,886.02 USD
        assert res.total_port_costs_usd == 15886.02

    def test_operational_mode_blocks_port_disbursements(self, isolated_db):
        """Verify Level 3 operational mode fails closed."""
        port_reg = PortDisbursementRegistry(db=isolated_db)
        res = port_reg.calculate_port_disbursement(
            un_locode="JPYOK",
            vessel_gt=40937.0,
            vessel_dwt=77095.0,
            loa_m=224.98,
            beam_m=32.24,
            draft_m=14.50,
            operational_mode=True,
        )
        assert res.is_complete is False
        assert any("OPERATIONAL_PORT_DISBURSEMENT_BLOCKED" in b for b in res.blocked_reasons)


# ===========================================================================
# WORKSTREAM D: Antimeridian-Safe Routing
# ===========================================================================

class TestWorkstreamDRouting:
    """Verifies antimeridian-safe routing across the Pacific with GeoJSON MultiLineString."""

    def test_transpacific_routing_segmentation(self):
        """Verify USHOU -> JPYOK returns CALCULATED, preserves ~9,363 NM, and generates MultiLineString."""
        engine = MaritimeRoutingEngine()
        req = RoutingRequest(
            origin="USHOU",
            destination="JPYOK",
            routing_mode="marnet",
            canals=["panama"],
            vessel_gt=40937.0,
            vessel_draft=14.50,
            vessel_beam=32.24,
            vessel_loa=224.98,
        )
        assert req.supports_segments is True  # Default must be True

        result = engine.calculate_route(req)
        assert result.status == RoutingStatus.CALCULATED
        assert result.summary is not None
        assert 9300.0 < result.summary.distance_nm < 9450.0
        assert "panama" in result.summary.canals_transited

        # GeoJSON rendering
        geojson = result.to_geojson()
        assert geojson["type"] == "MultiLineString"
        assert len(geojson["coordinates"]) >= 2  # Disjoint segments across the antimeridian

    def test_standard_route_emits_linestring(self):
        """Verify standard route without antimeridian crossing emits LineString."""
        engine = MaritimeRoutingEngine()
        req = RoutingRequest(
            origin="USHOU",
            destination="NLRTM",
            routing_mode="marnet",
            vessel_gt=40937.0,
            vessel_draft=14.50,
        )
        result = engine.calculate_route(req)
        assert result.status == RoutingStatus.CALCULATED
        geojson = result.to_geojson()
        assert geojson["type"] == "LineString"

    def test_legacy_fail_closed_supports_segments_false(self):
        """Verify explicit supports_segments=False preserves legacy fail-closed safety."""
        engine = MaritimeRoutingEngine()
        req = RoutingRequest(
            origin="USHOU",
            destination="JPYOK",
            routing_mode="marnet",
            canals=["panama"],
            supports_segments=False,
        )
        result = engine.calculate_route(req)
        assert result.status == RoutingStatus.UNSUPPORTED_ROUTE
        assert result.summary is None


# ===========================================================================
# WORKSTREAM E: Explicit Research Bunker Assumptions
# ===========================================================================

class TestWorkstreamEBunkerAssumptions:
    """Verifies 4-tier bunker classification, no silent fallback, and operational locks."""

    def test_user_supplied_bunker_assumption(self):
        """Verify user-supplied assumption resolves with USER_SUPPLIED_ASSUMPTION tier."""
        user_assump = UserSuppliedBunkerAssumption(
            price_usd_per_mt=625.50,
            fuel_grade="VLSFO",
            as_of_date=datetime.date(2024, 5, 1),
            port_unlocode="USHOU",
            user_label="Fixture Negotiation Recap",
        )
        res = resolve_research_bunker_price(
            port_unlocode="USHOU",
            fuel_grade="VLSFO",
            as_of_date=datetime.date(2024, 5, 1),
            user_assumption=user_assump,
            allow_research_composite=False,
            operational_mode=False,
        )
        assert res.is_available is True
        assert res.price_usd_per_mt == 625.50
        assert res.evidence_classification == BunkerEvidenceClassification.USER_SUPPLIED_ASSUMPTION
        assert "Fixture Negotiation Recap" in res.source_name

    def test_research_composite_benchmark(self):
        """Verify explicit research composite benchmark resolution."""
        composite = ResearchCompositeBenchmark(
            benchmark_name="G20 Global VLSFO Average",
            price_usd_per_mt=640.25,
            as_of_date=datetime.date(2024, 5, 1),
            publisher_or_source="Ship & Bunker Macro Index",
            disclaimer="Macro economic benchmark; not delivered physical price.",
        )
        res = resolve_research_bunker_price(
            port_unlocode="USHOU",
            fuel_grade="VLSFO",
            as_of_date=datetime.date(2024, 5, 1),
            allow_research_composite=True,
            research_composite=composite,
            operational_mode=False,
        )
        assert res.is_available is True
        assert res.price_usd_per_mt == 640.25
        assert res.evidence_classification == BunkerEvidenceClassification.RESEARCH_COMPOSITE_BENCHMARK
        assert len(res.disclaimers) >= 2

    def test_no_silent_fallback_fails_closed(self):
        """Verify that when no assumption or composite is supplied, resolution fails closed."""
        res = resolve_research_bunker_price(
            port_unlocode="USHOU",
            fuel_grade="VLSFO",
            as_of_date=datetime.date(2024, 5, 1),
            user_assumption=None,
            allow_research_composite=False,
            operational_mode=False,
        )
        assert res.is_available is False
        assert res.price_usd_per_mt is None
        assert res.evidence_classification == BunkerEvidenceClassification.UNAVAILABLE
        assert "NO_BUNKER_PRICE_AVAILABLE" in res.failure_reason

    def test_operational_mode_blocks_research_composite(self):
        """Verify operational mode strictly rejects research composites."""
        composite = ResearchCompositeBenchmark(
            benchmark_name="G20 Global VLSFO Average",
            price_usd_per_mt=640.25,
            as_of_date=datetime.date(2024, 5, 1),
            publisher_or_source="Ship & Bunker Macro Index",
            disclaimer="Macro economic benchmark; not delivered physical price.",
        )
        res = resolve_research_bunker_price(
            port_unlocode="USHOU",
            fuel_grade="VLSFO",
            as_of_date=datetime.date(2024, 5, 1),
            allow_research_composite=True,
            research_composite=composite,
            operational_mode=True,  # Operational mode
        )
        assert res.is_available is False
        assert "OPERATIONAL_BUNKER_PRICE_BLOCKED" in res.failure_reason


# ===========================================================================
# WORKSTREAM F: Controlled Research Voyage Assessment
# ===========================================================================

class TestWorkstreamFControlledVoyageAssessment:
    """Verifies end-to-end Level 2 voyage assessment, financial TCE, and governance boundaries."""

    def test_complete_level2_voyage_assessment(self, isolated_db):
        """Verify complete research assessment for USHOU -> Panama -> JPYOK on NAVIOS VICTORY."""
        curr_svc = CurrencyConversionService(db=isolated_db)
        curr_svc.seed_historical_statutory_rates()

        port_reg = PortDisbursementRegistry(db=isolated_db, currency_service=curr_svc)
        port_reg.seed_statutory_tariffs_to_db()

        v_reg = VesselRegistry(db=isolated_db)
        v_reg.seed_navios_victory_pilot()

        svc = ResearchVoyageAssessmentService(
            db=isolated_db,
            currency_service=curr_svc,
            port_registry=port_reg,
        )

        user_bunker = UserSuppliedBunkerAssumption(
            price_usd_per_mt=625.50,
            fuel_grade="VLSFO",
            as_of_date=datetime.date(2024, 5, 1),
            port_unlocode="USHOU",
            user_label="Exploratory Fixture Recap",
        )

        inp = ResearchVoyageInput(
            origin_unlocode="USHOU",
            destination_unlocode="JPYOK",
            vessel_imo="9713430",
            transit_date=datetime.date(2024, 5, 1),
            cargo_tonnes=60000.0,
            freight_rate_override_usd_per_mt=61.80,  # USDA AMS authentic rate
            user_bunker_assumption=user_bunker,
            commission_rate=0.0375,
            steaming_speed_knots=12.5,
            operational_mode=False,
        )

        res = svc.evaluate_research_voyage(inp)

        # Nautical & Voyage metrics
        assert res.route_distance_nm > 9300.0
        assert res.canals_transited == ["panama"]
        assert res.antimeridian_crossing_detected is True
        assert res.laden_sea_days > 31.0
        assert res.total_voyage_days > 40.0

        # Financial Revenue
        assert res.gross_freight_usd == 3708000.00
        assert res.commission_deductions_usd == 139050.00

        # Disbursements & Canal Tolls
        assert res.origin_port_disbursements_usd > 15000.0
        assert res.destination_port_disbursements_usd > 15000.0
        assert res.panama_canal_tolls_usd > 200000.0

        # Fuel & Bunker Expense
        assert 600.0 < res.laden_fuel_consumption_mt < 650.0
        assert res.bunker_fuel_costs_usd is not None
        assert res.bunker_fuel_costs_usd > 350000.0

        # Conventional TCE
        assert res.net_voyage_revenue_usd is not None
        assert res.tce_daily_rate_usd is not None
        assert res.tce_daily_rate_usd > 60000.0  # ~$72,111 / day

        # Epistemic & Readiness Governance (INVIOLATE BOUNDARIES)
        assert res.is_fully_validated is False
        assert res.is_ready_for_operations is False
        assert res.governance_tier == "LEVEL_2_RESEARCH_ONLY"
        assert res.ballast_assessment_eligible is False

        # Component Line Items
        assert len(res.components_breakdown) >= 6
        assert any(c.name == "Ballast Hydrostatics" for c in res.components_breakdown)

    def test_missing_bunker_price_fails_closed(self, isolated_db):
        """Verify assessment without bunker price fails closed on financial TCE."""
        curr_svc = CurrencyConversionService(db=isolated_db)
        curr_svc.seed_historical_statutory_rates()

        port_reg = PortDisbursementRegistry(db=isolated_db, currency_service=curr_svc)
        port_reg.seed_statutory_tariffs_to_db()

        v_reg = VesselRegistry(db=isolated_db)
        v_reg.seed_navios_victory_pilot()

        svc = ResearchVoyageAssessmentService(
            db=isolated_db,
            currency_service=curr_svc,
            port_registry=port_reg,
        )

        inp = ResearchVoyageInput(
            origin_unlocode="USHOU",
            destination_unlocode="JPYOK",
            vessel_imo="9713430",
            transit_date=datetime.date(2024, 5, 1),
            user_bunker_assumption=None,
            allow_research_composite_bunker=False,
        )

        res = svc.evaluate_research_voyage(inp)
        assert res.bunker_fuel_costs_usd is None
        assert res.net_voyage_revenue_usd is None
        assert res.tce_daily_rate_usd is None
        assert any("NO_BUNKER_PRICE_AVAILABLE" in b for b in res.blocked_reasons)

    def test_ballast_leg_request_blocks_assessment(self, isolated_db):
        """Verify requesting ballast leg assessment blocks with MISSING_BALLAST_HYDROSTATICS."""
        v_reg = VesselRegistry(db=isolated_db)
        v_reg.seed_navios_victory_pilot()

        svc = ResearchVoyageAssessmentService(db=isolated_db)
        inp = ResearchVoyageInput(
            origin_unlocode="USHOU",
            destination_unlocode="JPYOK",
            vessel_imo="9713430",
            require_ballast_leg_assessment=True,
        )
        res = svc.evaluate_research_voyage(inp)
        assert res.ballast_assessment_eligible is False
        assert any("MISSING_BALLAST_HYDROSTATICS" in b for b in res.blocked_reasons)

    def test_operational_mode_blocks_assessment(self, isolated_db):
        """Verify operational mode strictly blocks research voyage assessment."""
        svc = ResearchVoyageAssessmentService(db=isolated_db)
        inp = ResearchVoyageInput(
            origin_unlocode="USHOU",
            destination_unlocode="JPYOK",
            vessel_imo="9713430",
            operational_mode=True,
        )
        res = svc.evaluate_research_voyage(inp)
        assert res.is_ready_for_operations is False
        assert any("OPERATIONAL_MODE_BLOCKED" in b for b in res.blocked_reasons)

    def test_ballast_sea_days_default_and_zero_permits_laden_tce(self, isolated_db):
        """Verify default and explicit 0.0 ballast_sea_days allows laden-only research assessment."""
        v_reg = VesselRegistry(db=isolated_db)
        v_reg.seed_navios_victory_pilot()

        svc = ResearchVoyageAssessmentService(db=isolated_db)
        user_bunker = UserSuppliedBunkerAssumption(
            price_usd_per_mt=600.0,
            fuel_grade="VLSFO",
            as_of_date=datetime.date(2024, 5, 1),
            port_unlocode="USHOU",
            user_label="Test Recap",
        )

        # 1. Default (omitted) ballast_sea_days
        inp_default = ResearchVoyageInput(
            origin_unlocode="USHOU",
            destination_unlocode="JPYOK",
            vessel_imo="9713430",
            user_bunker_assumption=user_bunker,
        )
        assert inp_default.ballast_sea_days == 0.0
        res_default = svc.evaluate_research_voyage(inp_default)
        assert res_default.tce_daily_rate_usd is not None

        # 2. Explicit 0.0 ballast_sea_days
        inp_zero = ResearchVoyageInput(
            origin_unlocode="USHOU",
            destination_unlocode="JPYOK",
            vessel_imo="9713430",
            user_bunker_assumption=user_bunker,
            ballast_sea_days=0.0,
        )
        res_zero = svc.evaluate_research_voyage(inp_zero)
        assert res_zero.total_voyage_days == res_default.total_voyage_days
        assert res_zero.tce_daily_rate_usd is not None

    def test_positive_ballast_sea_days_fails_closed_on_financial_tce(self, isolated_db):
        """Verify positive ballast_sea_days updates total_voyage_days and fails closed on TCE."""
        v_reg = VesselRegistry(db=isolated_db)
        v_reg.seed_navios_victory_pilot()

        svc = ResearchVoyageAssessmentService(db=isolated_db)
        user_bunker = UserSuppliedBunkerAssumption(
            price_usd_per_mt=600.0,
            fuel_grade="VLSFO",
            as_of_date=datetime.date(2024, 5, 1),
            port_unlocode="USHOU",
            user_label="Test Recap",
        )

        inp_ballast = ResearchVoyageInput(
            origin_unlocode="USHOU",
            destination_unlocode="JPYOK",
            vessel_imo="9713430",
            user_bunker_assumption=user_bunker,
            ballast_sea_days=12.5,
        )
        res_ballast = svc.evaluate_research_voyage(inp_ballast)

        # Total voyage days includes ballast sea days
        expected_days = round(
            12.5
            + res_ballast.laden_sea_days
            + inp_ballast.loading_days
            + inp_ballast.discharge_days
            + inp_ballast.port_waiting_days
            + inp_ballast.canal_waiting_days
            + inp_ballast.canal_transit_days,
            3,
        )
        assert res_ballast.total_voyage_days == expected_days

        # Ballast assessment fails closed due to missing ballast hydrostatics
        assert res_ballast.ballast_assessment_eligible is False
        assert any("MISSING_BALLAST_HYDROSTATICS" in b for b in res_ballast.blocked_reasons)
        assert res_ballast.net_voyage_revenue_usd is None
        assert res_ballast.tce_daily_rate_usd is None
        assert res_ballast.is_fully_validated is False

    def test_negative_ballast_sea_days_rejected_by_schema(self):
        """Verify negative ballast_sea_days raises Pydantic ValidationError."""
        with pytest.raises(ValidationError):
            ResearchVoyageInput(
                origin_unlocode="USHOU",
                destination_unlocode="JPYOK",
                vessel_imo="9713430",
                ballast_sea_days=-1.5,
            )
