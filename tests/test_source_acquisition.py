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
from pelagos.database import Base, ensure_schema_compatibility
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
    QuarantinedRecord,
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
    assert obs.retrieval_time_utc is not None
    assert obs.observation_time != obs.retrieval_time_utc
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
        station_name="Cape Elizabeth",
        latitude=47.351,
        longitude=-124.741,
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
        station_name="Cape Elizabeth",
        latitude=47.351,
        longitude=-124.741,
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
        station_name="Cape Elizabeth",
        latitude=47.351,
        longitude=-124.741,
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


# =============================================================================
# 9. Stage B.1 Acquisition Hardening Regression Tests (F-01 to F-06)
# =============================================================================

# --- F-01: NOAA NDBC Partial-Failure Reporting ---

def test_f01_noaa_all_stations_succeed(test_db):
    """F-01: Ingestion run is marked 'completed' when all requested stations succeed."""
    mock_client = MagicMock(spec=ResilientHttpClient)
    ret_time = datetime.datetime.now(datetime.timezone.utc)
    mock_resp = AcquisitionResponse(
        url="https://ndbc.noaa.gov/46041.txt",
        status_code=200,
        text=SAMPLE_NDBC_VALID_TEXT,
        content=SAMPLE_NDBC_VALID_TEXT.encode(),
        retrieval_time_utc=ret_time,
        duration_seconds=0.1,
        headers={},
    )
    mock_client.get.return_value = mock_resp

    res = refresh_environmental_observations(
        db=test_db,
        station_ids=["46041", "42001"],
        client=mock_client,
    )
    assert res["status"] == "completed"
    assert res["accepted_count"] == 6  # 3 per station
    assert res["unparseable_count"] == 0

    run = test_db.query(IngestionRun).order_by(IngestionRun.id.desc()).first()
    assert run.status == "completed"
    assert run.error_details is None


def test_f01_noaa_one_station_succeeds_one_fails_is_partial(test_db):
    """F-01: Ingestion run is marked 'partial' when one station succeeds and another fails."""
    mock_client = MagicMock(spec=ResilientHttpClient)
    ret_time = datetime.datetime.now(datetime.timezone.utc)
    valid_resp = AcquisitionResponse(
        url="https://ndbc.noaa.gov/46041.txt",
        status_code=200,
        text=SAMPLE_NDBC_VALID_TEXT,
        content=SAMPLE_NDBC_VALID_TEXT.encode(),
        retrieval_time_utc=ret_time,
        duration_seconds=0.1,
        headers={},
    )

    def mock_get(url, **kwargs):
        if "46041" in url:
            return valid_resp
        raise AcquisitionHTTPError("HTTP 500 Internal Server Error", status_code=500, url=url)

    mock_client.get.side_effect = mock_get

    res = refresh_environmental_observations(
        db=test_db,
        station_ids=["46041", "42001"],
        client=mock_client,
    )
    # Crucial F-01 requirement: Must be partial, NOT completed
    assert res["status"] == "partial"
    assert res["accepted_count"] == 3
    assert res["station_results"]["46041"]["status"] == "success"
    assert res["station_results"]["42001"]["status"] == "error"
    assert "error_details" in res
    assert "Station 42001" in res["error_details"]

    run = test_db.query(IngestionRun).order_by(IngestionRun.id.desc()).first()
    assert run.status == "partial"
    assert "Station 42001" in run.error_details


def test_f01_noaa_all_stations_fail_is_failed(test_db):
    """F-01: Ingestion run is marked 'failed' when all requested stations encounter errors."""
    mock_client = MagicMock(spec=ResilientHttpClient)
    mock_client.get.side_effect = AcquisitionHTTPError("HTTP 503 Service Unavailable", status_code=503, url="https://ndbc.noaa.gov")

    res = refresh_environmental_observations(
        db=test_db,
        station_ids=["46041", "42001"],
        client=mock_client,
    )
    assert res["status"] == "failed"
    assert res["accepted_count"] == 0
    assert "error" in res

    run = test_db.query(IngestionRun).order_by(IngestionRun.id.desc()).first()
    assert run.status == "failed"
    assert "Station 46041" in run.error_details
    assert "Station 42001" in run.error_details


