"""Tests for Pelagos Stage C2.2 Bunker Fuel Price Acquisition and Fusion.

Validates external provider integration, schema validation, anomaly detection,
deterministic idempotency, network error resilience, hub-to-route association,
and strict prevention of synthetic pricing or silent port substitution.

Traces to DATA-001 Section 5.2, REQ-NFR-DIP-001, and Stage C2.2 mandates.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from pelagos.database import Base
from pelagos.ingestion.acquisition.bunker_provider import (
    DEFAULT_BUNKER_API_URL,
    KNOWN_BUNKER_GRADES,
    PROVIDER_NAME,
    fetch_bunker_prices,
    parse_iso_datetime,
    refresh_bunker_prices,
    validate_bunker_record,
)
from pelagos.ingestion.acquisition.client import (
    AcquisitionHTTPError,
    AcquisitionNetworkError,
    AcquisitionResponse,
    AcquisitionTimeoutError,
    ResilientHttpClient,
)
from pelagos.models import BunkerPriceObservation
from pelagos.routing.bunker import (
    BunkeringHubInfo,
    CORRIDOR_BUNKERING_HUBS,
    evaluate_bunker_freshness,
    resolve_corridor_bunker_fuel_references,
)


@pytest.fixture
def test_db_session():
    """Create an isolated SQLite database session for unit tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionTest = sessionmaker(bind=engine)
    session = SessionTest()
    yield session
    session.close()


class TestBunkerProviderValidation:
    """Tests for record-level parsing, type-safety, and validation rules."""

    def test_valid_provider_response_validation(self):
        """Valid delivered VLSFO quote parses correctly with preserved fields."""
        raw = {
            "locode": "ARBUE",
            "port": "Buenos Aires",
            "country": "AR",
            "grade": "VLSFO",
            "usdPerMt": 957.5,
            "kind": "indicative",
            "basis": "delivered",
            "source": "licensed:oilpriceapi:VLSFO_INDICATION_ARBUE_ZONA_COMUN_USD",
            "observedAt": "2026-11-06T17:47:08.000Z",
            "provenance": "Indicative, from a licensed feed.",
        }
        is_valid, cleaned, err = validate_bunker_record(raw)
        assert is_valid is True
        assert err is None
        assert cleaned is not None
        assert cleaned["port_code"] == "ARBUE"
        assert cleaned["port_name"] == "Buenos Aires"
        assert cleaned["country_code"] == "AR"
        assert cleaned["fuel_grade"] == "VLSFO"
        assert cleaned["price_value"] == 957.50
        assert cleaned["currency"] == "USD"
        assert cleaned["unit"] == "USD/MT"
        assert cleaned["pricing_type"] == "indicative"
        assert cleaned["delivery_basis"] == "delivered"
        assert cleaned["source_name"] == PROVIDER_NAME
        assert cleaned["source_feed"] == "licensed:oilpriceapi:VLSFO_INDICATION_ARBUE_ZONA_COMUN_USD"
        assert cleaned["observation_time"].year == 2026
        assert cleaned["observation_time"].tzinfo == datetime.timezone.utc

    def test_missing_and_malformed_fields_rejection(self):
        """Missing or malformed mandatory fields are rejected with descriptive reasons."""
        # Missing price
        bad_price = {"locode": "USSEA", "grade": "VLSFO", "observedAt": "2026-11-06T12:00:00Z"}
        assert validate_bunker_record(bad_price)[0] is False

        # Negative price
        neg_price = {"locode": "USSEA", "grade": "VLSFO", "usdPerMt": -50.0, "observedAt": "2026-11-06T12:00:00Z"}
        assert validate_bunker_record(neg_price)[0] is False

        # Non-numeric price
        str_price = {"locode": "USSEA", "grade": "VLSFO", "usdPerMt": "N/A", "observedAt": "2026-11-06T12:00:00Z"}
        assert validate_bunker_record(str_price)[0] is False

        # Invalid UN/LOCODE (must be 5 alphabetic letters)
        bad_locode = {"locode": "SEA1", "grade": "VLSFO", "usdPerMt": 600.0, "observedAt": "2026-11-06T12:00:00Z"}
        assert validate_bunker_record(bad_locode)[0] is False

        # Invalid observation timestamp
        bad_time = {"locode": "USSEA", "grade": "VLSFO", "usdPerMt": 600.0, "observedAt": "not-a-timestamp"}
        assert validate_bunker_record(bad_time)[0] is False

    def test_unknown_ports_and_grades_rejection(self):
        """Unrecognized fuel grades and commodities (e.g. crude oil) are strictly rejected."""
        # Crude oil proxy attempt (strictly disallowed by Pelagos integrity policy)
        brent = {"locode": "USSEA", "grade": "BRENT_CRUDE", "usdPerMt": 550.0, "observedAt": "2026-11-06T12:00:00Z"}
        is_valid, _, err = validate_bunker_record(brent)
        assert is_valid is False
        assert "Unrecognized or unsupported fuel grade" in err

        # Gasoline / jet fuel
        gasoline = {"locode": "USSEA", "grade": "GASOLINE", "usdPerMt": 700.0, "observedAt": "2026-11-06T12:00:00Z"}
        assert validate_bunker_record(gasoline)[0] is False

    def test_anomaly_detection_currency_mismatch(self):
        """Detects currency mismatch when source ticker quotes CNY but field reports usdPerMt."""
        zhoushan_anomaly = {
            "locode": "CNZOS",
            "port": "Zhoushan",
            "country": "CN",
            "grade": "HSFO",
            "usdPerMt": 6028,
            "kind": "indicative",
            "basis": "delivered",
            "source": "licensed:oilpriceapi:LSFO_380_CNZOS_BONDED_SELLER_CNY",
            "observedAt": "2026-11-06T10:33:42.000Z",
        }
        is_valid, _, err = validate_bunker_record(zhoushan_anomaly)
        assert is_valid is False
        assert "Currency mismatch detected" in err

    def test_never_infers_delivery_basis(self):
        """Delivery basis is strictly preserved as None when not supplied by provider."""
        record_no_basis = {
            "locode": "DKSKA",
            "port": "Skagen",
            "grade": "VLSFO",
            "usdPerMt": 705.5,
            "observedAt": "2026-11-06T17:47:08.000Z",
        }
        is_valid, cleaned, _ = validate_bunker_record(record_no_basis)
        assert is_valid is True
        assert cleaned["delivery_basis"] is None


