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
from pelagos.models import BunkerPriceObservation, QuarantinedRecord
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

        # Explicit audit mode evaluates historical quotes without operational gating
        summary = resolve_corridor_bunker_fuel_references("PNW-JPN-BULK", db=test_db_session, operational_mode=False)
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

        # Explicit audit mode evaluates historical quote match
        summary = resolve_corridor_bunker_fuel_references("PNW-JPN-BULK", db=test_db_session, operational_mode=False)
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


# ===========================================================================
# Stage F1.4.2.1 Hardening & Data Quality Test Suites
# ===========================================================================

class TestBunkerCurrencyIntegrity:
    """Tests for Workstream B: explicit currency validation and anti-corruption."""

    def test_valid_usd_record_accepted(self):
        """Valid USD record with verified source ticker is accepted."""
        raw = {
            "locode": "USHOU",
            "port": "Houston",
            "country": "US",
            "grade": "VLSFO",
            "usdPerMt": 730.50,
            "currency": "USD",
            "source": "licensed:oilpriceapi:VLSFO_USHOU_USD",
            "observedAt": "2026-11-12T03:13:14.000Z",
        }
        is_valid, cleaned, err = validate_bunker_record(raw)
        assert is_valid is True
        assert err is None
        assert cleaned["currency"] == "USD"
        assert cleaned["price_value"] == 730.50

    def test_explicit_cny_mislabeled_as_usd_quarantine(self):
        """Explicit CNY indicator in ticker or metadata must NEVER be accepted as USD."""
        # Case 1: Ticker ending in _CNY with usdPerMt field
        shanghai_cny = {
            "locode": "CNSGH",
            "port": "Shanghai",
            "country": "CN",
            "grade": "HSFO",
            "usdPerMt": 5473.0,
            "source": "licensed:oilpriceapi:LSFO_380_CNSHA_BONDED_CNY",
            "observedAt": "2026-11-12T23:20:28.000Z",
        }
        is_valid, cleaned, err = validate_bunker_record(shanghai_cny)
        assert is_valid is False
        assert cleaned is None
        assert "Currency mismatch detected" in err
        assert "CNY" in err

        # Case 2: Currency field explicitly says CNY
        explicit_cny_curr = {
            "locode": "CNZOS",
            "port": "Zhoushan",
            "country": "CN",
            "grade": "MGO",
            "usdPerMt": 6200.0,
            "currency": "CNY",
            "source": "licensed:feed:MGO_CNZOS",
            "observedAt": "2026-11-12T10:00:00.000Z",
        }
        is_valid2, _, err2 = validate_bunker_record(explicit_cny_curr)
        assert is_valid2 is False
        assert "Currency mismatch detected" in err2

    def test_conflicting_ticker_and_currency_metadata(self):
        """Conflicting currency indicators (e.g. currency=USD but ticker is _CNY) are quarantined."""
        conflict_record = {
            "locode": "CNZOS",
            "port": "Zhoushan",
            "grade": "VLSFO",
            "usdPerMt": 6028.0,
            "currency": "USD",
            "source": "licensed:oilpriceapi:LSFO_380_CNZOS_BONDED_SELLER_CNY",
            "observedAt": "2026-11-12T10:00:00.000Z",
        }
        is_valid, cleaned, err = validate_bunker_record(conflict_record)
        assert is_valid is False
        assert "Currency mismatch detected" in err or "Conflicting currency" in err

    def test_non_usd_tickers_without_conversion_quarantined(self):
        """Non-USD tickers (e.g. _EUR, _JPY) without verified conversion are quarantined."""
        eur_record = {
            "locode": "NLRTM",
            "port": "Rotterdam",
            "grade": "VLSFO",
            "price": 580.0,
            "currency": "EUR",
            "source": "licensed:feed:VLSFO_NLRTM_EUR",
            "observedAt": "2026-11-12T10:00:00.000Z",
        }
        is_valid, _, err = validate_bunker_record(eur_record)
        assert is_valid is False
        assert "Currency contradiction" in err or "Conflicting currency" in err

    def test_missing_currency_quarantined(self):
        """Missing currency metadata that cannot be resolved safely is quarantined."""
        missing_curr = {
            "locode": "SGSIN",
            "port": "Singapore",
            "grade": "VLSFO",
            "price": 620.0,
            "currency": "",
            "source": "unspecified_feed_without_currency",
            "observedAt": "2026-11-12T10:00:00.000Z",
        }
        is_valid, _, err = validate_bunker_record(missing_curr)
        assert is_valid is False
        assert "Missing explicit currency specification" in err