def test_f01_noaa_unparseable_records_present_is_partial(test_db):
    """F-01: Ingestion run is marked 'partial' when unparseable records are detected."""
    mock_client = MagicMock(spec=ResilientHttpClient)
    ret_time = datetime.datetime.now(datetime.timezone.utc)
    mock_resp = AcquisitionResponse(
        url="https://ndbc.noaa.gov/46041.txt",
        status_code=200,
        text=SAMPLE_NDBC_MALFORMED_TEXT,
        content=SAMPLE_NDBC_MALFORMED_TEXT.encode(),
        retrieval_time_utc=ret_time,
        duration_seconds=0.1,
        headers={},
    )
    mock_client.get.return_value = mock_resp

    res = refresh_environmental_observations(
        db=test_db,
        station_ids=["46041"],
        client=mock_client,
    )
    assert res["status"] == "partial"
    assert res["accepted_count"] == 1
    assert res["unparseable_count"] == 2

    run = test_db.query(IngestionRun).order_by(IngestionRun.id.desc()).first()
    assert run.status == "partial"
    assert run.unparseable_count == 2


# --- F-02: Coordinator-Owned Independent Transactions ---

def test_f02_successful_source_transaction_commits_all_intended_records(tmp_path):
    """F-02: Successful source transaction commits all intended records to database."""
    from pelagos.database import init_db

    db_path = tmp_path / "f02_success.db"
    engine = create_engine(f"sqlite:///{db_path}")
    init_db(engine)

    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()

    try:
        res = run_sources_refresh(
            db=session,
            sources=["environmental"],
            offline=True,
        )
        assert res["overall_status"] == "completed"
        assert res["results"]["environmental"]["status"] == "completed"
    finally:
        session.close()

    # Verify records were actually committed in database using an independent session
    verify_session = Session()
    try:
        obs_count = verify_session.query(EnvironmentalObservation).count()
        assert obs_count > 0, "Committed environmental observations must exist in database"
        run_record = verify_session.query(IngestionRun).filter(
            IngestionRun.source_name.like("%NOAA%")
        ).first()
        assert run_record is not None
        assert run_record.status == "completed"
    finally:
        verify_session.close()


def test_f02_failure_midway_rolls_back_partial_records(tmp_path):
    """F-02: Failure midway through an adapter rolls back its partial records cleanly."""
    from pelagos.database import init_db

    db_path = tmp_path / "f02_midway_fail.db"
    engine = create_engine(f"sqlite:///{db_path}")
    init_db(engine)

    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()

    mock_client = MagicMock(spec=ResilientHttpClient)
    ret_time = datetime.datetime.now(datetime.timezone.utc)
    mock_resp = AcquisitionResponse(
        url="https://ndbc.noaa.gov/46041.txt",
        status_code=200,
        text=SAMPLE_NDBC_VALID_TEXT,
        content=SAMPLE_NDBC_VALID_TEXT.encode(),
        retrieval_time_utc=ret_time,
        duration_seconds=0.1,
        headers={},
    )
    mock_client.get.return_value = mock_resp

    with patch(
        "pelagos.ingestion.acquisition.noaa_ndbc.parse_ndbc_realtime_text",
        side_effect=RuntimeError("Simulated unhandled processing crash midway through source"),
    ):
        try:
            res = run_sources_refresh(
                db=session,
                sources=["environmental"],
                offline=False,
                client=mock_client,
            )
            assert res["overall_status"] == "failed"
            assert res["results"]["environmental"]["status"] == "failed"
        finally:
            session.close()

    # In independent session, verify ZERO partial observations were committed
    verify_session = Session()
    try:
        count = verify_session.query(EnvironmentalObservation).count()
        assert count == 0, "Partial records must be rolled back completely upon failure"
    finally:
        verify_session.close()


