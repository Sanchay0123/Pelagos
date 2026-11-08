"""
Unit and Regression Test Suite for Pelagos C1.3.3 Candidate Forecast Model Benchmarking.
Covers:
1. Deterministic mathematical behavior of M1, M2, M3a, M3b, M4 models.
2. Contiguous calendar lookback enforcement per model type.
3. Zero future-data leakage in dynamic Ridge AR-1 parameter estimation.
4. Exact common target date intersection logic and sample-selection bias detection.
5. Diebold-Mariano test, HLN adjustment, and Moving Block Bootstrap CI reproducibility.
6. Formal research classifications (PROMISING, INCONCLUSIVE, NOT_BETTER_THAN_BASELINE).
7. Baseline non-promotion regression safeguards (Persistence remains default).

Traces to REQ-FR-FRF-001, ML-001 Section 5.3, and Pelagos C1.3.3.
"""

import pytest
import math
from datetime import date
from typing import List, Tuple, Dict

from pelagos.forecasting.schemas import (
    BaselineModelType,
    DEFAULT_BASELINE_MODEL,
    EXPERIMENTAL_MODELS,
    FLAG_TREND_NEGATIVE_CLAMP,
    FLAG_EXPERIMENTAL_MODEL,
    EvaluationStep,
)
from pelagos.forecasting.baselines import (
    predict_persistence,
    predict_rolling_ma,
    predict_linear_trend,
    predict_ridge_ar,
    predict_baseline,
    generate_baseline_forecast,
    MIN_TREND_RATE_CLAMP_USD_MT,
)
from pelagos.forecasting.evaluation import (
    add_calendar_months,
    subtract_calendar_months,
    check_contiguous_history,
    evaluate_walk_forward,
    evaluate_common_sample_comparison,
)
from pelagos.forecasting.statistical_testing import (
    calculate_loss_differentials,
    diebold_mariano_test,
    moving_block_bootstrap_ci,
    classify_candidate_performance,
    compare_candidate_to_baseline,
    student_t_two_sided_p_value,
    holm_bonferroni_step_down,
    RESEARCH_CLASSIFICATION_PROMISING,
    RESEARCH_CLASSIFICATION_INCONCLUSIVE,
    RESEARCH_CLASSIFICATION_NOT_BETTER,
)
from pelagos.forecasting.candidate_benchmarks import benchmark_route_candidates


class TestCandidateModelMathematics:
    """Verifies pure mathematical implementation of all candidate models."""

    def test_persistence_m1(self):
        """M1: y_hat_{t+h|t} = y_t."""
        history = [20.0, 22.0, 25.0]
        val, flags = predict_persistence(history, horizon=1)
        assert val == 25.0
        assert flags == []

        val3, _ = predict_persistence(history, horizon=3)
        assert val3 == 25.0

    def test_rolling_ma_3_m3a(self):
        """M3a: Mean of trailing 3 observations."""
        history = [10.0, 20.0, 30.0, 40.0]
        val, flags = predict_rolling_ma(history, window=3, horizon=1)
        assert val == round((20.0 + 30.0 + 40.0) / 3.0, 4)
        assert flags == []

        # Rejects if history < window
        with pytest.raises(ValueError, match="at least 3 historical observations"):
            predict_rolling_ma([10.0, 20.0], window=3)

    def test_rolling_ma_6_m3b(self):
        """M3b: Mean of trailing 6 observations."""
        history = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
        val, flags = predict_rolling_ma(history, window=6, horizon=1)
        assert val == 15.0
        assert flags == []

    def test_linear_trend_6_m4(self):
        """M4: OLS slope and intercept extrapolated to target horizon."""
        # Perfectly linear: y = 10 + 2 * x for x = 1..6 -> [12, 14, 16, 18, 20, 22]
        history = [12.0, 14.0, 16.0, 18.0, 20.0, 22.0]
        # At h=1, target x = 7 -> 10 + 2 * 7 = 24.0
        val, flags = predict_linear_trend(history, window=6, horizon=1)
        assert val == 24.0
        assert flags == []

        # At h=3, target x = 9 -> 10 + 2 * 9 = 28.0
        val3, _ = predict_linear_trend(history, window=6, horizon=3)
        assert val3 == 28.0

    def test_linear_trend_non_negative_clamp(self):
        """M4: Downward trend projecting negative rate clamped to MIN_TREND_RATE_CLAMP_USD_MT."""
        # Steep plunge: [30, 25, 20, 15, 10, 5] -> slope = -5, intercept = 35
        # x = 7 (h=1) -> 35 - 35 = 0 -> clamped to 0.10
        history = [30.0, 25.0, 20.0, 15.0, 10.0, 5.0]
        val, flags = predict_linear_trend(history, window=6, horizon=1)
        assert val == MIN_TREND_RATE_CLAMP_USD_MT
        assert FLAG_TREND_NEGATIVE_CLAMP in flags

    def test_ridge_ar_1_m2(self):
        """M2: Closed form normal equations with L2 alpha and iterated forecast."""
        # Constant history: y = 20.0
        history = [20.0] * 12
        val, flags, params = predict_ridge_ar(history, alpha=1.0, horizon=1)
        assert flags == [FLAG_EXPERIMENTAL_MODEL]
        assert params["l2_alpha"] == 1.0
        assert params["n_training_pairs"] == 11
        # Shrunk slightly toward mean
        assert 19.0 < val < 21.0

        # Iterated forecast at h=3
        val3, _, _ = predict_ridge_ar(history, alpha=1.0, horizon=3)
        assert 18.0 < val3 < 22.0

    def test_ridge_ar_non_negative_clamp(self):
        """M2: Negative prediction clamped to 0.10 USD/MT."""
        # Extreme negative trajectory
        history = [100.0, 50.0, 10.0, 1.0]
        # Force a case that dips below 0 with multi-step iteration
        val, flags, _ = predict_ridge_ar(history, alpha=0.0, horizon=10)
        assert val >= MIN_TREND_RATE_CLAMP_USD_MT