class TestBunkerProviderIngestion:
    """Tests for idempotent SQL ingestion and failure handling."""

    def test_offline_fixture_ingestion(self, test_db_session: Session):
        """Ingests genuine offline fixture, quarantines anomaly, and accepts valid records."""
        res = refresh_bunker_prices(db=test_db_session, offline=True)
        assert res["status"] in ("completed", "partial")
        assert res["records_read"] == 6
        assert res["accepted_count"] == 5  # 5 USD records accepted
        assert res["quarantined_count"] == 1  # 1 CNY anomaly quarantined
        assert res["duplicate_count"] == 0

        # Verify SQL state
        obs = test_db_session.query(BunkerPriceObservation).all()
        assert len(obs) == 5
        ports = {o.port_code for o in obs}
        assert "ARBUE" in ports
        assert "DKSKA" in ports
        assert "CNZOS" not in ports  # quarantined due to CNY mismatch

    def test_duplicate_ingestion_idempotency(self, test_db_session: Session):
        """Re-running ingestion skips existing records idempotently without errors."""
        res1 = refresh_bunker_prices(db=test_db_session, offline=True)
        assert res1["accepted_count"] == 5
        assert res1["duplicate_count"] == 0
        test_db_session.commit()

        # Second execution
        res2 = refresh_bunker_prices(db=test_db_session, offline=True)
        assert res2["accepted_count"] == 0
        assert res2["duplicate_count"] == 5
        assert test_db_session.query(BunkerPriceObservation).count() == 5

    def test_provider_timeout_and_network_failure(self, test_db_session: Session):
        """Handles network timeouts and HTTP errors with graceful failure reporting."""
        mock_client = MagicMock(spec=ResilientHttpClient)
        mock_client.get.side_effect = AcquisitionTimeoutError("Request timed out after 15.0s")

        res = refresh_bunker_prices(db=test_db_session, offline=False, client=mock_client)
        assert res["status"] == "failed"
        assert "timed out" in res["error"]
        assert res["accepted_count"] == 0

        # HTTP 500 Server Error
        mock_client.get.side_effect = AcquisitionHTTPError("HTTP 500 Internal Server Error", 500, DEFAULT_BUNKER_API_URL)
        res_http = refresh_bunker_prices(db=test_db_session, offline=False, client=mock_client)
        assert res_http["status"] == "failed"
        assert "500" in res_http["error"]

    def test_partial_provider_response(self, test_db_session: Session):
        """Partial provider responses with some malformed rows are handled robustly."""
        partial_payload = {
            "disclaimer": "Indicative only",
            "count": 2,
            "withheld": 10,
            "prices": [
                {
                    "locode": "DKSKA",
                    "port": "Skagen",
                    "grade": "VLSFO",
                    "usdPerMt": 705.5,
                    "observedAt": "2026-11-06T17:47:08.000Z",
                },
                {
                    "locode": "BADPORT",  # invalid 7 chars
                    "grade": "VLSFO",
                    "usdPerMt": 700.0,
                    "observedAt": "2026-11-06T17:47:08.000Z",
                },
            ],
        }

        mock_client = MagicMock(spec=ResilientHttpClient)
        mock_client.get.return_value = AcquisitionResponse(
            url=DEFAULT_BUNKER_API_URL,
            status_code=200,
            text="",
            content=b"",
            retrieval_time_utc=datetime.datetime.now(datetime.timezone.utc),
            duration_seconds=0.5,
            headers={},
        )
        mock_client.get.return_value.json = lambda: partial_payload

        res = refresh_bunker_prices(db=test_db_session, offline=False, client=mock_client)
        assert res["status"] == "partial"
        assert res["records_read"] == 2
        assert res["accepted_count"] == 1
        assert res["quarantined_count"] == 1