class TestBunkerUnitsAndPricingValidation:
    """Tests for Workstream C: price and mass unit validation."""

    def test_valid_mass_units_accepted(self):
        """USD/MT and standard mass unit variations are accepted."""
        for u in ("USD/MT", "$/MT", "MT", "USD/TONNE"):
            raw = {
                "locode": "USHOU",
                "grade": "VLSFO",
                "usdPerMt": 710.0,
                "unit": u,
                "observedAt": "2026-11-12T03:00:00Z",
            }
            is_valid, cleaned, _ = validate_bunker_record(raw)
            assert is_valid is True
            assert cleaned["unit"] == "USD/MT"

    def test_unsupported_volumetric_bbl_unit_rejection(self):
        """USD/BBL volumetric prices are rejected because mass conversion requires density."""
        bbl_record = {
            "locode": "USHOU",
            "grade": "VLSFO",
            "usdPerMt": 95.0,
            "unit": "USD/BBL",
            "observedAt": "2026-11-12T03:00:00Z",
        }
        is_valid, _, err = validate_bunker_record(bbl_record)
        assert is_valid is False
        assert "Unsupported volumetric unit" in err
        assert "API gravity and temperature density" in err

    def test_unsupported_volumetric_gal_unit_rejection(self):
        """USD/GAL volumetric prices are rejected because gallons cannot be converted without density."""
        gal_record = {
            "locode": "USHOU",
            "grade": "MGO",
            "usdPerMt": 2.85,
            "unit": "USD/GAL",
            "observedAt": "2026-11-12T03:00:00Z",
        }
        is_valid, _, err = validate_bunker_record(gal_record)
        assert is_valid is False
        assert "Unsupported volumetric unit" in err
        assert "density" in err

    def test_non_positive_and_non_numeric_prices(self):
        """Zero, negative, and non-numeric prices trigger hard validation failures."""
        for bad_p in (0.0, -100.0, "N/A", None):
            raw = {"locode": "USSEA", "grade": "VLSFO", "usdPerMt": bad_p, "observedAt": "2026-11-12T00:00:00Z"}
            is_valid, _, _ = validate_bunker_record(raw)
            assert is_valid is False

    def test_plausible_unusual_price_warning_vs_hard_rejection(self):
        """Distinguishes unusual but valid market prices from implausible hard rejections."""
        # 1. Unusually high price ($3,500/MT) -> Accepted with anomaly warning
        high_price = {
            "locode": "ARBUE",
            "grade": "MGO",
            "usdPerMt": 3500.0,
            "currency": "USD",
            "source": "licensed:feed:MGO_USD",
            "observedAt": "2026-11-12T00:00:00Z",
        }
        is_valid, cleaned, err = validate_bunker_record(high_price)
        assert is_valid is True
        assert err is None
        assert any("ANOMALY_WARNING" in w for w in cleaned.get("anomaly_warnings", []))

        # 2. Implausibly high price ($5,500/MT) -> Hard rejection
        extreme_high = {
            "locode": "ARBUE",
            "grade": "MGO",
            "usdPerMt": 5500.0,
            "observedAt": "2026-11-12T00:00:00Z",
        }
        assert validate_bunker_record(extreme_high)[0] is False

        # 3. Implausibly low price ($30/MT) -> Hard rejection
        extreme_low = {
            "locode": "ARBUE",
            "grade": "HSFO",
            "usdPerMt": 30.0,
            "observedAt": "2026-11-12T00:00:00Z",
        }
        assert validate_bunker_record(extreme_low)[0] is False


class TestBunkerFuelGradeNormalization:
    """Tests for Workstream D: marine fuel grade normalization and segregation."""

    def test_vlsfo_normalization(self):
        """VLSFO and VLSFO_05 normalize to canonical VLSFO with 0.50% S specification."""
        for g in ("VLSFO", "VLSFO_05", "VLSFO 0.5%"):
            raw = {"locode": "USHOU", "grade": g, "usdPerMt": 720.0, "observedAt": "2026-11-12T00:00:00Z"}
            is_valid, cleaned, _ = validate_bunker_record(raw)
            assert is_valid is True
            assert cleaned["fuel_grade"] == "VLSFO"
            assert cleaned["original_fuel_grade"] == g

    def test_hsfo_normalization(self):
        """IFO380, HFO, and LSFO_380 normalize to canonical HSFO with 3.50% S specification."""
        for g in ("HSFO", "IFO380", "IFO 380", "HFO", "HFO_380"):
            raw = {"locode": "USHOU", "grade": g, "usdPerMt": 480.0, "observedAt": "2026-11-12T00:00:00Z"}
            is_valid, cleaned, _ = validate_bunker_record(raw)
            assert is_valid is True
            assert cleaned["fuel_grade"] == "HSFO"

    def test_mgo_normalization(self):
        """LSMGO, DMA, and DMZ normalize to canonical MGO with 0.10% S specification."""
        for g in ("MGO", "LSMGO", "DMA", "DMZ"):
            raw = {"locode": "USHOU", "grade": g, "usdPerMt": 1200.0, "observedAt": "2026-11-12T00:00:00Z"}
            is_valid, cleaned, _ = validate_bunker_record(raw)
            assert is_valid is True
            assert cleaned["fuel_grade"] == "MGO"

    def test_mdo_distinct_from_mgo_never_merged(self):
        """MDO is recognized as a distinct marine grade and NEVER merged with MGO."""
        raw_mdo = {"locode": "USHOU", "grade": "MDO", "usdPerMt": 1100.0, "observedAt": "2026-11-12T00:00:00Z"}
        is_valid, cleaned, _ = validate_bunker_record(raw_mdo)
        assert is_valid is True
        assert cleaned["fuel_grade"] == "MDO"
        assert cleaned["fuel_grade"] != "MGO"

        raw_dmb = {"locode": "USHOU", "grade": "DMB", "usdPerMt": 1090.0, "observedAt": "2026-11-12T00:00:00Z"}
        is_valid_dmb, cleaned_dmb, _ = validate_bunker_record(raw_dmb)
        assert is_valid_dmb is True
        assert cleaned_dmb["fuel_grade"] == "MDO"
        assert cleaned_dmb["fuel_grade"] != "MGO"

    def test_unknown_and_generic_fuels_rejected(self):
        """Generic road diesel, crude proxies, and ambiguous fuel descriptions are strictly rejected."""
        for rejected in ("ROAD_DIESEL", "DIESEL", "AUTOMOTIVE_DIESEL", "ULSD", "BRENT", "WTI", "CRUDE", "FUEL_OIL", "RESIDUAL"):
            raw = {"locode": "USSEA", "grade": rejected, "usdPerMt": 600.0, "observedAt": "2026-11-12T00:00:00Z"}
            is_valid, cleaned, err = validate_bunker_record(raw)
            assert is_valid is False
            assert cleaned is None
            assert "Unrecognized or unsupported fuel grade" in err or "Ambiguous" in err


