"""
Unit and Regression Test Suite for Pelagos C3.2 Feature-Based Candidate ML Model Benchmarking.

Covers:
1. Chronological walk-forward ordering and strict-forward horizon eligibility (zero future lookahead).
2. Train/test separation and expanding window semantics.
3. Preprocessing isolation (StandardScaler fitted strictly on training rows).
4. Feature subset routing (linear_non_redundant for Ridge/Lasso, tree_based for RF, autoregressive for ablation).
5. Deterministic reproducibility under fixed random seeds.
6. Graceful handling of insufficient historical training observations (< 36 months).
7. Physical rate lower-bound clamping (>= 0.10 USD/MT).
8. Common-sample intersection and comparative metrics against Persistence baseline.
9. Statistical hypothesis testing: DM test with HLN small-sample adjustment and Holm-Bonferroni correction.
10. Canonical macroeconomic regime breakdown (PRE_SUPERCYCLE, SUPERCYCLE_PEAK_CRASH, POST_SUPERCYCLE, PANDEMIC_DISRUPTION).
11. Graceful degradation when external dependencies (LightGBM) are not installed.
12. Production safeguard: Persistence remains active default production benchmark.

Traces to REQ-FR-FRF-001, ML-001 Section 5.3, DATA-001 Section 5.2, and Stage C3.2.
"""

import pytest
import numpy as np
from datetime import date
from unittest.mock import MagicMock, patch

from pelagos.forecasting.ml_benchmarks import (
    ML_MODEL_PERSISTENCE,
    ML_MODEL_RIDGE,
    ML_MODEL_LASSO,
    ML_MODEL_RANDOM_FOREST,
    ML_MODEL_LIGHTGBM,
    ML_MODEL_RIDGE_AR,
    ML_CANDIDATE_CONFIGS,
    STANDARD_ML_BENCHMARK_MODELS,
    DEFAULT_MIN_TRAIN_MONTHS,
    DEFAULT_RANDOM_SEED,
    evaluate_ml_walk_forward,
    benchmark_route_ml_candidates,
    LIGHTGBM_AVAILABLE,
)
from pelagos.forecasting.schemas import (
    DEFAULT_BASELINE_MODEL,
    BaselineModelType,
    EvaluationStep,
    ModelEvaluationSummary,
)
from pelagos.forecasting.features import FEATURE_SUBSETS
from pelagos.forecasting.dataset import (
    ForecastDataset,
    ForecastDatasetRow,
    TemporalAvailabilityPolicy,
)
from pelagos.forecasting.statistical_testing import (
    holm_bonferroni_step_down,
    RESEARCH_CLASSIFICATION_NOT_BETTER,
    RESEARCH_CLASSIFICATION_INCONCLUSIVE,
    RESEARCH_CLASSIFICATION_PROMISING,
)


