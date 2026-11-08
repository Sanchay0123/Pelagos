"""
Read-only query service for the Pelagos Experimental Freight Index.
Integrates DuckDB columnar processing over published Parquet partitions with relational SQL fallback.
Traces to REQ-NFR-ABM-002, REQ-FR-DII-005, and ML-001.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import date
import duckdb
from sqlalchemy.orm import Session

from pelagos.models import FreightObservation
from pelagos.storage.freight_analytics import get_published_freight_parquet_paths
from pelagos.forecasting.schemas import (
    SUPPORTED_DIRECT_ROUTES,
    DISALLOWED_DERIVED_ROUTES,
    DEFAULT_BASELINE_MODEL,
    SUPPORTED_BASELINE_MODELS,
    PRELIMINARY_EVALUATION_DISCLAIMER,
    RouteEvaluationReport,
    BaselineForecastResult,
)
from pelagos.forecasting.regimes import (
    HISTORICAL_REGIMES,
    MarketRegimeId,
    MarketRegimeDefinition,
    RegimeEvaluationMetrics,
    HorizonSensitivityMetrics,
    HorizonGrowthMetrics,
    classify_target_date_regime,
    compute_regime_metrics,
    compute_horizon_growth,
)
from pelagos.forecasting.baselines import generate_baseline_forecast
from pelagos.forecasting.evaluation import evaluate_route_baselines
from pelagos.forecasting.index_engine import (
    INDEX_CODE,
    INDEX_NAME,
    DEFAULT_BASE_DATE,
    DEFAULT_BASE_USG_RATE,
    DEFAULT_BASE_PNW_RATE,
    BENCHMARK_DISTANCE_USG_NM,
    BENCHMARK_DISTANCE_PNW_NM,
    compute_pegi_series,
)

logger = logging.getLogger(__name__)


def get_freight_index(
    db: Session,
    storage_dir: Optional[Path] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    base_date: date = DEFAULT_BASE_DATE,
) -> Dict[str, Any]:
    """
    Computes and returns the Pelagos Experimental Grain Index (PEGI-USJPN) series.
    Uses DuckDB over published Parquet partitions if available; falls back to SQL if not.
    """
    paths = get_published_freight_parquet_paths(db=db, storage_dir=storage_dir)
    monthly_inputs: List[Dict[str, Any]] = []
    source = "parquet_analytical"

    if paths:
        # Fast path: Query Parquet partitions via DuckDB
        files_clause = "[" + ", ".join(f"'{p}'" for p in paths) + "]"
        con = duckdb.connect(":memory:")
        try:
            query = f"""
            SELECT
                CAST(observation_date AS VARCHAR) AS obs_date,
                MAX(CASE WHEN route_code = 'USG-JPN-BULK' THEN rate_value END) AS usg_rate,
                MAX(CASE WHEN route_code = 'PNW-JPN-BULK' THEN rate_value END) AS pnw_rate
            FROM read_parquet({files_clause})
            GROUP BY observation_date
            ORDER BY observation_date ASC
            """
            rows = con.execute(query).fetchall()
            for r in rows:
                monthly_inputs.append({
                    "observation_date": date.fromisoformat(str(r[0])),
                    "usg_rate": float(r[1]) if r[1] is not None else None,
                    "pnw_rate": float(r[2]) if r[2] is not None else None,
                })
        except Exception as exc:
            logger.warning(f"DuckDB index query over Parquet failed, falling back to SQL: {exc}")
            monthly_inputs = []
        finally:
            con.close()

    if not monthly_inputs:
        # Fallback path: Query SQLite FreightObservation rows
        source = "sql_relational"
        obs_rows = (
            db.query(
                FreightObservation.observation_date,
                FreightObservation.route_code,
                FreightObservation.rate_value,
            )
            .filter(FreightObservation.route_code.in_(["USG-JPN-BULK", "PNW-JPN-BULK"]))
            .order_by(FreightObservation.observation_date.asc())
            .all()
        )

        by_date: Dict[date, Dict[str, Optional[float]]] = {}
        for obs_d, code, rate in obs_rows:
            by_date.setdefault(obs_d, {"usg_rate": None, "pnw_rate": None})
            if code == "USG-JPN-BULK":
                by_date[obs_d]["usg_rate"] = rate
            elif code == "PNW-JPN-BULK":
                by_date[obs_d]["pnw_rate"] = rate

        for obs_d in sorted(by_date.keys()):
            monthly_inputs.append({
                "observation_date": obs_d,
                "usg_rate": by_date[obs_d]["usg_rate"],
                "pnw_rate": by_date[obs_d]["pnw_rate"],
            })

    if not monthly_inputs:
        return {
            "source": "empty",
            "index_code": INDEX_CODE,
            "index_name": INDEX_NAME,
            "base_period": {
                "date": base_date.isoformat(),
                "base_usg_usd_mt": DEFAULT_BASE_USG_RATE,
                "base_pnw_usd_mt": DEFAULT_BASE_PNW_RATE,
                "base_pegi_usd_mt": round((DEFAULT_BASE_USG_RATE + DEFAULT_BASE_PNW_RATE) / 2.0, 4),
            },
            "records_count": 0,
            "calculated_count": 0,
            "insufficient_data_count": 0,
            "observations": [],
        }

    # Compute index series
    series = compute_pegi_series(monthly_inputs, base_date=base_date)

    # Apply date filters if requested
    if start_date:
        series = [r for r in series if r.observation_date >= start_date]
    if end_date:
        series = [r for r in series if r.observation_date <= end_date]

    # Resolve base values reported in metadata
    resolved_base_usg = series[0].base_usg_usd_mt if series else DEFAULT_BASE_USG_RATE
    resolved_base_pnw = series[0].base_pnw_usd_mt if series else DEFAULT_BASE_PNW_RATE
    base_pegi_usd_mt = round((resolved_base_usg + resolved_base_pnw) / 2.0, 4)

    return {
        "source": source,
        "index_code": INDEX_CODE,
        "index_name": INDEX_NAME,
        "primary_metric": "pegi_usd_mt = (USG + PNW) / 2",
        "relative_index": "pegi_points = 0.5 * ((USG / base_usg) + (PNW / base_pnw)) * 100",
        "spread_metric": "spread_usd_mt = USG - PNW (separate derived indicator)",
        "base_period": {
            "date": base_date.isoformat(),
            "base_usg_usd_mt": resolved_base_usg,
            "base_pnw_usd_mt": resolved_base_pnw,
            "base_pegi_usd_mt": base_pegi_usd_mt,
        },
        "distance_weighted_scenario": {
            "formula": "0.669349 * USG + 0.330651 * PNW",
            "benchmark_distances_nm": {
                "USG-JPN-BULK": BENCHMARK_DISTANCE_USG_NM,
                "PNW-JPN-BULK": BENCHMARK_DISTANCE_PNW_NM,
            },
            "status": "exploratory_scenario_only",
        },
        "records_count": len(series),
        "calculated_count": sum(1 for r in series if r.status == "calculated"),
        "insufficient_data_count": sum(1 for r in series if r.status == "insufficient_data"),
        "observations": [r.to_dict() for r in series],
    }


def get_route_rate_observations(
    db: Session,
    route_code: str,
    storage_dir: Optional[Path] = None,
    exclude_reconstructed: bool = False,
) -> Tuple[List[Tuple[date, float]], str]:
    """
    Retrieves chronological monthly observations for a directly observed route.
    Uses DuckDB over published Parquet partitions if available; falls back to SQL.
    Strictly forbids derived routes like SPREAD-USG-PNW-JPN.
    Optionally filters out reconstructed observations when exclude_reconstructed is True.
    """
    if route_code in DISALLOWED_DERIVED_ROUTES:
        raise ValueError(
            f"Route '{route_code}' is a derived rate difference and cannot be queried as a directly observed corridor rate."
        )

    if route_code not in SUPPORTED_DIRECT_ROUTES:
        raise ValueError(
            f"Route '{route_code}' is not supported. Supported routes: {list(SUPPORTED_DIRECT_ROUTES)}"
        )

    paths = get_published_freight_parquet_paths(db=db, storage_dir=storage_dir)
    obs: List[Tuple[date, float]] = []
    source = "parquet_analytical"

    if paths:
        files_clause = "[" + ", ".join(f"'{p}'" for p in paths) + "]"
        con = duckdb.connect(":memory:")
        try:
            query = f"""
            SELECT CAST(observation_date AS VARCHAR), rate_value
            FROM read_parquet({files_clause})
            WHERE route_code = '{route_code}'
            """
            if exclude_reconstructed:
                try:
                    # Filter if column exists in Parquet
                    query += " AND (is_reconstructed IS NULL OR is_reconstructed = false)"
                except Exception:
                    pass
            query += " ORDER BY observation_date ASC"

            rows = con.execute(query).fetchall()
            for r in rows:
                if r[0] is not None and r[1] is not None:
                    obs.append((date.fromisoformat(str(r[0])), float(r[1])))
        except Exception as exc:
            logger.warning(f"DuckDB query for route '{route_code}' failed, falling back to SQL: {exc}")
            obs = []
        finally:
            con.close()

    if not obs:
        source = "sql_relational"
        query = db.query(FreightObservation.observation_date, FreightObservation.rate_value).filter(
            FreightObservation.route_code == route_code
        )
        if exclude_reconstructed:
            query = query.filter(FreightObservation.is_reconstructed.is_(False))
        rows = query.order_by(FreightObservation.observation_date.asc()).all()
        for obs_d, rate in rows:
            if obs_d is not None and rate is not None:
                obs.append((obs_d, float(rate)))

    obs.sort(key=lambda x: x[0])
    return obs, source


def evaluate_route_baseline_models(
    db: Session,
    route_code: str,
    burn_in_months: int = 12,
    horizon: int = 1,
    storage_dir: Optional[Path] = None,
    include_steps: bool = False,
    exclude_reconstructed: bool = False,
    candidate_origins: Optional[List[date]] = None,
    allow_boundary_origins: bool = False,
) -> Dict[str, Any]:
    """
    Executes chronological calendar-aware walk-forward evaluation across all baseline models for a route.
    Supports comparative sensitivity evaluation with or without reconstructed observations.
    """
    obs, source = get_route_rate_observations(
        db=db,
        route_code=route_code,
        storage_dir=storage_dir,
        exclude_reconstructed=exclude_reconstructed,
    )
    if not obs:
        raise ValueError(f"No observations found for route '{route_code}'")

    report = evaluate_route_baselines(
        observations=obs,
        route_code=route_code,
        burn_in_months=burn_in_months,
        horizon=horizon,
        include_steps=include_steps,
        candidate_origins=candidate_origins,
        allow_boundary_origins=allow_boundary_origins,
    )
    result = report.to_dict()
    result["source"] = source
    result["status"] = "preliminary_evaluation"
    result["disclaimer"] = PRELIMINARY_EVALUATION_DISCLAIMER
    result["exclude_reconstructed"] = exclude_reconstructed
    result["horizon_months"] = horizon
    return result


def compare_route_reconstruction_sensitivity(
    db: Session,
    route_code: str,
    burn_in_months: int = 12,
    horizon: int = 1,
    storage_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Evaluates both Condition A (Included) and Condition B (Excluded) for a route,
    and calculates exact common-sample metrics on the intersection of valid target dates.
    """
    included_res = evaluate_route_baseline_models(
        db=db,
        route_code=route_code,
        burn_in_months=burn_in_months,
        horizon=horizon,
        storage_dir=storage_dir,
        include_steps=True,
        exclude_reconstructed=False,
    )
    excluded_res = evaluate_route_baseline_models(
        db=db,
        route_code=route_code,
        burn_in_months=burn_in_months,
        horizon=horizon,
        storage_dir=storage_dir,
        include_steps=True,
        exclude_reconstructed=True,
    )

    inc_steps = included_res.get("steps", {}).get("persistence", [])
    exc_steps = excluded_res.get("steps", {}).get("persistence", [])

    inc_target_dates = {s["target_date"] for s in inc_steps if s.get("is_valid", True)}
    exc_target_dates = {s["target_date"] for s in exc_steps if s.get("is_valid", True)}
    common_dates = sorted(inc_target_dates.intersection(exc_target_dates))

    # Common-sample metrics for Persistence
    def _metrics_for_steps(steps_list, allowed_dates):
        subset = [s for s in steps_list if s["target_date"] in allowed_dates and s.get("is_valid", True)]
        if not subset:
            return {"mae": 0.0, "rmse": 0.0, "mape": 0.0, "count": 0}
        acts = [s["actual_value"] for s in subset]
        preds = [s["predicted_value"] for s in subset]
        from pelagos.forecasting.evaluation import calculate_mae, calculate_rmse, calculate_mape
        return {
            "count": len(subset),
            "mae": calculate_mae(acts, preds),
            "rmse": calculate_rmse(acts, preds),
            "mape": calculate_mape(acts, preds),
        }

    return {
        "route_code": route_code,
        "horizon_months": horizon,
        "condition_a_included": {
            "total_observations": included_res["total_observations"],
            "evaluation_steps_count": included_res["evaluation_steps_count"],
            "models": included_res["models"],
        },
        "condition_b_excluded": {
            "total_observations": excluded_res["total_observations"],
            "evaluation_steps_count": excluded_res["evaluation_steps_count"],
            "models": excluded_res["models"],
        },
        "common_sample_persistence": {
            "common_target_dates_count": len(common_dates),
            "common_start_date": common_dates[0] if common_dates else None,
            "common_end_date": common_dates[-1] if common_dates else None,
            "condition_a_metrics": _metrics_for_steps(inc_steps, set(common_dates)),
            "condition_b_metrics": _metrics_for_steps(exc_steps, set(common_dates)),
        },
    }


