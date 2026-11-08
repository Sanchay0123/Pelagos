"""
Comprehensive unit and regression tests for Pelagos Calendar-Aware Forecast Evaluation.
Traces to Pelagos C1.3.1 requirements and verifies:
1. Missing T+1 target skips origin without positional array shifting.
2. Missing T+3 target skips origin without shifting.
3. Missing T+6 target skips origin without shifting.
4. 2007-2008 excluded-observation window correctly skips omitted months without bridging.
5. Sep 2007 to Jan 2008 (4 calendar months) is NOT evaluated as T+1.
6. Sep 2007 to Sep 2008 (12 calendar months) is NOT evaluated as T+2.
7. Ridge AR skips origins lacking continuous monthly autoregressive lags.
8. Rolling MA-3 and MA-6 skip origins lacking 3 or 6 contiguous calendar months.
9. Common target-date intersection produces identical sample sizes across competing models.
10. Zero lookahead guarantee in training windows and predictions.
11. In-sample MASE scale denominator calculated exclusively from training data up to origin.
12. Zero or undefined MASE scale handled safely without division by zero.
13. Persistence flat predicted movement yields flat_prediction_rate = 1.0 and None directional accuracy.
14. Zero actual spot movement handled gracefully without metric calculation failure.
15. Complete continuous datasets retain expected sample counts and reproduction of specification benchmarks.
"""

from datetime import date
import pytest

from pelagos.ingestion.freight_usda import load_usda_vessel_rates_sample
from pelagos.forecasting.schemas import (
    BaselineModelType,
    EvaluationStep,
    ModelEvaluationSummary,
)
from pelagos.forecasting.evaluation import (
    add_calendar_months,
    subtract_calendar_months,
    calculate_mae,
    calculate_rmse,
    calculate_mape,
    calculate_training_scale,
    check_contiguous_history,
    evaluate_walk_forward,
    evaluate_route_baselines,
    evaluate_common_sample_comparison,
)


def _generate_monthly_series(start_year: int, start_month: int, count: int, base_val: float = 50.0, step_val: float = 1.0):
    """Generates synthetic monthly observations dated the 1st of each month."""
    obs = []
    for i in range(count):
        total_m = start_year * 12 + start_month - 1 + i
        y = total_m // 12
        m = total_m % 12 + 1
        obs.append((date(y, m, 1), round(base_val + i * step_val, 2)))
    return obs


# Scenario 1: Missing T+1 target
def test_missing_t1_target_skipped_without_array_shift():
    """
    Verifies that when the T+1 calendar target is missing from the observation set,
    the walk-forward step is skipped and array positions do NOT shift to a later date.
    """
    # 12 burn-in months (2025-01 to 2025-12), then 2026-02 and 2026-03 (2026-01 is MISSING)
    obs = _generate_monthly_series(2025, 1, 12, base_val=40.0)
    # Append 2026-02-01 and 2026-03-01
    obs.append((date(2026, 2, 1), 60.0))
    obs.append((date(2026, 3, 1), 65.0))

    # Evaluate persistence for horizon T+1, burn_in=12
    summary, steps = evaluate_walk_forward(
        observations=obs,
        model_type="persistence",
        burn_in_months=12,
        horizon=1,
        include_skipped_steps=True,
    )

    # Origin 2025-12-01 target at T+1 is 2026-01-01 (which is missing)
    dec_step = next(s for s in steps if s.origin_date == date(2025, 12, 1))
    assert dec_step.is_valid is False
    assert dec_step.skip_reason == "missing_target_date"
    assert dec_step.target_date == date(2026, 1, 1)

    # Array MUST NOT shift 2026-02-01 into 2025-12-01's T+1 target!
    # The valid step for 2026-02-01 predicts 2026-03-01
    valid_steps = [s for s in steps if s.is_valid]
    assert len(valid_steps) == 1
    assert valid_steps[0].origin_date == date(2026, 2, 1)
    assert valid_steps[0].target_date == date(2026, 3, 1)
    assert valid_steps[0].actual_value == 65.0
    assert valid_steps[0].predicted_value == 60.0

    assert summary.skipped_steps_count == 1
    assert summary.skipped_reasons["missing_target_date"] == 1


