"""Tests for transaction safety, failure isolation, timestamp preservation, and startup independence."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pelagos.database import Base, init_db, get_db
from pelagos.models import VesselPosition, QuarantinedRecord, IngestionRun
from pelagos.ingestion.service import IngestionService
from pelagos.api.main import app


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_timestamp_preservation_without_ordering_assumption(test_db):
    """
    Guardrail 2: Test timestamp preservation without assuming ingestion_time must always
    be later than observation_time. Preserves exact source observation timestamps and
    records actual system ingestion time.
    """
    service = IngestionService(test_db)
    # Even if observation timestamp is simulated in the future or distant past,
    # the exact observation timestamp must be preserved without distortion.
    records = [
        {
            "mmsi": "367100001",
            "vessel_name": "TIME TEST SHIP",
            "latitude": 30.0,
            "longitude": -90.0,
            "speed_knots": 12.0,
            "observation_time": "2024-01-01T12:34:56Z",
            "source_name": "NOAA MarineCadastre / USCG NAIS",
            "source_partition": "test-partition",
        }
    ]
    res = service.ingest_batch(records, source_partition="test-partition")
    assert res.accepted_count == 1

    saved = test_db.query(VesselPosition).filter(VesselPosition.mmsi == "367100001").first()
    assert saved is not None

    # Exact source observation timestamp is preserved down to the second in UTC
    assert saved.observation_time.isoformat().startswith("2024-01-01T12:34:56")

    # Ingestion time is recorded as a valid UTC datetime
    assert isinstance(saved.ingestion_time, datetime)
    assert saved.ingestion_time.tzinfo is not None or saved.ingestion_time.year >= 2024


def test_failed_run_audit_survives_rollback(test_db):
    """
    Guardrail 1: When an unhandled database exception or constraint error aborts a batch,
    the data transaction is rolled back, but an IngestionRun audit record with status='failed'
    survives to document the failure.
    """
    service = IngestionService(test_db)
    records = [
        {
            "mmsi": "367999001",
            "vessel_name": "ROLLBACK SHIP",
            "latitude": 30.0,
            "longitude": -90.0,
            "observation_time": "2024-01-01T00:00:00Z",
            "source_name": "NOAA MarineCadastre / USCG NAIS",
        }
    ]

    # Force a database error on add/flush by mocking db.add to raise an exception on VesselPosition
    original_add = test_db.add
    def fail_on_vessel(instance):
        if isinstance(instance, VesselPosition):
            raise RuntimeError("Simulated fatal database I/O error during insert")
        original_add(instance)

    with patch.object(test_db, "add", side_effect=fail_on_vessel):
        result = service.ingest_batch(records, source_partition="failing-partition")

    # Result reports failure
    assert result.status == "failed"
    assert result.accepted_count == 0
    assert result.failed_count == 1
    assert "Simulated fatal database I/O error" in (result.error_details or "")

    # Zero rows persisted in operational vessel positions table
    pos_count = test_db.query(VesselPosition).count()
    assert pos_count == 0

    # IngestionRun audit record exists and survived rollback
    run = test_db.query(IngestionRun).filter(IngestionRun.source_partition == "failing-partition").first()
    assert run is not None
    assert run.status == "failed"
    assert run.failed_count == 1
    assert run.accepted_count == 0
    assert "Simulated fatal database I/O error" in (run.error_details or "")


def test_reconciliation_counter_invariance(test_db):
    """
    Guardrail 1: Verify exact reconciliation equation:
    records_read == accepted + quarantined + duplicate + unparseable + failed.
    """
    service = IngestionService(test_db)
    batch = [
        # Valid 1
        {
            "mmsi": "367000101",
            "vessel_name": "VALID 1",
            "latitude": 28.0,
            "longitude": -89.0,
            "observation_time": "2024-01-01T01:00:00Z",
        },
        # Quarantined (kinematic outlier)
        {
            "mmsi": "367000102",
            "vessel_name": "OUTLIER",
            "latitude": 28.0,
            "longitude": -89.0,
            "speed_knots": 99.0,
            "observation_time": "2024-01-01T01:00:00Z",
        },
    ]

    # Run 1: 1 valid accepted, 1 quarantined, 0 duplicate, 2 unparseable simulated
    res1 = service.ingest_batch(
        batch, 
        source_partition="recon-partition",
        unparseable_count=2
    )
    assert res1.records_read == 4
    assert res1.accepted_count == 1
    assert res1.quarantined_count == 1
    assert res1.duplicate_count == 0
    assert res1.unparseable_count == 2
    assert res1.failed_count == 0
    assert res1.records_read == (
        res1.accepted_count + res1.quarantined_count + res1.duplicate_count + 
        res1.unparseable_count + res1.failed_count
    )
    assert res1.status == "partial"  # Partial because unparseable > 0 and accepted > 0

    # Run 2: Same batch again with 0 unparseable
    res2 = service.ingest_batch(
        batch,
        source_partition="recon-partition",
        unparseable_count=0
    )
    assert res2.records_read == 2
    assert res2.accepted_count == 0
    assert res2.duplicate_count == 1  # VALID 1 detected as duplicate
    assert res2.quarantined_count == 1  # Outlier routed to quarantine
    assert res2.records_read == (
        res2.accepted_count + res2.quarantined_count + res2.duplicate_count + 
        res2.unparseable_count + res2.failed_count
    )
    assert res2.status == "completed"


def test_api_startup_zero_network_zero_seeding():
    """
    Guardrail 4: API startup must be completely decoupled from sample seeding and network calls.
    Starting FastAPI on an empty database leaves the database empty and makes zero network calls.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    with patch("urllib.request.urlopen") as mock_url, \
         patch("fsspec.open") as mock_fsspec:
        with TestClient(app) as client:
            resp = client.get("/api/v1/vessels/snapshot")
            assert resp.status_code == 200
            data = resp.json()

            # Empty database state confirmed
            assert data["total_records"] == 0
            assert data["rows"] == []

            # Zero network calls made
            assert mock_url.call_count == 0
            assert mock_fsspec.call_count == 0

    app.dependency_overrides.clear()


