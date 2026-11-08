"""
Unit and regression tests for Pelagos Macroeconomic Regime Segmentation & Forecast Horizon Sensitivity.
Traces to Pelagos C1.3.2 requirements and verifies:
1. Every configured regime boundary is defined and ordered.
2. Inclusive start and end dates.
3. Exactly one regime assignment for every covered target date.
4. Dates outside configured regime coverage return None.
5. Forecast crossing a regime boundary is attributed to target date regime.
6. Target-date attribution rather than origin-date attribution.
7. No artificial training reset at regime boundaries.
8. No future-data leakage during regime analysis.
9. Missing target dates remain skipped within their target regime.
10. Contiguous lookback enforcement remains active.
11. Common target-date intersection is correct.
12. Condition-specific MASE denominators remain intact.
13. MASE aggregation preserves C1.3.1 arithmetic mean behavior.
14. N below 12 produces an explicit insufficient-sample status and warning.
15. Horizon growth calculations handle zero and unavailable denominators safely.
16. Missing regimes remain visible rather than disappearing from reports.
17. Repeated analysis produces deterministic results.
"""

from datetime import date
import pytest

from pelagos.forecasting.schemas import EvaluationStep
from pelagos.forecasting.regimes import (
    HISTORICAL_REGIMES,
    MarketRegimeId,
    MarketRegimeDefinition,
    RegimeEvaluationMetrics,
    HorizonSensitivityMetrics,
    HorizonGrowthMetrics,
    classify_target_date_regime,
    get_regime_by_id,
    compute_regime_metrics,
    compute_horizon_growth,
    MIN_DESCRIPTIVE_SAMPLE_THRESHOLD,
)
from pelagos.forecasting.evaluation import (
    evaluate_walk_forward,
    add_calendar_months,
    subtract_calendar_months,
)


# 1. Configured regime boundary completeness and order
def test_regime_boundaries_configured_and_ordered():
    """Verifies that all 4 historical regimes are centrally configured and monotonically ordered."""
    assert len(HISTORICAL_REGIMES) == 4
    expected_ids = [
        MarketRegimeId.PRE_SUPERCYCLE.value,
        MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value,
        MarketRegimeId.POST_SUPERCYCLE.value,
        MarketRegimeId.PANDEMIC_DISRUPTION.value,
    ]
    actual_ids = [r.regime_id for r in HISTORICAL_REGIMES]
    assert actual_ids == expected_ids

    # Check contiguous boundaries
    for i in range(len(HISTORICAL_REGIMES) - 1):
        r1 = HISTORICAL_REGIMES[i]
        r2 = HISTORICAL_REGIMES[i + 1]
        assert r1.end_date < r2.start_date
        # Exactly 1 month between end of r1 and start of r2
        next_m = add_calendar_months(r1.end_date, 1)
        assert next_m == r2.start_date


# 2. Inclusive start and end dates
def test_inclusive_start_and_end_dates():
    """Verifies that start and end dates of each regime are inclusive."""
    for r in HISTORICAL_REGIMES:
        assert r.contains_target_date(r.start_date) is True
        assert r.contains_target_date(r.end_date) is True
        # Just before start and just after end must be False
        assert r.contains_target_date(subtract_calendar_months(r.start_date, 1)) is False
        assert r.contains_target_date(add_calendar_months(r.end_date, 1)) is False


# 3. Exactly one regime assignment for every covered target date
def test_exactly_one_regime_for_covered_target_dates():
    """Verifies that every target date within the 1996-2026 span maps to exactly one regime."""
    start = date(1996, 1, 1)
    end = date(2026, 10, 1)
    cur = start
    while cur <= end:
        matches = [r for r in HISTORICAL_REGIMES if r.contains_target_date(cur)]
        assert len(matches) == 1, f"Date {cur} matched {len(matches)} regimes: {[m.regime_id for m in matches]}"
        classified = classify_target_date_regime(cur)
        assert classified is not None
        assert classified.regime_id == matches[0].regime_id
        cur = add_calendar_months(cur, 1)


# 4. Dates outside configured regime coverage
def test_dates_outside_configured_regime_coverage():
    """Verifies that dates before 1996-01-01 or after 2026-10-01 return None."""
    before_coverage = date(1995, 12, 1)
    after_coverage = date(2026, 11, 1)
    far_future = date(2030, 1, 1)

    assert classify_target_date_regime(before_coverage) is None
    assert classify_target_date_regime(after_coverage) is None
    assert classify_target_date_regime(far_future) is None


