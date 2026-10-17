"""
Deterministic unit and integration tests for Pelagos Freight Forecasting Baselines.
Traces to REQ-FR-FRF-001, ML-001 Section 5.3, and REQ-NFR-DIP-001.

Verifies:
1. Exact predictions for each baseline model (Persistence, MA-3, MA-6, Trend-6) on known input sequences.
2. Negative trend rate physical boundary clamping (clamped to 0.10 USD/MT with flag).
3. Warm-up and insufficient-history fencing (rejection with explicit status/exception).
4. Strict rejection of derived route SPREAD-USG-PNW-JPN (route conditioning constraint).
5. Mathematical precision of evaluation metrics (MAE, RMSE, MAPE).
6. Chronological zero-lookahead walk-forward evaluation (no future leakage).
7. Exact reproduction of the accepted specification benchmark table on the 24-month fixture.
8. Storage integration across published DuckDB Parquet partitions and SQL fallback.
"""

import math
from datetime import date
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.models import Base, FreightObservation
from pelagos.ingestion.freight_usda import load_usda_vessel_rates_sample, normalize_usda_record
from pelagos.storage.freight_writer import write_freight_partition_parquet, commit_freight_publication
from pelagos.forecasting.schemas import (
    BaselineModelType,
    STATUS_FORECAST_GENERATED,
    STATUS_INSUFFICIENT_HISTORY,
    FLAG_TREND_NEGATIVE_CLAMP,
)
from pelagos.forecasting.baselines import (
    predict_persistence,
    predict_rolling_ma,
    predict_linear_trend,
    predict_baseline,
    generate_baseline_forecast,
)
from pelagos.forecasting.evaluation import (
    calculate_mae,
    calculate_rmse,
    calculate_mape,
    evaluate_walk_forward,
    evaluate_route_baselines,
)
from pelagos.forecasting.service import (
    get_route_rate_observations,
    evaluate_route_baseline_models,
    forecast_next_route_rate,
)


def test_persistence_exact_prediction():
    """Verifies persistence baseline predicts latest observation at origin."""
    history = [45.0, 50.0, 55.25]
    pred, flags = predict_persistence(history, horizon=1)
    assert pred == 55.25
    assert flags == []

    # Horizon >= 1 maintains flat forecast
    pred3, _ = predict_persistence(history, horizon=3)
    assert pred3 == 55.25


def test_rolling_ma_exact_predictions():
    """Verifies Rolling MA-3 and MA-6 on known sequences."""
    history = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]

    # MA-3: mean of last 3: (40 + 50 + 60) / 3 = 50.0
    pred_ma3, flags = predict_rolling_ma(history, window=3)
    assert pred_ma3 == 50.00
    assert flags == []

    # MA-6: mean of last 6: (10 + 20 + 30 + 40 + 50 + 60) / 6 = 35.0
    pred_ma6, flags = predict_rolling_ma(history, window=6)
    assert pred_ma6 == 35.00
    assert flags == []


def test_linear_trend_exact_extrapolation():
    """Verifies Linear Trend-6 OLS extrapolation on known slope sequences."""
    # Perfectly linear sequence: y = 10 * x -> [10, 20, 30, 40, 50, 60]
    # For h=1, target x=7 -> 70.0
    history = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    pred_tr6, flags = predict_linear_trend(history, window=6, horizon=1)
    assert pred_tr6 == 70.00
    assert flags == []

    # Flat sequence: [50, 50, 50, 50, 50, 50] -> target 50.0
    flat_hist = [50.0] * 6
    pred_flat, _ = predict_linear_trend(flat_hist, window=6, horizon=1)
    assert pred_flat == 50.00


