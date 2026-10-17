"""
Deterministic unit and integration tests for the Pelagos Experimental Freight Index Engine (PEGI-USJPN).
Traces to ML-001, REQ-FR-FRF-001, and REQ-NFR-DIP-001.

Verifies:
1. Exact base period calculations (October 2024: 41.8500 USD/MT, 100.00 points, 23.60 spread).
2. Specification worked examples matching to exact precision.
3. Strict eligibility & insufficient-data fencing (missing or non-positive legs return None metrics).
4. Rejection of invalid base rate parameters.
5. Spread separation (strictly USG - PNW; never added to composite).
6. Determinism & reproducibility across runs.
7. Service layer with both DuckDB Parquet analytics and SQL relational fallback.
8. FastAPI /freight/index endpoint integration.
9. CLI freight-index subcommand formatting.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.models import Base, FreightRoute, FreightObservation, FreightPublication
from pelagos.database import get_db
from pelagos.api.main import app
from pelagos.forecasting.index_engine import (
    DEFAULT_BASE_DATE,
    DEFAULT_BASE_USG_RATE,
    DEFAULT_BASE_PNW_RATE,
    BENCHMARK_DISTANCE_USG_NM,
    BENCHMARK_DISTANCE_PNW_NM,
    INDEX_CODE,
    INDEX_NAME,
    STATUS_CALCULATED,
    STATUS_INSUFFICIENT_DATA,
    calculate_pegi_point,
    compute_pegi_series,
)
from pelagos.forecasting.service import get_freight_index
from pelagos.storage.freight_writer import write_freight_partition_parquet, commit_freight_publication
from pelagos.ingestion.freight_usda import load_usda_vessel_rates_sample, normalize_usda_record


def test_pegi_base_period_exact_verification():
    """Verifies that the October 2024 base period yields exact documented base metrics."""
    rec = calculate_pegi_point(
        observation_date=date(2024, 10, 1),
        usg_rate=53.65,
        pnw_rate=30.05,
        base_usg=53.65,
        base_pnw=30.05,
        base_date=date(2024, 10, 1),
    )

    assert rec.status == STATUS_CALCULATED
    assert rec.observation_date == date(2024, 10, 1)
    assert rec.usg_rate == 53.65
    assert rec.pnw_rate == 30.05

    # 1. Arithmetic mean rate: (53.65 + 30.05) / 2 = 41.8500
    assert rec.pegi_usd_mt == 41.8500

    # 2. Normalized index points: 0.5 * (1.0 + 1.0) * 100 = 100.00
    assert rec.pegi_points == 100.00

    # 3. Spread: 53.65 - 30.05 = 23.60
    assert rec.spread_usd_mt == 23.60

    # 4. Distance-weighted scenario: (9150 * 53.65 + 4520 * 30.05) / 13670 = 45.8466
    assert rec.pegi_distance_weighted_usd_mt == 45.8466
    assert rec.notes is None


def test_pegi_worked_examples_mathematical_precision():
    """Verifies the exact worked examples documented in specification Section 5.2."""
    examples = [
        # (Date, USG, PNW, Exp_Pegi_USD, Exp_Points, Exp_Spread, Exp_Dist)
        (date(2026, 7, 1), 68.95, 35.95, 52.4500, 124.08, 33.00, 58.0385),
        (date(2026, 8, 1), 72.94, 36.75, 54.8450, 129.13, 36.19, 60.9737),
        (date(2026, 9, 1), 76.38, 37.31, 56.8450, 133.26, 39.07, 63.4615),
    ]

    for obs_d, usg, pnw, exp_usd, exp_pts, exp_sp, exp_dist in examples:
        rec = calculate_pegi_point(
            observation_date=obs_d,
            usg_rate=usg,
            pnw_rate=pnw,
            base_usg=53.65,
            base_pnw=30.05,
        )
        assert rec.status == STATUS_CALCULATED
        assert rec.pegi_usd_mt == exp_usd, f"Mismatch on {obs_d} pegi_usd_mt: got {rec.pegi_usd_mt}, expected {exp_usd}"
        assert rec.pegi_points == exp_pts, f"Mismatch on {obs_d} pegi_points: got {rec.pegi_points}, expected {exp_pts}"
        assert rec.spread_usd_mt == exp_sp, f"Mismatch on {obs_d} spread_usd_mt: got {rec.spread_usd_mt}, expected {exp_sp}"
        assert rec.pegi_distance_weighted_usd_mt == exp_dist, f"Mismatch on {obs_d} dist: got {rec.pegi_distance_weighted_usd_mt}, expected {exp_dist}"


def test_pegi_missing_leg_enforces_insufficient_data():
    """
    Verifies REQ-NFR-DIP-001 zero-imputation policy:
    If either constituent leg is missing, the record is flagged 'insufficient_data'
    and all index values remain None. Zero synthetic interpolation.
    """
    # Case 1: USG missing
    rec1 = calculate_pegi_point(date(2025, 5, 1), usg_rate=None, pnw_rate=30.0)
    assert rec1.status == STATUS_INSUFFICIENT_DATA
    assert rec1.pegi_usd_mt is None
    assert rec1.pegi_points is None
    assert rec1.spread_usd_mt is None
    assert rec1.pegi_distance_weighted_usd_mt is None
    assert "Zero imputation applied" in rec1.notes

    # Case 2: PNW missing
    rec2 = calculate_pegi_point(date(2025, 5, 1), usg_rate=50.0, pnw_rate=None)
    assert rec2.status == STATUS_INSUFFICIENT_DATA
    assert rec2.pegi_usd_mt is None
    assert rec2.pegi_points is None
    assert rec2.spread_usd_mt is None
    assert rec2.pegi_distance_weighted_usd_mt is None

    # Case 3: Both missing
    rec3 = calculate_pegi_point(date(2025, 5, 1), usg_rate=None, pnw_rate=None)
    assert rec3.status == STATUS_INSUFFICIENT_DATA
    assert rec3.pegi_usd_mt is None


def test_pegi_non_positive_rate_enforces_insufficient_data():
    """Non-positive or anomalous zero rates trigger insufficient_data status."""
    rec_zero = calculate_pegi_point(date(2025, 5, 1), usg_rate=0.0, pnw_rate=30.0)
    assert rec_zero.status == STATUS_INSUFFICIENT_DATA
    assert rec_zero.pegi_usd_mt is None

    rec_neg = calculate_pegi_point(date(2025, 5, 1), usg_rate=50.0, pnw_rate=-10.0)
    assert rec_neg.status == STATUS_INSUFFICIENT_DATA
    assert rec_neg.pegi_usd_mt is None


def test_pegi_invalid_base_rates_raise_value_error():
    """Verifies that non-positive base rates are rejected as invalid configuration."""
    with pytest.raises(ValueError, match="Base rates must be strictly positive"):
        calculate_pegi_point(date(2025, 5, 1), usg_rate=50.0, pnw_rate=30.0, base_usg=0.0)

    with pytest.raises(ValueError, match="Base rates must be strictly positive"):
        calculate_pegi_point(date(2025, 5, 1), usg_rate=50.0, pnw_rate=30.0, base_pnw=-5.0)


def test_pegi_spread_treatment_strict_separation():
    """
    Verifies that spread is strictly USG - PNW and is NEVER added to the composite index.
    Confirms collinear double-counting is prevented.
    """
    rec = calculate_pegi_point(date(2026, 1, 1), usg_rate=60.0, pnw_rate=30.0, base_usg=60.0, base_pnw=30.0)
    assert rec.spread_usd_mt == 30.0  # 60 - 30
    assert rec.pegi_usd_mt == 45.0   # (60 + 30) / 2

    # Verify that pegi_usd_mt is strictly the mean, not mean + spread
    assert rec.pegi_usd_mt != (60.0 + 30.0 + 30.0) / 2.0


def test_pegi_series_from_fixture_determinism():
    """Computes full series from the 24-month fixture, verifying determinism across repeated runs."""
    raw_fixture = load_usda_vessel_rates_sample()

    monthly_inputs = []
    for raw in raw_fixture:
        obs_d = date.fromisoformat(raw["date"].split("T")[0])
        monthly_inputs.append({
            "observation_date": obs_d,
            "usg_rate": float(raw["gulf_to_japan"]),
            "pnw_rate": float(raw["pnw_to_japan"]),
        })

    series1 = compute_pegi_series(monthly_inputs)
    series2 = compute_pegi_series(monthly_inputs)

    assert len(series1) == 24
    assert len(series2) == 24
    assert all(r.status == STATUS_CALCULATED for r in series1)

    # Base record in series matches October 2024
    base_rec = next(r for r in series1 if r.observation_date == date(2024, 10, 1))
    assert base_rec.pegi_usd_mt == 41.8500
    assert base_rec.pegi_points == 100.00

    # Verify 100% determinism between runs
    for r1, r2 in zip(series1, series2):
        assert r1.observation_date == r2.observation_date
        assert r1.pegi_usd_mt == r2.pegi_usd_mt
        assert r1.pegi_points == r2.pegi_points
        assert r1.spread_usd_mt == r2.spread_usd_mt
        assert r1.pegi_distance_weighted_usd_mt == r2.pegi_distance_weighted_usd_mt


@pytest.fixture
def test_freight_db(tmp_path):
    """Provides isolated SQLite database populated with fixture observations."""
    db_path = tmp_path / "test_freight_index.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Ingest 24 fixture records
    raw_fixture = load_usda_vessel_rates_sample()
    for raw in raw_fixture:
        records, _ = normalize_usda_record(raw)
        for r in records:
            obs = FreightObservation(
                route_code=r.route_code,
                observation_date=r.observation_date,
                rate_value=r.rate_value,
                currency=r.currency,
                unit_of_measure=r.unit_of_measure,
                is_derived=r.is_derived,
                observation_period=r.observation_period,
                source_name=r.source_name,
                source_dataset_id=r.source_dataset_id,
                license_type=r.license_type,
                raw_payload=r.raw_payload,
                idempotency_key=r.idempotency_key,
            )
            session.add(obs)
    session.commit()
    yield session, engine, tmp_path
    session.close()


def test_pegi_service_sql_fallback(test_freight_db):
    """Verifies that get_freight_index functions accurately via SQL fallback when no Parquet files exist."""
    session, _, tmp_path = test_freight_db
    empty_parquet_dir = tmp_path / "empty_parquet"

    result = get_freight_index(db=session, storage_dir=empty_parquet_dir)

    assert result["source"] == "sql_relational"
    assert result["index_code"] == INDEX_CODE
    assert result["records_count"] == 24
    assert result["calculated_count"] == 24
    assert result["insufficient_data_count"] == 0

    base_obs = next(r for r in result["observations"] if r["observation_date"] == "2024-10-01")
    assert base_obs["pegi_usd_mt"] == 41.8500
    assert base_obs["pegi_points"] == 100.00


def test_pegi_service_duckdb_parquet(test_freight_db):
    """Verifies that get_freight_index operates via DuckDB over catalog-published Parquet partitions."""
    session, _, tmp_path = test_freight_db
    storage_dir = tmp_path / "parquet_storage"

    # Publish year partitions to Parquet
    for yr in [2024, 2025, 2026]:
        obs = session.query(FreightObservation).filter(
            FreightObservation.observation_date >= date(yr, 1, 1),
            FreightObservation.observation_date <= date(yr, 12, 31),
        ).all()
        if obs:
            p, count, size, sha = write_freight_partition_parquet(
                records=obs, base_dir=storage_dir, year=yr, generation=1
            )
            commit_freight_publication(
                db=session,
                year=yr,
                expected_generation=None,
                published_path=p,
                record_count=count,
                file_size_bytes=size,
                content_sha256=sha,
                snapshot_watermark_id=max(o.id for o in obs),
            )

    result = get_freight_index(db=session, storage_dir=storage_dir)

    assert result["source"] == "parquet_analytical"
    assert result["records_count"] == 24
    assert result["calculated_count"] == 24

    latest_obs = next(r for r in result["observations"] if r["observation_date"] == "2026-09-01")
    assert latest_obs["pegi_usd_mt"] == 56.8450
    assert latest_obs["pegi_points"] == 133.26
    assert latest_obs["spread_usd_mt"] == 39.07


def test_pegi_api_endpoint(test_freight_db):
    """Verifies that GET /freight/index responds with 200 OK and valid JSON index payload."""
    session, _, _ = test_freight_db

    def override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        response = client.get("/api/v1/freight/index?start_date=2026-01-01&end_date=2026-09-01")
        assert response.status_code == 200

        data = response.json()

        assert data["index_code"] == INDEX_CODE
        assert data["records_count"] == 9  # Jan to Sep 2026
        assert data["calculated_count"] == 9
        assert data["observations"][-1]["observation_date"] == "2026-09-01"
        assert data["observations"][-1]["pegi_usd_mt"] == 56.8450
        assert data["observations"][-1]["pegi_points"] == 133.26
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_pegi_cli_subcommand(test_freight_db, capsys):
    """Verifies that the freight-index CLI subcommand executes successfully in JSON and table modes."""
    session, engine, _ = test_freight_db
    from pelagos.cli import handle_freight_index

    class FakeArgs:
        command = "freight-index"
        db_url = str(engine.url)
        storage_dir = None
        since = "2026-06-01"
        limit = None
        format = "json"

    # Test JSON mode
    exit_code = handle_freight_index(FakeArgs())
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["index_code"] == INDEX_CODE
    assert payload["records_count"] == 4  # Jun, Jul, Aug, Sep 2026

    # Test Table mode
    FakeArgs.format = "table"
    exit_code2 = handle_freight_index(FakeArgs())
    assert exit_code2 == 0
    captured2 = capsys.readouterr()
    assert "PEGI-USJPN" in captured2.out
    assert "2026-09-01" in captured2.out
    assert "56.8450" in captured2.out
