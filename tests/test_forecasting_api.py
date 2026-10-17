"""
Focused tests for the Pelagos read-only forecasting API interface.
Traces to REQ-FR-FRF-001, ML-001 Section 5.3 & 5.4, and REQ-NFR-DIP-001.

Verifies:
1. Valid forecast requests for both routes (USG-JPN-BULK, PNW-JPN-BULK) across /forecast and /forecast/baseline.
2. Each supported baseline model and the documented default (persistence).
3. Client error handling (HTTP 400) on invalid routes and unsupported models.
4. Strict rejection of derived route SPREAD-USG-PNW-JPN.
5. Origin and target date semantics with zero lookahead future data leakage.
6. Insufficient history response contract (status: insufficient_history, predicted_value: None).
7. Evaluation summary endpoint response contract, disclaimers, and metric accuracy.
8. Read-only database preservation.
"""

import math
from datetime import date
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, event, inspect
from sqlalchemy.orm import sessionmaker

from pelagos.models import Base, FreightObservation, FreightPublication, ParquetExportTask, IngestionRun
from pelagos.database import get_db
from pelagos.api.main import app
from pelagos.ingestion.freight_usda import load_usda_vessel_rates_sample, normalize_usda_record
from pelagos.storage.freight_writer import write_freight_partition_parquet, commit_freight_publication
from pelagos.forecasting.schemas import (
    DEFAULT_BASELINE_MODEL,
    STATUS_FORECAST_GENERATED,
    STATUS_INSUFFICIENT_HISTORY,
    FLAG_TREND_NEGATIVE_CLAMP,
)


@pytest.fixture
def forecasting_api_db(tmp_path):
    """Provides isolated SQLite database and published Parquet storage for API tests."""
    db_path = tmp_path / "test_forecasting_api.db"
    storage_dir = tmp_path / "parquet_storage"
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

    # Publish Parquet partitions for 2024, 2025, 2026
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

    yield session, engine, storage_dir
    session.close()


@pytest.fixture
def client(forecasting_api_db):
    session, _, _ = forecasting_api_db

    def override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_forecast_valid_requests_both_routes(client):
    """Verifies valid forecast requests for both direct corridors across endpoint aliases."""
    # 1. USG-JPN-BULK via /api/v1/freight/forecast
    res_usg = client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK")
    assert res_usg.status_code == 200
    data_usg = res_usg.json()
    assert data_usg["route_code"] == "USG-JPN-BULK"
    assert data_usg["model_type"] == DEFAULT_BASELINE_MODEL
    assert data_usg["default_model_applied"] is True
    assert data_usg["origin_date"] == "2026-09-01"
    assert data_usg["target_date"] == "2026-10-01"
    assert data_usg["horizon_months"] == 1
    assert data_usg["predicted_value"] == 76.38  # latest USG observation in fixture
    assert data_usg["unit_of_measure"] == "USD/MT"
    assert data_usg["status"] == STATUS_FORECAST_GENERATED

    # 2. PNW-JPN-BULK via /api/v1/freight/forecast/baseline alias
    res_pnw = client.get("/api/v1/freight/forecast/baseline?route_code=PNW-JPN-BULK")
    assert res_pnw.status_code == 200
    data_pnw = res_pnw.json()
    assert data_pnw["route_code"] == "PNW-JPN-BULK"
    assert data_pnw["predicted_value"] == 37.31  # latest PNW observation in fixture
    assert data_pnw["target_date"] == "2026-10-01"


