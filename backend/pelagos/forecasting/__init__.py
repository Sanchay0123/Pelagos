"""
Pelagos Forecasting and Index Engine.
Encapsulated domain package for freight market indices and predictive baseline models.
Traces to REQ-FR-FRF-001, REQ-FR-DII-005, and ML-001.
"""

from pelagos.forecasting.index_engine import (
    DEFAULT_BASE_DATE,
    DEFAULT_BASE_USG_RATE,
    DEFAULT_BASE_PNW_RATE,
    BENCHMARK_DISTANCE_USG_NM,
    BENCHMARK_DISTANCE_PNW_NM,
    INDEX_CODE,
    INDEX_NAME,
    STATUS_CALCULATED,
    STATUS_INSUFFICIENT_DATA,
    FreightIndexRecord,
    calculate_pegi_point,
    compute_pegi_series,
)
from pelagos.forecasting.schemas import (
    BaselineModelType,
    BaselineForecastResult,
    EvaluationStep,
    ModelEvaluationSummary,
    RouteEvaluationReport,
    SUPPORTED_DIRECT_ROUTES,
    DISALLOWED_DERIVED_ROUTES,
    STATUS_FORECAST_GENERATED,
    STATUS_INSUFFICIENT_HISTORY,
    FLAG_TREND_NEGATIVE_CLAMP,
)
from pelagos.forecasting.baselines import (
    predict_persistence,
    predict_rolling_ma,
    predict_linear_trend,
    predict_baseline,
    generate_baseline_forecast,
)
from pelagos.forecasting.evaluation import (
    calculate_mae,
    calculate_rmse,
    calculate_mape,
    evaluate_walk_forward,
    evaluate_route_baselines,
)
from pelagos.forecasting.service import (
    get_freight_index,
    get_route_rate_observations,
    evaluate_route_baseline_models,
    forecast_next_route_rate,
)

__all__ = [
    # Index Engine
    "DEFAULT_BASE_DATE",
    "DEFAULT_BASE_USG_RATE",
    "DEFAULT_BASE_PNW_RATE",
    "BENCHMARK_DISTANCE_USG_NM",
    "BENCHMARK_DISTANCE_PNW_NM",
    "INDEX_CODE",
    "INDEX_NAME",
    "STATUS_CALCULATED",
    "STATUS_INSUFFICIENT_DATA",
    "FreightIndexRecord",
    "calculate_pegi_point",
    "compute_pegi_series",
    # Forecasting Schemas
    "BaselineModelType",
    "BaselineForecastResult",
    "EvaluationStep",
    "ModelEvaluationSummary",
    "RouteEvaluationReport",
    "SUPPORTED_DIRECT_ROUTES",
    "DISALLOWED_DERIVED_ROUTES",
    "STATUS_FORECAST_GENERATED",
    "STATUS_INSUFFICIENT_HISTORY",
    "FLAG_TREND_NEGATIVE_CLAMP",
    # Forecasting Baselines
    "predict_persistence",
    "predict_rolling_ma",
    "predict_linear_trend",
    "predict_baseline",
    "generate_baseline_forecast",
    # Evaluation
    "calculate_mae",
    "calculate_rmse",
    "calculate_mape",
    "evaluate_walk_forward",
    "evaluate_route_baselines",
    # Service
    "get_freight_index",
    "get_route_rate_observations",
    "evaluate_route_baseline_models",
    "forecast_next_route_rate",
]