# Scenario 2: Missing T+3 target
def test_missing_t3_target_skipped():
    """
    Verifies that when a T+3 calendar target is missing, the step is skipped
    and not matched with an adjacent index.
    """
    # 12 burn in (2024-01 to 2024-12), 2025-01, 2025-02, 2025-04 (2025-03 missing)
    obs = _generate_monthly_series(2024, 1, 12, base_val=30.0)
    obs.append((date(2025, 1, 1), 45.0))
    obs.append((date(2025, 2, 1), 46.0))
    obs.append((date(2025, 4, 1), 48.0))  # 2025-03-01 is missing

    # Origin 2024-12-01 at T+3 targets 2024-12 + 3m = 2025-03-01 (missing)
    summary, steps = evaluate_walk_forward(
        observations=obs,
        model_type="persistence",
        burn_in_months=12,
        horizon=3,
        candidate_origins=[date(2024, 12, 1)],
        include_skipped_steps=True,
    )
    assert summary.valid_steps_count == 0
    assert summary.skipped_steps_count == 1
    assert summary.skipped_reasons["missing_target_date"] == 1
    assert steps[0].is_valid is False
    assert steps[0].skip_reason == "missing_target_date"
    assert steps[0].target_date == date(2025, 3, 1)


# Scenario 3: Missing T+6 target
def test_missing_t6_target_skipped():
    """
    Verifies that when a T+6 calendar target is missing, the step is skipped.
    """
    obs = _generate_monthly_series(2024, 1, 18, base_val=30.0)
    # Remove month 18 (2025-06-01)
    target_to_remove = date(2025, 6, 1)
    obs = [o for o in obs if o[0] != target_to_remove]

    # Origin 2024-12-01 at T+6 targets 2025-06-01 (removed)
    summary, steps = evaluate_walk_forward(
        observations=obs,
        model_type="persistence",
        burn_in_months=12,
        horizon=6,
        candidate_origins=[date(2024, 12, 1)],
        include_skipped_steps=True,
    )
    assert summary.valid_steps_count == 0
    assert summary.skipped_steps_count == 1
    assert steps[0].target_date == date(2025, 6, 1)
    assert steps[0].skip_reason == "missing_target_date"


# Scenario 4: 2007-2008 excluded-observation window
def test_2007_2008_excluded_window_skips_ten_missing_months():
    """
    Verifies that when the 10 anomalous observations from Oct 2007 to Aug 2008
    (except Jan 2008) are excluded, origins preceding the gaps are skipped
    and never bridged positionally across the missing months.
    """
    # Create continuous series from 2005-01-01 to 2010-12-01 (72 months)
    full_obs = _generate_monthly_series(2005, 1, 72, base_val=50.0)

    # 10 excluded dates: Oct-Dec 2007 (3) and Feb-Aug 2008 (7)
    excluded_dates = {
        date(2007, 10, 1), date(2007, 11, 1), date(2007, 12, 1),
        date(2008, 2, 1), date(2008, 3, 1), date(2008, 4, 1),
        date(2008, 5, 1), date(2008, 6, 1), date(2008, 7, 1), date(2008, 8, 1),
    }
    filtered_obs = [o for o in full_obs if o[0] not in excluded_dates]

    # Evaluate persistence at T+1
    summary, steps = evaluate_walk_forward(
        observations=filtered_obs,
        model_type="persistence",
        burn_in_months=12,
        horizon=1,
        include_skipped_steps=True,
    )

    # At T+1, origins 2007-09-01 (targeting 2007-10-01) and 2008-01-01 (targeting 2008-02-01) must be skipped
    step_sep07 = next(s for s in steps if s.origin_date == date(2007, 9, 1))
    assert step_sep07.is_valid is False
    assert step_sep07.target_date == date(2007, 10, 1)
    assert step_sep07.skip_reason == "missing_target_date"

    step_jan08 = next(s for s in steps if s.origin_date == date(2008, 1, 1))
    assert step_jan08.is_valid is False
    assert step_jan08.target_date == date(2008, 2, 1)
    assert step_jan08.skip_reason == "missing_target_date"

    # Crucial assertion: no step in the backtest bridges 2007-09-01 to 2008-01-01 or 2008-01-01 to 2008-09-01
    for s in steps:
        if s.origin_date == date(2007, 9, 1):
            assert s.target_date != date(2008, 1, 1)
        if s.origin_date == date(2008, 1, 1):
            assert s.target_date != date(2008, 9, 1)

    # No valid step may ever target any of the 10 excluded dates
    for s in steps:
        if s.is_valid:
            assert s.target_date not in excluded_dates


