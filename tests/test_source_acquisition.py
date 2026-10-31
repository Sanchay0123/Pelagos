"""Comprehensive test suite for Pelagos Automated Multi-Source Data Acquisition (Stage B).

Verifies:
1. ResilientHttpClient: bounded timeouts, exponential backoff retries, HTTP error handling, content validation.
2. NOAA NDBC Environmental Buoy Adapter:
   - Parsing standard meteorological realtime stream.
   - Physical boundary validation (wind, wave, pressure).
   - Unit conversion (m/s to knots).
   - Incomplete and malformed line handling.
   - Empty response handling without destructive overwrite.
   - Separation of observation timestamp and retrieval timestamp.
   - Idempotent deduplication and updates.
   - Transaction rollback and preservation of existing data upon failure.
   - Offline fallback mode.
3. Bilateral Trade Adapter:
   - Transparent credential requirement reporting (requires_credentials).
   - Safe offline fallback from verified reference fixture.
   - Absolute non-treatment of trade value as freight rates.
4. Port Directory Reconciliation Adapter:
   - Non-destructive reconciliation (no deleting canonical ports on missing source entries).
   - Honest status reporting (manual_download_only).
5. Vessel Registry Acquisition Adapter:
   - Synchronizing canonical vessels from AIS positions.
   - Strict boundaries: is_live=False, commercial_charter_availability="unavailable".
   - Honest status reporting (manual_download_only).
6. Multi-Source Coordinator:
   - Selective and all-source execution.
   - Partial success handling when one source fails.
7. Coverage API:
   - Extended source_acquisition matrix.
   - Separation of fixtures vs acquired records.
   - Freshness classification (current, recent, historical).
   - Refusal to report live status for adapters without verified live streams.
8. CLI commands:
   - environmental-refresh, trade-refresh, ports-refresh, vessels-refresh, sources-refresh.

Zero external network dependency: all remote HTTP calls are mocked.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.api.routes.coverage import get_coverage_summary
from pelagos.cli import (
    build_parser,
    handle_coverage_summary,
    handle_environmental_refresh,
    handle_ports_refresh,
    handle_sources_refresh,
    handle_trade_refresh,
    handle_vessels_refresh,
)
from pelagos.database import Base
from pelagos.ingestion.acquisition.client import (
    AcquisitionError,
    AcquisitionHTTPError,
    AcquisitionNetworkError,
    AcquisitionResponse,
    AcquisitionTimeoutError,
    ResilientHttpClient,
)
from pelagos.ingestion.acquisition.coordinator import run_sources_refresh
from pelagos.ingestion.acquisition.noaa_ndbc import (
    KNOWN_NDBC_STATIONS,
    METERS_PER_SEC_TO_KNOTS,
    parse_ndbc_realtime_text,
    refresh_environmental_observations,
)
from pelagos.ingestion.acquisition.ports_adapter import refresh_ports_directory
from pelagos.ingestion.acquisition.trade_adapter import refresh_trade_observations
from pelagos.ingestion.acquisition.vessels_adapter import refresh_vessels_registry
from pelagos.models import (
    EnvironmentalObservation,
    FreightObservation,
    FreightRoute,
    IngestionRun,
    Port,
    PortAlias,
    TradeObservation,
    Vessel,
    VesselPosition,
)


# =============================================================================
# Fixtures and Sample Payloads
# =============================================================================

@pytest.fixture
def test_db():
    """In-memory SQLite database isolated per test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


SAMPLE_NDBC_VALID_TEXT = """#YY  MM DD hh mm WDIR WSPD GST  WVHT  DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS  PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m  sec   sec degT   hPa  degC  degC  degC  nmi   hPa    ft
2026 10 30 16 30 200  8.5 11.2   2.1   10   6.5 210 1018.2  14.2  13.8  11.5   MM  +1.2    MM
2026 10 30 16 00 190  7.8 10.5   2.0    9   6.2 205 1018.5  14.0  13.7  11.4   MM  +1.0    MM
2026 10 30 15 30 195  8.0 10.8   2.2   10   6.4 208 1018.8  13.9  13.6  11.3   MM  +0.8    MM
"""

