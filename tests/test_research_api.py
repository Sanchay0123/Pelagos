"""Focused API Tests for Stage F1.5.3-B: Dedicated Research Voyage Assessment API.

Validates the dedicated endpoint:
    POST /api/v1/research/voyage-assessment
    GET  /api/v1/research/voyage-assessment

Covers the 8 mandatory test domains:
1. Successful controlled research assessment (POST and GET).
2. Request validation failures (numeric bounds, types, UN/LOCODE format).
3. Service-level calculation or evidence failures (HTTP 500 error shielding).
4. Missing evidence behaviour (missing bunker prices fail closed on TCE).
5. Correct response schema (all fields, types, and line-item components).
6. Research governance classification (is_fully_validated=False, LEVEL_2_RESEARCH_ONLY).
7. Strict separation from the commercial recommendation service (/api/v1/charter/recommend).
8. Confirmation that a successful research response does NOT change operational readiness (is_ready_for_operations=False).
"""

from __future__ import annotations

import datetime
import logging
import os
import sqlite3
from typing import Generator
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from pelagos.api.main import app
from pelagos.api.routes.research import get_research_assessment_service
from pelagos.currency import CurrencyConversionService
from pelagos.database import Base, ensure_schema_compatibility, get_db
from pelagos.optimization.schemas import (
    ResearchVoyageAssessmentRequest,
    ResearchVoyageAssessmentResponse,
)
from pelagos.registry.port_disbursement_registry import PortDisbursementRegistry
from pelagos.registry.vessel_registry import VesselRegistry


