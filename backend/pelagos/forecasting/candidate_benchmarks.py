"""
Pelagos Candidate Forecast Model Benchmarking Engine.
Executes rigorous, leakage-free empirical benchmarking of candidate forecasting models
against the Persistence (M1) operational baseline:
- M1: Persistence (Operational Baseline)
- M2: Ridge AR-1 (L2 Regularized Autoregressive AR(1), alpha=1.0)
- M3a: Rolling MA-3 (Trailing 3-Month Moving Average)
- M3b: Rolling MA-6 (Trailing 6-Month Moving Average)
- M4: Linear Trend-6 (Trailing 6-Month OLS Trend with non-negative clamp)

Evaluates performance across:
- Monitored grain corridors (USG-JPN-BULK, PNW-JPN-BULK)
- Horizons (T+1, T+3, T+6)
- Reconciliation conditions (Condition A: Included, Condition B: Excluded)
- Macroeconomic regimes (PRE_SUPERCYCLE, SUPERCYCLE_PEAK_CRASH, POST_SUPERCYCLE, PANDEMIC_DISRUPTION, OVERALL)
- Dependency-aware statistical hypothesis testing (DM test, HLN adjustment, Block Bootstrap CI)

Traces to REQ-FR-FRF-001, ML-001 Section 5.3, and Pelagos C1.3.3.
"""

from typing import Dict, Any, List, Optional, Tuple, Set
from datetime import date
from pathlib import Path
from sqlalchemy.orm import Session

from pelagos.forecasting.schemas import (
    BaselineModelType,
    EvaluationStep,
    ModelEvaluationSummary,
    CommonSampleComparisonReport,
    SUPPORTED_DIRECT_ROUTES,
    DISALLOWED_DERIVED_ROUTES,
)
from pelagos.forecasting.evaluation import (
    evaluate_walk_forward,
    evaluate_common_sample_comparison,
    calculate_mae,
    calculate_rmse,
    calculate_mape,
)
from pelagos.forecasting.regimes import (
    HISTORICAL_REGIMES,
    MarketRegimeDefinition,
    compute_regime_metrics,
)
from pelagos.forecasting.statistical_testing import (
    compare_candidate_to_baseline,
    StatisticalComparisonResult,
    RESEARCH_CLASSIFICATION_PROMISING,
    RESEARCH_CLASSIFICATION_INCONCLUSIVE,
    RESEARCH_CLASSIFICATION_NOT_BETTER,
)
from pelagos.forecasting.service import get_route_rate_observations

BENCHMARK_CANDIDATE_MODELS = (
    BaselineModelType.PERSISTENCE.value,
    BaselineModelType.RIDGE_AR.value,
    BaselineModelType.ROLLING_MA_3.value,
    BaselineModelType.ROLLING_MA_6.value,
    BaselineModelType.LINEAR_TREND_6.value,
)