SAMPLE_NDBC_MALFORMED_TEXT = """#YY  MM DD hh mm WDIR WSPD GST  WVHT  DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS  PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m  sec   sec degT   hPa  degC  degC  degC  nmi   hPa    ft
2026 10 30
INCOMPLETE LINE
2026 10 30 16 30 200  8.5 11.2   2.1   10   6.5 210 1018.2  14.2  13.8  11.5   MM  +1.2    MM
"""

SAMPLE_NDBC_UNPHYSICAL_TEXT = """#YY  MM DD hh mm WDIR WSPD GST  WVHT  DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS  PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m  sec   sec degT   hPa  degC  degC  degC  nmi   hPa    ft
2026 10 30 16 30 200 999.0 11.2   2.1   10   6.5 210 1018.2  14.2  13.8  11.5   MM  +1.2    MM
2026 10 30 16 00 190  7.8 10.5  88.0    9   6.2 205 1018.5  14.0  13.7  11.4   MM  +1.0    MM
2026 10 30 15 30 195  8.0 10.8   2.2   10   6.4 208  400.0  13.9  13.6  11.3   MM  +0.8    MM
"""


# =============================================================================
# 1. Resilient HTTP Client Tests
# =============================================================================

def test_http_client_successful_retrieval():
    """Client successfully executes GET and attaches auditable retrieval time."""
    client = ResilientHttpClient(timeout_seconds=5.0, max_retries=2)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.url = "https://www.ndbc.noaa.gov/data/realtime2/46041.txt"
    mock_resp.text = SAMPLE_NDBC_VALID_TEXT
    mock_resp.content = SAMPLE_NDBC_VALID_TEXT.encode("utf-8")
    mock_resp.headers = {"Content-Type": "text/plain"}

    with patch("httpx.Client.get", return_value=mock_resp):
        res = client.get("https://www.ndbc.noaa.gov/data/realtime2/46041.txt")
        assert res.status_code == 200
        assert res.text == SAMPLE_NDBC_VALID_TEXT
        assert isinstance(res.retrieval_time_utc, datetime.datetime)
        assert res.retrieval_time_utc.tzinfo is not None
        assert res.duration_seconds >= 0.0


def test_http_client_retry_and_exponential_backoff():
    """Client retries on transient 503 and succeeds on subsequent attempt."""
    client = ResilientHttpClient(timeout_seconds=2.0, max_retries=3, backoff_factor=0.01)

    fail_resp = MagicMock()
    fail_resp.status_code = 503
    fail_resp.text = "Service Temporarily Unavailable"

    success_resp = MagicMock()
    success_resp.status_code = 200
    success_resp.url = "https://example.com/stream"
    success_resp.text = "OK"
    success_resp.content = b"OK"
    success_resp.headers = {}

    with patch("httpx.Client.get", side_effect=[fail_resp, fail_resp, success_resp]) as mock_get:
        with patch("time.sleep") as mock_sleep:
            res = client.get("https://example.com/stream")
            assert res.status_code == 200
            assert mock_get.call_count == 3
            assert mock_sleep.call_count == 2
            # Verify exponential backoff: 0.01 * 2^0, 0.01 * 2^1
            mock_sleep.assert_any_call(0.01)
            mock_sleep.assert_any_call(0.02)


def test_http_client_timeout_exhaustion():
    """Client raises AcquisitionTimeoutError when all retries time out."""
    client = ResilientHttpClient(timeout_seconds=1.0, max_retries=2, backoff_factor=0.01)

    with patch("httpx.Client.get", side_effect=httpx.TimeoutException("Read timed out")):
        with patch("time.sleep"):
            with pytest.raises(AcquisitionTimeoutError) as exc_info:
                client.get("https://example.com/slow")
            assert "timed out after" in str(exc_info.value)


def test_http_client_non_retryable_fatal_status():
    """Client immediately raises AcquisitionHTTPError on 404 without wasteful retries."""
    client = ResilientHttpClient(timeout_seconds=2.0, max_retries=3)

    resp_404 = MagicMock()
    resp_404.status_code = 404
    resp_404.text = "Station Not Found"

    with patch("httpx.Client.get", return_value=resp_404) as mock_get:
        with pytest.raises(AcquisitionHTTPError) as exc_info:
            client.get("https://www.ndbc.noaa.gov/data/realtime2/99999.txt")
        assert exc_info.value.status_code == 404
        assert mock_get.call_count == 1  # 404 must not be retried