def forecast_next_route_rate(
    db: Session,
    route_code: str,
    model_type: Optional[str] = None,
    origin_date: Optional[date] = None,
    horizon_months: int = 1,
    storage_dir: Optional[Path] = None,
    exclude_reconstructed: bool = False,
) -> Dict[str, Any]:
    """
    Generates next-horizon point forecast for a route using observations available on or before origin_date.
    If model_type is omitted, defaults to 'persistence'.
    If origin_date is omitted, defaults to latest observed date in the series.
    Zero-lookahead guarantee: observations after origin_date are strictly excluded from history.
    """
    selected_model = (model_type or DEFAULT_BASELINE_MODEL).lower().strip()
    default_model_applied = model_type is None

    obs, source = get_route_rate_observations(
        db=db,
        route_code=route_code,
        storage_dir=storage_dir,
        exclude_reconstructed=exclude_reconstructed,
    )
    if not obs:
        raise ValueError(f"No observations found for route '{route_code}'")

    if origin_date is not None:
        filtered_obs = [(d, r) for d, r in obs if d <= origin_date]
        if not filtered_obs:
            raise ValueError(
                f"No observations available on or before requested origin_date {origin_date.isoformat()}"
            )
        resolved_origin = origin_date
        history = [rate for _, rate in filtered_obs]
    else:
        resolved_origin, _ = obs[-1]
        history = [rate for _, rate in obs]

    # Target date: resolved_origin + horizon_months (anchored to 1st of month)
    y = resolved_origin.year + (resolved_origin.month + horizon_months - 1) // 12
    m = (resolved_origin.month + horizon_months - 1) % 12 + 1
    target_date = date(y, m, 1)

    res = generate_baseline_forecast(
        route_code=route_code,
        origin_date=resolved_origin,
        target_date=target_date,
        history=history,
        model_type=selected_model,
        horizon_months=horizon_months,
    )
    d = res.to_dict()
    d["source"] = source
    d["default_model_applied"] = default_model_applied
    d["exclude_reconstructed"] = exclude_reconstructed
    return d