# Scenario 5: Sep 2007 to Jan 2008 not treated as T+1
def test_sep_2007_to_jan_2008_not_treated_as_t1():
    """
    Verifies that the 4-month transition between 2007-09-01 and 2008-01-01
    is NOT evaluated as a 1-month forecast (T+1).
    """
    obs = _generate_monthly_series(2005, 1, 33, base_val=50.0)
    # Remove Oct, Nov, Dec 2007
    obs = [o for o in obs if o[0] not in (date(2007, 10, 1), date(2007, 11, 1), date(2007, 12, 1))]
    obs.append((date(2008, 1, 1), 99.0))
    obs.append((date(2008, 2, 1), 105.0))

    summary, steps = evaluate_walk_forward(
        observations=obs,
        model_type="persistence",
        burn_in_months=12,
        horizon=1,
        candidate_origins=[date(2007, 9, 1)],
        include_skipped_steps=True,
    )

    assert len(steps) == 1
    step = steps[0]
    # Origin 2007-09-01 target at T+1 is 2007-10-01 (which is missing)
    assert step.target_date == date(2007, 10, 1)
    assert step.is_valid is False
    assert step.skip_reason == "missing_target_date"
    # Crucial assertion: 2008-01-01 must NEVER be the target for 2007-09-01 at T+1
    assert step.target_date != date(2008, 1, 1)


# Scenario 6: Sep 2007 to Sep 2008 not treated as T+2
def test_sep_2007_to_sep_2008_not_treated_as_t2():
    """
    Verifies that the 12-month transition between 2007-09-01 and 2008-09-01
    is NOT evaluated as a 2-month forecast (T+2).
    """
    obs = _generate_monthly_series(2005, 1, 33, base_val=50.0)
    # Remove Oct, Nov, Dec 2007
    obs = [o for o in obs if o[0] not in (date(2007, 10, 1), date(2007, 11, 1), date(2007, 12, 1))]
    obs.append((date(2008, 1, 1), 99.0))
    # Exclude Feb through Aug 2008
    obs.append((date(2008, 9, 1), 88.0))

    summary, steps = evaluate_walk_forward(
        observations=obs,
        model_type="persistence",
        burn_in_months=12,
        horizon=2,
        candidate_origins=[date(2007, 9, 1)],
        include_skipped_steps=True,
    )

    assert len(steps) == 1
    step = steps[0]
    # T+2 target for 2007-09-01 must be 2007-11-01 (missing), NOT 2008-09-01!
    assert step.target_date == date(2007, 11, 1)
    assert step.is_valid is False
    assert step.skip_reason == "missing_target_date"
    assert step.target_date != date(2008, 9, 1)


# Scenario 7: Missing autoregressive lag months
def test_ridge_ar_skips_missing_autoregressive_lags():
    """
    Verifies that Ridge AR skips origins lacking continuous monthly lags
    rather than training on non-consecutive pairs across gaps.
    """
    obs = _generate_monthly_series(2025, 1, 15, base_val=50.0)
    # Remove month 10 (2025-10-01) to break continuity
    obs = [o for o in obs if o[0] != date(2025, 10, 1)]

    # For origin 2025-12-01, lookback of 12 months requires 2025-10-01 (which is missing)
    summary, steps = evaluate_walk_forward(
        observations=obs,
        model_type="ridge_ar",
        burn_in_months=12,
        horizon=1,
        candidate_origins=[date(2025, 12, 1)],
        include_skipped_steps=True,
    )

    assert summary.valid_steps_count == 0
    assert summary.skipped_steps_count == 1
    assert summary.skipped_reasons["missing_lookback_history"] == 1
    assert steps[0].is_valid is False
    assert steps[0].skip_reason == "missing_lookback_history"


# Scenario 8: Missing rolling-window history
def test_rolling_ma_skips_missing_lookback_history():
    """
    Verifies that Rolling MA-3 and MA-6 skip origins lacking 3 or 6 contiguous months.
    """
    obs = _generate_monthly_series(2025, 1, 15, base_val=50.0)
    # Remove 2025-11-01
    obs = [o for o in obs if o[0] != date(2025, 11, 1)]

    # Origin 2025-12-01: MA-3 requires [2025-10, 2025-11, 2025-12]. 2025-11 is missing!
    summary_ma3, steps_ma3 = evaluate_walk_forward(
        observations=obs,
        model_type="rolling_ma_3",
        burn_in_months=12,
        horizon=1,
        candidate_origins=[date(2025, 12, 1)],
        include_skipped_steps=True,
    )
    assert summary_ma3.valid_steps_count == 0
    assert summary_ma3.skipped_reasons["missing_lookback_history"] == 1
    assert steps_ma3[0].skip_reason == "missing_lookback_history"

    summary_ma6, steps_ma6 = evaluate_walk_forward(
        observations=obs,
        model_type="rolling_ma_6",
        burn_in_months=12,
        horizon=1,
        candidate_origins=[date(2025, 12, 1)],
        include_skipped_steps=True,
    )
    assert summary_ma6.valid_steps_count == 0
    assert summary_ma6.skipped_reasons["missing_lookback_history"] == 1