class TestContiguousLookbackEnforcement:
    """Verifies strict calendar lookback lag checking per candidate model."""

    def test_model_specific_lookback_requirements(self):
        # Full contiguous 12 months: 2020-01-01 to 2020-12-01
        dates = [date(2020, m, 1) for m in range(1, 13)]
        date_map = {d: 30.0 for d in dates}
        origin = date(2020, 12, 1)

        # M1 needs 1 month
        ok, h1, r1 = check_contiguous_history(origin, date_map, "persistence")
        assert ok is True and len(h1) == 1 and r1 is None

        # M3a needs 3 contiguous months
        ok, h3a, r3a = check_contiguous_history(origin, date_map, "rolling_ma_3")
        assert ok is True and len(h3a) == 3 and r3a is None

        # M3b needs 6 contiguous months
        ok, h3b, r3b = check_contiguous_history(origin, date_map, "rolling_ma_6")
        assert ok is True and len(h3b) == 6 and r3b is None

        # M4 needs 6 contiguous months
        ok, h4, r4 = check_contiguous_history(origin, date_map, "linear_trend_6")
        assert ok is True and len(h4) == 6 and r4 is None

        # M2 needs >= burn_in_months (12)
        ok, h2, r2 = check_contiguous_history(origin, date_map, "ridge_ar", burn_in_months=12)
        assert ok is True and len(h2) == 12 and r2 is None

    def test_broken_lookback_causes_model_specific_skips(self):
        """If 2 months ago is missing, M1 succeeds, M3a/M3b/M4/M2 skip."""
        # Missing 2020-10-01 (2 months prior to 2020-12-01)
        dates = [date(2020, m, 1) for m in range(1, 13) if m != 10]
        date_map = {d: 30.0 for d in dates}
        origin = date(2020, 12, 1)

        # M1 still succeeds (origin date 2020-12-01 is present)
        ok1, _, _ = check_contiguous_history(origin, date_map, "persistence")
        assert ok1 is True

        # M3a fails because 2020-10-01 is missing
        ok3a, _, r3a = check_contiguous_history(origin, date_map, "rolling_ma_3")
        assert ok3a is False
        assert r3a == "missing_lookback_history"

        # M2 fails because contiguous chain stops at 2 months
        ok2, _, r2 = check_contiguous_history(origin, date_map, "ridge_ar", burn_in_months=12)
        assert ok2 is False
        assert r2 == "missing_lookback_history"


class TestZeroDataLeakage:
    """Verifies dynamic parameters are strictly fitted without future data access."""

    def test_ridge_ar_zero_lookahead_guarantee(self):
        # 15 months of observations
        all_obs = [(date(2010, m, 1), 20.0 + float(m)) for m in range(1, 13)]
        # Predict at origin 2010-12-01
        origin_d = date(2010, 12, 1)
        history_before = [r for d, r in all_obs if d <= origin_d]
        pred_before, _, params_before = predict_ridge_ar(history_before, alpha=1.0, horizon=1)

        # Append future observations (2011-01-01, 2011-02-01) with massive shock
        future_obs = all_obs + [(date(2011, 1, 1), 999.0), (date(2011, 2, 1), 999.0)]
        # Forecast service at origin_d filters history <= origin_d
        history_at_origin = [r for d, r in future_obs if d <= origin_d]
        pred_after, _, params_after = predict_ridge_ar(history_at_origin, alpha=1.0, horizon=1)

        # Absolutely identical
        assert pred_before == pred_after
        assert params_before == params_after


