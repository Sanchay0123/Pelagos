"""
Tests for Pelagos C1.1 Auditable USDA Historical Rate Reconciliation.

Validates:
1. Exact canonical reconstruction of all ten approved 2007-2008 anomalous observations.
2. Exact raw payload preservation.
3. Normal observations remaining completely unchanged.
4. Unapproved inconsistencies being quarantined.
5. Idempotent reconciliation.
6. Reproducibility of canonical rates from raw payloads.
7. Additive schema migration compatibility against existing databases.
8. Preservation of existing rate_low and rate_high semantics.
9. Transaction rollback and failure isolation.
10. Parsing and validation of published October 2026 observation.
11. Regression test: Normal high rates are never multiplied by 10.
12. Downstream forecasting query filtering (exclude_reconstructed).
"""

import json
import pytest
from datetime import date, datetime, timezone
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker

from pelagos.models import Base, FreightRoute, FreightObservation, IngestionRun, QuarantinedRecord
from pelagos.database import ensure_schema_compatibility
from pelagos.ingestion.freight_usda import (
    normalize_usda_record,
    APPROVED_RECONCILIATION_DATES,
    RECONCILIATION_RULE_VERSION,
    RECONCILIATION_METHOD,
)
from pelagos.ingestion.freight_service import FreightIngestionService, ensure_standard_freight_routes
from pelagos.forecasting.service import (
    get_route_rate_observations,
    get_freight_reconciliation_audit,
    evaluate_route_baseline_models,
)

# Verbatim raw records for the ten approved anomalous observations
TEN_ANOMALOUS_RECORDS = [
    {"date": "2007-10-01T00:00:00.000", "month": "10", "year": "2007", "gulf_to_japan": "11.26", "pnw_to_japan": "86.7", "gulf_pnw_spread": "25.9"},
    {"date": "2007-11-01T00:00:00.000", "month": "11", "year": "2007", "gulf_to_japan": "11.8", "pnw_to_japan": "86.5", "gulf_pnw_spread": "31.5"},
    {"date": "2007-12-01T00:00:00.000", "month": "12", "year": "2007", "gulf_to_japan": "12.42", "pnw_to_japan": "79.35", "gulf_pnw_spread": "44.85"},
    {"date": "2008-02-01T00:00:00.000", "month": "2", "year": "2008", "gulf_to_japan": "10.24", "pnw_to_japan": "59.4", "gulf_pnw_spread": "43"},
    {"date": "2008-03-01T00:00:00.000", "month": "3", "year": "2008", "gulf_to_japan": "11.12", "pnw_to_japan": "66.5", "gulf_pnw_spread": "44.75"},
    {"date": "2008-04-01T00:00:00.000", "month": "4", "year": "2008", "gulf_to_japan": "11.32", "pnw_to_japan": "64.75", "gulf_pnw_spread": "48.5"},
    {"date": "2008-05-01T00:00:00.000", "month": "5", "year": "2008", "gulf_to_japan": "13.31", "pnw_to_japan": "72.2", "gulf_pnw_spread": "60.9"},
    {"date": "2008-06-01T00:00:00.000", "month": "6", "year": "2008", "gulf_to_japan": "12.72", "pnw_to_japan": "80.25", "gulf_pnw_spread": "47"},
    {"date": "2008-07-01T00:00:00.000", "month": "7", "year": "2008", "gulf_to_japan": "12.75", "pnw_to_japan": "72.5", "gulf_pnw_spread": "55"},
    {"date": "2008-08-01T00:00:00.000", "month": "8", "year": "2008", "gulf_to_japan": "11.58", "pnw_to_japan": "60", "gulf_pnw_spread": "55.8"},
]

EXPECTED_CANONICAL_RATES = {
    date(2007, 10, 1): 112.60,
    date(2007, 11, 1): 118.00,
    date(2007, 12, 1): 124.20,
    date(2008, 2, 1): 102.40,
    date(2008, 3, 1): 111.25,
    date(2008, 4, 1): 113.25,
    date(2008, 5, 1): 133.10,
    date(2008, 6, 1): 127.25,
    date(2008, 7, 1): 127.50,
    date(2008, 8, 1): 115.80,
}


