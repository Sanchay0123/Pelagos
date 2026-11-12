"""
Pelagos Feature-Based Candidate ML Model Benchmarking Engine.

Executes rigorous, leakage-free empirical walk-forward benchmarking of feature-driven
machine learning models against the operational Persistence (M1) baseline:
- M1: Persistence (Operational Baseline: y_{t+h|t} = y_t)
- M2: Ridge Regression (L2 Regularized Linear Model on linear_non_redundant features)
- M3: Lasso Regression (L1 Sparse Feature Selection on linear_non_redundant features)
- M4: Random Forest Regressor (Nonlinear Ensemble on tree_based features)
- M5: LightGBM Regressor (Gradient-Boosted Trees, conditional on dependency availability)
- Ablation: Ridge Autoregressive (L2 Regularized Linear Model on pure autoregressive lags)

Evaluates performance across:
- Monitored grain corridors: USG-JPN-BULK and PNW-JPN-BULK
- Forecast horizons: T+1, T+3, and T+6 months
- Expanding chronological walk-forward training windows (minimum 36 months)
- Preprocessing isolation: StandardScaler fitted strictly on training rows at each step
- Economic physical rate clamping (predictions >= 0.10 USD/MT)
- Statistical hypothesis testing: Diebold-Mariano with Harvey-Leybourne-Newbold (HLN 1997)
  small-sample adjustment, Moving Block Bootstrap 95% CIs, and Holm-Bonferroni FWER step-down
- Canonical C1.3.2 macroeconomic market regimes (PRE_SUPERCYCLE, SUPERCYCLE_PEAK_CRASH,
  POST_SUPERCYCLE, PANDEMIC_DISRUPTION)

Traces to REQ-FR-FRF-001, ML-001 Section 5.3, DATA-001 Section 5.2, and Stage C3.2.
"""

from __future__ import annotations

import logging
import math
import warnings
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy.orm import Session

# Scikit-Learn components
from sklearn.linear_model import Lasso, Ridge
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import StandardScaler

# LightGBM conditional availability check
try:
    import lightgbm as lgb
    LIGHTGBM_AVAILABLE = True
except ImportError:
    LIGHTGBM_AVAILABLE = False

from pelagos.forecasting.schemas import (
    BaselineModelType,
    EvaluationStep,
    ModelEvaluationSummary,
    CommonSampleComparisonReport,
    SUPPORTED_DIRECT_ROUTES,
    DISALLOWED_DERIVED_ROUTES,
)
from pelagos.forecasting.baselines import MIN_TREND_RATE_CLAMP_USD_MT
from pelagos.forecasting.evaluation import (
    calculate_mae,
    calculate_rmse,
    calculate_mape,
    calculate_training_scale,
)
from pelagos.forecasting.regimes import (
    HISTORICAL_REGIMES,
    MarketRegimeDefinition,
    MarketRegimeId,
    compute_regime_metrics,
)
from pelagos.forecasting.statistical_testing import (
    compare_candidate_to_baseline,
    holm_bonferroni_step_down,
    StatisticalComparisonResult,
    RESEARCH_CLASSIFICATION_PROMISING,
    RESEARCH_CLASSIFICATION_INCONCLUSIVE,
    RESEARCH_CLASSIFICATION_NOT_BETTER,
)
from pelagos.forecasting.features import FEATURE_SUBSETS
from pelagos.forecasting.dataset import (
    ForecastDataset,
    ForecastDatasetRow,
    build_forecast_dataset,
)

logger = logging.getLogger(__name__)

# Default walk-forward parameters
DEFAULT_MIN_TRAIN_MONTHS = 36  # 3-year minimum history before first out-of-sample prediction
DEFAULT_RANDOM_SEED = 42

# Candidate model identifiers
ML_MODEL_PERSISTENCE = "persistence"
ML_MODEL_RIDGE = "ridge"
ML_MODEL_LASSO = "lasso"
ML_MODEL_RANDOM_FOREST = "random_forest"
ML_MODEL_LIGHTGBM = "lightgbm"
ML_MODEL_RIDGE_AR = "ridge_autoregressive"

