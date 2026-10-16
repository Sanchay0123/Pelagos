"""
Tests for freight Parquet writer, DuckDB analytics, FastAPI endpoints, and CLI subcommands.
Traces to REQ-NFR-ABM-002, REQ-FR-DII-005, and ADR-0002.
"""

import pytest
from datetime import date, datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from pelagos.models import Base, FreightRoute, FreightObservation, FreightPublication
from pelagos.storage.freight_writer import write_freight_partition_parquet, commit_freight_publication
from pelagos.storage.freight_analytics import (
    get_freight_summary,
    get_freight_route_history,
    get_published_freight_parquet_paths,
)
from pelagos.config import settings
from pelagos.api.main import app
from pelagos.database import get_db
from pelagos.cli import build_parser, handle_freight_ingest, handle_freight_sync, handle_freight_summary


@pytest.fixture
def mem_db(tmp_path):
    db_file = tmp_path / "freight_test.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed routes
    r1 = FreightRoute(
        route_code="USG-JPN-BULK",
        route_name="U.S. Gulf to Japan",
        origin_region="U.S. Gulf",
        destination_region="Japan",
        unit_of_measure="USD/MT",
        currency="USD",
    )
    r2 = FreightRoute(
        route_code="PNW-JPN-BULK",
        route_name="PNW to Japan",
        origin_region="PNW",
        destination_region="Japan",
        unit_of_measure="USD/MT",
        currency="USD",
    )
    session.add_all([r1, r2])

    # Seed observations across 2024
    obs = [
        FreightObservation(
            route_code="USG-JPN-BULK",
            observation_date=date(2024, 1, 1),
            rate_value=50.0,
            currency="USD",
            unit_of_measure="USD/MT",
            source_name="USDA AMS",
            idempotency_key="TEST:1",
        ),
        FreightObservation(
            route_code="USG-JPN-BULK",
            observation_date=date(2024, 2, 1),
            rate_value=55.0,
            currency="USD",
            unit_of_measure="USD/MT",
            source_name="USDA AMS",
            idempotency_key="TEST:2",
        ),
        FreightObservation(
            route_code="PNW-JPN-BULK",
            observation_date=date(2024, 1, 1),
            rate_value=25.0,
            currency="USD",
            unit_of_measure="USD/MT",
            source_name="USDA AMS",
            idempotency_key="TEST:3",
        ),
    ]
    session.add_all(obs)
    session.commit()
    yield session, engine, db_file
    session.close()


def test_freight_parquet_writer(tmp_path, mem_db):
    """Verifies atomic Parquet writing, row-count verification, and SHA-256 calculation."""
    session, _, _ = mem_db
    records = session.query(FreightObservation).all()

    pub_path, rec_count, file_size, sha256_hash = write_freight_partition_parquet(
        records=records,
        base_dir=tmp_path,
        year=2024,
        generation=1,
        attempt_uuid="test-uuid",
        worker_id="test-worker",
    )

    assert pub_path.exists()
    assert rec_count == 3
    assert file_size > 0
    assert len(sha256_hash) == 64
    assert "year=2024" in str(pub_path)


def test_freight_duckdb_summary(tmp_path, mem_db):
    """Verifies that DuckDB analytics computes correct averages across published partitions."""
    session, _, _ = mem_db
    records = session.query(FreightObservation).all()

    # Write Parquet partition
    write_freight_partition_parquet(
        records=records,
        base_dir=tmp_path,
        year=2024,
    )

    summary = get_freight_summary(session, storage_dir=tmp_path)
    assert summary["source"] == "parquet_analytical"
    assert summary["total_observations"] == 3
    assert summary["distinct_routes"] == 2
    assert "USG-JPN-BULK" in summary["routes"]
    assert summary["routes"]["USG-JPN-BULK"]["mean_rate_usd_mt"] == 52.5  # (50 + 55) / 2
    assert summary["routes"]["PNW-JPN-BULK"]["mean_rate_usd_mt"] == 25.0