def get_freight_reconciliation_audit(db: Session) -> Dict[str, Any]:
    """
    Returns an auditable summary of all reconstructed freight observations in the database.
    Traces to C1.1 auditable historical rate reconciliation.
    """
    rows = (
        db.query(FreightObservation)
        .filter(FreightObservation.is_reconstructed.is_(True))
        .order_by(FreightObservation.observation_date.asc())
        .all()
    )
    items = []
    for r in rows:
        meta = json.loads(r.reconciliation_metadata) if r.reconciliation_metadata else {}
        items.append({
            "observation_date": r.observation_date.isoformat(),
            "route_code": r.route_code,
            "canonical_rate_value": r.rate_value,
            "original_rate_value": r.original_rate_value,
            "reconciliation_method": r.reconciliation_method,
            "metadata": meta,
        })
    return {
        "reconstructed_count": len(items),
        "observations": items,
    }


def get_regimes_metadata() -> List[Dict[str, Any]]:
    """Returns metadata for all configured historical macroeconomic market regimes."""
    return [r.to_dict() for r in HISTORICAL_REGIMES]


def analyze_route_regimes_and_horizons(
    db: Session,
    route_code: str,
    reconstruction_condition: str = "included",
    horizons: Tuple[int, ...] = (1, 3, 6),
    burn_in_months: int = 12,
    storage_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Executes formal macroeconomic regime segmentation and forecast horizon sensitivity analysis.
    Evaluates the operational Persistence baseline across fixed historical regimes
    and analyzes error growth across horizons (T+1, T+3, T+6).
    Traces to Pelagos C1.3.2 requirements.
    """
    if route_code in DISALLOWED_DERIVED_ROUTES:
        raise ValueError(
            f"Route '{route_code}' is a derived spread indicator and cannot be evaluated directly as a corridor rate."
        )
    if route_code not in SUPPORTED_DIRECT_ROUTES:
        raise ValueError(
            f"Route '{route_code}' is not supported for regime evaluation. Supported routes: {list(SUPPORTED_DIRECT_ROUTES)}"
        )

    norm_condition = reconstruction_condition.lower().strip()
    if norm_condition not in ("included", "excluded", "common_sample"):
        raise ValueError(
            f"reconstruction_condition must be 'included', 'excluded', or 'common_sample': got '{reconstruction_condition}'"
        )

    from pelagos.forecasting.evaluation import evaluate_walk_forward, calculate_mae, calculate_rmse, calculate_mape

    # 1. Obtain observations based on condition
    if norm_condition in ("included", "common_sample"):
        obs_inc, src_inc = get_route_rate_observations(db, route_code, storage_dir=storage_dir, exclude_reconstructed=False)
    if norm_condition in ("excluded", "common_sample"):
        obs_exc, src_exc = get_route_rate_observations(db, route_code, storage_dir=storage_dir, exclude_reconstructed=True)

    if norm_condition == "included":
        eval_obs = obs_inc
        data_source = src_inc
    elif norm_condition == "excluded":
        eval_obs = obs_exc
        data_source = src_exc
    else:
        eval_obs = obs_inc
        data_source = f"common_sample_intersection({src_inc})"

    overall_by_horizon: Dict[str, Dict[str, Any]] = {}
    horizon_metrics_objs: Dict[int, HorizonSensitivityMetrics] = {}
    by_regime_and_horizon: Dict[str, Dict[str, Dict[str, Any]]] = {
        r.regime_id: {} for r in HISTORICAL_REGIMES
    }
    regime_metrics_objs: Dict[str, Dict[int, RegimeEvaluationMetrics]] = {
        r.regime_id: {} for r in HISTORICAL_REGIMES
    }
    outside_coverage_counts: Dict[str, int] = {}

    for h in horizons:
        h_key = f"T+{h}"
        if norm_condition == "common_sample":
            _, steps_inc_raw = evaluate_walk_forward(obs_inc, "persistence", burn_in_months=burn_in_months, horizon=h, include_skipped_steps=True)
            _, steps_exc_raw = evaluate_walk_forward(obs_exc, "persistence", burn_in_months=burn_in_months, horizon=h, include_skipped_steps=True)
            common_target_dates = {s.target_date for s in steps_inc_raw if s.is_valid}.intersection(
                {s.target_date for s in steps_exc_raw if s.is_valid}
            )
            filtered_steps = [s for s in steps_inc_raw if s.target_date in common_target_dates and s.is_valid]
            steps = filtered_steps
            actuals = [s.actual_value for s in filtered_steps if s.actual_value is not None]
            preds = [s.predicted_value for s in filtered_steps if s.predicted_value is not None]
            mae = calculate_mae(actuals, preds) if actuals else 0.0
            rmse = calculate_rmse(actuals, preds) if actuals else 0.0
            mape = calculate_mape(actuals, preds) if actuals else 0.0
            valid_mases = [s.step_mase for s in filtered_steps if s.step_mase is not None]
            mase = round(sum(valid_mases) / len(valid_mases), 2) if valid_mases else None
            h_metrics = HorizonSensitivityMetrics(
                horizon_months=h,
                total_eligible_steps=len(filtered_steps),
                valid_steps_count=len(filtered_steps),
                skipped_steps_count=0,
                skipped_reasons={},
                mae=mae,
                rmse=rmse,
                mape=mape,
                mase=mase,
            )
        else:
            summary, steps = evaluate_walk_forward(
                observations=eval_obs,
                model_type="persistence",
                burn_in_months=burn_in_months,
                horizon=h,
                include_skipped_steps=True,
            )
            h_metrics = HorizonSensitivityMetrics(
                horizon_months=h,
                total_eligible_steps=summary.valid_steps_count + summary.skipped_steps_count,
                valid_steps_count=summary.valid_steps_count,
                skipped_steps_count=summary.skipped_steps_count,
                skipped_reasons=summary.skipped_reasons,
                mae=summary.mae,
                rmse=summary.rmse,
                mape=summary.mape,
                mase=summary.mase,
            )

        horizon_metrics_objs[h] = h_metrics
        overall_by_horizon[h_key] = h_metrics.to_dict()

        outside_count = sum(1 for s in steps if classify_target_date_regime(s.target_date) is None)
        outside_coverage_counts[h_key] = outside_count

        for regime in HISTORICAL_REGIMES:
            r_metrics = compute_regime_metrics(regime, steps)
            regime_metrics_objs[regime.regime_id][h] = r_metrics
            by_regime_and_horizon[regime.regime_id][h_key] = r_metrics.to_dict()

    overall_growth: List[Dict[str, Any]] = []
    horizon_pairs = [(1, 3), (1, 6), (3, 6)]
    for base_h, target_h in horizon_pairs:
        if base_h in horizon_metrics_objs and target_h in horizon_metrics_objs:
            growth_obj = compute_horizon_growth(horizon_metrics_objs[base_h], horizon_metrics_objs[target_h])
            overall_growth.append(growth_obj.to_dict())

    regime_growth: Dict[str, List[Dict[str, Any]]] = {}
    for regime in HISTORICAL_REGIMES:
        regime_growth[regime.regime_id] = []
        r_dict = regime_metrics_objs[regime.regime_id]
        for base_h, target_h in horizon_pairs:
            if base_h in r_dict and target_h in r_dict:
                b_metrics = r_dict[base_h]
                t_metrics = r_dict[target_h]
                if b_metrics.mae is not None and t_metrics.mae is not None:
                    base_sens = HorizonSensitivityMetrics(
                        horizon_months=base_h,
                        total_eligible_steps=b_metrics.total_eligible_steps,
                        valid_steps_count=b_metrics.valid_steps_count,
                        skipped_steps_count=b_metrics.skipped_steps_count,
                        skipped_reasons=b_metrics.skipped_reasons,
                        mae=b_metrics.mae,
                        rmse=b_metrics.rmse or 0.0,
                        mape=b_metrics.mape or 0.0,
                        mase=b_metrics.mase,
                    )
                    t_sens = HorizonSensitivityMetrics(
                        horizon_months=target_h,
                        total_eligible_steps=t_metrics.total_eligible_steps,
                        valid_steps_count=t_metrics.valid_steps_count,
                        skipped_steps_count=t_metrics.skipped_steps_count,
                        skipped_reasons=t_metrics.skipped_reasons,
                        mae=t_metrics.mae,
                        rmse=t_metrics.rmse or 0.0,
                        mape=t_metrics.mape or 0.0,
                        mase=t_metrics.mase,
                    )
                    rg_obj = compute_horizon_growth(base_sens, t_sens)
                    regime_growth[regime.regime_id].append(rg_obj.to_dict())

    return {
        "route_code": route_code,
        "reconstruction_condition": norm_condition,
        "data_source": data_source,
        "burn_in_months": burn_in_months,
        "horizons": list(horizons),
        "overall_horizon_sensitivity": overall_by_horizon,
        "overall_horizon_growth": overall_growth,
        "by_regime_and_horizon": by_regime_and_horizon,
        "regime_horizon_growth": regime_growth,
        "outside_coverage_steps_count": outside_coverage_counts,
        "status": "regime_and_horizon_analysis_completed",
    }
