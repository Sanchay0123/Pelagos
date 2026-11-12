"""Read-only Platform Data Overview and Detailed Data Explorer query service.

Provides audited, SQL-paginated, non-fabricated views across Pelagos's eight data
domains using active SQLAlchemy database sessions. Never modifies database rows,
never triggers network ingestion, never exposes secrets/credentials, and strictly
enforces Pelagos data integrity rules across UI/API consumers.
"""

from __future__ import annotations

import copy
import datetime
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Query, Session, aliased

from pelagos.config import settings
from pelagos.forecasting.dataset import (
    DEFAULT_BURN_IN_MONTHS,
    FEATURE_CATALOG,
    ROUTE_METADATA,
    SUPPORTED_HORIZONS,
    TemporalAvailabilityPolicy,
    build_forecast_dataset,
    generate_feature_inventory,
)
from pelagos.forecasting.features import EXCLUDED_FEATURE_NAMES, USABLE_FEATURE_NAMES
from pelagos.forecasting.schemas import (
    DEFAULT_BASELINE_MODEL,
    DISALLOWED_DERIVED_ROUTES,
    SUPPORTED_DIRECT_ROUTES,
)
from pelagos.forecasting.service import get_route_rate_observations
from pelagos.models import (
    BunkerPriceObservation,
    EnvironmentalObservation,
    FreightObservation,
    FreightPublication,
    FreightRoute,
    IngestionRun,
    Port,
    PortAlias,
    QuarantinedRecord,
    TradeObservation,
    Vessel,
    VesselPosition,
)
from pelagos.routing.bunker import CORRIDOR_BUNKERING_HUBS
from pelagos.routing.corridors import PORT_REGION_MAP, REGISTERED_CORRIDORS

logger = logging.getLogger(__name__)

_DEFAULT_MARNET_CACHE_DIR = Path(".cache/pelagos/routing")

CACHE_TTL_SECONDS = 60
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25

EXPLORER_DOMAINS: Tuple[str, ...] = (
    "freight",
    "trade",
    "environmental",
    "bunker",
    "vessels",
    "ports",
    "datasets",
    "sources",
)

DOMAIN_LABELS: Dict[str, str] = {
    "freight": "Freight Market",
    "trade": "Trade Data",
    "environmental": "Environmental Observations",
    "bunker": "Bunker Fuel",
    "vessels": "Vessel & AIS",
    "ports": "Ports & Routes",
    "datasets": "Forecasting Datasets",
    "sources": "Source Operations & Provenance",
}

_ARTIFACTS_DIR = Path(__file__).resolve().parents[3] / "docs" / "forecasting" / "artifacts"

_SECRET_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|bearer|authorization)\s*[:=]\s*[^\s,;\"']+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
)

_CACHE_LOCK = threading.Lock()
_OVERVIEW_CACHE: Dict[str, Tuple[float, Tuple[Any, ...], Dict[str, Any]]] = {}
_DATASET_CACHE: Dict[Tuple[str, Tuple[Any, ...], str, int], Tuple[float, Any]] = {}
_DATASET_ITEMS_CACHE: Dict[
    Tuple[str, Tuple[Any, ...], Tuple[str, ...], Tuple[int, ...]],
    Tuple[float, Dict[str, Any]],
] = {}
_BENCHMARK_CACHE: Dict[
    Tuple[str, Optional[int], Optional[int], bool],
    Dict[str, Any],
] = {}


def clear_platform_data_cache() -> None:
    """Clear in-memory overview and dataset caches (primarily for isolated tests)."""
    with _CACHE_LOCK:
        _OVERVIEW_CACHE.clear()
        _DATASET_CACHE.clear()
        _DATASET_ITEMS_CACHE.clear()
        _BENCHMARK_CACHE.clear()


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _to_iso_utc(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        dt = val if val.tzinfo is not None else val.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc).isoformat()
    if isinstance(val, datetime.date):
        return val.isoformat()
    return str(val)


def _sanitize_text(val: Optional[str]) -> Optional[str]:
    if val is None:
        return None
    cleaned = str(val)
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


def _db_bind_key(db: Session) -> str:
    try:
        bind = db.get_bind()
        url = str(getattr(bind, "url", ""))
        return f"{id(bind)}:{url}"
    except Exception:
        return str(id(db))


def _compute_db_fingerprint(db: Session) -> Tuple[Any, ...]:
    """Return lightweight database state signature so cache invalidates on test/DB mutations."""
    return (
        db.query(func.count(FreightObservation.id)).scalar() or 0,
        _to_iso_utc(db.query(func.max(FreightObservation.ingestion_time)).scalar()),
        db.query(func.count(TradeObservation.id)).scalar() or 0,
        db.query(func.count(EnvironmentalObservation.id)).scalar() or 0,
        db.query(func.count(BunkerPriceObservation.id)).scalar() or 0,
        db.query(func.count(Vessel.id)).scalar() or 0,
        db.query(func.count(VesselPosition.id)).scalar() or 0,
        db.query(func.count(Port.id)).scalar() or 0,
        db.query(func.count(PortAlias.id)).scalar() or 0,
        db.query(func.count(IngestionRun.id)).scalar() or 0,
        db.query(func.count(QuarantinedRecord.id)).scalar() or 0,
        db.query(func.count(FreightPublication.id)).scalar() or 0,
    )


def _classify_env_freshness(
    obs_time: Optional[datetime.datetime],
    now_utc: datetime.datetime,
) -> Tuple[str, Optional[float]]:
    if obs_time is None:
        return "unavailable", None
    dt = obs_time if obs_time.tzinfo is not None else obs_time.replace(tzinfo=datetime.timezone.utc)
    age_hours = round(max(0.0, (now_utc - dt.astimezone(datetime.timezone.utc)).total_seconds() / 3600.0), 2)
    if age_hours < 48.0:
        return "current", age_hours
    if age_hours < 168.0:
        return "recent", age_hours
    return "stale", age_hours


def _evaluate_corridor_bunker_coverage(db: Session) -> List[Dict[str, Any]]:
    """Inspect real bunker observation availability for every monitored corridor hub."""
    seen_codes: set[Tuple[str, str]] = set()
    hubs: List[Dict[str, Any]] = []
    grouped = {
        r[0]: {
            "count": r[1],
            "latest_obs": _to_iso_utc(r[2]),
            "grades": set(),
        }
        for r in db.query(
            BunkerPriceObservation.port_code,
            func.count(BunkerPriceObservation.id),
            func.max(BunkerPriceObservation.observation_time),
        )
        .group_by(BunkerPriceObservation.port_code)
        .all()
    }
    grade_rows = (
        db.query(BunkerPriceObservation.port_code, BunkerPriceObservation.fuel_grade)
        .distinct()
        .all()
    )
    for port_code, grade in grade_rows:
        if port_code in grouped and grade:
            grouped[port_code]["grades"].add(grade)

    for corridor_code, hub_list in CORRIDOR_BUNKERING_HUBS.items():
        for hub in hub_list:
            code = hub.port_id or hub.name
            if (corridor_code, code) in seen_codes:
                continue
            seen_codes.add((corridor_code, code))
            info = grouped.get(hub.port_id or "")
            has_quotes = bool(info and info["count"] > 0)
            hubs.append(
                {
                    "corridor_code": corridor_code,
                    "port_code": hub.port_id,
                    "port_name": hub.name,
                    "region": hub.region,
                    "role": hub.role,
                    "status": "available" if has_quotes else "unavailable",
                    "observation_count": info["count"] if info else 0,
                    "grades_covered": sorted(info["grades"]) if info else [],
                    "latest_observation_utc": info["latest_obs"] if info else None,
                    "reason": (
                        "Genuine indicative provider quotes stored in database."
                        if has_quotes
                        else "UNAVAILABLE (no genuine provider quotes ingested for this hub; zero synthetic fallback)."
                    ),
                }
            )
    return hubs


def _summarize_forecast_datasets_fast(db: Session) -> Dict[str, Any]:
    """Compute exact strict-forward dataset readiness from FreightObservation dates via SQL."""
    policy = TemporalAvailabilityPolicy()
    rows = (
        db.query(FreightObservation.route_code, FreightObservation.observation_date)
        .filter(FreightObservation.route_code.in_(SUPPORTED_DIRECT_ROUTES))
        .order_by(FreightObservation.observation_date.asc())
        .all()
    )
    dates_by_route: Dict[str, List[datetime.date]] = {rc: [] for rc in SUPPORTED_DIRECT_ROUTES}
    for rc, obs_date in rows:
        if rc in dates_by_route and obs_date is not None:
            dates_by_route[rc].append(obs_date)

    per_corridor_horizon: List[Dict[str, Any]] = []
    total_rows = 0
    total_labeled = 0
    total_unlabeled = 0
    ready_corridors: List[str] = []

    for rc in SUPPORTED_DIRECT_ROUTES:
        obs_dates = sorted(set(dates_by_route.get(rc, [])))
        obs_set = set(obs_dates)
        if len(obs_dates) <= DEFAULT_BURN_IN_MONTHS:
            for horizon in SUPPORTED_HORIZONS:
                per_corridor_horizon.append(
                    {
                        "route_code": rc,
                        "horizon_months": horizon,
                        "horizon_label": f"T+{horizon}",
                        "status": "unavailable",
                        "total_rows": 0,
                        "labeled_rows": 0,
                        "unlabeled_rows": 0,
                        "origin_start_date": obs_dates[0].isoformat() if obs_dates else None,
                        "origin_end_date": obs_dates[-1].isoformat() if obs_dates else None,
                        "reason": f"Insufficient observations ({len(obs_dates)} <= burn_in {DEFAULT_BURN_IN_MONTHS}).",
                    }
                )
            continue

        ready_corridors.append(rc)
        post_burn_in = obs_dates[DEFAULT_BURN_IN_MONTHS:]
        for horizon in SUPPORTED_HORIZONS:
            labeled = sum(
                1 for d in post_burn_in if policy.calculate_target_date(d, horizon) in obs_set
            )
            unlabeled = len(post_burn_in) - labeled
            total_rows += len(post_burn_in)
            total_labeled += labeled
            total_unlabeled += unlabeled
            per_corridor_horizon.append(
                {
                    "route_code": rc,
                    "horizon_months": horizon,
                    "horizon_label": f"T+{horizon}",
                    "status": "ready" if labeled > 0 else "insufficient",
                    "total_rows": len(post_burn_in),
                    "labeled_rows": labeled,
                    "unlabeled_rows": unlabeled,
                    "origin_start_date": post_burn_in[0].isoformat(),
                    "origin_end_date": post_burn_in[-1].isoformat(),
                    "target_offset_months": horizon + 1,
                }
            )

    overall_status = (
        "populated"
        if total_rows > 0 and len(ready_corridors) == len(SUPPORTED_DIRECT_ROUTES)
        else ("partial" if total_rows > 0 else "unavailable")
    )
    return {
        "status": overall_status,
        "supported_routes": list(SUPPORTED_DIRECT_ROUTES),
        "ready_routes": ready_corridors,
        "disallowed_routes": list(DISALLOWED_DERIVED_ROUTES),
        "supported_horizons": list(SUPPORTED_HORIZONS),
        "horizon_labels": [f"T+{h}" for h in SUPPORTED_HORIZONS],
        "horizon_alignment": policy.horizon_alignment,
        "burn_in_months": DEFAULT_BURN_IN_MONTHS,
        "usable_features_count": len(USABLE_FEATURE_NAMES),
        "excluded_features_count": len(EXCLUDED_FEATURE_NAMES),
        "total_dataset_rows": total_rows,
        "total_labeled_rows": total_labeled,
        "total_unlabeled_inference_rows": total_unlabeled,
        "production_baseline": DEFAULT_BASELINE_MODEL,
        "production_baseline_label": "Persistence (Active Production Baseline — Unchanged)",
        "policy_label": (
            "Pelagos Conservative Operational Assumption: 14-calendar-day post-month-end cutoff "
            "(provisional 12:00 PM America/New_York; observed_publication_time = None; "
            "23:59:59 UTC decision ordering)"
        ),
        "per_corridor_horizon": per_corridor_horizon,
    }


