"""DuckDB analytical query engine operating over published Parquet partitions."""

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
import duckdb
from sqlalchemy.orm import Session

from pelagos.models import ParquetExportTask

logger = logging.getLogger(__name__)


def get_published_parquet_paths(db: Session) -> List[str]:
    """
    Reader query: extracts the exact artifact paths for all currently published partitions.
    Completely isolated from in-flight worker attempt files.
    """
    tasks = (
        db.query(ParquetExportTask.published_artifact_path)
        .filter(
            ParquetExportTask.publication_status == "published",
            ParquetExportTask.published_artifact_path.isnot(None)
        )
        .all()
    )
    valid_paths = []
    for (p,) in tasks:
        if p and Path(p).exists():
            valid_paths.append(Path(p).as_posix())
    return valid_paths


def get_analytics_summary(db: Session) -> Dict[str, Any]:
    """
    Executes analytical aggregations over all published Parquet partitions using DuckDB:
    - Total record count and distinct vessel count
    - Temporal bounds (start / end)
    - Kinematic speed distribution (p50, p75, p90, p99)
    - Vessel type classification breakdown
    """
    paths = get_published_parquet_paths(db)
    if not paths:
        return {
            "source": "parquet_empty",
            "published_partitions_count": 0,
            "total_observations": 0,
            "distinct_vessels": 0,
            "time_range": {"start": None, "end": None},
            "speed_percentiles": {},
            "vessel_type_distribution": {},
        }

    # Format list of files for DuckDB scan
    files_clause = "[" + ", ".join(f"'{p}'" for p in paths) + "]"

    con = duckdb.connect(":memory:")
    try:
        # 1. Core aggregations
        core = con.execute(
            f"""
            SELECT 
                count(*),
                count(distinct mmsi),
                min(observation_time),
                max(observation_time),
                quantile_cont(speed_knots, 0.50),
                quantile_cont(speed_knots, 0.75),
                quantile_cont(speed_knots, 0.90),
                quantile_cont(speed_knots, 0.99)
            FROM read_parquet({files_clause})
            """
        ).fetchone()

        # 2. Vessel type breakdown
        type_rows = con.execute(
            f"""
            SELECT vessel_type, count(*)
            FROM read_parquet({files_clause})
            GROUP BY vessel_type
            ORDER BY count(*) DESC
            LIMIT 20
            """
        ).fetchall()

        vessel_type_dist = {row[0]: row[1] for row in type_rows}

        return {
            "source": "parquet_analytical",
            "published_partitions_count": len(paths),
            "total_observations": core[0] if core else 0,
            "distinct_vessels": core[1] if core else 0,
            "time_range": {
                "start": core[2].isoformat() if core and hasattr(core[2], "isoformat") else str(core[2]) if core else None,
                "end": core[3].isoformat() if core and hasattr(core[3], "isoformat") else str(core[3]) if core else None,
            },
            "speed_percentiles": {
                "p50_knots": round(float(core[4]), 2) if core and core[4] is not None else None,
                "p75_knots": round(float(core[5]), 2) if core and core[5] is not None else None,
                "p90_knots": round(float(core[6]), 2) if core and core[6] is not None else None,
                "p99_knots": round(float(core[7]), 2) if core and core[7] is not None else None,
            },
            "vessel_type_distribution": vessel_type_dist,
        }
    finally:
        con.close()


def get_vessel_track_from_parquet(db: Session, mmsi: str) -> List[Dict[str, Any]]:
    """
    Extracts time-ordered trajectory points for a given MMSI from published Parquet files.
    """
    paths = get_published_parquet_paths(db)
    if not paths:
        return []

    files_clause = "[" + ", ".join(f"'{p}'" for p in paths) + "]"
    con = duckdb.connect(":memory:")
    try:
        rows = con.execute(
            f"""
            SELECT 
                observation_time,
                latitude,
                longitude,
                speed_knots,
                course_degrees,
                heading_degrees,
                vessel_name
            FROM read_parquet({files_clause})
            WHERE mmsi = ?
            ORDER BY observation_time ASC
            """,
            [str(mmsi)]
        ).fetchall()

        track = []
        for r in rows:
            track.append({
                "timestamp": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                "latitude": float(r[1]),
                "longitude": float(r[2]),
                "speed_knots": float(r[3]) if r[3] is not None else None,
                "course_degrees": float(r[4]) if r[4] is not None else None,
                "heading_degrees": float(r[5]) if r[5] is not None else None,
                "vessel_name": r[6],
            })
        return track
    finally:
        con.close()