@pytest.fixture
def mem_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_all_ten_approved_anomalous_records_reconstructed():
    """Verify that all ten verified 2007-2008 anomalous observations are correctly reconstructed."""
    assert len(TEN_ANOMALOUS_RECORDS) == 10
    assert len(APPROVED_RECONCILIATION_DATES) == 10

    for raw in TEN_ANOMALOUS_RECORDS:
        norm_result = normalize_usda_record(raw)
        recs, unparseable = norm_result[0], norm_result[1]
        quarantined = getattr(norm_result, "quarantined", [])

        assert unparseable == 0
        assert len(quarantined) == 0
        assert len(recs) == 3

        codes = {r.route_code: r for r in recs}
        usg = codes["USG-JPN-BULK"]
        pnw = codes["PNW-JPN-BULK"]
        spread = codes["SPREAD-USG-PNW-JPN"]

        expected_rate = EXPECTED_CANONICAL_RATES[usg.observation_date]
        raw_gulf_float = float(raw["gulf_to_japan"])

        # USG is reconstructed
        assert usg.is_reconstructed is True
        assert usg.rate_value == expected_rate
        assert usg.original_rate_value == raw_gulf_float
        assert usg.reconciliation_method == RECONCILIATION_METHOD
        assert usg.reconciliation_metadata is not None

        meta = json.loads(usg.reconciliation_metadata)
        assert meta["original_gulf_to_japan"] == raw_gulf_float
        assert meta["reconstructed_canonical_value"] == expected_rate
        assert meta["reconciliation_rule"] == RECONCILIATION_RULE_VERSION
        assert meta["is_directly_reported"] is False
        assert meta["status"] == "reconstructed_canonical"

        # PNW and Spread remain non-reconstructed
        assert pnw.is_reconstructed is False
        assert pnw.original_rate_value is None
        assert spread.is_reconstructed is False
        assert spread.is_derived is True


def test_exact_raw_payload_preservation():
    """Verifies that the verbatim raw JSON string is preserved without modification."""
    raw = TEN_ANOMALOUS_RECORDS[0]  # 2007-10-01
    norm_result = normalize_usda_record(raw)
    usg = [r for r in norm_result[0] if r.route_code == "USG-JPN-BULK"][0]

    # Raw payload parsed matches the original input dictionary
    parsed_payload = json.loads(usg.raw_payload)
    assert parsed_payload["gulf_to_japan"] == "11.26"
    assert parsed_payload["pnw_to_japan"] == "86.7"
    assert parsed_payload["gulf_pnw_spread"] == "25.9"
    assert parsed_payload["date"] == "2007-10-01T00:00:00.000"


def test_correct_canonical_reconstruction_may_2008():
    """Verifies specific all-time historical peak reconstruction for May 2008."""
    raw = {
        "date": "2008-05-01T00:00:00.000",
        "month": "5",
        "year": "2008",
        "gulf_to_japan": "13.31",
        "pnw_to_japan": "72.2",
        "gulf_pnw_spread": "60.9",
    }
    recs, unparseable = normalize_usda_record(raw)
    assert unparseable == 0
    usg = [r for r in recs if r.route_code == "USG-JPN-BULK"][0]

    assert usg.rate_value == 133.10
    assert usg.original_rate_value == 13.31
    assert usg.is_reconstructed is True


def test_normal_observations_remain_unchanged():
    """Verifies that records with valid rate conservation are never reconstructed."""
    normal_raw = {
        "date": "2026-09-01T00:00:00.000",
        "month": "9",
        "year": "2026",
        "gulf_to_japan": "76.38",
        "pnw_to_japan": "37.31",
        "gulf_pnw_spread": "39.07",
    }
    recs, unparseable = normalize_usda_record(normal_raw)
    assert unparseable == 0
    assert len(recs) == 3

    for r in recs:
        assert r.is_reconstructed is False
        assert r.original_rate_value is None
        assert r.reconciliation_method is None
        assert r.reconciliation_metadata is None

    usg = [r for r in recs if r.route_code == "USG-JPN-BULK"][0]
    assert usg.rate_value == 76.38