class TestBunkerPortResolutionAndAntiSubstitution:
    """Tests for Workstream E: port identity validation without geographic substitution."""

    def test_canonical_priority_ports_resolved(self):
        """Priority hubs resolve to verified canonical names and country codes."""
        priority_checks = [
            ("USSEA", "Seattle", "US"),
            ("USPDX", "Portland", "US"),
            ("USHOU", "Houston", "US"),
            ("USMSY", "New Orleans", "US"),
            ("JPYOK", "Yokohama", "JP"),
        ]
        for code, expected_name, expected_country in priority_checks:
            raw = {"locode": code, "grade": "VLSFO", "usdPerMt": 650.0, "observedAt": "2026-11-12T00:00:00Z"}
            is_valid, cleaned, _ = validate_bunker_record(raw)
            assert is_valid is True
            assert cleaned["port_code"] == code
            assert cleaned["port_name"] == expected_name
            assert cleaned["country_code"] == expected_country

    def test_strict_no_nearby_port_substitution(self):
        """Tacoma is not substituted for Seattle, Vancouver is not substituted for Portland, Galveston is not substituted for Houston."""
        # Tacoma (USTAC) retains its exact UN/LOCODE, never substituted for USSEA
        raw_tacoma = {"locode": "USTAC", "port": "Tacoma", "grade": "VLSFO", "usdPerMt": 660.0, "observedAt": "2026-11-12T00:00:00Z"}
        _, cl_tac, _ = validate_bunker_record(raw_tacoma)
        assert cl_tac["port_code"] == "USTAC"
        assert cl_tac["port_code"] != "USSEA"

        # Galveston (USGAL) retains its exact UN/LOCODE, never substituted for USHOU
        raw_galveston = {"locode": "USGAL", "port": "Galveston", "grade": "VLSFO", "usdPerMt": 690.0, "observedAt": "2026-11-12T00:00:00Z"}
        _, cl_gal, _ = validate_bunker_record(raw_galveston)
        assert cl_gal["port_code"] == "USGAL"
        assert cl_gal["port_code"] != "USHOU"

    def test_unknown_port_rejected(self):
        """Non-UN/LOCODE port strings are rejected."""
        for bad_code in ("SEA1", "PORT_123", "BADPORT", ""):
            raw = {"locode": bad_code, "grade": "VLSFO", "usdPerMt": 650.0, "observedAt": "2026-11-12T00:00:00Z"}
            is_valid, _, err = validate_bunker_record(raw)
            assert is_valid is False
            assert "Invalid UN/LOCODE" in err