def test_freight_fastapi_endpoints(mem_db, monkeypatch):
    """Verifies FastAPI REST endpoints for routes, observations, summary, and history."""
    session, _, tmp_path = mem_db
    monkeypatch.setattr(settings, "parquet_storage_dir", str(tmp_path / "empty_parquet"))

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        # 1. Routes endpoint
        res_routes = client.get("/api/v1/freight/routes")
        assert res_routes.status_code == 200
        routes_data = res_routes.json()
        assert len(routes_data) >= 2
        route_codes = {r["route_code"] for r in routes_data}
        assert "USG-JPN-BULK" in route_codes

        # 2. Observations endpoint
        res_obs = client.get("/api/v1/freight/observations?route_code=USG-JPN-BULK")
        assert res_obs.status_code == 200
        obs_data = res_obs.json()
        assert len(obs_data) == 2

        # 3. Summary endpoint
        res_sum = client.get("/api/v1/freight/summary")
        assert res_sum.status_code == 200
        assert res_sum.json()["total_observations"] == 3

        # 4. Route history endpoint
        res_hist = client.get("/api/v1/freight/routes/USG-JPN-BULK/history")
        assert res_hist.status_code == 200
        assert res_hist.json()["observations_count"] == 2

        # 5. Non-existent route 404
        res_404 = client.get("/api/v1/freight/routes/NONEXISTENT/history")
        assert res_404.status_code == 404

    finally:
        app.dependency_overrides.clear()


def test_freight_cli_commands(tmp_path):
    """Verifies CLI commands: freight-ingest, freight-sync, and freight-summary."""
    db_file = tmp_path / "cli_freight.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    p_dir = tmp_path / "parquet_store"

    parser = build_parser()

    # 1. freight-ingest (offline sample)
    args_ingest = parser.parse_args([
        "freight-ingest",
        "--offline",
        "--limit", "6",
        "--db-url", db_url,
    ])
    code_ingest = handle_freight_ingest(args_ingest)
    assert code_ingest == 0

    # 2. freight-sync
    args_sync = parser.parse_args([
        "freight-sync",
        "--storage-dir", str(p_dir),
        "--db-url", db_url,
    ])
    code_sync = handle_freight_sync(args_sync)
    assert code_sync == 0

    # 3. freight-summary
    args_sum = parser.parse_args([
        "freight-summary",
        "--storage-dir", str(p_dir),
        "--db-url", db_url,
    ])
    code_sum = handle_freight_summary(args_sum)
    assert code_sum == 0


def test_freight_writer_unique_attempt_tokens(tmp_path, mem_db):
    """Verifies that separate export attempts produce distinct immutable files and do not overwrite or collide."""
    session, _, _ = mem_db
    records = session.query(FreightObservation).all()

    # Attempt 1 (auto-generated attempt token)
    path1, count1, size1, hash1 = write_freight_partition_parquet(
        records=records,
        base_dir=tmp_path,
        year=2024,
    )
    # Attempt 2 (auto-generated attempt token)
    path2, count2, size2, hash2 = write_freight_partition_parquet(
        records=records,
        base_dir=tmp_path,
        year=2024,
    )

    assert path1.exists()
    assert path2.exists()
    assert path1.name != path2.name  # Unique attempt UUIDs prevent overwriting
    assert "partition_g1_a" in path1.name
    assert "partition_g1_a" in path2.name

    # Temporary staging files must not linger
    pdir = tmp_path / "freight" / "year=2024"
    staging_files = list(pdir.glob("*.tmp.*"))
    assert len(staging_files) == 0


def test_freight_analytics_authoritative_deduplication(tmp_path, mem_db):
    """Verifies that DuckDB analytics selects only the latest authoritative attempt per year without double-counting."""
    session, _, _ = mem_db
    records = session.query(FreightObservation).all()

    # Create two separate attempt files in the same year directory
    p1, _, _, _ = write_freight_partition_parquet(records=records, base_dir=tmp_path, year=2024)
    p2, _, _, _ = write_freight_partition_parquet(records=records, base_dir=tmp_path, year=2024)

    # Even though 2 physical parquet files exist in year=2024, summary must count only 3 observations (not 6)
    summary = get_freight_summary(session, storage_dir=tmp_path)
    assert summary["source"] == "parquet_analytical"
    assert summary["total_observations"] == 3
    assert summary["published_partitions_count"] == 1


