"""Stage D Chartering Intelligence, Route/Cargo Compatibility, & Particulars Tests.

Validates Milestone D10 / D10.2 / OPT-002 hardening and ARCH-001 `/api/v1/charter/*`
capabilities against genuine SQLite database records and isolated constraint scenarios.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.api.main import app
from pelagos.database import Base, SessionLocal
from pelagos.models import Vessel, VesselPosition
from pelagos.optimization.chartering import (
    assess_vessel_for_voyage,
    recommend_charter_strategy,
)
from pelagos.optimization.matcher import (
    extract_port_draft_limit_meters,
    match_suitable_vessels,
)
from pelagos.optimization.schemas import (
    CharterRecommendRequest,
    SuitabilityStatus,
    VerifiedCharterInputs,
    VerifiedVesselFixtureInput,
    VesselCompatibilityRequest,
    VesselParticularsInput,
)
from pelagos.routing.ports import PortRepository


@pytest.fixture
def db_session():
    """Real Pelagos SQLite session fixture (read-only assertions only)."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """FastAPI TestClient fixture."""
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def isolated_registry_db():
    """Isolated in-memory database verifying canonical Vessel registry + tie-breaking."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    t_old = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    t_new = datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc)

    # Identical distance to Seattle entrance (48.5, -124.75), one older, one newer
    session.add(
        VesselPosition(
            mmsi="999000010",
            vessel_name="OLDER PANAMAX",
            vessel_type="Cargo",
            vessel_type_code=70,
            latitude=48.5,
            longitude=-124.75,
            draft_meters=12.0,
            length_meters=225.0,
            width_meters=32.2,
            observation_time=t_old,
            source_name="Test AIS",
            license_type="CC0",
            idempotency_key="t:old",
        )
    )
    session.add(
        Vessel(
            mmsi="999000010",
            vessel_name="OLDER PANAMAX",
            vessel_type="Cargo",
            vessel_type_code=70,
            dwt_tonnage=75000.0,
            length_meters=225.0,
            width_meters=32.2,
            draft_max_meters=12.0,
            source_name="Test Registry",
            commercial_status="active",
        )
    )

    session.add(
        VesselPosition(
            mmsi="999000020",
            vessel_name="NEWER SUPRAMAX",
            vessel_type="Cargo",
            vessel_type_code=70,
            latitude=48.5,
            longitude=-124.75,
            draft_meters=11.5,
            length_meters=195.0,
            width_meters=32.2,
            observation_time=t_new,
            source_name="Test AIS",
            license_type="CC0",
            idempotency_key="t:new",
        )
    )
    session.add(
        Vessel(
            mmsi="999000020",
            vessel_name="NEWER SUPRAMAX",
            vessel_type="Cargo",
            vessel_type_code=70,
            dwt_tonnage=58000.0,
            length_meters=195.0,
            width_meters=32.2,
            draft_max_meters=11.5,
            source_name="Test Registry",
            commercial_status="active",
        )
    )

    session.commit()
    yield session
    session.close()


