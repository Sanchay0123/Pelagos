"""Focused tests for ingestion pipeline, persistence, deduplication, and quarantine."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.database import Base
from pelagos.models import VesselPosition, QuarantinedRecord
from pelagos.ingestion.service import IngestionService
from pelagos.ingestion.marinecadastre import load_genuine_sample_records


@pytest.fixture
def db_session():
    """Isolated in-memory SQLite session for tests."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_ingest_valid_record(db_session):
    service = IngestionService(db_session)
    records = [
        {
            "mmsi": "367999888",
            "imo": "9876543",
            "vessel_name": "STAR CARRIER",
            "vessel_type": "Bulk Carrier",
            "latitude": 28.5,
            "longitude": -89.5,
            "speed_knots": 14.2,
            "course_degrees": 120.0,
            "heading_degrees": 121.0,
            "draft_meters": 12.5,
            "observation_time": "2024-01-01T10:00:00Z",
            "source_name": "NOAA MarineCadastre / USCG NAIS",
            "license": "CC0 1.0 Universal",
        }
    ]
    res = service.ingest_batch(records)
    assert res.accepted_count == 1
    assert res.quarantined_count == 0
    assert res.duplicate_count == 0

    # Verify database persistence
    saved = db_session.query(VesselPosition).filter(VesselPosition.mmsi == "367999888").first()
    assert saved is not None
    assert saved.vessel_name == "STAR CARRIER"
    assert saved.speed_knots == 14.2
    assert saved.latitude == 28.5
    assert saved.idempotency_key is not None


def test_idempotent_reingestion(db_session):
    """REQ-NFR-ARG-003: Idempotent processing and deduplication."""
    service = IngestionService(db_session)
    record = {
        "mmsi": "367999888",
        "vessel_name": "STAR CARRIER",
        "latitude": 28.5,
        "longitude": -89.5,
        "observation_time": "2024-01-01T10:00:00Z",
        "source_name": "NOAA MarineCadastre / USCG NAIS",
        "license": "CC0 1.0 Universal",
    }
    # First ingestion
    res1 = service.ingest_batch([record])
    assert res1.accepted_count == 1
    assert res1.duplicate_count == 0

    # Second ingestion of identical record
    res2 = service.ingest_batch([record])
    assert res2.accepted_count == 0
    assert res2.duplicate_count == 1

    # Database must still have exactly 1 record
    count = db_session.query(VesselPosition).filter(VesselPosition.mmsi == "367999888").count()
    assert count == 1


def test_quarantine_routing_on_invalid_data(db_session):
    """REQ-FR-DII-004: Routing anomalous observations to quarantine."""
    service = IngestionService(db_session)
    records = [
        # Invalid speed
        {
            "mmsi": "367111222",
            "vessel_name": "SPEEDY",
            "latitude": 30.0,
            "longitude": -90.0,
            "speed_knots": 99.0,
            "observation_time": "2024-01-01T10:00:00Z",
        },
        # Invalid coordinates
        {
            "mmsi": "367333444",
            "vessel_name": "LOST SHIP",
            "latitude": 110.0,
            "longitude": -90.0,
            "observation_time": "2024-01-01T10:00:00Z",
        }
    ]
    res = service.ingest_batch(records)
    assert res.accepted_count == 0
    assert res.quarantined_count == 2
    assert len(res.quarantined_records) == 2

    # Check quarantine table
    quarantined = db_session.query(QuarantinedRecord).all()
    assert len(quarantined) == 2
    error_codes = {q.error_code for q in quarantined}
    assert "ANOMALY_KINEMATIC_OUTLIER" in error_codes
    assert "ANOMALY_INVALID_COORDINATES" in error_codes


def test_load_genuine_marinecadastre_sample(db_session):
    """Verify loading real-world curated observations from NOAA."""
    service = IngestionService(db_session)
    sample_records = load_genuine_sample_records()
    assert len(sample_records) >= 50

    res = service.ingest_batch(sample_records)
    assert res.accepted_count == len(sample_records)
    assert res.quarantined_count == 0

    # Ensure zero synthetic data
    vessels = db_session.query(VesselPosition).all()
    assert len(vessels) == len(sample_records)
    for v in vessels:
        assert v.source_name == "NOAA MarineCadastre / USCG NAIS"
        assert "Public Domain" in v.license_type or "17 U.S.C." in v.license_type
        assert -90 <= v.latitude <= 90
        assert -180 <= v.longitude <= 180