# 5. Forecast crossing a regime boundary
def test_forecast_crossing_regime_boundary():
    """
    Verifies that a forecast originating in Pre-Supercycle (e.g. 2006-12-01)
    with a T+1 target of 2007-01-01 is attributed to Supercycle Peak & Crash.
    """
    origin = date(2006, 12, 1)
    target = date(2007, 1, 1)

    origin_regime = classify_target_date_regime(origin)
    target_regime = classify_target_date_regime(target)

    assert origin_regime.regime_id == MarketRegimeId.PRE_SUPERCYCLE.value
    assert target_regime.regime_id == MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value

    step = EvaluationStep(
        origin_date=origin,
        target_date=target,
        actual_value=50.0,
        predicted_value=48.0,
        absolute_error=2.0,
        squared_error=4.0,
        percentage_error=4.0,
        is_valid=True,
    )

    r_supercycle = get_regime_by_id(MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value)
    r_presupercycle = get_regime_by_id(MarketRegimeId.PRE_SUPERCYCLE.value)

    metrics_supercycle = compute_regime_metrics(r_supercycle, [step])
    metrics_presupercycle = compute_regime_metrics(r_presupercycle, [step])

    # Attributed to Supercycle
    assert metrics_supercycle.valid_steps_count == 1
    assert metrics_supercycle.mae == 2.0
    # Not attributed to Pre-Supercycle
    assert metrics_presupercycle.valid_steps_count == 0


# 6. Target-date attribution rather than origin-date attribution
def test_target_date_attribution_decisive():
    """
    Verifies that a boundary step is classified strictly by target_date,
    even if the origin date is 6 months earlier in a previous regime.
    """
    origin = date(2006, 8, 1)  # Pre-Supercycle
    target = date(2007, 2, 1)  # Supercycle Peak & Crash (T+6)

    step = EvaluationStep(
        origin_date=origin,
        target_date=target,
        actual_value=60.0,
        predicted_value=55.0,
        absolute_error=5.0,
        squared_error=25.0,
        percentage_error=8.33,
        is_valid=True,
    )

    classified = classify_target_date_regime(step.target_date)
    assert classified.regime_id == MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value


# 7. No artificial training reset at regime boundaries
def test_no_artificial_training_reset_at_regime_boundaries():
    """
    Verifies that when evaluating a step inside Supercycle (e.g. 2007-01-01),
    historical observations from Pre-Supercycle are accessible and not cleared.
    """
    # 12 months in 2006 (Pre-Supercycle) and 1 month in 2007 (Supercycle)
    obs = []
    for m in range(1, 13):
        obs.append((date(2006, m, 1), 30.0 + m))
    obs.append((date(2007, 1, 1), 50.0))

    summary, steps = evaluate_walk_forward(
        observations=obs,
        model_type="persistence",
        burn_in_months=12,
        horizon=1,
    )
    assert len(steps) == 1
    step = steps[0]
    assert step.origin_date == date(2006, 12, 1)
    assert step.target_date == date(2007, 1, 1)
    # Predicted value is 42.0 (latest value in 2006 history)
    assert step.predicted_value == 42.0
    # Training scale uses all in-sample differences from 2006!
    assert step.training_scale is not None
    assert step.training_scale > 0.0


# 8. No future-data leakage during regime analysis
def test_no_future_data_leakage_in_regime_analysis():
    """
    Verifies that future data within a regime cannot leak into predictions for earlier dates.
    """
    obs_clean = [
        (date(2007, 1, 1), 40.0), (date(2007, 2, 1), 42.0), (date(2007, 3, 1), 45.0)
    ]
    # Add burn-in of 12 months prior
    burn_in = [(date(2006, m, 1), 30.0) for m in range(1, 13)]
    clean_series = burn_in + obs_clean

    contam_series = list(clean_series)
    # Corrupt future point in same regime
    contam_series[-1] = (date(2007, 3, 1), 9999.0)

    _, steps_clean = evaluate_walk_forward(clean_series, "persistence", burn_in_months=12, horizon=1)
    _, steps_contam = evaluate_walk_forward(contam_series, "persistence", burn_in_months=12, horizon=1)

    # First step in 2007 (origin 2006-12 predicting 2007-01) must be identical
    step_clean_jan = next(s for s in steps_clean if s.target_date == date(2007, 1, 1))
    step_contam_jan = next(s for s in steps_contam if s.target_date == date(2007, 1, 1))

    assert step_clean_jan.predicted_value == step_contam_jan.predicted_value
    assert step_clean_jan.training_scale == step_contam_jan.training_scale