def test_linear_trend_negative_clamping():
    """Verifies physical boundary constraint: negative extrapolated rates clamp to 0.10 USD/MT."""
    # Steep downward trend: [60, 50, 40, 30, 20, 10]
    # OLS slope is -10. At x=7, unconstrained y = 0.0 -> clamped to 0.10
    steep_down = [60.0, 50.0, 40.0, 30.0, 20.0, 10.0]
    pred_clamped, flags = predict_linear_trend(steep_down, window=6, horizon=1)
    assert pred_clamped == 0.10
    assert FLAG_TREND_NEGATIVE_CLAMP in flags

    # Even steeper downward trend predicting negative value
    very_steep_down = [100.0, 80.0, 60.0, 40.0, 20.0, 5.0]
    pred_neg, flags_neg = predict_linear_trend(very_steep_down, window=6, horizon=1)
    assert pred_neg == 0.10
    assert FLAG_TREND_NEGATIVE_CLAMP in flags_neg


def test_insufficient_history_rejections():
    """Verifies that models explicitly reject history shorter than required warm-up window."""
    # Persistence: requires >= 1
    with pytest.raises(ValueError, match="at least 1 historical observation"):
        predict_persistence([])

    # MA-3: requires >= 3
    with pytest.raises(ValueError, match="requires at least 3 historical observations"):
        predict_rolling_ma([50.0, 55.0], window=3)

    # MA-6: requires >= 6
    with pytest.raises(ValueError, match="requires at least 6 historical observations"):
        predict_rolling_ma([50.0, 55.0, 60.0, 65.0, 70.0], window=6)

    # Trend-6: requires >= 6
    with pytest.raises(ValueError, match="requires at least 6 historical observations"):
        predict_linear_trend([50.0, 55.0, 60.0, 65.0, 70.0], window=6)

    # generate_baseline_forecast returns structured insufficient_history record
    rec = generate_baseline_forecast(
        route_code="USG-JPN-BULK",
        origin_date=date(2025, 2, 1),
        target_date=date(2025, 3, 1),
        history=[50.0, 55.0],
        model_type="rolling_ma_6",
    )
    assert rec.status == STATUS_INSUFFICIENT_HISTORY
    assert rec.predicted_value is None
    assert "requires at least 6" in rec.notes


def test_non_positive_and_invalid_rates_rejection():
    """Verifies that non-positive observations in history are rejected."""
    with pytest.raises(ValueError, match="must be strictly positive"):
        predict_rolling_ma([50.0, 0.0, 55.0], window=3)

    with pytest.raises(ValueError, match="must be strictly positive"):
        predict_linear_trend([50.0, 55.0, -5.0, 60.0, 65.0, 70.0], window=6)

    with pytest.raises(ValueError, match="horizon must be >= 1"):
        predict_persistence([50.0], horizon=0)


def test_disallowed_derived_route_spread_rejection():
    """Verifies that derived rate spread cannot be forecast directly as a corridor rate."""
    with pytest.raises(ValueError, match="derived rate difference indicator"):
        generate_baseline_forecast(
            route_code="SPREAD-USG-PNW-JPN",
            origin_date=date(2025, 5, 1),
            target_date=date(2025, 6, 1),
            history=[20.0, 22.0, 25.0],
            model_type="persistence",
        )

    with pytest.raises(ValueError, match="not supported for baseline forecasting"):
        generate_baseline_forecast(
            route_code="NONEXISTENT-ROUTE",
            origin_date=date(2025, 5, 1),
            target_date=date(2025, 6, 1),
            history=[20.0, 22.0, 25.0],
            model_type="persistence",
        )


def test_evaluation_metrics_precision():
    """Verifies mathematical precision of MAE, RMSE, and MAPE calculations."""
    actuals = [10.0, 20.0, 30.0]
    preds = [12.0, 18.0, 33.0]

    # Absolute errors: 2.0, 2.0, 3.0 -> mean = 7.0 / 3 = 2.3333 -> 2.33
    assert calculate_mae(actuals, preds) == 2.33

    # Squared errors: 4.0, 4.0, 9.0 -> mean = 17.0 / 3 = 5.6667 -> sqrt = 2.3805 -> 2.38
    assert calculate_rmse(actuals, preds) == 2.38

    # Percentage errors: 2/10 = 20%, 2/20 = 10%, 3/30 = 10% -> mean = 40% / 3 = 13.3333% -> 13.33%
    assert calculate_mape(actuals, preds) == 13.33

    # Mismatched lengths
    with pytest.raises(ValueError, match="identical length"):
        calculate_mae([10.0], [10.0, 20.0])