# Authoritative Candidate Model Registry & Metadata
ML_CANDIDATE_CONFIGS: Dict[str, Dict[str, Any]] = {
    ML_MODEL_PERSISTENCE: {
        "model_id": ML_MODEL_PERSISTENCE,
        "display_name": "M1: Persistence (Operational Baseline)",
        "model_family": "deterministic_baseline",
        "feature_subset_name": "rate_lag_0",
        "description": "Deterministic operational baseline predicting y_{t+h|t} = y_t directly from rate_lag_0.",
        "hyperparameters": {},
        "requires_scaling": False,
        "is_experimental": False,
    },
    ML_MODEL_RIDGE: {
        "model_id": ML_MODEL_RIDGE,
        "display_name": "M2: Ridge Regression (L2 Regularized)",
        "model_family": "regularized_linear",
        "feature_subset_name": "linear_non_redundant",
        "description": "L2-regularized linear regression on 13 non-collinear features (rate lags, volatility, harmonic seasonality, cross-corridor spread, 3 regime flags).",
        "hyperparameters": {"alpha": 1.0, "random_state": DEFAULT_RANDOM_SEED},
        "requires_scaling": True,
        "is_experimental": True,
    },
    ML_MODEL_LASSO: {
        "model_id": ML_MODEL_LASSO,
        "display_name": "M3: Lasso Regression (L1 Feature Selection)",
        "model_family": "sparse_linear",
        "feature_subset_name": "linear_non_redundant",
        "description": "L1-regularized sparse linear regression performing dynamic feature selection on 13 non-collinear features.",
        "hyperparameters": {"alpha": 0.1, "max_iter": 2000, "random_state": DEFAULT_RANDOM_SEED},
        "requires_scaling": True,
        "is_experimental": True,
    },
    ML_MODEL_RANDOM_FOREST: {
        "model_id": ML_MODEL_RANDOM_FOREST,
        "display_name": "M4: Random Forest Regressor",
        "model_family": "tree_ensemble",
        "feature_subset_name": "tree_based",
        "description": "Nonlinear bagging ensemble of 50 decision trees (max_depth=5, min_samples_split=4) on 25 tree-appropriate features.",
        "hyperparameters": {
            "n_estimators": 50,
            "max_depth": 5,
            "min_samples_split": 4,
            "random_state": DEFAULT_RANDOM_SEED,
            "n_jobs": 1,
        },
        "requires_scaling": False,
        "is_experimental": True,
    },
    ML_MODEL_LIGHTGBM: {
        "model_id": ML_MODEL_LIGHTGBM,
        "display_name": "M5: LightGBM Regressor (GBDT)",
        "model_family": "gradient_boosted_trees",
        "feature_subset_name": "tree_based",
        "description": "Gradient-boosted decision trees (GBDT) on 25 tree-appropriate features (conditional on library availability).",
        "hyperparameters": {
            "n_estimators": 100,
            "max_depth": 4,
            "learning_rate": 0.05,
            "random_state": DEFAULT_RANDOM_SEED,
            "verbose": -1,
        },
        "requires_scaling": False,
        "is_experimental": True,
    },
    ML_MODEL_RIDGE_AR: {
        "model_id": ML_MODEL_RIDGE_AR,
        "display_name": "Ablation: Ridge Autoregressive (Lags Only)",
        "model_family": "regularized_linear",
        "feature_subset_name": "autoregressive",
        "description": "L2-regularized linear regression restricted exclusively to 6 autoregressive rate lags (lags 0, 1, 2, 3, 6, 12).",
        "hyperparameters": {"alpha": 1.0, "random_state": DEFAULT_RANDOM_SEED},
        "requires_scaling": True,
        "is_experimental": True,
    },
}