# Scenario 9: Common target-date intersection
def test_common_target_date_intersection():
    """
    Verifies that common-sample evaluation restricts metrics strictly to the
    exact intersection of valid target dates across all models.
    """
    # Create two step lists where Model A is valid on [D1, D2, D3] and Model B is valid on [D2, D3, D4]
    d1 = date(2026, 1, 1)
    d2 = date(2026, 2, 1)
    d3 = date(2026, 3, 1)
    d4 = date(2026, 4, 1)

    steps_a = [
        EvaluationStep(origin_date=subtract_calendar_months(d, 1), target_date=d, actual_value=50.0, predicted_value=52.0, absolute_error=2.0, squared_error=4.0, percentage_error=4.0, is_valid=True)
        for d in [d1, d2, d3]
    ]
    steps_b = [
        EvaluationStep(origin_date=subtract_calendar_months(d, 1), target_date=d, actual_value=50.0, predicted_value=55.0, absolute_error=5.0, squared_error=25.0, percentage_error=10.0, is_valid=True)
        for d in [d2, d3, d4]
    ]

    report = evaluate_common_sample_comparison(
        models_steps={"model_a": steps_a, "model_b": steps_b},
        route_code="USG-JPN-BULK",
        horizon=1,
    )

    # Intersection is exactly [D2, D3] (2 dates)
    assert report.common_target_dates_count == 2
    assert report.common_start_date == d2
    assert report.common_end_date == d3

    # Both models must have evaluation_steps_count == 2 on the common sample
    assert report.models["model_a"].evaluation_steps_count == 2
    assert report.models["model_b"].evaluation_steps_count == 2
    assert report.models["model_a"].mae == 2.0
    assert report.models["model_b"].mae == 5.0


# Scenario 10: Zero lookahead in training windows
def test_zero_lookahead_in_training_windows():
    """
    Verifies that observations after the forecast origin are never accessed
    by model predictions or training scaling.
    """
    obs_base = _generate_monthly_series(2025, 1, 14, base_val=40.0)
    # Contaminate future observation at Month 14 (index 13) with extreme outlier
    obs_clean = list(obs_base)
    obs_contam = list(obs_base)
    obs_contam[-1] = (obs_contam[-1][0], 9999.0)

    # Backtest up to Month 12 origin predicting Month 13
    s_clean, steps_clean = evaluate_walk_forward(
        obs_clean, "ridge_ar", burn_in_months=12, horizon=1, candidate_origins=[date(2025, 12, 1)]
    )
    s_contam, steps_contam = evaluate_walk_forward(
        obs_contam, "ridge_ar", burn_in_months=12, horizon=1, candidate_origins=[date(2025, 12, 1)]
    )

    # The prediction for Month 13 from Month 12 MUST be 100% identical regardless of Month 14 contamination
    assert steps_clean[0].predicted_value == steps_contam[0].predicted_value
    assert steps_clean[0].training_scale == steps_contam[0].training_scale


# Scenario 11: In-sample MASE scale calculated strictly from training data
def test_mase_scale_calculated_strictly_from_training_data():
    """
    Verifies that the MASE denominator at origin t uses only in-sample differences up to origin t.
    """
    history = [
        (date(2025, 1, 1), 10.0),
        (date(2025, 2, 1), 12.0),  # diff = 2.0
        (date(2025, 3, 1), 15.0),  # diff = 3.0
        (date(2025, 4, 1), 13.0),  # diff = 2.0
    ]
    # Mean diff = (2 + 3 + 2) / 3 = 7/3 = 2.3333
    scale = calculate_training_scale(history)
    assert scale == 2.3333

    # Add a broken gap: 2025-06-01 (skipping 2025-05)
    history.append((date(2025, 6, 1), 30.0))
    # 2025-06-01 minus 1m is 2025-05-01 (missing, so not in diffs)
    # Scale should remain (2 + 3 + 2) / 3 = 2.3333
    scale_gap = calculate_training_scale(history)
    assert scale_gap == 2.3333