def test_walk_forward_leakage_prevention():
    """
    Verifies that chronological walk-forward strictly fences future observations.
    Injecting a massive outlier in future periods must have zero effect on earlier forecasts.
    """
    # 15 consecutive monthly dates
    base_dates = [date(2025, m, 1) if m <= 12 else date(2026, m - 12, 1) for m in range(1, 16)]
    clean_series = [50.0 + i for i in range(15)]

    # Contaminated series with an extreme outlier at step 14
    contaminated_series = list(clean_series)
    contaminated_series[13] = 99999.0  # Future outlier

    clean_obs = list(zip(base_dates, clean_series))
    contam_obs = list(zip(base_dates, contaminated_series))

    # Evaluate persistence for burn_in=12 (predicting steps 13 and 14)
    summary_clean, steps_clean = evaluate_walk_forward(clean_obs, "persistence", burn_in_months=12)
    summary_contam, steps_contam = evaluate_walk_forward(contam_obs, "persistence", burn_in_months=12)

    # Step 1 (predicting month 13 from month 12 history) must be 100% IDENTICAL
    assert steps_clean[0].predicted_value == steps_contam[0].predicted_value
    assert steps_clean[0].actual_value == steps_contam[0].actual_value


def test_fixture_backtest_reproduces_specification_benchmarks():
    """
    Verifies that walk-forward evaluation on the committed 24-month fixture
    reproduces the exact benchmark error metrics documented in specification Section 5.3.4.
    """
    raw_fixture = load_usda_vessel_rates_sample()
    raw_fixture.sort(key=lambda x: x["date"])

    usg_obs = []
    pnw_obs = []
    for r in raw_fixture:
        d = date.fromisoformat(r["date"].split("T")[0])
        usg_obs.append((d, float(r["gulf_to_japan"])))
        pnw_obs.append((d, float(r["pnw_to_japan"])))

    assert len(usg_obs) == 24
    assert len(pnw_obs) == 24

    # 1. Evaluate USG-JPN-BULK
    usg_report = evaluate_route_baselines(usg_obs, route_code="USG-JPN-BULK", burn_in_months=12, horizon=1)
    assert usg_report.evaluation_steps_count == 12
    assert usg_report.eval_start_date == date(2025, 10, 1)
    assert usg_report.eval_end_date == date(2026, 9, 1)

    usg_m = usg_report.models
    # USG Persistence: MAE 3.14, RMSE 3.81, MAPE 5.03%
    assert usg_m["persistence"].mae == 3.14
    assert usg_m["persistence"].rmse == 3.81
    assert usg_m["persistence"].mape == 5.03

    # USG MA-3: MAE 4.39, RMSE 5.73, MAPE 6.80%
    assert usg_m["rolling_ma_3"].mae == 4.39
    assert usg_m["rolling_ma_3"].rmse == 5.73
    assert usg_m["rolling_ma_3"].mape == 6.80

    # USG MA-6: MAE 6.85, RMSE 7.73, MAPE 10.45%
    assert usg_m["rolling_ma_6"].mae == 6.85
    assert usg_m["rolling_ma_6"].rmse == 7.73
    assert usg_m["rolling_ma_6"].mape == 10.45

    # USG Trend-6: MAE 5.05, RMSE 5.99, MAPE 8.19%
    assert usg_m["linear_trend_6"].mae == 5.05
    assert usg_m["linear_trend_6"].rmse == 5.99
    assert usg_m["linear_trend_6"].mape == 8.19

    # 2. Evaluate PNW-JPN-BULK
    pnw_report = evaluate_route_baselines(pnw_obs, route_code="PNW-JPN-BULK", burn_in_months=12, horizon=1)
    assert pnw_report.evaluation_steps_count == 12
    assert pnw_report.eval_start_date == date(2025, 10, 1)
    assert pnw_report.eval_end_date == date(2026, 9, 1)

    pnw_m = pnw_report.models
    # PNW Persistence: MAE 1.12, RMSE 1.50, MAPE 3.39%
    assert pnw_m["persistence"].mae == 1.12
    assert pnw_m["persistence"].rmse == 1.50
    assert pnw_m["persistence"].mape == 3.39

    # PNW MA-3: MAE 1.68, RMSE 2.32, MAPE 4.97%
    assert pnw_m["rolling_ma_3"].mae == 1.68
    assert pnw_m["rolling_ma_3"].rmse == 2.32
    assert pnw_m["rolling_ma_3"].mape == 4.97

    # PNW MA-6: MAE 2.61, RMSE 3.19, MAPE 7.55%
    assert pnw_m["rolling_ma_6"].mae == 2.61
    assert pnw_m["rolling_ma_6"].rmse == 3.19
    assert pnw_m["rolling_ma_6"].mape == 7.55

    # PNW Trend-6: MAE 1.98, RMSE 2.33, MAPE 5.91%
    assert pnw_m["linear_trend_6"].mae == 1.98
    assert pnw_m["linear_trend_6"].rmse == 2.33
    assert pnw_m["linear_trend_6"].mape == 5.91