def test_freight_concurrent_exporters_older_finishes_first(tmp_path, mem_db):
    """
    Regression Test 1, 2, & 4:
    Two exporters start from the same published generation (Gen 1).
    Exporter A has an older SQL snapshot (watermark 3, count 3).
    Exporter B has a newer SQL snapshot (watermark 5, count 5) with newly ingested data.
    Order: Older snapshot (A) finishes export and commits first -> publishes Gen 2.
           Newer snapshot (B) finishes export afterward -> publishes Gen 3.
    Guarantees:
    - Authoritative generation is allocated atomically inside commit transaction (Gen 2, then Gen 3).
    - Exporter B (the newest eligible snapshot) remains authoritative!
    """
    session, _, _ = mem_db
    records_gen1 = session.query(FreightObservation).filter(FreightObservation.id <= 2).all()

    # 1. Baseline: Catalog has Gen 1 published with watermark 2
    p1, c1, s1, h1 = write_freight_partition_parquet(records=records_gen1, base_dir=tmp_path, year=2024, generation=1)
    ok1 = commit_freight_publication(
        db=session,
        year=2024,
        published_path=p1,
        record_count=c1,
        file_size_bytes=s1,
        content_sha256=h1,
        snapshot_watermark_id=2,
    )
    assert ok1 is True
    pub = session.query(FreightPublication).filter_by(year=2024).first()
    assert pub.generation == 1
    assert pub.snapshot_watermark_id == 2

    # 2. Both exporters start while published generation is 1
    # Exporter A captures older snapshot (3 records, watermark 3)
    records_a = session.query(FreightObservation).filter(FreightObservation.id <= 3).all()
    watermark_a = max(r.id for r in records_a)
    path_a, count_a, size_a, sha_a = write_freight_partition_parquet(
        records=records_a,
        base_dir=tmp_path,
        year=2024,
        generation=2,
        attempt_uuid="attempt-a",
    )

    # Ingest newer observations into database
    new_obs = [
        FreightObservation(
            route_code="USG-JPN-BULK",
            observation_date=date(2024, 11, 1),
            rate_value=58.0,
            currency="USD",
            unit_of_measure="USD/MT",
            source_name="USDA AgTransport",
            idempotency_key="TEST-2024-11-01-USG",
        ),
        FreightObservation(
            route_code="PNW-JPN-BULK",
            observation_date=date(2024, 11, 1),
            rate_value=29.0,
            currency="USD",
            unit_of_measure="USD/MT",
            source_name="USDA AgTransport",
            idempotency_key="TEST-2024-11-01-PNW",
        ),
    ]
    session.add_all(new_obs)
    session.commit()

    # Exporter B captures newer snapshot (5 records, watermark 5)
    records_b = session.query(FreightObservation).all()
    watermark_b = max(r.id for r in records_b)
    assert watermark_b > watermark_a
    path_b, count_b, size_b, sha_b = write_freight_partition_parquet(
        records=records_b,
        base_dir=tmp_path,
        year=2024,
        generation=2,
        attempt_uuid="attempt-b",
    )

    # 3. Exporter A (older snapshot) finishes export first and commits -> publishes Gen 2
    ok_a = commit_freight_publication(
        db=session,
        year=2024,
        published_path=path_a,
        record_count=count_a,
        file_size_bytes=size_a,
        content_sha256=sha_a,
        snapshot_watermark_id=watermark_a,
    )
    assert ok_a is True
    pub_mid = session.query(FreightPublication).filter_by(year=2024).first()
    assert pub_mid.generation == 2
    assert pub_mid.snapshot_watermark_id == watermark_a

    # 4. Exporter B (newer snapshot) finishes afterward and commits -> atomically allocated Gen 3
    ok_b = commit_freight_publication(
        db=session,
        year=2024,
        published_path=path_b,
        record_count=count_b,
        file_size_bytes=size_b,
        content_sha256=sha_b,
        snapshot_watermark_id=watermark_b,
    )
    assert ok_b is True
    pub_final = session.query(FreightPublication).filter_by(year=2024).first()
    assert pub_final.generation == 3
    assert pub_final.snapshot_watermark_id == watermark_b
    assert pub_final.record_count == count_b
    # The newest eligible snapshot remains authoritative!
    assert "attempt-b" in pub_final.published_artifact_path