def test_f02_earlier_successful_source_remains_committed_after_later_source_fails(tmp_path):
    """F-02: Earlier successful source remains committed after a later source fails."""
    from pelagos.database import init_db

    db_path = tmp_path / "f02_independent_isolation.db"
    engine = create_engine(f"sqlite:///{db_path}")
    init_db(engine)

    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()

    try:
        with patch(
            "pelagos.ingestion.acquisition.coordinator.refresh_trade_observations",
            side_effect=RuntimeError("Fatal trade database constraint failure"),
        ):
            res = run_sources_refresh(
                db=session,
                sources=["environmental", "trade"],
                offline=True,
            )

        assert res["overall_status"] == "partial_success"
        assert res["results"]["environmental"]["status"] == "completed"
        assert res["results"]["trade"]["status"] == "failed"
    finally:
        session.close()

    # Independent session verifies environmental remained committed and trade has 0 records
    verify_session = Session()
    try:
        env_count = verify_session.query(EnvironmentalObservation).count()
        assert env_count > 0, "Earlier source must remain committed"
        trade_count = verify_session.query(TradeObservation).count()
        assert trade_count == 0, "Failed source must have zero records"
    finally:
        verify_session.close()


def test_f02_adapter_cannot_commit_unrelated_pending_session_changes(test_db):
    """F-02: Adapters do not commit, ensuring unrelated pending session changes are never committed."""
    unrelated_vessel = VesselPosition(
        mmsi="999888777",
        vessel_name="PENDING UNCOMMITTED SHIP",
        vessel_type="Commercial",
        latitude=25.0,
        longitude=-80.0,
        observation_time=datetime.datetime.now(datetime.timezone.utc),
        source_name="Manual Unrelated",
        license_type="Public Domain",
        idempotency_key="UNRELATED:PENDING:1",
    )
    test_db.add(unrelated_vessel)

    # Calling adapter directly does NOT commit the pending session changes
    refresh_trade_observations(db=test_db, offline=True)

    # Calling rollback rolls back the unrelated vessel, proving the adapter never committed it
    test_db.rollback()
    vessel_in_db = test_db.query(VesselPosition).filter(VesselPosition.mmsi == "999888777").first()
    assert vessel_in_db is None, "Adapter must not commit caller's pending session changes"


def test_f02_failed_ingestion_run_audit_survives_rollback(tmp_path):
    """F-02: Failed IngestionRun audit record survives source rollback."""
    from pelagos.database import init_db

    db_path = tmp_path / "f02_audit_survival.db"
    engine = create_engine(f"sqlite:///{db_path}")
    init_db(engine)

    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()

    try:
        with patch(
            "pelagos.ingestion.acquisition.coordinator.refresh_environmental_observations",
            side_effect=RuntimeError("Catastrophic environmental ingestion failure"),
        ):
            res = run_sources_refresh(
                db=session,
                sources=["environmental"],
                offline=True,
            )

        assert res["overall_status"] == "failed"
        assert res["results"]["environmental"]["status"] == "failed"
    finally:
        session.close()

    verify_session = Session()
    try:
        failed_run = verify_session.query(IngestionRun).filter(
            IngestionRun.status == "failed",
            IngestionRun.source_partition == "realtime_buoy_stream",
        ).first()
        assert failed_run is not None, "Failed audit record must survive source rollback"
        assert "Catastrophic environmental ingestion failure" in (failed_run.error_details or "")
        assert verify_session.query(EnvironmentalObservation).count() == 0
    finally:
        verify_session.close()


def test_f02_audit_persistence_failure_does_not_corrupt_or_commit_unrelated_data(test_db):
    """F-02: Audit persistence failure does not corrupt session or commit unrelated source data."""
    unrelated_obs = TradeObservation(
        reporter_country="United States",
        reporter_iso2="US",
        partner_country="Japan",
        partner_iso2="JP",
        commodity_code="100190",
        commodity_name="Wheat",
        trade_flow="Export",
        period_date=datetime.date(2026, 1, 1),
        source_name="Unrelated Source",
        idempotency_key="UNRELATED:TRADE:TEST",
    )
    test_db.add(unrelated_obs)

    with patch(
        "pelagos.ingestion.acquisition.coordinator.refresh_environmental_observations",
        side_effect=RuntimeError("Primary adapter error"),
    ):
        with patch(
            "pelagos.ingestion.acquisition.coordinator._persist_failed_source_audit",
            side_effect=RuntimeError("Secondary audit disk full crash"),
        ):
            res = run_sources_refresh(
                db=test_db,
                sources=["environmental"],
                offline=True,
            )

    assert res["overall_status"] == "failed"
    err = res["results"]["environmental"]["error"]
    assert "Primary adapter error" in err
    assert "Secondary audit disk full crash" in err or "audit persistence failure" in err

    # Unrelated trade observation was not committed
    test_db.rollback()
    assert test_db.query(TradeObservation).filter(TradeObservation.idempotency_key == "UNRELATED:TRADE:TEST").first() is None


