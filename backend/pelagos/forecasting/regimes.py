"""
Pelagos Macroeconomic Historical Market Regime Definitions and Analysis Engine.
Provides centralized, documented historical analytical periods and evaluation segmentation.
Traces to Pelagos C1.3.2 requirements.

NOTE: These periods represent fixed historical date segments established for analytical
benchmarking and forecast horizon sensitivity analysis. They are NOT causal explanations
for individual freight rate price shocks.
"""

from enum import Enum
from datetime import date
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

from pelagos.forecasting.schemas import EvaluationStep
from pelagos.forecasting.evaluation import calculate_mae, calculate_rmse, calculate_mape


class MarketRegimeId(str, Enum):
    """Supported historical macroeconomic market regime identifiers."""
    PRE_SUPERCYCLE = "PRE_SUPERCYCLE"
    SUPERCYCLE_PEAK_CRASH = "SUPERCYCLE_PEAK_CRASH"
    POST_SUPERCYCLE = "POST_SUPERCYCLE"
    PANDEMIC_DISRUPTION = "PANDEMIC_DISRUPTION"


@dataclass(frozen=True)
class MarketRegimeDefinition:
    """Fixed analytical historical market regime period definition."""
    regime_id: str
    display_name: str
    start_date: date
    end_date: date
    description: str

    def contains_target_date(self, target_date: date) -> bool:
        """Inclusive check: start_date <= target_date <= end_date."""
        return self.start_date <= target_date <= self.end_date

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime_id": self.regime_id,
            "display_name": self.display_name,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "description": self.description,
        }


# Single source of truth for historical macroeconomic market regimes
HISTORICAL_REGIMES: Tuple[MarketRegimeDefinition, ...] = (
    MarketRegimeDefinition(
        regime_id=MarketRegimeId.PRE_SUPERCYCLE.value,
        display_name="Pre-Supercycle",
        start_date=date(1996, 1, 1),
        end_date=date(2006, 12, 1),
        description=(
            "Historical baseline period preceding the mid-2000s commodity boom. "
            "Characterized by moderate spot freight rates and stable seasonal grain shipping cycles."
        ),
    ),
    MarketRegimeDefinition(
        regime_id=MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value,
        display_name="Supercycle Peak & Crash",
        start_date=date(2007, 1, 1),
        end_date=date(2009, 12, 1),
        description=(
            "Extreme commodity market expansion driven by rapid industrial demand followed by the "
            "2008 global financial crisis and sharp freight rate collapse."
        ),
    ),
    MarketRegimeDefinition(
        regime_id=MarketRegimeId.POST_SUPERCYCLE.value,
        display_name="Post-Supercycle",
        start_date=date(2010, 1, 1),
        end_date=date(2019, 12, 1),
        description=(
            "Post-financial crisis prolonged freight overcapacity and fleet expansion. "
            "Characterized by lower spot freight volatility and subdued real grain rates."
        ),
    ),
    MarketRegimeDefinition(
        regime_id=MarketRegimeId.PANDEMIC_DISRUPTION.value,
        display_name="Pandemic & Disruption",
        start_date=date(2020, 1, 1),
        end_date=date(2026, 10, 1),
        description=(
            "COVID-19 logistical bottlenecks, port congestion, bunker price fluctuations, "
            "and subsequent geopolitical supply-chain disruptions."
        ),
    ),
)

MIN_DESCRIPTIVE_SAMPLE_THRESHOLD = 12


def classify_target_date_regime(target_date: date) -> Optional[MarketRegimeDefinition]:
    """
    Classifies a forecast target date into its exactly matching market regime.
    Returns None if target_date falls outside configured regime coverage.
    Ensures every covered target date belongs to exactly one regime.
    """
    for regime in HISTORICAL_REGIMES:
        if regime.contains_target_date(target_date):
            return regime
    return None


def get_regime_by_id(regime_id: str) -> Optional[MarketRegimeDefinition]:
    """Retrieves a regime definition by its string identifier."""
    norm = regime_id.upper().strip()
    for regime in HISTORICAL_REGIMES:
        if regime.regime_id == norm:
            return regime
    return None


@dataclass(frozen=True)
class RegimeEvaluationMetrics:
    """Summary forecast evaluation metrics for a specific regime and horizon."""
    regime_id: str
    display_name: str
    start_date: date
    end_date: date
    total_eligible_steps: int
    valid_steps_count: int
    skipped_steps_count: int
    skipped_reasons: Dict[str, int]
    mae: Optional[float]
    rmse: Optional[float]
    mape: Optional[float]
    mase: Optional[float]
    flat_prediction_count: int
    flat_prediction_rate: float
    flat_actual_movement_count: int
    flat_actual_movement_rate: float
    non_flat_actual_movement_count: int
    eligible_directional_steps_count: int
    correct_directional_predictions: int
    directional_accuracy_non_flat: Optional[float]
    is_sample_sufficient: bool = True
    sample_warning: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["start_date"] = self.start_date.isoformat()
        d["end_date"] = self.end_date.isoformat()
        return d