def test_freight_concurrent_exporters_older_finishes_last_is_fenced(tmp_path, mem_db):
    """
    Regression Test 1, 3, & 4:
    Two exporters start from the same published generation (Gen 1).
    Exporter B has a newer SQL snapshot (watermark 5, count 5).
    Exporter A has an older SQL snapshot (watermark 3, count 3).
    Order: Newer snapshot (B) finishes export and commits first -> publishes Gen 2.
           Older snapshot (A) finishes export afterward -> attempts publication.
    Guarantees:
    - Exporter A is unconditionally FENCED because its watermark (3) < published watermark (5).
    - Commit returns False and rolls back.
    - Exporter B (the newest eligible snapshot) strictly remains authoritative!
    """
    session, _, _ = mem_db
    records_gen1 = session.query(FreightObservation).filter(FreightObservation.id <= 2).all()

    # 1. Baseline: Catalog has Gen 1 published with watermark 2
    p1, c1, s1, h1 = write_freight_partition_parquet(records=records_gen1, base_dir=tmp_path, year=2024, generation=1)
    commit_freight_publication(
        db=session,
        year=2024,
        published_path=p1,
        record_count=c1,
        file_size_bytes=s1,
        content_sha256=h1,
        snapshot_watermark_id=2,
    )

    # 2. Both exporters start while published generation is 1
    # Exporter A captures older snapshot (3 records, watermark 3)
    records_a = session.query(FreightObservation).filter(FreightObservation.id <= 3).all()
    watermark_a = max(r.id for r in records_a)
    path_a, count_a, size_a, sha_a = write_freight_partition_parquet(
        records=records_a,
        base_dir=tmp_path,
        year=2024,
        generation=2,
        attempt_uuid="attempt-a",
    )

    # Ingest newer observations
    new_obs = [
        FreightObservation(
            route_code="USG-JPN-BULK",
            observation_date=date(2024, 12, 1),
            rate_value=60.0,
            currency="USD",
            unit_of_measure="USD/MT",
            source_name="USDA AgTransport",
            idempotency_key="TEST-2024-12-01-USG",
        ),
        FreightObservation(
            route_code="PNW-JPN-BULK",
            observation_date=date(2024, 12, 1),
            rate_value=31.0,
            currency="USD",
            unit_of_measure="USD/MT",
            source_name="USDA AgTransport",
            idempotency_key="TEST-2024-12-01-PNW",
        ),
    ]
    session.add_all(new_obs)
    session.commit()

    # Exporter B captures newer snapshot (5 records, watermark 5)
    records_b = session.query(FreightObservation).all()
    watermark_b = max(r.id for r in records_b)
    assert watermark_b > watermark_a
    path_b, count_b, size_b, sha_b = write_freight_partition_parquet(
        records=records_b,
        base_dir=tmp_path,
        year=2024,
        generation=2,
        attempt_uuid="attempt-b",
    )

    # 3. Exporter B (newer snapshot) finishes export and commits first -> publishes Gen 2
    ok_b = commit_freight_publication(
        db=session,
        year=2024,
        published_path=path_b,
        record_count=count_b,
        file_size_bytes=size_b,
        content_sha256=sha_b,
        snapshot_watermark_id=watermark_b,
    )
    assert ok_b is True
    pub_mid = session.query(FreightPublication).filter_by(year=2024).first()
    assert pub_mid.generation == 2
    assert pub_mid.snapshot_watermark_id == watermark_b
    assert "attempt-b" in pub_mid.published_artifact_path

    # 4. Exporter A (older snapshot) finishes export afterward and attempts publication
    ok_a = commit_freight_publication(
        db=session,
        year=2024,
        published_path=path_a,
        record_count=count_a,
        file_size_bytes=size_a,
        content_sha256=sha_a,
        snapshot_watermark_id=watermark_a,
    )
    # Stale older snapshot MUST be fenced!
    assert ok_a is False

    # 5. Catalog must NOT have been regressed or corrupted
    pub_final = session.query(FreightPublication).filter_by(year=2024).first()
    assert pub_final.generation == 2
    assert pub_final.snapshot_watermark_id == watermark_b
    assert pub_final.record_count == count_b
    # Exporter B strictly remains authoritative!
    assert "attempt-b" in pub_final.published_artifact_path