def test_f02_session_remains_usable_after_rollback(test_db):
    """F-02: SQLAlchemy session remains fully usable to query and persist after a source rollback."""
    with patch(
        "pelagos.ingestion.acquisition.coordinator.refresh_environmental_observations",
        side_effect=RuntimeError("Simulated transient failure"),
    ):
        res = run_sources_refresh(
            db=test_db,
            sources=["environmental"],
            offline=True,
        )
    assert res["overall_status"] == "failed"

    # Session must be immediately usable for subsequent queries and operations
    new_port = Port(
        unlocode="USXYZ",
        port_id="USXYZ",
        source_id="USXYZ (Test)",
        name="Test Port After Rollback",
        country="United States",
        country_code="US",
        latitude=30.0,
        longitude=-90.0,
        source_url="https://example.com",
    )
    test_db.add(new_port)
    test_db.commit()

    saved = test_db.query(Port).filter(Port.unlocode == "USXYZ").first()
    assert saved is not None
    assert saved.name == "Test Port After Rollback"


def test_f02_overall_status_success_partial_and_total_failure(test_db):
    """F-02: Coordinator accurately aggregates overall status for success, partial success, and failure."""
    # 1. Total success
    res_success = run_sources_refresh(
        db=test_db,
        sources=["ports", "vessels"],
        offline=True,
    )
    assert res_success["overall_status"] == "completed"
    assert len(res_success["failed_sources"]) == 0

    # 2. Partial success
    with patch(
        "pelagos.ingestion.acquisition.coordinator.refresh_ports_directory",
        side_effect=RuntimeError("Port authority offline"),
    ):
        res_partial = run_sources_refresh(
            db=test_db,
            sources=["ports", "vessels"],
            offline=True,
        )
    assert res_partial["overall_status"] == "partial_success"
    assert res_partial["failed_sources"] == ["ports"]

    # 3. Total failure
    with patch(
        "pelagos.ingestion.acquisition.coordinator.refresh_ports_directory",
        side_effect=RuntimeError("Port authority offline"),
    ):
        with patch(
            "pelagos.ingestion.acquisition.coordinator.refresh_vessels_registry",
            side_effect=RuntimeError("Vessel registry offline"),
        ):
            res_failed = run_sources_refresh(
                db=test_db,
                sources=["ports", "vessels"],
                offline=True,
            )
    assert res_failed["overall_status"] == "failed"
    assert set(res_failed["failed_sources"]) == {"ports", "vessels"}


# --- F-03: Stable Trade Coverage Identification ---

def test_f03_trade_coverage_queries_stable_partition_and_empty_db(test_db):
    """F-03: Coverage summary accurately queries trade sync via partition or source name."""
    # 1. Empty database behavior
    summary_empty = get_coverage_summary(db=test_db)
    trade_empty = summary_empty["source_acquisition"]["bilateral_trade"]
    assert trade_empty["last_successful_sync"] is None
    assert trade_empty["failed_refresh_attempts"] == 0

    # 2. Add an IngestionRun with the actual source partition and USDA source name
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    success_run = IngestionRun(
        run_type="remote",
        source_name="USDA FAS GATS / Indian DGCI&S",
        source_partition="trade_observations",
        started_at=now_utc,
        completed_at=now_utc,
        status="completed",
        accepted_count=13,
    )
    failed_run = IngestionRun(
        run_type="remote",
        source_name="USDA FAS GATS / Indian DGCI&S",
        source_partition="trade_observations",
        started_at=now_utc,
        completed_at=now_utc,
        status="failed",
        error_details="HTTP 403 Forbidden",
    )
    test_db.add(success_run)
    test_db.add(failed_run)
    test_db.commit()

    summary_populated = get_coverage_summary(db=test_db)
    trade_pop = summary_populated["source_acquisition"]["bilateral_trade"]
    assert trade_pop["last_successful_sync"] is not None
    assert trade_pop["failed_refresh_attempts"] == 1