@pytest.fixture
def client_and_isolated_db() -> Generator[tuple[TestClient, Session], None, None]:
    """Provide an isolated in-memory SQLite database and FastAPI TestClient."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    ensure_schema_compatibility(engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Seed isolated database with NAVIOS VICTORY pilot data, statutory port tariffs, and historical currency fixings
    seed_session = TestingSession()
    v_reg = VesselRegistry(db=seed_session)
    v_reg.seed_navios_victory_pilot()
    port_reg = PortDisbursementRegistry(db=seed_session)
    port_reg.seed_statutory_tariffs_to_db()
    curr_svc = CurrencyConversionService(db=seed_session)
    curr_svc.seed_historical_statutory_rates()
    seed_session.commit()
    seed_session.close()

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        active_session = TestingSession()
        yield test_client, active_session
        active_session.close()

    app.dependency_overrides.clear()
    engine.dispose()


# ===========================================================================
# 1. Successful Controlled Research Assessment (POST and GET)
# ===========================================================================

def test_01_successful_controlled_research_assessment_post(client_and_isolated_db):
    """1. POST /api/v1/research/voyage-assessment executes controlled Level 2 pilot assessment."""
    client, _ = client_and_isolated_db
    payload = {
        "origin_unlocode": "USHOU",
        "destination_unlocode": "JPYOK",
        "vessel_imo": "9713430",
        "transit_date": "2024-05-01",
        "cargo_tonnes": 60000.0,
        "freight_rate_override_usd_per_mt": 61.80,
        "user_bunker_assumption": {
            "price_usd_per_mt": 600.0,
            "fuel_grade": "VLSFO",
            "as_of_date": "2024-05-01",
            "port_unlocode": "USHOU",
            "user_label": "Fixture Recap Benchmark",
        },
        "commission_rate": 0.0375,
        "steaming_speed_knots": 12.5,
        "ballast_sea_days": 0.0,
        "operational_mode": False,
    }

    resp = client.post("/api/v1/research/voyage-assessment", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    # Core identification & routing
    assert data["corridor_code"] == "USG-JPN-BULK"
    assert data["vessel_imo"] == "9713430"
    assert data["vessel_name"] == "NAVIOS VICTORY"
    assert data["vessel_class"] == "Panamax"
    assert data["origin_port"] == "USHOU"
    assert data["destination_port"] == "JPYOK"
    assert data["canals_transited"] == ["panama"]
    assert data["route_distance_nm"] > 9300.0
    assert data["antimeridian_crossing_detected"] is True

    # Revenue (Exact)
    assert data["cargo_tonnes"] == 60000.0
    assert data["freight_rate_usd_per_mt"] == 61.80
    assert data["gross_freight_usd"] == pytest.approx(3708000.00, rel=1e-4)
    assert data["commission_rate"] == 0.0375
    assert data["commission_deductions_usd"] == pytest.approx(139050.00, rel=1e-4)

    # Disbursements & Tolls (Exact Audited Statutory Schedules)
    assert data["origin_port_disbursements_usd"] == pytest.approx(23746.18, rel=1e-4)
    assert data["destination_port_disbursements_usd"] == pytest.approx(15886.02, rel=1e-4)
    assert data["total_port_disbursements_usd"] == pytest.approx(39632.20, rel=1e-4)
    assert data["panama_canal_tolls_usd"] == pytest.approx(204112.45, rel=1e-4)

    # Steaming Duration, Consumption & Fuel Costs (Exact Audited SFOC)
    assert data["laden_sea_days"] == pytest.approx(31.211, rel=1e-4)
    assert data["total_voyage_days"] == pytest.approx(40.711, rel=1e-4)
    assert data["laden_fuel_consumption_mt"] == pytest.approx(622.69, rel=1e-4)
    assert data["bunker_price_usd_per_mt"] == 600.0
    assert data["bunker_fuel_costs_usd"] == pytest.approx(373614.00, rel=1e-4)
    assert data["bunker_evidence_classification"] == "USER_SUPPLIED_ASSUMPTION"

    # Conventional Voyage TCE & Net Revenue (Exact Audited Economics)
    assert data["net_voyage_revenue_usd"] == pytest.approx(2951591.35, rel=1e-4)
    assert data["tce_daily_rate_usd"] == pytest.approx(72501.08, rel=1e-4)

    # Epistemic & Readiness Governance
    assert data["is_fully_validated"] is False
    assert data["is_ready_for_operations"] is False
    assert data["governance_tier"] == "LEVEL_2_RESEARCH_ONLY"
    assert data["ballast_assessment_eligible"] is False

    # Line item breakdown & disclaimers
    assert len(data["components_breakdown"]) >= 6
    assert any(c["category"] == "FUEL" for c in data["components_breakdown"])
    assert any(c["category"] == "PORT_DUES" for c in data["components_breakdown"])
    assert any(c["category"] == "CANAL_TOLLS" for c in data["components_breakdown"])
    assert "LEVEL 2 RESEARCH ASSESSMENT ONLY" in data["api_governance_notice"]
    assert data["assessment_timestamp"] is not None


def test_02_successful_controlled_research_assessment_get(client_and_isolated_db):
    """2. GET /api/v1/research/voyage-assessment executes controlled assessment via query parameters."""
    client, _ = client_and_isolated_db
    params = {
        "origin": "USHOU",
        "destination": "JPYOK",
        "vessel_imo": "9713430",
        "transit_date": "2024-05-01",
        "cargo_tonnes": 60000.0,
        "freight_rate_override": 61.80,
        "user_bunker_price": 600.0,
        "steaming_speed_knots": 12.5,
    }

    resp = client.get("/api/v1/research/voyage-assessment", params=params)
    assert resp.status_code == 200
    data = resp.json()

    assert data["vessel_imo"] == "9713430"
    assert data["gross_freight_usd"] == pytest.approx(3708000.00, rel=1e-4)
    assert data["commission_deductions_usd"] == pytest.approx(139050.00, rel=1e-4)
    assert data["origin_port_disbursements_usd"] == pytest.approx(23746.18, rel=1e-4)
    assert data["destination_port_disbursements_usd"] == pytest.approx(15886.02, rel=1e-4)
    assert data["total_port_disbursements_usd"] == pytest.approx(39632.20, rel=1e-4)
    assert data["panama_canal_tolls_usd"] == pytest.approx(204112.45, rel=1e-4)
    assert data["bunker_fuel_costs_usd"] == pytest.approx(373614.00, rel=1e-4)
    assert data["net_voyage_revenue_usd"] == pytest.approx(2951591.35, rel=1e-4)
    assert data["tce_daily_rate_usd"] == pytest.approx(72501.08, rel=1e-4)
    assert data["is_ready_for_operations"] is False


# ===========================================================================
# 2. Request Validation Failures (HTTP 422)
# ===========================================================================

def test_03_request_validation_failures(client_and_isolated_db):
    """3. Schema validation strictly rejects invalid numeric bounds and malformed inputs."""
    client, _ = client_and_isolated_db

    # Negative cargo intake
    r1 = client.post("/api/v1/research/voyage-assessment", json={"cargo_tonnes": -500.0})
    assert r1.status_code == 422

    # Zero cargo intake
    r2 = client.post("/api/v1/research/voyage-assessment", json={"cargo_tonnes": 0.0})
    assert r2.status_code == 422

    # Steaming speed exceeds 25 knots
    r3 = client.post("/api/v1/research/voyage-assessment", json={"steaming_speed_knots": 35.0})
    assert r3.status_code == 422

    # Negative commission rate
    r4 = client.post("/api/v1/research/voyage-assessment", json={"commission_rate": -0.05})
    assert r4.status_code == 422

    # Excessive commission rate (> 20%)
    r5 = client.post("/api/v1/research/voyage-assessment", json={"commission_rate": 0.35})
    assert r5.status_code == 422

    # Negative ballast sea days
    r6 = client.post("/api/v1/research/voyage-assessment", json={"ballast_sea_days": -2.5})
    assert r6.status_code == 422

    # Gatun Lake elevation out of range (70-90 ft)
    r7 = client.post("/api/v1/research/voyage-assessment", json={"gatun_elevation_ft": 95.0})
    assert r7.status_code == 422

    # Invalid port LOCODE length
    r8 = client.post("/api/v1/research/voyage-assessment", json={"origin_unlocode": "HOU"})
    assert r8.status_code == 422


# ===========================================================================
# 3. Unsupported Route & Unqualified Vessel Inputs (HTTP 400)
# ===========================================================================

def test_04_unsupported_corridor_rejected(client_and_isolated_db):
    """4. Unsupported corridors are rejected with explicit HTTP 400."""
    client, _ = client_and_isolated_db

    # Unmonitored European corridor
    resp = client.post(
        "/api/v1/research/voyage-assessment",
        json={"origin_unlocode": "NLRTM", "destination_unlocode": "SGSIN"},
    )
    assert resp.status_code == 400
    assert "UNSUPPORTED_RESEARCH_CORRIDOR" in resp.json()["detail"]

    # PNW corridor (qualified for chartering screening, but research pilot is USG-JPN)
    resp2 = client.post(
        "/api/v1/research/voyage-assessment",
        json={"origin_unlocode": "USSEA", "destination_unlocode": "JPYOK"},
    )
    assert resp2.status_code == 400
    assert "UNSUPPORTED_RESEARCH_CORRIDOR" in resp2.json()["detail"]


def test_05_unqualified_vessel_rejected(client_and_isolated_db):
    """5. Unqualified or unverified vessels are rejected with explicit HTTP 400."""
    client, _ = client_and_isolated_db

    # Candidate vessel Iris Bliss (IMO 9787493 - disqualified in F1.5.2-A)
    resp = client.post(
        "/api/v1/research/voyage-assessment",
        json={"vessel_imo": "9787493"},
    )
    assert resp.status_code == 400
    assert "UNQUALIFIED_RESEARCH_VESSEL" in resp.json()["detail"]

    # Arbitrary unseeded IMO
    resp2 = client.post(
        "/api/v1/research/voyage-assessment",
        json={"vessel_imo": "9999999"},
    )
    assert resp2.status_code == 400
    assert "UNQUALIFIED_RESEARCH_VESSEL" in resp2.json()["detail"]


# ===========================================================================
# 4. Missing Evidence Behaviour (Fail-Closed TCE)
# ===========================================================================

def test_06_missing_bunker_evidence_fails_closed(client_and_isolated_db):
    """6. Omitting bunker price fails closed on financial TCE without inventing prices."""
    client, _ = client_and_isolated_db
    payload = {
        "origin_unlocode": "USHOU",
        "destination_unlocode": "JPYOK",
        "vessel_imo": "9713430",
        "user_bunker_assumption": None,
        "allow_research_composite_bunker": False,
    }

    resp = client.post("/api/v1/research/voyage-assessment", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["bunker_fuel_costs_usd"] is None
    assert data["net_voyage_revenue_usd"] is None
    assert data["tce_daily_rate_usd"] is None
    assert any("NO_BUNKER_PRICE_AVAILABLE" in b for b in data["blocked_reasons"])
    assert data["is_fully_validated"] is False
    assert data["is_ready_for_operations"] is False


def test_07_ballast_sea_days_accounting_and_tce_fail_closed(client_and_isolated_db):
    """7. Positive ballast_sea_days correctly updates total_voyage_days and blocks TCE."""
    client, _ = client_and_isolated_db
    payload = {
        "origin_unlocode": "USHOU",
        "destination_unlocode": "JPYOK",
        "vessel_imo": "9713430",
        "user_bunker_assumption": {
            "price_usd_per_mt": 600.0,
            "fuel_grade": "VLSFO",
            "as_of_date": "2024-05-01",
            "port_unlocode": "USHOU",
            "user_label": "Test",
        },
        "ballast_sea_days": 12.5,
    }

    resp = client.post("/api/v1/research/voyage-assessment", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    # Total days includes the 12.5 ballast days
    expected_days = round(
        12.5
        + data["laden_sea_days"]
        + 3.0  # loading_days
        + 3.0  # discharge_days
        + 1.0  # port_waiting_days
        + 1.5  # canal_waiting_days
        + 1.0, # canal_transit_days
        3,
    )
    assert data["total_voyage_days"] == expected_days

    # Blocks TCE because certified shipyard ballast hydrostatics are unavailable
    assert data["ballast_assessment_eligible"] is False
    assert data["net_voyage_revenue_usd"] is None
    assert data["tce_daily_rate_usd"] is None
    assert any("MISSING_BALLAST_HYDROSTATICS" in b for b in data["blocked_reasons"])


def test_08_operational_mode_request_strictly_fails_closed(client_and_isolated_db):
    """8. Requesting operational_mode=True fails closed under Level 2 governance."""
    client, _ = client_and_isolated_db
    payload = {
        "origin_unlocode": "USHOU",
        "destination_unlocode": "JPYOK",
        "vessel_imo": "9713430",
        "operational_mode": True,
    }

    resp = client.post("/api/v1/research/voyage-assessment", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["is_ready_for_operations"] is False
    assert data["net_voyage_revenue_usd"] is None
    assert data["tce_daily_rate_usd"] is None
    assert any("OPERATIONAL_MODE_BLOCKED" in b for b in data["blocked_reasons"])


# ===========================================================================
# 5. Service-Level Error Shielding (HTTP 500)
# ===========================================================================

def test_09_service_level_calculation_error_shielding(client_and_isolated_db, caplog):
    """9. Unhandled service errors return sanitized HTTP 500 without leaking sensitive messages."""
    client, _ = client_and_isolated_db

    sensitive_detail = "/etc/vault/postgres_secret.key: SELECT token FROM api_keys WHERE role='root'"

    class BrokenService:
        def evaluate_research_voyage(self, _):
            raise RuntimeError(f"Database query failed: {sensitive_detail}")

    app.dependency_overrides[get_research_assessment_service] = lambda: BrokenService()

    try:
        with caplog.at_level(logging.ERROR):
            resp = client.post(
                "/api/v1/research/voyage-assessment",
                json={"origin_unlocode": "USHOU", "destination_unlocode": "JPYOK", "vessel_imo": "9713430"},
            )
        assert resp.status_code == 500
        detail = resp.json()["detail"]

        # Stable error code is preserved
        assert "RESEARCH_ASSESSMENT_EXECUTION_ERROR" in detail
        # Sanitized detail is returned exactly
        assert detail == (
            "RESEARCH_ASSESSMENT_EXECUTION_ERROR: An internal service error occurred "
            "during research voyage calculation. Please refer to server logs."
        )
        # Sensitive details are strictly shielded from the client response
        assert sensitive_detail not in detail
        assert "/etc/vault" not in detail
        assert "SELECT token" not in detail
        assert "postgres_secret" not in detail

        # Full exception and traceback are logged server-side
        assert any(sensitive_detail in rec.message for rec in caplog.records)
        assert any("Service error during research voyage assessment" in rec.message for rec in caplog.records)
    finally:
        app.dependency_overrides.pop(get_research_assessment_service, None)


# ===========================================================================
# 6. Strict Separation from Commercial Charter Recommendation Service
# ===========================================================================

def test_10_separation_from_commercial_charter_matcher(client_and_isolated_db):
    """10. Executing research assessment does NOT pollute commercial charter recommendation."""
    client, _ = client_and_isolated_db

    # Step 1: Execute research assessment for NAVIOS VICTORY
    research_payload = {
        "origin_unlocode": "USHOU",
        "destination_unlocode": "JPYOK",
        "vessel_imo": "9713430",
        "transit_date": "2024-05-01",
        "cargo_tonnes": 60000.0,
        "user_bunker_assumption": {
            "price_usd_per_mt": 600.0,
            "fuel_grade": "VLSFO",
            "as_of_date": "2024-05-01",
            "port_unlocode": "USHOU",
            "user_label": "Fixture Recap",
        },
    }
    r_res = client.post("/api/v1/research/voyage-assessment", json=research_payload)
    assert r_res.status_code == 200
    assert r_res.json()["is_ready_for_operations"] is False

    # Step 2: Query commercial chartering endpoint
    charter_payload = {
        "origin": "USHOU",
        "destination": "JPYOK",
        "cargo_tonnes": 60000.0,
    }
    r_charter = client.post("/api/v1/charter/recommend", json=charter_payload)
    assert r_charter.status_code == 200
    c_data = r_charter.json()

    # Commercial recommendation remains strictly unissued
    assert c_data["status"] == "insufficient_data"
    assert c_data["recommendation_issued"] is False
    assert c_data["recommended_action"] is None
    assert c_data["contract_comparisons"] == []

    # Commercial open tonnage gate remains strictly unavailable
    gates = c_data["data_readiness_gates"]
    assert gates["commercial_open_tonnage_and_laycan"]["status"] == "unavailable"

    # Research calculations are NOT described as commercial owner profitability
    assert "owner_operating_profitability" not in r_res.json()
    assert "commercial_fixture_clearing" not in r_res.json()


# ===========================================================================
# 7. Primary Database Isolation Verification
# ===========================================================================

def test_11_primary_database_isolation():
    """11. Direct verification that production pelagos.db is never mutated by API calls."""
    db_path = "pelagos.db"
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        integrity = cur.execute("PRAGMA integrity_check").fetchall()
        conn.close()
        assert integrity == [("ok",)]
        assert os.path.getsize(db_path) == 1904640