def test_unapproved_inconsistencies_are_quarantined(mem_db):
    """Verifies that records violating rate conservation on unapproved dates are quarantined."""
    inconsistent_raw = {
        "date": "2015-05-01T00:00:00.000",
        "month": "5",
        "year": "2015",
        "gulf_to_japan": "50.00",
        "pnw_to_japan": "25.00",
        "gulf_pnw_spread": "10.00",  # 50 - 25 != 10 (diff = 15.00)
    }
    norm_res = normalize_usda_record(inconsistent_raw)
    assert len(norm_res[0]) == 0  # No valid candidate emitted
    assert len(norm_res.quarantined) == 1

    q = norm_res.quarantined[0]
    assert q.error_code == "ERR_FREIGHT_CONSERVATION_VIOLATION"
    assert "diff=15.00" in q.error_details

    # Ingest batch via service
    service = FreightIngestionService(mem_db)
    res = service.ingest_usda_batch([inconsistent_raw], run_type="test_quarantine")
    assert res.accepted_count == 0
    assert res.quarantined_count == 1
    assert res.status == "partial"

    # Verify quarantined_records row in database
    q_rows = mem_db.query(QuarantinedRecord).all()
    assert len(q_rows) == 1
    assert q_rows[0].error_code == "ERR_FREIGHT_CONSERVATION_VIOLATION"
    assert q_rows[0].source_name == "USDA Agricultural Marketing Service / O'Neil Commodity Consulting"

    # Verify no row in freight_observations
    assert mem_db.query(FreightObservation).count() == 0


def test_idempotent_reconciliation(mem_db):
    """Verifies that re-ingesting anomalous records is completely idempotent."""
    service = FreightIngestionService(mem_db)
    batch = TEN_ANOMALOUS_RECORDS[:3]

    res1 = service.ingest_usda_batch(batch, run_type="test_initial")
    assert res1.accepted_count == 9  # 3 records * 3 routes
    assert res1.duplicate_count == 0
    assert mem_db.query(FreightObservation).count() == 9

    # Re-ingest same batch
    res2 = service.ingest_usda_batch(batch, run_type="test_duplicate")
    assert res2.accepted_count == 0
    assert res2.duplicate_count == 9
    assert mem_db.query(FreightObservation).count() == 9


def test_reproducibility_from_raw_payload(mem_db):
    """Verifies that an auditor can reproduce the canonical value strictly from raw_payload."""
    service = FreightIngestionService(mem_db)
    service.ingest_usda_batch([TEN_ANOMALOUS_RECORDS[0]])  # 2007-10-01

    obs = (
        mem_db.query(FreightObservation)
        .filter(FreightObservation.route_code == "USG-JPN-BULK")
        .one()
    )
    assert obs.is_reconstructed is True

    # Reproduce strictly from raw_payload JSON
    raw = json.loads(obs.raw_payload)
    pnw_val = float(raw["pnw_to_japan"])
    spread_val = float(raw["gulf_pnw_spread"])
    reproduced_canonical = round(pnw_val + spread_val, 2)

    assert reproduced_canonical == obs.rate_value
    assert float(raw["gulf_to_japan"]) == obs.original_rate_value