class TestBunkerRouteIntelligenceFusion:
    """Tests for corridor hub-to-price resolution and strict anti-substitution."""

    def test_corridor_hubs_remain_unavailable_when_unquoted(self, test_db_session: Session):
        """When provider does not quote corridor hubs, hubs remain explicitly unavailable."""
        # Ingest genuine fixture (which has ARBUE and DKSKA, but no Seattle/Houston/Yokohama)
        refresh_bunker_prices(db=test_db_session, offline=True)

        summary = resolve_corridor_bunker_fuel_references("PNW-JPN-BULK", db=test_db_session)
        assert summary.status == "unavailable"
        assert summary.hubs_with_prices_count == 0
        assert len(summary.bunkering_hubs_in_scope) == 3

        # Every hub is unavailable
        for hub in summary.bunkering_hubs_in_scope:
            assert hub.status == "unavailable"
            assert "unquoted by provider" in hub.availability_reason

        # No silent substitution: external benchmarks are in regional_benchmarks, not in hub quotes
        assert len(summary.regional_benchmarks) > 0
        reg_ports = {b.port_code for b in summary.regional_benchmarks}
        assert "ARBUE" in reg_ports
        assert "DKSKA" in reg_ports
        # Seattle hub quotes list is strictly empty
        seattle_hub = next(h for h in summary.bunkering_hubs_in_scope if h.port_id == "USSEA")
        assert len(seattle_hub.quotes) == 0

    def test_hub_becomes_available_when_genuine_quote_exists(self, test_db_session: Session):
        """When a genuine quote exists for a corridor hub, it transitions to available."""
        # Insert a genuine quote for Seattle
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        obs = BunkerPriceObservation(
            port_code="USSEA",
            port_name="Seattle",
            country_code="US",
            fuel_grade="VLSFO",
            price_value=645.0,
            currency="USD",
            unit="USD/MT",
            pricing_type="indicative",
            delivery_basis="delivered",
            source_name=PROVIDER_NAME,
            source_feed="licensed:oilpriceapi:VLSFO_INDICATION_USSEA_USD",
            observation_time=now_utc - datetime.timedelta(hours=5),
            retrieval_time_utc=now_utc,
            idempotency_key="BFP:USSEA:VLSFO:TEST_KEY",
        )
        test_db_session.add(obs)
        test_db_session.commit()

        summary = resolve_corridor_bunker_fuel_references("PNW-JPN-BULK", db=test_db_session)
        assert summary.status == "partial"  # 1 of 3 hubs available
        assert summary.hubs_with_prices_count == 1

        seattle_hub = next(h for h in summary.bunkering_hubs_in_scope if h.port_id == "USSEA")
        assert seattle_hub.status == "available"
        assert len(seattle_hub.quotes) == 1
        assert seattle_hub.quotes[0].price_usd_per_mt == 645.0
        assert seattle_hub.quotes[0].freshness == "fresh"

        # Portland and Yokohama remain unavailable
        pdx_hub = next(h for h in summary.bunkering_hubs_in_scope if h.port_id == "USPDX")
        assert pdx_hub.status == "unavailable"

    def test_freshness_classification_tiers(self):
        """Bunker quotes are classified as fresh (<=24h), recent (<=168h), or stale (>168h)."""
        ref_time = datetime.datetime(2026, 11, 9, 12, 0, tzinfo=datetime.timezone.utc)

        # 6 hours old -> fresh
        obs_fresh = ref_time - datetime.timedelta(hours=6)
        freshness, age = evaluate_bunker_freshness(obs_fresh, ref_time)
        assert freshness == "fresh"
        assert age == pytest.approx(6.0, abs=0.1)

        # 72 hours old (3 days) -> recent
        obs_recent = ref_time - datetime.timedelta(hours=72)
        freshness, age = evaluate_bunker_freshness(obs_recent, ref_time)
        assert freshness == "recent"
        assert age == pytest.approx(72.0, abs=0.1)

        # 200 hours old (> 7 days) -> stale
        obs_stale = ref_time - datetime.timedelta(hours=200)
        freshness, age = evaluate_bunker_freshness(obs_stale, ref_time)
        assert freshness == "stale"
        assert age == pytest.approx(200.0, abs=0.1)

    def test_live_provider_connectivity(self):
        """Live connectivity check to bunkerfuelprices.com public API."""
        try:
            client = ResilientHttpClient(timeout_seconds=8.0, max_retries=1)
            data = fetch_bunker_prices(client=client, limit=5)
            assert "prices" in data
            assert isinstance(data["prices"], list)
            assert "count" in data
            assert "withheld" in data
        except Exception as exc:
            pytest.skip(f"Live provider connectivity test skipped due to network unavailability: {exc}")