def _extract_model_comparisons(
    *,
    common_models: Dict[str, Any],
    stats_map: Dict[str, Any],
    evaluated_map: Dict[str, Any],
    config_map: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Assemble ordered per-model metric and statistical rows preserving source precision and nulls."""
    ordered_names: List[str] = []
    seen = set()
    for source in (common_models, evaluated_map, stats_map):
        for name in source.keys():
            if name not in seen:
                seen.add(name)
                ordered_names.append(name)

    rows: List[Dict[str, Any]] = []
    unavailable: List[Dict[str, Any]] = []
    for name in ordered_names:
        cm = common_models.get(name) if isinstance(common_models.get(name), dict) else {}
        ev = evaluated_map.get(name) if isinstance(evaluated_map.get(name), dict) else {}
        st = stats_map.get(name) if isinstance(stats_map.get(name), dict) else {}
        cfg = config_map.get(name) if isinstance(config_map.get(name), dict) else {}
        ev_summary = ev.get("summary") if isinstance(ev.get("summary"), dict) else {}

        is_available = ev.get("is_available")
        if is_available is None:
            is_available = st.get("is_available", True) if st else True
        is_available = bool(is_available)
        unavailability_reason = ev.get("unavailability_reason") or st.get("reason")
        display_name = ev.get("display_name") or cfg.get("display_name") or name
        model_family = ev.get("model_family") or cfg.get("model_family")

        row: Dict[str, Any] = {
            "model_name": name,
            "display_name": display_name,
            "model_family": model_family,
            "feature_subset_name": ev.get("feature_subset_name") or cfg.get("feature_subset_name"),
            "feature_count": ev.get("feature_count"),
            "uses_origin_regime": cfg.get("uses_origin_regime"),
            "is_baseline": name == DEFAULT_BASELINE_MODEL,
            "is_available": is_available,
            "unavailability_reason": unavailability_reason,
            "sample_size": cm.get("sample_size"),
            "valid_steps_count": ev_summary.get("valid_steps_count"),
            "skipped_steps_count": ev_summary.get("skipped_steps_count"),
            "mae": cm.get("mae"),
            "rmse": cm.get("rmse"),
            "mape": cm.get("mape"),
            "mase": cm.get("mase"),
            "directional_accuracy_non_flat": cm.get("directional_accuracy_non_flat"),
            "relative_mae_ratio": cm.get("relative_mae_ratio"),
            "relative_mae_percent_change": cm.get("relative_mae_percent_change"),
            "relative_rmse_ratio": cm.get("relative_rmse_ratio"),
            "dm_statistic": st.get("dm_statistic"),
            "hln_statistic": st.get("hln_statistic"),
            "p_value": st.get("p_value"),
            "statistically_significant": st.get("statistically_significant"),
            "holm_adjusted_p_value": st.get("holm_adjusted_p_value"),
            "holm_statistically_significant": st.get("holm_statistically_significant"),
            "favors_candidate": st.get("favors_candidate"),
            "favors_baseline": st.get("favors_baseline"),
            "bootstrap_ci_lower": st.get("bootstrap_ci_lower"),
            "bootstrap_ci_upper": st.get("bootstrap_ci_upper"),
            "ci_contains_zero": st.get("ci_contains_zero"),
            "block_length": st.get("block_length"),
            "bootstrap_iterations": st.get("bootstrap_iterations"),
            "degrees_of_freedom": st.get("degrees_of_freedom"),
            "research_classification": st.get("research_classification"),
            "status": st.get("status"),
        }
        rows.append(row)
        if not is_available:
            unavailable.append(
                {
                    "model_name": name,
                    "display_name": display_name,
                    "model_family": model_family,
                    "unavailability_reason": unavailability_reason,
                    "status": st.get("status") or "UNAVAILABLE",
                }
            )
    return rows, unavailable


def _extract_regime_breakdown(
    breakdown: Dict[str, Any],
    *,
    config_map: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Extract ordered per-regime metrics and statistical comparisons without rounding or zero-filling."""
    regimes: List[Dict[str, Any]] = []
    for reg_key, reg_val in (breakdown or {}).items():
        if not isinstance(reg_val, dict):
            continue
        reg_models = reg_val.get("models") if isinstance(reg_val.get("models"), dict) else {}
        reg_stats = (
            reg_val.get("statistical_comparisons")
            if isinstance(reg_val.get("statistical_comparisons"), dict)
            else {}
        )
        sample_size = None
        for mvals in reg_models.values():
            if isinstance(mvals, dict) and mvals.get("valid_steps_count") is not None:
                sample_size = mvals.get("valid_steps_count")
                break
        model_rows, _ = _extract_model_comparisons(
            common_models=reg_models,
            stats_map=reg_stats,
            evaluated_map={},
            config_map=config_map,
        )
        for row in model_rows:
            raw_m = reg_models.get(row["model_name"])
            if isinstance(raw_m, dict):
                row["sample_size"] = raw_m.get("valid_steps_count")
                row["valid_steps_count"] = raw_m.get("valid_steps_count")
                row["skipped_steps_count"] = raw_m.get("skipped_steps_count")
                row["is_sample_sufficient"] = raw_m.get("is_sample_sufficient")
                row["sample_warning"] = raw_m.get("sample_warning")
        regimes.append(
            {
                "regime_id": reg_val.get("regime_id") or reg_key,
                "display_name": reg_val.get("display_name") or reg_key,
                "start_date": reg_val.get("start_date"),
                "end_date": reg_val.get("end_date"),
                "sample_size": sample_size,
                "models": model_rows,
            }
        )
    return regimes


def _build_highlight_entry(key: str, exp: Dict[str, Any]) -> Dict[str, Any]:
    """Build backward-compatible abbreviated highlight row for overview and drawer."""
    common_comp = exp.get("common_sample_comparison") or {}
    common = common_comp.get("models") or {}
    pers = common.get("persistence") or {}
    best_cand_name = None
    best_cand_mae = None
    for mname, mvals in common.items():
        if mname == "persistence":
            continue
        mae = mvals.get("mae")
        if mae is not None and (best_cand_mae is None or mae < best_cand_mae):
            best_cand_mae = mae
            best_cand_name = mname
    return {
        "experiment_key": key,
        "route_code": exp.get("route_code"),
        "horizon_months": exp.get("horizon_months"),
        "common_sample_size": common_comp.get("common_sample_size"),
        "persistence_mae": pers.get("mae"),
        "best_experimental_candidate": best_cand_name,
        "best_experimental_mae": best_cand_mae,
        "classification": "experimental_not_promoted",
    }


def _load_benchmark_artifacts_summary(include_details: bool = False) -> Dict[str, Any]:
    """Load saved C3.2 & C3.3 research benchmark artifacts cleanly if present.

    When ``include_details=False`` (used by ``/overview``), returns only the lightweight
    abbreviated highlights. When ``include_details=True`` (used by ``/explorer/datasets``),
    includes complete source-truth per-model metrics, DM/HLN/Holm statistics, bootstrap CIs,
    regime breakdowns, and LightGBM unavailability provenance.
    """
    c32_path = _ARTIFACTS_DIR / "c32_ml_benchmark_results.json"
    c33_path = _ARTIFACTS_DIR / "c33_regime_investigation_results.json"
    c32_mtime = c32_path.stat().st_mtime_ns if c32_path.exists() else None
    c33_mtime = c33_path.stat().st_mtime_ns if c33_path.exists() else None
    cache_key = (str(_ARTIFACTS_DIR), c32_mtime, c33_mtime, include_details)

    with _CACHE_LOCK:
        cached = _BENCHMARK_CACHE.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)

    artifacts: Dict[str, Any] = {
        "production_baseline": DEFAULT_BASELINE_MODEL,
        "production_note": (
            "Persistence remains the active production forecasting baseline. "
            "Experimental ML and regime-aware candidates were NOT promoted to production."
        ),
        "c32_available": False,
        "c33_available": False,
        "c32_highlights": [],
        "c33_highlights": [],
    }
    if include_details:
        artifacts["c32_metadata"] = None
        artifacts["c32_experiments"] = []
        artifacts["c33_metadata"] = None
        artifacts["c33_experiments"] = []

    if c32_path.exists():
        try:
            data = json.loads(c32_path.read_text(encoding="utf-8"))
            artifacts["c32_available"] = True
            meta = data.get("metadata") or {}
            model_configs = meta.get("model_configs") or {}
            if include_details:
                artifacts["c32_metadata"] = {
                    "benchmark_type": meta.get("benchmark_type"),
                    "evaluated_routes": meta.get("evaluated_routes"),
                    "evaluated_horizons": meta.get("evaluated_horizons"),
                    "min_training_samples": meta.get("min_training_samples"),
                    "random_seed": meta.get("random_seed"),
                    "lightgbm_available": meta.get("lightgbm_available"),
                    "artifact_path": "docs/forecasting/artifacts/c32_ml_benchmark_results.json",
                }
            for key, exp in (data.get("experiments") or {}).items():
                artifacts["c32_highlights"].append(_build_highlight_entry(key, exp))
                if include_details:
                    common_comp = exp.get("common_sample_comparison") or {}
                    models, unavailable_models = _extract_model_comparisons(
                        common_models=common_comp.get("models") or {},
                        stats_map=exp.get("statistical_comparisons") or {},
                        evaluated_map=exp.get("models_evaluated") or {},
                        config_map=model_configs,
                    )
                    artifacts["c32_experiments"].append(
                        {
                            "experiment_key": key,
                            "route_code": exp.get("route_code"),
                            "horizon_months": exp.get("horizon_months"),
                            "total_labeled_rows": exp.get("total_labeled_rows"),
                            "min_training_samples": exp.get("min_training_samples"),
                            "common_sample_size": common_comp.get("common_sample_size"),
                            "common_start_date": common_comp.get("common_start_date"),
                            "common_end_date": common_comp.get("common_end_date"),
                            "models": models,
                            "unavailable_models": unavailable_models,
                            "regime_breakdown": _extract_regime_breakdown(
                                exp.get("regime_breakdown") or {},
                                config_map=model_configs,
                            ),
                        }
                    )
        except Exception as exc:
            logger.warning("Failed to parse C3.2 benchmark artifact: %s", exc)

    if c33_path.exists():
        try:
            data = json.loads(c33_path.read_text(encoding="utf-8"))
            artifacts["c33_available"] = True
            meta = data.get("metadata") or {}
            exp_configs = meta.get("experiment_configs") or {}
            if include_details:
                artifacts["c33_metadata"] = {
                    "investigation_stage": meta.get("investigation_stage"),
                    "evaluated_routes": meta.get("evaluated_routes"),
                    "evaluated_horizons": meta.get("evaluated_horizons"),
                    "min_training_samples": meta.get("min_training_samples"),
                    "min_regime_selection_samples": meta.get("min_regime_selection_samples"),
                    "random_seed": meta.get("random_seed"),
                    "production_baseline": meta.get("production_baseline"),
                    "artifact_path": "docs/forecasting/artifacts/c33_regime_investigation_results.json",
                }
            for key, exp in (data.get("experiments") or {}).items():
                artifacts["c33_highlights"].append(_build_highlight_entry(key, exp))
                if include_details:
                    common_comp = exp.get("common_sample_comparison") or {}
                    models, _ = _extract_model_comparisons(
                        common_models=common_comp.get("models") or {},
                        stats_map=exp.get("statistical_comparisons") or {},
                        evaluated_map=exp.get("experiments_evaluated") or {},
                        config_map=exp_configs,
                    )
                    rpa = exp.get("regime_partitioned_analysis")
                    rpa_out: Optional[Dict[str, Any]] = None
                    if isinstance(rpa, dict):
                        matched_comp = rpa.get("matched_subset_comparison") or {}
                        matched_exp = (rpa.get("experiment") or {}) if isinstance(rpa.get("experiment"), dict) else {}
                        matched_models, _ = _extract_model_comparisons(
                            common_models=matched_comp.get("models") or {},
                            stats_map=rpa.get("matched_subset_statistical_comparisons") or {},
                            evaluated_map={"regime_partitioned_ridge_ar": matched_exp} if matched_exp else {},
                            config_map=exp_configs,
                        )
                        rpa_summary = matched_exp.get("summary") if isinstance(matched_exp.get("summary"), dict) else {}
                        rpa_out = {
                            "matched_subset_sample_size": rpa.get("matched_subset_sample_size"),
                            "common_start_date": matched_comp.get("common_start_date"),
                            "common_end_date": matched_comp.get("common_end_date"),
                            "skipped_steps_count": rpa_summary.get("skipped_steps_count"),
                            "skipped_reasons": rpa_summary.get("skipped_reasons"),
                            "models": matched_models,
                        }
                    artifacts["c33_experiments"].append(
                        {
                            "experiment_key": key,
                            "route_code": exp.get("route_code"),
                            "horizon_months": exp.get("horizon_months"),
                            "total_labeled_rows": exp.get("total_labeled_rows"),
                            "min_training_samples": exp.get("min_training_samples"),
                            "min_regime_selection_samples": exp.get("min_regime_selection_samples"),
                            "common_sample_size": common_comp.get("common_sample_size"),
                            "common_start_date": common_comp.get("common_start_date"),
                            "common_end_date": common_comp.get("common_end_date"),
                            "regime_sample_audit": exp.get("regime_sample_audit"),
                            "models": models,
                            "regime_breakdown": _extract_regime_breakdown(
                                exp.get("regime_breakdown") or {},
                                config_map=exp_configs,
                            ),
                            "regime_partitioned_analysis": rpa_out,
                        }
                    )
        except Exception as exc:
            logger.warning("Failed to parse C3.3 regime artifact: %s", exc)

    with _CACHE_LOCK:
        _BENCHMARK_CACHE[cache_key] = copy.deepcopy(artifacts)
    return artifacts


def _get_routing_topology_summary() -> Dict[str, Any]:
    """Read MARNET and corridor topology metadata cleanly without triggering network downloads."""
    cache_dir = _DEFAULT_MARNET_CACHE_DIR
    manifest_path = cache_dir / "marnet_manifest.json"
    edges_path = cache_dir / "marnet_edges.json"
    marnet_nodes_count = None
    marnet_edges_count = None
    marnet_cache_present = edges_path.exists()
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            marnet_nodes_count = manifest.get("node_count") or manifest.get("nodes_count")
            marnet_edges_count = manifest.get("edge_count") or manifest.get("edges_count")
        except Exception:
            pass
    return {
        "default_routing_mode": settings.routing_mode,
        "supported_modes": ["prototype", "marnet"],
        "marnet_cache_present": marnet_cache_present,
        "marnet_nodes_count": marnet_nodes_count,
        "marnet_edges_count": marnet_edges_count,
        "topology_qualification": "generalized_50km_non_certified_navigation",
        "topology_warning": (
            "Generalized Eurostat SeaRoute 50 km shipping-lane graph (~4,000 nodes / ~10,000 edges upstream). "
            "NOT certified navigation intelligence, ECDIS passage planning, or shallow-water clearance."
        ),
        "monitored_corridor_definitions": [
            {
                "route_code": v["route_code"],
                "route_name": v["route_name"],
                "origin_region": v["origin_region"],
                "destination_region": v["destination_region"],
                "benchmark_distance_nm": v["benchmark_distance_nm"],
                "vessel_class": v["vessel_class"],
            }
            for v in REGISTERED_CORRIDORS.values()
        ],
    }


def _resolve_port_corridor_tag(port: Port) -> str:
    """Resolve port region/ELIGIBILITY to monitored corridor codes (without claiming rate monitoring everywhere)."""
    if not port.is_monitored_corridor_eligible:
        return "Unmonitored"
    region = PORT_REGION_MAP.get(port.unlocode) or PORT_REGION_MAP.get(port.port_id) or port.port_region
    if region == "US Pacific Northwest":
        return "PNW-JPN-BULK (Origin)"
    if region == "US Gulf Coast":
        return "USG-JPN-BULK (Origin)"
    if region == "Japan":
        return "PNW-JPN-BULK / USG-JPN-BULK (Destination)"
    return "Corridor Eligible"


