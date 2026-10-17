"""
Pelagos Chronological Walk-Forward Backtest and Evaluation Engine.
Implements leak-free out-of-sample walk-forward backtesting and error metric calculation.
Traces to REQ-FR-FRF-001, ML-001 Section 5.3, and REQ-NFR-DIP-001.
"""

import math
from typing import List, Tuple, Dict, Any, Optional
from datetime import date

from pelagos.forecasting.schemas import (
    BaselineModelType,
    EvaluationStep,
    ModelEvaluationSummary,
    RouteEvaluationReport,
    SUPPORTED_DIRECT_ROUTES,
    DISALLOWED_DERIVED_ROUTES,
)
from pelagos.forecasting.baselines import predict_baseline


def calculate_mae(actuals: List[float], predictions: List[float]) -> float:
    """Calculates Mean Absolute Error (MAE): (1/N) * sum(|y - y_hat|)."""
    if not actuals or not predictions or len(actuals) != len(predictions):
        raise ValueError("Actuals and predictions must be non-empty lists of identical length")
    return round(sum(abs(a - p) for a, p in zip(actuals, predictions)) / len(actuals), 2)


def calculate_rmse(actuals: List[float], predictions: List[float]) -> float:
    """Calculates Root Mean Squared Error (RMSE): sqrt((1/N) * sum((y - y_hat)^2))."""
    if not actuals or not predictions or len(actuals) != len(predictions):
        raise ValueError("Actuals and predictions must be non-empty lists of identical length")
    mse = sum((a - p) ** 2 for a, p in zip(actuals, predictions)) / len(actuals)
    return round(math.sqrt(mse), 2)


def calculate_mape(actuals: List[float], predictions: List[float]) -> float:
    """Calculates Mean Absolute Percentage Error (MAPE): (1/N) * sum(|(y - y_hat)/y|) * 100."""
    if not actuals or not predictions or len(actuals) != len(predictions):
        raise ValueError("Actuals and predictions must be non-empty lists of identical length")
    for a in actuals:
        if a <= 0.0:
            raise ValueError(f"Actual values must be strictly positive for MAPE computation: got {a}")
    return round(
        (sum(abs(a - p) / a for a, p in zip(actuals, predictions)) / len(actuals)) * 100.0,
        2,
    )


def evaluate_walk_forward(
    observations: List[Tuple[date, float]],
    model_type: str,
    route_code: str = "USG-JPN-BULK",
    burn_in_months: int = 12,
    horizon: int = 1,
) -> Tuple[ModelEvaluationSummary, List[EvaluationStep]]:
    """
    Executes a strict chronological walk-forward out-of-sample evaluation for a single model.
    Zero-lookahead guarantee: At origin index t, only observations where observation_date <= d_t
    are accessible to the forecasting function.
    """
    if len(observations) <= burn_in_months:
        raise ValueError(
            f"Total observations ({len(observations)}) must exceed burn-in training history ({burn_in_months})"
        )

    # Sort observations strictly chronologically
    sorted_obs = sorted(observations, key=lambda x: x[0])
    total_len = len(sorted_obs)

    steps: List[EvaluationStep] = []
    actuals: List[float] = []
    preds: List[float] = []

    # Origin index runs from burn_in_months - 1 to total_len - 1 - horizon
    for origin_idx in range(burn_in_months - 1, total_len - horizon):
        origin_date, _ = sorted_obs[origin_idx]
        target_idx = origin_idx + horizon
        target_date, actual_val = sorted_obs[target_idx]

        # History is strictly observations up to and including origin_idx (zero lookahead)
        history = [val for _, val in sorted_obs[:origin_idx + 1]]

        pred_val, _ = predict_baseline(model_type, history, horizon=horizon)

        abs_err = round(abs(actual_val - pred_val), 4)
        sq_err = round((actual_val - pred_val) ** 2, 4)
        pct_err = round((abs_err / actual_val) * 100.0, 4)

        steps.append(EvaluationStep(
            origin_date=origin_date,
            target_date=target_date,
            actual_value=actual_val,
            predicted_value=pred_val,
            absolute_error=abs_err,
            squared_error=sq_err,
            percentage_error=pct_err,
        ))
        actuals.append(actual_val)
        preds.append(pred_val)

    mae = calculate_mae(actuals, preds)
    rmse = calculate_rmse(actuals, preds)
    mape = calculate_mape(actuals, preds)

    summary = ModelEvaluationSummary(
        model_type=model_type,
        route_code=route_code,
        evaluation_steps_count=len(steps),
        burn_in_months=burn_in_months,
        eval_start_date=steps[0].target_date,
        eval_end_date=steps[-1].target_date,
        mae=mae,
        rmse=rmse,
        mape=mape,
        relative_mae_vs_persistence=None,
        relative_rmse_vs_persistence=None,
    )

    return summary, steps