STANDARD_ML_BENCHMARK_MODELS = (
    ML_MODEL_PERSISTENCE,
    ML_MODEL_RIDGE,
    ML_MODEL_LASSO,
    ML_MODEL_RANDOM_FOREST,
    ML_MODEL_LIGHTGBM,
    ML_MODEL_RIDGE_AR,
)


@dataclass(frozen=True)
class MLModelBenchmarkResult:
    """Detailed benchmark evaluation result for a single candidate ML model."""
    model_name: str
    display_name: str
    model_family: str
    feature_subset_name: str
    feature_count: int
    features_used: List[str]
    hyperparameters: Dict[str, Any]
    is_available: bool
    unavailability_reason: Optional[str] = None
    summary: Optional[ModelEvaluationSummary] = None
    steps: List[EvaluationStep] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "display_name": self.display_name,
            "model_family": self.model_family,
            "feature_subset_name": self.feature_subset_name,
            "feature_count": self.feature_count,
            "features_used": self.features_used,
            "hyperparameters": self.hyperparameters,
            "is_available": self.is_available,
            "unavailability_reason": self.unavailability_reason,
            "summary": self.summary.to_dict() if self.summary else None,
            "steps_count": len(self.steps),
        }


def _extract_feature_matrix(
    rows: List[ForecastDatasetRow],
    feature_names: List[str],
) -> np.ndarray:
    """Extracts contiguous 2D float64 numpy array for specified features."""
    matrix = np.empty((len(rows), len(feature_names)), dtype=np.float64)
    for i, row in enumerate(rows):
        for j, feat_name in enumerate(feature_names):
            val = row.features.get(feat_name, 0.0)
            matrix[i, j] = 0.0 if val is None or math.isnan(val) else float(val)
    return matrix