def test_forecast_all_supported_models_and_default(client):
    """Verifies that each supported model can be explicitly requested, and default is persistence."""
    # 1. Default (omitted model_type)
    r_def = client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK")
    assert r_def.status_code == 200
    assert r_def.json()["model_type"] == "persistence"
    assert r_def.json()["default_model_applied"] is True

    # 2. Explicit persistence
    r_pers = client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK&model_type=persistence")
    assert r_pers.status_code == 200
    assert r_pers.json()["model_type"] == "persistence"
    assert r_pers.json()["default_model_applied"] is False
    assert r_pers.json()["predicted_value"] == 76.38

    # 3. Rolling MA-3: mean of Jul (68.95), Aug (72.94), Sep (76.38) = 72.7567 -> 72.76
    r_ma3 = client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK&model_type=rolling_ma_3")
    assert r_ma3.status_code == 200
    assert r_ma3.json()["model_type"] == "rolling_ma_3"
    assert r_ma3.json()["predicted_value"] == 72.7567

    # 4. Rolling MA-6
    r_ma6 = client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK&model_type=rolling_ma_6")
    assert r_ma6.status_code == 200
    assert r_ma6.json()["model_type"] == "rolling_ma_6"
    assert isinstance(r_ma6.json()["predicted_value"], float)

    # 5. Linear Trend-6
    r_tr6 = client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK&model_type=linear_trend_6")
    assert r_tr6.status_code == 200
    assert r_tr6.json()["model_type"] == "linear_trend_6"
    assert isinstance(r_tr6.json()["predicted_value"], float)


def test_forecast_input_validation_errors(client):
    """Verifies 400 Bad Request on invalid route codes, disallowed spread routes, and unknown models."""
    # 1. Derived spread route rejected
    r_spread = client.get("/api/v1/freight/forecast?route_code=SPREAD-USG-PNW-JPN")
    assert r_spread.status_code == 400
    assert "derived spread indicator" in r_spread.json()["detail"]

    # 2. Unknown route rejected
    r_unknown_route = client.get("/api/v1/freight/forecast?route_code=ATLANTIC-CAPESIZE")
    assert r_unknown_route.status_code == 400
    assert "Unsupported route code" in r_unknown_route.json()["detail"]

    # 3. Unknown model rejected
    r_unknown_model = client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK&model_type=deep_lstm_transformer")
    assert r_unknown_model.status_code == 400
    assert "Unsupported baseline model" in r_unknown_model.json()["detail"]


def test_origin_date_semantics_and_leakage_prevention(client):
    """
    Verifies that supplying origin_date uses strictly observations on or before origin,
    fencing out future data.
    """
    # Request forecast originating at 2025-06-01 (target month: 2025-07-01)
    res = client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK&origin_date=2025-06-01&model_type=persistence")
    assert res.status_code == 200
    data = res.json()
    assert data["origin_date"] == "2025-06-01"
    assert data["target_date"] == "2025-07-01"
    assert data["history_count_observed"] == 9  # Oct 2024 to Jun 2025

    # Rate at 2025-06-01 was 46.88 in fixture
    assert data["predicted_value"] == 46.88

    # Request origin before any observations exist
    res_early = client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK&origin_date=2024-01-01")
    assert res_early.status_code == 400
    assert "No observations available on or before" in res_early.json()["detail"]


def test_insufficient_history_response_contract(client):
    """
    Verifies that when history at origin is shorter than model's required window,
    the endpoint returns 200 OK with status='insufficient_history' and predicted_value=None.
    """
    # Origin 2024-11-01 has only 2 observations (Oct, Nov 2024). MA-6 requires 6.
    res = client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK&origin_date=2024-11-01&model_type=rolling_ma_6")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == STATUS_INSUFFICIENT_HISTORY
    assert data["predicted_value"] is None
    assert data["history_count_observed"] == 2
    assert "requires at least 6" in data["notes"]