class TestParticularsAndRegistryReconciliation:
    """Verify explicit missing-particulars reporting and canonical Vessel integration."""

    def test_genuine_vessels_expose_missing_particulars_explicitly(self, db_session) -> None:
        """Historical AIS vessels explicitly surface missing DWT, draft (where null), and hold cleanliness."""
        # TROODOS SUN (212719000): has LOA, beam, draft, IMO; lacks DWT, built_year, flag, holds, fixture
        res_troodos = assess_vessel_for_voyage(
            db_session,
            VesselCompatibilityRequest(
                mmsi="212719000",
                cargo_tonnes=52000.0,
                origin="USSEA",
                destination="JPYOK",
            ),
        )
        assert res_troodos.status == "assessed"
        assert res_troodos.candidate is not None
        c_tr = res_troodos.candidate
        assert c_tr.verified_cargo_capacity_dwt is None
        assert c_tr.cargo_capacity_status == "UNVERIFIED"
        assert "dwt_tonnage" in c_tr.missing_particulars
        assert "hold_cleanliness" in c_tr.missing_particulars
        assert "commercial_fixture_status" in c_tr.missing_particulars
        assert "draft_meters" not in c_tr.missing_particulars

        # Verify AIS sync registry status is strictly separated from commercial openness
        assert c_tr.registry_commercial_status == "active"
        assert c_tr.commercial_availability == "UNCONFIRMED"
        assert res_troodos.commercial_availability_status["commercial_availability"] == "UNCONFIRMED"
        assert res_troodos.ais_observation_status["is_stale"] is True
        assert res_troodos.ais_observation_status["data_mode"] == "historical"

        # AMERICAN COURAGE (367121040): lacks draft_meters in DB as well as DWT
        res_courage = assess_vessel_for_voyage(
            db_session,
            VesselCompatibilityRequest(
                mmsi="367121040",
                cargo_tonnes=52000.0,
                origin="USSEA",
                destination="JPYOK",
            ),
        )
        assert res_courage.status == "assessed"
        assert res_courage.candidate is not None
        assert "draft_meters" in res_courage.candidate.missing_particulars
        assert "dwt_tonnage" in res_courage.candidate.missing_particulars
        assert res_courage.candidate.particulars_completeness is not None
        assert res_courage.candidate.particulars_completeness.completeness_status in {"partial", "insufficient_data"}

    def test_verified_dwt_enforces_cargo_capacity_and_newer_tiebreaker(self, isolated_registry_db) -> None:
        """Canonical Vessel DWT is enforced when populated and newer AIS fixes sort before older ties."""
        resp_fit = match_suitable_vessels(
            isolated_registry_db,
            {"origin": "USSEA", "destination": "JPYOK", "cargo_tonnes": 55000.0},
        )
        assert resp_fit.potentially_suitable_count == 2
        # Both are 0.0 NM from Seattle; NEWER SUPRAMAX (11:00) must sort ahead of OLDER PANAMAX (10:00)
        assert resp_fit.candidates[0].mmsi == "999000020"
        assert resp_fit.candidates[0].verified_cargo_capacity_dwt == 58000.0
        assert resp_fit.candidates[0].cargo_capacity_status == "VERIFIED"

        # 65,000 MT cargo exceeds NEWER SUPRAMAX 58,000 DWT -> rejected via DIMENSION_MISMATCH
        resp_over = match_suitable_vessels(
            isolated_registry_db,
            {"origin": "USSEA", "destination": "JPYOK", "cargo_tonnes": 65000.0},
        )
        supramax = next(c for c in resp_over.candidates if c.mmsi == "999000020")
        assert supramax.suitability_status == SuitabilityStatus.DIMENSION_MISMATCH
        assert supramax.screening_stage == "verified_dwt_insufficient"
        assert supramax.cargo_compatibility is not None
        assert supramax.cargo_compatibility.status == "incompatible"


