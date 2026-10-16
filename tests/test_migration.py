"""Tests for fresh and additive database schema migration."""

import sqlite3
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from pelagos.database import Base, init_db, ensure_schema_compatibility
from pelagos.models import VesselPosition, IngestionRun


def test_fresh_database_initialization(tmp_path):
    """Calling init_db on a clean engine creates all tables with all governed columns."""
    db_file = tmp_path / "fresh_test.db"
    engine = create_engine(f"sqlite:///{db_file}")

    init_db(engine)

    inspector = inspect(engine)
    tables = inspector.get_table_names()
    assert "vessel_positions" in tables
    assert "quarantined_records" in tables
    assert "ingestion_runs" in tables

    v_cols = {c["name"] for c in inspector.get_columns("vessel_positions")}
    assert "source_partition" in v_cols
    assert "ingestion_run_id" in v_cols

    q_cols = {c["name"] for c in inspector.get_columns("quarantined_records")}
    assert "source_partition" in q_cols
    assert "ingestion_run_id" in q_cols
    assert "mmsi" in q_cols
    assert "raw_record_id" in q_cols

    r_cols = {c["name"] for c in inspector.get_columns("ingestion_runs")}
    assert "raw_records_received" in r_cols
    assert "records_read" in r_cols
    assert "accepted_count" in r_cols
    assert "quarantined_count" in r_cols
    assert "duplicate_count" in r_cols
    assert "unparseable_count" in r_cols
    assert "failed_count" in r_cols
    assert "status" in r_cols

    assert "freight_routes" in tables
    assert "freight_observations" in tables
    assert "freight_publications" in tables


def test_existing_database_additive_migration(tmp_path):
    """
    Existing databases lacking D5 columns must be migrated additively and non-destructively
    without wiping pre-existing records.
    """
    db_file = tmp_path / "legacy_test.db"

    # 1. Create a legacy database using raw sqlite3 without D5 columns
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE vessel_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mmsi VARCHAR(32) NOT NULL,
            imo VARCHAR(32),
            vessel_name VARCHAR(128) NOT NULL,
            vessel_type VARCHAR(64) NOT NULL,
            vessel_type_code INTEGER,
            latitude FLOAT NOT NULL,
            longitude FLOAT NOT NULL,
            speed_knots FLOAT,
            course_degrees FLOAT,
            heading_degrees FLOAT,
            draft_meters FLOAT,
            length_meters FLOAT,
            width_meters FLOAT,
            destination VARCHAR(128),
            navigational_status VARCHAR(64),
            observation_time DATETIME NOT NULL,
            publication_time DATETIME,
            ingestion_time DATETIME NOT NULL,
            processing_time DATETIME NOT NULL,
            source_name VARCHAR(128) NOT NULL,
            license_type VARCHAR(64) NOT NULL,
            raw_record_id VARCHAR(128),
            idempotency_key VARCHAR(256) UNIQUE NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO vessel_positions (
            mmsi, vessel_name, vessel_type, latitude, longitude,
            observation_time, ingestion_time, processing_time,
            source_name, license_type, idempotency_key
        ) VALUES (
            '367123456', 'LEGACY SHIP', 'Tanker', 29.5, -94.2,
            '2024-01-01 00:00:00', '2024-01-01 00:00:05', '2024-01-01 00:00:06',
            'NOAA MarineCadastre / USCG NAIS', 'U.S. Public Domain', 'legacy:367123456:2024'
        )
    """)
    conn.execute("""
        CREATE TABLE quarantined_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name VARCHAR(128) NOT NULL,
            error_code VARCHAR(64) NOT NULL,
            error_details TEXT NOT NULL,
            raw_payload TEXT NOT NULL,
            ingestion_time DATETIME NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE ingestion_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type VARCHAR(32) NOT NULL,
            source_name VARCHAR(128) NOT NULL,
            source_partition VARCHAR(128) NOT NULL,
            records_read INTEGER NOT NULL,
            accepted_count INTEGER NOT NULL,
            quarantined_count INTEGER NOT NULL,
            duplicate_count INTEGER NOT NULL,
            unparseable_count INTEGER NOT NULL,
            failed_count INTEGER NOT NULL,
            status VARCHAR(32) NOT NULL,
            started_at DATETIME NOT NULL,
            completed_at DATETIME NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO ingestion_runs (
            run_type, source_name, source_partition, records_read, accepted_count,
            quarantined_count, duplicate_count, unparseable_count, failed_count,
            status, started_at, completed_at
        ) VALUES (
            'bundled', 'NOAA', 'legacy-part', 10, 10, 0, 0, 0, 0, 'completed',
            '2024-01-01 00:00:00', '2024-01-01 00:00:05'
        )
    """)
    conn.commit()
    conn.close()

    # 2. Run init_db with the legacy database
    engine = create_engine(f"sqlite:///{db_file}")
    init_db(engine)

    # 3. Verify columns were added additively and pre-existing row survived
    inspector = inspect(engine)
    v_cols = {c["name"] for c in inspector.get_columns("vessel_positions")}
    assert "source_partition" in v_cols
    assert "ingestion_run_id" in v_cols

    q_cols = {c["name"] for c in inspector.get_columns("quarantined_records")}
    assert "source_partition" in q_cols
    assert "ingestion_run_id" in q_cols
    assert "mmsi" in q_cols
    assert "raw_record_id" in q_cols

    r_cols = {c["name"] for c in inspector.get_columns("ingestion_runs")}
    assert "raw_records_received" in r_cols  # Column added additively

    tables = inspector.get_table_names()
    assert "ingestion_runs" in tables
    assert "freight_publications" in tables
    f_cols = {c["name"] for c in inspector.get_columns("freight_publications")}
    assert "snapshot_watermark_id" in f_cols

    # 4. Verify indexes were created on added columns
    v_indexes = {idx["name"] for idx in inspector.get_indexes("vessel_positions")}
    assert "ix_vessel_positions_source_partition" in v_indexes
    assert "ix_vessel_positions_ingestion_run_id" in v_indexes

    q_indexes = {idx["name"] for idx in inspector.get_indexes("quarantined_records")}
    assert "ix_quarantined_records_source_partition" in q_indexes
    assert "ix_quarantined_records_ingestion_run_id" in q_indexes
    assert "ix_quarantined_records_mmsi" in q_indexes

    # Verify existing records were preserved intact
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()
    existing_ship = session.query(VesselPosition).filter(VesselPosition.mmsi == "367123456").first()
    assert existing_ship is not None
    assert existing_ship.vessel_name == "LEGACY SHIP"
    assert existing_ship.source_partition is None  # Added column initialized to NULL

    existing_run = session.query(IngestionRun).filter(IngestionRun.source_partition == "legacy-part").first()
    assert existing_run is not None
    assert existing_run.records_read == 10
    assert existing_run.raw_records_received is None  # Added column safely null for legacy records
    session.close()
