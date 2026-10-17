"""
Read-only query service for the Pelagos Experimental Freight Index.
Integrates DuckDB columnar processing over published Parquet partitions with relational SQL fallback.
Traces to REQ-NFR-ABM-002, REQ-FR-DII-005, and ML-001.
"""

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
) -> Tuple[List[Tuple[date, float]], str]:
    """
    Retrieves chronological monthly observations for a directly observed route.
    Uses DuckDB over published Parquet partitions if available; falls back to SQL.
    Strictly forbids derived routes like SPREAD-USG-PNW-JPN.
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
            ORDER BY observation_date ASC
            """
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
        rows = (
            db.query(FreightObservation.observation_date, FreightObservation.rate_value)
            .filter(FreightObservation.route_code == route_code)
            .order_by(FreightObservation.observation_date.asc())
            .all()
        )
        for obs_d, rate in rows:
            if obs_d is not None and rate is not None:
                obs.append((obs_d, float(rate)))

    obs.sort(key=lambda x: x[0])
    return obs, source


def evaluate_route_baseline_models(
    db: Session,
    route_code: str,
    burn_in_months: int = 12,
    storage_dir: Optional[Path] = None,
    include_steps: bool = False,
) -> Dict[str, Any]:
    """
    Executes chronological walk-forward evaluation across all baseline models for a route.
    """
    obs, source = get_route_rate_observations(db=db, route_code=route_code, storage_dir=storage_dir)
    if not obs:
        raise ValueError(f"No observations found for route '{route_code}'")

    report = evaluate_route_baselines(
        observations=obs,
        route_code=route_code,
        burn_in_months=burn_in_months,
        include_steps=include_steps,
    )
    result = report.to_dict()
    result["source"] = source
    result["status"] = "preliminary_evaluation"
    result["disclaimer"] = PRELIMINARY_EVALUATION_DISCLAIMER
    return result


def forecast_next_route_rate(
    db: Session,
    route_code: str,
    model_type: Optional[str] = None,
    origin_date: Optional[date] = None,
    horizon_months: int = 1,
    storage_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Generates next-horizon point forecast for a route using observations available on or before origin_date.
    If model_type is omitted, defaults to 'persistence'.
    If origin_date is omitted, defaults to latest observed date in the series.
    Zero-lookahead guarantee: observations after origin_date are strictly excluded from history.
    """
    selected_model = (model_type or DEFAULT_BASELINE_MODEL).lower().strip()
    default_model_applied = model_type is None

    obs, source = get_route_rate_observations(db=db, route_code=route_code, storage_dir=storage_dir)
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
    return d