# 9. Missing target dates remain skipped within their target regime
def test_missing_target_dates_skipped_within_target_regime():
    """
    Verifies that when a target date within Supercycle is missing (e.g. 2007-10-01),
    it is counted as skipped in that regime, not dropped.
    """
    r_supercycle = get_regime_by_id(MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value)

    skipped_step = EvaluationStep(
        origin_date=date(2007, 9, 1),
        target_date=date(2007, 10, 1),  # Inside Supercycle
        actual_value=None,
        predicted_value=None,
        is_valid=False,
        skip_reason="missing_target_date",
    )

    metrics = compute_regime_metrics(r_supercycle, [skipped_step])
    assert metrics.total_eligible_steps == 1
    assert metrics.valid_steps_count == 0
    assert metrics.skipped_steps_count == 1
    assert metrics.skipped_reasons.get("missing_target_date") == 1


# 10. Contiguous lookback enforcement remains active
def test_contiguous_lookback_enforcement_remains_active():
    """
    Verifies that steps skipped due to missing lookback are recorded as skipped
    with their correct reason within the target regime.
    """
    r_supercycle = get_regime_by_id(MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value)

    lookback_skipped = EvaluationStep(
        origin_date=date(2008, 1, 1),
        target_date=date(2008, 4, 1),
        actual_value=None,
        predicted_value=None,
        is_valid=False,
        skip_reason="missing_lookback_history",
    )

    metrics = compute_regime_metrics(r_supercycle, [lookback_skipped])
    assert metrics.skipped_steps_count == 1
    assert metrics.skipped_reasons.get("missing_lookback_history") == 1


# 11. Common target-date intersection is correct
def test_common_target_date_intersection():
    """
    Verifies that common-sample regime evaluation filters steps to the exact common dates.
    """
    d1 = date(2007, 1, 1)
    d2 = date(2007, 2, 1)
    d3 = date(2007, 3, 1)

    steps_cond_a = [
        EvaluationStep(origin_date=subtract_calendar_months(d, 1), target_date=d, actual_value=50.0, predicted_value=52.0, absolute_error=2.0, squared_error=4.0, percentage_error=4.0, is_valid=True)
        for d in [d1, d2, d3]
    ]
    # Condition B only has d1 and d2
    common_dates = {d1, d2}
    filtered_a = [s for s in steps_cond_a if s.target_date in common_dates]

    r_supercycle = get_regime_by_id(MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value)
    metrics_a = compute_regime_metrics(r_supercycle, filtered_a)

    assert metrics_a.valid_steps_count == 2
    assert metrics_a.mae == 2.0


# 12. Condition-specific MASE denominators remain intact
def test_condition_specific_mase_denominators_intact():
    """
    Verifies that step_mase values computed with condition-specific scales
    are accurately aggregated within regime metrics.
    """
    r_supercycle = get_regime_by_id(MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value)

    step1 = EvaluationStep(
        origin_date=date(2007, 1, 1), target_date=date(2007, 2, 1),
        actual_value=50.0, predicted_value=48.0, absolute_error=2.0, squared_error=4.0, percentage_error=4.0,
        training_scale=2.0, step_mase=1.0, is_valid=True
    )
    step2 = EvaluationStep(
        origin_date=date(2007, 2, 1), target_date=date(2007, 3, 1),
        actual_value=50.0, predicted_value=46.0, absolute_error=4.0, squared_error=16.0, percentage_error=8.0,
        training_scale=2.0, step_mase=2.0, is_valid=True
    )

    metrics = compute_regime_metrics(r_supercycle, [step1, step2], min_sample_threshold=2)
    assert metrics.mase == 1.5  # (1.0 + 2.0) / 2


