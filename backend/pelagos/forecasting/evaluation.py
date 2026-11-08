"""
Pelagos Chronological Walk-Forward Backtest and Evaluation Engine.
Implements leak-free out-of-sample calendar-aware walk-forward backtesting,
contiguous lookback history enforcement, common-sample evaluation, and error metric calculation.
Traces to REQ-FR-FRF-001, ML-001 Section 5.3, and REQ-NFR-DIP-001.
"""

import math
from typing import List, Tuple, Dict, Any, Optional, Set
from datetime import date
from collections import defaultdict

from pelagos.forecasting.schemas import (
    BaselineModelType,
    EvaluationStep,
    ModelEvaluationSummary,
    CommonSampleComparisonReport,
    RouteEvaluationReport,
    SUPPORTED_DIRECT_ROUTES,
    DISALLOWED_DERIVED_ROUTES,
)
from pelagos.forecasting.baselines import predict_baseline


def add_calendar_months(d: date, months: int) -> date:
    """Adds calendar months preserving day-of-month or clamping to month end."""
    total_m = d.year * 12 + d.month - 1 + months
    y = total_m // 12
    m = total_m % 12 + 1
    day = min(d.day, 28) if d.day > 28 else d.day
    return date(y, m, day)


def subtract_calendar_months(d: date, months: int) -> date:
    """Subtracts calendar months preserving day-of-month or clamping to month end."""
    total_m = d.year * 12 + d.month - 1 - months
    y = total_m // 12
    m = total_m % 12 + 1
    day = min(d.day, 28) if d.day > 28 else d.day
    return date(y, m, day)


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


def calculate_training_scale(history_obs: List[Tuple[date, float]]) -> Optional[float]:
    """
    Calculates in-sample scale denominator for MASE strictly from contiguous 1-step
    calendar pairs in history available up to origin (zero lookahead).
    scale_t = (1 / |D_t|) * sum_{(d, d-1m) in D_t} |y(d) - y(d-1m)|.
    """
    if len(history_obs) < 2:
        return None
    date_map = {d: val for d, val in history_obs}
    diffs = []
    for d, val in history_obs:
        prev_d = subtract_calendar_months(d, 1)
        if prev_d in date_map:
            diffs.append(abs(val - date_map[prev_d]))
    if not diffs:
        return None
    scale = sum(diffs) / len(diffs)
    return round(scale, 4)


def check_contiguous_history(
    origin_date: date,
    date_map: Dict[date, float],
    model_type: str,
    burn_in_months: int = 12,
) -> Tuple[bool, List[float], Optional[str]]:
    """
    Verifies required contiguous calendar monthly lags for a given model type.
    Returns (is_valid, history_list, skip_reason).
    """
    norm_type = model_type.lower().strip()
    if norm_type in ("persistence", "m1"):
        if origin_date not in date_map:
            return False, [], "missing_origin_date"
        return True, [date_map[origin_date]], None

    elif norm_type in ("rolling_ma_3", "ma3", "m2_3"):
        required_lags = [subtract_calendar_months(origin_date, k) for k in range(3)]
        for req_d in required_lags:
            if req_d not in date_map:
                return False, [], "missing_lookback_history"
        return True, [date_map[req_d] for req_d in reversed(required_lags)], None

    elif norm_type in ("rolling_ma_6", "ma6", "m2_6"):
        required_lags = [subtract_calendar_months(origin_date, k) for k in range(6)]
        for req_d in required_lags:
            if req_d not in date_map:
                return False, [], "missing_lookback_history"
        return True, [date_map[req_d] for req_d in reversed(required_lags)], None

    elif norm_type in ("linear_trend_6", "trend6", "m3"):
        required_lags = [subtract_calendar_months(origin_date, k) for k in range(6)]
        for req_d in required_lags:
            if req_d not in date_map:
                return False, [], "missing_lookback_history"
        return True, [date_map[req_d] for req_d in reversed(required_lags)], None

    elif norm_type in ("ridge_ar", "ridge", "ar1", "ridge_ar_1"):
        # Traces contiguous history backward from origin_date
        contiguous_dates = []
        cur_d = origin_date
        while cur_d in date_map:
            contiguous_dates.append(cur_d)
            cur_d = subtract_calendar_months(cur_d, 1)
        if len(contiguous_dates) < burn_in_months:
            return False, [], "missing_lookback_history"
        history = [date_map[d] for d in reversed(contiguous_dates)]
        return True, history, None

    else:
        if origin_date not in date_map:
            return False, [], "missing_origin_date"
        return True, [date_map[origin_date]], None


