"""
Tests for USDA freight adapter, FreightIngestionService, idempotency, and conservation equation.
Traces to REQ-FR-DII-003, REQ-FR-DII-005, and REQ-NFR-DIP-001.
"""

import pytest
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.models import Base, FreightRoute, FreightObservation, IngestionRun
from pelagos.ingestion.freight_usda import normalize_usda_record, load_usda_vessel_rates_sample
from pelagos.ingestion.freight_service import FreightIngestionService, ensure_standard_freight_routes


@pytest.fixture
def mem_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_usda_record_normalization():
    """Verifies that a single USDA record expands into discrete route observations."""
    raw = {
        "date": "2026-09-01T00:00:00.000",
        "month": "9",
        "year": "2026",
        "gulf_to_japan": "76.38",
        "pnw_to_japan": "37.31",
        "gulf_pnw_spread": "39.07",
    }
    recs, unparseable = normalize_usda_record(raw)
    assert unparseable == 0
    assert len(recs) == 3

    codes = {r.route_code: r for r in recs}
    assert "USG-JPN-BULK" in codes
    assert "PNW-JPN-BULK" in codes
    assert "SPREAD-USG-PNW-JPN" in codes

    assert codes["USG-JPN-BULK"].rate_value == 76.38
    assert codes["USG-JPN-BULK"].is_derived is False
    assert codes["USG-JPN-BULK"].observation_date == date(2026, 9, 1)

    assert codes["PNW-JPN-BULK"].rate_value == 37.31
    assert codes["PNW-JPN-BULK"].is_derived is False

    assert codes["SPREAD-USG-PNW-JPN"].rate_value == 39.07
    assert codes["SPREAD-USG-PNW-JPN"].is_derived is True


def test_freight_ingestion_conservation_equation(mem_db):
    """
    Verifies that batch ingestion satisfies:
    records_read == accepted + quarantined + duplicate + unparseable + failed
    while total_received distinguishes raw source records.
    """
    sample_records = load_usda_vessel_rates_sample()[:5]
    service = FreightIngestionService(mem_db)

    # Initial ingestion: 5 raw records * 3 routes each = 15 candidates
    res1 = service.ingest_usda_batch(sample_records, run_type="test_initial")
    assert res1.status == "completed"
    assert res1.total_received == 5
    assert res1.records_read == 15
    assert res1.accepted_count == 15
    assert res1.duplicate_count == 0
    assert res1.unparseable_count == 0
    assert res1.quarantined_count == 0
    assert res1.failed_count == 0

    # IngestionResult conservation invariant
    assert res1.records_read == (
        res1.accepted_count + res1.quarantined_count + res1.duplicate_count +
        res1.unparseable_count + res1.failed_count
    )

    # IngestionRun database audit invariant
    run = mem_db.query(IngestionRun).filter_by(id=res1.run_id).one()
    assert run.raw_records_received == 5
    assert run.records_read == 15
    assert run.accepted_count == 15
    assert run.records_read == (
        run.accepted_count + run.quarantined_count + run.duplicate_count +
        run.unparseable_count + run.failed_count
    )

    # Re-running ingestion must reject all duplicates without changing row count
    res2 = service.ingest_usda_batch(sample_records, run_type="test_duplicate")
    assert res2.total_received == 5
    assert res2.records_read == 15
    assert res2.accepted_count == 0
    assert res2.duplicate_count == 15
    assert res2.records_read == (
        res2.accepted_count + res2.quarantined_count + res2.duplicate_count +
        res2.unparseable_count + res2.failed_count
    )

    total_obs = mem_db.query(FreightObservation).count()
    assert total_obs == 15


def test_freight_dry_run_isolation(mem_db):
    """Verifies that dry-run mode accurately counts without persisting observations or IngestionRun audit records."""
    sample_records = load_usda_vessel_rates_sample()[:3]
    service = FreightIngestionService(mem_db)

    res = service.ingest_usda_batch(sample_records, dry_run=True)
    assert res.status == "dry_run"
    assert res.total_received == 3
    assert res.records_read == 9
    assert res.accepted_count == 9
    assert res.records_read == (
        res.accepted_count + res.quarantined_count + res.duplicate_count +
        res.unparseable_count + res.failed_count
    )

    assert mem_db.query(FreightObservation).count() == 0
    assert mem_db.query(IngestionRun).count() == 0


def test_freight_missing_route_values(mem_db):
    """Verifies that a record with missing/omitted routes expands only available routes without padding."""
    partial_record = [
        {
            "date": "2026-09-01T00:00:00.000",
            "gulf_to_japan": "76.38",
            "pnw_to_japan": "37.31",
            "gulf_pnw_spread": None,  # Omitted route
        }
    ]
    service = FreightIngestionService(mem_db)
    res = service.ingest_usda_batch(partial_record)

    assert res.total_received == 1
    assert res.records_read == 2  # Only 2 candidate observations presented
    assert res.accepted_count == 2
    assert res.unparseable_count == 0
    assert res.records_read == (
        res.accepted_count + res.quarantined_count + res.duplicate_count +
        res.unparseable_count + res.failed_count
    )

    obs = mem_db.query(FreightObservation).all()
    assert len(obs) == 2
    codes = {o.route_code for o in obs}
    assert codes == {"USG-JPN-BULK", "PNW-JPN-BULK"}


def test_freight_malformed_route_values(mem_db):
    """Verifies that an invalid rate on one route isolates only that route without discarding valid routes."""
    mixed_record = [
        {
            "date": "2026-09-01T00:00:00.000",
            "gulf_to_japan": "-10.0",  # Implausible negative rate
            "pnw_to_japan": "37.31",   # Valid rate
            "gulf_pnw_spread": "39.07", # Valid spread
        }
    ]
    service = FreightIngestionService(mem_db)
    res = service.ingest_usda_batch(mixed_record)

    assert res.total_received == 1
    assert res.records_read == 3  # 2 valid + 1 unparseable candidate
    assert res.accepted_count == 2
    assert res.unparseable_count == 1
    assert res.status == "partial"
    assert res.records_read == (
        res.accepted_count + res.quarantined_count + res.duplicate_count +
        res.unparseable_count + res.failed_count
    )

    obs = mem_db.query(FreightObservation).all()
    assert len(obs) == 2
    codes = {o.route_code for o in obs}
    assert codes == {"PNW-JPN-BULK", "SPREAD-USG-PNW-JPN"}


def test_freight_malformed_record_handling(mem_db):
    """Verifies that malformed or missing-date records are categorized as unparseable."""
    malformed = [
        {"month": "9", "gulf_to_japan": "75.0"},  # Missing date, 1 route attempted
        {"date": "not-a-date", "gulf_to_japan": "75.0", "pnw_to_japan": "35.0"},  # Invalid date, 2 routes attempted
        {"date": "2024-01-01T00:00:00.000", "gulf_to_japan": "-10.0"},  # Implausible negative rate, 1 route
    ]
    service = FreightIngestionService(mem_db)
    res = service.ingest_usda_batch(malformed)

    assert res.total_received == 3
    assert res.records_read == 4  # 1 + 2 + 1 = 4 candidate observations attempted
    assert res.accepted_count == 0
    assert res.unparseable_count == 4
    assert res.records_read == (
        res.accepted_count + res.quarantined_count + res.duplicate_count +
        res.unparseable_count + res.failed_count
    )
    assert mem_db.query(FreightObservation).count() == 0


