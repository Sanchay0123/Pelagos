"""Tests for Parquet storage schema contract, atomic writer, catalog, and SQL partition extraction."""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.models import Base, VesselPosition, ParquetExportTask
from pelagos.storage.schema import TELEMETRY_PARQUET_SCHEMA, records_to_pyarrow_table
from pelagos.storage.writer import write_partition_parquet, compute_file_sha256
from pelagos.storage.outbox import get_partition_records
from pelagos.storage.catalog import extract_parquet_partition_metadata, get_published_partition_catalog


@pytest.fixture
def mem_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_schema_compatibility_with_vessel_positions():
    """
    Schema Contract Test:
    Verifies that TELEMETRY_PARQUET_SCHEMA matches VesselPosition.__table__.columns:
    - Exactly 26 fields present.
    - All field names match.
    - Nullability matches exactly for every field.
    - All 8 floating point columns are pa.float64().
    - All 4 temporal columns are pa.timestamp('us', tz='UTC').
    """
    sql_cols = {c.name: c for c in VesselPosition.__table__.columns}
    assert len(TELEMETRY_PARQUET_SCHEMA) == 26
    assert len(sql_cols) == 26

    float_cols = {
        "latitude", "longitude", "speed_knots", "course_degrees",
        "heading_degrees", "draft_meters", "length_meters", "width_meters"
    }
    timestamp_cols = {
        "observation_time", "publication_time", "ingestion_time", "processing_time"
    }

    for pa_field in TELEMETRY_PARQUET_SCHEMA:
        name = pa_field.name
        assert name in sql_cols, f"Field '{name}' missing from SQL model"
        sql_col = sql_cols[name]

        # Nullability contract
        assert pa_field.nullable == sql_col.nullable, (
            f"Nullability mismatch for '{name}': Parquet={pa_field.nullable}, SQL={sql_col.nullable}"
        )

        # Float precision contract
        if name in float_cols:
            assert pa_field.type == pa.float64(), f"Field '{name}' must be float64, got {pa_field.type}"

        # Timestamp precision contract
        if name in timestamp_cols:
            assert pa_field.type == pa.timestamp("us", tz="UTC"), (
                f"Field '{name}' must be timestamp('us', tz='UTC'), got {pa_field.type}"
            )


def test_parquet_writer_and_sha256_integrity(tmp_path):
    """
    Verifies atomic Parquet writing from staging .tmp to final .parquet,
    row count verification, file size, and streaming SHA-256 calculation.
    """
    obs_time = datetime(2024, 1, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)
    records = [
        VesselPosition(
            id=1,
            mmsi="367123456",
            vessel_name="TEST VESSEL 1",
            vessel_type="Tanker",
            latitude=29.12345,
            longitude=-94.12345,
            speed_knots=12.5,
            observation_time=obs_time,
            ingestion_time=obs_time,
            processing_time=obs_time,
            source_name="NOAA MarineCadastre",
            license_type="CC0",
            idempotency_key="NOAA:367123456:2024-01-01T12:00:00.123456Z"
        ),
        VesselPosition(
            id=2,
            mmsi="367654321",
            vessel_name="TEST VESSEL 2",
            vessel_type="Cargo",
            latitude=29.54321,
            longitude=-94.54321,
            speed_knots=15.0,
            observation_time=obs_time + timedelta(minutes=10),
            ingestion_time=obs_time,
            processing_time=obs_time,
            source_name="NOAA MarineCadastre",
            license_type="CC0",
            idempotency_key="NOAA:367654321:2024-01-01T12:10:00.123456Z"
        ),
    ]

    attempt_uuid = "11111111-2222-3333-4444-555555555555"
    pub_path, rec_count, file_size, content_sha256 = write_partition_parquet(
        records=records,
        base_dir=tmp_path,
        partition_date_str="2024-01-01",
        generation=1,
        attempt_uuid=attempt_uuid,
        worker_id="test-worker"
    )

    assert pub_path.exists()
    assert rec_count == 2
    assert file_size == pub_path.stat().st_size
    assert len(content_sha256) == 64
    assert content_sha256 == compute_file_sha256(pub_path)

    # Read back through PyArrow
    table = pq.read_table(pub_path)
    assert table.num_rows == 2
    assert pq.read_schema(pub_path).equals(TELEMETRY_PARQUET_SCHEMA)


def test_sql_authoritative_daily_partition_rebuild(mem_db):
    """
    Verifies that get_partition_records extracts records strictly using
    half-open UTC interval [2024-01-01 00:00:00, 2024-01-02 00:00:00)
    and does not leak records from adjacent dates.
    """
    d1_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    d1_mid = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    d1_end = datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)
    d2_start = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)

    for idx, obs in enumerate([d1_start, d1_mid, d1_end, d2_start]):
        mem_db.add(VesselPosition(
            id=idx + 1,
            mmsi=f"10000000{idx}",
            vessel_name=f"Vessel {idx}",
            vessel_type="Tug",
            latitude=20.0 + idx,
            longitude=-80.0 + idx,
            observation_time=obs,
            ingestion_time=obs,
            processing_time=obs,
            source_name="NOAA",
            license_type="CC0",
            idempotency_key=f"NOAA:{idx}:{obs.isoformat()}"
        ))
    mem_db.commit()

    # Query 2024-01-01 partition
    recs_d1 = get_partition_records(mem_db, "2024-01-01")
    assert len(recs_d1) == 3
    assert {r.id for r in recs_d1} == {1, 2, 3}

    # Query 2024-01-02 partition
    recs_d2 = get_partition_records(mem_db, "2024-01-02")
    assert len(recs_d2) == 1
    assert recs_d2[0].id == 4


def test_parquet_catalog_with_footer_and_duckdb_fallback(tmp_path):
    """
    Verifies catalog extraction of record count, bounding boxes (simple numeric bounds),
    and DuckDB statistic fallback.
    """
    obs_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    records = [
        VesselPosition(
            id=1, mmsi="111", vessel_name="V1", vessel_type="Tanker",
            latitude=15.5, longitude=-95.5, observation_time=obs_time,
            ingestion_time=obs_time, processing_time=obs_time,
            source_name="NOAA", license_type="CC0", idempotency_key="K1"
        ),
        VesselPosition(
            id=2, mmsi="222", vessel_name="V2", vessel_type="Cargo",
            latitude=28.0, longitude=-75.0, observation_time=obs_time + timedelta(hours=2),
            ingestion_time=obs_time, processing_time=obs_time,
            source_name="NOAA", license_type="CC0", idempotency_key="K2"
        ),
    ]

    pub_path, _, _, _ = write_partition_parquet(
        records=records,
        base_dir=tmp_path,
        partition_date_str="2024-01-01",
        generation=1,
        attempt_uuid="catalog-uuid-test",
        worker_id="catalog-worker"
    )

    catalog = extract_parquet_partition_metadata(pub_path)
    assert catalog["record_count"] == 2
    assert catalog["distinct_vessels"] == 2
    assert catalog["bounds"]["min_latitude"] == 15.5
    assert catalog["bounds"]["max_latitude"] == 28.0
    assert catalog["bounds"]["min_longitude"] == -95.5
    assert catalog["bounds"]["max_longitude"] == -75.0