@pytest.fixture
def test_forecasting_db(tmp_path):
    """Provides isolated SQLite database populated with fixture observations for service tests."""
    db_path = tmp_path / "test_forecasting.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

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


def test_forecasting_service_sql_fallback(test_forecasting_db):
    """Verifies service evaluations operate accurately via SQL fallback."""
    session, _, tmp_path = test_forecasting_db
    empty_storage = tmp_path / "empty_parquet"

    res = evaluate_route_baseline_models(
        db=session,
        route_code="USG-JPN-BULK",
        burn_in_months=12,
        storage_dir=empty_storage,
    )
    assert res["source"] == "sql_relational"
    assert res["route_code"] == "USG-JPN-BULK"
    assert res["evaluation_steps_count"] == 12
    assert res["models"]["persistence"]["mae"] == 3.14

    fc = forecast_next_route_rate(
        db=session,
        route_code="USG-JPN-BULK",
        model_type="persistence",
        storage_dir=empty_storage,
    )
    assert fc["source"] == "sql_relational"
    assert fc["origin_date"] == "2026-09-01"
    assert fc["target_date"] == "2026-10-01"
    assert fc["predicted_value"] == 76.38  # latest USG observation in fixture


def test_forecasting_service_duckdb_parquet(test_forecasting_db):
    """Verifies service evaluations operate via DuckDB over published Parquet partitions."""
    session, _, tmp_path = test_forecasting_db
    storage_dir = tmp_path / "parquet_storage"

    # Publish year partitions
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

    res = evaluate_route_baseline_models(
        db=session,
        route_code="PNW-JPN-BULK",
        burn_in_months=12,
        storage_dir=storage_dir,
    )
    assert res["source"] == "parquet_analytical"
    assert res["route_code"] == "PNW-JPN-BULK"
    assert res["evaluation_steps_count"] == 12
    assert res["models"]["persistence"]["mae"] == 1.12
    assert res["models"]["persistence"]["rmse"] == 1.50

    fc = forecast_next_route_rate(
        db=session,
        route_code="PNW-JPN-BULK",
        model_type="rolling_ma_3",
        storage_dir=storage_dir,
    )
    assert fc["source"] == "parquet_analytical"
    assert fc["origin_date"] == "2026-09-01"
    assert fc["target_date"] == "2026-10-01"
    # Latest 3 PNW rates in fixture: Jul (35.95), Aug (36.75), Sep (37.31) -> mean = 36.67
    assert fc["predicted_value"] == 36.67