def test_http_client_network_error_exhaustion():
    """Client raises AcquisitionNetworkError on persistent connection error."""
    client = ResilientHttpClient(timeout_seconds=1.0, max_retries=2, backoff_factor=0.01)

    with patch("httpx.Client.get", side_effect=httpx.ConnectError("Connection refused")):
        with patch("time.sleep"):
            with pytest.raises(AcquisitionNetworkError) as exc_info:
                client.get("https://example.com/down")
            assert "Failed to acquire" in str(exc_info.value) or "Connection refused" in str(exc_info.value)


# =============================================================================
# 2. NOAA NDBC Environmental Buoy Adapter Tests
# =============================================================================

def test_ndbc_parser_valid_records():
    """Parser converts whitespace-delimited standard met text into typed observation dicts."""
    retrieval_dt = datetime.datetime.now(datetime.timezone.utc)
    records, unparseable, quarantine_reasons = parse_ndbc_realtime_text(
        text=SAMPLE_NDBC_VALID_TEXT,
        station_id="46041",
        retrieval_time_utc=retrieval_dt,
        max_observations=10,
    )
    assert len(records) == 3
    assert unparseable == 0
    assert len(quarantine_reasons) == 0

    first = records[0]
    assert first["station_id"] == "46041"
    assert first["observation_time"] == datetime.datetime(2026, 10, 30, 16, 30, tzinfo=datetime.timezone.utc)
    assert first["retrieval_time_utc"] == retrieval_dt
    assert first["wave_height_meters"] == 2.1
    # Wind speed converted from 8.5 m/s to knots
    expected_kts = round(8.5 * METERS_PER_SEC_TO_KNOTS, 2)
    assert first["wind_speed_knots"] == expected_kts
    assert first["air_pressure_hpa"] == 1018.2
    assert first["sea_surface_temp_c"] == 13.8
    assert first["idempotency_key"] == "NOAA:NDBC:46041:2026-10-30T16:30:00+00:00"


def test_ndbc_parser_unit_conversion():
    """Wind speed in m/s is strictly converted to nautical knots."""
    text = """#YY  MM DD hh mm WDIR WSPD GST  WVHT  DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS  PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m  sec   sec degT   hPa  degC  degC  degC  nmi   hPa    ft
2026 10 30 12 00 180 10.0  MM     MM   MM    MM  MM     MM    MM    MM    MM   MM    MM    MM
"""
    records, _, _ = parse_ndbc_realtime_text(text, "46041", datetime.datetime.now(datetime.timezone.utc))
    assert len(records) == 1
    # 10.0 m/s * 1.943844 = 19.44 knots
    assert records[0]["wind_speed_knots"] == 19.44


def test_ndbc_parser_physical_boundary_rejection():
    """Parser rejects unphysical values (wind > 180 kts, wave > 35m, pressure < 850 hPa)."""
    retrieval_dt = datetime.datetime.now(datetime.timezone.utc)
    records, unparseable, quarantine = parse_ndbc_realtime_text(
        text=SAMPLE_NDBC_UNPHYSICAL_TEXT,
        station_id="46041",
        retrieval_time_utc=retrieval_dt,
    )
    # All 3 records contain unphysical readings and must be quarantined
    assert len(records) == 0
    assert unparseable == 3
    assert len(quarantine) == 3
    assert any("Unphysical wind" in q for q in quarantine)
    assert any("Unphysical wave" in q for q in quarantine)
    assert any("Unphysical pressure" in q for q in quarantine)


def test_ndbc_parser_malformed_lines_handling():
    """Parser skips incomplete lines gracefully without dropping valid subsequent lines."""
    records, unparseable, quarantine = parse_ndbc_realtime_text(
        text=SAMPLE_NDBC_MALFORMED_TEXT,
        station_id="46041",
        retrieval_time_utc=datetime.datetime.now(datetime.timezone.utc),
    )
    assert len(records) == 1  # The one valid row parsed
    assert unparseable == 2  # The two malformed rows skipped
    assert records[0]["observation_time"] == datetime.datetime(2026, 10, 30, 16, 30, tzinfo=datetime.timezone.utc)