def evaluate_walk_forward(
    observations: List[Tuple[date, float]],
    model_type: str,
    route_code: str = "USG-JPN-BULK",
    burn_in_months: int = 12,
    horizon: int = 1,
    candidate_origins: Optional[List[date]] = None,
    allow_boundary_origins: bool = False,
    include_skipped_steps: bool = False,
) -> Tuple[ModelEvaluationSummary, List[EvaluationStep]]:
    """
    Executes a strict chronological calendar-aware walk-forward out-of-sample evaluation.
    Zero-lookahead guarantee: At origin date d_t, only observations where observation_date <= d_t
    are accessible to the forecasting function and training scale calculation.
    Target date is determined strictly via calendar arithmetic: d_target = d_origin + horizon months.
    Missing target dates or broken lookback windows are explicitly skipped and recorded without
    shifting array positions or compressing elapsed time.
    """
    if len(observations) <= burn_in_months:
        raise ValueError(
            f"Total observations ({len(observations)}) must exceed burn-in training history ({burn_in_months})"
        )

    # Sort observations strictly chronologically
    sorted_obs = sorted(observations, key=lambda x: x[0])
    date_map: Dict[date, float] = {d: val for d, val in sorted_obs}
    all_dates = [d for d, _ in sorted_obs]
    max_obs_date = all_dates[-1]

    # Determine candidate forecast origins
    if candidate_origins is not None:
        eval_origins = [d for d in candidate_origins if d in date_map]
    else:
        if allow_boundary_origins:
            eval_origins = all_dates[burn_in_months - 1:]
        else:
            eval_origins = [
                d for d in all_dates[burn_in_months - 1:]
                if add_calendar_months(d, horizon) <= max_obs_date
            ]

    valid_steps: List[EvaluationStep] = []
    skipped_steps: List[EvaluationStep] = []
    skipped_reasons: Dict[str, int] = defaultdict(int)

    for origin_date in eval_origins:
        origin_val = date_map[origin_date]

        # 1. Lookback lag / history continuity check
        is_lookback_ok, model_history, lookback_reason = check_contiguous_history(
            origin_date=origin_date,
            date_map=date_map,
            model_type=model_type,
            burn_in_months=burn_in_months,
        )
        if not is_lookback_ok:
            reason = lookback_reason or "missing_lookback_history"
            skipped_reasons[reason] += 1
            skipped_steps.append(EvaluationStep(
                origin_date=origin_date,
                target_date=add_calendar_months(origin_date, horizon),
                actual_value=None,
                predicted_value=None,
                absolute_error=None,
                squared_error=None,
                percentage_error=None,
                is_valid=False,
                skip_reason=reason,
            ))
            continue

        # 2. Calendar target date lookup
        target_date = add_calendar_months(origin_date, horizon)
        if target_date not in date_map:
            skipped_reasons["missing_target_date"] += 1
            skipped_steps.append(EvaluationStep(
                origin_date=origin_date,
                target_date=target_date,
                actual_value=None,
                predicted_value=None,
                absolute_error=None,
                squared_error=None,
                percentage_error=None,
                is_valid=False,
                skip_reason="missing_target_date",
            ))
            continue

        actual_val = date_map[target_date]

        # 3. Predict strictly from history available at origin (zero lookahead)
        pred_val, _ = predict_baseline(model_type, model_history, horizon=horizon)

        abs_err = round(abs(actual_val - pred_val), 4)
        sq_err = round((actual_val - pred_val) ** 2, 4)
        pct_err = round((abs_err / actual_val) * 100.0, 4)

        # 4. In-sample scale denominator for MASE (zero lookahead up to origin)
        history_up_to_origin = [(d, v) for d, v in sorted_obs if d <= origin_date]
        training_scale = calculate_training_scale(history_up_to_origin)
        step_mase = round(abs_err / training_scale, 4) if training_scale and training_scale > 0.0 else None

        # 5. Directional concordance
        actual_change = round(actual_val - origin_val, 4)
        predicted_change = round(pred_val - origin_val, 4)
        is_pred_flat = abs(predicted_change) < 1e-6
        is_act_flat = abs(actual_change) < 1e-6

        if not is_pred_flat and not is_act_flat:
            direction_concordant = (predicted_change > 0) == (actual_change > 0)
        else:
            direction_concordant = None

        valid_steps.append(EvaluationStep(
            origin_date=origin_date,
            target_date=target_date,
            actual_value=actual_val,
            predicted_value=pred_val,
            absolute_error=abs_err,
            squared_error=sq_err,
            percentage_error=pct_err,
            is_valid=True,
            skip_reason=None,
            training_scale=training_scale,
            step_mase=step_mase,
            actual_change=actual_change,
            predicted_change=predicted_change,
            direction_concordant=direction_concordant,
        ))

    # Summary metrics computation
    total_skipped = len(skipped_steps)
    if valid_steps:
        actuals = [s.actual_value for s in valid_steps if s.actual_value is not None]
        preds = [s.predicted_value for s in valid_steps if s.predicted_value is not None]
        mae = calculate_mae(actuals, preds)
        rmse = calculate_rmse(actuals, preds)
        mape = calculate_mape(actuals, preds)

        valid_mases = [s.step_mase for s in valid_steps if s.step_mase is not None]
        mase = round(sum(valid_mases) / len(valid_mases), 2) if valid_mases else None

        eval_start_date = valid_steps[0].target_date
        eval_end_date = valid_steps[-1].target_date

        flat_pred_count = sum(
            1 for s in valid_steps
            if s.predicted_change is not None and abs(s.predicted_change) < 1e-6
        )
        flat_prediction_rate = round(flat_pred_count / len(valid_steps), 4)

        actual_nonzero_count = sum(
            1 for s in valid_steps
            if s.actual_change is not None and abs(s.actual_change) >= 1e-6
        )
        actual_non_zero_movement_rate = round(actual_nonzero_count / len(valid_steps), 4)

        eligible_directional_steps = [s for s in valid_steps if s.direction_concordant is not None]
        eligible_directional_count = len(eligible_directional_steps)
        if eligible_directional_count > 0:
            concordant_count = sum(1 for s in eligible_directional_steps if s.direction_concordant is True)
            mda_non_flat = round((concordant_count / eligible_directional_count) * 100.0, 2)
        else:
            mda_non_flat = None
    else:
        mae = 0.0
        rmse = 0.0
        mape = 0.0
        mase = None
        eval_start_date = None
        eval_end_date = None
        flat_prediction_rate = 0.0
        actual_non_zero_movement_rate = 0.0
        eligible_directional_count = 0
        mda_non_flat = None

    summary = ModelEvaluationSummary(
        model_type=model_type,
        route_code=route_code,
        evaluation_steps_count=len(valid_steps),
        burn_in_months=burn_in_months,
        eval_start_date=eval_start_date,
        eval_end_date=eval_end_date,
        mae=mae,
        rmse=rmse,
        mape=mape,
        valid_steps_count=len(valid_steps),
        skipped_steps_count=total_skipped,
        skipped_reasons=dict(skipped_reasons),
        mase=mase,
        directional_accuracy_non_flat=mda_non_flat,
        flat_prediction_rate=flat_prediction_rate,
        actual_non_zero_movement_rate=actual_non_zero_movement_rate,
        eligible_directional_steps_count=eligible_directional_count,
        relative_mae_vs_persistence=None,
        relative_rmse_vs_persistence=None,
    )

    if include_skipped_steps:
        all_combined = sorted(valid_steps + skipped_steps, key=lambda s: s.origin_date)
        return summary, all_combined
    return summary, valid_steps