def test_evaluation_summary_response_contract(client):
    """
    Verifies /freight/forecast/evaluate returns complete out-of-sample evaluation summaries,
    persistence comparative ratios, and preliminary disclaimer.
    """
    res = client.get("/api/v1/freight/forecast/evaluate?route_code=USG-JPN-BULK")
    assert res.status_code == 200
    data = res.json()

    assert data["route_code"] == "USG-JPN-BULK"
    assert data["total_observations"] == 24
    assert data["burn_in_months"] == 12
    assert data["eval_start_date"] == "2025-10-01"
    assert data["eval_end_date"] == "2026-09-01"
    assert data["evaluation_steps_count"] == 12
    assert data["status"] == "preliminary_evaluation"

    # Mandatory qualification disclaimer
    assert "Preliminary walk-forward evaluation" in data["disclaimer"]
    assert "must NOT be interpreted as the forecast value for next month" in data["disclaimer"]

    # Model metrics verification
    models = data["models"]
    assert "persistence" in models
    assert "rolling_ma_3" in models
    assert "rolling_ma_6" in models
    assert "linear_trend_6" in models

    assert models["persistence"]["mae"] == 3.14
    assert models["persistence"]["rmse"] == 3.81
    assert models["persistence"]["mape"] == 5.03
    assert models["persistence"]["relative_mae_vs_persistence"] == 1.0

    assert models["rolling_ma_3"]["mae"] == 4.39
    assert models["rolling_ma_3"]["relative_mae_vs_persistence"] == 1.4

    # Disallowed spread route
    res_sp = client.get("/api/v1/freight/forecast/evaluate?route_code=SPREAD-USG-PNW-JPN")
    assert res_sp.status_code == 400


def test_evaluation_with_step_details(client):
    """Verifies include_steps=true returns detailed step-by-step backtest comparisons."""
    res = client.get("/api/v1/freight/forecast/evaluate?route_code=PNW-JPN-BULK&include_steps=true")
    assert res.status_code == 200
    data = res.json()
    assert "steps" in data
    assert len(data["steps"]["persistence"]) == 12
    step1 = data["steps"]["persistence"][0]
    assert step1["origin_date"] == "2025-09-01"
    assert step1["target_date"] == "2025-10-01"
    assert "actual_value" in step1
    assert "predicted_value" in step1


def test_forecast_multi_step_horizons_1_to_6(client):
    """
    Verifies that horizon_months accepts 1 through 6, producing correctly advanced
    target dates across calendar year boundaries and valid direct multi-step predictions.
    Also confirms rejection of out-of-bound horizons (0 and 7).
    """
    expected_targets = {
        1: "2026-10-01",
        2: "2026-11-01",
        3: "2026-12-01",
        4: "2027-01-01",
        5: "2027-02-01",
        6: "2027-03-01",
    }

    # 1. Test Persistence across all horizons (flat multi-step baseline)
    for h, exp_target in expected_targets.items():
        res = client.get(f"/api/v1/freight/forecast?route_code=USG-JPN-BULK&model_type=persistence&horizon_months={h}")
        assert res.status_code == 200
        d = res.json()
        assert d["origin_date"] == "2026-09-01"
        assert d["target_date"] == exp_target
        assert d["horizon_months"] == h
        assert d["predicted_value"] == 76.38  # latest observation maintained flat

    # 2. Test Rolling MA-3 across all horizons (direct trailing mean projection)
    for h, exp_target in expected_targets.items():
        res = client.get(f"/api/v1/freight/forecast?route_code=USG-JPN-BULK&model_type=rolling_ma_3&horizon_months={h}")
        assert res.status_code == 200
        d = res.json()
        assert d["origin_date"] == "2026-09-01"
        assert d["target_date"] == exp_target
        assert d["horizon_months"] == h
        assert d["predicted_value"] == 72.7567

    # 3. Test Rolling MA-6 across all horizons (direct trailing mean projection)
    for h, exp_target in expected_targets.items():
        res = client.get(f"/api/v1/freight/forecast?route_code=USG-JPN-BULK&model_type=rolling_ma_6&horizon_months={h}")
        assert res.status_code == 200
        d = res.json()
        assert d["origin_date"] == "2026-09-01"
        assert d["target_date"] == exp_target
        assert d["horizon_months"] == h
        assert d["predicted_value"] == 71.0117

    # 4. Test Linear Trend-6 extrapolation slope across horizons
    expected_trend_preds = {
        1: 76.7167,
        2: 78.3467,
        3: 79.9767,
        4: 81.6067,
        5: 83.2367,
        6: 84.8667,
    }
    trend_preds = []
    for h in range(1, 7):
        res = client.get(f"/api/v1/freight/forecast?route_code=USG-JPN-BULK&model_type=linear_trend_6&horizon_months={h}")
        assert res.status_code == 200
        d = res.json()
        assert d["origin_date"] == "2026-09-01"
        assert d["target_date"] == expected_targets[h]
        assert d["horizon_months"] == h
        assert math.isclose(d["predicted_value"], expected_trend_preds[h], abs_tol=0.001)
        trend_preds.append(d["predicted_value"])

    # Consecutive month differences must match the OLS slope (approx +1.63 USD/MT/month)
    slopes = [round(trend_preds[i] - trend_preds[i - 1], 4) for i in range(1, len(trend_preds))]
    assert all(math.isclose(s, slopes[0], abs_tol=0.001) for s in slopes)

    # 5. Test horizon boundary constraints (ge=1, le=6)
    r_zero = client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK&horizon_months=0")
    assert r_zero.status_code == 422  # FastAPI validation error

    r_seven = client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK&horizon_months=7")
    assert r_seven.status_code == 422