@pytest.fixture
def synthetic_forecast_dataset():
    """
    Creates a synthetic ForecastDataset with 50 sequential monthly observations
    for testing walk-forward logic, train/test splitting, and leakage controls.
    """
    rows = []
    base_year = 2000
    for idx in range(50):
        year = base_year + (idx // 12)
        month = (idx % 12) + 1
        obs_date = date(year, month, 1)

        # Target date is 1 month ahead (T+1)
        target_month = (month % 12) + 1
        target_year = year + (1 if month == 12 else 0)
        target_date = date(target_year, target_month, 1)

        # Fake features and target
        rate = 20.0 + idx * 0.5 + (idx % 3) * 1.5
        features = {
            "rate_lag_0": rate,
            "rate_lag_1": rate - 0.5,
            "rate_lag_2": rate - 1.0,
            "rate_lag_3": rate - 1.5,
            "rate_lag_6": rate - 3.0,
            "rate_lag_12": rate - 6.0,
            "rate_diff_1": 0.5,
            "rate_diff_3": 1.5,
            "rate_diff_12": 6.0,
            "rate_pct_change_1": 2.5,
            "rate_pct_change_3": 7.5,
            "rolling_mean_3": rate - 0.5,
            "rolling_mean_6": rate - 1.5,
            "rolling_mean_12": rate - 3.0,
            "rolling_std_6": 1.2,
            "rolling_std_12": 2.1,
            "rolling_ratio_12": 1.05,
            "origin_month": month,
            "origin_quarter": ((month - 1) // 3) + 1,
            "target_month": target_month,
            "origin_sin_month": float(np.sin(2 * np.pi * month / 12)),
            "origin_cos_month": float(np.cos(2 * np.pi * month / 12)),
            "regime_supercycle_peak_crash": 0.0,
            "regime_post_supercycle": 1.0 if year >= 2010 else 0.0,
            "regime_pandemic_disruption": 1.0 if year >= 2020 else 0.0,
            "regime_code": 0 if year < 2007 else (1 if year < 2010 else 2),
            "competitor_rate_lag_0": rate * 1.2,
            "competitor_rate_lag_1": (rate - 0.5) * 1.2,
            "corridor_spread_lag_0": rate * 0.2,
            "corridor_spread_mean_3": rate * 0.18,
            "route_benchmark_distance_nm": 4520.0,
        }

        # True target is rate + 0.5
        target_val = rate + 0.5

        row = ForecastDatasetRow(
            observation_date=obs_date,
            availability_date=date(year, month, 15),
            decision_date=date(year, month, 15),
            target_date=target_date,
            target_period_start=target_date,
            target_availability_date=date(target_year, target_month, 15),
            horizon_months=1,
            route_code="PNW-JPN-BULK",
            target_value=target_val,
            target_change=0.5,
            features=features,
            regime_id="POST_SUPERCYCLE",
            is_labeled=True,
        )
        rows.append(row)

    return ForecastDataset(
        route_code="PNW-JPN-BULK",
        horizon_months=1,
        burn_in_months=12,
        feature_names=list(features.keys()),
        rows=rows,
    )


class TestCandidateMLWalkForward:
    """Verifies chronological integrity, train/test splitting, and zero-leakage walk-forward evaluation."""

    def test_chronological_ordering_and_training_cutoff(self, synthetic_forecast_dataset):
        """
        Asserts chronological eligibility on the synthetic observation-relative fixture (target_date = obs + 1m):
        training samples j must satisfy target_date_j <= observation_date_i and
        target_availability_date_j <= decision_date_i (latest eligible row is i-1 here).
        """
        res = evaluate_ml_walk_forward(
            dataset=synthetic_forecast_dataset,
            model_name=ML_MODEL_PERSISTENCE,
            min_training_samples=10,
        )

        assert res.summary is not None
        assert res.summary.valid_steps_count > 0

        # Verify steps
        valid_steps = [s for s in res.steps if s.is_valid]
        skipped_steps = [s for s in res.steps if not s.is_valid]

        # First 10 steps should be skipped due to min_training_samples=10
        assert len(skipped_steps) == 10
        for s in skipped_steps:
            assert "insufficient_training_history" in s.skip_reason

        # Valid steps start at index 10 (11th observation)
        assert len(valid_steps) == 40
        assert valid_steps[0].origin_date == synthetic_forecast_dataset.labeled_rows[10].observation_date

    def test_ridge_and_lasso_linear_subset(self, synthetic_forecast_dataset):
        """Verifies that Ridge and Lasso execute properly on linear_non_redundant features."""
        ridge_res = evaluate_ml_walk_forward(
            dataset=synthetic_forecast_dataset,
            model_name=ML_MODEL_RIDGE,
            min_training_samples=12,
        )
        assert ridge_res.is_available is True
        assert ridge_res.feature_subset_name == "linear_non_redundant"
        assert ridge_res.feature_count == len(FEATURE_SUBSETS["linear_non_redundant"])
        assert ridge_res.summary is not None
        assert ridge_res.summary.mae > 0.0
        assert ridge_res.summary.rmse > 0.0

        lasso_res = evaluate_ml_walk_forward(
            dataset=synthetic_forecast_dataset,
            model_name=ML_MODEL_LASSO,
            min_training_samples=12,
        )
        assert lasso_res.is_available is True
        assert lasso_res.feature_subset_name == "linear_non_redundant"
        assert lasso_res.summary is not None
        assert lasso_res.summary.mae > 0.0

    def test_random_forest_tree_subset(self, synthetic_forecast_dataset):
        """Verifies that Random Forest executes properly on tree_based features."""
        rf_res = evaluate_ml_walk_forward(
            dataset=synthetic_forecast_dataset,
            model_name=ML_MODEL_RANDOM_FOREST,
            min_training_samples=15,
        )
        assert rf_res.is_available is True
        assert rf_res.feature_subset_name == "tree_based"
        assert rf_res.feature_count == len(FEATURE_SUBSETS["tree_based"])
        assert rf_res.summary is not None
        assert rf_res.summary.mae > 0.0

    def test_reproducibility_with_fixed_seed(self, synthetic_forecast_dataset):
        """Verifies that stochastic ensemble models (Random Forest) yield identical predictions with fixed seed."""
        res1 = evaluate_ml_walk_forward(
            dataset=synthetic_forecast_dataset,
            model_name=ML_MODEL_RANDOM_FOREST,
            min_training_samples=12,
            seed=42,
        )
        res2 = evaluate_ml_walk_forward(
            dataset=synthetic_forecast_dataset,
            model_name=ML_MODEL_RANDOM_FOREST,
            min_training_samples=12,
            seed=42,
        )

        preds1 = [s.predicted_value for s in res1.steps if s.is_valid]
        preds2 = [s.predicted_value for s in res2.steps if s.is_valid]

        assert len(preds1) == len(preds2)
        assert preds1 == preds2

    def test_physical_rate_clamping(self, synthetic_forecast_dataset):
        """Verifies that negative or zero model outputs are clamped to >= 0.10 USD/MT."""
        # Create a mock dataset with extreme negative trend
        modified_rows = []
        for r in synthetic_forecast_dataset.labeled_rows:
            mod_feat = dict(r.features)
            # Inject huge negative values to force negative regression prediction
            mod_feat["rate_lag_0"] = -100.0
            mod_feat["rate_lag_1"] = 100.0
            row = ForecastDatasetRow(
                observation_date=r.observation_date,
                availability_date=r.availability_date,
                decision_date=r.decision_date,
                target_date=r.target_date,
                target_period_start=r.target_period_start,
                target_availability_date=r.target_availability_date,
                horizon_months=r.horizon_months,
                route_code=r.route_code,
                target_value=r.target_value,
                target_change=r.target_change,
                features=mod_feat,
                regime_id=r.regime_id,
                is_labeled=True,
            )
            modified_rows.append(row)

        mod_ds = ForecastDataset(
            route_code="PNW-JPN-BULK",
            horizon_months=1,
            burn_in_months=12,
            feature_names=list(modified_rows[0].features.keys()),
            rows=modified_rows,
        )

        res = evaluate_ml_walk_forward(
            dataset=mod_ds,
            model_name=ML_MODEL_RIDGE,
            min_training_samples=12,
        )
        for s in res.steps:
            if s.is_valid and s.predicted_value is not None:
                assert s.predicted_value >= 0.10


class TestLightGBMAvailability:
    """Verifies graceful handling of conditional dependencies."""

    def test_lightgbm_graceful_degradation(self, synthetic_forecast_dataset):
        """Verifies that when LightGBM is evaluated, it degrades gracefully if unavailable."""
        res = evaluate_ml_walk_forward(
            dataset=synthetic_forecast_dataset,
            model_name=ML_MODEL_LIGHTGBM,
            min_training_samples=12,
        )
        if not LIGHTGBM_AVAILABLE:
            assert res.is_available is False
            assert "UNAVAILABLE" in res.unavailability_reason
            assert res.summary is None
            assert len(res.steps) == 0
        else:
            assert res.is_available is True
            assert res.summary is not None


class TestStatisticalTestingAndMultiComparison:
    """Verifies statistical testing and Holm-Bonferroni correction across candidates."""

    def test_holm_bonferroni_step_down(self):
        """Verifies Holm-Bonferroni step-down monotonic p-value adjustment."""
        # Unsorted candidate p-values
        raw_p = [0.01, 0.04, 0.03, 0.20]
        # Sorted: p(1)=0.01 (m=4 -> *4 = 0.04), p(2)=0.03 (m=3 -> *3 = 0.09), p(3)=0.04 (m=2 -> *2 = 0.08, max=0.09), p(4)=0.20 (m=1 -> *1 = 0.20)
        adj_p = holm_bonferroni_step_down(raw_p)

        assert len(adj_p) == 4
        # Original positions:
        # idx 0 (0.01) -> 0.04
        # idx 1 (0.04) -> 0.09 (running max with idx 2)
        # idx 2 (0.03) -> 0.09
        # idx 3 (0.20) -> 0.20
        assert adj_p[0] == 0.04
        assert adj_p[1] == 0.09
        assert adj_p[2] == 0.09
        assert adj_p[3] == 0.20

    def test_production_policy_safeguard(self):
        """Verifies that the default production baseline model remains Persistence."""
        assert DEFAULT_BASELINE_MODEL == BaselineModelType.PERSISTENCE.value
        assert ML_CANDIDATE_CONFIGS[ML_MODEL_PERSISTENCE]["is_experimental"] is False
        assert ML_CANDIDATE_CONFIGS[ML_MODEL_RIDGE]["is_experimental"] is True
        assert ML_CANDIDATE_CONFIGS[ML_MODEL_RANDOM_FOREST]["is_experimental"] is True


class TestLiveRouteBenchmarking:
    """Tests end-to-end benchmarking using genuine database observations."""

    def test_benchmark_route_ml_candidates_live(self):
        """
        Executes benchmark_route_ml_candidates on PNW-JPN-BULK at T+1 with a 36-month minimum window.
        Asserts common-sample alignment and statistical comparison against Persistence.
        """
        from pelagos.database import SessionLocal

        db = SessionLocal()
        try:
            # Run fast subset (Persistence, Ridge, Lasso, Ridge AR)
            fast_models = (
                ML_MODEL_PERSISTENCE,
                ML_MODEL_RIDGE,
                ML_MODEL_LASSO,
                ML_MODEL_RIDGE_AR,
            )
            res = benchmark_route_ml_candidates(
                db=db,
                route_code="PNW-JPN-BULK",
                horizon=1,
                min_training_samples=36,
                models=fast_models,
            )

            assert res["route_code"] == "PNW-JPN-BULK"
            assert res["horizon_months"] == 1
            assert res["total_labeled_rows"] == 356

            common = res["common_sample_comparison"]
            assert common["common_sample_size"] == 319  # 356 - 37 skipped steps = 319
            assert common["common_start_date"] == "2000-04-01"

            # Check that Persistence MAE is around ~3.49
            p_metrics = common["models"][ML_MODEL_PERSISTENCE]
            assert 3.40 <= p_metrics["mae"] <= 3.60

            # Statistical comparisons against Persistence
            stats = res["statistical_comparisons"]
            assert ML_MODEL_RIDGE in stats
            assert "holm_adjusted_p_value" in stats[ML_MODEL_RIDGE]
            assert "dm_statistic" in stats[ML_MODEL_RIDGE]
            assert "hln_statistic" in stats[ML_MODEL_RIDGE]

            # Regime breakdown
            regimes = res["regime_breakdown"]
            assert "PRE_SUPERCYCLE" in regimes
            assert "SUPERCYCLE_PEAK_CRASH" in regimes
            assert "POST_SUPERCYCLE" in regimes
            assert "PANDEMIC_DISRUPTION" in regimes
        finally:
            db.close()


class TestInvariantRegressions:
    """Focused regression tests for strict-forward availability, train-only scaling, MDA, and regime boundaries."""

    @staticmethod
    def _add_months(d: date, months: int) -> date:
        total_m = d.year * 12 + d.month - 1 + months
        return date(total_m // 12, total_m % 12 + 1, 1)

    def _make_strict_forward_dataset(self, start_year: int = 2005, count: int = 40, delayed_idx: int = None) -> ForecastDataset:
        rows = []
        for idx in range(count):
            obs_date = self._add_months(date(start_year, 1, 1), idx)
            avail_date = self._add_months(obs_date, 1).replace(day=14)
            dec_date = avail_date
            # Strict forward T+1 targets M+2
            target_date = self._add_months(obs_date, 2)
            target_avail_date = self._add_months(target_date, 1).replace(day=14)
            if delayed_idx is not None and idx == delayed_idx:
                # Simulate delayed release beyond next decision date
                target_avail_date = self._add_months(target_avail_date, 2)

            rate = 25.0 + idx * 0.4 + (idx % 4) * 0.3
            target_val = rate + 0.6 + ((idx % 2) * 0.2 - 0.1)
            features = {
                f: float(rate)
                for f in FEATURE_SUBSETS["all"]
            }
            features.update({
                "rate_lag_0": rate,
                "rate_lag_1": rate - 0.4,
                "rate_lag_2": rate - 0.8,
                "rate_lag_3": rate - 1.2,
                "rolling_std_6": 0.5 + (idx % 3) * 0.1,
                "rolling_std_12": 0.8 + (idx % 5) * 0.1,
                "origin_sin_month": float(np.sin(2 * np.pi * obs_date.month / 12)),
                "origin_cos_month": float(np.cos(2 * np.pi * obs_date.month / 12)),
                "regime_supercycle_peak_crash": 1.0 if 2007 <= target_date.year <= 2009 else 0.0,
                "regime_post_supercycle": 1.0 if 2010 <= target_date.year <= 2019 else 0.0,
                "regime_pandemic_disruption": 1.0 if target_date.year >= 2020 else 0.0,
                "competitor_rate_lag_0": rate * 1.3,
                "corridor_spread_lag_0": rate * 0.3,
            })
            rows.append(
                ForecastDatasetRow(
                    observation_date=obs_date,
                    availability_date=avail_date,
                    decision_date=dec_date,
                    target_date=target_date,
                    target_period_start=target_date,
                    target_availability_date=target_avail_date,
                    horizon_months=1,
                    route_code="PNW-JPN-BULK",
                    target_value=target_val,
                    target_change=round(target_val - rate, 4),
                    features=features,
                    regime_id="PRE_SUPERCYCLE" if target_date.year <= 2006 else "SUPERCYCLE_PEAK_CRASH",
                    is_labeled=True,
                )
            )
        return ForecastDataset(
            route_code="PNW-JPN-BULK",
            horizon_months=1,
            burn_in_months=12,
            feature_names=list(FEATURE_SUBSETS["all"]),
            rows=rows,
        )

    def test_strict_forward_availability_and_unsorted_inputs(self):
        """
        Verifies:
        1) Unsorted input rows are chronologically ordered prior to walk-forward splitting.
        2) Under strict-forward T+1 (target_date = obs + 2m), step i excludes row i-1
           so min_training_samples=10 skips the first 11 rows (indices 0..10).
        3) Delaying target_availability_date_j beyond decision_date_i excludes row j even
           when target_date_j <= observation_date_i.
        """
        ds_sorted = self._make_strict_forward_dataset(count=30)
        reversed_rows = list(reversed(ds_sorted.rows))
        ds_unsorted = ForecastDataset(
            route_code=ds_sorted.route_code,
            horizon_months=1,
            burn_in_months=12,
            feature_names=ds_sorted.feature_names,
            rows=reversed_rows,
        )

        res_sorted = evaluate_ml_walk_forward(ds_sorted, ML_MODEL_RIDGE, min_training_samples=10, seed=42)
        res_unsorted = evaluate_ml_walk_forward(ds_unsorted, ML_MODEL_RIDGE, min_training_samples=10, seed=42)

        assert res_sorted.summary.skipped_steps_count == 11  # i=0..10 skipped (i=10 only has j=0..8 = 9 <= 10-1)
        assert res_sorted.summary.valid_steps_count == 19
        assert [s.predicted_value for s in res_sorted.steps] == [s.predicted_value for s in res_unsorted.steps]

        # Delay row index 9's target_availability_date so at step i=11 (decision 2006-01-14),
        # row 9 (obs 2005-10-01, target 2005-12-01, normal avail 2006-01-14) becomes delayed to 2006-03-14.
        ds_delayed = self._make_strict_forward_dataset(count=30, delayed_idx=9)
        res_delayed = evaluate_ml_walk_forward(ds_delayed, ML_MODEL_RIDGE, min_training_samples=10, seed=42)
        # Step i=11 only has indices 0..8 (9 eligible < 10) -> now 12 skipped steps instead of 11!
        assert res_delayed.summary.skipped_steps_count == 12
        assert res_delayed.summary.valid_steps_count == 18

    def test_train_only_standard_scaler_isolation(self):
        """
        Verifies StandardScaler and model weights at step k are strictly isolated from
        future rows and future test magnitudes (truncation invariance).
        """
        ds_full = self._make_strict_forward_dataset(count=35)
        ds_trunc = ForecastDataset(
            route_code=ds_full.route_code,
            horizon_months=1,
            burn_in_months=12,
            feature_names=ds_full.feature_names,
            rows=ds_full.rows[:25],
        )
        # Corrupt later rows after index 24 in a copy to simulate massive scale shift in future
        shifted_rows = list(ds_full.rows[:25])
        for future_r in ds_full.rows[25:]:
            bad_feats = dict(future_r.features)
            bad_feats["rate_lag_0"] = 99999.0
            bad_feats["corridor_spread_lag_0"] = 50000.0
            shifted_rows.append(
                ForecastDatasetRow(
                    observation_date=future_r.observation_date,
                    availability_date=future_r.availability_date,
                    decision_date=future_r.decision_date,
                    target_date=future_r.target_date,
                    target_period_start=future_r.target_period_start,
                    target_availability_date=future_r.target_availability_date,
                    horizon_months=future_r.horizon_months,
                    route_code=future_r.route_code,
                    target_value=future_r.target_value,
                    target_change=future_r.target_change,
                    features=bad_feats,
                    regime_id=future_r.regime_id,
                    is_labeled=True,
                )
            )
        ds_shifted = ForecastDataset(
            route_code=ds_full.route_code,
            horizon_months=1,
            burn_in_months=12,
            feature_names=ds_full.feature_names,
            rows=shifted_rows,
        )

        res_trunc = evaluate_ml_walk_forward(ds_trunc, ML_MODEL_RIDGE, min_training_samples=10, seed=42)
        res_shifted = evaluate_ml_walk_forward(ds_shifted, ML_MODEL_RIDGE, min_training_samples=10, seed=42)

        trunc_preds = [s.predicted_value for s in res_trunc.steps if s.is_valid]
        shifted_preds_prefix = [s.predicted_value for s in res_shifted.steps[:25] if s.is_valid]
        assert trunc_preds == shifted_preds_prefix

    def test_mda_and_flat_prediction_accounting(self, synthetic_forecast_dataset):
        """
        Verifies descriptive MDA handling:
        - Persistence emits 100% flat predictions (predicted_change == 0) -> 0 eligible directional steps, MDA=None.
        - Candidate models track flat predictions, non-zero actuals, and compute MDA on non-flat pairs only.
        """
        res_pers = evaluate_ml_walk_forward(synthetic_forecast_dataset, ML_MODEL_PERSISTENCE, min_training_samples=12)
        assert res_pers.summary.flat_prediction_rate == 1.0
        assert res_pers.summary.eligible_directional_steps_count == 0
        assert res_pers.summary.directional_accuracy_non_flat is None

        res_ridge = evaluate_ml_walk_forward(synthetic_forecast_dataset, ML_MODEL_RIDGE, min_training_samples=12)
        assert res_ridge.summary.flat_prediction_rate < 1.0
        assert res_ridge.summary.eligible_directional_steps_count > 0
        assert res_ridge.summary.directional_accuracy_non_flat is not None

    def test_regime_attribution_by_target_date_boundary(self):
        """
        Verifies steps crossing a macroeconomic regime boundary between observation_date
        (e.g. 2006-11-01, PRE_SUPERCYCLE) and target_date (2007-01-01, SUPERCYCLE_PEAK_CRASH)
        are attributed to regimes strictly by target_date per C1.3.2.
        """
        from pelagos.forecasting.regimes import HISTORICAL_REGIMES, compute_regime_metrics

        ds = self._make_strict_forward_dataset(start_year=2004, count=40)
        res = evaluate_ml_walk_forward(ds, ML_MODEL_PERSISTENCE, min_training_samples=10)
        valid = [s for s in res.steps if s.is_valid]

        # Find step with origin 2006-11-01 -> target 2007-01-01
        boundary_step = next(s for s in valid if s.origin_date == date(2006, 11, 1))
        assert boundary_step.target_date == date(2007, 1, 1)

        pre_reg = next(r for r in HISTORICAL_REGIMES if r.regime_id == "PRE_SUPERCYCLE")
        super_reg = next(r for r in HISTORICAL_REGIMES if r.regime_id == "SUPERCYCLE_PEAK_CRASH")

        pre_metrics = compute_regime_metrics(pre_reg, valid)
        super_metrics = compute_regime_metrics(super_reg, valid)
        # Sum of counts across covered regimes matches valid steps crossing those regimes
        assert pre_reg.contains_target_date(boundary_step.target_date) is False
        assert super_reg.contains_target_date(boundary_step.target_date) is True
        assert pre_metrics.valid_steps_count + super_metrics.valid_steps_count == len(valid)