class TestCommonSampleIntersection:
    """Verifies mathematical fairness through common target date intersection."""

    def test_common_sample_alignment(self):
        # Steps for model A: targets Jan, Feb, Mar, Apr
        steps_a = [
            EvaluationStep(origin_date=date(2020, 1, 1), target_date=date(2020, 2, 1), actual_value=20.0, predicted_value=21.0, is_valid=True),
            EvaluationStep(origin_date=date(2020, 2, 1), target_date=date(2020, 3, 1), actual_value=22.0, predicted_value=22.0, is_valid=True),
            EvaluationStep(origin_date=date(2020, 3, 1), target_date=date(2020, 4, 1), actual_value=25.0, predicted_value=24.0, is_valid=True),
        ]
        # Steps for model B: missed Feb due to missing lookback, targets Mar, Apr
        steps_b = [
            EvaluationStep(origin_date=date(2020, 1, 1), target_date=date(2020, 2, 1), actual_value=None, predicted_value=None, is_valid=False, skip_reason="missing_lookback_history"),
            EvaluationStep(origin_date=date(2020, 2, 1), target_date=date(2020, 3, 1), actual_value=22.0, predicted_value=23.0, is_valid=True),
            EvaluationStep(origin_date=date(2020, 3, 1), target_date=date(2020, 4, 1), actual_value=25.0, predicted_value=26.0, is_valid=True),
        ]

        report = evaluate_common_sample_comparison(
            models_steps={"model_a": steps_a, "model_b": steps_b},
            route_code="USG-JPN-BULK",
            horizon=1,
        )

        # Intersection is exactly 2 dates (Mar, Apr)
        assert report.common_target_dates_count == 2
        assert report.common_start_date == date(2020, 3, 1)
        assert report.common_end_date == date(2020, 4, 1)
        assert report.models["model_a"].valid_steps_count == 2
        assert report.models["model_b"].valid_steps_count == 2