def test_endpoints_are_strictly_read_only(forecasting_api_db, client):
    """
    Verifies that forecasting API calls cause zero mutations to database rows
    across all tables in the complete database and zero writes to published Parquet partition files.
    Also intercepts and asserts zero mutating SQL statements (INSERT/UPDATE/DELETE/DDL).
    """
    session, engine, storage_dir = forecasting_api_db

    # 1. Capture complete database state across all tables in SQLite
    inspector = inspect(engine)
    all_tables = sorted(inspector.get_table_names())
    assert len(all_tables) > 0, "Test database must contain tables"

    def capture_full_db_state():
        state = {}
        with engine.connect() as conn:
            for tbl in all_tables:
                rows = conn.execute(text(f"SELECT * FROM `{tbl}` ORDER BY rowid")).fetchall()
                state[tbl] = rows
        return state

    db_state_before = capture_full_db_state()

    # 2. Baseline storage files state (file size and mtime)
    parquet_files_before = {
        p.as_posix(): (p.stat().st_size, p.stat().st_mtime)
        for p in storage_dir.rglob("*.parquet")
    }

    # 3. Intercept write operations on SQLAlchemy Engine during requests
    write_statements = []

    def intercept_write_operations(conn, cursor, statement, parameters, context, executemany):
        stmt_upper = statement.strip().upper()
        mutating_verbs = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "TRUNCATE")
        if any(stmt_upper.startswith(v) for v in mutating_verbs):
            write_statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", intercept_write_operations)

    try:
        # Execute read-only API calls across all endpoints, models, horizons, and dates
        client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK")
        client.get("/api/v1/freight/forecast?route_code=PNW-JPN-BULK&model_type=linear_trend_6&horizon_months=3")
        client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK&model_type=rolling_ma_3&horizon_months=6")
        client.get("/api/v1/freight/forecast?route_code=USG-JPN-BULK&model_type=rolling_ma_6&horizon_months=2")
        client.get("/api/v1/freight/forecast/baseline?route_code=USG-JPN-BULK&origin_date=2025-06-01")
        client.get("/api/v1/freight/forecast/evaluate?route_code=USG-JPN-BULK")
        client.get("/api/v1/freight/forecast/evaluate?route_code=PNW-JPN-BULK&include_steps=true")
    finally:
        event.remove(engine, "before_cursor_execute", intercept_write_operations)

    # Assert zero mutating SQL statements executed
    assert write_statements == [], f"Mutating SQL operations detected: {write_statements}"

    # Assert zero database mutations across every table in the complete database
    db_state_after = capture_full_db_state()
    assert db_state_after.keys() == db_state_before.keys()
    for tbl in all_tables:
        assert (
            db_state_after[tbl] == db_state_before[tbl]
        ), f"Table '{tbl}' was mutated during read-only forecasting API requests"

    # Assert zero storage files created, modified, or truncated
    parquet_files_after = {
        p.as_posix(): (p.stat().st_size, p.stat().st_mtime)
        for p in storage_dir.rglob("*.parquet")
    }
    assert parquet_files_after == parquet_files_before