def test_freight_publication_failure_preserves_last_valid_state(tmp_path, mem_db):
    """
    Verifies that failed or interrupted publication attempts do not corrupt or
    alter the authoritative catalog. The last valid publication is strictly preserved.
    """
    session, _, _ = mem_db
    records = session.query(FreightObservation).all()

    # Commit initial valid Generation 1
    p1, count1, size1, sha1 = write_freight_partition_parquet(
        records=records,
        base_dir=tmp_path,
        year=2024,
        generation=1,
    )
    ok1 = commit_freight_publication(
        db=session,
        year=2024,
        claimed_generation=1,
        published_path=str(p1),
        record_count=count1,
        file_size_bytes=size1,
        content_sha256=sha1,
    )
    assert ok1 is True

    # Simulate an export attempt for Generation 2 that crashes during commit
    try:
        class BadSession:
            def query(self, *args, **kwargs):
                raise RuntimeError("Database connection crashed during publication")
            def rollback(self):
                pass

        commit_freight_publication(
            db=BadSession(),
            year=2024,
            claimed_generation=2,
            published_path="/invalid/path/g2.parquet",
            record_count=10,
            file_size_bytes=1000,
            content_sha256="abc",
        )
    except Exception:
        pass

    # The authoritative publication must remain strictly intact
    pub = session.query(FreightPublication).filter_by(year=2024).first()
    assert pub.generation == 1
    assert pub.published_artifact_path == str(p1)
    assert pub.published_sha256 == sha1


def test_freight_publication_retry_behavior(tmp_path, mem_db):
    """
    Verifies retry behavior: when an export attempt fails before publication,
    a retry worker can successfully publish the claimed generation with a fresh unique artifact token.
    """
    session, _, _ = mem_db
    records = session.query(FreightObservation).all()

    # Attempt 1: Worker crashes / fails before committing
    p_fail, _, _, _ = write_freight_partition_parquet(
        records=records,
        base_dir=tmp_path,
        year=2024,
        generation=1,
        attempt_uuid="attempt-1-failed",
    )
    # Attempt 1 never calls commit_freight_publication

    # Catalog has no publication yet
    assert session.query(FreightPublication).filter_by(year=2024).first() is None

    # Retry Worker runs with a fresh attempt token for Generation 1
    p_retry, count_r, size_r, sha_r = write_freight_partition_parquet(
        records=records,
        base_dir=tmp_path,
        year=2024,
        generation=1,
        attempt_uuid="attempt-2-retry",
    )
    assert p_fail != p_retry

    ok = commit_freight_publication(
        db=session,
        year=2024,
        claimed_generation=1,
        published_path=str(p_retry),
        record_count=count_r,
        file_size_bytes=size_r,
        content_sha256=sha_r,
    )
    assert ok is True

    pub = session.query(FreightPublication).filter_by(year=2024).first()
    assert pub.generation == 1
    assert pub.published_artifact_path == str(p_retry)
    assert "attempt-2-retry" in pub.published_artifact_path


def test_freight_reader_selection_ignores_rogue_and_stale_files(tmp_path, mem_db):
    """
    Verifies that readers strictly query the database publication catalog,
    completely ignoring rogue, uncommitted, or stale filesystem artifacts
    even if their mtime is newer.
    """
    import os
    import time
    session, _, _ = mem_db
    records = session.query(FreightObservation).all()

    # 1. Authoritative publication committed to catalog
    p_auth, count_auth, size_auth, sha_auth = write_freight_partition_parquet(
        records=records,
        base_dir=tmp_path,
        year=2024,
        generation=1,
    )
    commit_freight_publication(
        db=session,
        year=2024,
        claimed_generation=1,
        published_path=str(p_auth),
        record_count=count_auth,
        file_size_bytes=size_auth,
        content_sha256=sha_auth,
    )

    # 2. Plant a rogue / competing file in the same year directory with a newer mtime
    year_dir = tmp_path / "freight" / "year=2024"
    rogue_path = year_dir / "partition_g99_rogue.parquet"
    rogue_path.write_bytes(p_auth.read_bytes())
    # Set rogue file's mtime 1000 seconds into the future
    future_time = time.time() + 1000
    os.utime(rogue_path, (future_time, future_time))

    # 3. get_published_freight_parquet_paths must return ONLY the catalog designated path
    published_paths = get_published_freight_parquet_paths(db=session, storage_dir=tmp_path)
    assert len(published_paths) == 1
    assert published_paths[0] == Path(p_auth).as_posix()
    assert rogue_path.as_posix() not in published_paths

    # 4. DuckDB summary must reflect only the cataloged partition (3 observations, not 6)
    summary = get_freight_summary(db=session, storage_dir=tmp_path)
    assert summary["source"] == "parquet_analytical"
    assert summary["total_observations"] == 3
    assert summary["published_partitions_count"] == 1