# --- F-04: Persisted Retrieval Timestamps ---

def test_f04_retrieval_timestamps_persisted_and_distinct(test_db):
    """F-04: retrieval_time_utc is persisted and distinct from observation_time."""
    mock_client = MagicMock(spec=ResilientHttpClient)
    ret_time = datetime.datetime(2026, 10, 31, 14, 22, 10, tzinfo=datetime.timezone.utc)
    mock_resp = AcquisitionResponse(
        url="https://ndbc.noaa.gov/46041.txt",
        status_code=200,
        text=SAMPLE_NDBC_VALID_TEXT,
        content=SAMPLE_NDBC_VALID_TEXT.encode(),
        retrieval_time_utc=ret_time,
        duration_seconds=0.1,
        headers={},
    )
    mock_client.get.return_value = mock_resp

    res = refresh_environmental_observations(
        db=test_db,
        station_ids=["46041"],
        client=mock_client,
    )
    assert res["status"] == "completed"

    obs = test_db.query(EnvironmentalObservation).filter(EnvironmentalObservation.station_id == "46041").first()
    assert obs is not None
    assert obs.retrieval_time_utc is not None
    assert obs.retrieval_time_utc.year == 2026
    assert obs.retrieval_time_utc.month == 10
    assert obs.retrieval_time_utc.day == 31
    assert obs.retrieval_time_utc.hour == 14
    # Observation time is from payload (2026-10-30 16:30)
    assert obs.observation_time != obs.retrieval_time_utc

    # Offline fixture must have NULL retrieval timestamp (do not invent history)
    res_off = refresh_environmental_observations(db=test_db, offline=True)
    assert res_off["status"] == "completed"
    fixture_obs = (
        test_db.query(EnvironmentalObservation)
        .filter(EnvironmentalObservation.observation_time < datetime.datetime(2025, 1, 1))
        .first()
    )
    assert fixture_obs is not None
    assert fixture_obs.retrieval_time_utc is None