def evaluate_common_sample_comparison(
    models_steps: Dict[str, List[EvaluationStep]],
    route_code: str,
    horizon: int = 1,
    burn_in_months: int = 12,
) -> CommonSampleComparisonReport:
    """
    Evaluates competing models strictly on the exact intersection of valid calendar target dates.
    Guarantees mathematically fair benchmark comparisons without sample-size divergence.
    """
    if not models_steps:
        return CommonSampleComparisonReport(
            route_code=route_code,
            horizon_months=horizon,
            common_target_dates_count=0,
            common_start_date=None,
            common_end_date=None,
            models={},
        )

    # Compute intersection of valid target dates across all models
    model_valid_dates: Dict[str, Set[date]] = {}
    for m_type, steps in models_steps.items():
        model_valid_dates[m_type] = {s.target_date for s in steps if s.is_valid and s.actual_value is not None}

    common_dates_set = set.intersection(*model_valid_dates.values()) if model_valid_dates else set()
    sorted_common_dates = sorted(common_dates_set)
    common_count = len(sorted_common_dates)

    if common_count == 0:
        return CommonSampleComparisonReport(
            route_code=route_code,
            horizon_months=horizon,
            common_target_dates_count=0,
            common_start_date=None,
            common_end_date=None,
            models={},
        )

    common_start = sorted_common_dates[0]
    common_end = sorted_common_dates[-1]

    summaries: Dict[str, ModelEvaluationSummary] = {}
    for m_type, steps in models_steps.items():
        filtered_steps = [s for s in steps if s.is_valid and s.target_date in common_dates_set]
        actuals = [s.actual_value for s in filtered_steps if s.actual_value is not None]
        preds = [s.predicted_value for s in filtered_steps if s.predicted_value is not None]

        mae = calculate_mae(actuals, preds)
        rmse = calculate_rmse(actuals, preds)
        mape = calculate_mape(actuals, preds)

        valid_mases = [s.step_mase for s in filtered_steps if s.step_mase is not None]
        mase = round(sum(valid_mases) / len(valid_mases), 2) if valid_mases else None

        flat_pred_count = sum(
            1 for s in filtered_steps
            if s.predicted_change is not None and abs(s.predicted_change) < 1e-6
        )
        flat_rate = round(flat_pred_count / len(filtered_steps), 4)

        actual_nonzero_count = sum(
            1 for s in filtered_steps
            if s.actual_change is not None and abs(s.actual_change) >= 1e-6
        )
        act_nonzero_rate = round(actual_nonzero_count / len(filtered_steps), 4)

        eligible_directional = [s for s in filtered_steps if s.direction_concordant is not None]
        eligible_count = len(eligible_directional)
        if eligible_count > 0:
            concordant_count = sum(1 for s in eligible_directional if s.direction_concordant is True)
            mda_non_flat = round((concordant_count / eligible_count) * 100.0, 2)
        else:
            mda_non_flat = None

        summaries[m_type] = ModelEvaluationSummary(
            model_type=m_type,
            route_code=route_code,
            evaluation_steps_count=len(filtered_steps),
            burn_in_months=burn_in_months,
            eval_start_date=common_start,
            eval_end_date=common_end,
            mae=mae,
            rmse=rmse,
            mape=mape,
            valid_steps_count=len(filtered_steps),
            skipped_steps_count=0,
            skipped_reasons={},
            mase=mase,
            directional_accuracy_non_flat=mda_non_flat,
            flat_prediction_rate=flat_rate,
            actual_non_zero_movement_rate=act_nonzero_rate,
            eligible_directional_steps_count=eligible_count,
            relative_mae_vs_persistence=None,
            relative_rmse_vs_persistence=None,
        )

    # Compute comparison ratios vs Persistence on common sample
    persist_key = BaselineModelType.PERSISTENCE.value
    if persist_key in summaries:
        p_mae = summaries[persist_key].mae
        p_rmse = summaries[persist_key].rmse
        updated: Dict[str, ModelEvaluationSummary] = {}
        for m_type, s in summaries.items():
            rel_mae = round(s.mae / p_mae, 2) if p_mae > 0 else None
            rel_rmse = round(s.rmse / p_rmse, 2) if p_rmse > 0 else None
            updated[m_type] = ModelEvaluationSummary(
                model_type=s.model_type,
                route_code=s.route_code,
                evaluation_steps_count=s.evaluation_steps_count,
                burn_in_months=s.burn_in_months,
                eval_start_date=s.eval_start_date,
                eval_end_date=s.eval_end_date,
                mae=s.mae,
                rmse=s.rmse,
                mape=s.mape,
                valid_steps_count=s.valid_steps_count,
                skipped_steps_count=s.skipped_steps_count,
                skipped_reasons=s.skipped_reasons,
                mase=s.mase,
                directional_accuracy_non_flat=s.directional_accuracy_non_flat,
                flat_prediction_rate=s.flat_prediction_rate,
                actual_non_zero_movement_rate=s.actual_non_zero_movement_rate,
                eligible_directional_steps_count=s.eligible_directional_steps_count,
                relative_mae_vs_persistence=rel_mae,
                relative_rmse_vs_persistence=rel_rmse,
            )
        summaries = updated

    return CommonSampleComparisonReport(
        route_code=route_code,
        horizon_months=horizon,
        common_target_dates_count=common_count,
        common_start_date=common_start,
        common_end_date=common_end,
        models=summaries,
    )