def test_freight_publication_atomic_cas_independent_sessions_stale_fenced(tmp_path, mem_db):
    """
    Deterministic Interleaving Regression Test with Independent Sessions:
    Verifies that publication is protected by a genuine database-enforced CAS update.
    Simulates two independent database sessions (session_a and session_b) representing
    concurrent workers that both observe the exact same baseline (Generation 1) before
    either attempts publication.

    Sequence:
    1. Worker B (newer snapshot, watermark 5) publishes first -> CAS matches 1 row, updates to Gen 2.
    2. Worker A (older snapshot, watermark 3, holding stale expected_generation 1) attempts publication.
    3. Worker A's conditional SQL UPDATE matches 0 rows in the database -> rolls back and returns False.
    4. Worker B's newer snapshot strictly remains authoritative in the catalog at Generation 2.
    """
    _, engine, _ = mem_db
    Session = sessionmaker(bind=engine)
    session_setup = Session()
    records_gen1 = session_setup.query(FreightObservation).filter(FreightObservation.id <= 2).all()

    # 1. Baseline: Catalog has Gen 1 published with watermark 2
    p1, c1, s1, h1 = write_freight_partition_parquet(records=records_gen1, base_dir=tmp_path, year=2024, generation=1)
    ok1 = commit_freight_publication(
        db=session_setup,
        year=2024,
        published_path=p1,
        record_count=c1,
        file_size_bytes=s1,
        content_sha256=h1,
        snapshot_watermark_id=2,
        expected_generation=None,
    )
    assert ok1 is True
    session_setup.close()

    # 2. Ingest newer observations into database
    session_ingest = Session()
    new_obs = [
        FreightObservation(
            route_code="USG-JPN-BULK",
            observation_date=date(2024, 12, 1),
            rate_value=60.0,
            currency="USD",
            unit_of_measure="USD/MT",
            source_name="USDA AgTransport",
            idempotency_key="TEST-2024-12-01-USG-IND",
        ),
        FreightObservation(
            route_code="PNW-JPN-BULK",
            observation_date=date(2024, 12, 1),
            rate_value=31.0,
            currency="USD",
            unit_of_measure="USD/MT",
            source_name="USDA AgTransport",
            idempotency_key="TEST-2024-12-01-PNW-IND",
        ),
    ]
    session_ingest.add_all(new_obs)
    session_ingest.commit()
    session_ingest.close()

    # 3. Two INDEPENDENT sessions observe the baseline before either attempts publication
    session_a = Session()
    session_b = Session()

    # Both workers query baseline state: both observe expected_generation = 1
    baseline_a = session_a.query(FreightPublication.generation).filter_by(year=2024).scalar()
    baseline_b = session_b.query(FreightPublication.generation).filter_by(year=2024).scalar()
    assert baseline_a == 1
    assert baseline_b == 1

    # Worker A prepares older snapshot (watermark 3)
    records_a = session_a.query(FreightObservation).filter(FreightObservation.id <= 3).all()
    watermark_a = max(r.id for r in records_a)  # 3
    path_a, count_a, size_a, sha_a = write_freight_partition_parquet(
        records=records_a, base_dir=tmp_path, year=2024, generation=2, attempt_uuid="attempt-a-ind"
    )

    # Worker B prepares newer snapshot (watermark 5)
    records_b = session_b.query(FreightObservation).all()
    watermark_b = max(r.id for r in records_b)  # 5
    assert watermark_b > watermark_a
    path_b, count_b, size_b, sha_b = write_freight_partition_parquet(
        records=records_b, base_dir=tmp_path, year=2024, generation=2, attempt_uuid="attempt-b-ind"
    )

    # 4. Worker B (newer snapshot) publishes first with its observed expected_generation=1
    ok_b = commit_freight_publication(
        db=session_b,
        year=2024,
        expected_generation=baseline_b,  # 1
        published_path=path_b,
        record_count=count_b,
        file_size_bytes=size_b,
        content_sha256=sha_b,
        snapshot_watermark_id=watermark_b,
    )
    assert ok_b is True
    session_b.close()

    # Verify that in DB, catalog has moved to Generation 2
    session_verify = Session()
    pub_mid = session_verify.query(FreightPublication).filter_by(year=2024).first()
    assert pub_mid.generation == 2
    assert pub_mid.snapshot_watermark_id == watermark_b
    assert "attempt-b-ind" in pub_mid.published_artifact_path

    # 5. Worker A attempts publication using its stale expected_generation=1 and older watermark=3
    ok_a = commit_freight_publication(
        db=session_a,
        year=2024,
        expected_generation=baseline_a,  # 1 (stale: DB is now 2)
        published_path=path_a,
        record_count=count_a,
        file_size_bytes=size_a,
        content_sha256=sha_a,
        snapshot_watermark_id=watermark_a,  # 3 (stale: DB is now 5)
    )
    # Stale attempt MUST fail CAS (0 rows updated in SQL)
    assert ok_a is False
    session_a.close()

    # 6. Verify that Worker B's newer snapshot strictly remained authoritative
    session_verify.expire_all()
    pub_final = session_verify.query(FreightPublication).filter_by(year=2024).first()
    assert pub_final.generation == 2
    assert pub_final.snapshot_watermark_id == watermark_b
    assert pub_final.record_count == count_b
    assert "attempt-b-ind" in pub_final.published_artifact_path
    session_verify.close()