def test_f04_schema_compatibility_additive_column(tmp_path):
    """F-04: ensure_schema_compatibility safely adds retrieval_time_utc to existing database."""
    from sqlalchemy import text
    db_file = tmp_path / "legacy_env.db"
    legacy_engine = create_engine(f"sqlite:///{db_file}")

    # Create legacy table without retrieval_time_utc
    with legacy_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE environmental_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station_id VARCHAR(32) NOT NULL,
                station_name VARCHAR(128) NOT NULL,
                latitude FLOAT NOT NULL,
                longitude FLOAT NOT NULL,
                sea_area VARCHAR(64) NOT NULL,
                observation_time DATETIME NOT NULL,
                wave_height_meters FLOAT,
                wave_period_seconds FLOAT,
                wind_speed_knots FLOAT,
                wind_direction_degrees FLOAT,
                sea_surface_temp_c FLOAT,
                air_pressure_hpa FLOAT,
                source_name VARCHAR(128) NOT NULL,
                license_type VARCHAR(64) NOT NULL,
                quality_status VARCHAR(32) NOT NULL,
                idempotency_key VARCHAR(256) UNIQUE NOT NULL,
                created_at DATETIME NOT NULL
            )
        """))
        conn.execute(text("""
            INSERT INTO environmental_observations (
                station_id, station_name, latitude, longitude, sea_area, observation_time,
                source_name, license_type, quality_status, idempotency_key, created_at
            ) VALUES (
                '46041', 'Cape Disappointment', 46.107, -124.502, 'North Pacific',
                '2026-10-30 12:00:00', 'NOAA NDBC', 'Public Domain', 'verified',
                'TEST:KEY:1', '2026-10-30 12:00:00'
            )
        """))
        conn.commit()

    # Run additive migration
    ensure_schema_compatibility(legacy_engine)

    # Verify column added and existing data intact
    with legacy_engine.connect() as conn:
        row = conn.execute(text("SELECT id, station_id, retrieval_time_utc FROM environmental_observations")).first()
        assert row[0] == 1
        assert row[1] == "46041"
        assert row[2] is None  # Existing observation remains intact with null retrieval timestamp


def test_f04_sqlite_migration_10_step_lifecycle(tmp_path):
    """F-04: Rigorous 10-step verification of additive, idempotent retrieval_time_utc migration."""
    from sqlalchemy import text
    from pelagos.database import ensure_schema_compatibility, init_db

    db_file = tmp_path / "legacy_migration_10step.db"
    legacy_engine = create_engine(f"sqlite:///{db_file}")

    # 1. Create a legacy schema without retrieval_time_utc
    with legacy_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE environmental_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station_id VARCHAR(32) NOT NULL,
                station_name VARCHAR(128) NOT NULL,
                latitude FLOAT NOT NULL,
                longitude FLOAT NOT NULL,
                sea_area VARCHAR(64) NOT NULL,
                observation_time DATETIME NOT NULL,
                wave_height_meters FLOAT,
                wave_period_seconds FLOAT,
                wind_speed_knots FLOAT,
                wind_direction_degrees FLOAT,
                sea_surface_temp_c FLOAT,
                air_pressure_hpa FLOAT,
                source_name VARCHAR(128) NOT NULL,
                license_type VARCHAR(64) NOT NULL,
                quality_status VARCHAR(32) NOT NULL,
                idempotency_key VARCHAR(256) UNIQUE NOT NULL,
                created_at DATETIME NOT NULL
            )
        """))

        # 2. Insert representative historical observations
        conn.execute(text("""
            INSERT INTO environmental_observations (
                station_id, station_name, latitude, longitude, sea_area, observation_time,
                wave_height_meters, source_name, license_type, quality_status, idempotency_key, created_at
            ) VALUES
            ('46041', 'Cape Disappointment', 46.107, -124.502, 'North Pacific', '2026-10-01 10:00:00', 2.5, 'NOAA NDBC', 'Public Domain', 'verified', 'HIST:1', '2026-10-01 10:00:00'),
            ('42001', 'Mid Gulf', 25.897, -89.663, 'Gulf of Mexico', '2026-10-02 12:00:00', 1.8, 'NOAA NDBC', 'Public Domain', 'verified', 'HIST:2', '2026-10-02 12:00:00')
        """))
        conn.commit()

    # 3. Run the migration
    ensure_schema_compatibility(legacy_engine)

    # 4. Verify the new column using PRAGMA table_info(environmental_observations)
    with legacy_engine.connect() as conn:
        table_info = conn.execute(text("PRAGMA table_info(environmental_observations)")).fetchall()
        col_names = [row[1] for row in table_info]
        assert "retrieval_time_utc" in col_names, "retrieval_time_utc column must be present in table info"

        # 5. Verify existing records and row counts remain unchanged
        rows = conn.execute(text("SELECT id, station_id, wave_height_meters, retrieval_time_utc FROM environmental_observations ORDER BY id")).fetchall()
        assert len(rows) == 2, "Row count must remain exactly 2"
        assert rows[0][1] == "46041"
        assert rows[0][2] == 2.5
        assert rows[1][1] == "42001"
        assert rows[1][2] == 1.8

        # 6. Verify historical retrieval timestamps are NULL
        assert rows[0][3] is None, "Historical record 1 retrieval timestamp must be NULL"
        assert rows[1][3] is None, "Historical record 2 retrieval timestamp must be NULL"

    # 7. Run the migration a second time and verify idempotency
    ensure_schema_compatibility(legacy_engine)
    with legacy_engine.connect() as conn:
        table_info_2 = conn.execute(text("PRAGMA table_info(environmental_observations)")).fetchall()
        col_names_2 = [row[1] for row in table_info_2]
        assert col_names_2.count("retrieval_time_utc") == 1
        assert conn.execute(text("SELECT COUNT(*) FROM environmental_observations")).scalar() == 2

    # 8. Insert a new observation with both timestamps
    now_obs = datetime.datetime(2026, 11, 1, 14, 0, 0, tzinfo=datetime.timezone.utc)
    now_ret = datetime.datetime(2026, 11, 1, 14, 2, 30, tzinfo=datetime.timezone.utc)
    Session = sessionmaker(bind=legacy_engine)
    with Session() as s:
        new_obs = EnvironmentalObservation(
            station_id="41009",
            station_name="Canaveral",
            latitude=28.508,
            longitude=-80.185,
            sea_area="Western North Atlantic",
            observation_time=now_obs,
            retrieval_time_utc=now_ret,
            wave_height_meters=1.5,
            source_name="NOAA NDBC",
            license_type="Public Domain",
            quality_status="verified",
            idempotency_key="NEW:OBS:WITH:BOTH",
        )
        s.add(new_obs)
        s.commit()

    # 9. Verify both timestamps are persisted correctly
    with Session() as s:
        fetched = s.query(EnvironmentalObservation).filter(
            EnvironmentalObservation.idempotency_key == "NEW:OBS:WITH:BOTH"
        ).first()
        assert fetched is not None
        assert fetched.observation_time is not None
        assert fetched.retrieval_time_utc is not None
        assert fetched.observation_time != fetched.retrieval_time_utc
        assert fetched.retrieval_time_utc.minute == 2

    # 10. Verify a fresh database initializes successfully
    fresh_db = tmp_path / "fresh_10step_db.db"
    fresh_engine = create_engine(f"sqlite:///{fresh_db}")
    init_db(fresh_engine)
    with fresh_engine.connect() as conn:
        fresh_info = conn.execute(text("PRAGMA table_info(environmental_observations)")).fetchall()
        fresh_cols = [r[1] for r in fresh_info]
        assert "retrieval_time_utc" in fresh_cols