def evaluate_route_baselines(
    observations: List[Tuple[date, float]],
    route_code: str,
    burn_in_months: int = 12,
    horizon: int = 1,
    include_steps: bool = False,
    include_skipped_steps: bool = False,
    candidate_origins: Optional[List[date]] = None,
    allow_boundary_origins: bool = False,
) -> RouteEvaluationReport:
    """
    Evaluates all standard baseline models (Persistence, MA-3, MA-6, Trend-6, Ridge AR)
    across the chronological calendar-aware walk-forward backtest window.
    Compares all alternative models directly against Persistence on both individual
    and exact common-sample target dates.
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
        BaselineModelType.RIDGE_AR.value,
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
            candidate_origins=candidate_origins,
            allow_boundary_origins=allow_boundary_origins,
            include_skipped_steps=include_skipped_steps,
        )
        summaries[m_type] = summary
        all_steps[m_type] = steps

    # Compute comparison ratios vs Persistence on individual eligible samples
    persist_summary = summaries.get(BaselineModelType.PERSISTENCE.value)
    updated_summaries: Dict[str, ModelEvaluationSummary] = {}

    for m_type, s in summaries.items():
        if persist_summary and persist_summary.mae > 0:
            rel_mae = round(s.mae / persist_summary.mae, 2)
            rel_rmse = round(s.rmse / persist_summary.rmse, 2) if persist_summary.rmse > 0 else None
        else:
            rel_mae = None
            rel_rmse = None

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
            valid_steps_count=s.valid_steps_count,
            skipped_steps_count=s.skipped_steps_count,
            skipped_reasons=s.skipped_reasons,
            mase=s.mase,
            directional_accuracy_non_flat=s.directional_accuracy_non_flat,
            flat_prediction_rate=s.flat_prediction_rate,
            actual_non_zero_movement_rate=s.actual_non_zero_movement_rate,
            eligible_directional_steps_count=s.eligible_directional_steps_count,
            relative_mae_vs_persistence=rel_mae,
            relative_rmse_vs_persistence=rel_rmse,
        )

    # Compute common-sample comparison
    common_comparison = evaluate_common_sample_comparison(
        models_steps=all_steps,
        route_code=route_code,
        horizon=horizon,
        burn_in_months=burn_in_months,
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
        common_sample=common_comparison,
        steps=all_steps if include_steps else None,
    )