def test_ndbc_parser_empty_response():
    """Parser handles completely empty text without error."""
    records, unparseable, quarantine = parse_ndbc_realtime_text(
        text="",
        station_id="46041",
        retrieval_time_utc=datetime.datetime.now(datetime.timezone.utc),
    )
    assert records == []
    assert unparseable == 0
    assert "Payload too short" in quarantine[0]


def test_ndbc_mocked_live_ingestion_and_idempotency(test_db):
    """End-to-end ingestion with mocked HTTP: records persisted, repeated refresh deduplicated."""
    mock_client = MagicMock(spec=ResilientHttpClient)
    ret_time = datetime.datetime(2026, 10, 30, 17, 0, 0, tzinfo=datetime.timezone.utc)
    mock_resp = AcquisitionResponse(
        url="https://www.ndbc.noaa.gov/data/realtime2/46041.txt",
        status_code=200,
        text=SAMPLE_NDBC_VALID_TEXT,
        content=SAMPLE_NDBC_VALID_TEXT.encode(),
        retrieval_time_utc=ret_time,
        duration_seconds=0.15,
        headers={},
    )
    mock_client.get.return_value = mock_resp

    # First refresh: accepts 3 records
    res1 = refresh_environmental_observations(
        db=test_db,
        station_ids=["46041"],
        client=mock_client,
        max_per_station=10,
    )
    assert res1["status"] == "completed"
    assert res1["records_read"] == 3
    assert res1["accepted_count"] == 3
    assert res1["duplicate_count"] == 0
    assert res1["mode"] == "remote"
    assert test_db.query(EnvironmentalObservation).count() == 3

    # Check distinct separation of retrieval time and observation time
    obs = test_db.query(EnvironmentalObservation).first()
    assert obs.observation_time.year == 2026
    assert obs.observation_time.month == 10
    assert obs.observation_time.day == 30
    assert obs.observation_time.hour == 16
    assert obs.observation_time.minute == 30
    assert obs.idempotency_key == "NOAA:NDBC:46041:2026-10-30T16:30:00+00:00"

    # Second refresh: exactly identical feed -> 100% deduplicated
    res2 = refresh_environmental_observations(
        db=test_db,
        station_ids=["46041"],
        client=mock_client,
        max_per_station=10,
    )
    assert res2["status"] == "completed"
    assert res2["accepted_count"] == 0
    assert res2["duplicate_count"] == 3
    assert test_db.query(EnvironmentalObservation).count() == 3  # Count unchanged


def test_ndbc_preservation_of_existing_data_on_failed_refresh(test_db):
    """Failed HTTP refresh must never overwrite or delete previously existing observations."""
    # 1. Seed with an existing observation
    initial_obs = EnvironmentalObservation(
        station_id="46041",
        station_name="Cape Disappointment",
        latitude=46.107,
        longitude=-124.502,
        sea_area="North Pacific",
        observation_time=datetime.datetime(2026, 10, 30, 12, 0, tzinfo=datetime.timezone.utc),
        wind_speed_knots=15.0,
        source_name="NOAA National Data Buoy Center (NDBC)",
        license_type="Public Domain",
        quality_status="verified",
        idempotency_key="NOAA:NDBC:46041:2026-10-30T12:00:00+00:00",
    )
    test_db.add(initial_obs)
    test_db.commit()
    assert test_db.query(EnvironmentalObservation).count() == 1

    # 2. Mock HTTP failure (500 Internal Server Error)
    mock_client = MagicMock(spec=ResilientHttpClient)
    mock_client.get.side_effect = AcquisitionHTTPError("500 Server Error", status_code=500, url="https://ndbc.noaa.gov")

    res = refresh_environmental_observations(
        db=test_db,
        station_ids=["46041"],
        client=mock_client,
    )
    assert res["status"] == "failed"
    assert "500 Server Error" in res["error"]

    # 3. Verify original database record remains completely intact
    persisted = test_db.query(EnvironmentalObservation).all()
    assert len(persisted) == 1
    assert persisted[0].station_id == "46041"
    assert persisted[0].wind_speed_knots == 15.0


