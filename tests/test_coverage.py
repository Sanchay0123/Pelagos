"""Focused tests for computed spatial/temporal coverage and honest boundary disclaimers."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.database import Base, init_db
from pelagos.ingestion.service import IngestionService
from pelagos.ingestion.coverage import compute_coverage_summary


@pytest.fixture
def clean_db():
    engine = create_engine("sqlite:///:memory:", echo=False)
    init_db(engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_coverage_empty_database(clean_db):
    """Empty database must return explicit empty state with null extents and clear disclaimers."""
    summary = compute_coverage_summary(clean_db)
    assert summary["status"] == "empty"
    assert summary["total_observations"] == 0
    assert summary["distinct_vessels"] == 0
    assert summary["spatial_extent"] is None
    assert summary["temporal_extent"] is None
    assert summary["partitions_present"] == []
    assert "No observations present in database" in summary["disclaimers"]["coverage_assessment"]
    assert "NOT a navigation-grade safety system" in summary["disclaimers"]["safety_disclaimer"]


def test_coverage_populated_database(clean_db):
    """Populated database calculates mathematical bounds and enforces discrete-observation disclaimer."""
    service = IngestionService(clean_db)
    records = [
        {
            "mmsi": "367000001",
            "vessel_name": "VESSEL ALPHA",
            "latitude": 25.50000,
            "longitude": -80.20000,
            "observation_time": "2024-01-01T04:00:00Z",
            "source_name": "NOAA MarineCadastre / USCG NAIS",
            "source_partition": "ais-2024-01-01.parquet",
        },
        {
            "mmsi": "367000002",
            "vessel_name": "VESSEL BETA",
            "latitude": 42.80000,
            "longitude": -70.50000,
            "observation_time": "2024-01-01T16:30:00Z",
            "source_name": "NOAA MarineCadastre / USCG NAIS",
            "source_partition": "ais-2024-01-01.parquet",
        },
    ]
    service.ingest_batch(records, source_partition="ais-2024-01-01.parquet")

    summary = compute_coverage_summary(clean_db)
    assert summary["status"] == "available"
    assert summary["total_observations"] == 2
    assert summary["distinct_vessels"] == 2

    # Spatial bounds
    spatial = summary["spatial_extent"]
    assert spatial["min_latitude"] == 25.5
    assert spatial["max_latitude"] == 42.8
    assert spatial["min_longitude"] == -80.2
    assert spatial["max_longitude"] == -70.5
    assert "Spatial bounding box represents observed ping coordinates only" in spatial["spatial_notice"]
    assert "not continuous geographic coverage" in spatial["spatial_notice"]

    # Temporal bounds
    temporal = summary["temporal_extent"]
    assert temporal["earliest_observation_utc"] == "2024-01-01T04:00:00+00:00"
    assert temporal["latest_observation_utc"] == "2024-01-01T16:30:00+00:00"
    assert temporal["observation_span_hours"] == 12.5
    assert temporal["temporal_mode"] == "historical"
    assert "purely historical" in temporal["temporal_notice"]

    # Partition presence
    assert summary["partitions_present"] == ["ais-2024-01-01.parquet"]