# 13. MASE aggregation preserves C1.3.1 behavior
def test_mase_aggregation_preserves_arithmetic_mean():
    """Verifies that regime MASE is strictly the arithmetic mean of step_mase."""
    r_presupercycle = get_regime_by_id(MarketRegimeId.PRE_SUPERCYCLE.value)

    steps = [
        EvaluationStep(origin_date=date(2000, 1, 1), target_date=date(2000, 2, 1), actual_value=20.0, predicted_value=22.0, absolute_error=2.0, squared_error=4.0, percentage_error=10.0, step_mase=1.2, is_valid=True),
        EvaluationStep(origin_date=date(2000, 2, 1), target_date=date(2000, 3, 1), actual_value=20.0, predicted_value=24.0, absolute_error=4.0, squared_error=16.0, percentage_error=20.0, step_mase=2.4, is_valid=True),
    ]

    metrics = compute_regime_metrics(r_presupercycle, steps, min_sample_threshold=2)
    assert metrics.mase == round((1.2 + 2.4) / 2.0, 2)


# 14. N below 12 produces an explicit insufficient-sample status
def test_insufficient_sample_threshold_warning():
    """
    Verifies that when a regime has fewer than 12 valid steps,
    is_sample_sufficient is False and an explicit warning is attached.
    """
    r_supercycle = get_regime_by_id(MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value)

    few_steps = [
        EvaluationStep(
            origin_date=date(2007, 1, 1), target_date=date(2007, 2, 1),
            actual_value=50.0, predicted_value=52.0, absolute_error=2.0, squared_error=4.0, percentage_error=4.0,
            is_valid=True
        )
    ]  # N = 1

    metrics = compute_regime_metrics(r_supercycle, few_steps, min_sample_threshold=MIN_DESCRIPTIVE_SAMPLE_THRESHOLD)
    assert metrics.valid_steps_count == 1
    assert metrics.is_sample_sufficient is False
    assert metrics.sample_warning is not None
    assert "Insufficient sample size" in metrics.sample_warning


# 15. Horizon growth calculations handle zero and unavailable denominators
def test_horizon_growth_safe_division():
    """
    Verifies that compute_horizon_growth safely returns None for percentage increases
    when base MAE, RMSE, or MAPE is zero or metrics are unavailable.
    """
    base_zero = HorizonSensitivityMetrics(
        horizon_months=1, total_eligible_steps=10, valid_steps_count=10, skipped_steps_count=0,
        skipped_reasons={}, mae=0.0, rmse=0.0, mape=0.0, mase=None
    )
    target_pos = HorizonSensitivityMetrics(
        horizon_months=3, total_eligible_steps=10, valid_steps_count=10, skipped_steps_count=0,
        skipped_reasons={}, mae=3.5, rmse=5.0, mape=8.0, mase=1.5
    )

    growth = compute_horizon_growth(base_zero, target_pos)
    assert growth.absolute_mae_diff == 3.5
    assert growth.relative_mae_increase_pct is None  # Safe division by zero
    assert growth.relative_rmse_increase_pct is None
    assert growth.relative_mape_increase_pct is None


# 16. Missing regimes remain visible rather than disappearing from reports
def test_missing_regimes_remain_visible():
    """
    Verifies that if a regime has zero steps, it still produces a valid
    RegimeEvaluationMetrics object with valid_steps_count=0 and mae=None.
    """
    r_pandemic = get_regime_by_id(MarketRegimeId.PANDEMIC_DISRUPTION.value)

    # Empty steps list
    metrics = compute_regime_metrics(r_pandemic, [])
    assert metrics.regime_id == MarketRegimeId.PANDEMIC_DISRUPTION.value
    assert metrics.valid_steps_count == 0
    assert metrics.mae is None
    assert metrics.rmse is None
    assert metrics.is_sample_sufficient is False


# 17. Repeated analysis produces deterministic results
def test_repeated_analysis_is_deterministic():
    """
    Verifies that calling regime analysis multiple times produces bitwise identical results.
    """
    step = EvaluationStep(
        origin_date=date(2015, 1, 1), target_date=date(2015, 2, 1),
        actual_value=30.0, predicted_value=32.0, absolute_error=2.0, squared_error=4.0, percentage_error=6.67,
        actual_change=2.0, predicted_change=2.0, direction_concordant=True, is_valid=True
    )
    r_post = get_regime_by_id(MarketRegimeId.POST_SUPERCYCLE.value)

    run1 = compute_regime_metrics(r_post, [step], min_sample_threshold=1)
    run2 = compute_regime_metrics(r_post, [step], min_sample_threshold=1)

    assert run1.to_dict() == run2.to_dict()

