"""Unit and regression test suite for Stage C3.3 Regime-Aware & Multivariate Forecasting Experiments.

Covers:
1. Decision-time origin-regime assignment with zero target-period regime leakage.
2. Chronological training eligibility and train-only StandardScaler fit isolation.
3. Non-fallback insufficient-regime abstention (SUPERCYCLE_PEAK_CRASH and synthetic small regimes).
4. Regime-conditioned selector validation fallback under insufficient completed history.
5. Exact common-sample and matched-subset alignment, clamping, and deterministic seed reproducibility.
6. Empirical multivariate feasibility audit and frozen Persistence production boundary.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from pelagos.database import SessionLocal
from pelagos.forecasting.dataset import (
    ForecastDataset,
    ForecastDatasetRow,
    add_months_to_date,
    build_forecast_dataset,
)
from pelagos.forecasting.regime_experiments import (
    C33_FULL_SAMPLE_MODELS,
    C33_MODEL_MV_SPREAD,
    C33_MODEL_MV_VOL_SEASONAL,
    C33_MODEL_PERSISTENCE,
    C33_MODEL_REGIME_INTERACTION,
    C33_MODEL_REGIME_PARTITIONED_AR,
    C33_MODEL_REGIME_SELECTOR,
    C33_MODEL_RIDGE_AR,
    assess_multivariate_feasibility,
    audit_regime_sample_availability,
    benchmark_route_c33_experiments,
    decision_time_regime_id,
    evaluate_regime_aware_experiment,
)
from pelagos.forecasting.regimes import MarketRegimeId
from pelagos.forecasting.schemas import DEFAULT_BASELINE_MODEL


@pytest.fixture(scope="module")
def db_session():
    """Shared read-only session for Stage C3.3 integration checks."""
    session = SessionLocal()
    yield session
    session.close()


def _make_multi_regime_synthetic_dataset(
    start_obs: date = date(2002, 1, 1),
    n_months: int = 120,
    corrupt_from_idx: int | None = None,
) -> ForecastDataset:
    """Create a deterministic strict-forward synthetic dataset spanning PRE, SUPERCYCLE, and POST regimes."""
    rows: list[ForecastDatasetRow] = []
    for i in range(n_months):
        obs_date = add_months_to_date(start_obs, i)
        avail_date = add_months_to_date(obs_date, 1).replace(day=14)
        dec_date = avail_date
        tgt_date = add_months_to_date(obs_date, 2)  # strict-forward T+1 (+2 months)
        tgt_avail = add_months_to_date(tgt_date, 1).replace(day=14)

        rate = 30.0 + 0.15 * i + 2.0 * math.sin(2.0 * math.pi * obs_date.month / 12.0)
        if corrupt_from_idx is not None and i >= corrupt_from_idx:
            rate = 99999.0

        lag1 = rate - 0.2
        features = {
            "rate_lag_0": rate,
            "rate_lag_1": lag1,
            "rate_lag_2": rate - 0.4,
            "rate_lag_3": rate - 0.6,
            "rate_lag_6": rate - 1.2,
            "rate_lag_12": rate - 2.4,
            "rate_diff_1": rate - lag1,
            "rolling_std_6": 1.5,
            "rolling_std_12": 2.3,
            "rolling_ratio_12": 1.02,
            "origin_sin_month": math.sin(2.0 * math.pi * obs_date.month / 12.0),
            "origin_cos_month": math.cos(2.0 * math.pi * obs_date.month / 12.0),
            "competitor_rate_lag_0": rate * 1.4,
            "corridor_spread_lag_0": rate * 0.4,
            "corridor_spread_mean_3": rate * 0.38,
        }
        tgt_val = 99999.0 if (corrupt_from_idx is not None and i >= corrupt_from_idx) else rate + 0.3
        rows.append(
            ForecastDatasetRow(
                observation_date=obs_date,
                availability_date=avail_date,
                decision_date=dec_date,
                target_date=tgt_date,
                target_period_start=tgt_date,
                target_availability_date=tgt_avail,
                horizon_months=1,
                route_code="PNW-JPN-BULK",
                target_value=tgt_val,
                target_change=round(tgt_val - rate, 4),
                features=features,
                regime_id=decision_time_regime_id(tgt_date),
                is_labeled=True,
            )
        )

    return ForecastDataset(
        route_code="PNW-JPN-BULK",
        horizon_months=1,
        burn_in_months=12,
        feature_names=list(rows[0].features.keys()),
        rows=rows,
    )


class TestC33TemporalAndRegimeLeakage:
    """Verify strict decision-time regime assignment and walk-forward scaler/training isolation."""

    def test_origin_regime_boundary_uses_decision_time_not_target_period(self):
        """Observation in Dec 2006 assigns PRE_SUPERCYCLE even when T+1 target falls in Feb 2007."""
        assert decision_time_regime_id(date(2006, 12, 1)) == MarketRegimeId.PRE_SUPERCYCLE.value
        assert decision_time_regime_id(date(2007, 1, 1)) == MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value

    def test_mutating_target_regime_id_never_changes_predictions(self):
        """Mutating future evaluation row.regime_id produces identical C3.3 predictions across all regime experiments."""
        base_ds = _make_multi_regime_synthetic_dataset(n_months=90)
        mutated_rows = [
            ForecastDatasetRow(
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
                features=dict(r.features),
                regime_id=MarketRegimeId.PANDEMIC_DISRUPTION.value,  # Corrupt target-date evaluation label
                is_labeled=True,
            )
            for r in base_ds.rows
        ]
        mutated_ds = ForecastDataset(
            route_code=base_ds.route_code,
            horizon_months=base_ds.horizon_months,
            burn_in_months=base_ds.burn_in_months,
            feature_names=base_ds.feature_names,
            rows=mutated_rows,
        )

        for exp_name in (
            C33_MODEL_REGIME_SELECTOR,
            C33_MODEL_REGIME_INTERACTION,
            C33_MODEL_REGIME_PARTITIONED_AR,
        ):
            r_clean = evaluate_regime_aware_experiment(base_ds, exp_name, min_training_samples=20, seed=42)
            r_mut = evaluate_regime_aware_experiment(mutated_ds, exp_name, min_training_samples=20, seed=42)
            preds_clean = [(s.target_date, s.is_valid, s.predicted_value) for s in r_clean.steps]
            preds_mut = [(s.target_date, s.is_valid, s.predicted_value) for s in r_mut.steps]
            assert preds_clean == preds_mut

    def test_future_corruption_does_not_leak_into_earlier_steps_or_scaler(self):
        """Corrupting features/targets at index >= 65 leaves all predictions for origin idx < 65 identical."""
        clean_ds = _make_multi_regime_synthetic_dataset(n_months=90, corrupt_from_idx=None)
        corrupt_ds = _make_multi_regime_synthetic_dataset(n_months=90, corrupt_from_idx=65)
        cutoff_origin = clean_ds.rows[64].observation_date

        for exp_name in (
            C33_MODEL_REGIME_INTERACTION,
            C33_MODEL_MV_SPREAD,
            C33_MODEL_MV_VOL_SEASONAL,
            C33_MODEL_REGIME_SELECTOR,
            C33_MODEL_REGIME_PARTITIONED_AR,
        ):
            r_clean = evaluate_regime_aware_experiment(clean_ds, exp_name, min_training_samples=20, seed=42)
            r_corrupt = evaluate_regime_aware_experiment(corrupt_ds, exp_name, min_training_samples=20, seed=42)
            early_clean = [(s.origin_date, s.predicted_value) for s in r_clean.steps if s.origin_date <= cutoff_origin]
            early_corrupt = [(s.origin_date, s.predicted_value) for s in r_corrupt.steps if s.origin_date <= cutoff_origin]
            assert early_clean == early_corrupt


class TestC33InsufficientRegimeAndReproducibility:
    """Verify insufficient-regime handling, fallback behavior, clamping, and fixed-seed determinism."""

    def test_supercycle_insufficient_regime_abstention_on_live_dataset(self, db_session):
        """SUPERCYCLE_PEAK_CRASH (36 months) yields max 34 eligible pairs (<36) -> 0 valid partitioned steps."""
        ds = build_forecast_dataset(db=db_session, route_code="PNW-JPN-BULK", horizon_months=1)
        audit = audit_regime_sample_availability(ds, min_regime_samples=36)
        assert audit["origin_vs_target_regime_mismatches"] == 6
        assert audit["max_eligible_same_origin_train_pairs"][MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value] == 34
        assert MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value in audit["completely_insufficient_regimes"]

        part_res = evaluate_regime_aware_experiment(ds, C33_MODEL_REGIME_PARTITIONED_AR, min_training_samples=36, seed=42)
        assert part_res.diagnostics["origin_regime_valid_counts"][MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value] == 0
        assert part_res.diagnostics["origin_regime_skipped_counts"][MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value] == 36
        assert MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value in part_res.diagnostics["insufficient_regimes"]

    def test_selector_falls_back_to_persistence_when_in_regime_validation_insufficient(self):
        """If min_regime_selection_samples exceeds any regime history, selector always picks Persistence."""
        ds = _make_multi_regime_synthetic_dataset(n_months=60)
        p_res = evaluate_regime_aware_experiment(ds, C33_MODEL_PERSISTENCE, min_training_samples=15, seed=42)
        sel_res = evaluate_regime_aware_experiment(
            ds, C33_MODEL_REGIME_SELECTOR, min_training_samples=15, min_regime_selection_samples=500, seed=42
        )
        assert sel_res.diagnostics["selection_counts"][C33_MODEL_RIDGE_AR] == 0
        assert [s.predicted_value for s in sel_res.steps] == [s.predicted_value for s in p_res.steps]

    def test_physical_clamping_and_seed_reproducibility(self):
        """Extreme downward features clamp to >= 0.10 USD/MT and identical seeds yield identical outputs."""
        ds = _make_multi_regime_synthetic_dataset(n_months=60)
        extreme_rows = []
        for idx, r in enumerate(ds.rows):
            feats = dict(r.features)
            if idx == len(ds.rows) - 1:
                feats["competitor_rate_lag_0"] = -5000.0
                feats["corridor_spread_lag_0"] = -5000.0
            extreme_rows.append(
                ForecastDatasetRow(
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
                    features=feats,
                    regime_id=r.regime_id,
                    is_labeled=True,
                )
            )
        ext_ds = ForecastDataset(
            route_code=ds.route_code,
            horizon_months=ds.horizon_months,
            burn_in_months=ds.burn_in_months,
            feature_names=ds.feature_names,
            rows=extreme_rows,
        )
        r1 = evaluate_regime_aware_experiment(ext_ds, C33_MODEL_MV_SPREAD, min_training_samples=15, seed=42)
        r2 = evaluate_regime_aware_experiment(ext_ds, C33_MODEL_MV_SPREAD, min_training_samples=15, seed=42)
        preds1 = [s.predicted_value for s in r1.steps if s.is_valid]
        preds2 = [s.predicted_value for s in r2.steps if s.is_valid]
        assert preds1 == preds2
        assert all(p >= 0.10 for p in preds1)

    def test_common_sample_alignment_and_matched_subset_on_live_db(self, db_session):
        """benchmark_route_c33_experiments enforces exact N=319 full-sample and matched N=209 subset parity."""
        res = benchmark_route_c33_experiments(db=db_session, route_code="PNW-JPN-BULK", horizon=1, seed=42)
        cs = res["common_sample_comparison"]
        assert cs["common_sample_size"] == 319
        for m in C33_FULL_SAMPLE_MODELS:
            assert cs["models"][m]["sample_size"] == 319
        for cand in [m for m in C33_FULL_SAMPLE_MODELS if m != C33_MODEL_PERSISTENCE]:
            assert res["statistical_comparisons"][cand]["sample_size"] == 319
            assert "holm_adjusted_p_value" in res["statistical_comparisons"][cand]

        part = res["regime_partitioned_analysis"]
        assert part["matched_subset_sample_size"] == 209
        assert part["matched_subset_comparison"]["models"][C33_MODEL_PERSISTENCE]["sample_size"] == 209
        assert part["matched_subset_comparison"]["models"][C33_MODEL_REGIME_PARTITIONED_AR]["sample_size"] == 209

    def test_multivariate_feasibility_and_production_baseline_safeguard(self, db_session):
        """External non-freight domains report exact DB counts and remain excluded; Persistence remains default."""
        feas = assess_multivariate_feasibility(db=db_session)
        assert feas["database_counts"]["freight_observations"] == 1110
        assert feas["database_counts"]["trade_observations"] == 13
        assert feas["database_counts"]["environmental_observations"] == 13
        assert feas["database_counts"]["bunker_price_observations"] == 5
        assert feas["database_counts"]["vessel_positions"] == 120
        assert all(not dom["feasible_for_historical_backtest"] for dom in feas["excluded_external_domains"])
        assert DEFAULT_BASELINE_MODEL == "persistence"