def benchmark_route_candidates(
    observations: List[Tuple[date, float]],
    route_code: str,
    horizon: int = 1,
    burn_in_months: int = 12,
    models: Tuple[str, ...] = BENCHMARK_CANDIDATE_MODELS,
    condition_label: Optional[str] = None,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Benchmarks candidate models against Persistence baseline for a given route and horizon.
    Returns:
    - Individual walk-forward evaluation summaries and step-level series
    - Strict common-sample comparison across all models
    - Statistical testing against Persistence on the common target dates
    - Breakdown by macroeconomic regimes
    """
    if route_code in DISALLOWED_DERIVED_ROUTES:
        raise ValueError(
            f"Route '{route_code}' is a derived spread indicator and cannot be evaluated directly as a corridor rate."
        )
    if route_code not in SUPPORTED_DIRECT_ROUTES:
        raise ValueError(
            f"Route '{route_code}' is not supported. Supported routes: {list(SUPPORTED_DIRECT_ROUTES)}"
        )

    # 1. Run walk-forward evaluation for each model
    individual_summaries: Dict[str, ModelEvaluationSummary] = {}
    model_steps: Dict[str, List[EvaluationStep]] = {}

    for m_type in models:
        summary, steps = evaluate_walk_forward(
            observations=observations,
            model_type=m_type,
            route_code=route_code,
            burn_in_months=burn_in_months,
            horizon=horizon,
            include_skipped_steps=True,
        )
        individual_summaries[m_type] = summary
        model_steps[m_type] = steps

    # 2. Compute exact common-sample comparison across all evaluated models
    common_report = evaluate_common_sample_comparison(
        models_steps=model_steps,
        route_code=route_code,
        horizon=horizon,
        burn_in_months=burn_in_months,
    )

    # 3. Statistical comparisons against Persistence (M1) on the common target dates
    stat_comparisons: Dict[str, StatisticalComparisonResult] = {}
    p_steps = model_steps.get(BaselineModelType.PERSISTENCE.value, [])

    for m_type in models:
        if m_type == BaselineModelType.PERSISTENCE.value:
            continue
        c_steps = model_steps.get(m_type, [])
        comp_res = compare_candidate_to_baseline(
            baseline_steps=p_steps,
            candidate_steps=c_steps,
            model_b=m_type,
            route_code=route_code,
            horizon_months=horizon,
            model_a=BaselineModelType.PERSISTENCE.value,
            loss_type="absolute",
            seed=seed,
            condition=condition_label,
        )
        stat_comparisons[m_type] = comp_res

    # 4. Regime breakdown for all models
    regime_results: Dict[str, Dict[str, Any]] = {}
    for reg in HISTORICAL_REGIMES:
        r_dict: Dict[str, Any] = {
            "display_name": reg.display_name,
            "start_date": reg.start_date.isoformat(),
            "end_date": reg.end_date.isoformat(),
            "models": {},
            "statistical_comparisons": {},
        }
        # Regime metrics for each model
        for m_type in models:
            m_metrics = compute_regime_metrics(reg, model_steps[m_type])
            r_dict["models"][m_type] = m_metrics.to_dict()

        # Regime statistical comparisons vs Persistence
        for m_type in models:
            if m_type == BaselineModelType.PERSISTENCE.value:
                continue
            reg_comp = compare_candidate_to_baseline(
                baseline_steps=[s for s in p_steps if s.is_valid and reg.contains_target_date(s.target_date)],
                candidate_steps=[s for s in model_steps[m_type] if s.is_valid and reg.contains_target_date(s.target_date)],
                model_b=m_type,
                route_code=route_code,
                horizon_months=horizon,
                model_a=BaselineModelType.PERSISTENCE.value,
                loss_type="absolute",
                seed=seed,
                condition=condition_label,
                regime_id=reg.regime_id,
            )
            r_dict["statistical_comparisons"][m_type] = reg_comp.to_dict()

        regime_results[reg.regime_id] = r_dict

    return {
        "route_code": route_code,
        "horizon_months": horizon,
        "condition": condition_label,
        "individual_summaries": {k: v.to_dict() for k, v in individual_summaries.items()},
        "common_sample": common_report.to_dict(),
        "statistical_comparisons": {k: v.to_dict() for k, v in stat_comparisons.items()},
        "by_regime": regime_results,
    }


def execute_comprehensive_candidate_benchmarks(
    db: Session,
    routes: Tuple[str, ...] = SUPPORTED_DIRECT_ROUTES,
    horizons: Tuple[int, ...] = (1, 3, 6),
    burn_in_months: int = 12,
    storage_dir: Optional[Path] = None,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Executes full benchmarking grid across corridors, conditions, and horizons.
    Returns complete structured dataset for C1.3.3 analysis and report generation.
    """
    results: Dict[str, Any] = {
        "benchmark_models": list(BENCHMARK_CANDIDATE_MODELS),
        "routes": list(routes),
        "horizons": list(horizons),
        "burn_in_months": burn_in_months,
        "experiments": {},
    }

    for route in routes:
        conditions = [
            ("condition_a_included", False, "Condition A (Included Canonical)"),
            ("condition_b_excluded", True, "Condition B (Excluded Raw Gaps)"),
        ]

        for cond_id, exclude_recon, cond_label in conditions:
            obs, source = get_route_rate_observations(
                db=db,
                route_code=route,
                storage_dir=storage_dir,
                exclude_reconstructed=exclude_recon,
            )
            if not obs:
                continue

            for h in horizons:
                exp_key = f"{route}_{cond_id}_T{h}"
                bench_res = benchmark_route_candidates(
                    observations=obs,
                    route_code=route,
                    horizon=h,
                    burn_in_months=burn_in_months,
                    condition_label=cond_label,
                    seed=seed,
                )
                bench_res["data_source"] = source
                bench_res["exclude_reconstructed"] = exclude_recon
                bench_res["total_observations"] = len(obs)
                results["experiments"][exp_key] = bench_res

    return results