def test_ndbc_offline_mode_fallback(test_db):
    """Offline mode bypasses network client and loads from verified fixture."""
    res = refresh_environmental_observations(
        db=test_db,
        offline=True,
    )
    assert res["status"] in ("completed", "partial")
    assert res["mode"] == "bundled"
    assert res["accepted_count"] > 0
    assert test_db.query(EnvironmentalObservation).count() > 0


# =============================================================================
# 3. Bilateral Trade Adapter Tests
# =============================================================================

def test_trade_adapter_reports_requires_credentials_and_falls_back(test_db):
    """Trade adapter honestly reports requires_credentials when API key is missing and uses fixture."""
    res = refresh_trade_observations(
        db=test_db,
        api_key=None,
        offline=False,
    )
    assert res["integration_status"] == "requires_credentials"
    assert res["status"] in ("completed", "partial")
    assert res["accepted_count"] > 0
    assert "active API key" in res["notes"]
    assert test_db.query(TradeObservation).count() > 0


def test_trade_adapter_never_creates_freight_rates(test_db):
    """Trade observation ingestion strictly enforces that trade value per tonne is NOT a freight rate."""
    refresh_trade_observations(db=test_db, offline=True)

    # Verify no FreightObservation records were created
    freight_count = test_db.query(FreightObservation).count()
    assert freight_count == 0

    # Verify all trade records are in TradeObservation table with trade disclaimers
    trades = test_db.query(TradeObservation).all()
    assert len(trades) > 0
    for t in trades:
        assert t.volume_metric_tons > 0
        assert t.trade_value_usd > 0
        assert t.unit_price_usd_per_mt > 0
        # Unit price is commodity price, not freight rate
        assert t.trade_flow in ("Export", "Import")


# =============================================================================
# 4. Port Directory Reconciliation Adapter Tests
# =============================================================================

def test_ports_adapter_non_destructive_reconciliation(test_db):
    """Port reconciliation adds/updates records without deleting existing canonical ports."""
    # 1. Pre-seed a custom port
    custom_port = Port(
        unlocode="INTEST",
        port_id="INTEST",
        source_id="TEST",
        name="Test Canonical Port",
        country="India",
        country_code="IN",
        latitude=15.0,
        longitude=73.0,
        port_region="West Coast India",
        provenance="Custom Pre-Seed",
    )
    test_db.add(custom_port)
    test_db.commit()

    # 2. Run reconciliation
    res = refresh_ports_directory(db=test_db, offline=True)
    assert res["integration_status"] == "manual_download_only"
    assert res["status"] in ("completed", "partial")

    # 3. Ensure custom pre-seeded port was NOT dropped
    found = test_db.query(Port).filter(Port.unlocode == "INTEST").first()
    assert found is not None
    assert found.name == "Test Canonical Port"

    # Ensure canonical ports from fixture were reconciled
    assert test_db.query(Port).count() >= 40


# =============================================================================
# 5. Vessel Registry Acquisition Adapter Tests
# =============================================================================

def test_vessels_adapter_boundaries_and_charter_limitations(test_db):
    """Vessels adapter synchronizes vessels while enforcing is_live=False and charter unavailable."""
    # Seed a historical position observation
    pos = VesselPosition(
        mmsi="367000001",
        vessel_name="PACIFIC BULKER",
        vessel_type="Bulk Carrier",
        latitude=20.0,
        longitude=-120.0,
        observation_time=datetime.datetime(2024, 10, 15, 12, 0),
        source_name="NOAA MarineCadastre",
        source_partition="ais-2024-10-15.parquet",
        license_type="Public Domain",
        idempotency_key="AIS:367000001:2024-10-15T12:00:00",
    )
    test_db.add(pos)
    test_db.commit()

    res = refresh_vessels_registry(db=test_db, offline=True)
    assert res["integration_status"] == "manual_download_only"
    assert res["commercial_charter_availability"] == "unavailable"
    assert res["is_live"] is False

    # Check database vessel record
    vessel = test_db.query(Vessel).filter(Vessel.mmsi == "367000001").first()
    assert vessel is not None
    assert vessel.vessel_name == "PACIFIC BULKER"