class TestBunkerFreshnessAndProvenanceIntegrity:
    """Tests for Workstream F: observation freshness and provenance preservation."""

    def test_freshness_classification_tiers(self):
        """Correctly classifies fresh (<=24h), recent (<=168h), stale (>168h), and unknown."""
        ref = datetime.datetime(2026, 11, 13, 12, 0, tzinfo=datetime.timezone.utc)

        # Fresh (12 hours old)
        t_fresh = ref - datetime.timedelta(hours=12)
        f_tier, age = evaluate_bunker_freshness(t_fresh, ref)
        assert f_tier == "fresh"
        assert age == pytest.approx(12.0, abs=0.1)

        # Recent (48 hours old)
        t_recent = ref - datetime.timedelta(hours=48)
        f_tier, age = evaluate_bunker_freshness(t_recent, ref)
        assert f_tier == "recent"
        assert age == pytest.approx(48.0, abs=0.1)

        # Stale (240 hours old)
        t_stale = ref - datetime.timedelta(hours=240)
        f_tier, age = evaluate_bunker_freshness(t_stale, ref)
        assert f_tier == "stale"
        assert age == pytest.approx(240.0, abs=0.1)

        # Unknown
        f_tier, age = evaluate_bunker_freshness(None, ref)
        assert f_tier == "unknown"
        assert age == -1.0

    def test_provenance_notes_preservation(self):
        """Preserves original raw grade, currency, unit, and ISO designation in structured provenance."""
        import json
        raw = {
            "locode": "USHOU",
            "grade": "IFO380",
            "usdPerMt": 490.0,
            "currency": "USD",
            "unit": "USD/MT",
            "provenance": "Authentic physical supplier indication",
            "observedAt": "2026-11-12T00:00:00Z",
        }
        is_valid, cleaned, _ = validate_bunker_record(raw)
        assert is_valid is True
        notes = json.loads(cleaned["provenance_notes"])
        assert notes["original_fuel_grade"] == "IFO380"
        assert notes["canonical_fuel_grade"] == "HSFO"
        assert notes["max_sulfur_pct"] == 3.50
        assert notes["provider_provenance"] == "Authentic physical supplier indication"


class TestBunkerIngestionAndQuarantineHardening:
    """Tests for Workstream G & H: withholding visibility, failure isolation, and quarantine audit."""

    def test_withholding_and_coverage_visibility(self, test_db_session: Session):
        """Exposes published count, withheld count, withheld reason, and priority hub availability."""
        mock_payload = {
            "disclaimer": "Indicative only",
            "count": 3,
            "withheld": 155,
            "withheldReason": "Generated or unverified figures are never served here.",
            "prices": [
                {"locode": "USHOU", "grade": "HSFO", "usdPerMt": 494.5, "observedAt": "2026-11-12T03:00:00Z"},
                {"locode": "USHOU", "grade": "VLSFO", "usdPerMt": 730.5, "observedAt": "2026-11-12T03:00:00Z"},
                {"locode": "USHOU", "grade": "MGO", "usdPerMt": 1261.5, "observedAt": "2026-11-12T03:00:00Z"},
            ],
        }
        mock_client = MagicMock(spec=ResilientHttpClient)
        mock_client.get.return_value = AcquisitionResponse(
            url=DEFAULT_BUNKER_API_URL,
            status_code=200,
            text="",
            content=b"",
            retrieval_time_utc=datetime.datetime.now(datetime.timezone.utc),
            duration_seconds=0.3,
            headers={},
        )
        mock_client.get.return_value.json = lambda: mock_payload

        res = refresh_bunker_prices(db=test_db_session, offline=False, client=mock_client)
        assert res["status"] == "completed"
        assert res["records_read"] == 3
        assert res["accepted_count"] == 3
        assert res["withheld_by_provider"] == 155
        assert "Generated or unverified" in res["withheld_reason"]
        assert "USHOU" in res["coverage_by_port"]
        assert set(res["coverage_by_port"]["USHOU"]) == {"HSFO", "VLSFO", "MGO"}

        # Unquoted priority hubs are explicitly listed
        unavail = res["unavailable_priority_hubs"]
        assert "USSEA" in unavail
        assert "USPDX" in unavail
        assert "USMSY" in unavail
        assert "JPYOK" in unavail
        assert "USHOU" not in unavail

    def test_quarantine_isolation_valid_records_retained(self, test_db_session: Session):
        """When an invalid or anomalous record appears, it is quarantined while valid records are committed."""
        mixed_payload = {
            "prices": [
                # Valid Houston quote
                {"locode": "USHOU", "grade": "VLSFO", "usdPerMt": 725.0, "observedAt": "2026-11-12T00:00:00Z"},
                # CNY currency mismatch anomaly (must be quarantined!)
                {
                    "locode": "CNZOS",
                    "grade": "HSFO",
                    "usdPerMt": 6028.0,
                    "source": "licensed:feed:HSFO_CNZOS_CNY",
                    "observedAt": "2026-11-12T00:00:00Z",
                },
                # Valid Buenos Aires quote
                {"locode": "ARBUE", "grade": "MGO", "usdPerMt": 1900.0, "observedAt": "2026-11-12T00:00:00Z"},
            ]
        }
        mock_client = MagicMock(spec=ResilientHttpClient)
        mock_client.get.return_value = AcquisitionResponse(
            url=DEFAULT_BUNKER_API_URL,
            status_code=200,
            text="",
            content=b"",
            retrieval_time_utc=datetime.datetime.now(datetime.timezone.utc),
            duration_seconds=0.2,
            headers={},
        )
        mock_client.get.return_value.json = lambda: mixed_payload

        res = refresh_bunker_prices(db=test_db_session, offline=False, client=mock_client)
        assert res["status"] == "partial"
        assert res["records_read"] == 3
        assert res["accepted_count"] == 2
        assert res["quarantined_count"] == 1

        # Check SQL database state: only the 2 valid records are persisted
        persisted = test_db_session.query(BunkerPriceObservation).all()
        assert len(persisted) == 2
        persisted_ports = {p.port_code for p in persisted}
        assert "USHOU" in persisted_ports
        assert "ARBUE" in persisted_ports
        assert "CNZOS" not in persisted_ports

        # Quarantine log contains the Zhoushan anomaly with exact reason
        assert len(res["quarantine_log"]) == 1
        q_entry = res["quarantine_log"][0]
        assert q_entry["raw_record"]["locode"] == "CNZOS"
        assert "Currency mismatch detected" in q_entry["reason"]