@dataclass(frozen=True)
class HorizonSensitivityMetrics:
    """Error metrics for an entire corridor condition at a specific forecast horizon."""
    horizon_months: int
    total_eligible_steps: int
    valid_steps_count: int
    skipped_steps_count: int
    skipped_reasons: Dict[str, int]
    mae: float
    rmse: float
    mape: float
    mase: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HorizonGrowthMetrics:
    """Comparative error growth between two forecast horizons."""
    base_horizon: int
    target_horizon: int
    absolute_mae_diff: float
    relative_mae_increase_pct: Optional[float]
    absolute_rmse_diff: float
    relative_rmse_increase_pct: Optional[float]
    absolute_mape_diff: float
    relative_mape_increase_pct: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_regime_metrics(
    regime: MarketRegimeDefinition,
    steps: List[EvaluationStep],
    min_sample_threshold: int = MIN_DESCRIPTIVE_SAMPLE_THRESHOLD,
) -> RegimeEvaluationMetrics:
    """
    Computes summary evaluation metrics strictly for steps attributed to a given regime
    based on target_date. Handles insufficient sample counts and zero division gracefully.
    """
    # Group steps attributed to this regime by target_date
    regime_steps = [s for s in steps if regime.contains_target_date(s.target_date)]
    total_eligible = len(regime_steps)
    valid_steps = [s for s in regime_steps if s.is_valid and s.actual_value is not None]
    skipped_steps = [s for s in regime_steps if not s.is_valid]

    skipped_reasons: Dict[str, int] = {}
    for s in skipped_steps:
        r = s.skip_reason or "unknown"
        skipped_reasons[r] = skipped_reasons.get(r, 0) + 1

    valid_count = len(valid_steps)
    skipped_count = len(skipped_steps)
    is_sufficient = valid_count >= min_sample_threshold
    sample_warning = (
        None if is_sufficient
        else f"Insufficient sample size (N={valid_count} < {min_sample_threshold}) for robust regime metrics"
    )

    if valid_count > 0:
        actuals = [s.actual_value for s in valid_steps]
        preds = [s.predicted_value for s in valid_steps]
        mae = calculate_mae(actuals, preds)
        rmse = calculate_rmse(actuals, preds)
        mape = calculate_mape(actuals, preds)

        valid_mases = [s.step_mase for s in valid_steps if s.step_mase is not None]
        mase = round(sum(valid_mases) / len(valid_mases), 2) if valid_mases else None

        flat_pred_count = sum(
            1 for s in valid_steps
            if s.predicted_change is not None and abs(s.predicted_change) < 1e-6
        )
        flat_pred_rate = round(flat_pred_count / valid_count, 4)

        flat_act_count = sum(
            1 for s in valid_steps
            if s.actual_change is not None and abs(s.actual_change) < 1e-6
        )
        flat_act_rate = round(flat_act_count / valid_count, 4)
        non_flat_act_count = valid_count - flat_act_count

        eligible_directional = [s for s in valid_steps if s.direction_concordant is not None]
        eligible_directional_count = len(eligible_directional)
        correct_directional = sum(1 for s in eligible_directional if s.direction_concordant is True)
        if eligible_directional_count > 0:
            mda_non_flat = round((correct_directional / eligible_directional_count) * 100.0, 2)
        else:
            mda_non_flat = None
    else:
        mae = None
        rmse = None
        mape = None
        mase = None
        flat_pred_count = 0
        flat_pred_rate = 0.0
        flat_act_count = 0
        flat_act_rate = 0.0
        non_flat_act_count = 0
        eligible_directional_count = 0
        correct_directional = 0
        mda_non_flat = None

    return RegimeEvaluationMetrics(
        regime_id=regime.regime_id,
        display_name=regime.display_name,
        start_date=regime.start_date,
        end_date=regime.end_date,
        total_eligible_steps=total_eligible,
        valid_steps_count=valid_count,
        skipped_steps_count=skipped_count,
        skipped_reasons=skipped_reasons,
        mae=mae,
        rmse=rmse,
        mape=mape,
        mase=mase,
        flat_prediction_count=flat_pred_count,
        flat_prediction_rate=flat_pred_rate,
        flat_actual_movement_count=flat_act_count,
        flat_actual_movement_rate=flat_act_rate,
        non_flat_actual_movement_count=non_flat_act_count,
        eligible_directional_steps_count=eligible_directional_count,
        correct_directional_predictions=correct_directional,
        directional_accuracy_non_flat=mda_non_flat,
        is_sample_sufficient=is_sufficient,
        sample_warning=sample_warning,
    )


def compute_horizon_growth(
    base: HorizonSensitivityMetrics,
    target: HorizonSensitivityMetrics,
) -> HorizonGrowthMetrics:
    """Calculates absolute and relative error growth between two forecast horizons."""
    abs_mae_diff = round(target.mae - base.mae, 2)
    rel_mae_inc = round((abs_mae_diff / base.mae) * 100.0, 2) if base.mae > 0 else None

    abs_rmse_diff = round(target.rmse - base.rmse, 2)
    rel_rmse_inc = round((abs_rmse_diff / base.rmse) * 100.0, 2) if base.rmse > 0 else None

    abs_mape_diff = round(target.mape - base.mape, 2)
    rel_mape_inc = round((abs_mape_diff / base.mape) * 100.0, 2) if base.mape > 0 else None

    return HorizonGrowthMetrics(
        base_horizon=base.horizon_months,
        target_horizon=target.horizon_months,
        absolute_mae_diff=abs_mae_diff,
        relative_mae_increase_pct=rel_mae_inc,
        absolute_rmse_diff=abs_rmse_diff,
        relative_rmse_increase_pct=rel_rmse_inc,
        absolute_mape_diff=abs_mape_diff,
        relative_mape_increase_pct=rel_mape_inc,
    )