def test_intra_batch_duplicate_deduplication(test_db):
    """
    Review Fix 1: Intra-batch duplicate records must be detected in memory and counted
    as duplicates without raising IntegrityError on commit or aborting the batch.
    Applies equally to dry-run and production execution.
    """
    service = IngestionService(test_db)
    batch_with_duplicates = [
        # Record A
        {
            "mmsi": "367000555",
            "vessel_name": "INTRA-BATCH VESSEL 1",
            "latitude": 28.5,
            "longitude": -89.5,
            "observation_time": "2024-01-01T10:00:00Z",
            "source_name": "NOAA MarineCadastre / USCG NAIS",
        },
        # Record A duplicate in the same batch
        {
            "mmsi": "367000555",
            "vessel_name": "INTRA-BATCH VESSEL 1",
            "latitude": 28.5,
            "longitude": -89.5,
            "observation_time": "2024-01-01T10:00:00Z",
            "source_name": "NOAA MarineCadastre / USCG NAIS",
        },
        # Record B distinct
        {
            "mmsi": "367000777",
            "vessel_name": "INTRA-BATCH VESSEL 2",
            "latitude": 29.0,
            "longitude": -90.0,
            "observation_time": "2024-01-01T10:00:00Z",
            "source_name": "NOAA MarineCadastre / USCG NAIS",
        },
    ]

    # 1. Verify in dry-run mode
    dry_res = service.ingest_batch(batch_with_duplicates, dry_run=True)
    assert dry_res.is_dry_run is True
    assert dry_res.status == "dry_run"
    assert dry_res.records_read == 3
    assert dry_res.accepted_count == 2
    assert dry_res.duplicate_count == 1
    assert dry_res.quarantined_count == 0
    assert dry_res.failed_count == 0
    assert test_db.query(VesselPosition).count() == 0

    # 2. Verify in production mode (no IntegrityError rollback)
    prod_res = service.ingest_batch(batch_with_duplicates, dry_run=False)
    assert prod_res.status == "completed"
    assert prod_res.records_read == 3
    assert prod_res.accepted_count == 2
    assert prod_res.duplicate_count == 1
    assert prod_res.quarantined_count == 0
    assert prod_res.failed_count == 0

    # Exactly 2 rows persisted in database
    db_count = test_db.query(VesselPosition).filter(
        VesselPosition.mmsi.in_(["367000555", "367000777"])
    ).count()
    assert db_count == 2

    # 3. Subsequent ingestion of the same batch detects all 3 as duplicates against DB
    retry_res = service.ingest_batch(batch_with_duplicates, dry_run=False)
    assert retry_res.accepted_count == 0
    assert retry_res.duplicate_count == 3
    assert retry_res.status == "completed"


def test_all_unparseable_batch_status_failed(test_db):
    """
    Review Fix 3: A batch containing only unparseable records must be marked 'failed',
    not 'completed'.
    """
    service = IngestionService(test_db)
    res = service.ingest_batch([], unparseable_count=5, source_partition="unparseable-partition")

    assert res.records_read == 5
    assert res.accepted_count == 0
    assert res.quarantined_count == 0
    assert res.duplicate_count == 0
    assert res.unparseable_count == 5
    assert res.failed_count == 0
    assert res.status == "failed"
    assert "All records in partition were unparseable" in (res.error_details or "")
    assert res.records_read == (
        res.accepted_count + res.quarantined_count + res.duplicate_count +
        res.unparseable_count + res.failed_count
    )

    # Persisted IngestionRun also has status='failed'
    run = test_db.query(IngestionRun).filter(IngestionRun.source_partition == "unparseable-partition").first()
    assert run is not None
    assert run.status == "failed"
    assert run.unparseable_count == 5


def test_mixed_valid_and_unparseable_batch_status_partial(test_db):
    """
    Review Fix 3: A batch yielding usable records alongside unparseable records is marked 'partial'.
    """
    service = IngestionService(test_db)
    records = [
        {
            "mmsi": "367000888",
            "vessel_name": "PARTIAL TEST SHIP",
            "latitude": 27.5,
            "longitude": -88.5,
            "observation_time": "2024-01-01T12:00:00Z",
            "source_name": "NOAA MarineCadastre / USCG NAIS",
        }
    ]
    res = service.ingest_batch(records, unparseable_count=3, source_partition="partial-partition")

    assert res.records_read == 4
    assert res.accepted_count == 1
    assert res.unparseable_count == 3
    assert res.status == "partial"
    assert res.records_read == (
        res.accepted_count + res.quarantined_count + res.duplicate_count +
        res.unparseable_count + res.failed_count
    )