class TestBunkerHistoricalProtection:
    """Tests for Workstream H & Stage F1.4.2.1 historical integrity protection."""

    def test_existing_five_genuine_observations_remain_unchanged(self, test_db_session: Session):
        """Authentic offline fixture ingestion produces exactly the 5 genuine observations and 1 quarantine."""
        res = refresh_bunker_prices(db=test_db_session, offline=True)
        assert res["records_read"] == 6
        assert res["accepted_count"] == 5
        assert res["quarantined_count"] == 1

        obs = test_db_session.query(BunkerPriceObservation).all()
        assert len(obs) == 5

        # Confirm exact ports and grades match historical baseline
        records = {(o.port_code, o.fuel_grade): o.price_value for o in obs}
        assert ("ARBUE", "MGO") in records and records[("ARBUE", "MGO")] == 1933.56
        assert ("ARBUE", "VLSFO") in records and records[("ARBUE", "VLSFO")] == 957.50
        assert ("DKSKA", "HSFO") in records and records[("DKSKA", "HSFO")] == 608.50
        assert ("DKSKA", "MGO") in records and records[("DKSKA", "MGO")] == 1391.00
        assert ("DKSKA", "VLSFO") in records and records[("DKSKA", "VLSFO")] == 705.50

    def test_zhoushan_quarantine_evidence_available_for_audit(self, test_db_session: Session):
        """Zhoushan record is preserved in quarantine log and never dropped silently."""
        res = refresh_bunker_prices(db=test_db_session, offline=True)
        assert len(res["quarantine_log"]) == 1
        zhoushan_q = res["quarantine_log"][0]
        assert zhoushan_q["raw_record"]["locode"] == "CNZOS"
        assert zhoushan_q["raw_record"]["usdPerMt"] == 6028
        assert "Currency mismatch detected" in zhoushan_q["reason"]
        assert "CNY" in zhoushan_q["reason"]

    def test_historical_raw_payload_unmodified(self):
        """Raw fixture file genuine_bunker_prices.json remains authentic and unaltered."""
        fixture_path = Path(__file__).resolve().parent.parent / "backend" / "pelagos" / "data" / "genuine_bunker_prices.json"
        assert fixture_path.exists()
        import json
        with open(fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["count"] == 6
        assert len(data["prices"]) == 6
        assert data["prices"][5]["locode"] == "CNZOS"
        assert data["prices"][5]["usdPerMt"] == 6028


class TestBunkerQuarantinePersistence:
    """Tests for Stage F1.4.2.1 Addendum: persistent quarantine storage and restart survival."""

    def test_quarantine_raw_payload_stored_durably_in_db(self, test_db_session: Session):
        """Quarantined raw payload and rejection details are durably stored in QuarantinedRecord."""
        res = refresh_bunker_prices(db=test_db_session, offline=True)
        assert res["quarantined_count"] == 1

        # Query SQL QuarantinedRecord directly
        q_records = test_db_session.query(QuarantinedRecord).all()
        assert len(q_records) == 1
        q = q_records[0]
        assert q.source_name == "Bunker Fuel Prices (bunkerfuelprices.com)"
        assert q.error_code == "CURRENCY_CONTRADICTION"
        assert "Currency mismatch detected" in q.error_details

        # Verify raw payload recovery
        import json
        recovered = json.loads(q.raw_payload)
        assert recovered["locode"] == "CNZOS"
        assert recovered["grade"] == "HSFO"
        assert recovered["usdPerMt"] == 6028
        assert "CNY" in recovered["source"]

    def test_quarantine_rejection_reasons_survive_application_restart(self, tmp_path):
        """Rejection reasons and raw evidence survive complete engine disposal and application restart."""
        import json
        db_file = tmp_path / "isolated_restart_bunker_test.db"
        db_url = f"sqlite:///{db_file}"

        # 1. Process 1: Ingest, quarantine, flush, and commit
        engine1 = create_engine(db_url)
        Base.metadata.create_all(engine1)
        Session1 = sessionmaker(bind=engine1)
        session1 = Session1()

        res1 = refresh_bunker_prices(db=session1, offline=True)
        assert res1["accepted_count"] == 5
        assert res1["quarantined_count"] == 1
        session1.commit()
        session1.close()
        engine1.dispose()

        # 2. Process 2 (Application Restart): New engine & session connecting to the disk database
        engine2 = create_engine(db_url)
        Session2 = sessionmaker(bind=engine2)
        session2 = Session2()

        # Verify quarantined record survived restart
        restarted_q = session2.query(QuarantinedRecord).all()
        assert len(restarted_q) == 1
        q = restarted_q[0]
        assert q.error_code == "CURRENCY_CONTRADICTION"
        assert "CNY" in q.error_details
        payload = json.loads(q.raw_payload)
        assert payload["locode"] == "CNZOS"
        assert payload["usdPerMt"] == 6028

        # Verify canonical bunker prices in session 2: only 5 valid records, NO CNZOS
        obs2 = session2.query(BunkerPriceObservation).all()
        assert len(obs2) == 5
        locodes2 = {o.port_code for o in obs2}
        assert "ARBUE" in locodes2
        assert "DKSKA" in locodes2
        assert "CNZOS" not in locodes2

        # 3. Repeat Ingestion Idempotency: re-running ingestion does not duplicate quarantine or observation rows
        res2 = refresh_bunker_prices(db=session2, offline=True)
        session2.commit()
        assert res2["accepted_count"] == 0
        assert res2["duplicate_count"] == 5

        # Confirm exact row counts are unchanged
        assert session2.query(QuarantinedRecord).count() == 1
        assert session2.query(BunkerPriceObservation).count() == 5

        session2.close()
        engine2.dispose()

    def test_quarantined_records_cannot_enter_bunker_price_observations(self, test_db_session: Session):
        """Strict isolation guarantee: quarantined records never enter canonical observations."""
        mixed_payload = {
            "prices": [
                {"locode": "USHOU", "grade": "VLSFO", "usdPerMt": 725.0, "observedAt": "2026-11-12T00:00:00Z"},
                {"locode": "CNZOS", "grade": "HSFO", "usdPerMt": 6028, "source": "feed_CNY", "observedAt": "2026-11-12T00:00:00Z"},
                {"locode": "USSEA", "grade": "BRENT", "usdPerMt": 80.0, "observedAt": "2026-11-12T00:00:00Z"},
                {"locode": "USSEA", "grade": "VLSFO", "usdPerMt": 5500.0, "observedAt": "2026-11-12T00:00:00Z"},
            ]
        }
        mock_client = MagicMock(spec=ResilientHttpClient)
        mock_client.get.return_value.json = lambda: mixed_payload

        res = refresh_bunker_prices(db=test_db_session, offline=False, client=mock_client)
        assert res["accepted_count"] == 1
        assert res["quarantined_count"] == 3

        # SQL checks
        obs = test_db_session.query(BunkerPriceObservation).all()
        assert len(obs) == 1
        assert obs[0].port_code == "USHOU"
        assert obs[0].fuel_grade == "VLSFO"

        q_recs = test_db_session.query(QuarantinedRecord).all()
        assert len(q_recs) == 3
        q_codes = {q.error_code for q in q_recs}
        assert "CURRENCY_CONTRADICTION" in q_codes
        assert "UNSUPPORTED_FUEL_GRADE" in q_codes
        assert "EXTREME_PRICE_REQUIRES_MANUAL_REVIEW" in q_codes


class TestBunkerFuelSpecificationSemantics:
    """Tests for Stage F1.4.2.1 Addendum: rigorous fuel specification concept demarcation."""

    def test_generic_label_does_not_infer_source_reported_specs(self):
        """Generic grade label does NOT infer source-reported sulfur content or ISO spec."""
        import json
        raw = {
            "locode": "USHOU",
            "grade": "VLSFO",
            "usdPerMt": 725.0,
            "observedAt": "2026-11-12T00:00:00Z",
        }
        is_valid, cleaned, _ = validate_bunker_record(raw)
        assert is_valid is True
        notes = json.loads(cleaned["provenance_notes"])
        assert notes["canonical_fuel_grade"] == "VLSFO"
        assert notes["source_reported_iso_designation"] is None
        assert notes["source_reported_sulfur_pct"] is None
        assert notes["verified_blend_composition"] is None
        assert notes["nominal_max_sulfur_spec"] == 0.50
        assert notes["max_sulfur_pct"] == 0.50
        assert notes["regulatory_max_sulfur_pct"] is None
        assert notes["nominal_iso_reference"] == "ISO 8217 RMG/RME 0.5% S"
        reg = notes["regulatory_assessment"]
        assert reg["status"] == "CONTEXT_DEPENDENT"
        assert reg["global_marpol_sulfur_limit_pct"] == 0.50
        assert reg["applicable_eca_sulfur_limit_pct"] == 0.10
        assert reg["compliance_arrangement"] == "UNKNOWN"
        assert reg["route_location_context"] == "UNAVAILABLE"

    def test_explicit_source_reported_specs_preserved(self):
        """When source payload explicitly provides sulfur and ISO spec, they are preserved accurately."""
        import json
        raw = {
            "locode": "SGSIN",
            "grade": "HSFO",
            "usdPerMt": 485.0,
            "iso_designation": "ISO 8217:2017 RMG 380",
            "sulfur_pct": 2.45,
            "observedAt": "2026-11-12T00:00:00Z",
        }
        is_valid, cleaned, _ = validate_bunker_record(raw)
        assert is_valid is True
        notes = json.loads(cleaned["provenance_notes"])
        assert notes["canonical_fuel_grade"] == "HSFO"
        assert notes["source_reported_iso_designation"] == "ISO 8217:2017 RMG 380"
        assert notes["source_reported_sulfur_pct"] == 2.45
        assert notes["nominal_max_sulfur_spec"] == 3.50
        assert notes["max_sulfur_pct"] == 3.50
        assert notes["regulatory_max_sulfur_pct"] is None
        reg = notes["regulatory_assessment"]
        assert reg["status"] == "CONTEXT_DEPENDENT"
        assert reg["scrubber_equivalence_applicable"] is True

    def test_biofuel_blend_composition_unknown_unless_explicit(self):
        """Biofuel blend composition is unknown (None) unless explicitly reported by provider."""
        import json
        # Generic B24 without blend composition
        raw_generic_bio = {
            "locode": "SGSIN",
            "grade": "B24",
            "usdPerMt": 850.0,
            "observedAt": "2026-11-12T00:00:00Z",
        }
        is_valid, cleaned, _ = validate_bunker_record(raw_generic_bio)
        assert is_valid is True
        notes = json.loads(cleaned["provenance_notes"])
        assert notes["canonical_fuel_grade"] == "B24"
        assert notes["verified_blend_composition"] is None

        # Explicit certified blend composition
        raw_certified_bio = {
            "locode": "SGSIN",
            "grade": "B24",
            "usdPerMt": 850.0,
            "blend_composition": "24% UCOME / 76% VLSFO",
            "observedAt": "2026-11-12T00:00:00Z",
        }
        is_valid, cleaned_c, _ = validate_bunker_record(raw_certified_bio)
        assert is_valid is True
        notes_c = json.loads(cleaned_c["provenance_notes"])
        assert notes_c["verified_blend_composition"] == "24% UCOME / 76% VLSFO"


class TestBunkerPriceThresholdReview:
    """Tests for Stage F1.4.2.1 Addendum: price threshold review and category separation."""

    def test_extreme_price_quarantined_for_manual_review_not_discarded(self):
        """Prices exceeding $5,000/MT are quarantined with EXTREME_PRICE_REQUIRES_MANUAL_REVIEW."""
        raw_extreme = {
            "locode": "ARBUE",
            "grade": "MGO",
            "usdPerMt": 5500.0,
            "observedAt": "2026-11-12T00:00:00Z",
        }
        is_valid, _, reason = validate_bunker_record(raw_extreme)
        assert is_valid is False
        assert "EXTREME_PRICE_REQUIRES_MANUAL_REVIEW" in reason
        assert "exceeds standard operational review threshold ($5,000/MT)" in reason

    def test_price_categories_separated_distinctly(self):
        """Separates structurally invalid, non-positive, currency errors, volumetric errors, unusual prices, and extreme review prices."""
        # 1. Structural error (non-numeric)
        is_valid, _, err = validate_bunker_record({"locode": "USSEA", "grade": "VLSFO", "usdPerMt": "INVALID", "observedAt": "2026-11-12T00:00:00Z"})
        assert is_valid is False and "STRUCTURAL_INVALID_PRICE" in err

        # 2. Non-positive price
        is_valid, _, err = validate_bunker_record({"locode": "USSEA", "grade": "VLSFO", "usdPerMt": 0.0, "observedAt": "2026-11-12T00:00:00Z"})
        assert is_valid is False and "NON_POSITIVE_PRICE" in err

        # 3. Currency contradiction
        is_valid, _, err = validate_bunker_record({"locode": "CNZOS", "grade": "HSFO", "usdPerMt": 6028, "source": "feed_CNY", "observedAt": "2026-11-12T00:00:00Z"})
        assert is_valid is False and "Currency mismatch detected" in err

        # 4. Volumetric error
        is_valid, _, err = validate_bunker_record({"locode": "USSEA", "grade": "VLSFO", "usdPerMt": 95.0, "unit": "USD/BBL", "observedAt": "2026-11-12T00:00:00Z"})
        assert is_valid is False and "Unsupported volumetric unit" in err

        # 5. Unusual price level ($3,500/MT) -> Accepted with warning
        is_valid, cleaned, err = validate_bunker_record({"locode": "ARBUE", "grade": "MGO", "usdPerMt": 3500.0, "observedAt": "2026-11-12T00:00:00Z"})
        assert is_valid is True and any("UNUSUAL_PRICE_LEVEL" in w for w in cleaned["anomaly_warnings"])

        # 6. Extreme price review (> $5,000/MT) -> Quarantined for review
        is_valid, _, err = validate_bunker_record({"locode": "ARBUE", "grade": "MGO", "usdPerMt": 6000.0, "observedAt": "2026-11-12T00:00:00Z"})
        assert is_valid is False and "EXTREME_PRICE_REQUIRES_MANUAL_REVIEW" in err


class TestBunkerProviderTermsAndPermittedUse:
    """Tests for Stage F1.4.2.1 Addendum: provider terms and permitted use compliance."""

    def test_licensing_status_and_permitted_use_flags(self, test_db_session: Session):
        """Integration reports PENDING_PROVIDER_PERMISSION and explicitly tracks permitted use restrictions."""
        res = refresh_bunker_prices(db=test_db_session, offline=True)
        assert res["licensing_status"] == "PENDING_PROVIDER_PERMISSION"
        permitted = res["permitted_use"]
        assert permitted["automated_api_retrieval"] is False
        assert permitted["repeated_polling"] is False
        assert permitted["retention_of_observations"] is False
        assert permitted["competing_service_usage"] is False
        assert permitted["status"] == "PENDING_PROVIDER_PERMISSION"
        assert "Section 05" in permitted["terms_reference"]


class TestBunkerOfflineFixtureGovernance:
    """Tests for Stage F1.4.2.1 Final Closure: offline fixture governance and operational isolation."""

    def test_fixture_governance_metadata_in_refresh_result(self, test_db_session: Session):
        """refresh_bunker_prices exposes operational availability status and governance role."""
        res = refresh_bunker_prices(db=test_db_session, offline=True)
        assert res["operational_availability"] == "UNAVAILABLE"
        assert res["operational_use_permitted"] is False
        assert res["data_governance_role"] == "HISTORICAL_AUDIT_EVIDENCE"

    def test_provider_derived_observations_barred_from_operational_calculations(self, test_db_session: Session):
        """Calling resolve_corridor_bunker_fuel_references in operational_mode bars restricted observations."""
        from pelagos.routing.bunker import resolve_corridor_bunker_fuel_references

        # Refresh to populate DB with the 5 offline observations
        refresh_bunker_prices(db=test_db_session, offline=True)

        # Operational mode: True -> restricted provider observations CANNOT enter operational calculations
        summary_op = resolve_corridor_bunker_fuel_references(
            db=test_db_session,
            corridor_code="PNW-JPN-BULK",
            operational_mode=True,
        )
        assert summary_op.operational_availability == "UNAVAILABLE"
        assert summary_op.operational_use_permitted is False
        assert summary_op.data_governance_role == "RESTRICTED_HISTORICAL_AUDIT_ONLY"
        assert summary_op.status == "unavailable"
        # In operational mode, all hubs must be reported as unavailable because provider is restricted
        assert all(h.status == "unavailable" for h in summary_op.bunkering_hubs_in_scope)
        assert len(summary_op.latest_quotes) == 0

        # Non-operational / audit mode: False -> returns observations for historical audit evaluation
        summary_audit = resolve_corridor_bunker_fuel_references(
            db=test_db_session,
            corridor_code="PNW-JPN-BULK",
            operational_mode=False,
        )
        assert summary_audit.operational_availability == "RESTRICTED_HISTORICAL_AUDIT_ONLY"
        assert summary_audit.operational_use_permitted is False
        assert summary_audit.data_governance_role == "HISTORICAL_AUDIT_EVIDENCE"

    def test_default_operational_mode_is_fail_closed(self, test_db_session: Session):
        """Calling resolve_corridor_bunker_fuel_references without operational_mode defaults to fail-closed True."""
        from pelagos.routing.bunker import resolve_corridor_bunker_fuel_references

        refresh_bunker_prices(db=test_db_session, offline=True)

        # Omit operational_mode parameter entirely: must default to operational_mode=True
        summary_default = resolve_corridor_bunker_fuel_references(
            db=test_db_session,
            corridor_code="PNW-JPN-BULK",
        )
        assert summary_default.operational_availability == "UNAVAILABLE"
        assert summary_default.operational_use_permitted is False
        assert summary_default.data_governance_role == "RESTRICTED_HISTORICAL_AUDIT_ONLY"
        assert summary_default.status == "unavailable"
        assert all(h.status == "unavailable" for h in summary_default.bunkering_hubs_in_scope)
        assert len(summary_default.latest_quotes) == 0

    def test_historical_audit_observations_preserved_unaltered(self):
        """Confirms genuine_bunker_prices.json contains exactly the 6 historical audit records uncorrupted."""
        import json
        from pelagos.ingestion.acquisition.bunker_provider import DEFAULT_FIXTURE_PATH

        with open(DEFAULT_FIXTURE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        prices = data["prices"]
        assert len(prices) == 6
        locodes = [p["locode"] for p in prices]
        assert locodes.count("ARBUE") == 2
        assert locodes.count("DKSKA") == 3
        assert locodes.count("CNZOS") == 1