def evaluate_ml_walk_forward(
    dataset: ForecastDataset,
    model_name: str,
    feature_subset_name: Optional[str] = None,
    min_training_samples: int = DEFAULT_MIN_TRAIN_MONTHS,
    seed: int = DEFAULT_RANDOM_SEED,
) -> MLModelBenchmarkResult:
    """
    Executes an expanding chronological walk-forward out-of-sample backtest for an ML model.

    Strict Leakage Controls:
    1. Chronological Ordering: Evaluation proceeds row-by-row in chronological order of observation_date.
    2. Strict Forward Horizon Eligibility: At forecast origin i with observation_date T_i, a historical
       sample j is eligible for the training set if and only if:
           row_j.target_date <= row_i.observation_date
       Because row_j.target_availability_date <= row_i.decision_date under strict-forward semantics,
       this guarantees zero lookahead of target realization or post-origin publication data.
    3. Preprocessing Isolation: Any feature transformation (e.g., StandardScaler) is fitted strictly
       on X_train at each step, and applied to X_test with zero exposure to future samples.
    4. Physical Clamping: Predictions are bounded below by MIN_TREND_RATE_CLAMP_USD_MT (0.10 USD/MT).
    5. Minimum Sample Threshold: Origins with fewer than min_training_samples eligible historical
       training pairs are recorded as skipped.
    """
    config = ML_CANDIDATE_CONFIGS.get(model_name)
    if not config:
        raise ValueError(f"Unknown ML candidate model: '{model_name}'. Available: {list(ML_CANDIDATE_CONFIGS.keys())}")

    feat_subset = feature_subset_name or config["feature_subset_name"]
    display_name = config["display_name"]
    model_family = config["model_family"]
    hyperparams = dict(config["hyperparameters"])

    # Resolve features
    if feat_subset == "rate_lag_0":
        feature_names = ["rate_lag_0"]
    else:
        if feat_subset not in FEATURE_SUBSETS:
            raise ValueError(f"Feature subset '{feat_subset}' not defined in FEATURE_SUBSETS")
        feature_names = list(FEATURE_SUBSETS[feat_subset])

    # Check dependency availability
    if model_name == ML_MODEL_LIGHTGBM and not LIGHTGBM_AVAILABLE:
        return MLModelBenchmarkResult(
            model_name=model_name,
            display_name=display_name,
            model_family=model_family,
            feature_subset_name=feat_subset,
            feature_count=len(feature_names),
            features_used=feature_names,
            hyperparameters=hyperparams,
            is_available=False,
            unavailability_reason="UNAVAILABLE (dependency lightgbm not installed)",
            summary=None,
            steps=[],
        )

    labeled_rows = sorted(
        dataset.labeled_rows,
        key=lambda r: (r.observation_date, r.decision_date, r.target_date),
    )
    if not labeled_rows:
        raise ValueError(f"Forecast dataset for {dataset.route_code} horizon {dataset.horizon_months} has no labeled rows.")

    # Pre-extract data matrices for vectorization efficiency
    obs_dates = [r.observation_date for r in labeled_rows]
    decision_dates = [r.decision_date for r in labeled_rows]
    target_dates = [r.target_date for r in labeled_rows]
    target_avail_dates = [r.target_availability_date for r in labeled_rows]
    actual_targets = np.array([r.target_value for r in labeled_rows], dtype=np.float64)
    origin_rates = np.array([float(r.features.get("rate_lag_0", 0.0)) for r in labeled_rows], dtype=np.float64)

    if model_name != ML_MODEL_PERSISTENCE:
        X_all = _extract_feature_matrix(labeled_rows, feature_names)
    else:
        X_all = None

    valid_steps: List[EvaluationStep] = []
    skipped_steps: List[EvaluationStep] = []
    skipped_reasons: Dict[str, int] = defaultdict(int)

    # Expanding Walk-Forward Backtesting Loop
    for i, row in enumerate(labeled_rows):
        cur_obs_date = obs_dates[i]
        cur_dec_date = decision_dates[i]
        cur_target_date = target_dates[i]
        actual_val = actual_targets[i]
        origin_val = origin_rates[i]

        # Eligible training indices: target must be observed on/before origin AND published/available
        # on/before the current decision date (decision_timestamp is 23:59:59 UTC, strictly after Day 14 release).
        train_indices = [
            j
            for j in range(i)
            if target_dates[j] <= cur_obs_date and target_avail_dates[j] <= cur_dec_date
        ]

        if len(train_indices) < min_training_samples:
            reason = f"insufficient_training_history (<{min_training_samples})"
            skipped_reasons[reason] += 1
            skipped_steps.append(EvaluationStep(
                origin_date=cur_obs_date,
                target_date=cur_target_date,
                actual_value=actual_val,
                predicted_value=None,
                absolute_error=None,
                squared_error=None,
                percentage_error=None,
                is_valid=False,
                skip_reason=reason,
            ))
            continue

        # In-sample training scale for MASE (zero lookahead from training history)
        train_pairs = [(target_dates[j], actual_targets[j]) for j in train_indices]
        train_pairs_sorted = sorted(train_pairs, key=lambda x: x[0])
        training_scale = calculate_training_scale(train_pairs_sorted)

        # Generate Model Prediction
        if model_name == ML_MODEL_PERSISTENCE:
            pred_raw = origin_val
        else:
            y_train = actual_targets[train_indices]
            X_train = X_all[train_indices, :]
            X_test = X_all[i:i+1, :]

            if config.get("requires_scaling", False):
                scaler = StandardScaler()
                X_train_proc = scaler.fit_transform(X_train)
                X_test_proc = scaler.transform(X_test)
            else:
                X_train_proc = X_train
                X_test_proc = X_test

            if model_name in (ML_MODEL_RIDGE, ML_MODEL_RIDGE_AR):
                reg = Ridge(alpha=hyperparams.get("alpha", 1.0), random_state=seed)
                reg.fit(X_train_proc, y_train)
                pred_raw = float(reg.predict(X_test_proc)[0])
            elif model_name == ML_MODEL_LASSO:
                reg = Lasso(
                    alpha=hyperparams.get("alpha", 0.1),
                    max_iter=hyperparams.get("max_iter", 2000),
                    random_state=seed,
                )
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    reg.fit(X_train_proc, y_train)
                pred_raw = float(reg.predict(X_test_proc)[0])
            elif model_name == ML_MODEL_RANDOM_FOREST:
                rf = RandomForestRegressor(
                    n_estimators=hyperparams.get("n_estimators", 100),
                    max_depth=hyperparams.get("max_depth", 5),
                    min_samples_split=hyperparams.get("min_samples_split", 4),
                    random_state=seed,
                    n_jobs=hyperparams.get("n_jobs", 1),
                )
                rf.fit(X_train_proc, y_train)
                pred_raw = float(rf.predict(X_test_proc)[0])
            elif model_name == ML_MODEL_LIGHTGBM and LIGHTGBM_AVAILABLE:
                lgb_reg = lgb.LGBMRegressor(
                    n_estimators=hyperparams.get("n_estimators", 100),
                    max_depth=hyperparams.get("max_depth", 4),
                    learning_rate=hyperparams.get("learning_rate", 0.05),
                    random_state=seed,
                    verbose=-1,
                )
                lgb_reg.fit(X_train_proc, y_train)
                pred_raw = float(lgb_reg.predict(X_test_proc)[0])
            else:
                raise ValueError(f"Unhandled model execution for: {model_name}")

        # Clamping to physical rate lower bound
        pred_clamped = max(float(pred_raw), MIN_TREND_RATE_CLAMP_USD_MT)

        # Step error calculation
        abs_err = round(abs(actual_val - pred_clamped), 4)
        sq_err = round((actual_val - pred_clamped) ** 2, 4)
        pct_err = round((abs_err / actual_val) * 100.0, 4) if actual_val > 0.0 else None
        step_mase = round(abs_err / training_scale, 4) if training_scale and training_scale > 0.0 else None

        actual_change = round(actual_val - origin_val, 4)
        pred_change = round(pred_clamped - origin_val, 4)
        is_pred_flat = abs(pred_change) < 1e-6
        is_act_flat = abs(actual_change) < 1e-6

        if not is_pred_flat and not is_act_flat:
            direction_concordant = bool((pred_change > 0) == (actual_change > 0))
        else:
            direction_concordant = None

        valid_steps.append(EvaluationStep(
            origin_date=cur_obs_date,
            target_date=cur_target_date,
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
        ))

    # Compute Aggregate Summary Metrics
    if valid_steps:
        actuals = [s.actual_value for s in valid_steps]
        preds = [s.predicted_value for s in valid_steps]
        mae = calculate_mae(actuals, preds)
        rmse = calculate_rmse(actuals, preds)
        mape = calculate_mape(actuals, preds)

        valid_mases = [s.step_mase for s in valid_steps if s.step_mase is not None]
        mase = round(sum(valid_mases) / len(valid_mases), 2) if valid_mases else None

        eval_start_date = valid_steps[0].target_date
        eval_end_date = valid_steps[-1].target_date

        flat_pred_count = sum(1 for s in valid_steps if s.predicted_change is not None and abs(s.predicted_change) < 1e-6)
        flat_rate = round(flat_pred_count / len(valid_steps), 4)

        act_nonzero_count = sum(1 for s in valid_steps if s.actual_change is not None and abs(s.actual_change) >= 1e-6)
        act_nonzero_rate = round(act_nonzero_count / len(valid_steps), 4)

        eligible_directional = [s for s in valid_steps if s.direction_concordant is not None]
        eligible_count = len(eligible_directional)
        if eligible_count > 0:
            concordant_count = sum(1 for s in eligible_directional if bool(s.direction_concordant))
            mda_non_flat = round((concordant_count / eligible_count) * 100.0, 2)
        else:
            mda_non_flat = None

        summary = ModelEvaluationSummary(
            model_type=model_name,
            route_code=dataset.route_code,
            evaluation_steps_count=len(valid_steps) + len(skipped_steps),
            burn_in_months=min_training_samples,
            eval_start_date=eval_start_date,
            eval_end_date=eval_end_date,
            mae=mae,
            rmse=rmse,
            mape=mape,
            valid_steps_count=len(valid_steps),
            skipped_steps_count=len(skipped_steps),
            skipped_reasons=dict(skipped_reasons),
            mase=mase,
            directional_accuracy_non_flat=mda_non_flat,
            flat_prediction_rate=flat_rate,
            actual_non_zero_movement_rate=act_nonzero_rate,
            eligible_directional_steps_count=eligible_count,
            relative_mae_vs_persistence=None,
            relative_rmse_vs_persistence=None,
        )
    else:
        summary = None

    all_steps = valid_steps + skipped_steps
    all_steps_sorted = sorted(all_steps, key=lambda s: s.origin_date)

    return MLModelBenchmarkResult(
        model_name=model_name,
        display_name=display_name,
        model_family=model_family,
        feature_subset_name=feat_subset,
        feature_count=len(feature_names),
        features_used=feature_names,
        hyperparameters=hyperparams,
        is_available=True,
        unavailability_reason=None,
        summary=summary,
        steps=all_steps_sorted,
    )


