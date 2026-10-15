"""Tests for DuckDB analytical queries, Parquet catalog APIs, and CLI subcommands."""

import pytest
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from pelagos.models import Base, VesselPosition, ParquetExportTask
from pelagos.storage.writer import write_partition_parquet
from pelagos.storage.outbox import stage_or_reactivate_outbox_task, commit_export_completion
from pelagos.storage.analytics import get_analytics_summary, get_vessel_track_from_parquet
from pelagos.storage.catalog import get_published_partition_catalog
from pelagos.api.main import app
from pelagos.database import get_db
from pelagos.cli import build_parser, handle_parquet_sync, handle_parquet_catalog, handle_parquet_reconcile, handle_analytics_summary


@pytest.fixture
def mem_db(tmp_path):
    db_file = tmp_path / "analytics_fixture.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed 3 records on 2024-01-01
    obs1 = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    obs2 = datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
    obs3 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    recs = [
        VesselPosition(
            id=1, mmsi="367000111", vessel_name="SEA LION", vessel_type="Tug",
            latitude=28.1, longitude=-94.1, speed_knots=10.0, course_degrees=90.0,
            observation_time=obs1, ingestion_time=obs1, processing_time=obs1,
            source_name="NOAA", license_type="CC0", idempotency_key="K1"
        ),
        VesselPosition(
            id=2, mmsi="367000111", vessel_name="SEA LION", vessel_type="Tug",
            latitude=28.2, longitude=-94.0, speed_knots=12.0, course_degrees=92.0,
            observation_time=obs2, ingestion_time=obs2, processing_time=obs2,
            source_name="NOAA", license_type="CC0", idempotency_key="K2"
        ),
        VesselPosition(
            id=3, mmsi="367000222", vessel_name="OCEAN GIANT", vessel_type="Cargo",
            latitude=28.5, longitude=-93.5, speed_knots=18.5, course_degrees=180.0,
            observation_time=obs3, ingestion_time=obs3, processing_time=obs3,
            source_name="NOAA", license_type="CC0", idempotency_key="K3"
        ),
    ]
    for r in recs:
        session.add(r)
    session.commit()

    # Stage outbox task
    stage_or_reactivate_outbox_task(session, "2024-01-01")
    session.commit()

    # Worker claims task to get assigned attempt_uuid
    from pelagos.storage.outbox import claim_next_export_task
    claimed = claim_next_export_task(session, worker_id="test-worker", lease_seconds=120)
    assert claimed is not None
    _, attempt_uuid = claimed

    # Stage and export to Parquet
    pub_path, count, fsize, sha = write_partition_parquet(
        records=recs,
        base_dir=tmp_path,
        partition_date_str="2024-01-01",
        generation=1,
        attempt_uuid=attempt_uuid,
        worker_id="test-worker"
    )

    commit_export_completion(
        db=session,
        partition_date="2024-01-01",
        claimed_generation=1,
        worker_id="test-worker",
        attempt_uuid=attempt_uuid,
        published_path=str(pub_path.resolve()),
        record_count=count,
        file_size_bytes=fsize,
        content_sha256=sha
    )

    yield session
    session.close()


def test_duckdb_analytics_summary(mem_db):
    """Verifies that DuckDB computes exact metrics across published Parquet partitions."""
    summary = get_analytics_summary(mem_db)
    assert summary["source"] == "parquet_analytical"
    assert summary["published_partitions_count"] == 1
    assert summary["total_observations"] == 3
    assert summary["distinct_vessels"] == 2
    assert "Tug" in summary["vessel_type_distribution"]
    assert "Cargo" in summary["vessel_type_distribution"]
    assert summary["vessel_type_distribution"]["Tug"] == 2
    assert summary["vessel_type_distribution"]["Cargo"] == 1
    assert summary["speed_percentiles"]["p50_knots"] is not None


def test_vessel_track_extraction(mem_db):
    """Verifies that time-series track points are extracted from Parquet via DuckDB."""
    track = get_vessel_track_from_parquet(mem_db, mmsi="367000111")
    assert len(track) == 2
    assert track[0]["vessel_name"] == "SEA LION"
    assert track[0]["speed_knots"] == 10.0
    assert track[1]["speed_knots"] == 12.0
    assert track[0]["timestamp"] < track[1]["timestamp"]


def test_analytics_api_endpoints(mem_db):
    """Verifies FastAPI analytics routes: summary, partitions, and track."""
    def override_get_db():
        yield mem_db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        # 1. Summary
        res_sum = client.get("/api/v1/analytics/summary")
        assert res_sum.status_code == 200
        data_sum = res_sum.json()
        assert data_sum["total_observations"] == 3

        # 2. Partitions
        res_part = client.get("/api/v1/analytics/partitions")
        assert res_part.status_code == 200
        data_part = res_part.json()
        assert len(data_part) == 1
        assert data_part[0]["partition_date"] == "2024-01-01"

        # 3. Vessel track found
        res_track = client.get("/api/v1/analytics/vessels/367000111/track")
        assert res_track.status_code == 200
        data_track = res_track.json()
        assert data_track["status"] == "published"
        assert data_track["points_count"] == 2

        # 4. Vessel not found (404)
        res_404 = client.get("/api/v1/analytics/vessels/999999999/track")
        assert res_404.status_code == 404

    finally:
        app.dependency_overrides.clear()


def test_cli_parquet_commands(tmp_path):
    """Verifies CLI parquet-sync, parquet-catalog, parquet-reconcile, and analytics-summary."""
    db_file = tmp_path / "cli_test.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    p_dir = tmp_path / "parquet_store"

    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    obs = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    pos = VesselPosition(
        id=1, mmsi="367000555", vessel_name="CLI VESSEL", vessel_type="Cargo",
        latitude=25.0, longitude=-90.0, speed_knots=14.0,
        observation_time=obs, ingestion_time=obs, processing_time=obs,
        source_name="NOAA", license_type="CC0", idempotency_key="CLI:1"
    )
    session.add(pos)
    stage_or_reactivate_outbox_task(session, "2024-01-01")
    session.commit()
    session.close()

    parser = build_parser()

    # 1. parquet-sync
    args_sync = parser.parse_args([
        "parquet-sync",
        "--db-url", db_url,
        "--storage-dir", str(p_dir),
        "--worker-id", "cli-worker"
    ])
    code_sync = handle_parquet_sync(args_sync)
    assert code_sync == 0

    # 2. parquet-catalog
    args_cat = parser.parse_args([
        "parquet-catalog",
        "--db-url", db_url
    ])
    code_cat = handle_parquet_catalog(args_cat)
    assert code_cat == 0

    # 3. analytics-summary
    args_analytics = parser.parse_args([
        "analytics-summary",
        "--db-url", db_url
    ])
    code_analytics = handle_analytics_summary(args_analytics)
    assert code_analytics == 0

    # 4. parquet-reconcile
    args_rec = parser.parse_args([
        "parquet-reconcile",
        "--db-url", db_url,
        "--storage-dir", str(p_dir)
    ])
    code_rec = handle_parquet_reconcile(args_rec)
    assert code_rec == 0