class TestPortCanalAndCargoCompatibility:
    """Verify genuine port limits, Panama Canal beam/advisory boundaries, and unnavigable routes."""

    def test_extracts_only_documented_port_draft_limits(self) -> None:
        """Numeric limits come strictly from verified port records without inventing missing berth specs."""
        repo = PortRepository()
        kalama = repo.get_port("USKAL")
        baton_rouge = repo.get_port("USBTR")
        mundra = repo.get_port("INMUN")
        seattle = repo.get_port("USSEA")
        paradip = repo.get_port("INPRT")

        lim_kal, _ = extract_port_draft_limit_meters(kalama)
        lim_btr, _ = extract_port_draft_limit_meters(baton_rouge)
        lim_mun, _ = extract_port_draft_limit_meters(mundra)
        lim_sea, _ = extract_port_draft_limit_meters(seattle)
        lim_prt, _ = extract_port_draft_limit_meters(paradip)

        assert lim_kal == pytest.approx(13.11, abs=0.01)
        assert lim_btr == pytest.approx(13.72, abs=0.01)
        assert lim_mun == 17.5
        assert lim_sea is None
        assert lim_prt is None  # Channel depth is not treated as berth max draft

    def test_kalama_documented_draft_prunes_deep_draft_vessel(self, db_session) -> None:
        """COSCO KAOHSIUNG (13.8m draft) fails USKAL 43-ft (13.11m) limit; missing draft yields INSUFFICIENT_DATA."""
        resp = match_suitable_vessels(
            db_session,
            {"origin": "USKAL", "destination": "JPYOK", "cargo_tonnes": 52000.0, "max_vessels": 120},
        )
        assert resp.status == "matched"
        cosco = next(c for c in resp.candidates if c.mmsi == "477136800")
        assert cosco.suitability_status == SuitabilityStatus.DIMENSION_MISMATCH
        assert cosco.screening_stage == "route_or_port_constraint_violation"
        assert cosco.route_compatibility is not None
        assert cosco.route_compatibility.status == "incompatible"
        assert cosco.route_compatibility.origin_port is not None
        assert cosco.route_compatibility.origin_port.status == "incompatible"

        # AMERICAN COURAGE has missing draft -> insufficient_data rather than assuming clearance
        courage = next(c for c in resp.candidates if c.mmsi == "367121040")
        assert courage.suitability_status == SuitabilityStatus.INSUFFICIENT_DATA
        assert courage.route_compatibility is not None
        assert courage.route_compatibility.origin_port is not None
        assert courage.route_compatibility.origin_port.status == "insufficient_data"

        # TROODOS SUN (12.5m draft <= 13.11m) clears channel draft check (berth LOA/beam still unverified)
        troodos = next(c for c in resp.candidates if c.mmsi == "212719000")
        assert troodos.suitability_status == SuitabilityStatus.POTENTIALLY_SUITABLE

    def test_unnavigable_landlocked_route_returns_unavailable(self, db_session) -> None:
        """Caspian landlocked origin returns unavailable and marks route_navigable=False."""
        resp = match_suitable_vessels(
            db_session,
            {"origin": "CASP_TEST", "destination": "JPYOK", "cargo_tonnes": 52000.0},
        )
        assert resp.status == "unavailable"
        assert resp.potentially_suitable_count == 0
        assert resp.route_compatibility_summary is not None
        assert resp.route_compatibility_summary["route_navigable"] is False
        assert resp.candidates[0].route_compatibility is not None
        assert resp.candidates[0].route_compatibility.status == "incompatible"

    def test_usg_panama_canal_transit_compatibility_notes(self, db_session) -> None:
        """USG -> Japan highlights Panama Canal Neopanamax requirement and unverified live ACP advisories."""
        res = assess_vessel_for_voyage(
            db_session,
            VesselCompatibilityRequest(
                mmsi="212719000",  # TROODOS SUN: 228m x 35m beam
                cargo_tonnes=52000.0,
                origin="USMSY",
                destination="JPYOK",
            ),
        )
        assert res.candidate is not None
        rc = res.candidate.route_compatibility
        assert rc is not None
        assert any("panama" in c.lower() for c in rc.canals_transited)
        assert any("Neopanamax" in n for n in rc.canal_compatibility_notes)
        assert "panama_canal_acp_neopanamax_draft_and_slot_rules" in rc.missing_requirements

        # Standalone beam > 51.25m exceeds even Neopanamax structural limit -> incompatible
        ultra_wide = assess_vessel_for_voyage(
            db_session,
            VesselCompatibilityRequest(
                vessel=VesselParticularsInput(
                    mmsi="999888777",
                    vessel_name="ULTRA WIDE",
                    vessel_type="Cargo",
                    vessel_type_code=70,
                    length_meters=360.0,
                    width_meters=55.0,
                    draft_meters=14.0,
                ),
                cargo_tonnes=90000.0,
                origin="USMSY",
                destination="JPYOK",
            ),
        )
        assert ultra_wide.overall_suitability == SuitabilityStatus.DIMENSION_MISMATCH
        assert ultra_wide.candidate is not None
        assert ultra_wide.candidate.route_compatibility is not None
        assert ultra_wide.candidate.route_compatibility.status == "incompatible"


