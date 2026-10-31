"""Comprehensive tests for Pelagos Multi-Source Maritime Data Fusion Layer.

Verifies:
- Canonical entity models and schema integrity (ports, aliases, vessels, trade, environment).
- Source connector parsing, validation, and quarantine of invalid records.
- Deterministic deduplication and idempotent ingestion.
- Indian and international port directory expansion (OD-ROUT-06-03 reconciliation).
- Port alias resolution and disambiguation.
- Strict architectural separation of coverage types (directory != routing != freight != forecast != live AIS != charter availability).
- Multi-source coverage reporting API contracts.
"""

import datetime
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.database import Base, init_db
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
from pelagos.ingestion.ports_ingest import ingest_ports_from_fixture, validate_port_record
from pelagos.ingestion.vessel_registry import infer_vessel_class, sync_vessels_from_positions
from pelagos.ingestion.trade_ingest import ingest_trade_observations, validate_trade_record
from pelagos.ingestion.environmental_ingest import ingest_environmental_observations, validate_env_record
from pelagos.routing.ports import PortRepository
from pelagos.routing.corridors import (
    CorridorStatus,
    PORT_REGION_MAP,
    resolve_freight_corridor,
    resolve_port_region,
)
from pelagos.routing.schemas import PortRecord
from pelagos.api.routes.coverage import get_coverage_summary