def benchmark_route_ml_candidates(
    db: Session,
    route_code: str,
    horizon: int = 1,
    min_training_samples: int = DEFAULT_MIN_TRAIN_MONTHS,
    models: Tuple[str, ...] = STANDARD_ML_BENCHMARK_MODELS,
    seed: int = DEFAULT_RANDOM_SEED,
) -> Dict[str, Any]:
    """
    Executes comprehensive candidate ML model benchmarking for a specific route and horizon.

    Workflow:
    1. Builds strict-forward historical feature dataset.
    2. Runs expanding walk-forward out-of-sample backtest for each model.
    3. Aligns models onto the strict common-sample intersection of target dates.
    4. Computes Diebold-Mariano statistical tests (with HLN correction) and Moving Block Bootstrap CIs.
    5. Applies family-wise Holm-Bonferroni step-down correction across candidate p-values.
    6. Breaks down performance across the 4 canonical market regimes.
    """
    if route_code in DISALLOWED_DERIVED_ROUTES:
        raise ValueError(f"Route '{route_code}' is a derived spread indicator and cannot be evaluated directly as a corridor rate.")
    if route_code not in SUPPORTED_DIRECT_ROUTES:
        raise ValueError(f"Route '{route_code}' is not supported. Supported routes: {list(SUPPORTED_DIRECT_ROUTES)}")

    # 1. Build dataset
    dataset = build_forecast_dataset(db, route_code=route_code, horizon_months=horizon)

    # 2. Run walk-forward for each model
    model_results: Dict[str, MLModelBenchmarkResult] = {}
    model_valid_steps: Dict[str, List[EvaluationStep]] = {}

    for m_name in models:
        res = evaluate_ml_walk_forward(
            dataset=dataset,
            model_name=m_name,
            min_training_samples=min_training_samples,
            seed=seed,
        )
        model_results[m_name] = res
        if res.is_available and res.summary is not None:
            model_valid_steps[m_name] = [s for s in res.steps if s.is_valid]

    # 3. Exact common-sample comparison across available evaluated models
    common_target_dates: Optional[set] = None
    for m_name, steps in model_valid_steps.items():
        td_set = {s.target_date for s in steps}
        if common_target_dates is None:
            common_target_dates = set(td_set)
        else:
            common_target_dates = common_target_dates.intersection(td_set)

    common_dates_sorted = sorted(common_target_dates) if common_target_dates else []
    common_N = len(common_dates_sorted)

    common_summaries: Dict[str, Dict[str, Any]] = {}
    p_mae_common = None
    p_rmse_common = None

    if common_N > 0:
        # First compute Persistence on common sample
        if ML_MODEL_PERSISTENCE in model_valid_steps:
            p_map = {s.target_date: s for s in model_valid_steps[ML_MODEL_PERSISTENCE]}
            p_common_steps = [p_map[d] for d in common_dates_sorted]
            p_acts = [s.actual_value for s in p_common_steps]
            p_preds = [s.predicted_value for s in p_common_steps]
            p_mae_common = calculate_mae(p_acts, p_preds)
            p_rmse_common = calculate_rmse(p_acts, p_preds)

        for m_name, steps in model_valid_steps.items():
            s_map = {s.target_date: s for s in steps}
            c_steps = [s_map[d] for d in common_dates_sorted]
            acts = [s.actual_value for s in c_steps]
            preds = [s.predicted_value for s in c_steps]

            mae = calculate_mae(acts, preds)
            rmse = calculate_rmse(acts, preds)
            mape = calculate_mape(acts, preds)
            mases = [s.step_mase for s in c_steps if s.step_mase is not None]
            mase = round(sum(mases) / len(mases), 2) if mases else None

            rel_mae_ratio = round(mae / p_mae_common, 2) if p_mae_common and p_mae_common > 0 else None
            rel_mae_pct = round(((mae - p_mae_common) / p_mae_common) * 100.0, 2) if p_mae_common and p_mae_common > 0 else None
            rel_rmse_ratio = round(rmse / p_rmse_common, 2) if p_rmse_common and p_rmse_common > 0 else None

            # Directional accuracy on common sample
            dir_steps = [s for s in c_steps if s.direction_concordant is not None]
            if dir_steps:
                concordant = sum(1 for s in dir_steps if bool(s.direction_concordant))
                mda = round((concordant / len(dir_steps)) * 100.0, 2)
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
                "relative_mae_ratio": rel_mae_ratio,
                "relative_mae_percent_change": rel_mae_pct,
                "relative_rmse_ratio": rel_rmse_ratio,
            }

    # 4. Statistical comparisons against Persistence
    stat_comparisons: Dict[str, Dict[str, Any]] = {}
    candidate_names: List[str] = []
    raw_p_values: List[float] = []

    common_set = set(common_dates_sorted)
    p_steps = [
        s for s in model_valid_steps.get(ML_MODEL_PERSISTENCE, [])
        if not common_set or s.target_date in common_set
    ]

    for m_name in models:
        if m_name == ML_MODEL_PERSISTENCE:
            continue
        if m_name not in model_valid_steps:
            stat_comparisons[m_name] = {
                "is_available": False,
                "status": "UNAVAILABLE",
                "reason": model_results[m_name].unavailability_reason,
            }
            continue

        c_steps = [
            s for s in model_valid_steps[m_name]
            if not common_set or s.target_date in common_set
        ]
        comp_res = compare_candidate_to_baseline(
            baseline_steps=p_steps,
            candidate_steps=c_steps,
            model_b=m_name,
            route_code=route_code,
            horizon_months=horizon,
            model_a=ML_MODEL_PERSISTENCE,
            loss_type="absolute",
            seed=seed,
        )
        res_dict = comp_res.to_dict()
        stat_comparisons[m_name] = res_dict
        candidate_names.append(m_name)
        raw_p_values.append(comp_res.p_value)

    # 5. Holm-Bonferroni step-down correction across candidate family
    if raw_p_values:
        adj_p_values = holm_bonferroni_step_down(raw_p_values)
        for c_name, adj_p in zip(candidate_names, adj_p_values):
            stat_comparisons[c_name]["holm_adjusted_p_value"] = float(adj_p)
            stat_comparisons[c_name]["holm_statistically_significant"] = bool(adj_p < 0.05)

    # 6. Regime Breakdown across the 4 canonical regimes
    regime_results: Dict[str, Dict[str, Any]] = {}
    for reg in HISTORICAL_REGIMES:
        r_dict: Dict[str, Any] = {
            "regime_id": reg.regime_id,
            "display_name": reg.display_name,
            "start_date": reg.start_date.isoformat(),
            "end_date": reg.end_date.isoformat(),
            "models": {},
            "statistical_comparisons": {},
        }

        # Model metrics in regime (on common sample when available)
        for m_name, steps in model_valid_steps.items():
            common_steps_m = [s for s in steps if not common_set or s.target_date in common_set]
            m_metrics = compute_regime_metrics(reg, common_steps_m)
            r_dict["models"][m_name] = m_metrics.to_dict()

        # Statistical comparison vs Persistence in regime
        reg_p_steps = [s for s in p_steps if s.is_valid and reg.contains_target_date(s.target_date)]
        for m_name in models:
            if m_name == ML_MODEL_PERSISTENCE or m_name not in model_valid_steps:
                continue
            reg_c_steps = [
                s for s in model_valid_steps[m_name]
                if s.is_valid
                and (not common_set or s.target_date in common_set)
                and reg.contains_target_date(s.target_date)
            ]
            reg_comp = compare_candidate_to_baseline(
                baseline_steps=reg_p_steps,
                candidate_steps=reg_c_steps,
                model_b=m_name,
                route_code=route_code,
                horizon_months=horizon,
                model_a=ML_MODEL_PERSISTENCE,
                loss_type="absolute",
                seed=seed,
                regime_id=reg.regime_id,
            )
            r_dict["statistical_comparisons"][m_name] = reg_comp.to_dict()

        regime_results[reg.regime_id] = r_dict

    return {
        "route_code": route_code,
        "horizon_months": horizon,
        "total_labeled_rows": len(dataset.labeled_rows),
        "min_training_samples": min_training_samples,
        "models_evaluated": {k: v.to_dict() for k, v in model_results.items()},
        "common_sample_comparison": {
            "common_sample_size": common_N,
            "common_start_date": common_dates_sorted[0].isoformat() if common_dates_sorted else None,
            "common_end_date": common_dates_sorted[-1].isoformat() if common_dates_sorted else None,
            "models": common_summaries,
        },
        "statistical_comparisons": stat_comparisons,
        "regime_breakdown": regime_results,
    }


