"""
Pelagos Freight Forecasting Baselines.
Implements pure, deterministic mathematical baseline forecasting models:
1. Persistence (Naive Last Observation)
2. Rolling Window Moving Average (MA-3, MA-6)
3. Historical Linear Trend Extrapolation (Trend-6 with non-negative clamping)

Traces to REQ-FR-FRF-001, ML-001 Section 5.3, and REQ-NFR-DIP-001.
"""

from typing import List, Tuple, Optional
from datetime import date

from pelagos.forecasting.schemas import (
    BaselineModelType,
    BaselineForecastResult,
    SUPPORTED_DIRECT_ROUTES,
    DISALLOWED_DERIVED_ROUTES,
    STATUS_FORECAST_GENERATED,
    STATUS_INSUFFICIENT_HISTORY,
    FLAG_TREND_NEGATIVE_CLAMP,
)

MIN_TREND_RATE_CLAMP_USD_MT = 0.10


def predict_persistence(
    history: List[float],
    horizon: int = 1,
) -> Tuple[float, List[str]]:
    """
    Persistence baseline: y_hat_{t+h|t} = y_t.
    Requires at least 1 valid observation at origin.
    """
    if horizon < 1:
        raise ValueError(f"Forecast horizon must be >= 1 month: got {horizon}")
    if not history:
        raise ValueError("Persistence baseline requires at least 1 historical observation")

    latest_val = history[-1]
    if latest_val is None or latest_val <= 0.0:
        raise ValueError(f"Latest observation must be a positive number: got {latest_val}")

    return float(latest_val), []


def predict_rolling_ma(
    history: List[float],
    window: int,
    horizon: int = 1,
) -> Tuple[float, List[str]]:
    """
    Rolling window moving average: y_hat_{t+h|t} = (1/k) * sum_{i=0}^{k-1} y_{t-i}.
    Requires at least `window` consecutive observations preceding origin.
    """
    if horizon < 1:
        raise ValueError(f"Forecast horizon must be >= 1 month: got {horizon}")
    if window < 1:
        raise ValueError(f"Rolling window size must be >= 1: got {window}")
    if len(history) < window:
        raise ValueError(
            f"Rolling MA-{window} requires at least {window} historical observations: got {len(history)}"
        )

    eval_window = history[-window:]
    for val in eval_window:
        if val is None or val <= 0.0:
            raise ValueError(f"Historical observations in rolling window must be strictly positive: got {val}")

    ma_val = sum(eval_window) / float(window)
    return round(ma_val, 4), []


def predict_linear_trend(
    history: List[float],
    window: int = 6,
    horizon: int = 1,
) -> Tuple[float, List[str]]:
    """
    Linear trend extrapolation via Ordinary Least Squares (OLS) over trailing `window` months:
    y_hat_{t+h|t} = alpha_t + beta_t * (window + horizon)
    where alpha_t and beta_t are estimated on x = [1, 2, ..., window].

    Boundary Constraint:
    Freight rates cannot be negative. If extrapolated y_hat <= 0, clamped to 0.10 USD/MT
    with flag FLAG_TREND_NEGATIVE_CLAMP attached.
    """
    if horizon < 1:
        raise ValueError(f"Forecast horizon must be >= 1 month: got {horizon}")
    if window < 2:
        raise ValueError(f"Linear trend requires at least 2 historical observations: got {window}")
    if len(history) < window:
        raise ValueError(
            f"Linear Trend-{window} requires at least {window} historical observations: got {len(history)}"
        )

    eval_window = history[-window:]
    for val in eval_window:
        if val is None or val <= 0.0:
            raise ValueError(f"Historical observations in trend window must be strictly positive: got {val}")

    # x coordinates: 1, 2, ..., window
    n = window
    x = list(range(1, n + 1))
    x_mean = (n + 1) / 2.0
    y_mean = sum(eval_window) / float(n)

    numerator = sum((x[i] - x_mean) * (eval_window[i] - y_mean) for i in range(n))
    denominator = sum((x[i] - x_mean) ** 2 for i in range(n))

    if denominator == 0.0:
        # Constant x: fallback to mean
        slope = 0.0
    else:
        slope = numerator / denominator

    intercept = y_mean - slope * x_mean

    # Target point: x = window + horizon
    target_x = window + horizon
    y_pred = intercept + slope * target_x

    flags: List[str] = []
    if y_pred <= 0.0:
        y_pred = MIN_TREND_RATE_CLAMP_USD_MT
        flags.append(FLAG_TREND_NEGATIVE_CLAMP)

    return round(y_pred, 4), flags


def predict_baseline(
    model_type: str,
    history: List[float],
    horizon: int = 1,
) -> Tuple[float, List[str]]:
    """
    High-level dispatcher for baseline predictions by model type.
    """
    norm_type = model_type.lower().strip()
    if norm_type in ("persistence", "m1"):
        return predict_persistence(history, horizon=horizon)
    elif norm_type in ("rolling_ma_3", "ma3", "m2_3"):
        return predict_rolling_ma(history, window=3, horizon=horizon)
    elif norm_type in ("rolling_ma_6", "ma6", "m2_6"):
        return predict_rolling_ma(history, window=6, horizon=horizon)
    elif norm_type in ("linear_trend_6", "trend6", "m3"):
        return predict_linear_trend(history, window=6, horizon=horizon)
    else:
        raise ValueError(f"Unsupported baseline model type: '{model_type}'")


def generate_baseline_forecast(
    route_code: str,
    origin_date: date,
    target_date: date,
    history: List[float],
    model_type: str,
    horizon_months: int = 1,
) -> BaselineForecastResult:
    """
    Generates a structured, validated BaselineForecastResult from an observation sequence.
    Rejects derived route spread codes to enforce route conditioning boundary.
    """
    if route_code in DISALLOWED_DERIVED_ROUTES:
        raise ValueError(
            f"Route '{route_code}' is a derived rate difference indicator and cannot be forecast directly as a corridor rate."
        )

    if route_code not in SUPPORTED_DIRECT_ROUTES:
        raise ValueError(
            f"Route '{route_code}' is not supported for baseline forecasting. Supported routes: {list(SUPPORTED_DIRECT_ROUTES)}"
        )

    try:
        pred_val, flags = predict_baseline(model_type, history, horizon=horizon_months)
        return BaselineForecastResult(
            route_code=route_code,
            origin_date=origin_date,
            target_date=target_date,
            horizon_months=horizon_months,
            model_type=model_type,
            predicted_value=pred_val,
            unit_of_measure="USD/MT",
            status=STATUS_FORECAST_GENERATED,
            flags=flags,
            history_count_observed=len(history),
            notes=None,
        )
    except ValueError as exc:
        return BaselineForecastResult(
            route_code=route_code,
            origin_date=origin_date,
            target_date=target_date,
            horizon_months=horizon_months,
            model_type=model_type,
            predicted_value=None,
            unit_of_measure="USD/MT",
            status=STATUS_INSUFFICIENT_HISTORY,
            flags=[],
            history_count_observed=len(history),
            notes=str(exc),
        )