@pytest.fixture
def memory_db():
    """Isolated in-memory SQLite database for test isolation."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


# =========================================================================
# 1. Port Connector Parsing, Validation, and Ingestion Tests
# =========================================================================

def test_validate_port_record_valid():
    item = {
        "unlocode": "INBOM",
        "name": "Mumbai",
        "latitude": 18.9439,
        "longitude": 72.8428,
    }
    valid, err = validate_port_record(item)
    assert valid is True
    assert err is None


def test_validate_port_record_invalid():
    # Missing UN/LOCODE
    valid, err = validate_port_record({"name": "Test", "latitude": 10.0, "longitude": 20.0})
    assert valid is False
    assert "UN/LOCODE" in err

    # Out of bounds latitude
    valid, err = validate_port_record({"unlocode": "TEST1", "name": "Test", "latitude": 95.0, "longitude": 20.0})
    assert valid is False
    assert "Latitude out of bounds" in err

    # Out of bounds longitude
    valid, err = validate_port_record({"unlocode": "TEST1", "name": "Test", "latitude": 10.0, "longitude": 200.0})
    assert valid is False
    assert "Longitude out of bounds" in err


def test_ingest_ports_idempotent(memory_db):
    res1 = ingest_ports_from_fixture(memory_db)
    assert res1["status"] in ("completed", "partial")
    assert res1["accepted_count"] > 0
    assert res1["duplicate_count"] == 0
    count_after_first = memory_db.query(Port).count()

    # Second run: strictly idempotent
    res2 = ingest_ports_from_fixture(memory_db)
    assert res2["status"] in ("completed", "partial")
    assert res2["accepted_count"] == 0
    assert res2["duplicate_count"] == res1["records_read"]
    assert memory_db.query(Port).count() == count_after_first


def test_indian_port_directory_coverage(memory_db):
    ingest_ports_from_fixture(memory_db)

    indian_ports = memory_db.query(Port).filter(Port.country_code == "IN").all()
    assert len(indian_ports) >= 16

    indian_unlocodes = {p.unlocode for p in indian_ports}
    expected_major = {
        "INBOM", "INNSA", "INIXY", "INMUN", "INMAA", "INCOK",
        "INVTZ", "INPRT", "INCCU", "INTUT", "INMRM", "INNML",
        "INENN", "INPAV", "INKAT", "INHAL"
    }
    for code in expected_major:
        assert code in indian_unlocodes, f"Missing expected major Indian port: {code}"

    # Confirm region assignment
    for p in indian_ports:
        assert p.port_region == "India"
        assert p.is_monitored_corridor_eligible is False  # Indian routes are not grain export benchmarks


def test_port_alias_resolution(memory_db):
    ingest_ports_from_fixture(memory_db)
    repo = PortRepository()

    # Direct UN/LOCODE lookup
    mumbai_by_code = repo.get_port("INBOM")
    assert mumbai_by_code is not None
    assert mumbai_by_code.name == "Mumbai (Bombay)"

    # Look up by historical name alias "Bombay"
    mumbai_by_alias = repo.get_port("Bombay")
    assert mumbai_by_alias is not None
    assert mumbai_by_alias.port_id == "INBOM"

    # Look up by code alias "JNPT"
    nhava_by_alias = repo.get_port("JNPT")
    assert nhava_by_alias is not None
    assert nhava_by_alias.port_id == "INNSA"

    # Look up by terminal name "Elliott Bay"
    seattle_by_alias = repo.get_port("Elliott Bay")
    assert seattle_by_alias is not None
    assert seattle_by_alias.port_id == "USSEA"


def test_port_coordinate_snapping():
    """Verify coordinate snapping considers canonical ports while preserving prototype lookups."""
    repo = PortRepository()

    # 1. Mumbai coordinates resolving to INBOM
    res_bom = repo.find_nearest_port(longitude=72.84, latitude=18.94, max_distance_nm=150.0)
    assert res_bom is not None
    assert res_bom[0].port_id == "INBOM"
    assert res_bom[1] < 1.0  # within 1 NM

    # Also test via resolve_endpoint
    from pelagos.routing.schemas import PortIdentifierOrCoords
    port_bom, coords_bom, err_bom = repo.resolve_endpoint(
        PortIdentifierOrCoords(coordinates=[72.84, 18.94])
    )
    assert err_bom is None
    assert port_bom is not None
    assert port_bom.port_id == "INBOM"

    # 2. Singapore coordinates resolving correctly
    res_sin = repo.find_nearest_port(longitude=103.85, latitude=1.28, max_distance_nm=150.0)
    assert res_sin is not None
    assert res_sin[0].port_id == "SGSIN"

    # 3. Unknown or distant coordinate returning None
    res_null = repo.find_nearest_port(longitude=0.0, latitude=0.0, max_distance_nm=150.0)
    assert res_null is None

    res_south_pacific = repo.find_nearest_port(longitude=-140.0, latitude=-45.0, max_distance_nm=150.0)
    assert res_south_pacific is None

    # 4. Existing prototype lookup behavior remaining intact
    res_sea = repo.find_nearest_port(longitude=-124.75, latitude=48.5, max_distance_nm=150.0)
    assert res_sea is not None
    assert res_sea[0].port_id == "USSEA"

    res_yok = repo.find_nearest_port(longitude=139.73, latitude=35.15, max_distance_nm=150.0)
    assert res_yok is not None
    assert res_yok[0].port_id == "JPYOK"

    res_rtm = repo.find_nearest_port(longitude=4.47, latitude=51.92, max_distance_nm=150.0)
    assert res_rtm is not None
    assert res_rtm[0].port_id == "NLRTM"

    res_msy = repo.find_nearest_port(longitude=-89.43, latitude=28.90, max_distance_nm=150.0)
    assert res_msy is not None
    assert res_msy[0].port_id == "USMSY"


def test_port_alias_normalization():
    """Verify safe port alias normalization for case, whitespace, punctuation, and name tokens."""
    repo = PortRepository()

    # Required test ports: Mumbai, Bombay, JNPT, Nhava Sheva, Kochi, Vizag, and Portland
    test_cases = [
        # Mumbai / Bombay
        ("Mumbai", "INBOM"),
        ("  mumbai  ", "INBOM"),
        ("Bombay", "INBOM"),
        # JNPT
        ("JNPT", "INNSA"),
        # Nhava Sheva
        ("Nhava Sheva", "INNSA"),
        ("nhava   sheva", "INNSA"),
        ("Nhava-Sheva", "INNSA"),
        # Kochi
        ("Kochi", "INCOK"),
        ("kochi", "INCOK"),
        # Vizag
        ("Vizag", "INVTZ"),
        ("vizag", "INVTZ"),
        # Portland
        ("Portland", "USPDX"),
        ("portland", "USPDX"),
        ("Port of Portland", "USPDX"),
        ("Portland (OR)", "USPDX"),
        ("PORTLAND, OR", "USPDX"),
    ]

    for query, expected_port_id in test_cases:
        port, coords, err = repo.resolve_endpoint(query)
        assert err is None, f"Query '{query}' failed with error: {err}"
        assert port is not None, f"Query '{query}' resolved to None"
        assert port.port_id == expected_port_id, f"Query '{query}' resolved to {port.port_id}, expected {expected_port_id}"
        assert coords is not None

    # Verify no ambiguous fuzzy matching: unknown strings must fail safely
    unknown_port, _, err_unknown = repo.resolve_endpoint("Random Unknown City")
    assert unknown_port is None
    assert "Unknown or unsupported port identifier" in err_unknown


# =========================================================================
# 2. Decision OD-ROUT-06-03 Reconciliation Tests
# =========================================================================

def test_od_rout_06_03_grain_port_expansion():
    # Regional PNW ports
    assert PORT_REGION_MAP.get("USPDX") == "US Pacific Northwest"
    assert PORT_REGION_MAP.get("USKAL") == "US Pacific Northwest"
    assert PORT_REGION_MAP.get("USVAN") == "US Pacific Northwest"
    assert PORT_REGION_MAP.get("USTAC") == "US Pacific Northwest"

    # Regional US Gulf ports
    assert PORT_REGION_MAP.get("USBTR") == "US Gulf Coast"
    assert PORT_REGION_MAP.get("USHOU") == "US Gulf Coast"
    assert PORT_REGION_MAP.get("USMOB") == "US Gulf Coast"

    # Corridor resolution for Portland OR -> Yokohama resolves to PNW-JPN-BULK
    res_pdx = resolve_freight_corridor("USPDX", "JPYOK")
    assert res_pdx.status == CorridorStatus.MONITORED
    assert res_pdx.route_code == "PNW-JPN-BULK"

    # Corridor resolution for Kalama WA -> Chiba resolves to PNW-JPN-BULK
    res_kal = resolve_freight_corridor("USKAL", "JPCHB")
    assert res_kal.status == CorridorStatus.MONITORED
    assert res_kal.route_code == "PNW-JPN-BULK"

    # Corridor resolution for Baton Rouge LA -> Yokohama resolves to USG-JPN-BULK
    res_btr = resolve_freight_corridor("USBTR", "JPYOK")
    assert res_btr.status == CorridorStatus.MONITORED
    assert res_btr.route_code == "USG-JPN-BULK"


# =========================================================================
# 3. Vessel Registry Synchronization Tests
# =========================================================================

def test_infer_vessel_class():
    assert infer_vessel_class("Bulk Carrier", 229.0, 32.2) == "Panamax"
    assert infer_vessel_class("Bulk Carrier", 190.0, 32.2) == "Supramax"
    assert infer_vessel_class("Bulk Carrier", 160.0, 25.0) == "Handymax"
    assert infer_vessel_class("Bulk Carrier", 299.0, 45.0) == "Capesize"
    assert infer_vessel_class("Tug", 30.0, 10.0) is None


def test_sync_vessels_from_positions(memory_db):
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    # Insert two position observations for the same vessel
    pos1 = VesselPosition(
        mmsi="123456789",
        imo="9876543",
        vessel_name="PACIFIC BULKER",
        vessel_type="Bulk Carrier",
        latitude=48.0,
        longitude=-125.0,
        observation_time=now_utc - datetime.timedelta(hours=1),
        source_name="NOAA MarineCadastre / USCG NAIS",
        license_type="Public Domain",
        idempotency_key="test:1",
        length_meters=225.0,
        width_meters=32.2,
    )
    pos2 = VesselPosition(
        mmsi="123456789",
        imo="9876543",
        vessel_name="PACIFIC BULKER",
        vessel_type="Bulk Carrier",
        latitude=48.2,
        longitude=-125.2,
        observation_time=now_utc,
        source_name="NOAA MarineCadastre / USCG NAIS",
        license_type="Public Domain",
        idempotency_key="test:2",
        length_meters=225.0,
        width_meters=32.2,
    )
    memory_db.add_all([pos1, pos2])
    memory_db.commit()

    res = sync_vessels_from_positions(memory_db)
    assert res["status"] == "completed"
    assert res["records_read"] == 1
    assert res["accepted_count"] == 1

    vessel = memory_db.query(Vessel).filter(Vessel.mmsi == "123456789").first()
    assert vessel is not None
    assert vessel.vessel_name == "PACIFIC BULKER"
    assert vessel.vessel_class == "Panamax"
    assert vessel.commercial_status == "active"


# =========================================================================
# 4. Trade and Environmental Connector Tests
# =========================================================================

def test_trade_connector_validation_and_idempotency(memory_db):
    # Validation tests
    valid, _ = validate_trade_record({
        "reporter_iso2": "US",
        "partner_iso2": "JP",
        "commodity_code": "100199",
        "period_date": "2024-10-01",
        "volume_metric_tons": 50000.0,
    })
    assert valid is True

    invalid, err = validate_trade_record({
        "reporter_iso2": "US",
        "partner_iso2": "JP",
        "commodity_code": "100199",
        "period_date": "2024-10-01",
        "volume_metric_tons": -100.0,
    })
    assert invalid is False
    assert "positive" in err

    # Ingestion idempotency
    res1 = ingest_trade_observations(memory_db)
    assert res1["status"] in ("completed", "partial")
    assert res1["accepted_count"] > 0
    trade_count = memory_db.query(TradeObservation).count()

    res2 = ingest_trade_observations(memory_db)
    assert res2["accepted_count"] == 0
    assert res2["duplicate_count"] == res1["records_read"]
    assert memory_db.query(TradeObservation).count() == trade_count


def test_environmental_connector_validation_and_idempotency(memory_db):
    # Validation tests
    valid, _ = validate_env_record({
        "station_id": "46041",
        "observation_time": "2024-10-15T00:00:00Z",
        "latitude": 46.107,
        "longitude": -124.502,
        "wave_height_meters": 3.2,
    })
    assert valid is True

    unphysical, err = validate_env_record({
        "station_id": "46041",
        "observation_time": "2024-10-15T00:00:00Z",
        "latitude": 46.107,
        "longitude": -124.502,
        "wave_height_meters": 55.0,  # Unphysical wave height
    })
    assert unphysical is False
    assert "Unphysical" in err

    # Ingestion idempotency
    res1 = ingest_environmental_observations(memory_db)
    assert res1["status"] in ("completed", "partial")
    assert res1["accepted_count"] > 0
    env_count = memory_db.query(EnvironmentalObservation).count()

    res2 = ingest_environmental_observations(memory_db)
    assert res2["accepted_count"] == 0
    assert res2["duplicate_count"] == res1["records_read"]
    assert memory_db.query(EnvironmentalObservation).count() == env_count


# =========================================================================
# 5. Separation of Coverage Types Tests
# =========================================================================

def test_separation_of_coverage_types():
    # 1. Port exists in directory, but route is unmonitored for freight
    res_in_sg = resolve_freight_corridor("INBOM", "SGSIN")
    assert res_in_sg.status == CorridorStatus.UNMONITORED
    assert res_in_sg.route_code is None
    assert "does not map to any monitored commercial freight region" in res_in_sg.reason or "unmonitored" in res_in_sg.reason

    # 2. Reverse direction is unmonitored
    res_rev = resolve_freight_corridor("JPYOK", "USSEA")
    assert res_rev.status == CorridorStatus.UNMONITORED
    assert "Reverse corridor" in res_rev.reason

    # 3. Northern Europe -> Southeast Asia is unmonitored
    res_nl_sg = resolve_freight_corridor("NLRTM", "SGSIN")
    assert res_nl_sg.status == CorridorStatus.UNMONITORED


def test_coverage_summary_endpoint(memory_db):
    ingest_ports_from_fixture(memory_db)
    ingest_trade_observations(memory_db)
    ingest_environmental_observations(memory_db)

    summary = get_coverage_summary(memory_db)
    assert summary["status"] == "ok"
    assert "separation_of_coverage_types" in summary
    assert summary["port_directory"]["total_ports"] >= 37
    assert summary["port_directory"]["indian_ports_count"] >= 16
    assert summary["vessels_and_ais"]["telemetry_mode"] == "historical"
    assert summary["vessels_and_ais"]["commercial_charter_availability"] == "unavailable"
    assert summary["trade_and_commodities"]["total_observations"] > 0
    assert summary["environmental_oceanographic"]["total_observations"] > 0