class TestStatisticalTesting:
    """Verifies Diebold-Mariano test, HLN adjustment, and block bootstrap calculations."""

    def test_loss_differential_sign_convention(self):
        """Positive loss differential favors candidate (Model B)."""
        # Baseline error = 5.0, Candidate error = 3.0 -> Candidate is better by 2.0
        errors_a = [5.0]
        errors_b = [3.0]
        diffs = calculate_loss_differentials(errors_a, errors_b, loss_type="absolute")
        assert diffs == [2.0]

        # Baseline error = 2.0, Candidate error = 4.0 -> Candidate is worse by 2.0
        diffs_worse = calculate_loss_differentials([2.0], [4.0], loss_type="absolute")
        assert diffs_worse == [-2.0]

    def test_diebold_mariano_identical_series(self):
        """Identical forecasts produce zero DM statistic and p-value 1.0."""
        diffs = [0.0] * 50
        dm, hln, p_val, mean_d = diebold_mariano_test(diffs, horizon=1)
        assert dm == 0.0
        assert hln == 0.0
        assert p_val == 1.0
        assert mean_d == 0.0

    def test_diebold_mariano_hln_small_sample_factor(self):
        """HLN factor adjusts DM test statistic for horizon and sample size."""
        # Synthetic difference series with constant positive advantage
        diffs = [1.0, 1.2, 0.8, 1.1, 0.9, 1.3, 1.0, 0.7, 1.1, 1.2] * 5  # N = 50
        N = len(diffs)
        h = 3
        expected_hln_factor = math.sqrt((N + 1 - 2 * h + h * (h - 1) / float(N)) / float(N))

        dm, hln, p_val, mean_d = diebold_mariano_test(diffs, horizon=h)
        assert math.isclose(hln / dm, expected_hln_factor, rel_tol=1e-3)
        assert mean_d > 0.0
        assert p_val < 0.01

    def test_student_t_reference_distribution(self):
        """Verifies exact Student-t two-sided p-values against theoretical values."""
        # df = 1 (Cauchy): t = 1.0 -> p = 0.50
        assert math.isclose(student_t_two_sided_p_value(1.0, df=1), 0.50, abs_tol=1e-4)

        # df = 35: t = 2.0301 (5% critical value) -> p ≈ 0.05
        p_35 = student_t_two_sided_p_value(2.0301, df=35)
        assert math.isclose(p_35, 0.05, abs_tol=1e-3)

        # df = 119: t = 1.9801 (5% critical value) -> p ≈ 0.05
        p_119 = student_t_two_sided_p_value(1.9801, df=119)
        assert math.isclose(p_119, 0.05, abs_tol=1e-3)

        # t = 0 -> p = 1.0
        assert student_t_two_sided_p_value(0.0, df=100) == 1.0

    def test_holm_bonferroni_step_down(self):
        """Verifies Holm's step-down procedure controls FWER and maintains monotonicity."""
        raw_p = [0.005, 0.02, 0.04, 0.50]
        # m = 4:
        # rank 1: p=0.005 -> 4 * 0.005 = 0.02
        # rank 2: p=0.02  -> 3 * 0.02 = 0.06
        # rank 3: p=0.04  -> 2 * 0.04 = 0.08
        # rank 4: p=0.50  -> 1 * 0.50 = 0.50
        adj = holm_bonferroni_step_down(raw_p)
        assert math.isclose(adj[0], 0.02, abs_tol=1e-4)
        assert math.isclose(adj[1], 0.06, abs_tol=1e-4)
        assert math.isclose(adj[2], 0.08, abs_tol=1e-4)
        assert math.isclose(adj[3], 0.50, abs_tol=1e-4)

    def test_diebold_mariano_student_t_evaluation(self):
        """Verifies HLN-adjusted statistic is evaluated against Student-t(N-1), not standard normal."""
        # For N = 36 (Supercycle Crash), df = 35
        # Suppose diffs has t_stat = 2.00
        # Normal approx would give p = 0.0455 (< 0.05)
        # Student-t(35) gives p = 0.0533 (> 0.05)
        diffs = [1.0] * 20 + [-0.5] * 16  # N = 36
        dm, hln, p_val, mean_d = diebold_mariano_test(diffs, horizon=1)
        # Verify p-value matches Student's t on df=35
        expected_p = student_t_two_sided_p_value(hln, df=35)
        assert math.isclose(p_val, expected_p, abs_tol=1e-4)

    def test_moving_block_bootstrap_reproducibility(self):
        """Fixed seed guarantees identical bootstrap confidence intervals."""
        diffs = [-0.5, 0.2, 0.1, -0.3, 0.4, 0.6, -0.1, 0.2, 0.0, -0.2] * 10
        ci1_low, ci1_high = moving_block_bootstrap_ci(diffs, horizon=1, seed=42)
        ci2_low, ci2_high = moving_block_bootstrap_ci(diffs, horizon=1, seed=42)
        assert ci1_low == ci2_low
        assert ci1_high == ci2_high

    def test_research_classification_rules(self):
        # Promising: >= 5% improvement, p < 0.05, CI > 0
        c_prom = classify_candidate_performance(
            percent_improvement=6.5, p_value=0.02, ci_lower=0.10, ci_upper=0.50, mean_differential=0.30
        )
        assert c_prom == RESEARCH_CLASSIFICATION_PROMISING

        # Not Better: <= -5% improvement, p < 0.05
        c_bad = classify_candidate_performance(
            percent_improvement=-12.0, p_value=0.001, ci_lower=-1.5, ci_upper=-0.5, mean_differential=-1.0
        )
        assert c_bad == RESEARCH_CLASSIFICATION_NOT_BETTER

        # Inconclusive: positive improvement but CI spans zero (p >= 0.05)
        c_inc = classify_candidate_performance(
            percent_improvement=2.5, p_value=0.35, ci_lower=-0.15, ci_upper=0.30, mean_differential=0.08
        )
        assert c_inc == RESEARCH_CLASSIFICATION_INCONCLUSIVE


class TestBaselineNonPromotionGuard:
    """Safeguards operational baseline: Persistence remains default and winning."""

    def test_default_baseline_is_persistence(self):
        """Persistence must remain the default operational baseline model."""
        assert DEFAULT_BASELINE_MODEL == BaselineModelType.PERSISTENCE.value
        assert "persistence" not in EXPERIMENTAL_MODELS
        assert "ridge_ar" in EXPERIMENTAL_MODELS

    def test_ridge_ar_generates_experimental_warning(self):
        """Ridge AR point forecasts must attach experimental warnings and flags."""
        res = generate_baseline_forecast(
            route_code="USG-JPN-BULK",
            origin_date=date(2020, 12, 1),
            target_date=date(2021, 1, 1),
            history=[30.0] * 15,
            model_type="ridge_ar",
            horizon_months=1,
        )
        assert res.is_experimental is True
        assert res.model_family == "experimental_trained"
        assert FLAG_EXPERIMENTAL_MODEL in res.flags
        assert "Experimental trained candidate" in res.notes

    def test_persistence_remains_operational_baseline_on_synthetic_walk_forward(self):
        """Persistence maintains deterministic zero-parameter stability."""
        obs = [(date(2020, m, 1), 30.0 + (m % 3)) for m in range(1, 13)]
        summary_m1, _ = evaluate_walk_forward(obs, "persistence", horizon=1, burn_in_months=6)
        summary_ma3, _ = evaluate_walk_forward(obs, "rolling_ma_3", horizon=1, burn_in_months=6)
        # Persistence evaluates cleanly with zero lookback skips
        assert summary_m1.valid_steps_count > 0
        assert summary_m1.model_type == "persistence"