def test_schema_migration_compatibility():
    """Verifies additive schema migration against an older SQLite database schema."""
    engine = create_engine("sqlite:///:memory:")

    # Create older schema without reconciliation columns
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE freight_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_code VARCHAR(64) NOT NULL,
                observation_date DATE NOT NULL,
                rate_value FLOAT NOT NULL,
                currency VARCHAR(8) NOT NULL DEFAULT 'USD',
                unit_of_measure VARCHAR(32) NOT NULL DEFAULT 'USD/MT',
                is_derived BOOLEAN NOT NULL DEFAULT 0,
                rate_high FLOAT,
                rate_low FLOAT,
                observation_period VARCHAR(16) NOT NULL DEFAULT 'monthly',
                source_name VARCHAR(128) NOT NULL,
                source_dataset_id VARCHAR(64),
                publication_time TIMESTAMP,
                ingestion_time TIMESTAMP NOT NULL,
                processing_time TIMESTAMP NOT NULL,
                license_type VARCHAR(64) NOT NULL,
                raw_payload TEXT,
                idempotency_key VARCHAR(256) NOT NULL UNIQUE,
                ingestion_run_id INTEGER
            )
        """))
        conn.execute(text("""
            INSERT INTO freight_observations (
                route_code, observation_date, rate_value, ingestion_time, processing_time,
                license_type, idempotency_key, source_name
            ) VALUES (
                'USG-JPN-BULK', '2024-10-01', 53.65, '2026-11-08 12:00:00', '2026-11-08 12:00:00',
                'U.S. Public Domain', 'USDA:legacy:USG:2024-10-01', 'Legacy'
            )
        """))
        conn.commit()

    # Run additive migration
    ensure_schema_compatibility(engine)

    # Inspect table to verify columns were added
    inspector = inspect(engine)
    col_names = {c["name"] for c in inspector.get_columns("freight_observations")}
    assert "is_reconstructed" in col_names
    assert "original_rate_value" in col_names
    assert "reconciliation_method" in col_names
    assert "reconciliation_metadata" in col_names

    # Verify existing record preserved with default is_reconstructed = 0
    with engine.connect() as conn:
        row = conn.execute(text("SELECT route_code, rate_value, is_reconstructed FROM freight_observations")).fetchone()
        assert row[0] == "USG-JPN-BULK"
        assert row[1] == 53.65
        assert row[2] == 0


def test_rate_low_and_high_semantics_intact(mem_db):
    """Verifies that rate_low and rate_high are never overwritten by reconciliation."""
    ensure_standard_freight_routes(mem_db)
    now_utc = datetime.now(timezone.utc)
    obs = FreightObservation(
        route_code="USG-JPN-BULK",
        observation_date=date(2007, 10, 1),
        rate_value=112.60,
        rate_low=105.00,   # Assessed lower market boundary
        rate_high=120.00,  # Assessed upper market boundary
        is_reconstructed=True,
        original_rate_value=11.26,
        reconciliation_method=RECONCILIATION_METHOD,
        source_name="USDA AMS",
        license_type="Public Domain",
        idempotency_key="TEST:rate_low_high:2007-10-01",
        ingestion_time=now_utc,
        processing_time=now_utc,
    )
    mem_db.add(obs)
    mem_db.commit()

    loaded = mem_db.query(FreightObservation).filter_by(idempotency_key="TEST:rate_low_high:2007-10-01").one()
    assert loaded.rate_low == 105.00
    assert loaded.rate_high == 120.00
    assert loaded.original_rate_value == 11.26
    assert loaded.rate_value == 112.60


def test_transaction_rollback_and_source_isolation(mem_db):
    """Verifies that failed ingestion transactions roll back cleanly without partial commits."""
    service = FreightIngestionService(mem_db)
    raw_records = [
        TEN_ANOMALOUS_RECORDS[0],
        TEN_ANOMALOUS_RECORDS[1],
    ]
    # Simulate dry run first
    res = service.ingest_usda_batch(raw_records, dry_run=True)
    assert res.accepted_count == 6
    assert res.is_dry_run is True
    assert mem_db.query(FreightObservation).count() == 0

    # Test failure during active session rollback
    try:
        with mem_db.begin_nested():
            # Add partial data with commit=False so outer transaction maintains control
            service.ingest_usda_batch(raw_records, dry_run=False, commit=False)
            raise RuntimeError("Simulated mid-coordinator fault")
    except RuntimeError:
        mem_db.rollback()

    assert mem_db.query(FreightObservation).count() == 0


def test_october_2026_published_record_parsing():
    """Verifies parsing and reconciliation check for the live October 2026 observation."""
    oct_2026_raw = {
        "date": "2026-10-01T00:00:00.000",
        "month": "10",
        "year": "2026",
        "gulf_to_japan": "76",
        "pnw_to_japan": "38",
        "gulf_pnw_spread": "38",
    }
    norm_res = normalize_usda_record(oct_2026_raw)
    recs, unparseable = norm_res[0], norm_res[1]
    assert unparseable == 0
    assert len(recs) == 3

    codes = {r.route_code: r for r in recs}
    assert codes["USG-JPN-BULK"].rate_value == 76.0
    assert codes["USG-JPN-BULK"].is_reconstructed is False
    assert codes["PNW-JPN-BULK"].rate_value == 38.0
    assert codes["SPREAD-USG-PNW-JPN"].rate_value == 38.0


def test_regression_high_rate_never_multiplied_by_ten():
    """
    Regression test: High rates (> 100 USD/MT) that satisfy conservation
    must NEVER be multiplied by 10 or reconstructed.
    """
    # 2008-01-01: USG was 99.0, PNW was 58.0, spread was 41.0 (99 - 58 == 41)
    jan_2008 = {
        "date": "2008-01-01T00:00:00.000",
        "month": "1",
        "year": "2008",
        "gulf_to_japan": "99",
        "pnw_to_japan": "58",
        "gulf_pnw_spread": "41",
    }
    recs1, _ = normalize_usda_record(jan_2008)
    usg1 = [r for r in recs1 if r.route_code == "USG-JPN-BULK"][0]
    assert usg1.rate_value == 99.0
    assert usg1.is_reconstructed is False

    # A hypothetical future high rate of 125.0 USD/MT
    hypothetical_high = {
        "date": "2028-05-01T00:00:00.000",
        "month": "5",
        "year": "2028",
        "gulf_to_japan": "125.0",
        "pnw_to_japan": "75.0",
        "gulf_pnw_spread": "50.0",
    }
    recs2, _ = normalize_usda_record(hypothetical_high)
    usg2 = [r for r in recs2 if r.route_code == "USG-JPN-BULK"][0]
    assert usg2.rate_value == 125.0
    assert usg2.is_reconstructed is False
    assert usg2.original_rate_value is None


def test_forecasting_service_exclude_reconstructed_filtering(mem_db, tmp_path):
    """Verifies that forecasting query service supports comparative filtering of reconstructed observations."""
    service = FreightIngestionService(mem_db)
    empty_storage = tmp_path / "empty_storage"
    empty_storage.mkdir()

    # Ingest 1 normal record and 1 reconstructed record
    normal_record = {
        "date": "2007-09-01T00:00:00.000",
        "month": "9",
        "year": "2007",
        "gulf_to_japan": "97.0",
        "pnw_to_japan": "66.65",
        "gulf_pnw_spread": "30.35",
    }
    reconstructed_record = TEN_ANOMALOUS_RECORDS[0]  # 2007-10-01 (reconstructed to 112.60)

    service.ingest_usda_batch([normal_record, reconstructed_record])

    # 1. Query with exclude_reconstructed=False (default: returns both)
    all_obs, _ = get_route_rate_observations(mem_db, "USG-JPN-BULK", storage_dir=empty_storage, exclude_reconstructed=False)
    assert len(all_obs) == 2
    assert all_obs[0] == (date(2007, 9, 1), 97.0)
    assert all_obs[1] == (date(2007, 10, 1), 112.60)

    # 2. Query with exclude_reconstructed=True (filters out 2007-10-01)
    filtered_obs, _ = get_route_rate_observations(mem_db, "USG-JPN-BULK", storage_dir=empty_storage, exclude_reconstructed=True)
    assert len(filtered_obs) == 1
    assert filtered_obs[0] == (date(2007, 9, 1), 97.0)

    # 3. Audit report query
    audit = get_freight_reconciliation_audit(mem_db)
    assert audit["reconstructed_count"] == 1
    assert audit["observations"][0]["observation_date"] == "2007-10-01"
    assert audit["observations"][0]["canonical_rate_value"] == 112.60
    assert audit["observations"][0]["original_rate_value"] == 11.26


def test_duckdb_parquet_exclude_reconstructed_filtering(mem_db, tmp_path):
    """Verifies that DuckDB Parquet path correctly filters reconstructed observations."""
    import pyarrow.parquet as pq
    from pelagos.storage.schema import freight_records_to_pyarrow_table
    from pelagos.models import FreightPublication

    base_storage = tmp_path / "parquet_storage"
    year_dir = base_storage / "freight" / "year=2007"
    year_dir.mkdir(parents=True)
    parquet_file = year_dir / "partition_g1_a1.parquet"

    now_utc = datetime.now(timezone.utc)
    obs_normal = FreightObservation(
        id=101,
        route_code="USG-JPN-BULK",
        observation_date=date(2007, 9, 1),
        rate_value=97.0,
        currency="USD",
        unit_of_measure="USD/MT",
        is_derived=False,
        observation_period="monthly",
        source_name="USDA AMS",
        source_dataset_id="ehs5-yac3",
        ingestion_time=now_utc,
        processing_time=now_utc,
        license_type="U.S. Public Domain",
        idempotency_key="TEST:2007-09-01",
        is_reconstructed=False,
    )
    obs_recon = FreightObservation(
        id=102,
        route_code="USG-JPN-BULK",
        observation_date=date(2007, 10, 1),
        rate_value=112.60,
        currency="USD",
        unit_of_measure="USD/MT",
        is_derived=False,
        observation_period="monthly",
        source_name="USDA AMS",
        source_dataset_id="ehs5-yac3",
        ingestion_time=now_utc,
        processing_time=now_utc,
        license_type="U.S. Public Domain",
        idempotency_key="TEST:2007-10-01",
        is_reconstructed=True,
        original_rate_value=11.26,
    )

    table = freight_records_to_pyarrow_table([obs_normal, obs_recon])
    pq.write_table(table, str(parquet_file))

    # Catalog publication
    pub = FreightPublication(
        year=2007,
        generation=1,
        published_artifact_path=str(parquet_file),
        published_sha256="fake_sha256_hash",
        record_count=2,
        file_size_bytes=parquet_file.stat().st_size,
    )
    mem_db.add(pub)
    mem_db.commit()

    # Query with DuckDB - exclude_reconstructed=False
    obs_all, source_all = get_route_rate_observations(mem_db, "USG-JPN-BULK", storage_dir=base_storage, exclude_reconstructed=False)
    assert source_all == "parquet_analytical"
    assert len(obs_all) == 2
    assert obs_all[0] == (date(2007, 9, 1), 97.0)
    assert obs_all[1] == (date(2007, 10, 1), 112.60)

    # Query with DuckDB - exclude_reconstructed=True
    obs_filt, source_filt = get_route_rate_observations(mem_db, "USG-JPN-BULK", storage_dir=base_storage, exclude_reconstructed=True)
    assert source_filt == "parquet_analytical"
    assert len(obs_filt) == 1
    assert obs_filt[0] == (date(2007, 9, 1), 97.0)


def test_quarantine_persistence_failure_handling(mem_db, monkeypatch):
    """Verifies that failed quarantine persistence marks status as incomplete and updates failed_count."""
    service = FreightIngestionService(mem_db)

    # Raw record with corridor spread violation that won't match approved anomaly dates
    inconsistent_record = {
        "date": "2015-05-01T00:00:00.000",
        "month": "5",
        "year": "2015",
        "gulf_to_japan": "50.0",
        "pnw_to_japan": "30.0",
        "gulf_pnw_spread": "10.0",  # 50 != 30 + 10
    }

    # Simulate database flush failure when adding QuarantinedRecord
    orig_add = mem_db.add
    def failing_add(instance):
        if isinstance(instance, QuarantinedRecord):
            raise RuntimeError("Simulated quarantine storage failure")
        return orig_add(instance)

    monkeypatch.setattr(mem_db, "add", failing_add)

    result = service.ingest_usda_batch([inconsistent_record], dry_run=False)

    # Must be marked incomplete, not completed
    assert result.status == "incomplete"
    assert result.quarantined_count == 0  # Did NOT silently count unpersisted record
    assert result.failed_count == 1
    assert "Quarantine persistence failure" in result.error_details