class TestCharterRecommendationReadinessAndApi:
    """Verify Chartering Recommendation Gate refuses fabrication when inputs are incomplete."""

    def test_genuine_db_pnw_returns_insufficient_data_without_fabricated_action(self, client: TestClient) -> None:
        """POST /api/v1/charter/recommend returns explicit insufficient_data on genuine PNW-JPN-BULK."""
        payload = {
            "origin": "USSEA",
            "destination": "JPYOK",
            "cargo_tonnes": 52000.0,
        }
        resp = client.post("/api/v1/charter/recommend", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["status"] == "insufficient_data"
        assert data["recommendation_issued"] is False
        assert data["recommended_action"] is None
        assert data["recommended_contract_structure"] is None
        assert data["contract_comparisons"] == []

        gates = data["data_readiness_gates"]
        assert gates["corridor_and_freight_market"]["status"] == "available"
        assert gates["directional_forecast_edge"]["status"] == "insufficient_data"
        assert gates["vessel_physical_particulars"]["status"] == "insufficient_data"
        assert gates["commercial_open_tonnage_and_laycan"]["status"] == "unavailable"
        assert gates["bunker_and_voyage_economics"]["status"] == "unavailable"

        # Genuine analytics are surfaced transparently without inventing recommendations
        analytics = data["verified_supporting_analytics"]
        assert analytics["applicable_rate"]["rate_value"] == 38.00
        assert analytics["gross_freight_cost_usd"] == 1976000.0
        assert analytics["data_mode"] == "historical"
        assert "commercial_open_tonnage_list" in data["missing_decision_inputs"]
        assert "verified_vessel_dwt_tonnage" in data["missing_decision_inputs"]

    def test_genuine_db_usg_flags_panama_rules_in_missing_inputs(self, client: TestClient) -> None:
        """GET /api/v1/charter/recommend for USG-JPN-BULK audits Panama Canal rule gap."""
        resp = client.get(
            "/api/v1/charter/recommend",
            params={"route_code": "USG-JPN-BULK", "cargo_tonnes": 55000.0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "insufficient_data"
        assert data["recommendation_issued"] is False
        assert "panama_canal_live_authority_draft_and_slot_rules" in data["missing_decision_inputs"]

    def test_unmonitored_and_unnavigable_return_unavailable(self, client: TestClient) -> None:
        """Unmonitored corridor and Caspian route return unavailable without action."""
        r_unmon = client.post(
            "/api/v1/charter/recommend",
            json={"origin": "NLRTM", "destination": "SGSIN", "cargo_tonnes": 50000.0},
        )
        assert r_unmon.status_code == 200
        assert r_unmon.json()["status"] == "unavailable"
        assert r_unmon.json()["recommended_action"] is None

        r_casp = client.post(
            "/api/v1/charter/recommend",
            json={"origin": "CASP_TEST", "destination": "JPYOK", "cargo_tonnes": 50000.0},
        )
        assert r_casp.status_code == 200
        assert r_casp.json()["status"] == "unavailable"

    def test_charter_assess_endpoints_and_validation(self, client: TestClient) -> None:
        """GET & POST /api/v1/charter/assess handle genuine MMSI, missing MMSI, and 422 checks."""
        r_ok = client.get(
            "/api/v1/charter/assess",
            params={"mmsi": "212719000", "origin": "USSEA", "destination": "JPYOK", "cargo_tonnes": 52000.0},
        )
        assert r_ok.status_code == 200
        assert r_ok.json()["status"] == "assessed"
        assert r_ok.json()["candidate"]["mmsi"] == "212719000"

        r_miss = client.get("/api/v1/charter/assess", params={"mmsi": "000000000", "origin": "USSEA", "destination": "JPYOK"})
        assert r_miss.status_code == 200
        assert r_miss.json()["status"] == "vessel_not_found"

        r_bad_cargo = client.post(
            "/api/v1/charter/recommend",
            json={"origin": "USSEA", "destination": "JPYOK", "cargo_tonnes": 0.0},
        )
        assert r_bad_cargo.status_code == 422

        r_bad_laycan = client.post(
            "/api/v1/charter/recommend",
            json={
                "origin": "USSEA",
                "destination": "JPYOK",
                "cargo_tonnes": 52000.0,
                "laycan_start": "2026-11-20",
                "laycan_end": "2026-11-10",
            },
        )
        assert r_bad_laycan.status_code == 422

    def test_verified_inputs_enforce_constraints_and_deterministic_recommendation(self, db_session) -> None:
        """Verified fixtures prune laden/draft-violating/late vessels and compute reproducible rankings."""
        verified_bundle = VerifiedCharterInputs(
            source_provenance="Unit Test Verified Fixture Feed",
            bunker_price_usd_per_mt=620.0,
            bunker_fuel_grade="VLSFO",
            port_disbursements_usd=45000.0,
            canal_tolls_usd=0.0,
            voyage_loading_discharge_days=6.0,
            consecutive_voyages_count=3,
            origin_max_draft_meters=12.8,
            destination_max_draft_meters=13.5,
            origin_max_loa_meters=235.0,
            destination_max_loa_meters=240.0,
            origin_max_beam_meters=36.0,
            destination_max_beam_meters=36.0,
            verified_future_rate_usd_per_mt=41.50,
            vessel_fixtures=[
                # 1. Pruned: commercial_status != open (laden)
                VerifiedVesselFixtureInput(
                    mmsi="800000001",
                    vessel_name="LADEN BULKER",
                    length_meters=225.0,
                    width_meters=32.2,
                    draft_meters=12.0,
                    dwt_tonnage=75000.0,
                    commercial_status="laden",
                    hold_cleanliness_verified=True,
                    current_latitude=48.4,
                    current_longitude=-124.8,
                    service_speed_knots=13.5,
                    daily_fuel_consumption_mt=26.0,
                    spot_charter_rate_usd_per_mt=38.0,
                    observation_time="2026-11-01T00:00:00Z",
                ),
                # 2. Pruned: draft 13.4m > origin max draft 12.8m
                VerifiedVesselFixtureInput(
                    mmsi="800000002",
                    vessel_name="DEEP DRAFT PANAMAX",
                    length_meters=228.0,
                    width_meters=32.2,
                    draft_meters=13.4,
                    dwt_tonnage=82000.0,
                    commercial_status="open",
                    hold_cleanliness_verified=True,
                    current_latitude=48.4,
                    current_longitude=-124.8,
                    service_speed_knots=13.0,
                    daily_fuel_consumption_mt=27.0,
                    spot_charter_rate_usd_per_mt=38.0,
                    observation_time="2026-11-01T00:00:00Z",
                ),
                # 3. Pruned: misses laycan (far away in South Atlantic > 10 days away)
                VerifiedVesselFixtureInput(
                    mmsi="800000003",
                    vessel_name="TOO FAR BULKER",
                    length_meters=198.0,
                    width_meters=32.2,
                    draft_meters=11.8,
                    dwt_tonnage=63000.0,
                    commercial_status="open",
                    hold_cleanliness_verified=True,
                    current_latitude=-34.0,
                    current_longitude=-55.0,
                    service_speed_knots=12.0,
                    daily_fuel_consumption_mt=24.0,
                    spot_charter_rate_usd_per_mt=38.0,
                    observation_time="2026-11-01T00:00:00Z",
                ),
                # 4. Compliant open candidate near PNW
                VerifiedVesselFixtureInput(
                    mmsi="800000004",
                    vessel_name="COMPLIANT GRAIN STAR",
                    length_meters=225.0,
                    width_meters=32.2,
                    draft_meters=12.1,
                    dwt_tonnage=74000.0,
                    commercial_status="open",
                    hold_cleanliness_verified=True,
                    current_latitude=46.0,
                    current_longitude=-125.0,
                    service_speed_knots=13.5,
                    daily_fuel_consumption_mt=25.0,
                    spot_charter_rate_usd_per_mt=38.0,
                    coa_consecutive_voyage_rate_usd_per_mt=36.0,
                    time_charter_daily_hire_usd=16500.0,
                    observation_time="2026-11-01T00:00:00Z",
                ),
            ],
        )

        req = CharterRecommendRequest(
            origin="USSEA",
            destination="JPYOK",
            cargo_tonnes=52000.0,
            laycan_start=date(2026, 11, 1),
            laycan_end=date(2026, 11, 5),
            verified_commercial_inputs=verified_bundle,
        )
        r1 = recommend_charter_strategy(db_session, req)
        r2 = recommend_charter_strategy(db_session, req)

        assert r1.status == "recommended"
        assert r1.recommendation_issued is True
        assert r1.recommended_action == "Fix Now"
        assert r1.recommended_contract_structure is not None
        assert len(r1.ranked_vessel_candidates) == 1
        assert r1.ranked_vessel_candidates[0].mmsi == "800000004"
        assert r1.ranked_vessel_candidates[0].commercial_availability == "VERIFIED_OPEN"
        assert r1.ranked_vessel_candidates[0].laycan_feasibility == "FEASIBLE"
        assert len(r1.contract_comparisons) == 3

        # Deterministic reproducibility check
        assert r1.contract_comparisons == r2.contract_comparisons
        assert r1.recommended_contract_structure == r2.recommended_contract_structure

        # If all compliant vessels are removed (e.g. tight laycan), must return unavailable without guessing
        tight_req = CharterRecommendRequest(
            origin="USSEA",
            destination="JPYOK",
            cargo_tonnes=52000.0,
            laycan_start=date(2026, 11, 1),
            laycan_end=date(2026, 11, 1),
            verified_commercial_inputs=VerifiedCharterInputs(
                source_provenance="Unit Test Tight Laycan",
                bunker_price_usd_per_mt=620.0,
                voyage_loading_discharge_days=6.0,
                origin_max_draft_meters=12.8,
                destination_max_draft_meters=13.5,
                vessel_fixtures=verified_bundle.vessel_fixtures[:2],  # Only laden and deep-draft (13.4m > 12.8m) vessels
            ),
        )
        r_tight = recommend_charter_strategy(db_session, tight_req)
        assert r_tight.status == "unavailable"
        assert r_tight.recommendation_issued is False
        assert r_tight.recommended_action is None

        # Passing verified fixtures without verified port draft/LOA limits refuses to issue recommendation
        unverified_ports_req = CharterRecommendRequest(
            origin="USSEA",
            destination="JPYOK",
            cargo_tonnes=52000.0,
            verified_commercial_inputs=VerifiedCharterInputs(
                source_provenance="Unit Test Missing Port Limits",
                bunker_price_usd_per_mt=620.0,
                voyage_loading_discharge_days=6.0,
                vessel_fixtures=[verified_bundle.vessel_fixtures[3]],
            ),
        )
        r_unv_port = recommend_charter_strategy(db_session, unverified_ports_req)
        assert r_unv_port.status == "insufficient_data"
        assert r_unv_port.recommendation_issued is False
        assert r_unv_port.recommended_action is None