def _corridor_summary_rows(db: Session) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Query freight routes and observation aggregates cleanly via SQL."""
    routes = {r.route_code: r for r in db.query(FreightRoute).all()}
    agg_rows = (
        db.query(
            FreightObservation.route_code,
            func.count(FreightObservation.id).label("cnt"),
            func.min(FreightObservation.observation_date).label("min_d"),
            func.max(FreightObservation.observation_date).label("max_d"),
            func.sum(case((FreightObservation.is_derived.is_(True), 1), else_=0)).label("derived_cnt"),
            func.sum(case((FreightObservation.is_reconstructed.is_(True), 1), else_=0)).label("recon_cnt"),
        )
        .group_by(FreightObservation.route_code)
        .order_by(FreightObservation.route_code.asc())
        .all()
    )

    latest_rate_by_route: Dict[str, FreightObservation] = {}
    for agg in agg_rows:
        if agg.max_d is not None:
            obs = (
                db.query(FreightObservation)
                .filter(
                    FreightObservation.route_code == agg.route_code,
                    FreightObservation.observation_date == agg.max_d,
                )
                .first()
            )
            if obs is not None:
                latest_rate_by_route[agg.route_code] = obs

    underlying: List[Dict[str, Any]] = []
    derived: List[Dict[str, Any]] = []

    seen_codes = {a.route_code for a in agg_rows} | set(routes.keys())
    agg_map = {a.route_code: a for a in agg_rows}

    for code in sorted(seen_codes):
        meta = routes.get(code)
        agg = agg_map.get(code)
        latest = latest_rate_by_route.get(code)
        cnt = int(agg.cnt) if agg else 0
        derived_cnt = int(agg.derived_cnt or 0) if agg else (cnt if code in DISALLOWED_DERIVED_ROUTES else 0)
        recon_cnt = int(agg.recon_cnt or 0) if agg else 0
        is_derived_series = (code in DISALLOWED_DERIVED_ROUTES) or (derived_cnt > 0 and derived_cnt == cnt)

        entry = {
            "route_code": code,
            "route_name": meta.route_name if meta else code,
            "origin_region": meta.origin_region if meta else None,
            "destination_region": meta.destination_region if meta else None,
            "vessel_class": meta.vessel_class if meta else None,
            "cargo_type": meta.cargo_type if meta else None,
            "benchmark_distance_nm": meta.benchmark_distance_nm if meta else None,
            "is_derived_series": is_derived_series,
            "observation_count": cnt,
            "reconstructed_count": recon_cnt,
            "earliest_observation": _to_iso_utc(agg.min_d) if agg else None,
            "latest_observation": _to_iso_utc(agg.max_d) if agg else None,
            "latest_rate_value": latest.rate_value if latest else None,
            "currency": latest.currency if latest else (meta.currency if meta else "USD"),
            "unit": latest.unit_of_measure if latest else (meta.unit_of_measure if meta else "USD/MT"),
            "source_name": latest.source_name if latest else None,
            "forecast_supported": not is_derived_series and cnt > 0,
            "available_horizons": [f"T+{h}" for h in SUPPORTED_HORIZONS] if not is_derived_series else [],
        }
        if is_derived_series:
            derived.append(entry)
        else:
            underlying.append(entry)

    return underlying, derived


def _build_platform_overview_uncached(db: Session) -> Dict[str, Any]:
    now_utc = _utc_now()

    # 1. Freight Market
    underlying_corridors, derived_series = _corridor_summary_rows(db)
    freight_underlying_count = sum(c["observation_count"] for c in underlying_corridors)
    freight_derived_count = sum(c["observation_count"] for c in derived_series)
    freight_total_count = freight_underlying_count + freight_derived_count
    freight_recon_count = sum(c["reconstructed_count"] for c in underlying_corridors + derived_series)
    min_freight_d = db.query(func.min(FreightObservation.observation_date)).scalar()
    max_freight_d = db.query(func.max(FreightObservation.observation_date)).scalar()
    freight_pub_count = db.query(func.count(FreightPublication.id)).scalar() or 0
    max_freight_ing = db.query(func.max(FreightObservation.ingestion_time)).scalar()

    # 2. Trade Data
    trade_count, min_trade_d, max_trade_d, trade_commodities_cnt = (
        db.query(
            func.count(TradeObservation.id),
            func.min(TradeObservation.period_date),
            func.max(TradeObservation.period_date),
            func.count(func.distinct(TradeObservation.commodity_code)),
        ).one()
    )
    trade_count = int(trade_count or 0)
    trade_commodities_cnt = int(trade_commodities_cnt or 0)
    trade_flows_raw = (
        db.query(
            TradeObservation.reporter_iso2,
            TradeObservation.partner_iso2,
            TradeObservation.source_name,
            func.count(TradeObservation.id),
        )
        .group_by(
            TradeObservation.reporter_iso2,
            TradeObservation.partner_iso2,
            TradeObservation.source_name,
        )
        .all()
    )

    # 3. Environmental Observations
    env_count, min_env_t, max_env_t = (
        db.query(
            func.count(EnvironmentalObservation.id),
            func.min(EnvironmentalObservation.observation_time),
            func.max(EnvironmentalObservation.observation_time),
        ).one()
    )
    env_count = int(env_count or 0)
    env_freshness, env_age_hours = _classify_env_freshness(max_env_t, now_utc)
    env_stations_raw = (
        db.query(
            EnvironmentalObservation.station_id,
            EnvironmentalObservation.station_name,
            EnvironmentalObservation.latitude,
            EnvironmentalObservation.longitude,
            EnvironmentalObservation.sea_area,
            EnvironmentalObservation.source_name,
            func.count(EnvironmentalObservation.id),
            func.min(EnvironmentalObservation.observation_time),
            func.max(EnvironmentalObservation.observation_time),
        )
        .group_by(
            EnvironmentalObservation.station_id,
            EnvironmentalObservation.station_name,
            EnvironmentalObservation.latitude,
            EnvironmentalObservation.longitude,
            EnvironmentalObservation.sea_area,
            EnvironmentalObservation.source_name,
        )
        .order_by(func.max(EnvironmentalObservation.observation_time).desc())
        .all()
    )
    env_stations = [
        {
            "station_id": s[0],
            "station_name": s[1],
            "latitude": s[2],
            "longitude": s[3],
            "sea_area": s[4],
            "source_name": s[5],
            "observation_count": int(s[6]),
            "earliest_observation_utc": _to_iso_utc(s[7]),
            "latest_observation_utc": _to_iso_utc(s[8]),
        }
        for s in env_stations_raw
    ]

    # 4. Bunker Fuel
    bunker_count, min_bunker_t, max_bunker_t, max_bunker_ret = (
        db.query(
            func.count(BunkerPriceObservation.id),
            func.min(BunkerPriceObservation.observation_time),
            func.max(BunkerPriceObservation.observation_time),
            func.max(BunkerPriceObservation.retrieval_time_utc),
        ).one()
    )
    bunker_count = int(bunker_count or 0)
    bunker_ports_raw = (
        db.query(
            BunkerPriceObservation.port_code,
            BunkerPriceObservation.port_name,
            BunkerPriceObservation.country_code,
            func.count(BunkerPriceObservation.id),
            func.max(BunkerPriceObservation.observation_time),
        )
        .group_by(
            BunkerPriceObservation.port_code,
            BunkerPriceObservation.port_name,
            BunkerPriceObservation.country_code,
        )
        .all()
    )
    bunker_grades = sorted(
        r[0] for r in db.query(func.distinct(BunkerPriceObservation.fuel_grade)).all() if r[0]
    )
    bunker_corridor_hubs = _evaluate_corridor_bunker_coverage(db)
    corridor_hubs_with_prices = sum(1 for h in bunker_corridor_hubs if h["status"] == "available")

    # 5. Vessel & AIS
    vessel_agg = db.query(
        func.count(Vessel.id),
        func.count(Vessel.imo),
        func.count(Vessel.vessel_class),
        func.count(Vessel.dwt_tonnage),
        func.count(Vessel.gross_tonnage),
        func.count(Vessel.length_meters),
        func.count(Vessel.width_meters),
        func.count(Vessel.draft_max_meters),
        func.count(Vessel.built_year),
    ).one()
    canonical_vessels = int(vessel_agg[0] or 0)
    particulars_completeness = {
        "total_vessels": canonical_vessels,
        "imo_present": int(vessel_agg[1] or 0),
        "vessel_class_present": int(vessel_agg[2] or 0),
        "dwt_tonnage_present": int(vessel_agg[3] or 0),
        "gross_tonnage_present": int(vessel_agg[4] or 0),
        "length_meters_present": int(vessel_agg[5] or 0),
        "width_meters_present": int(vessel_agg[6] or 0),
        "draft_max_meters_present": int(vessel_agg[7] or 0),
        "built_year_present": int(vessel_agg[8] or 0),
    }
    vessel_positions, distinct_pos_mmsi, min_pos_t, max_pos_t, max_pos_ing = (
        db.query(
            func.count(VesselPosition.id),
            func.count(func.distinct(VesselPosition.mmsi)),
            func.min(VesselPosition.observation_time),
            func.max(VesselPosition.observation_time),
            func.max(VesselPosition.ingestion_time),
        ).one()
    )
    vessel_positions = int(vessel_positions or 0)
    distinct_pos_mmsi = int(distinct_pos_mmsi or 0)
    vessel_type_counts = [
        {"vessel_type": r[0], "count": int(r[1])}
        for r in db.query(Vessel.vessel_type, func.count(Vessel.id))
        .group_by(Vessel.vessel_type)
        .order_by(func.count(Vessel.id).desc())
        .all()
    ]

    # 6. Ports & Routes
    total_ports, corridor_eligible_ports, graph_snapped_ports = (
        db.query(
            func.count(Port.id),
            func.sum(case((Port.is_monitored_corridor_eligible.is_(True), 1), else_=0)),
            func.count(Port.nearest_graph_node),
        ).one()
    )
    total_ports = int(total_ports or 0)
    corridor_eligible_ports = int(corridor_eligible_ports or 0)
    graph_snapped_ports = int(graph_snapped_ports or 0)
    total_aliases = db.query(func.count(PortAlias.id)).scalar() or 0
    ports_by_country = [
        {"country_code": r[0], "country": r[1], "count": int(r[2])}
        for r in db.query(Port.country_code, Port.country, func.count(Port.id))
        .group_by(Port.country_code, Port.country)
        .order_by(func.count(Port.id).desc())
        .all()
    ]
    routing_topology = _get_routing_topology_summary()

    # 7. Forecasting Datasets
    datasets_summary = _summarize_forecast_datasets_fast(db)
    benchmarks_summary = _load_benchmark_artifacts_summary()

    # 8. Source Operations & Provenance
    total_runs, completed_runs, partial_runs, failed_runs = (
        db.query(
            func.count(IngestionRun.id),
            func.sum(case((IngestionRun.status == "completed", 1), else_=0)),
            func.sum(case((IngestionRun.status == "partial", 1), else_=0)),
            func.sum(case((IngestionRun.status == "failed", 1), else_=0)),
        ).one()
    )
    total_runs = int(total_runs or 0)
    completed_runs = int(completed_runs or 0)
    partial_runs = int(partial_runs or 0)
    failed_runs = int(failed_runs or 0)
    quarantined_count = db.query(func.count(QuarantinedRecord.id)).scalar() or 0

    latest_success_run = (
        db.query(IngestionRun)
        .filter(IngestionRun.status.in_(["completed", "partial"]))
        .order_by(IngestionRun.completed_at.desc(), IngestionRun.id.desc())
        .first()
    )

    latest_run_summary = (
        {
            "run_id": latest_success_run.id,
            "run_type": latest_success_run.run_type,
            "source_name": latest_success_run.source_name,
            "source_partition": latest_success_run.source_partition,
            "status": latest_success_run.status,
            "records_read": latest_success_run.records_read,
            "accepted_count": latest_success_run.accepted_count,
            "duplicate_count": latest_success_run.duplicate_count,
            "quarantined_count": latest_success_run.quarantined_count,
            "completed_at": _to_iso_utc(latest_success_run.completed_at),
        }
        if latest_success_run
        else None
    )

    # Retrieval timestamps across tables
    retrieval_candidates = [
        ("ingestion_runs", latest_success_run.completed_at if latest_success_run else None),
        ("bunker_price_observations", max_bunker_ret),
        ("freight_observations", max_freight_ing),
        ("vessel_positions", max_pos_ing),
    ]
    valid_retrievals = [
        (src, ts if ts.tzinfo is not None else ts.replace(tzinfo=datetime.timezone.utc))
        for src, ts in retrieval_candidates
        if ts is not None
    ]
    latest_retrieval_entry = max(valid_retrievals, key=lambda x: x[1]) if valid_retrievals else None

    source_health = [
        {
            "source_id": "usda_agtransport",
            "source_name": "USDA AgTransport (SODA ehs5-yac3)",
            "domain": "freight",
            "status": "operational" if freight_total_count > 0 else "empty",
            "acquisition_mode": "remote_api_monthly",
            "records_in_db": freight_total_count,
            "latest_observation": _to_iso_utc(max_freight_d),
            "latest_sync_utc": _to_iso_utc(max_freight_ing),
            "notes": "370 monthly points per series (1996-01 to 2026-10); 10 USG-JPN observations reconstructed.",
        },
        {
            "source_id": "noaa_ndbc_incois",
            "source_name": "NOAA NDBC / INCOIS Deepwater Buoys",
            "domain": "environmental",
            "status": env_freshness if env_count > 0 else "empty",
            "acquisition_mode": "remote_realtime_snapshot",
            "records_in_db": env_count,
            "latest_observation": _to_iso_utc(max_env_t),
            "latest_sync_utc": None,
            "notes": "Station buoy snapshots (46041, 42001, AD01); missing sensors preserved as NULL.",
        },
        {
            "source_id": "bunker_fuel_prices",
            "source_name": "Bunker Fuel Prices (bunkerfuelprices.com)",
            "domain": "bunker",
            "status": "partial" if bunker_count > 0 else "empty",
            "acquisition_mode": "spot_indicative_feed",
            "records_in_db": bunker_count,
            "latest_observation": _to_iso_utc(max_bunker_t),
            "latest_sync_utc": _to_iso_utc(max_bunker_ret),
            "notes": "Indicative spot prices for ARBUE & DKSKA only; US/JP corridor hubs unavailable.",
        },
        {
            "source_id": "usda_fas_dgcis",
            "source_name": "USDA FAS GATS / Indian DGCI&S",
            "domain": "trade",
            "status": "historical_sample" if trade_count > 0 else "empty",
            "acquisition_mode": "bundled_fixture_requires_credentials",
            "records_in_db": trade_count,
            "latest_observation": _to_iso_utc(max_trade_d),
            "latest_sync_utc": None,
            "notes": "Official trade declarations (Oct-Dec 2024); commodity customs unit values are NOT freight rates.",
        },
        {
            "source_id": "marinecadastre_nais",
            "source_name": "NOAA MarineCadastre / USCG NAIS",
            "domain": "vessels",
            "status": "historical_sample" if (canonical_vessels > 0 or vessel_positions > 0) else "empty",
            "acquisition_mode": "historical_sample_offline_ais",
            "records_in_db": vessel_positions,
            "latest_observation": _to_iso_utc(max_pos_t),
            "latest_sync_utc": _to_iso_utc(max_pos_ing),
            "notes": "Live AIS stream disconnected; historical US coastal telemetry does NOT indicate commercial charter availability.",
        },
        {
            "source_id": "unece_wpi_ports",
            "source_name": "UNECE UN/LOCODE / NGA WPI / Indian Ports",
            "domain": "ports",
            "status": "operational" if total_ports > 0 else "empty",
            "acquisition_mode": "canonical_directory_with_marnet",
            "records_in_db": total_ports,
            "latest_observation": None,
            "latest_sync_utc": None,
            "notes": f"{total_ports} canonical ports with {total_aliases} aliases; generalized MARNET 50km topology is non-certified.",
        },
    ]

    populated_source_count = sum(1 for s in source_health if s["records_in_db"] > 0)
    operational_remote_count = sum(
        1
        for s in source_health
        if s["records_in_db"] > 0 and s["acquisition_mode"].startswith("remote_")
    )

    total_raw_observations = freight_total_count + trade_count + env_count + bunker_count + vessel_positions
    total_physical_entities = total_ports + canonical_vessels
    total_genuine_records = total_raw_observations + total_physical_entities

    # Domain status evaluation
    domain_statuses = {
        "freight": "populated" if freight_total_count > 0 else "empty",
        "trade": "populated" if trade_count > 0 else "empty",
        "environmental": ("populated" if env_count > 0 else "empty"),
        "bunker": ("partial" if bunker_count > 0 and corridor_hubs_with_prices == 0 else ("populated" if bunker_count > 0 else "empty")),
        "vessels": "populated" if (canonical_vessels > 0 or vessel_positions > 0) else "empty",
        "ports": "populated" if total_ports > 0 else "empty",
        "datasets": datasets_summary["status"],
        "sources": "populated" if total_runs > 0 else "empty",
    }
    populated_domains_count = sum(1 for st in domain_statuses.values() if st in ("populated", "partial", "ready"))
    domains_fully_populated_count = sum(1 for st in domain_statuses.values() if st in ("populated", "ready"))
    domains_partial_count = sum(1 for st in domain_statuses.values() if st == "partial")
    domains_empty_or_unavailable_count = sum(
        1 for st in domain_statuses.values() if st in ("empty", "unavailable")
    )

    domains = {
        "freight": {
            "domain": "freight",
            "label": DOMAIN_LABELS["freight"],
            "status": domain_statuses["freight"],
            "total_observations": freight_total_count,
            "underlying_corridor_observations": freight_underlying_count,
            "derived_spread_observations": freight_derived_count,
            "reconstructed_observations": freight_recon_count,
            "monitored_corridors": underlying_corridors,
            "derived_series": derived_series,
            "freight_publications_count": freight_pub_count,
            "historical_range": {
                "start": _to_iso_utc(min_freight_d),
                "end": _to_iso_utc(max_freight_d),
            },
            "available_forecast_horizons": [f"T+{h}" for h in SUPPORTED_HORIZONS] if underlying_corridors else [],
            "active_production_baseline": DEFAULT_BASELINE_MODEL,
        },
        "trade": {
            "domain": "trade",
            "label": DOMAIN_LABELS["trade"],
            "status": domain_statuses["trade"],
            "total_observations": trade_count,
            "commodities_count": trade_commodities_cnt,
            "flows": [
                {"reporter_iso2": r[0], "partner_iso2": r[1], "source_name": r[2], "count": int(r[3])}
                for r in trade_flows_raw
            ],
            "date_range": {
                "start": _to_iso_utc(min_trade_d),
                "end": _to_iso_utc(max_trade_d),
            },
            "freshness": "historical" if trade_count > 0 else "empty",
            "semantic_notice": "Trade unit values represent commodity customs value (USD/MT) and must NOT be treated as ocean freight rates.",
        },
        "environmental": {
            "domain": "environmental",
            "label": DOMAIN_LABELS["environmental"],
            "status": domain_statuses["environmental"],
            "total_observations": env_count,
            "stations_count": len(env_stations),
            "stations": env_stations,
            "date_range": {
                "start": _to_iso_utc(min_env_t),
                "end": _to_iso_utc(max_env_t),
            },
            "latest_observation_utc": _to_iso_utc(max_env_t),
            "age_hours": env_age_hours,
            "freshness": env_freshness,
            "coverage_note": "Station buoy measurements only; missing readings remain NULL (no interpolation); mid-ocean unmonitored.",
        },
        "bunker": {
            "domain": "bunker",
            "label": DOMAIN_LABELS["bunker"],
            "status": domain_statuses["bunker"],
            "total_observations": bunker_count,
            "ports_covered": [
                {
                    "port_code": r[0],
                    "port_name": r[1],
                    "country_code": r[2],
                    "observation_count": int(r[3]),
                    "latest_observation_utc": _to_iso_utc(r[4]),
                }
                for r in bunker_ports_raw
            ],
            "grades_covered": bunker_grades,
            "corridor_hubs_covered_count": corridor_hubs_with_prices,
            "corridor_hub_coverage": bunker_corridor_hubs,
            "date_range": {
                "start": _to_iso_utc(min_bunker_t),
                "end": _to_iso_utc(max_bunker_t),
            },
            "latest_retrieval_utc": _to_iso_utc(max_bunker_ret),
            "pricing_qualification": "indicative_only",
            "quarantined_records_in_db": quarantined_count,
        },
        "vessels": {
            "domain": "vessels",
            "label": DOMAIN_LABELS["vessels"],
            "status": domain_statuses["vessels"],
            "canonical_vessels": canonical_vessels,
            "position_observations": vessel_positions,
            "distinct_mmsi_positions": distinct_pos_mmsi,
            "telemetry_mode": "historical" if vessel_positions > 0 else "unavailable",
            "is_live": False,
            "live_ais_connected": False,
            "commercial_charter_availability": "unavailable",
            "date_range": {
                "start": _to_iso_utc(min_pos_t),
                "end": _to_iso_utc(max_pos_t),
            },
            "vessel_types": vessel_type_counts,
            "particulars_completeness": particulars_completeness,
            "qualification_notice": "Historical AIS positions & physical particulars only; NOT open tonnage or commercial charter availability.",
        },
        "ports": {
            "domain": "ports",
            "label": DOMAIN_LABELS["ports"],
            "status": domain_statuses["ports"],
            "total_ports": total_ports,
            "total_aliases": total_aliases,
            "corridor_eligible_ports": corridor_eligible_ports,
            "non_corridor_ports": max(0, total_ports - corridor_eligible_ports),
            "graph_snapped_ports": graph_snapped_ports,
            "by_country": ports_by_country,
            "routing_topology": routing_topology,
        },
        "datasets": datasets_summary,
        "sources": {
            "domain": "sources",
            "label": DOMAIN_LABELS["sources"],
            "status": domain_statuses["sources"],
            "total_ingestion_runs": total_runs,
            "completed_runs": completed_runs,
            "partial_runs": partial_runs,
            "failed_runs": failed_runs,
            "quarantined_records": quarantined_count,
            "latest_successful_run": latest_run_summary,
            "latest_retrieval_utc": _to_iso_utc(latest_retrieval_entry[1]) if latest_retrieval_entry else None,
            "latest_retrieval_source": latest_retrieval_entry[0] if latest_retrieval_entry else None,
        },
    }

    return {
        "status": "ok",
        "generated_at": now_utc.isoformat(),
        "cache": {
            "ttl_seconds": CACHE_TTL_SECONDS,
            "cached": False,
            "note": "60-second in-memory cache is a read optimization only, not a freshness guarantee.",
        },
        "global_summary": {
            "total_genuine_records": total_genuine_records,
            "counting_methodology": (
                "total_genuine_records combines total_raw_observations "
                "(source_observations_excluding_derived: direct corridor freight + trade + env + bunker + AIS positions; "
                "plus derived freight_derived_spread SPREAD-USG-PNW-JPN) and registered_physical_entities "
                "(canonical ports + canonical vessels). Do not equate total_genuine_records with independent raw observations. "
                "Port aliases, freight_publications (Parquet partition records), ingestion_runs, and derived_dataset_rows "
                "(ForecastDataset rows across corridors & horizons) are reported separately to prevent double-counting."
            ),
            "breakdown": {
                "raw_observations_total": total_raw_observations,
                "raw_observations_excluding_derived_spread": total_raw_observations - freight_derived_count,
                "source_observations_excluding_derived": total_raw_observations - freight_derived_count,
                "freight_underlying_corridor": freight_underlying_count,
                "freight_derived_spread": freight_derived_count,
                "freight_reconstructed": freight_recon_count,
                "freight_publications_count": freight_pub_count,
                "trade_observations": trade_count,
                "environmental_observations": env_count,
                "bunker_observations": bunker_count,
                "ais_position_observations": vessel_positions,
                "registered_physical_entities": total_physical_entities,
                "canonical_ports": total_ports,
                "canonical_vessels": canonical_vessels,
                "identifier_mappings_port_aliases": total_aliases,
                "ingestion_audit_runs": total_runs,
                "quarantined_records": quarantined_count,
                "derived_dataset_rows": datasets_summary["total_dataset_rows"],
            },
            "connected_operational_sources": populated_source_count,
            "total_registered_source_feeds": len(source_health),
            "automated_remote_sources_active": operational_remote_count,
            "domains_populated_count": populated_domains_count,
            "domains_with_records_count": populated_domains_count,
            "domains_fully_populated_count": domains_fully_populated_count,
            "domains_partial_count": domains_partial_count,
            "domains_empty_or_unavailable_count": domains_empty_or_unavailable_count,
            "total_domains": len(EXPLORER_DOMAINS),
            "latest_successful_ingestion_timestamp": latest_run_summary["completed_at"] if latest_run_summary else None,
            "latest_successful_ingestion_source": latest_run_summary["source_name"] if latest_run_summary else None,
            "latest_data_retrieval_timestamp": _to_iso_utc(latest_retrieval_entry[1]) if latest_retrieval_entry else None,
            "latest_data_retrieval_table": latest_retrieval_entry[0] if latest_retrieval_entry else None,
        },
        "source_health": source_health,
        "domains": domains,
        "benchmarks": benchmarks_summary,
        "integrity_notices": [
            "Commercial freight rates cover strictly U.S.-Japan grain bulk corridors (PNW-JPN-BULK, USG-JPN-BULK) and their derived spread (SPREAD-USG-PNW-JPN).",
            "Persistence remains the active production forecasting baseline; experimental C3.2/C3.3 candidates are not promoted.",
            "Bunker fuel prices cover ARBUE & DKSKA only as indicative quotes; corridor hubs (USSEA, USPDX, USHOU, USMSY, JPYOK) have zero ingested quotes and display UNAVAILABLE.",
            "Historical MarineCadastre AIS positions and vessels do NOT indicate live telemetry or commercial charter availability.",
            "Trade customs unit values represent commodity prices and must NOT be equated to ocean freight rates.",
            "Generalized Eurostat SeaRoute 50km MARNET paths and port directories are not certified navigation intelligence.",
        ],
    }


def get_platform_data_overview(db: Session, use_cache: bool = True) -> Dict[str, Any]:
    """Return platform-wide multi-domain data overview with 60s fingerprint-aware cache."""
    try:
        bind_key = _db_bind_key(db)
        fingerprint = _compute_db_fingerprint(db)
    except SQLAlchemyError as exc:
        logger.warning("Overview query unavailable due to database error: %s", exc)
        return {
            "status": "unavailable",
            "generated_at": _utc_now().isoformat(),
            "reason": "Database tables unavailable or unreadable.",
            "cache": {"ttl_seconds": CACHE_TTL_SECONDS, "cached": False},
            "global_summary": None,
            "source_health": [],
            "domains": {},
            "benchmarks": _load_benchmark_artifacts_summary(),
            "integrity_notices": [],
        }

    now_mono = time.monotonic()
    if use_cache:
        with _CACHE_LOCK:
            cached_entry = _OVERVIEW_CACHE.get(bind_key)
            if cached_entry is not None:
                cached_at, cached_fp, cached_payload = cached_entry
                if cached_fp == fingerprint and (now_mono - cached_at) < CACHE_TTL_SECONDS:
                    return {
                        **cached_payload,
                        "cache": {
                            **cached_payload["cache"],
                            "cached": True,
                        },
                    }

    payload = _build_platform_overview_uncached(db)
    if use_cache:
        with _CACHE_LOCK:
            _OVERVIEW_CACHE[bind_key] = (now_mono, fingerprint, payload)
    return payload


def _paginate_query(
    query: Query,
    sort_col: Any,
    sort_order: str,
    page: int,
    page_size: int,
) -> Tuple[int, List[Any]]:
    total_count = int(query.count())
    order_expr = sort_col.asc() if sort_order == "asc" else sort_col.desc()
    offset = (page - 1) * page_size
    rows = query.order_by(order_expr).offset(offset).limit(page_size).all()
    return total_count, rows


def _make_envelope(
    *,
    domain: str,
    availability_status: str,
    page: int,
    page_size: int,
    total_count: int,
    sort_by: str,
    sort_order: str,
    allowed_sort_fields: Sequence[str],
    applied_filters: Dict[str, Any],
    filter_options: Dict[str, Any],
    summary: Dict[str, Any],
    integrity_notices: List[str],
    items: List[Dict[str, Any]],
    secondary_panels: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    total_pages = max(1, (total_count + page_size - 1) // page_size) if total_count > 0 else 0
    if availability_status == "unavailable":
        status = "unavailable"
    elif total_count == 0:
        status = "empty"
    else:
        status = "ok"
    return {
        "status": status,
        "domain": domain,
        "domain_label": DOMAIN_LABELS[domain],
        "availability_status": availability_status,
        "reason": reason,
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "allowed_sort_fields": list(allowed_sort_fields),
        "applied_filters": {k: v for k, v in applied_filters.items() if v is not None and v != ""},
        "filter_options": filter_options,
        "summary": summary,
        "integrity_notices": integrity_notices,
        "items": items,
        "secondary_panels": secondary_panels or {},
    }


def _resolve_sort(
    sort_by: Optional[str],
    default_sort: str,
    sort_columns: Dict[str, Any],
) -> str:
    chosen = sort_by or default_sort
    if chosen not in sort_columns:
        allowed = ", ".join(sorted(sort_columns.keys()))
        raise ValueError(f"Unsupported sort_by='{chosen}'. Allowed fields: {allowed}")
    return chosen


def _parse_iso_date(val: Optional[str], field_name: str) -> Optional[datetime.date]:
    if not val:
        return None
    try:
        return datetime.date.fromisoformat(val.strip()[:10])
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name} date '{val}'; expected YYYY-MM-DD.") from exc


def _query_freight_explorer(
    db: Session,
    *,
    page: int,
    page_size: int,
    sort_by: Optional[str],
    sort_order: str,
    search: Optional[str],
    route_code: Optional[str] = None,
    series_type: Optional[str] = None,
    reconstructed_only: Optional[bool] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    sort_map = {
        "observation_date": FreightObservation.observation_date,
        "route_code": FreightObservation.route_code,
        "rate_value": FreightObservation.rate_value,
        "is_derived": FreightObservation.is_derived,
        "is_reconstructed": FreightObservation.is_reconstructed,
        "ingestion_time": FreightObservation.ingestion_time,
    }
    chosen_sort = _resolve_sort(sort_by, "observation_date", sort_map)
    d_from = _parse_iso_date(date_from, "date_from")
    d_to = _parse_iso_date(date_to, "date_to")

    query = db.query(FreightObservation)
    if route_code and route_code != "all":
        query = query.filter(FreightObservation.route_code == route_code.strip())
    if series_type == "underlying":
        query = query.filter(FreightObservation.is_derived.is_(False))
    elif series_type == "spread":
        query = query.filter(FreightObservation.is_derived.is_(True))
    if reconstructed_only is True:
        query = query.filter(FreightObservation.is_reconstructed.is_(True))
    if d_from:
        query = query.filter(FreightObservation.observation_date >= d_from)
    if d_to:
        query = query.filter(FreightObservation.observation_date <= d_to)
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                FreightObservation.route_code.ilike(term),
                FreightObservation.source_name.ilike(term),
                FreightObservation.source_dataset_id.ilike(term),
                FreightObservation.reconciliation_method.ilike(term),
            )
        )

    total_db = db.query(func.count(FreightObservation.id)).scalar() or 0
    total_count, rows = _paginate_query(query, sort_map[chosen_sort], sort_order, page, page_size)
    routes_available = sorted(
        r[0] for r in db.query(func.distinct(FreightObservation.route_code)).all() if r[0]
    )

    items = [
        {
            "id": r.id,
            "route_code": r.route_code,
            "observation_date": _to_iso_utc(r.observation_date),
            "rate_value": r.rate_value,
            "currency": r.currency,
            "unit_of_measure": r.unit_of_measure,
            "is_derived": bool(r.is_derived),
            "series_classification": "derived_spread" if r.is_derived else "underlying_corridor",
            "rate_high": r.rate_high,
            "rate_low": r.rate_low,
            "observation_period": r.observation_period,
            "is_reconstructed": bool(r.is_reconstructed),
            "original_rate_value": r.original_rate_value,
            "reconciliation_method": r.reconciliation_method,
            "reconciliation_metadata": r.reconciliation_metadata,
            "source_name": r.source_name,
            "source_dataset_id": r.source_dataset_id,
            "license_type": r.license_type,
            "publication_time": _to_iso_utc(r.publication_time),
            "ingestion_time": _to_iso_utc(r.ingestion_time),
            "processing_time": _to_iso_utc(r.processing_time),
            "ingestion_run_id": r.ingestion_run_id,
            "idempotency_key": r.idempotency_key,
        }
        for r in rows
    ]

    underlying, derived = _corridor_summary_rows(db)
    recon_total = db.query(func.count(FreightObservation.id)).filter(FreightObservation.is_reconstructed.is_(True)).scalar() or 0
    min_d = db.query(func.min(FreightObservation.observation_date)).scalar()
    max_d = db.query(func.max(FreightObservation.observation_date)).scalar()

    return _make_envelope(
        domain="freight",
        availability_status="populated" if total_db > 0 else "empty",
        page=page,
        page_size=page_size,
        total_count=total_count,
        sort_by=chosen_sort,
        sort_order=sort_order,
        allowed_sort_fields=list(sort_map.keys()),
        applied_filters={
            "route_code": route_code,
            "series_type": series_type,
            "reconstructed_only": reconstructed_only,
            "date_from": date_from,
            "date_to": date_to,
            "search": search,
        },
        filter_options={
            "route_codes": routes_available,
            "series_types": ["all", "underlying", "spread"],
        },
        summary={
            "total_database_observations": total_db,
            "underlying_corridor_observations": sum(c["observation_count"] for c in underlying),
            "derived_spread_observations": sum(c["observation_count"] for c in derived),
            "reconstructed_observations": recon_total,
            "date_range": {"start": _to_iso_utc(min_d), "end": _to_iso_utc(max_d)},
        },
        integrity_notices=[
            "SPREAD-USG-PNW-JPN is a derived spread series (USG minus PNW), separated from direct corridor benchmarks and disallowed for direct forecasting.",
            "Observed USDA SODA ehs5-yac3 rows contain no row-level publication_time (preserved as NULL); reconstructed entries preserve original_rate_value and reconciliation_method.",
        ],
        items=items,
        secondary_panels={"corridors": underlying + derived},
        reason="No freight rate observations match the current filter criteria." if total_count == 0 else None,
    )


def _query_trade_explorer(
    db: Session,
    *,
    page: int,
    page_size: int,
    sort_by: Optional[str],
    sort_order: str,
    search: Optional[str],
    reporter_iso2: Optional[str] = None,
    partner_iso2: Optional[str] = None,
    commodity_code: Optional[str] = None,
    trade_flow: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    sort_map = {
        "period_date": TradeObservation.period_date,
        "reporter_country": TradeObservation.reporter_country,
        "partner_country": TradeObservation.partner_country,
        "commodity_name": TradeObservation.commodity_name,
        "volume_metric_tons": TradeObservation.volume_metric_tons,
        "trade_value_usd": TradeObservation.trade_value_usd,
        "unit_price_usd_per_mt": TradeObservation.unit_price_usd_per_mt,
        "source_name": TradeObservation.source_name,
    }
    chosen_sort = _resolve_sort(sort_by, "period_date", sort_map)
    d_from = _parse_iso_date(date_from, "date_from")
    d_to = _parse_iso_date(date_to, "date_to")

    query = db.query(TradeObservation)
    if reporter_iso2 and reporter_iso2 != "all":
        query = query.filter(TradeObservation.reporter_iso2 == reporter_iso2.strip().upper())
    if partner_iso2 and partner_iso2 != "all":
        query = query.filter(TradeObservation.partner_iso2 == partner_iso2.strip().upper())
    if commodity_code and commodity_code != "all":
        query = query.filter(TradeObservation.commodity_code == commodity_code.strip())
    if trade_flow and trade_flow != "all":
        query = query.filter(TradeObservation.trade_flow == trade_flow.strip())
    if d_from:
        query = query.filter(TradeObservation.period_date >= d_from)
    if d_to:
        query = query.filter(TradeObservation.period_date <= d_to)
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                TradeObservation.reporter_country.ilike(term),
                TradeObservation.partner_country.ilike(term),
                TradeObservation.commodity_name.ilike(term),
                TradeObservation.commodity_code.ilike(term),
                TradeObservation.source_name.ilike(term),
            )
        )

    total_db = db.query(func.count(TradeObservation.id)).scalar() or 0
    total_count, rows = _paginate_query(query, sort_map[chosen_sort], sort_order, page, page_size)

    reporters = sorted(r[0] for r in db.query(func.distinct(TradeObservation.reporter_iso2)).all() if r[0])
    partners = sorted(r[0] for r in db.query(func.distinct(TradeObservation.partner_iso2)).all() if r[0])
    commodities = [
        {"code": r[0], "name": r[1]}
        for r in db.query(TradeObservation.commodity_code, TradeObservation.commodity_name)
        .distinct()
        .order_by(TradeObservation.commodity_code.asc())
        .all()
    ]

    items = [
        {
            "id": r.id,
            "reporter_country": r.reporter_country,
            "reporter_iso2": r.reporter_iso2,
            "partner_country": r.partner_country,
            "partner_iso2": r.partner_iso2,
            "commodity_code": r.commodity_code,
            "commodity_name": r.commodity_name,
            "trade_flow": r.trade_flow,
            "period_date": _to_iso_utc(r.period_date),
            "period_type": r.period_type,
            "volume_metric_tons": r.volume_metric_tons,
            "trade_value_usd": r.trade_value_usd,
            "unit_price_usd_per_mt": r.unit_price_usd_per_mt,
            "unit_price_semantic_label": "Commodity Customs Unit Value (USD/MT) — NOT an Ocean Freight Rate",
            "source_name": r.source_name,
            "license_type": r.license_type,
            "created_at": _to_iso_utc(r.created_at),
            "idempotency_key": r.idempotency_key,
        }
        for r in rows
    ]

    return _make_envelope(
        domain="trade",
        availability_status="populated" if total_db > 0 else "empty",
        page=page,
        page_size=page_size,
        total_count=total_count,
        sort_by=chosen_sort,
        sort_order=sort_order,
        allowed_sort_fields=list(sort_map.keys()),
        applied_filters={
            "reporter_iso2": reporter_iso2,
            "partner_iso2": partner_iso2,
            "commodity_code": commodity_code,
            "trade_flow": trade_flow,
            "date_from": date_from,
            "date_to": date_to,
            "search": search,
        },
        filter_options={
            "reporters": reporters,
            "partners": partners,
            "commodities": commodities,
        },
        summary={
            "total_database_observations": total_db,
            "distinct_commodities": len(commodities),
        },
        integrity_notices=[
            "Trade unit_price_usd_per_mt reflects underlying commodity export valuation (grain, rice, iron ore, ferro-chromium) and is NEVER an ocean freight rate.",
            "Trade records cover isolated late-2024 monthly declarations and cannot be extrapolated as continuous multi-decade history.",
        ],
        items=items,
        reason="No trade observations match the active filters." if total_count == 0 else None,
    )


def _query_environmental_explorer(
    db: Session,
    *,
    page: int,
    page_size: int,
    sort_by: Optional[str],
    sort_order: str,
    search: Optional[str],
    station_id: Optional[str] = None,
    sea_area: Optional[str] = None,
    quality_status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    sort_map = {
        "observation_time": EnvironmentalObservation.observation_time,
        "station_id": EnvironmentalObservation.station_id,
        "station_name": EnvironmentalObservation.station_name,
        "wave_height_meters": EnvironmentalObservation.wave_height_meters,
        "wind_speed_knots": EnvironmentalObservation.wind_speed_knots,
        "air_pressure_hpa": EnvironmentalObservation.air_pressure_hpa,
        "sea_surface_temp_c": EnvironmentalObservation.sea_surface_temp_c,
    }
    chosen_sort = _resolve_sort(sort_by, "observation_time", sort_map)
    d_from = _parse_iso_date(date_from, "date_from")
    d_to = _parse_iso_date(date_to, "date_to")

    query = db.query(EnvironmentalObservation)
    if station_id and station_id != "all":
        query = query.filter(EnvironmentalObservation.station_id == station_id.strip())
    if sea_area and sea_area != "all":
        query = query.filter(EnvironmentalObservation.sea_area.ilike(f"%{sea_area.strip()}%"))
    if quality_status and quality_status != "all":
        query = query.filter(EnvironmentalObservation.quality_status == quality_status.strip())
    if d_from:
        query = query.filter(
            EnvironmentalObservation.observation_time
            >= datetime.datetime(d_from.year, d_from.month, d_from.day, tzinfo=datetime.timezone.utc)
        )
    if d_to:
        query = query.filter(
            EnvironmentalObservation.observation_time
            <= datetime.datetime(d_to.year, d_to.month, d_to.day, 23, 59, 59, tzinfo=datetime.timezone.utc)
        )
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                EnvironmentalObservation.station_id.ilike(term),
                EnvironmentalObservation.station_name.ilike(term),
                EnvironmentalObservation.sea_area.ilike(term),
                EnvironmentalObservation.source_name.ilike(term),
            )
        )

    total_db = db.query(func.count(EnvironmentalObservation.id)).scalar() or 0
    total_count, rows = _paginate_query(query, sort_map[chosen_sort], sort_order, page, page_size)
    now_utc = _utc_now()

    stations = [
        {"station_id": r[0], "station_name": r[1]}
        for r in db.query(EnvironmentalObservation.station_id, EnvironmentalObservation.station_name)
        .distinct()
        .order_by(EnvironmentalObservation.station_id.asc())
        .all()
    ]
    sea_areas = sorted(r[0] for r in db.query(func.distinct(EnvironmentalObservation.sea_area)).all() if r[0])

    items = []
    for r in rows:
        freshness, age_hours = _classify_env_freshness(r.observation_time, now_utc)
        items.append(
            {
                "id": r.id,
                "station_id": r.station_id,
                "station_name": r.station_name,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "sea_area": r.sea_area,
                "observation_time": _to_iso_utc(r.observation_time),
                "retrieval_time_utc": _to_iso_utc(r.retrieval_time_utc),
                "freshness_classification": freshness,
                "age_hours": age_hours,
                "wave_height_meters": r.wave_height_meters,
                "wave_period_seconds": r.wave_period_seconds,
                "wind_speed_knots": r.wind_speed_knots,
                "wind_direction_degrees": r.wind_direction_degrees,
                "sea_surface_temp_c": r.sea_surface_temp_c,
                "air_pressure_hpa": r.air_pressure_hpa,
                "source_name": r.source_name,
                "license_type": r.license_type,
                "quality_status": r.quality_status,
                "idempotency_key": r.idempotency_key,
            }
        )

    max_obs = db.query(func.max(EnvironmentalObservation.observation_time)).scalar()
    overall_freshness, overall_age_hours = _classify_env_freshness(max_obs, now_utc)

    return _make_envelope(
        domain="environmental",
        availability_status="populated" if total_db > 0 else "empty",
        page=page,
        page_size=page_size,
        total_count=total_count,
        sort_by=chosen_sort,
        sort_order=sort_order,
        allowed_sort_fields=list(sort_map.keys()),
        applied_filters={
            "station_id": station_id,
            "sea_area": sea_area,
            "quality_status": quality_status,
            "date_from": date_from,
            "date_to": date_to,
            "search": search,
        },
        filter_options={
            "stations": stations,
            "sea_areas": sea_areas,
        },
        summary={
            "total_database_observations": total_db,
            "stations_count": len(stations),
            "latest_observation_utc": _to_iso_utc(max_obs),
            "overall_freshness": overall_freshness if total_db > 0 else "empty",
            "latest_age_hours": overall_age_hours,
        },
        integrity_notices=[
            "Missing buoy sensor values (such as wave_height_meters or wave_period_seconds on select timestamps) remain NULL and are NEVER interpolated or filled.",
            "Moored buoy observations reflect point conditions near coastal fairways only; open mid-ocean transit weather is unmonitored.",
        ],
        items=items,
        reason="No environmental buoy observations match the selected filters." if total_count == 0 else None,
    )


def _query_bunker_explorer(
    db: Session,
    *,
    page: int,
    page_size: int,
    sort_by: Optional[str],
    sort_order: str,
    search: Optional[str],
    port_code: Optional[str] = None,
    fuel_grade: Optional[str] = None,
    pricing_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    sort_map = {
        "observation_time": BunkerPriceObservation.observation_time,
        "port_code": BunkerPriceObservation.port_code,
        "port_name": BunkerPriceObservation.port_name,
        "fuel_grade": BunkerPriceObservation.fuel_grade,
        "price_value": BunkerPriceObservation.price_value,
        "retrieval_time_utc": BunkerPriceObservation.retrieval_time_utc,
    }
    chosen_sort = _resolve_sort(sort_by, "observation_time", sort_map)
    d_from = _parse_iso_date(date_from, "date_from")
    d_to = _parse_iso_date(date_to, "date_to")

    query = db.query(BunkerPriceObservation)
    if port_code and port_code != "all":
        query = query.filter(BunkerPriceObservation.port_code == port_code.strip().upper())
    if fuel_grade and fuel_grade != "all":
        query = query.filter(BunkerPriceObservation.fuel_grade == fuel_grade.strip().upper())
    if pricing_type and pricing_type != "all":
        query = query.filter(BunkerPriceObservation.pricing_type == pricing_type.strip())
    if d_from:
        query = query.filter(
            BunkerPriceObservation.observation_time
            >= datetime.datetime(d_from.year, d_from.month, d_from.day, tzinfo=datetime.timezone.utc)
        )
    if d_to:
        query = query.filter(
            BunkerPriceObservation.observation_time
            <= datetime.datetime(d_to.year, d_to.month, d_to.day, 23, 59, 59, tzinfo=datetime.timezone.utc)
        )
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                BunkerPriceObservation.port_code.ilike(term),
                BunkerPriceObservation.port_name.ilike(term),
                BunkerPriceObservation.fuel_grade.ilike(term),
                BunkerPriceObservation.source_name.ilike(term),
                BunkerPriceObservation.source_feed.ilike(term),
            )
        )

    total_db = db.query(func.count(BunkerPriceObservation.id)).scalar() or 0
    total_count, rows = _paginate_query(query, sort_map[chosen_sort], sort_order, page, page_size)
    corridor_hubs = _evaluate_corridor_bunker_coverage(db)
    quarantined_bunker_count = (
        db.query(func.count(QuarantinedRecord.id))
        .filter(
            or_(
                QuarantinedRecord.source_name.ilike("%bunker%"),
                QuarantinedRecord.error_code.ilike("%currency%"),
                QuarantinedRecord.error_code.ilike("%bunker%"),
            )
        )
        .scalar()
        or 0
    )

    items = [
        {
            "id": r.id,
            "port_code": r.port_code,
            "port_name": r.port_name,
            "country_code": r.country_code,
            "fuel_grade": r.fuel_grade,
            "price_value": r.price_value,
            "currency": r.currency,
            "unit": r.unit,
            "pricing_type": r.pricing_type,
            "indicative_qualification": "Indicative Market Quote — Not a Firm Supplier Offer",
            "delivery_basis": r.delivery_basis,
            "source_name": r.source_name,
            "source_feed": r.source_feed,
            "observation_time": _to_iso_utc(r.observation_time),
            "retrieval_time_utc": _to_iso_utc(r.retrieval_time_utc),
            "provenance_notes": r.provenance_notes,
            "quarantine_status": "accepted",
            "idempotency_key": r.idempotency_key,
        }
        for r in rows
    ]

    ports_avail = [
        {"port_code": r[0], "port_name": r[1]}
        for r in db.query(BunkerPriceObservation.port_code, BunkerPriceObservation.port_name).distinct().all()
    ]
    grades_avail = sorted(r[0] for r in db.query(func.distinct(BunkerPriceObservation.fuel_grade)).all() if r[0])

    hubs_covered = sum(1 for h in corridor_hubs if h["status"] == "available")
    availability_status = "partial" if (total_db > 0 and hubs_covered == 0) else ("populated" if total_db > 0 else "empty")

    return _make_envelope(
        domain="bunker",
        availability_status=availability_status,
        page=page,
        page_size=page_size,
        total_count=total_count,
        sort_by=chosen_sort,
        sort_order=sort_order,
        allowed_sort_fields=list(sort_map.keys()),
        applied_filters={
            "port_code": port_code,
            "fuel_grade": fuel_grade,
            "pricing_type": pricing_type,
            "date_from": date_from,
            "date_to": date_to,
            "search": search,
        },
        filter_options={
            "ports": ports_avail,
            "fuel_grades": grades_avail,
        },
        summary={
            "total_accepted_quotes": total_db,
            "quarantined_bunker_records_in_db": quarantined_bunker_count,
            "corridor_hubs_covered": hubs_covered,
            "corridor_hubs_total": len(corridor_hubs),
        },
        integrity_notices=[
            "Primary grain corridor hubs (Seattle USSEA, Portland USPDX, Houston USHOU, New Orleans USMSY, Panama PAPAC, Yokohama JPYOK) currently have ZERO ingested bunker quotes and remain honestly UNAVAILABLE.",
            "Accepted quotes (Buenos Aires ARBUE and Skagen DKSKA) are indicative feed observations only, not contracted bunker stems, and are never substituted onto corridor hubs.",
        ],
        items=items,
        secondary_panels={
            "corridor_hub_coverage": corridor_hubs,
        },
        reason=(
            "No bunker quotes found for the selected filter."
            if total_count == 0 and total_db > 0
            else ("Zero bunker quotes ingested in database." if total_db == 0 else None)
        ),
    )


def _query_vessels_explorer(
    db: Session,
    *,
    page: int,
    page_size: int,
    sort_by: Optional[str],
    sort_order: str,
    search: Optional[str],
    vessel_type: Optional[str] = None,
    vessel_class: Optional[str] = None,
    has_imo: Optional[bool] = None,
    has_dwt: Optional[bool] = None,
    **_: Any,
) -> Dict[str, Any]:
    total_vessels_tbl = db.query(func.count(Vessel.id)).scalar() or 0
    total_pos_tbl = db.query(func.count(VesselPosition.id)).scalar() or 0

    latest_pos_subq = (
        db.query(
            VesselPosition.mmsi.label("mmsi"),
            func.max(VesselPosition.id).label("max_pos_id"),
        )
        .group_by(VesselPosition.mmsi)
        .subquery()
    )
    LatestPos = aliased(VesselPosition)

    if total_vessels_tbl > 0:
        sort_map = {
            "vessel_name": Vessel.vessel_name,
            "mmsi": Vessel.mmsi,
            "vessel_type": Vessel.vessel_type,
            "vessel_class": Vessel.vessel_class,
            "dwt_tonnage": Vessel.dwt_tonnage,
            "length_meters": Vessel.length_meters,
            "draft_max_meters": Vessel.draft_max_meters,
            "observation_time": LatestPos.observation_time,
        }
        chosen_sort = _resolve_sort(sort_by, "vessel_name", sort_map)
        query = (
            db.query(Vessel, LatestPos)
            .outerjoin(latest_pos_subq, Vessel.mmsi == latest_pos_subq.c.mmsi)
            .outerjoin(LatestPos, LatestPos.id == latest_pos_subq.c.max_pos_id)
        )
        if vessel_type and vessel_type != "all":
            query = query.filter(Vessel.vessel_type == vessel_type.strip())
        if vessel_class and vessel_class != "all":
            if vessel_class == "unclassified":
                query = query.filter(Vessel.vessel_class.is_(None))
            else:
                query = query.filter(Vessel.vessel_class == vessel_class.strip())
        if has_imo is True:
            query = query.filter(Vessel.imo.isnot(None))
        elif has_imo is False:
            query = query.filter(Vessel.imo.is_(None))
        if has_dwt is True:
            query = query.filter(Vessel.dwt_tonnage.isnot(None))
        elif has_dwt is False:
            query = query.filter(Vessel.dwt_tonnage.is_(None))
        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    Vessel.vessel_name.ilike(term),
                    Vessel.mmsi.ilike(term),
                    Vessel.imo.ilike(term),
                    Vessel.vessel_type.ilike(term),
                    Vessel.vessel_class.ilike(term),
                    LatestPos.destination.ilike(term),
                )
            )
        total_count, paired_rows = _paginate_query(query, sort_map[chosen_sort], sort_order, page, page_size)
        items = []
        for v, pos in paired_rows:
            missing = []
            for label, val in (
                ("imo", v.imo),
                ("vessel_class", v.vessel_class),
                ("dwt_tonnage", v.dwt_tonnage),
                ("gross_tonnage", v.gross_tonnage),
                ("length_meters", v.length_meters),
                ("width_meters", v.width_meters),
                ("draft_max_meters", v.draft_max_meters),
                ("built_year", v.built_year),
                ("flag_country", v.flag_country),
            ):
                if val is None:
                    missing.append(label)
            items.append(
                {
                    "mmsi": v.mmsi,
                    "imo": v.imo,
                    "vessel_name": v.vessel_name,
                    "vessel_type": v.vessel_type,
                    "vessel_class": v.vessel_class,
                    "commercial_status": v.commercial_status,
                    "telemetry_qualification": "Historical AIS Research Sample (NOT Open Tonnage / Live Fleet)",
                    "is_live": False,
                    "latitude": pos.latitude if pos else None,
                    "longitude": pos.longitude if pos else None,
                    "speed_knots": pos.speed_knots if pos else None,
                    "course_degrees": pos.course_degrees if pos else None,
                    "heading_degrees": pos.heading_degrees if pos else None,
                    "destination": pos.destination if pos else None,
                    "navigational_status": pos.navigational_status if pos else None,
                    "observation_time": _to_iso_utc(pos.observation_time) if pos else None,
                    "ingestion_time": _to_iso_utc(pos.ingestion_time) if pos else None,
                    "source_name": v.source_name,
                    "source_partition": pos.source_partition if pos else None,
                    "length_meters": v.length_meters,
                    "width_meters": v.width_meters,
                    "draft_max_meters": v.draft_max_meters,
                    "dwt_tonnage": v.dwt_tonnage,
                    "gross_tonnage": v.gross_tonnage,
                    "built_year": v.built_year,
                    "flag_country": v.flag_country,
                    "missing_particulars": missing,
                }
            )
        types_avail = sorted(r[0] for r in db.query(func.distinct(Vessel.vessel_type)).all() if r[0])
        classes_avail = sorted(r[0] for r in db.query(func.distinct(Vessel.vessel_class)).all() if r[0])
    else:
        # Fallback if isolated DB has VesselPosition rows without synced Vessel registry rows
        sort_map = {
            "vessel_name": VesselPosition.vessel_name,
            "mmsi": VesselPosition.mmsi,
            "vessel_type": VesselPosition.vessel_type,
            "length_meters": VesselPosition.length_meters,
            "draft_max_meters": VesselPosition.draft_meters,
            "observation_time": VesselPosition.observation_time,
        }
        chosen_sort = _resolve_sort(sort_by, "observation_time", sort_map)
        query = db.query(VesselPosition)
        if vessel_type and vessel_type != "all":
            query = query.filter(VesselPosition.vessel_type == vessel_type.strip())
        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    VesselPosition.vessel_name.ilike(term),
                    VesselPosition.mmsi.ilike(term),
                    VesselPosition.imo.ilike(term),
                    VesselPosition.vessel_type.ilike(term),
                )
            )
        total_count, pos_rows = _paginate_query(query, sort_map[chosen_sort], sort_order, page, page_size)
        items = [
            {
                "mmsi": p.mmsi,
                "imo": p.imo,
                "vessel_name": p.vessel_name,
                "vessel_type": p.vessel_type,
                "vessel_class": None,
                "commercial_status": "unknown",
                "telemetry_qualification": "Historical AIS Research Sample (NOT Open Tonnage / Live Fleet)",
                "is_live": False,
                "latitude": p.latitude,
                "longitude": p.longitude,
                "speed_knots": p.speed_knots,
                "course_degrees": p.course_degrees,
                "heading_degrees": p.heading_degrees,
                "destination": p.destination,
                "navigational_status": p.navigational_status,
                "observation_time": _to_iso_utc(p.observation_time),
                "ingestion_time": _to_iso_utc(p.ingestion_time),
                "source_name": p.source_name,
                "source_partition": p.source_partition,
                "length_meters": p.length_meters,
                "width_meters": p.width_meters,
                "draft_max_meters": p.draft_meters,
                "dwt_tonnage": None,
                "gross_tonnage": None,
                "built_year": None,
                "flag_country": None,
                "missing_particulars": ["vessel_class", "dwt_tonnage", "gross_tonnage", "built_year", "flag_country"],
            }
            for p in pos_rows
        ]
        types_avail = sorted(r[0] for r in db.query(func.distinct(VesselPosition.vessel_type)).all() if r[0])
        classes_avail = []

    return _make_envelope(
        domain="vessels",
        availability_status="populated" if (total_vessels_tbl > 0 or total_pos_tbl > 0) else "empty",
        page=page,
        page_size=page_size,
        total_count=total_count,
        sort_by=chosen_sort,
        sort_order=sort_order,
        allowed_sort_fields=list(sort_map.keys()),
        applied_filters={
            "vessel_type": vessel_type,
            "vessel_class": vessel_class,
            "has_imo": has_imo,
            "has_dwt": has_dwt,
            "search": search,
        },
        filter_options={
            "vessel_types": types_avail,
            "vessel_classes": classes_avail,
        },
        summary={
            "canonical_vessels": total_vessels_tbl,
            "position_observations": total_pos_tbl,
            "telemetry_mode": "historical",
            "commercial_charter_availability": "unavailable",
        },
        integrity_notices=[
            "AIS positions are a historical U.S. coastal research sample (MarineCadastre) — NEVER live fleet tracking or commercial open tonnage.",
            "Missing deadweight tonnage (dwt_tonnage), gross tonnage, built year, flag, and IMO values are explicitly preserved as missing; charter feasibility cannot be assumed from AIS presence.",
        ],
        items=items,
        reason="No vessels match the active filter criteria." if total_count == 0 else None,
    )


def _query_ports_explorer(
    db: Session,
    *,
    page: int,
    page_size: int,
    sort_by: Optional[str],
    sort_order: str,
    search: Optional[str],
    country_code: Optional[str] = None,
    port_region: Optional[str] = None,
    corridor_eligible: Optional[str] = None,
    graph_connected: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    sort_map = {
        "unlocode": Port.unlocode,
        "name": Port.name,
        "country_code": Port.country_code,
        "port_region": Port.port_region,
        "is_monitored_corridor_eligible": Port.is_monitored_corridor_eligible,
        "nearest_graph_node": Port.nearest_graph_node,
        "latitude": Port.latitude,
        "longitude": Port.longitude,
    }
    chosen_sort = _resolve_sort(sort_by, "name", sort_map)

    query = db.query(Port)
    if country_code and country_code != "all":
        query = query.filter(Port.country_code == country_code.strip().upper())
    if port_region and port_region != "all":
        query = query.filter(Port.port_region == port_region.strip())
    if corridor_eligible == "true":
        query = query.filter(Port.is_monitored_corridor_eligible.is_(True))
    elif corridor_eligible == "false":
        query = query.filter(Port.is_monitored_corridor_eligible.is_(False))
    if graph_connected == "true":
        query = query.filter(Port.nearest_graph_node.isnot(None))
    elif graph_connected == "false":
        query = query.filter(Port.nearest_graph_node.is_(None))
    if search and search.strip():
        term = f"%{search.strip()}%"
        matching_alias_ports = select(PortAlias.port_unlocode).where(PortAlias.alias_name.ilike(term))
        query = query.filter(
            or_(
                Port.unlocode.ilike(term),
                Port.port_id.ilike(term),
                Port.name.ilike(term),
                Port.country.ilike(term),
                Port.port_region.ilike(term),
                Port.unlocode.in_(matching_alias_ports),
            )
        )

    total_db = db.query(func.count(Port.id)).scalar() or 0
    total_aliases = db.query(func.count(PortAlias.id)).scalar() or 0
    total_count, rows = _paginate_query(query, sort_map[chosen_sort], sort_order, page, page_size)

    # Fetch aliases only for the paginated slice of UN/LOCODEs
    page_unlocodes = [p.unlocode for p in rows]
    aliases_by_port: Dict[str, List[Dict[str, str]]] = {u: [] for u in page_unlocodes}
    if page_unlocodes:
        alias_rows = (
            db.query(PortAlias)
            .filter(PortAlias.port_unlocode.in_(page_unlocodes))
            .order_by(PortAlias.alias_name.asc())
            .all()
        )
        for a in alias_rows:
            aliases_by_port.setdefault(a.port_unlocode, []).append(
                {
                    "alias_name": a.alias_name,
                    "alias_type": a.alias_type,
                    "source_name": a.source_name,
                }
            )

    countries = [
        {"country_code": r[0], "country": r[1]}
        for r in db.query(Port.country_code, Port.country).distinct().order_by(Port.country_code.asc()).all()
    ]
    regions = sorted(r[0] for r in db.query(func.distinct(Port.port_region)).all() if r[0])

    items = [
        {
            "unlocode": p.unlocode,
            "port_id": p.port_id,
            "source_id": p.source_id,
            "name": p.name,
            "country": p.country,
            "country_code": p.country_code,
            "subdivision": p.subdivision,
            "port_region": p.port_region,
            "latitude": p.latitude,
            "longitude": p.longitude,
            "crs": p.crs,
            "coordinate_type": p.coordinate_type,
            "is_monitored_corridor_eligible": bool(p.is_monitored_corridor_eligible),
            "corridor_coverage": _resolve_port_corridor_tag(p),
            "nearest_graph_node": p.nearest_graph_node,
            "marnet_connectivity_status": "snapped" if p.nearest_graph_node else "directory_only",
            "aliases_count": len(aliases_by_port.get(p.unlocode, [])),
            "aliases": aliases_by_port.get(p.unlocode, []),
            "provenance": p.provenance,
            "license": p.license,
            "access_limitations": p.access_limitations,
            "source_url": p.source_url,
            "navigation_warning": "Generalized MARNET topology & UN/LOCODE metadata — NOT certified navigation intelligence.",
        }
        for p in rows
    ]

    return _make_envelope(
        domain="ports",
        availability_status="populated" if total_db > 0 else "empty",
        page=page,
        page_size=page_size,
        total_count=total_count,
        sort_by=chosen_sort,
        sort_order=sort_order,
        allowed_sort_fields=list(sort_map.keys()),
        applied_filters={
            "country_code": country_code,
            "port_region": port_region,
            "corridor_eligible": corridor_eligible,
            "graph_connected": graph_connected,
            "search": search,
        },
        filter_options={
            "countries": countries,
            "regions": regions,
        },
        summary={
            "total_ports": total_db,
            "total_aliases": total_aliases,
            "corridor_eligible_ports": db.query(func.count(Port.id)).filter(Port.is_monitored_corridor_eligible.is_(True)).scalar() or 0,
            "graph_snapped_ports": db.query(func.count(Port.id)).filter(Port.nearest_graph_node.isnot(None)).scalar() or 0,
        },
        integrity_notices=[
            "Generalized MARNET 50km shipping-lane topology and port directory points are NOT certified navigational charts, depth soundings, or pilotage intelligence.",
            "Directory presence or MARNET graph connectivity does NOT imply an active freight rate benchmark or forecasting model.",
        ],
        items=items,
        secondary_panels={"routing_topology": _get_routing_topology_summary()},
        reason="No ports match the active filter criteria." if total_count == 0 else None,
    )


def _get_cached_dataset(
    db: Session,
    bind_key: str,
    fp: Tuple[Any, ...],
    route_code: str,
    horizon: int,
    preloaded_series: Optional[Dict[str, Tuple[List[Tuple[datetime.date, float]], str]]] = None,
):
    now_mono = time.monotonic()
    key = (bind_key, fp, route_code, horizon)
    with _CACHE_LOCK:
        hit = _DATASET_CACHE.get(key)
        if hit is not None and (now_mono - hit[0]) < CACHE_TTL_SECONDS:
            return hit[1]
    # Prevent fallback to workspace parquet_data when the active DB session has no FreightPublication rows.
    ds = build_forecast_dataset(
        db=db,
        route_code=route_code,
        horizon_months=horizon,
        storage_dir=Path("__pelagos_sql_session_only__"),
        preloaded_series=preloaded_series,
    )
    with _CACHE_LOCK:
        _DATASET_CACHE[key] = (now_mono, ds)
    return ds


def _query_datasets_explorer(
    db: Session,
    *,
    page: int,
    page_size: int,
    sort_by: Optional[str],
    sort_order: str,
    search: Optional[str],
    route_code: Optional[str] = None,
    horizon: Optional[int] = None,
    regime_id: Optional[str] = None,
    is_labeled: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    sort_fields = [
        "observation_date",
        "decision_date",
        "target_date",
        "route_code",
        "horizon_months",
        "target_value",
        "target_change",
        "regime_id",
        "is_labeled",
    ]
    chosen_sort = sort_by or "observation_date"
    if chosen_sort not in sort_fields:
        raise ValueError(f"Unsupported sort_by='{chosen_sort}'. Allowed fields: {', '.join(sort_fields)}")

    d_from = _parse_iso_date(date_from, "date_from")
    d_to = _parse_iso_date(date_to, "date_to")

    benchmarks = _load_benchmark_artifacts_summary(include_details=True)

    # Reject derived spread route explicitly with honest unavailable status rather than silent coercion
    if route_code in DISALLOWED_DERIVED_ROUTES:
        feature_inventory = generate_feature_inventory(db)
        return _make_envelope(
            domain="datasets",
            availability_status="unavailable",
            page=page,
            page_size=page_size,
            total_count=0,
            sort_by=chosen_sort,
            sort_order=sort_order,
            allowed_sort_fields=sort_fields,
            applied_filters={"route_code": route_code, "horizon": horizon},
            filter_options={"routes": list(SUPPORTED_DIRECT_ROUTES), "horizons": list(SUPPORTED_HORIZONS)},
            summary=_summarize_forecast_datasets_fast(db),
            integrity_notices=[
                f"Derived series '{route_code}' cannot be modeled as a direct corridor forecast dataset.",
                "Persistence remains the active production baseline; experimental ML models are NOT promoted.",
            ],
            items=[],
            secondary_panels={"feature_inventory": feature_inventory, "benchmarks": benchmarks},
            reason=f"Derived spread '{route_code}' is excluded from direct forecasting datasets.",
        )

    if route_code and route_code != "all" and route_code not in SUPPORTED_DIRECT_ROUTES:
        feature_inventory = generate_feature_inventory(db)
        return _make_envelope(
            domain="datasets",
            availability_status="unavailable",
            page=page,
            page_size=page_size,
            total_count=0,
            sort_by=chosen_sort,
            sort_order=sort_order,
            allowed_sort_fields=sort_fields,
            applied_filters={"route_code": route_code, "horizon": horizon},
            filter_options={"routes": list(SUPPORTED_DIRECT_ROUTES), "horizons": list(SUPPORTED_HORIZONS)},
            summary=_summarize_forecast_datasets_fast(db),
            integrity_notices=[
                "Forecasting datasets are strictly bounded to supported U.S.-Japan grain bulk corridors.",
            ],
            items=[],
            secondary_panels={"feature_inventory": feature_inventory, "benchmarks": benchmarks},
            reason=f"Unsupported route '{route_code}'. Supported direct routes: {', '.join(SUPPORTED_DIRECT_ROUTES)}.",
        )

    target_routes = [route_code] if (route_code and route_code in SUPPORTED_DIRECT_ROUTES) else list(SUPPORTED_DIRECT_ROUTES)
    if horizon is not None:
        if horizon not in SUPPORTED_HORIZONS:
            raise ValueError(f"Unsupported horizon={horizon}. Allowed horizons: {', '.join(map(str, SUPPORTED_HORIZONS))}")
        target_horizons = [horizon]
    else:
        target_horizons = list(SUPPORTED_HORIZONS)

    bind_key = _db_bind_key(db)
    fp = _compute_db_fingerprint(db)
    cache_key = (bind_key, fp, tuple(target_routes), tuple(target_horizons))
    now_mono = time.monotonic()

    cached_bundle: Optional[Dict[str, Any]] = None
    with _CACHE_LOCK:
        hit = _DATASET_ITEMS_CACHE.get(cache_key)
        if hit is not None and (now_mono - hit[0]) < CACHE_TTL_SECONDS:
            cached_bundle = hit[1]

    if cached_bundle is not None:
        combined_rows = cached_bundle["combined_rows"]
        build_errors = cached_bundle["build_errors"]
        regimes_seen = cached_bundle["regimes_seen"]
        feature_inventory = cached_bundle["feature_inventory"]
        fast_summary = cached_bundle["fast_summary"]
    else:
        combined_rows = []
        build_errors = []
        regimes_seen = set()

        # Preload each required direct corridor rate series once per request instead of
        # re-opening DuckDB/Parquet or SQL across every (route, horizon) pair.
        preloaded_series: Dict[str, Tuple[List[Tuple[datetime.date, float]], str]] = {}
        sql_only_fallback_dir = Path("__pelagos_sql_session_only__")
        routes_to_preload = set(target_routes)
        for rc in list(routes_to_preload):
            comp = ROUTE_METADATA.get(rc, {}).get("competitor_route")
            if comp and comp in SUPPORTED_DIRECT_ROUTES:
                routes_to_preload.add(comp)
        for preload_rc in sorted(routes_to_preload):
            try:
                preloaded_series[preload_rc] = get_route_rate_observations(
                    db=db,
                    route_code=preload_rc,
                    storage_dir=sql_only_fallback_dir,
                )
            except Exception as exc:
                logger.debug("Preload route '%s' skipped (%s)", preload_rc, exc)

        for rc in target_routes:
            for h in target_horizons:
                try:
                    ds = _get_cached_dataset(db, bind_key, fp, rc, h, preloaded_series=preloaded_series)
                    for row in ds.rows:
                        regimes_seen.add(row.regime_id)
                        combined_rows.append(
                            {
                                "route_code": row.route_code,
                                "horizon_months": row.horizon_months,
                                "horizon_label": f"T+{row.horizon_months}",
                                "observation_date": row.observation_date.isoformat(),
                                "availability_date": row.availability_date.isoformat(),
                                "conservative_availability_cutoff": _to_iso_utc(row.conservative_availability_cutoff),
                                "observed_publication_time": _to_iso_utc(row.observed_publication_time),
                                "decision_date": row.decision_date.isoformat(),
                                "decision_timestamp": _to_iso_utc(row.decision_timestamp),
                                "target_date": row.target_date.isoformat(),
                                "target_period_start": row.target_period_start.isoformat(),
                                "horizon_mode": row.horizon_mode,
                                "regime_id": row.regime_id,
                                "is_labeled": bool(row.is_labeled),
                                "rate_lag_0": row.features.get("rate_lag_0"),
                                "target_value": row.target_value,
                                "target_change": row.target_change,
                                "feature_count": len(row.features),
                                "sample_features": {
                                    k: row.features.get(k)
                                    for k in ("rate_lag_0", "rate_lag_1", "rate_ma_3", "rate_vol_6", "competitor_rate_lag_0", "corridor_spread_lag_0")
                                    if k in row.features
                                },
                            }
                        )
                except ValueError as exc:
                    build_errors.append(f"{rc} T+{h}: {exc}")

        feature_inventory = generate_feature_inventory(db)
        fast_summary = _summarize_forecast_datasets_fast(db)
        with _CACHE_LOCK:
            _DATASET_ITEMS_CACHE[cache_key] = (
                time.monotonic(),
                {
                    "combined_rows": combined_rows,
                    "build_errors": build_errors,
                    "regimes_seen": regimes_seen,
                    "feature_inventory": feature_inventory,
                    "fast_summary": fast_summary,
                },
            )

    if not combined_rows and build_errors:
        return _make_envelope(
            domain="datasets",
            availability_status="unavailable",
            page=page,
            page_size=page_size,
            total_count=0,
            sort_by=chosen_sort,
            sort_order=sort_order,
            allowed_sort_fields=sort_fields,
            applied_filters={
                "route_code": route_code,
                "horizon": horizon,
                "regime_id": regime_id,
                "is_labeled": is_labeled,
                "date_from": date_from,
                "date_to": date_to,
                "search": search,
            },
            filter_options={"routes": list(SUPPORTED_DIRECT_ROUTES), "horizons": list(SUPPORTED_HORIZONS)},
            summary=fast_summary,
            integrity_notices=[
                "Persistence remains the active production baseline; experimental ML models are NOT promoted.",
            ],
            items=[],
            secondary_panels={"feature_inventory": feature_inventory, "benchmarks": benchmarks},
            reason="; ".join(build_errors),
        )

    # Apply filters over cached rows
    filtered = combined_rows
    if regime_id and regime_id != "all":
        filtered = [r for r in filtered if r["regime_id"] == regime_id.strip()]
    if is_labeled == "true":
        filtered = [r for r in filtered if r["is_labeled"]]
    elif is_labeled == "false":
        filtered = [r for r in filtered if not r["is_labeled"]]
    if d_from:
        from_iso = d_from.isoformat()
        filtered = [r for r in filtered if r["observation_date"] >= from_iso]
    if d_to:
        to_iso = d_to.isoformat()
        filtered = [r for r in filtered if r["observation_date"] <= to_iso]
    if search and search.strip():
        q = search.strip().lower()
        filtered = [
            r
            for r in filtered
            if q in r["route_code"].lower()
            or q in r["regime_id"].lower()
            or q in r["observation_date"].lower()
            or q in r["target_date"].lower()
        ]

    reverse = sort_order != "asc"
    filtered.sort(
        key=lambda r: (
            r[chosen_sort] is None,
            r[chosen_sort] if r[chosen_sort] is not None else "",
            r["observation_date"],
            r["route_code"],
            r["horizon_months"],
        ),
        reverse=reverse,
    )

    total_count = len(filtered)
    start = (page - 1) * page_size
    page_items = filtered[start : start + page_size]

    return _make_envelope(
        domain="datasets",
        availability_status=fast_summary["status"],
        page=page,
        page_size=page_size,
        total_count=total_count,
        sort_by=chosen_sort,
        sort_order=sort_order,
        allowed_sort_fields=sort_fields,
        applied_filters={
            "route_code": route_code,
            "horizon": horizon,
            "regime_id": regime_id,
            "is_labeled": is_labeled,
            "date_from": date_from,
            "date_to": date_to,
            "search": search,
        },
        filter_options={
            "routes": list(SUPPORTED_DIRECT_ROUTES),
            "horizons": list(SUPPORTED_HORIZONS),
            "regimes": sorted(regimes_seen),
        },
        summary=fast_summary,
        integrity_notices=[
            "Persistence (DEFAULT_BASELINE_MODEL = 'persistence') remains the active production forecasting baseline across T+1, T+3, and T+6.",
            "C3.2 and C3.3 ML/regime models are experimental research benchmarks ONLY — none outperformed Persistence statistically across common samples.",
            "Day 14 post-month-end (12:00 PM ET cutoff, 23:59:59 UTC decision, observed_publication_time = None) is a Pelagos conservative operational assumption.",
        ],
        items=page_items,
        secondary_panels={
            "feature_inventory": feature_inventory,
            "excluded_features": [
                {"feature_name": fname, "source": FEATURE_CATALOG[fname].source, "reason": FEATURE_CATALOG[fname].exclusion_reason}
                for fname in EXCLUDED_FEATURE_NAMES
                if fname in FEATURE_CATALOG
            ],
            "benchmarks": benchmarks,
        },
        reason="No dataset rows match the selected filters." if total_count == 0 else None,
    )


def _query_sources_explorer(
    db: Session,
    *,
    page: int,
    page_size: int,
    sort_by: Optional[str],
    sort_order: str,
    search: Optional[str],
    view: Optional[str] = "runs",
    run_status: Optional[str] = None,
    run_type: Optional[str] = None,
    source_name: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    active_view = (view or "runs").strip().lower()
    if active_view not in ("runs", "quarantine"):
        raise ValueError(f"Unsupported sources view='{active_view}'. Allowed values: runs, quarantine")

    total_runs = db.query(func.count(IngestionRun.id)).scalar() or 0
    total_quarantine = db.query(func.count(QuarantinedRecord.id)).scalar() or 0

    retrieval_summary = {
        "total_ingestion_runs": total_runs,
        "total_quarantined_records": total_quarantine,
        "bunker_latest_retrieval_utc": _to_iso_utc(db.query(func.max(BunkerPriceObservation.retrieval_time_utc)).scalar()),
        "freight_latest_ingestion_utc": _to_iso_utc(db.query(func.max(FreightObservation.ingestion_time)).scalar()),
        "vessels_latest_ingestion_utc": _to_iso_utc(db.query(func.max(VesselPosition.ingestion_time)).scalar()),
        "env_latest_retrieval_utc": _to_iso_utc(db.query(func.max(EnvironmentalObservation.retrieval_time_utc)).scalar()),
    }

    if active_view == "quarantine":
        sort_map = {
            "ingestion_time": QuarantinedRecord.ingestion_time,
            "source_name": QuarantinedRecord.source_name,
            "error_code": QuarantinedRecord.error_code,
            "id": QuarantinedRecord.id,
        }
        chosen_sort = _resolve_sort(sort_by, "ingestion_time", sort_map)
        query = db.query(QuarantinedRecord)
        if source_name and source_name != "all":
            query = query.filter(QuarantinedRecord.source_name.ilike(f"%{source_name.strip()}%"))
        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    QuarantinedRecord.source_name.ilike(term),
                    QuarantinedRecord.error_code.ilike(term),
                    QuarantinedRecord.error_details.ilike(term),
                    QuarantinedRecord.source_partition.ilike(term),
                )
            )
        total_count, rows = _paginate_query(query, sort_map[chosen_sort], sort_order, page, page_size)
        items = [
            {
                "id": r.id,
                "source_name": r.source_name,
                "source_partition": r.source_partition,
                "ingestion_run_id": r.ingestion_run_id,
                "mmsi": r.mmsi,
                "raw_record_id": r.raw_record_id,
                "error_code": r.error_code,
                "error_details": _sanitize_text(r.error_details),
                "raw_payload_preview": _sanitize_text((r.raw_payload or "")[:300]),
                "ingestion_time": _to_iso_utc(r.ingestion_time),
            }
            for r in rows
        ]
        sources_list = sorted(r[0] for r in db.query(func.distinct(QuarantinedRecord.source_name)).all() if r[0])
        return _make_envelope(
            domain="sources",
            availability_status="populated" if total_quarantine > 0 else "empty",
            page=page,
            page_size=page_size,
            total_count=total_count,
            sort_by=chosen_sort,
            sort_order=sort_order,
            allowed_sort_fields=list(sort_map.keys()),
            applied_filters={"view": active_view, "source_name": source_name, "search": search},
            filter_options={"views": ["runs", "quarantine"], "sources": sources_list},
            summary=retrieval_summary,
            integrity_notices=[
                "Credentials, API tokens, and environment secrets are strictly redacted and never exposed via platform APIs.",
                "Quarantined records ledger currently contains zero committed rows in SQLite." if total_quarantine == 0 else "Quarantined records isolate failed/rejected payloads.",
            ],
            items=items,
            reason="No quarantined records present in the database." if total_count == 0 else None,
        )

    # Default: IngestionRun audit ledger
    sort_map = {
        "completed_at": IngestionRun.completed_at,
        "started_at": IngestionRun.started_at,
        "id": IngestionRun.id,
        "source_name": IngestionRun.source_name,
        "status": IngestionRun.status,
        "records_read": IngestionRun.records_read,
        "accepted_count": IngestionRun.accepted_count,
        "duplicate_count": IngestionRun.duplicate_count,
        "quarantined_count": IngestionRun.quarantined_count,
        "failed_count": IngestionRun.failed_count,
    }
    chosen_sort = _resolve_sort(sort_by, "completed_at", sort_map)
    query = db.query(IngestionRun)
    if run_status and run_status != "all":
        query = query.filter(IngestionRun.status == run_status.strip())
    if run_type and run_type != "all":
        query = query.filter(IngestionRun.run_type == run_type.strip())
    if source_name and source_name != "all":
        query = query.filter(IngestionRun.source_name == source_name.strip())
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                IngestionRun.source_name.ilike(term),
                IngestionRun.source_partition.ilike(term),
                IngestionRun.run_type.ilike(term),
                IngestionRun.status.ilike(term),
                IngestionRun.error_details.ilike(term),
            )
        )

    total_count, rows = _paginate_query(query, sort_map[chosen_sort], sort_order, page, page_size)
    sources_list = sorted(r[0] for r in db.query(func.distinct(IngestionRun.source_name)).all() if r[0])
    statuses_list = sorted(r[0] for r in db.query(func.distinct(IngestionRun.status)).all() if r[0])
    run_types_list = sorted(r[0] for r in db.query(func.distinct(IngestionRun.run_type)).all() if r[0])

    items = [
        {
            "id": r.id,
            "run_type": r.run_type,
            "source_name": r.source_name,
            "source_partition": r.source_partition,
            "status": r.status,
            "raw_records_received": r.raw_records_received,
            "records_read": r.records_read,
            "accepted_count": r.accepted_count,
            "duplicate_count": r.duplicate_count,
            "quarantined_count": r.quarantined_count,
            "unparseable_count": r.unparseable_count,
            "failed_count": r.failed_count,
            "error_details": _sanitize_text(r.error_details),
            "started_at": _to_iso_utc(r.started_at),
            "completed_at": _to_iso_utc(r.completed_at),
        }
        for r in rows
    ]

    return _make_envelope(
        domain="sources",
        availability_status="populated" if total_runs > 0 else "empty",
        page=page,
        page_size=page_size,
        total_count=total_count,
        sort_by=chosen_sort,
        sort_order=sort_order,
        allowed_sort_fields=list(sort_map.keys()),
        applied_filters={
            "view": active_view,
            "run_status": run_status,
            "run_type": run_type,
            "source_name": source_name,
            "search": search,
        },
        filter_options={
            "views": ["runs", "quarantine"],
            "sources": sources_list,
            "statuses": statuses_list,
            "run_types": run_types_list,
        },
        summary=retrieval_summary,
        integrity_notices=[
            "Sensitive tokens, credentials, and environment variables are strictly excluded from all provenance responses.",
            f"Quarantined records table currently holds {total_quarantine} rows; failed/quarantined counters reflect logged pipeline executions.",
        ],
        items=items,
        reason="No ingestion run records match the selected filters." if total_count == 0 else None,
    )


_DOMAIN_HANDLERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "freight": _query_freight_explorer,
    "trade": _query_trade_explorer,
    "environmental": _query_environmental_explorer,
    "bunker": _query_bunker_explorer,
    "vessels": _query_vessels_explorer,
    "ports": _query_ports_explorer,
    "datasets": _query_datasets_explorer,
    "sources": _query_sources_explorer,
}


def query_explorer_domain(
    db: Session,
    domain: str,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    sort_by: Optional[str] = None,
    sort_order: str = "desc",
    search: Optional[str] = None,
    **filters: Any,
) -> Dict[str, Any]:
    """Execute read-only paginated, sorted, filtered query for a validated explorer domain."""
    normalized = (domain or "").strip().lower()
    handler = _DOMAIN_HANDLERS.get(normalized)
    if handler is None:
        raise KeyError(f"Unknown explorer domain '{domain}'. Valid domains: {', '.join(EXPLORER_DOMAINS)}")

    if page < 1:
        raise ValueError("page must be >= 1")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
    order = (sort_order or "desc").strip().lower()
    if order not in ("asc", "desc"):
        raise ValueError("sort_order must be 'asc' or 'desc'")

    try:
        return handler(
            db,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=order,
            search=search,
            **filters,
        )
    except SQLAlchemyError as exc:
        logger.warning("Explorer domain '%s' database error: %s", normalized, exc)
        return _make_envelope(
            domain=normalized,
            availability_status="unavailable",
            page=page,
            page_size=page_size,
            total_count=0,
            sort_by=sort_by or "id",
            sort_order=order,
            allowed_sort_fields=[],
            applied_filters=filters,
            filter_options={},
            summary={},
            integrity_notices=[],
            items=[],
            reason=f"Underlying storage for domain '{normalized}' is currently unavailable.",
        )