# =============================================================================
# 6. Multi-Source Coordinator Tests
# =============================================================================

def test_coordinator_combined_execution_all_sources(test_db):
    """Coordinator executes multi-source refresh and aggregates results."""
    mock_client = MagicMock(spec=ResilientHttpClient)
    ret_time = datetime.datetime.now(datetime.timezone.utc)
    mock_resp = AcquisitionResponse(
        url="https://ndbc.noaa.gov/stream",
        status_code=200,
        text=SAMPLE_NDBC_VALID_TEXT,
        content=SAMPLE_NDBC_VALID_TEXT.encode(),
        retrieval_time_utc=ret_time,
        duration_seconds=0.1,
        headers={},
    )
    mock_client.get.return_value = mock_resp

    res = run_sources_refresh(
        db=test_db,
        sources=["environmental", "trade", "ports", "vessels"],
        client=mock_client,
        offline=False,
    )
    assert res["overall_status"] == "completed"
    assert set(res["sources_executed"]) == {"environmental", "trade", "ports", "vessels"}
    assert "environmental" in res["results"]
    assert "trade" in res["results"]
    assert "ports" in res["results"]
    assert "vessels" in res["results"]


def test_coordinator_partial_success_handling(test_db):
    """When one source encounters an error, coordinator continues and reports partial_success."""
    mock_client = MagicMock(spec=ResilientHttpClient)
    # Environmental fails with 500
    mock_client.get.side_effect = AcquisitionHTTPError("500 Server Error", 500, "https://ndbc.noaa.gov")

    res = run_sources_refresh(
        db=test_db,
        sources=["environmental", "trade", "ports"],
        client=mock_client,
        offline=False,
    )
    assert res["overall_status"] == "partial_success"
    assert "environmental" in res["failed_sources"]
    assert res["results"]["trade"]["status"] in ("completed", "partial")
    assert res["results"]["ports"]["status"] in ("completed", "partial")


def test_coordinator_single_source_selection(test_db):
    """Coordinator respects user request to refresh only a specific source."""
    res = run_sources_refresh(
        db=test_db,
        sources=["ports"],
        offline=True,
    )
    assert res["overall_status"] == "completed"
    assert res["sources_executed"] == ["ports"]
    assert "trade" not in res["results"]


# =============================================================================
# 7. Coverage API & Freshness Matrix Tests
# =============================================================================

def test_coverage_api_source_acquisition_matrix(test_db):
    """Coverage summary exposes auditable source acquisition breakdown with honest boundaries."""
    # Populate via coordinator
    run_sources_refresh(db=test_db, offline=True)

    summary = get_coverage_summary(db=test_db)
    assert summary["status"] == "ok"
    matrix = summary["source_acquisition"]
    assert "environmental_buoys" in matrix
    assert "bilateral_trade" in matrix
    assert "ports_directory" in matrix
    assert "vessels_and_ais" in matrix

    # Verify honest boundaries
    env = matrix["environmental_buoys"]
    assert env["integration_status"] == "automated_working"
    assert "ndbc.noaa.gov" in env["endpoint"]

    trade = matrix["bilateral_trade"]
    assert trade["integration_status"] == "requires_credentials"
    assert "API key" in trade["notes"]

    ports = matrix["ports_directory"]
    assert ports["integration_status"] == "manual_download_only"

    vessels = matrix["vessels_and_ais"]
    assert vessels["integration_status"] == "manual_download_only"
    assert vessels["live_ais_connected"] is False
    assert vessels["commercial_charter_availability"] == "unavailable"