# --- F-05: HTTP 400 Fast-Fail ---

def test_f05_http_400_fast_fail_no_retries():
    """F-05: HTTP 400 Bad Request fast-fails immediately with exactly one request attempt."""
    client = ResilientHttpClient(max_retries=3, backoff_factor=0.01)

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 400
    mock_resp.text = "Bad Request: malformed station parameters"
    mock_resp.url = "https://example.com/bad"

    with patch("httpx.Client.get", return_value=mock_resp) as mock_get:
        with pytest.raises(AcquisitionHTTPError) as exc_info:
            client.get("https://example.com/bad")

        assert exc_info.value.status_code == 400
        # Exactly one request attempt must be made; no retry loop
        assert mock_get.call_count == 1


# --- F-06: Safe Station Metadata Handling ---

def test_f06_unknown_ndbc_station_no_fabricated_zero_coordinates(test_db):
    """F-06: Unknown NDBC stations never receive (0.0, 0.0) coordinates and are quarantined safely."""
    ret_time = datetime.datetime.now(datetime.timezone.utc)
    # Parse unverified station 99999
    records, unparse, reasons = parse_ndbc_realtime_text(
        text=SAMPLE_NDBC_VALID_TEXT,
        station_id="99999",
        retrieval_time_utc=ret_time,
    )
    assert len(records) == 3
    # Coordinates must be None, NOT (0.0, 0.0)
    for rec in records:
        assert rec["latitude"] is None
        assert rec["longitude"] is None
        assert rec["latitude"] != 0.0
        assert rec["longitude"] != 0.0

    # Ingestion into database must quarantine unpositioned records rather than fabricating coordinates
    mock_client = MagicMock(spec=ResilientHttpClient)
    mock_resp = AcquisitionResponse(
        url="https://ndbc.noaa.gov/99999.txt",
        status_code=200,
        text=SAMPLE_NDBC_VALID_TEXT,
        content=SAMPLE_NDBC_VALID_TEXT.encode(),
        retrieval_time_utc=ret_time,
        duration_seconds=0.1,
        headers={},
    )
    mock_client.get.return_value = mock_resp

    res = refresh_environmental_observations(
        db=test_db,
        station_ids=["99999"],
        client=mock_client,
    )
    assert res["status"] == "partial"
    assert res["accepted_count"] == 0
    assert res["quarantined_count"] == 3
    assert res["station_results"]["99999"]["status"] == "quarantined"
    assert "warning" in res["station_results"]["99999"]

    # No environmental observations created with (0.0, 0.0)
    zero_obs = test_db.query(EnvironmentalObservation).filter(
        EnvironmentalObservation.station_id == "99999"
    ).all()
    assert len(zero_obs) == 0

    # Records quarantined with proper reason
    quarantined = test_db.query(QuarantinedRecord).filter(
        QuarantinedRecord.error_code == "UNAVAILABLE_STATION_COORDINATES"
    ).all()
    assert len(quarantined) == 3