# Scenario 12: Zero or undefined MASE scale handled safely
def test_zero_or_undefined_mase_scale_handled_safely():
    """
    Verifies that constant series with scale = 0.0 or insufficient history
    do not trigger ZeroDivisionError and step_mase is safely None.
    """
    constant_obs = _generate_monthly_series(2025, 1, 14, base_val=50.0, step_val=0.0)
    summary, steps = evaluate_walk_forward(
        observations=constant_obs,
        model_type="persistence",
        burn_in_months=12,
        horizon=1,
    )
    # Training scale is 0.0 because all diffs are 0
    assert steps[0].training_scale == 0.0
    assert steps[0].step_mase is None
    assert summary.mase is None


# Scenario 13: Flat predicted movement metrics
def test_flat_predicted_movement_metrics():
    """
    Verifies that Persistence always produces flat_prediction_rate = 1.0,
    eligible_directional_steps_count = 0, and directional_accuracy_non_flat = None.
    """
    obs = _generate_monthly_series(2025, 1, 24, base_val=40.0, step_val=2.0)
    summary, steps = evaluate_walk_forward(
        observations=obs,
        model_type="persistence",
        burn_in_months=12,
        horizon=1,
    )
    assert summary.flat_prediction_rate == 1.0
    assert summary.actual_non_zero_movement_rate == 1.0
    assert summary.eligible_directional_steps_count == 0
    assert summary.directional_accuracy_non_flat is None


# Scenario 14: Zero actual spot movement handled gracefully
def test_zero_actual_movement_handled_gracefully():
    """
    Verifies that when actual market spot rates do not change between origin and target,
    concordance is cleanly set to None and calculations succeed without error.
    """
    obs = _generate_monthly_series(2025, 1, 14, base_val=40.0)
    # Set target equal to origin
    obs[12] = (obs[12][0], 40.0)  # origin
    obs[13] = (obs[13][0], 40.0)  # target (actual change = 0)

    summary, steps = evaluate_walk_forward(
        observations=obs,
        model_type="linear_trend_6",
        burn_in_months=12,
        horizon=1,
        candidate_origins=[obs[12][0]],
    )
    assert len(steps) == 1
    assert steps[0].actual_change == 0.0
    # Since actual change is 0.0, direction is not eligible
    assert steps[0].direction_concordant is None


# Scenario 15: Complete continuous datasets retaining expected sample counts and benchmarks
def test_complete_continuous_dataset_retains_expected_sample_counts_and_metrics():
    """
    Verifies that on the 24-month fixture, calendar walk-forward reproduces
    the exact 12-step benchmark metrics with zero skipped steps.
    """
    raw_fixture = load_usda_vessel_rates_sample()
    raw_fixture.sort(key=lambda x: x["date"])

    usg_obs = []
    pnw_obs = []
    for r in raw_fixture:
        d = date.fromisoformat(r["date"].split("T")[0])
        usg_obs.append((d, float(r["gulf_to_japan"])))
        pnw_obs.append((d, float(r["pnw_to_japan"])))

    # Evaluate USG-JPN-BULK
    usg_report = evaluate_route_baselines(usg_obs, route_code="USG-JPN-BULK", burn_in_months=12, horizon=1)
    assert usg_report.evaluation_steps_count == 12
    assert usg_report.eval_start_date == date(2025, 10, 1)
    assert usg_report.eval_end_date == date(2026, 9, 1)

    usg_m = usg_report.models
    assert usg_m["persistence"].valid_steps_count == 12
    assert usg_m["persistence"].skipped_steps_count == 0
    assert usg_m["persistence"].mae == 3.14
    assert usg_m["persistence"].rmse == 3.81
    assert usg_m["persistence"].mape == 5.03

    # Common sample comparison across all models
    assert usg_report.common_sample is not None
    assert usg_report.common_sample.common_target_dates_count == 12
    assert usg_report.common_sample.models["persistence"].mae == 3.14

    # Evaluate PNW-JPN-BULK
    pnw_report = evaluate_route_baselines(pnw_obs, route_code="PNW-JPN-BULK", burn_in_months=12, horizon=1)
    assert pnw_report.evaluation_steps_count == 12
    pnw_m = pnw_report.models
    assert pnw_m["persistence"].mae == 1.12
    assert pnw_m["persistence"].rmse == 1.50
    assert pnw_m["persistence"].mape == 3.39