def test_coverage_api_freshness_classification(test_db):
    """Observation timestamps are dynamically classified as current (<48h), recent (<7d), or historical."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # 1. Historical observation (30 days ago)
    old_obs = EnvironmentalObservation(
        station_id="46041",
        station_name="Cape Disappointment",
        latitude=46.107,
        longitude=-124.502,
        sea_area="North Pacific",
        observation_time=now_utc - datetime.timedelta(days=30),
        source_name="NOAA NDBC",
        license_type="Public Domain",
        quality_status="verified",
        idempotency_key="NOAA:NDBC:46041:old",
    )
    test_db.add(old_obs)
    test_db.commit()

    summary_old = get_coverage_summary(db=test_db)
    assert summary_old["source_acquisition"]["environmental_buoys"]["freshness"] == "historical"

    # 2. Current observation (2 hours ago)
    recent_obs = EnvironmentalObservation(
        station_id="46041",
        station_name="Cape Disappointment",
        latitude=46.107,
        longitude=-124.502,
        sea_area="North Pacific",
        observation_time=now_utc - datetime.timedelta(hours=2),
        source_name="NOAA NDBC",
        license_type="Public Domain",
        quality_status="verified",
        idempotency_key="NOAA:NDBC:46041:new",
    )
    test_db.add(recent_obs)
    test_db.commit()

    summary_new = get_coverage_summary(db=test_db)
    assert summary_new["source_acquisition"]["environmental_buoys"]["freshness"] == "current"


# =============================================================================
# 8. CLI Commands Tests
# =============================================================================

def test_cli_environmental_refresh(tmp_path, capsys):
    """CLI environmental-refresh command executes offline successfully."""
    db_file = tmp_path / "cli_env.db"
    db_url = f"sqlite:///{db_file}"
    parser = build_parser()

    args = parser.parse_args(["environmental-refresh", "--offline", "--db-url", db_url])
    code = handle_environmental_refresh(args)
    assert code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["status"] in ("completed", "partial")
    assert out["accepted_count"] > 0


def test_cli_trade_refresh(tmp_path, capsys):
    """CLI trade-refresh command executes offline successfully."""
    db_file = tmp_path / "cli_trade.db"
    db_url = f"sqlite:///{db_file}"
    parser = build_parser()

    args = parser.parse_args(["trade-refresh", "--offline", "--db-url", db_url])
    code = handle_trade_refresh(args)
    assert code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["status"] in ("completed", "partial")
    assert out["accepted_count"] > 0


def test_cli_ports_refresh(tmp_path, capsys):
    """CLI ports-refresh command reconciles directory offline."""
    db_file = tmp_path / "cli_ports.db"
    db_url = f"sqlite:///{db_file}"
    parser = build_parser()

    args = parser.parse_args(["ports-refresh", "--offline", "--db-url", db_url])
    code = handle_ports_refresh(args)
    assert code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["status"] in ("completed", "partial")


def test_cli_vessels_refresh(tmp_path, capsys):
    """CLI vessels-refresh command synchronizes vessels offline."""
    db_file = tmp_path / "cli_vessels.db"
    db_url = f"sqlite:///{db_file}"
    parser = build_parser()

    args = parser.parse_args(["vessels-refresh", "--offline", "--db-url", db_url])
    code = handle_vessels_refresh(args)
    assert code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["status"] in ("completed", "partial")


def test_cli_sources_refresh_combined(tmp_path, capsys):
    """CLI sources-refresh runs multi-source coordinator offline."""
    db_file = tmp_path / "cli_all.db"
    db_url = f"sqlite:///{db_file}"
    parser = build_parser()

    args = parser.parse_args(["sources-refresh", "--offline", "--db-url", db_url])
    code = handle_sources_refresh(args)
    assert code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["overall_status"] == "completed"
    assert "environmental" in out["results"]
    assert "trade" in out["results"]
    assert "ports" in out["results"]
    assert "vessels" in out["results"]


def test_cli_coverage_summary_includes_acquisition_matrix(tmp_path, capsys):
    """CLI coverage-summary command outputs source acquisition matrix."""
    db_file = tmp_path / "cli_cov.db"
    db_url = f"sqlite:///{db_file}"
    parser = build_parser()

    # Pre-populate
    args_sync = parser.parse_args(["sources-refresh", "--offline", "--db-url", db_url])
    handle_sources_refresh(args_sync)
    capsys.readouterr()  # clear buffer

    args_cov = parser.parse_args(["coverage-summary", "--db-url", db_url])
    code = handle_coverage_summary(args_cov)
    assert code == 0

    out = json.loads(capsys.readouterr().out)
    assert "source_acquisition" in out
    assert out["source_acquisition"]["environmental_buoys"]["integration_status"] == "automated_working"
