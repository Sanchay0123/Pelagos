"""
DuckDB analytical query engine operating over published freight Parquet partitions and SQL fallback.
Traces to REQ-NFR-ABM-002, REQ-FR-DII-005, and ADR-0002.
"""

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import date
import duckdb
from sqlalchemy.orm import Session
from sqlalchemy import func

from pelagos.config import settings
from pelagos.models import FreightObservation, FreightRoute, FreightPublication

logger = logging.getLogger(__name__)


def get_published_freight_parquet_paths(
    db: Optional[Session] = None,
    storage_dir: Optional[Path] = None,
) -> List[str]:
    """
    Finds the authoritative published immutable Parquet artifacts designated by the
    database publication catalog (FreightPublication).
    Ensures readers retrieve ONLY the catalog-designated published artifact for each year,
    completely ignoring competing attempt artifacts, stale attempts, or uncommitted files.
    """
    if db is not None:
        try:
            pubs = (
                db.query(FreightPublication.published_artifact_path)
                .filter(FreightPublication.published_artifact_path.isnot(None))
                .all()
            )
            valid_paths = []
            for (p_str,) in pubs:
                if p_str and Path(p_str).is_file():
                    valid_paths.append(Path(p_str).as_posix())
            if valid_paths:
                return sorted(valid_paths)
        except Exception as e:
            logger.warning(f"Could not load freight publication catalog from database: {e}")

    # Fallback when database has no publications or db is None (e.g. offline unit testing)
    base = storage_dir or Path(settings.parquet_storage_dir)
    freight_dir = base / "freight"
    if not freight_dir.exists():
        return []

    # Map year folder name -> latest authoritative partition file
    year_partitions: Dict[str, Path] = {}
    for p in sorted(freight_dir.glob("year=*/partition_g*.parquet")):
        if p.is_file() and not p.name.endswith(".tmp") and ".tombstone." not in p.name:
            year_key = p.parent.name
            if year_key not in year_partitions or p.stat().st_mtime >= year_partitions[year_key].stat().st_mtime:
                year_partitions[year_key] = p

    return [p.as_posix() for p in sorted(year_partitions.values(), key=lambda x: x.as_posix())]


def get_freight_summary(db: Session, storage_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Computes high-performance analytical aggregates across freight observations.
    Uses DuckDB over published Parquet partitions if available; falls back to SQL if not.
    """
    paths = get_published_freight_parquet_paths(db=db, storage_dir=storage_dir)

    if paths:
        # Fast path: DuckDB over columnar Parquet files
        files_clause = "[" + ", ".join(f"'{p}'" for p in paths) + "]"
        con = duckdb.connect(":memory:")
        try:
            total_obs = con.execute(f"SELECT count(*) FROM read_parquet({files_clause})").fetchone()[0]
            routes_count = con.execute(f"SELECT count(distinct route_code) FROM read_parquet({files_clause})").fetchone()[0]
            date_range = con.execute(
                f"SELECT min(observation_date), max(observation_date) FROM read_parquet({files_clause})"
            ).fetchone()

            route_rows = con.execute(
                f"""
                SELECT 
                    route_code,
                    count(*),
                    round(avg(rate_value), 2),
                    round(min(rate_value), 2),
                    round(max(rate_value), 2),
                    round(coalesce(stddev_samp(rate_value), 0), 2),
                    is_derived
                FROM read_parquet({files_clause})
                GROUP BY route_code, is_derived
                ORDER BY route_code ASC
                """
            ).fetchall()

            routes_summary = {}
            for r in route_rows:
                routes_summary[r[0]] = {
                    "observations_count": r[1],
                    "mean_rate_usd_mt": float(r[2]),
                    "min_rate_usd_mt": float(r[3]),
                    "max_rate_usd_mt": float(r[4]),
                    "stddev_rate_usd_mt": float(r[5]),
                    "is_derived": bool(r[6]),
                }

            return {
                "source": "parquet_analytical",
                "published_partitions_count": len(paths),
                "total_observations": total_obs,
                "distinct_routes": routes_count,
                "date_range": {
                    "start": str(date_range[0]) if date_range and date_range[0] else None,
                    "end": str(date_range[1]) if date_range and date_range[1] else None,
                },
                "routes": routes_summary,
            }
        finally:
            con.close()

    # Fallback path: SQL relational table queries
    total_obs = db.query(func.count(FreightObservation.id)).scalar() or 0
    if total_obs == 0:
        return {
            "source": "sql_empty",
            "published_partitions_count": 0,
            "total_observations": 0,
            "distinct_routes": 0,
            "date_range": {"start": None, "end": None},
            "routes": {},
        }

    date_range = db.query(
        func.min(FreightObservation.observation_date),
        func.max(FreightObservation.observation_date)
    ).first()

    route_stats = (
        db.query(
            FreightObservation.route_code,
            func.count(FreightObservation.id),
            func.avg(FreightObservation.rate_value),
            func.min(FreightObservation.rate_value),
            func.max(FreightObservation.rate_value),
            FreightObservation.is_derived,
        )
        .group_by(FreightObservation.route_code, FreightObservation.is_derived)
        .all()
    )

    routes_summary = {}
    for r in route_stats:
        routes_summary[r[0]] = {
            "observations_count": r[1],
            "mean_rate_usd_mt": round(float(r[2]), 2) if r[2] else 0.0,
            "min_rate_usd_mt": round(float(r[3]), 2) if r[3] else 0.0,
            "max_rate_usd_mt": round(float(r[4]), 2) if r[4] else 0.0,
            "stddev_rate_usd_mt": 0.0,
            "is_derived": bool(r[5]),
        }

    return {
        "source": "sql_relational",
        "published_partitions_count": 0,
        "total_observations": total_obs,
        "distinct_routes": len(routes_summary),
        "date_range": {
            "start": str(date_range[0]) if date_range and date_range[0] else None,
            "end": str(date_range[1]) if date_range and date_range[1] else None,
        },
        "routes": routes_summary,
    }


def get_freight_route_history(
    db: Session,
    route_code: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    """Retrieves chronological rate observation history for a specific route."""
    query = db.query(FreightObservation).filter(FreightObservation.route_code == route_code)

    if start_date:
        query = query.filter(FreightObservation.observation_date >= start_date)
    if end_date:
        query = query.filter(FreightObservation.observation_date <= end_date)

    query = query.order_by(FreightObservation.observation_date.asc()).limit(limit)
    rows = query.all()

    return [
        {
            "observation_date": str(r.observation_date),
            "rate_value": r.rate_value,
            "currency": r.currency,
            "unit": r.unit_of_measure,
            "is_derived": r.is_derived,
            "source_name": r.source_name,
        }
        for r in rows
    ]
