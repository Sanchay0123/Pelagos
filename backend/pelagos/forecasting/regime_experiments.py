"""Pelagos Stage C3.3 Regime-Aware & Controlled Multivariate Forecasting Investigation.

Evaluates strictly leakage-free regime-aware models and orthogonal multivariate
additions built exclusively from historically covered data domains, benchmarked
against the operational Persistence (M1) baseline and Stage C3.2 Ridge AR ablation:

- persistence: Operational baseline (y_{t+h|t} = y_t) [Frozen]
- ridge_autoregressive: C3.2 pure AR baseline (6 freight lags)
- regime_conditioned_selector: Walk-forward selector between Persistence and Ridge AR
  using strictly completed prior realized errors in the decision-time origin regime
- regime_interaction_ridge_ar: Ridge regression combining 6 AR lags with decision-time
  origin-regime indicators and controlled short-momentum interactions (regime * rate_diff_1)
- multivariate_ar_spread: Controlled multivariate Ridge adding strictly gated
  cross-corridor / spread features onto the 6 AR lags
- multivariate_ar_vol_seasonal: Controlled multivariate Ridge adding own-route
  volatility, mean-reversion ratio, and harmonic seasonality onto the 6 AR lags
- regime_partitioned_ridge_ar: Non-fallback regime-specific Ridge AR abstaining
  whenever eligible same-regime history falls below min_training_samples

Traces to Stage C3.3, Stage C3.2, Stage C3.1.2, C1.3.2, and ML-001.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sqlalchemy.orm import Session

from pelagos.forecasting.baselines import MIN_TREND_RATE_CLAMP_USD_MT
from pelagos.forecasting.dataset import (
    ForecastDataset,
    ForecastDatasetRow,
    build_forecast_dataset,
    generate_feature_inventory,
)
from pelagos.forecasting.evaluation import (
    calculate_mae,
    calculate_mape,
    calculate_rmse,
    calculate_training_scale,
)
from pelagos.forecasting.features import FEATURE_CATALOG, FEATURE_SUBSETS
from pelagos.forecasting.ml_benchmarks import (
    DEFAULT_MIN_TRAIN_MONTHS,
    DEFAULT_RANDOM_SEED,
    ML_MODEL_PERSISTENCE,
    ML_MODEL_RIDGE_AR,
    MLModelBenchmarkResult,
    evaluate_ml_walk_forward,
)
from pelagos.forecasting.regimes import (
    HISTORICAL_REGIMES,
    MIN_DESCRIPTIVE_SAMPLE_THRESHOLD,
    MarketRegimeId,
    classify_target_date_regime,
    compute_regime_metrics,
)
from pelagos.forecasting.schemas import (
    DISALLOWED_DERIVED_ROUTES,
    EvaluationStep,
    ModelEvaluationSummary,
    SUPPORTED_DIRECT_ROUTES,
)
from pelagos.forecasting.statistical_testing import (
    compare_candidate_to_baseline,
    holm_bonferroni_step_down,
)

logger = logging.getLogger(__name__)

DEFAULT_MIN_REGIME_SELECTION_SAMPLES = 12

C33_MODEL_PERSISTENCE = ML_MODEL_PERSISTENCE
C33_MODEL_RIDGE_AR = ML_MODEL_RIDGE_AR
C33_MODEL_REGIME_SELECTOR = "regime_conditioned_selector"
C33_MODEL_REGIME_INTERACTION = "regime_interaction_ridge_ar"
C33_MODEL_MV_SPREAD = "multivariate_ar_spread"
C33_MODEL_MV_VOL_SEASONAL = "multivariate_ar_vol_seasonal"
C33_MODEL_REGIME_PARTITIONED_AR = "regime_partitioned_ridge_ar"

AR_BASE_FEATURES: Tuple[str, ...] = tuple(FEATURE_SUBSETS["autoregressive"])
MV_SPREAD_FEATURES: Tuple[str, ...] = AR_BASE_FEATURES + (
    "competitor_rate_lag_0",
    "corridor_spread_lag_0",
    "corridor_spread_mean_3",
)
MV_VOL_SEASONAL_FEATURES: Tuple[str, ...] = AR_BASE_FEATURES + (
    "rolling_std_6",
    "rolling_std_12",
    "rolling_ratio_12",
    "origin_sin_month",
    "origin_cos_month",
)
REGIME_INTERACTION_FEATURE_NAMES: List[str] = list(AR_BASE_FEATURES) + [
    "origin_regime_supercycle_peak_crash",
    "origin_regime_post_supercycle",
    "origin_regime_pandemic_disruption",
    "origin_regime_supercycle_x_rate_diff_1",
    "origin_regime_post_supercycle_x_rate_diff_1",
    "origin_regime_pandemic_x_rate_diff_1",
]

C33_EXPERIMENT_CONFIGS: Dict[str, Dict[str, Any]] = {
    C33_MODEL_PERSISTENCE: {
        "model_id": C33_MODEL_PERSISTENCE,
        "display_name": "M1: Persistence (Operational Baseline)",
        "model_family": "deterministic_baseline",
        "feature_subset_name": "rate_lag_0",
        "features": ["rate_lag_0"],
        "hyperparameters": {},
        "is_experimental": False,
        "uses_origin_regime": False,
    },
    C33_MODEL_RIDGE_AR: {
        "model_id": C33_MODEL_RIDGE_AR,
        "display_name": "C3.2 Reference: Ridge Autoregressive (6 Lags)",
        "model_family": "regularized_linear",
        "feature_subset_name": "autoregressive",
        "features": list(AR_BASE_FEATURES),
        "hyperparameters": {"alpha": 1.0, "random_state": DEFAULT_RANDOM_SEED},
        "is_experimental": True,
        "uses_origin_regime": False,
    },
    C33_MODEL_REGIME_SELECTOR: {
        "model_id": C33_MODEL_REGIME_SELECTOR,
        "display_name": "C3.3 Regime-Conditioned Selector (Persistence vs Ridge AR)",
        "model_family": "regime_conditioned_selector",
        "feature_subset_name": "autoregressive + decision_time_origin_regime",
        "features": list(AR_BASE_FEATURES),
        "hyperparameters": {
            "min_training_samples": DEFAULT_MIN_TRAIN_MONTHS,
            "min_regime_selection_samples": DEFAULT_MIN_REGIME_SELECTION_SAMPLES,
            "ridge_alpha": 1.0,
            "random_state": DEFAULT_RANDOM_SEED,
        },
        "is_experimental": True,
        "uses_origin_regime": True,
    },
    C33_MODEL_REGIME_INTERACTION: {
        "model_id": C33_MODEL_REGIME_INTERACTION,
        "display_name": "C3.3 Regime-Interaction Ridge AR (Regime x rate_diff_1)",
        "model_family": "regime_aware_regularized_linear",
        "feature_subset_name": "autoregressive + origin_regime_x_rate_diff_1",
        "features": list(REGIME_INTERACTION_FEATURE_NAMES),
        "hyperparameters": {"alpha": 1.0, "random_state": DEFAULT_RANDOM_SEED},
        "is_experimental": True,
        "uses_origin_regime": True,
    },
    C33_MODEL_MV_SPREAD: {
        "model_id": C33_MODEL_MV_SPREAD,
        "display_name": "C3.3 Multivariate Ridge: AR + Cross-Corridor & Spread",
        "model_family": "multivariate_regularized_linear",
        "feature_subset_name": "autoregressive_plus_cross_corridor",
        "features": list(MV_SPREAD_FEATURES),
        "hyperparameters": {"alpha": 1.0, "random_state": DEFAULT_RANDOM_SEED},
        "is_experimental": True,
        "uses_origin_regime": False,
    },
    C33_MODEL_MV_VOL_SEASONAL: {
        "model_id": C33_MODEL_MV_VOL_SEASONAL,
        "display_name": "C3.3 Multivariate Ridge: AR + Volatility & Seasonality",
        "model_family": "multivariate_regularized_linear",
        "feature_subset_name": "autoregressive_plus_vol_seasonal",
        "features": list(MV_VOL_SEASONAL_FEATURES),
        "hyperparameters": {"alpha": 1.0, "random_state": DEFAULT_RANDOM_SEED},
        "is_experimental": True,
        "uses_origin_regime": False,
    },
    C33_MODEL_REGIME_PARTITIONED_AR: {
        "model_id": C33_MODEL_REGIME_PARTITIONED_AR,
        "display_name": "C3.3 Non-Fallback Regime-Partitioned Ridge AR",
        "model_family": "regime_partitioned_regularized_linear",
        "feature_subset_name": "autoregressive (same-origin-regime subset)",
        "features": list(AR_BASE_FEATURES),
        "hyperparameters": {
            "alpha": 1.0,
            "min_regime_training_samples": DEFAULT_MIN_TRAIN_MONTHS,
            "random_state": DEFAULT_RANDOM_SEED,
        },
        "is_experimental": True,
        "uses_origin_regime": True,
    },
}

C33_FULL_SAMPLE_MODELS: Tuple[str, ...] = (
    C33_MODEL_PERSISTENCE,
    C33_MODEL_RIDGE_AR,
    C33_MODEL_REGIME_SELECTOR,
    C33_MODEL_REGIME_INTERACTION,
    C33_MODEL_MV_SPREAD,
    C33_MODEL_MV_VOL_SEASONAL,
)


@dataclass(frozen=True)
class C33ExperimentResult:
    """Walk-forward result container for a single Stage C3.3 experiment."""

    model_name: str
    display_name: str
    model_family: str
    feature_subset_name: str
    feature_count: int
    features_used: List[str]
    hyperparameters: Dict[str, Any]
    summary: Optional[ModelEvaluationSummary]
    steps: List[EvaluationStep] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "display_name": self.display_name,
            "model_family": self.model_family,
            "feature_subset_name": self.feature_subset_name,
            "feature_count": self.feature_count,
            "features_used": self.features_used,
            "hyperparameters": self.hyperparameters,
            "summary": self.summary.to_dict() if self.summary else None,
            "steps_count": len(self.steps),
            "diagnostics": self.diagnostics,
        }


def decision_time_regime_id(origin_date: date) -> str:
    """Assign C1.3.2 macroeconomic regime strictly from decision-time origin_date.

    Guarantees zero future target-period leakage: row.regime_id (target-date regime)
    is never consulted during predictor encoding or model selection.
    """
    regime = classify_target_date_regime(origin_date)
    return regime.regime_id if regime else MarketRegimeId.PANDEMIC_DISRUPTION.value


def _extract_regime_interaction_vector(row: ForecastDatasetRow) -> List[float]:
    """Build leakage-free 12-element vector combining 6 AR lags with origin-regime interactions."""
    base = [float(row.features.get(f, 0.0)) for f in AR_BASE_FEATURES]
    lag0 = float(row.features.get("rate_lag_0", 0.0))
    lag1 = float(row.features.get("rate_lag_1", lag0))
    rate_diff_1 = lag0 - lag1

    org_regime = decision_time_regime_id(row.observation_date)
    is_super = 1.0 if org_regime == MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value else 0.0
    is_post = 1.0 if org_regime == MarketRegimeId.POST_SUPERCYCLE.value else 0.0
    is_pand = 1.0 if org_regime == MarketRegimeId.PANDEMIC_DISRUPTION.value else 0.0

    return base + [
        is_super,
        is_post,
        is_pand,
        is_super * rate_diff_1,
        is_post * rate_diff_1,
        is_pand * rate_diff_1,
    ]


def _extract_named_feature_vector(feature_names: Sequence[str], row: ForecastDatasetRow) -> List[float]:
    """Extract float feature list cleanly with NaN/None safety."""
    vec: List[float] = []
    for fname in feature_names:
        val = row.features.get(fname, 0.0)
        vec.append(0.0 if val is None or math.isnan(float(val)) else float(val))
    return vec


def _build_valid_step(
    obs_date: date,
    tgt_date: date,
    actual_val: float,
    origin_val: float,
    pred_raw: float,
    training_scale: Optional[float],
) -> EvaluationStep:
    """Construct a valid clamped EvaluationStep using C3.2 error & directional conventions."""
    pred_clamped = max(float(pred_raw), MIN_TREND_RATE_CLAMP_USD_MT)
    abs_err = round(abs(actual_val - pred_clamped), 4)
    sq_err = round((actual_val - pred_clamped) ** 2, 4)
    pct_err = round((abs_err / actual_val) * 100.0, 4) if actual_val > 0.0 else None
    step_mase = round(abs_err / training_scale, 4) if training_scale and training_scale > 0.0 else None

    actual_change = round(actual_val - origin_val, 4)
    pred_change = round(pred_clamped - origin_val, 4)
    if abs(pred_change) >= 1e-6 and abs(actual_change) >= 1e-6:
        direction_concordant: Optional[bool] = bool((pred_change > 0) == (actual_change > 0))
    else:
        direction_concordant = None

    return EvaluationStep(
        origin_date=obs_date,
        target_date=tgt_date,
        actual_value=actual_val,
        predicted_value=round(pred_clamped, 4),
        absolute_error=abs_err,
        squared_error=sq_err,
        percentage_error=pct_err,
        is_valid=True,
        skip_reason=None,
        training_scale=training_scale,
        step_mase=step_mase,
        actual_change=actual_change,
        predicted_change=pred_change,
        direction_concordant=direction_concordant,
    )


def _build_skipped_step(obs_date: date, tgt_date: date, actual_val: float, reason: str) -> EvaluationStep:
    """Construct a cleanly recorded abstained EvaluationStep."""
    return EvaluationStep(
        origin_date=obs_date,
        target_date=tgt_date,
        actual_value=actual_val,
        predicted_value=None,
        absolute_error=None,
        squared_error=None,
        percentage_error=None,
        is_valid=False,
        skip_reason=reason,
    )


def _summarize_experiment_steps(
    model_name: str,
    route_code: str,
    min_training_samples: int,
    valid_steps: List[EvaluationStep],
    skipped_steps: List[EvaluationStep],
    skipped_reasons: Dict[str, int],
) -> Optional[ModelEvaluationSummary]:
    """Compute aggregate ModelEvaluationSummary over valid & skipped EvaluationSteps."""
    if not valid_steps:
        return None

    actuals = [s.actual_value for s in valid_steps if s.actual_value is not None]
    preds = [s.predicted_value for s in valid_steps if s.predicted_value is not None]
    valid_mases = [s.step_mase for s in valid_steps if s.step_mase is not None]

    flat_pred_count = sum(
        1 for s in valid_steps if s.predicted_change is not None and abs(s.predicted_change) < 1e-6
    )
    act_nonzero_count = sum(
        1 for s in valid_steps if s.actual_change is not None and abs(s.actual_change) >= 1e-6
    )
    eligible_directional = [s for s in valid_steps if s.direction_concordant is not None]
    if eligible_directional:
        concordant_count = sum(1 for s in eligible_directional if bool(s.direction_concordant))
        mda_non_flat: Optional[float] = round((concordant_count / len(eligible_directional)) * 100.0, 2)
    else:
        mda_non_flat = None

    return ModelEvaluationSummary(
        model_type=model_name,
        route_code=route_code,
        evaluation_steps_count=len(valid_steps) + len(skipped_steps),
        burn_in_months=min_training_samples,
        eval_start_date=valid_steps[0].target_date,
        eval_end_date=valid_steps[-1].target_date,
        mae=calculate_mae(actuals, preds),
        rmse=calculate_rmse(actuals, preds),
        mape=calculate_mape(actuals, preds),
        valid_steps_count=len(valid_steps),
        skipped_steps_count=len(skipped_steps),
        skipped_reasons=dict(skipped_reasons),
        mase=round(sum(valid_mases) / len(valid_mases), 2) if valid_mases else None,
        directional_accuracy_non_flat=mda_non_flat,
        flat_prediction_rate=round(flat_pred_count / len(valid_steps), 4),
        actual_non_zero_movement_rate=round(act_nonzero_count / len(valid_steps), 4),
        eligible_directional_steps_count=len(eligible_directional),
        relative_mae_vs_persistence=None,
        relative_rmse_vs_persistence=None,
    )


def _fit_predict_scaled_ridge(
    X_all: np.ndarray,
    y_all: np.ndarray,
    train_indices: List[int],
    test_idx: int,
    alpha: float,
    seed: int,
) -> float:
    """Fit train-only StandardScaler + Ridge regression and return raw point forecast."""
    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_all[train_indices, :])
    X_test = scaler.transform(X_all[test_idx : test_idx + 1, :])
    reg = Ridge(alpha=alpha, random_state=seed)
    reg.fit(X_train, y_all[train_indices])
    return float(reg.predict(X_test)[0])


def _eligible_training_indices(
    labeled_rows: List[ForecastDatasetRow],
    idx: int,
    same_origin_regime: bool = False,
) -> List[int]:
    """Return strictly leakage-free historical training indices available at decision_date_i."""
    cur_row = labeled_rows[idx]
    cur_org_regime = decision_time_regime_id(cur_row.observation_date) if same_origin_regime else None
    indices: List[int] = []
    for j in range(idx):
        cand = labeled_rows[j]
        if cand.target_date <= cur_row.observation_date and cand.target_availability_date <= cur_row.decision_date:
            if not same_origin_regime or decision_time_regime_id(cand.observation_date) == cur_org_regime:
                indices.append(j)
    return indices


def assess_multivariate_feasibility(db: Optional[Session] = None) -> Dict[str, Any]:
    """Compile empirical historical feasibility inventory across all Pelagos domains and C3.1 features.

    Reads live database counts from pelagos.db without writing or fabricating data.
    """
    from pelagos.database import SessionLocal
    from pelagos.models import (
        BunkerPriceObservation,
        EnvironmentalObservation,
        FreightObservation,
        TradeObservation,
        VesselPosition,
    )

    session = db or SessionLocal()
    try:
        inventory = generate_feature_inventory(db=session)
        ais_count = session.query(VesselPosition).count()
        trade_count = session.query(TradeObservation).count()
        env_count = session.query(EnvironmentalObservation).count()
        bunker_count = session.query(BunkerPriceObservation).count()
        freight_count = session.query(FreightObservation).count()

        usable_groups = {
            "autoregressive_lag": [n for n, d in FEATURE_CATALOG.items() if d.is_usable_historically and d.category == "autoregressive_lag"],
            "momentum": [n for n, d in FEATURE_CATALOG.items() if d.is_usable_historically and d.category == "momentum"],
            "rolling_statistic": [n for n, d in FEATURE_CATALOG.items() if d.is_usable_historically and d.category == "rolling_statistic"],
            "seasonality": [n for n, d in FEATURE_CATALOG.items() if d.is_usable_historically and d.category == "seasonality"],
            "regime": [n for n, d in FEATURE_CATALOG.items() if d.is_usable_historically and d.category == "regime"],
            "cross_corridor": [n for n, d in FEATURE_CATALOG.items() if d.is_usable_historically and d.category == "cross_corridor"],
            "route_metadata": [n for n, d in FEATURE_CATALOG.items() if d.is_usable_historically and d.category == "route_metadata"],
        }

        excluded_external = [
            {
                "domain": "trade_observations",
                "database_records": trade_count,
                "historical_timeline_months": 370,
                "covered_months": 3,
                "missingness_pct": 99.1,
                "feasible_for_historical_backtest": False,
                "reason": "Covers only Oct-Dec 2024 (8 USDA FAS GATS + 5 Indian DGCI&S); 1996-Sept 2024 absent.",
                "future_data_requirement": "Multi-decade monthly bilateral bulk grain export volume archive (1996-present) aligned to post-month-end release dates.",
            },
            {
                "domain": "environmental_observations",
                "database_records": env_count,
                "historical_timeline_months": 370,
                "covered_months": 2,
                "missingness_pct": 99.9,
                "feasible_for_historical_backtest": False,
                "reason": "Isolated moored-buoy snapshots (46041: 6, 42001: 5, AD01: 2) in Oct 2024 and Oct 2026.",
                "future_data_requirement": "Continuous monthly aggregated North Pacific & Gulf weather/wave indices over 1996-present.",
            },
            {
                "domain": "bunker_price_observations",
                "database_records": bunker_count,
                "historical_timeline_months": 370,
                "covered_months": 1,
                "missingness_pct": 100.0,
                "feasible_for_historical_backtest": False,
                "reason": "Only 5 single-day spot quotes (2026-11-06) at ARBUE/DKSKA; core corridor ports (Seattle, Houston, Tokyo Bay) UNAVAILABLE.",
                "future_data_requirement": "Authentic historical monthly VLSFO/IFO380 bunker series for Pacific Northwest, U.S. Gulf, and Japanese hubs.",
            },
            {
                "domain": "vessel_positions",
                "database_records": ais_count,
                "historical_timeline_months": 370,
                "covered_months": 1,
                "missingness_pct": 99.7,
                "feasible_for_historical_backtest": False,
                "reason": "Single historical 48h/10s coastal AIS sample (2024-01-01/Oct 2024); zero multi-year congestion time series.",
                "future_data_requirement": "Multi-year monthly port-congestion / bulk carrier queue-time indices for PNW and USG terminals.",
            },
        ]

        return {
            "database_counts": {
                "freight_observations": freight_count,
                "trade_observations": trade_count,
                "environmental_observations": env_count,
                "bunker_price_observations": bunker_count,
                "vessel_positions": ais_count,
            },
            "domain_inventory": inventory,
            "usable_c31_feature_groups": usable_groups,
            "total_usable_c31_features": sum(len(v) for v in usable_groups.values()),
            "excluded_external_domains": excluded_external,
        }
    finally:
        if db is None:
            session.close()


def audit_regime_sample_availability(
    dataset: ForecastDataset,
    min_regime_samples: int = DEFAULT_MIN_TRAIN_MONTHS,
) -> Dict[str, Any]:
    """Audit chronological decision-time regime sample availability and transition mismatches."""
    labeled_rows = sorted(
        dataset.labeled_rows,
        key=lambda r: (r.observation_date, r.decision_date, r.target_date),
    )

    origin_counts: Dict[str, int] = {r.regime_id: 0 for r in HISTORICAL_REGIMES}
    target_counts: Dict[str, int] = {r.regime_id: 0 for r in HISTORICAL_REGIMES}
    max_same_origin_train: Dict[str, int] = {r.regime_id: 0 for r in HISTORICAL_REGIMES}
    trainable_steps_by_origin: Dict[str, int] = {r.regime_id: 0 for r in HISTORICAL_REGIMES}
    transition_mismatch_count = 0

    for i, row in enumerate(labeled_rows):
        org_reg = decision_time_regime_id(row.observation_date)
        tgt_reg = row.regime_id
        origin_counts[org_reg] = origin_counts.get(org_reg, 0) + 1
        target_counts[tgt_reg] = target_counts.get(tgt_reg, 0) + 1
        if org_reg != tgt_reg:
            transition_mismatch_count += 1

        same_tr = _eligible_training_indices(labeled_rows, i, same_origin_regime=True)
        if len(same_tr) > max_same_origin_train[org_reg]:
            max_same_origin_train[org_reg] = len(same_tr)
        if len(same_tr) >= min_regime_samples:
            trainable_steps_by_origin[org_reg] += 1

    insufficient_regimes = [
        r.regime_id
        for r in HISTORICAL_REGIMES
        if trainable_steps_by_origin[r.regime_id] == 0
    ]

    return {
        "route_code": dataset.route_code,
        "horizon_months": dataset.horizon_months,
        "total_labeled_rows": len(labeled_rows),
        "min_regime_training_samples": min_regime_samples,
        "origin_vs_target_regime_mismatches": transition_mismatch_count,
        "origin_regime_counts": origin_counts,
        "target_regime_counts": target_counts,
        "max_eligible_same_origin_train_pairs": max_same_origin_train,
        "trainable_steps_at_threshold": trainable_steps_by_origin,
        "completely_insufficient_regimes": insufficient_regimes,
    }


def _run_ridge_walk_forward(
    dataset: ForecastDataset,
    model_name: str,
    extractor: Callable[[ForecastDatasetRow], List[float]],
    min_training_samples: int = DEFAULT_MIN_TRAIN_MONTHS,
    alpha: float = 1.0,
    seed: int = DEFAULT_RANDOM_SEED,
    same_origin_regime: bool = False,
) -> C33ExperimentResult:
    """Shared leakage-free walk-forward runner for C3.3 regularized linear variants."""
    config = C33_EXPERIMENT_CONFIGS[model_name]
    labeled_rows = sorted(
        dataset.labeled_rows,
        key=lambda r: (r.observation_date, r.decision_date, r.target_date),
    )
    if not labeled_rows:
        raise ValueError(f"Forecast dataset for {dataset.route_code} horizon {dataset.horizon_months} has no labeled rows.")

    X_all = np.array([extractor(r) for r in labeled_rows], dtype=np.float64)
    y_all = np.array([float(r.target_value) for r in labeled_rows], dtype=np.float64)

    valid_steps: List[EvaluationStep] = []
    skipped_steps: List[EvaluationStep] = []
    skipped_reasons: Dict[str, int] = defaultdict(int)
    per_origin_regime_valid: Dict[str, int] = {r.regime_id: 0 for r in HISTORICAL_REGIMES}
    per_origin_regime_skipped: Dict[str, int] = {r.regime_id: 0 for r in HISTORICAL_REGIMES}

    skip_label = (
        f"insufficient_regime_training_history (<{min_training_samples})"
        if same_origin_regime
        else f"insufficient_training_history (<{min_training_samples})"
    )

    for i, row in enumerate(labeled_rows):
        org_regime = decision_time_regime_id(row.observation_date)
        train_indices = _eligible_training_indices(labeled_rows, i, same_origin_regime=same_origin_regime)
        actual_val = float(row.target_value)
        origin_val = float(row.features.get("rate_lag_0", 0.0))

        if len(train_indices) < min_training_samples:
            skipped_reasons[skip_label] += 1
            per_origin_regime_skipped[org_regime] += 1
            skipped_steps.append(_build_skipped_step(row.observation_date, row.target_date, actual_val, skip_label))
            continue

        train_pairs = sorted(
            [(labeled_rows[j].target_date, float(labeled_rows[j].target_value)) for j in train_indices],
            key=lambda x: x[0],
        )
        training_scale = calculate_training_scale(train_pairs)
        pred_raw = _fit_predict_scaled_ridge(X_all, y_all, train_indices, i, alpha=alpha, seed=seed)

        per_origin_regime_valid[org_regime] += 1
        valid_steps.append(
            _build_valid_step(
                obs_date=row.observation_date,
                tgt_date=row.target_date,
                actual_val=actual_val,
                origin_val=origin_val,
                pred_raw=pred_raw,
                training_scale=training_scale,
            )
        )

    all_steps_sorted = sorted(valid_steps + skipped_steps, key=lambda s: s.origin_date)
    summary = _summarize_experiment_steps(
        model_name=model_name,
        route_code=dataset.route_code,
        min_training_samples=min_training_samples,
        valid_steps=valid_steps,
        skipped_steps=skipped_steps,
        skipped_reasons=dict(skipped_reasons),
    )

    diagnostics: Dict[str, Any] = {}
    if same_origin_regime:
        diagnostics = {
            "origin_regime_valid_counts": per_origin_regime_valid,
            "origin_regime_skipped_counts": per_origin_regime_skipped,
            "insufficient_regimes": [
                r_id for r_id, cnt in per_origin_regime_valid.items() if cnt == 0
            ],
        }

    return C33ExperimentResult(
        model_name=model_name,
        display_name=config["display_name"],
        model_family=config["model_family"],
        feature_subset_name=config["feature_subset_name"],
        feature_count=len(config["features"]),
        features_used=list(config["features"]),
        hyperparameters=dict(config["hyperparameters"]),
        summary=summary,
        steps=all_steps_sorted,
        diagnostics=diagnostics,
    )


def _run_regime_conditioned_selector(
    dataset: ForecastDataset,
    persistence_result: MLModelBenchmarkResult,
    ridge_ar_result: MLModelBenchmarkResult,
    min_training_samples: int = DEFAULT_MIN_TRAIN_MONTHS,
    min_regime_selection_samples: int = DEFAULT_MIN_REGIME_SELECTION_SAMPLES,
) -> C33ExperimentResult:
    """Walk-forward selector choosing between Persistence and Ridge AR strictly from past in-regime MAE."""
    config = C33_EXPERIMENT_CONFIGS[C33_MODEL_REGIME_SELECTOR]
    labeled_rows = sorted(
        dataset.labeled_rows,
        key=lambda r: (r.observation_date, r.decision_date, r.target_date),
    )

    p_valid_map = {s.target_date: s for s in persistence_result.steps if s.is_valid}
    ar_valid_map = {s.target_date: s for s in ridge_ar_result.steps if s.is_valid}
    p_skip_map = {s.target_date: s for s in persistence_result.steps if not s.is_valid}

    # Build lookup of completed prior steps keyed by origin_regime & realized dates
    prior_evaluated: List[Tuple[date, date, date, str, float, float]] = []
    for r in labeled_rows:
        td = r.target_date
        if td in p_valid_map and td in ar_valid_map:
            p_err = float(p_valid_map[td].absolute_error)
            ar_err = float(ar_valid_map[td].absolute_error)
            prior_evaluated.append(
                (r.target_date, r.target_availability_date, r.observation_date, decision_time_regime_id(r.observation_date), p_err, ar_err)
            )

    valid_steps: List[EvaluationStep] = []
    skipped_steps: List[EvaluationStep] = []
    skipped_reasons: Dict[str, int] = defaultdict(int)
    selection_counts: Dict[str, int] = {C33_MODEL_PERSISTENCE: 0, C33_MODEL_RIDGE_AR: 0}
    selection_by_origin_regime: Dict[str, Dict[str, int]] = {
        rg.regime_id: {C33_MODEL_PERSISTENCE: 0, C33_MODEL_RIDGE_AR: 0}
        for rg in HISTORICAL_REGIMES
    }
    insufficient_val_fallbacks = 0

    for row in labeled_rows:
        td = row.target_date
        if td not in p_valid_map or td not in ar_valid_map:
            skip_s = p_skip_map.get(td)
            reason = skip_s.skip_reason if skip_s and skip_s.skip_reason else f"insufficient_training_history (<{min_training_samples})"
            skipped_reasons[reason] += 1
            skipped_steps.append(_build_skipped_step(row.observation_date, td, float(row.target_value), reason))
            continue

        cur_org_regime = decision_time_regime_id(row.observation_date)
        completed_in_regime = [
            (p_e, ar_e)
            for p_td, p_tav, p_obs, p_reg, p_e, ar_e in prior_evaluated
            if p_obs < row.observation_date
            and p_td <= row.observation_date
            and p_tav <= row.decision_date
            and p_reg == cur_org_regime
        ]

        chosen_model = C33_MODEL_PERSISTENCE
        if len(completed_in_regime) < min_regime_selection_samples:
            insufficient_val_fallbacks += 1
        else:
            mean_p_err = sum(x[0] for x in completed_in_regime) / float(len(completed_in_regime))
            mean_ar_err = sum(x[1] for x in completed_in_regime) / float(len(completed_in_regime))
            if mean_ar_err < mean_p_err:
                chosen_model = C33_MODEL_RIDGE_AR

        selection_counts[chosen_model] += 1
        selection_by_origin_regime[cur_org_regime][chosen_model] += 1
        chosen_step = ar_valid_map[td] if chosen_model == C33_MODEL_RIDGE_AR else p_valid_map[td]
        valid_steps.append(chosen_step)

    all_steps_sorted = sorted(valid_steps + skipped_steps, key=lambda s: s.origin_date)
    summary = _summarize_experiment_steps(
        model_name=C33_MODEL_REGIME_SELECTOR,
        route_code=dataset.route_code,
        min_training_samples=min_training_samples,
        valid_steps=valid_steps,
        skipped_steps=skipped_steps,
        skipped_reasons=dict(skipped_reasons),
    )
    hyperparams = dict(config["hyperparameters"])
    hyperparams["min_training_samples"] = min_training_samples
    hyperparams["min_regime_selection_samples"] = min_regime_selection_samples

    return C33ExperimentResult(
        model_name=C33_MODEL_REGIME_SELECTOR,
        display_name=config["display_name"],
        model_family=config["model_family"],
        feature_subset_name=config["feature_subset_name"],
        feature_count=len(config["features"]),
        features_used=list(config["features"]),
        hyperparameters=hyperparams,
        summary=summary,
        steps=all_steps_sorted,
        diagnostics={
            "selection_counts": selection_counts,
            "selection_by_origin_regime": selection_by_origin_regime,
            "insufficient_regime_validation_fallbacks": insufficient_val_fallbacks,
        },
    )


def evaluate_regime_aware_experiment(
    dataset: ForecastDataset,
    experiment_name: str,
    min_training_samples: int = DEFAULT_MIN_TRAIN_MONTHS,
    min_regime_selection_samples: int = DEFAULT_MIN_REGIME_SELECTION_SAMPLES,
    seed: int = DEFAULT_RANDOM_SEED,
    cached_persistence: Optional[MLModelBenchmarkResult] = None,
    cached_ridge_ar: Optional[MLModelBenchmarkResult] = None,
) -> C33ExperimentResult:
    """Execute a single Stage C3.3 experiment cleanly on a strict-forward ForecastDataset."""
    if experiment_name not in C33_EXPERIMENT_CONFIGS:
        raise ValueError(
            f"Unknown C3.3 experiment '{experiment_name}'. Available: {list(C33_EXPERIMENT_CONFIGS.keys())}"
        )

    if experiment_name in (C33_MODEL_PERSISTENCE, C33_MODEL_RIDGE_AR):
        base_res = evaluate_ml_walk_forward(
            dataset=dataset,
            model_name=experiment_name,
            min_training_samples=min_training_samples,
            seed=seed,
        )
        cfg = C33_EXPERIMENT_CONFIGS[experiment_name]
        return C33ExperimentResult(
            model_name=experiment_name,
            display_name=cfg["display_name"],
            model_family=base_res.model_family,
            feature_subset_name=base_res.feature_subset_name,
            feature_count=base_res.feature_count,
            features_used=list(base_res.features_used),
            hyperparameters=dict(base_res.hyperparameters),
            summary=base_res.summary,
            steps=list(base_res.steps),
        )

    if experiment_name == C33_MODEL_REGIME_SELECTOR:
        p_res = cached_persistence or evaluate_ml_walk_forward(
            dataset=dataset, model_name=C33_MODEL_PERSISTENCE, min_training_samples=min_training_samples, seed=seed
        )
        ar_res = cached_ridge_ar or evaluate_ml_walk_forward(
            dataset=dataset, model_name=C33_MODEL_RIDGE_AR, min_training_samples=min_training_samples, seed=seed
        )
        return _run_regime_conditioned_selector(
            dataset=dataset,
            persistence_result=p_res,
            ridge_ar_result=ar_res,
            min_training_samples=min_training_samples,
            min_regime_selection_samples=min_regime_selection_samples,
        )

    if experiment_name == C33_MODEL_REGIME_INTERACTION:
        return _run_ridge_walk_forward(
            dataset=dataset,
            model_name=experiment_name,
            extractor=_extract_regime_interaction_vector,
            min_training_samples=min_training_samples,
            alpha=1.0,
            seed=seed,
            same_origin_regime=False,
        )

    if experiment_name == C33_MODEL_MV_SPREAD:
        return _run_ridge_walk_forward(
            dataset=dataset,
            model_name=experiment_name,
            extractor=lambda row: _extract_named_feature_vector(MV_SPREAD_FEATURES, row),
            min_training_samples=min_training_samples,
            alpha=1.0,
            seed=seed,
            same_origin_regime=False,
        )

    if experiment_name == C33_MODEL_MV_VOL_SEASONAL:
        return _run_ridge_walk_forward(
            dataset=dataset,
            model_name=experiment_name,
            extractor=lambda row: _extract_named_feature_vector(MV_VOL_SEASONAL_FEATURES, row),
            min_training_samples=min_training_samples,
            alpha=1.0,
            seed=seed,
            same_origin_regime=False,
        )

    if experiment_name == C33_MODEL_REGIME_PARTITIONED_AR:
        return _run_ridge_walk_forward(
            dataset=dataset,
            model_name=experiment_name,
            extractor=lambda row: _extract_named_feature_vector(AR_BASE_FEATURES, row),
            min_training_samples=min_training_samples,
            alpha=1.0,
            seed=seed,
            same_origin_regime=True,
        )

    raise ValueError(f"Unhandled C3.3 experiment: {experiment_name}")


def _evaluate_common_intersection(
    model_valid_steps: Dict[str, List[EvaluationStep]],
    model_order: Sequence[str],
    route_code: str,
    horizon: int,
    seed: int,
) -> Dict[str, Any]:
    """Compare models strictly over identical target_date intersection with DM/HLN, Bootstrap, and Holm."""
    common_dates: Optional[set] = None
    for m_name in model_order:
        steps = model_valid_steps.get(m_name, [])
        td_set = {s.target_date for s in steps}
        common_dates = td_set if common_dates is None else common_dates.intersection(td_set)

    common_dates_sorted = sorted(common_dates) if common_dates else []
    common_N = len(common_dates_sorted)
    common_set = set(common_dates_sorted)

    common_summaries: Dict[str, Dict[str, Any]] = {}
    p_mae_common: Optional[float] = None
    p_rmse_common: Optional[float] = None

    if common_N > 0 and C33_MODEL_PERSISTENCE in model_valid_steps:
        p_map = {s.target_date: s for s in model_valid_steps[C33_MODEL_PERSISTENCE]}
        p_steps_common = [p_map[d] for d in common_dates_sorted]
        p_mae_common = calculate_mae([s.actual_value for s in p_steps_common], [s.predicted_value for s in p_steps_common])
        p_rmse_common = calculate_rmse([s.actual_value for s in p_steps_common], [s.predicted_value for s in p_steps_common])

    for m_name in model_order:
        if common_N == 0 or m_name not in model_valid_steps:
            continue
        s_map = {s.target_date: s for s in model_valid_steps[m_name]}
        c_steps = [s_map[d] for d in common_dates_sorted]
        acts = [s.actual_value for s in c_steps]
        preds = [s.predicted_value for s in c_steps]

        mae = calculate_mae(acts, preds)
        rmse = calculate_rmse(acts, preds)
        mape = calculate_mape(acts, preds)
        mases = [s.step_mase for s in c_steps if s.step_mase is not None]
        mase = round(sum(mases) / len(mases), 2) if mases else None

        dir_steps = [s for s in c_steps if s.direction_concordant is not None]
        if dir_steps:
            mda = round((sum(1 for s in dir_steps if bool(s.direction_concordant)) / len(dir_steps)) * 100.0, 2)
        else:
            mda = None

        common_summaries[m_name] = {
            "model_name": m_name,
            "sample_size": common_N,
            "mae": mae,
            "rmse": rmse,
            "mape": mape,
            "mase": mase,
            "directional_accuracy_non_flat": mda,
            "relative_mae_ratio": round(mae / p_mae_common, 2) if p_mae_common and p_mae_common > 0 else None,
            "relative_mae_percent_change": round(((mae - p_mae_common) / p_mae_common) * 100.0, 2) if p_mae_common and p_mae_common > 0 else None,
            "relative_rmse_ratio": round(rmse / p_rmse_common, 2) if p_rmse_common and p_rmse_common > 0 else None,
        }

    stat_comparisons: Dict[str, Dict[str, Any]] = {}
    p_steps = [s for s in model_valid_steps.get(C33_MODEL_PERSISTENCE, []) if s.target_date in common_set]
    cand_names: List[str] = []
    raw_p_vals: List[float] = []

    for m_name in model_order:
        if m_name == C33_MODEL_PERSISTENCE or common_N == 0:
            continue
        c_steps = [s for s in model_valid_steps.get(m_name, []) if s.target_date in common_set]
        comp_res = compare_candidate_to_baseline(
            baseline_steps=p_steps,
            candidate_steps=c_steps,
            model_b=m_name,
            route_code=route_code,
            horizon_months=horizon,
            model_a=C33_MODEL_PERSISTENCE,
            loss_type="absolute",
            seed=seed,
        )
        stat_comparisons[m_name] = comp_res.to_dict()
        cand_names.append(m_name)
        raw_p_vals.append(comp_res.p_value)

    if raw_p_vals:
        adj_ps = holm_bonferroni_step_down(raw_p_vals)
        for c_name, adj_p in zip(cand_names, adj_ps):
            stat_comparisons[c_name]["holm_adjusted_p_value"] = float(adj_p)
            stat_comparisons[c_name]["holm_statistically_significant"] = bool(adj_p < 0.05)

    # Target-date regime breakdown over exact common sample
    regime_breakdown: Dict[str, Dict[str, Any]] = {}
    for reg in HISTORICAL_REGIMES:
        r_dict: Dict[str, Any] = {
            "regime_id": reg.regime_id,
            "display_name": reg.display_name,
            "start_date": reg.start_date.isoformat(),
            "end_date": reg.end_date.isoformat(),
            "models": {},
            "statistical_comparisons": {},
        }
        reg_p_steps = [s for s in p_steps if reg.contains_target_date(s.target_date)]
        for m_name in model_order:
            common_m_steps = [s for s in model_valid_steps.get(m_name, []) if s.target_date in common_set]
            r_dict["models"][m_name] = compute_regime_metrics(reg, common_m_steps).to_dict()
            if m_name != C33_MODEL_PERSISTENCE:
                reg_c_steps = [s for s in common_m_steps if reg.contains_target_date(s.target_date)]
                r_dict["statistical_comparisons"][m_name] = compare_candidate_to_baseline(
                    baseline_steps=reg_p_steps,
                    candidate_steps=reg_c_steps,
                    model_b=m_name,
                    route_code=route_code,
                    horizon_months=horizon,
                    model_a=C33_MODEL_PERSISTENCE,
                    loss_type="absolute",
                    seed=seed,
                    regime_id=reg.regime_id,
                ).to_dict()
        regime_breakdown[reg.regime_id] = r_dict

    return {
        "common_sample_comparison": {
            "common_sample_size": common_N,
            "common_start_date": common_dates_sorted[0].isoformat() if common_dates_sorted else None,
            "common_end_date": common_dates_sorted[-1].isoformat() if common_dates_sorted else None,
            "models": common_summaries,
        },
        "statistical_comparisons": stat_comparisons,
        "regime_breakdown": regime_breakdown,
    }


def benchmark_route_c33_experiments(
    db: Session,
    route_code: str,
    horizon: int = 1,
    min_training_samples: int = DEFAULT_MIN_TRAIN_MONTHS,
    min_regime_selection_samples: int = DEFAULT_MIN_REGIME_SELECTION_SAMPLES,
    seed: int = DEFAULT_RANDOM_SEED,
) -> Dict[str, Any]:
    """Run all Stage C3.3 regime-aware and controlled multivariate experiments for one route/horizon."""
    if route_code in DISALLOWED_DERIVED_ROUTES:
        raise ValueError(f"Route '{route_code}' is a derived spread indicator and cannot be evaluated directly.")
    if route_code not in SUPPORTED_DIRECT_ROUTES:
        raise ValueError(f"Route '{route_code}' is not supported. Supported routes: {list(SUPPORTED_DIRECT_ROUTES)}")

    dataset = build_forecast_dataset(db, route_code=route_code, horizon_months=horizon)
    regime_audit = audit_regime_sample_availability(dataset, min_regime_samples=min_training_samples)

    p_cached = evaluate_ml_walk_forward(
        dataset=dataset, model_name=C33_MODEL_PERSISTENCE, min_training_samples=min_training_samples, seed=seed
    )
    ar_cached = evaluate_ml_walk_forward(
        dataset=dataset, model_name=C33_MODEL_RIDGE_AR, min_training_samples=min_training_samples, seed=seed
    )

    exp_results: Dict[str, C33ExperimentResult] = {}
    valid_steps_map: Dict[str, List[EvaluationStep]] = {}

    for m_name in C33_FULL_SAMPLE_MODELS:
        res = evaluate_regime_aware_experiment(
            dataset=dataset,
            experiment_name=m_name,
            min_training_samples=min_training_samples,
            min_regime_selection_samples=min_regime_selection_samples,
            seed=seed,
            cached_persistence=p_cached,
            cached_ridge_ar=ar_cached,
        )
        exp_results[m_name] = res
        valid_steps_map[m_name] = [s for s in res.steps if s.is_valid]

    full_comparison = _evaluate_common_intersection(
        model_valid_steps=valid_steps_map,
        model_order=C33_FULL_SAMPLE_MODELS,
        route_code=route_code,
        horizon=horizon,
        seed=seed,
    )

    # Separately evaluate strict non-fallback regime-partitioned Ridge AR on its matched trainable subset
    part_res = evaluate_regime_aware_experiment(
        dataset=dataset,
        experiment_name=C33_MODEL_REGIME_PARTITIONED_AR,
        min_training_samples=min_training_samples,
        seed=seed,
    )
    part_valid = [s for s in part_res.steps if s.is_valid]
    matched_subset_comparison = _evaluate_common_intersection(
        model_valid_steps={
            C33_MODEL_PERSISTENCE: valid_steps_map[C33_MODEL_PERSISTENCE],
            C33_MODEL_RIDGE_AR: valid_steps_map[C33_MODEL_RIDGE_AR],
            C33_MODEL_REGIME_PARTITIONED_AR: part_valid,
        },
        model_order=(C33_MODEL_PERSISTENCE, C33_MODEL_RIDGE_AR, C33_MODEL_REGIME_PARTITIONED_AR),
        route_code=route_code,
        horizon=horizon,
        seed=seed,
    )

    return {
        "route_code": route_code,
        "horizon_months": horizon,
        "total_labeled_rows": len(dataset.labeled_rows),
        "min_training_samples": min_training_samples,
        "min_regime_selection_samples": min_regime_selection_samples,
        "regime_sample_audit": regime_audit,
        "experiments_evaluated": {k: v.to_dict() for k, v in exp_results.items()},
        "common_sample_comparison": full_comparison["common_sample_comparison"],
        "statistical_comparisons": full_comparison["statistical_comparisons"],
        "regime_breakdown": full_comparison["regime_breakdown"],
        "regime_partitioned_analysis": {
            "experiment": part_res.to_dict(),
            "matched_subset_sample_size": matched_subset_comparison["common_sample_comparison"]["common_sample_size"],
            "matched_subset_comparison": matched_subset_comparison["common_sample_comparison"],
            "matched_subset_statistical_comparisons": matched_subset_comparison["statistical_comparisons"],
            "matched_subset_regime_breakdown": matched_subset_comparison["regime_breakdown"],
        },
    }


def execute_comprehensive_c33_investigation(
    db: Session,
    routes: Tuple[str, ...] = SUPPORTED_DIRECT_ROUTES,
    horizons: Tuple[int, ...] = (1, 3, 6),
    min_training_samples: int = DEFAULT_MIN_TRAIN_MONTHS,
    min_regime_selection_samples: int = DEFAULT_MIN_REGIME_SELECTION_SAMPLES,
    seed: int = DEFAULT_RANDOM_SEED,
) -> Dict[str, Any]:
    """Execute complete Stage C3.3 empirical investigation across corridors and horizons."""
    feasibility = assess_multivariate_feasibility(db=db)
    experiments: Dict[str, Any] = {}

    for route in routes:
        for h in horizons:
            key = f"{route}_T{h}"
            logger.info("Executing Stage C3.3 investigation for %s...", key)
            experiments[key] = benchmark_route_c33_experiments(
                db=db,
                route_code=route,
                horizon=h,
                min_training_samples=min_training_samples,
                min_regime_selection_samples=min_regime_selection_samples,
                seed=seed,
            )

    return {
        "metadata": {
            "investigation_stage": "Stage C3.3 Regime-Aware & Controlled Multivariate Forecasting Investigation",
            "evaluated_routes": list(routes),
            "evaluated_horizons": list(horizons),
            "min_training_samples": min_training_samples,
            "min_regime_selection_samples": min_regime_selection_samples,
            "random_seed": seed,
            "production_baseline": C33_MODEL_PERSISTENCE,
            "experiment_configs": C33_EXPERIMENT_CONFIGS,
        },
        "multivariate_feasibility": feasibility,
        "experiments": experiments,
    }