def test_f06_known_ndbc_stations_work_correctly(test_db):
    """F-06: Authoritative stations in KNOWN_NDBC_STATIONS retain verified coordinates."""
    for st_id in ["46041", "42001", "46029", "41009", "44025"]:
        assert st_id in KNOWN_NDBC_STATIONS
        meta = KNOWN_NDBC_STATIONS[st_id]
        assert meta["latitude"] is not None and meta["latitude"] != 0.0
        assert meta["longitude"] is not None and meta["longitude"] != 0.0
        assert meta["sea_area"] != "Unknown / Unresolved Coordinates"


def test_f06_station_46041_verified_noaa_metadata():
    """F-06 / Stage B.3: Station 46041 identity and coordinates match verified NOAA NDBC Cape Elizabeth metadata."""
    assert "46041" in KNOWN_NDBC_STATIONS
    meta = KNOWN_NDBC_STATIONS["46041"]

    # Station name matches the verified NOAA identity
    assert "Cape Elizabeth" in meta["station_name"]
    assert "Aberdeen" in meta["station_name"]
    assert "Disappointment" not in meta["station_name"]

    # Coordinates match verified NOAA metadata within sensible tolerance (0.01 deg)
    assert pytest.approx(meta["latitude"], abs=0.01) == 47.351
    assert pytest.approx(meta["longitude"], abs=0.01) == -124.741

    # Provenance source URL is explicit and traceable
    assert "https://www.ndbc.noaa.gov/station_page.php?station=46041" in meta["provenance"]

    # Parsing text with station 46041 applies the verified Cape Elizabeth metadata
    records, unparseable, _ = parse_ndbc_realtime_text(
        text=SAMPLE_NDBC_VALID_TEXT,
        station_id="46041",
        retrieval_time_utc=datetime.datetime.now(datetime.timezone.utc),
    )
    assert len(records) > 0
    assert "Cape Elizabeth" in records[0]["station_name"]
    assert pytest.approx(records[0]["latitude"], abs=0.01) == 47.351
    assert pytest.approx(records[0]["longitude"], abs=0.01) == -124.741


def test_f06_quarantined_observations_excluded_from_normal_environmental_queries(test_db):
    """F-06: Quarantined unknown station observations are excluded from normal environmental queries."""
    mock_client = MagicMock(spec=ResilientHttpClient)
    ret_time = datetime.datetime.now(datetime.timezone.utc)
    mock_resp = AcquisitionResponse(
        url="https://ndbc.noaa.gov/UNKNOWN99.txt",
        status_code=200,
        text=SAMPLE_NDBC_VALID_TEXT,
        content=SAMPLE_NDBC_VALID_TEXT.encode(),
        retrieval_time_utc=ret_time,
        duration_seconds=0.1,
        headers={},
    )
    mock_client.get.return_value = mock_resp

    res = refresh_environmental_observations(
        db=test_db,
        station_ids=["UNKNOWN99"],
        client=mock_client,
    )
    assert res["status"] == "partial"
    assert res["accepted_count"] == 0
    assert res["quarantined_count"] == 3

    # Normal environmental observations table must be completely empty
    normal_obs = test_db.query(EnvironmentalObservation).all()
    assert len(normal_obs) == 0, "Quarantined observations must NOT enter environmental_observations table"

    # Quarantined table has the records, with original station ID preserved in raw_record_id and payload
    quarantined = test_db.query(QuarantinedRecord).filter(
        QuarantinedRecord.error_code == "UNAVAILABLE_STATION_COORDINATES"
    ).all()
    assert len(quarantined) == 3
    for q in quarantined:
        assert "UNKNOWN99" in q.raw_record_id
        assert "UNKNOWN99" in q.raw_payload