def execute_comprehensive_ml_benchmarks(
    db: Session,
    routes: Tuple[str, ...] = SUPPORTED_DIRECT_ROUTES,
    horizons: Tuple[int, ...] = (1, 3, 6),
    min_training_samples: int = DEFAULT_MIN_TRAIN_MONTHS,
    seed: int = DEFAULT_RANDOM_SEED,
) -> Dict[str, Any]:
    """
    Executes the full grid of ML candidate benchmarks across corridors and horizons.
    Returns complete structured dataset for C3.2 report and machine-readable output.
    """
    results: Dict[str, Any] = {
        "metadata": {
            "benchmark_type": "Stage C3.2 Feature-Based Candidate ML Model Benchmarking",
            "evaluated_routes": list(routes),
            "evaluated_horizons": list(horizons),
            "min_training_samples": min_training_samples,
            "random_seed": seed,
            "lightgbm_available": LIGHTGBM_AVAILABLE,
            "model_configs": {k: {
                "display_name": v["display_name"],
                "model_family": v["model_family"],
                "feature_subset_name": v["feature_subset_name"],
                "hyperparameters": v["hyperparameters"],
                "requires_scaling": v.get("requires_scaling", False),
            } for k, v in ML_CANDIDATE_CONFIGS.items()},
        },
        "experiments": {},
    }

    for route in routes:
        for h in horizons:
            exp_key = f"{route}_T{h}"
            logger.info(f"Running ML benchmark grid for {exp_key}...")
            bench_res = benchmark_route_ml_candidates(
                db=db,
                route_code=route,
                horizon=h,
                min_training_samples=min_training_samples,
                seed=seed,
            )
            results["experiments"][exp_key] = bench_res

    return results