def evaluate_route_baselines(
    observations: List[Tuple[date, float]],
    route_code: str,
    burn_in_months: int = 12,
    horizon: int = 1,
    include_steps: bool = False,
) -> RouteEvaluationReport:
    """
    Evaluates all standard baseline models (Persistence, MA-3, MA-6, Trend-6)
    across the chronological walk-forward backtest window.
    Compares all alternative models directly against Persistence.
    """
    if route_code in DISALLOWED_DERIVED_ROUTES:
        raise ValueError(
            f"Route '{route_code}' is a derived rate difference indicator and cannot be evaluated directly as a corridor rate."
        )

    if route_code not in SUPPORTED_DIRECT_ROUTES:
        raise ValueError(
            f"Route '{route_code}' is not supported for baseline evaluation. Supported routes: {list(SUPPORTED_DIRECT_ROUTES)}"
        )

    model_types = [
        BaselineModelType.PERSISTENCE.value,
        BaselineModelType.ROLLING_MA_3.value,
        BaselineModelType.ROLLING_MA_6.value,
        BaselineModelType.LINEAR_TREND_6.value,
    ]

    summaries: Dict[str, ModelEvaluationSummary] = {}
    all_steps: Dict[str, List[EvaluationStep]] = {}

    for m_type in model_types:
        summary, steps = evaluate_walk_forward(
            observations=observations,
            model_type=m_type,
            route_code=route_code,
            burn_in_months=burn_in_months,
            horizon=horizon,
        )
        summaries[m_type] = summary
        all_steps[m_type] = steps

    # Compute comparison ratios vs Persistence
    persist_summary = summaries[BaselineModelType.PERSISTENCE.value]
    updated_summaries: Dict[str, ModelEvaluationSummary] = {}

    for m_type, s in summaries.items():
        rel_mae = round(s.mae / persist_summary.mae, 2) if persist_summary.mae > 0 else None
        rel_rmse = round(s.rmse / persist_summary.rmse, 2) if persist_summary.rmse > 0 else None

        updated_summaries[m_type] = ModelEvaluationSummary(
            model_type=s.model_type,
            route_code=s.route_code,
            evaluation_steps_count=s.evaluation_steps_count,
            burn_in_months=s.burn_in_months,
            eval_start_date=s.eval_start_date,
            eval_end_date=s.eval_end_date,
            mae=s.mae,
            rmse=s.rmse,
            mape=s.mape,
            relative_mae_vs_persistence=rel_mae,
            relative_rmse_vs_persistence=rel_rmse,
        )

    first_model = list(updated_summaries.values())[0]

    return RouteEvaluationReport(
        route_code=route_code,
        total_observations=len(observations),
        burn_in_months=burn_in_months,
        eval_start_date=first_model.eval_start_date,
        eval_end_date=first_model.eval_end_date,
        evaluation_steps_count=first_model.evaluation_steps_count,
        models=updated_summaries,
        steps=all_steps if include_steps else None,
    )
