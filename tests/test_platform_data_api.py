"""Tests for Stage E1 Platform Data Overview & Detailed Data Explorer REST APIs."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from pelagos.api.main import app
from pelagos.database import Base, get_db
from pelagos.forecasting.schemas import DEFAULT_BASELINE_MODEL
from pelagos.models import (
    BunkerPriceObservation,
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
from pelagos.storage.platform_data import (
    EXPLORER_DOMAINS,
    clear_platform_data_cache,
)


@pytest.fixture
def isolated_client(tmp_path: Path) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    """Isolated SQLite database-backed TestClient with clean dependency override."""
    db_file = tmp_path / "test_e1.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    clear_platform_data_cache()
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client, TestingSessionLocal
    finally:
        app.dependency_overrides.clear()
        clear_platform_data_cache()
        engine.dispose()


def _seed_sample_db(session: Session) -> None:
    """Populate controlled authentic-shape records across all 8 domains."""
    now = datetime.datetime(2026, 11, 8, 12, 0, tzinfo=datetime.timezone.utc)
    session.add_all(
        [
            FreightRoute(
                route_code="PNW-JPN-BULK",
                route_name="PNW to Japan Grain",
                origin_region="U.S. Pacific Northwest",
                destination_region="Japan",
                vessel_class="Panamax",
                cargo_type="Grain",
                unit_of_measure="USD/MT",
                currency="USD",
                benchmark_distance_nm=4520.0,
            ),
            FreightRoute(
                route_code="USG-JPN-BULK",
                route_name="USG to Japan Grain",
                origin_region="U.S. Gulf Coast",
                destination_region="Japan",
                vessel_class="Panamax",
                cargo_type="Grain",
                unit_of_measure="USD/MT",
                currency="USD",
                benchmark_distance_nm=9150.0,
            ),
            FreightRoute(
                route_code="SPREAD-USG-PNW-JPN",
                route_name="Spread USG-PNW",
                origin_region="US",
                destination_region="Japan",
                vessel_class="Panamax",
                cargo_type="Spread",
                unit_of_measure="USD/MT",
                currency="USD",
            ),
        ]
    )

    # 18 months so build_forecast_dataset (burn_in=12) yields 6 rows per corridor
    for m_idx in range(18):
        year = 2024 + (m_idx // 12)
        month = (m_idx % 12) + 1
        d = datetime.date(year, month, 1)
        pnw_rate = 30.0 + m_idx
        usg_rate = 58.0 + m_idx
        is_recon = m_idx == 2
        session.add(
            FreightObservation(
                route_code="PNW-JPN-BULK",
                observation_date=d,
                rate_value=pnw_rate,
                currency="USD",
                unit_of_measure="USD/MT",
                is_derived=False,
                is_reconstructed=False,
                source_name="USDA AMS",
                idempotency_key=f"pnw:{d.isoformat()}",
                ingestion_time=now,
            )
        )
        session.add(
            FreightObservation(
                route_code="USG-JPN-BULK",
                observation_date=d,
                rate_value=usg_rate,
                currency="USD",
                unit_of_measure="USD/MT",
                is_derived=False,
                is_reconstructed=is_recon,
                original_rate_value=usg_rate - 1.5 if is_recon else None,
                reconciliation_method="usda_ftr_cross_check" if is_recon else None,
                source_name="USDA AMS",
                idempotency_key=f"usg:{d.isoformat()}",
                ingestion_time=now,
            )
        )
        session.add(
            FreightObservation(
                route_code="SPREAD-USG-PNW-JPN",
                observation_date=d,
                rate_value=usg_rate - pnw_rate,
                currency="USD",
                unit_of_measure="USD/MT",
                is_derived=True,
                is_reconstructed=False,
                source_name="USDA AMS",
                idempotency_key=f"spr:{d.isoformat()}",
                ingestion_time=now,
            )
        )

    session.add(
        TradeObservation(
            reporter_country="United States",
            reporter_iso2="US",
            partner_country="Japan",
            partner_iso2="JP",
            commodity_code="100199",
            commodity_name="Wheat",
            trade_flow="Export",
            period_date=datetime.date(2024, 10, 1),
            period_type="monthly",
            volume_metric_tons=150000.0,
            trade_value_usd=42000000.0,
            unit_price_usd_per_mt=280.0,
            source_name="USDA FAS GATS",
            license_type="Public Domain",
            idempotency_key="trd:1",
        )
    )

    session.add(
        EnvironmentalObservation(
            station_id="46041",
            station_name="Cape Elizabeth, WA",
            latitude=47.351,
            longitude=-124.741,
            sea_area="North Pacific",
            observation_time=datetime.datetime(2026, 10, 30, 16, 0, tzinfo=datetime.timezone.utc),
            wave_height_meters=None,  # Verify missing fields remain NULL
            wind_speed_knots=15.0,
            air_pressure_hpa=1012.0,
            source_name="NOAA NDBC",
            quality_status="verified",
            idempotency_key="env:1",
        )
    )

    session.add(
        BunkerPriceObservation(
            port_code="ARBUE",
            port_name="Buenos Aires",
            country_code="AR",
            fuel_grade="VLSFO",
            price_value=957.5,
            currency="USD",
            unit="USD/MT",
            pricing_type="indicative",
            delivery_basis="delivered",
            source_name="Bunker Fuel Prices",
            source_feed="oilpriceapi:test",
            observation_time=datetime.datetime(2026, 11, 6, 17, 0, tzinfo=datetime.timezone.utc),
            retrieval_time_utc=datetime.datetime(2026, 11, 9, 11, 30, tzinfo=datetime.timezone.utc),
            provenance_notes="Indicative only.",
            idempotency_key="bnk:1",
        )
    )

    session.add_all(
        [
            Port(
                unlocode="USSEA",
                port_id="USSEA",
                source_id="WPI:USSEA",
                name="Seattle",
                country="United States",
                country_code="US",
                latitude=48.5,
                longitude=-124.75,
                port_region="US Pacific Northwest",
                is_monitored_corridor_eligible=True,
                nearest_graph_node="MARNET_10",
            ),
            Port(
                unlocode="INBOM",
                port_id="INBOM",
                source_id="WPI:INBOM",
                name="Mumbai",
                country="India",
                country_code="IN",
                latitude=18.94,
                longitude=72.84,
                port_region="India",
                is_monitored_corridor_eligible=False,
                nearest_graph_node=None,
            ),
        ]
    )
    session.add(
        PortAlias(
            port_unlocode="INBOM",
            alias_name="Bombay Port",
            alias_type="historical_name",
            source_name="UNECE",
        )
    )

    session.add(
        Vessel(
            mmsi="367000111",
            imo="9123456",
            vessel_name="BULK PIONEER",
            vessel_type="Cargo",
            vessel_class="Panamax",
            dwt_tonnage=None,
            length_meters=225.0,
            width_meters=32.2,
            draft_max_meters=13.5,
            source_name="NOAA MarineCadastre",
            commercial_status="unknown",
        )
    )
    session.add(
        VesselPosition(
            mmsi="367000111",
            imo="9123456",
            vessel_name="BULK PIONEER",
            vessel_type="Cargo",
            latitude=48.1,
            longitude=-124.2,
            speed_knots=12.4,
            observation_time=datetime.datetime(2024, 1, 1, 0, 0, 5, tzinfo=datetime.timezone.utc),
            source_name="NOAA MarineCadastre",
            license_type="Public Domain",
            idempotency_key="pos:1",
        )
    )

    session.add(
        IngestionRun(
            run_type="remote",
            source_name="USDA AgTransport",
            source_partition="ehs5-yac3",
            records_read=54,
            accepted_count=54,
            quarantined_count=1,
            duplicate_count=0,
            unparseable_count=0,
            failed_count=0,
            status="completed",
            error_details="Authorization: Bearer sk-secret-token-should-not-leak",
            started_at=now - datetime.timedelta(minutes=2),
            completed_at=now,
        )
    )
    session.add(
        QuarantinedRecord(
            source_name="Bunker Fuel Prices",
            error_code="CURRENCY_MISMATCH",
            error_details="api_key=my_hidden_key CNY quote under USD feed",
            raw_payload=json.dumps({"port": "CNZOS", "token": "secret123"}),
            ingestion_time=now,
        )
    )
    session.commit()


def test_overview_empty_database_honest(isolated_client):
    client, _ = isolated_client
    resp = client.get("/api/v1/platform-data/overview?use_cache=false")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["global_summary"]["total_genuine_records"] == 0
    assert data["global_summary"]["domains_populated_count"] == 0
    assert data["domains"]["freight"]["status"] == "empty"
    assert data["domains"]["bunker"]["corridor_hubs_covered_count"] == 0
    assert data["domains"]["datasets"]["status"] == "unavailable"


def test_overview_aggregation_and_cache(isolated_client):
    client, SessionMaker = isolated_client
    with SessionMaker() as session:
        _seed_sample_db(session)

    r1 = client.get("/api/v1/platform-data/overview")
    assert r1.status_code == 200
    d1 = r1.json()
    gs = d1["global_summary"]
    bd = gs["breakdown"]

    # 54 freight + 1 trade + 1 env + 1 bunker + 1 AIS = 58 raw (40 source + 18 derived spread); + (2 ports + 1 vessel) = 61
    assert bd["raw_observations_total"] == 58
    assert bd["source_observations_excluding_derived"] == 40
    assert bd["freight_underlying_corridor"] == 36
    assert bd["freight_derived_spread"] == 18
    assert bd["freight_reconstructed"] == 1
    assert bd["freight_publications_count"] == 0
    assert bd["registered_physical_entities"] == 3
    assert bd["identifier_mappings_port_aliases"] == 1
    assert gs["total_genuine_records"] == 61
    assert gs["domains_populated_count"] == 8
    assert gs["domains_with_records_count"] == 8
    assert gs["domains_fully_populated_count"] == 7
    assert gs["domains_partial_count"] == 1
    assert gs["latest_successful_ingestion_source"] == "USDA AgTransport"
    assert d1["domains"]["freight"]["active_production_baseline"] == DEFAULT_BASELINE_MODEL == "persistence"
    assert d1["cache"]["cached"] is False

    r2 = client.get("/api/v1/platform-data/overview")
    assert r2.status_code == 200
    assert r2.json()["cache"]["cached"] is True


def test_explorer_domains_pagination_sorting_and_filters(isolated_client):
    client, SessionMaker = isolated_client
    with SessionMaker() as session:
        _seed_sample_db(session)

    # Freight pagination & filter
    r_fr = client.get(
        "/api/v1/platform-data/explorer/freight?page=1&page_size=5&series_type=underlying&sort_by=observation_date&sort_order=desc"
    )
    assert r_fr.status_code == 200
    d_fr = r_fr.json()
    assert d_fr["status"] == "ok"
    assert d_fr["total_count"] == 36
    assert d_fr["total_pages"] == 8
    assert len(d_fr["items"]) == 5
    assert all(not item["is_derived"] for item in d_fr["items"])

    r_recon = client.get("/api/v1/platform-data/explorer/freight?reconstructed_only=true")
    assert r_recon.status_code == 200
    assert r_recon.json()["total_count"] == 1
    assert r_recon.json()["items"][0]["reconciliation_method"] == "usda_ftr_cross_check"

    # Trade explicitly preserves unit price semantics
    r_tr = client.get("/api/v1/platform-data/explorer/trade?reporter_iso2=US")
    assert r_tr.status_code == 200
    assert "NOT an Ocean Freight Rate" in r_tr.json()["items"][0]["unit_price_semantic_label"]

    # Environmental preserves NULL wave height (no interpolation)
    r_env = client.get("/api/v1/platform-data/explorer/environmental?station_id=46041")
    assert r_env.status_code == 200
    assert r_env.json()["items"][0]["wave_height_meters"] is None

    # Bunker keeps USSEA/USHOU/JPYOK unavailable
    r_bnk = client.get("/api/v1/platform-data/explorer/bunker")
    assert r_bnk.status_code == 200
    d_bnk = r_bnk.json()
    assert d_bnk["availability_status"] == "partial"
    corridor_hubs = d_bnk["secondary_panels"]["corridor_hub_coverage"]
    assert all(h["status"] == "unavailable" for h in corridor_hubs)

    # Vessels keeps historical qualification & preserves missing DWT
    r_vs = client.get("/api/v1/platform-data/explorer/vessels?has_imo=true")
    assert r_vs.status_code == 200
    v_item = r_vs.json()["items"][0]
    assert v_item["is_live"] is False
    assert v_item["dwt_tonnage"] is None
    assert "dwt_tonnage" in v_item["missing_particulars"]

    # Ports search by alias resolves INBOM
    r_pt = client.get("/api/v1/platform-data/explorer/ports?search=Bombay")
    assert r_pt.status_code == 200
    assert r_pt.json()["total_count"] == 1
    assert r_pt.json()["items"][0]["unlocode"] == "INBOM"
    assert r_pt.json()["items"][0]["aliases"][0]["alias_name"] == "Bombay Port"

    # Datasets pagination & baseline labeling
    r_ds = client.get("/api/v1/platform-data/explorer/datasets?route_code=PNW-JPN-BULK&horizon=1&page=1&page_size=4")
    assert r_ds.status_code == 200
    d_ds = r_ds.json()
    assert d_ds["total_count"] == 6
    assert len(d_ds["items"]) == 4
    assert d_ds["secondary_panels"]["benchmarks"]["production_baseline"] == "persistence"

    # Sources redacts secrets cleanly in both runs & quarantine views
    r_src = client.get("/api/v1/platform-data/explorer/sources?view=runs")
    assert r_src.status_code == 200
    err_detail = r_src.json()["items"][0]["error_details"]
    assert "sk-secret-token" not in err_detail
    assert "[REDACTED]" in err_detail

    r_quar = client.get("/api/v1/platform-data/explorer/sources?view=quarantine")
    assert r_quar.status_code == 200
    q_err = r_quar.json()["items"][0]["error_details"]
    assert "my_hidden_key" not in q_err
    assert "[REDACTED]" in q_err


def test_explorer_error_and_unavailable_states(isolated_client):
    client, _ = isolated_client

    # Invalid domain -> 404
    r_404 = client.get("/api/v1/platform-data/explorer/invalid_domain")
    assert r_404.status_code == 404

    # Invalid sort field -> 400
    r_bad_sort = client.get("/api/v1/platform-data/explorer/freight?sort_by=nonexistent_col")
    assert r_bad_sort.status_code == 400
    assert "Allowed fields" in r_bad_sort.json()["detail"]

    # Invalid page_size -> 422
    r_big_page = client.get("/api/v1/platform-data/explorer/freight?page_size=5000")
    assert r_big_page.status_code == 422

    # Datasets on empty DB -> unavailable envelope
    r_ds_empty = client.get("/api/v1/platform-data/explorer/datasets")
    assert r_ds_empty.status_code == 200
    assert r_ds_empty.json()["status"] == "unavailable"
    assert r_ds_empty.json()["availability_status"] == "unavailable"

    # Derived spread explicitly unavailable for direct dataset build
    r_spread = client.get("/api/v1/platform-data/explorer/datasets?route_code=SPREAD-USG-PNW-JPN")
    assert r_spread.status_code == 200
    assert r_spread.json()["status"] == "unavailable"

    # All other domains on empty DB return clean empty status
    for dom in EXPLORER_DOMAINS:
        if dom == "datasets":
            continue
        r = client.get(f"/api/v1/platform-data/explorer/{dom}")
        assert r.status_code == 200
        assert r.json()["status"] == "empty"
        assert r.json()["items"] == []


def test_live_sqlite_database_counts_parity():
    """Verify live pelagos.db endpoint reflects real DB rows with zero fabrication."""
    clear_platform_data_cache()
    client = TestClient(app)
    r = client.get("/api/v1/platform-data/overview?use_cache=false")
    assert r.status_code == 200
    data = r.json()
    bd = data["global_summary"]["breakdown"]
    assert data["global_summary"]["total_genuine_records"] == 1424
    assert data["global_summary"]["domains_with_records_count"] == 8
    assert data["global_summary"]["domains_fully_populated_count"] == 7
    assert data["global_summary"]["domains_partial_count"] == 1
    assert bd["raw_observations_total"] == 1261
    assert bd["source_observations_excluding_derived"] == 891
    assert bd["freight_underlying_corridor"] == 740
    assert bd["freight_derived_spread"] == 370
    assert bd["freight_reconstructed"] == 10
    assert bd["freight_publications_count"] == 31
    assert bd["trade_observations"] == 13
    assert bd["environmental_observations"] == 13
    assert bd["bunker_observations"] == 5
    assert bd["ais_position_observations"] == 120
    assert bd["canonical_ports"] == 43
    assert bd["canonical_vessels"] == 120
    assert bd["identifier_mappings_port_aliases"] == 77
    assert bd["derived_dataset_rows"] == 2148
