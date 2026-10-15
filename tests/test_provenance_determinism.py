"""Tests for subsecond timestamp precision, cross-environment determinism, and savepoint ORM lifecycle."""

import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError, InvalidRequestError

from pelagos.models import Base, VesselPosition
from pelagos.ingestion.schemas import TelemetryRecord
from pelagos.ingestion.service import IngestionService, _safe_evict_failed_orm_object, is_idempotency_key_violation
from pelagos.ingestion.validator import validate_telemetry_record, parse_and_verify_timestamp


@pytest.fixture
def mem_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_microsecond_idempotency_key_precision(mem_db):
    """
    Verifies that idempotency key format preserves full microsecond precision (.ffffffZ)
    and distinguishes records arriving in the same second.
    """
    service = IngestionService(mem_db)

    t1 = datetime(2024, 1, 1, 14, 30, 15, 123456, tzinfo=timezone.utc)
    t2 = datetime(2024, 1, 1, 14, 30, 15, 654321, tzinfo=timezone.utc)

    rec1 = TelemetryRecord(
        mmsi="367412340", vessel_name="Vessel A", vessel_type="Tanker",
        latitude=28.5, longitude=-94.5, observation_time=t1,
        source_name="NOAA MarineCadastre / USCG NAIS", license="CC0"
    )
    rec2 = TelemetryRecord(
        mmsi="367412340", vessel_name="Vessel A", vessel_type="Tanker",
        latitude=28.5, longitude=-94.5, observation_time=t2,
        source_name="NOAA MarineCadastre / USCG NAIS", license="CC0"
    )

    key1 = service.generate_idempotency_key(rec1)
    key2 = service.generate_idempotency_key(rec2)

    assert key1 == "NOAA MarineCadastre / USCG NAIS:367412340:2024-01-01T14:30:15.123456Z"
    assert key2 == "NOAA MarineCadastre / USCG NAIS:367412340:2024-01-01T14:30:15.654321Z"
    assert key1 != key2, "Keys within same second with different microseconds must be distinct"


def test_cross_environment_timezone_determinism(monkeypatch):
    """
    Verifies that naive observation timestamps with guaranteed NOAA source contract
    produce identical UTC keys regardless of host operating system timezone environment.
    """
    raw_payload = {
        "mmsi": "367999888",
        "latitude": 29.5,
        "longitude": -94.2,
        "observation_time": "2024-01-01 12:00:00.500000",
        "source_name": "NOAA MarineCadastre / USCG NAIS",
    }

    # Verify under simulated UTC
    monkeypatch.setenv("TZ", "UTC")
    val_utc = validate_telemetry_record(raw_payload)
    assert val_utc.is_valid is True
    assert val_utc.record.observation_time.tzinfo == timezone.utc

    # Verify under simulated America/New_York (EST)
    monkeypatch.setenv("TZ", "America/New_York")
    val_est = validate_telemetry_record(raw_payload)
    assert val_est.is_valid is True
    assert val_est.record.observation_time.tzinfo == timezone.utc

    # Verify under simulated Asia/Kolkata (IST)
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    val_ist = validate_telemetry_record(raw_payload)
    assert val_ist.is_valid is True
    assert val_ist.record.observation_time.tzinfo == timezone.utc

    assert val_utc.record.observation_time == val_est.record.observation_time == val_ist.record.observation_time


def test_per_record_savepoint_and_orm_eviction(mem_db):
    """
    Verifies that per-record savepoints isolate duplicate-key violations,
    safely evicts failed ORM state, and allows non-conflicting records to commit.
    """
    service = IngestionService(mem_db)

    # Ingest baseline record
    rec1 = {
        "mmsi": "367111000",
        "vessel_name": "Alpha",
        "vessel_type": "Tug",
        "latitude": 29.0,
        "longitude": -94.0,
        "observation_time": "2024-01-01T10:00:00.000000Z",
        "source_name": "NOAA MarineCadastre"
    }
    res1 = service.ingest_batch([rec1])
    assert res1.accepted_count == 1

    # Ingest batch containing 1 duplicate and 1 novel record
    rec2_dup = dict(rec1)
    rec3_novel = {
        "mmsi": "367222000",
        "vessel_name": "Beta",
        "vessel_type": "Tug",
        "latitude": 29.1,
        "longitude": -94.1,
        "observation_time": "2024-01-01T10:05:00.000000Z",
        "source_name": "NOAA MarineCadastre"
    }

    res2 = service.ingest_batch([rec2_dup, rec3_novel])
    assert res2.duplicate_count == 1
    assert res2.accepted_count == 1
    assert res2.status == "completed"

    # Verify both records are in DB
    total = mem_db.query(VesselPosition).count()
    assert total == 2


def test_orm_eviction_already_detached_entity(mem_db):
    """
    Verifies that _safe_evict_failed_orm_object safely handles already-detached
    or unmanaged entities without raising InvalidRequestError.
    """
    obs_time = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    entity = VesselPosition(
        mmsi="999888777", vessel_name="Ghost Ship", vessel_type="Cargo",
        latitude=0.0, longitude=0.0, observation_time=obs_time,
        ingestion_time=obs_time, processing_time=obs_time,
        source_name="NOAA", license_type="CC0",
        idempotency_key="NOAA:999888777:2024-01-01T12:00:00.000000Z"
    )
    
    # Purely transient entity (never added to session)
    insp = inspect(entity, raiseerr=False)
    assert insp.session is None
    _safe_evict_failed_orm_object(mem_db, entity)  # Must not error

    # Explicitly expunged/detached entity
    mem_db.add(entity)
    mem_db.expunge(entity)
    insp2 = inspect(entity, raiseerr=False)
    assert insp2.session is None or insp2.detached is True
    _safe_evict_failed_orm_object(mem_db, entity)  # Must not error


def test_dialect_aware_integrity_classification():
    """
    Verifies that is_idempotency_key_violation distinguishes unique constraint
    collisions on idempotency_key from NOT NULL or foreign key violations.
    """
    # Simulate SQLite unique constraint error
    class MockOrigUnique:
        sqlite_errorname = "SQLITE_CONSTRAINT_UNIQUE"
        def __str__(self):
            return "UNIQUE constraint failed: vessel_positions.idempotency_key"

    class MockExcUnique(IntegrityError):
        def __init__(self):
            self.orig = MockOrigUnique()

    assert is_idempotency_key_violation(MockExcUnique(), dialect_name="sqlite") is True

    # Simulate SQLite NOT NULL error
    class MockOrigNotNull:
        sqlite_errorname = "SQLITE_CONSTRAINT_NOTNULL"
        def __str__(self):
            return "NOT NULL constraint failed: vessel_positions.latitude"

    class MockExcNotNull(IntegrityError):
        def __init__(self):
            self.orig = MockOrigNotNull()

    assert is_idempotency_key_violation(MockExcNotNull(), dialect_name="sqlite") is False

    # Simulate PostgreSQL unique constraint error
    class MockDiag:
        constraint_name = "uq_vessel_positions_idempotency_key"

    class MockOrigPgUnique:
        pgcode = "23505"
        diag = MockDiag()
        def __str__(self):
            return "duplicate key value violates unique constraint"

    class MockExcPgUnique(IntegrityError):
        def __init__(self):
            self.orig = MockOrigPgUnique()

    assert is_idempotency_key_violation(MockExcPgUnique(), dialect_name="postgresql") is True