def test_freight_initial_publication_race_independent_sessions(tmp_path, mem_db):
    """
    Deterministic Interleaving Regression Test for Initial Publication Race:
    Simulates two independent database sessions where both workers observe no catalog entry
    exists yet (expected_generation = None), and race to publish Generation 1.

    Guarantees:
    - Worker A inserts Generation 1 and commits.
    - Worker B catches the UNIQUE constraint conflict on year, rolls back the failed insert,
      and safely falls through to the CAS update path.
    - Because Worker B's snapshot is fresher (watermark 5 > 3), Worker B's CAS update matches 1 row,
      advances the catalog to Generation 2, and Worker B becomes authoritative.
    """
    _, engine, _ = mem_db
    Session = sessionmaker(bind=engine)

    # Empty catalog for year 2025 (no row exists)
    session_a = Session()
    session_b = Session()

    now_utc = datetime.now(timezone.utc)
    path_a = tmp_path / "freight" / "year=2025" / "partition_g1_init_a.parquet"
    path_b = tmp_path / "freight" / "year=2025" / "partition_g1_init_b.parquet"
    path_a.parent.mkdir(parents=True, exist_ok=True)
    path_a.write_bytes(b"dummy_a")
    path_b.write_bytes(b"dummy_b")

    # Worker A publishes initial entry (watermark 3)
    ok_a = commit_freight_publication(
        db=session_a,
        year=2025,
        expected_generation=None,
        published_path=path_a,
        record_count=3,
        file_size_bytes=len(b"dummy_a"),
        content_sha256="sha_init_a",
        snapshot_watermark_id=3,
    )
    assert ok_a is True
    session_a.close()

    # Worker B had also observed expected_generation = None, but attempts publication with newer watermark 5
    ok_b = commit_freight_publication(
        db=session_b,
        year=2025,
        expected_generation=None,  # Race: observed None before A committed
        published_path=path_b,
        record_count=5,
        file_size_bytes=len(b"dummy_b"),
        content_sha256="sha_init_b",
        snapshot_watermark_id=5,
    )
    # Worker B safely catches unique conflict, falls through to CAS, advances to Gen 2
    assert ok_b is True
    session_b.close()

    # Verify authoritative catalog in DB
    session_verify = Session()
    pub = session_verify.query(FreightPublication).filter_by(year=2025).first()
    assert pub.generation == 2
    assert pub.snapshot_watermark_id == 5
    assert pub.record_count == 5
    assert "partition_g2_init_b.parquet" in pub.published_artifact_path
    session_verify.close()



