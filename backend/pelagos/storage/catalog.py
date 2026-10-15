"""Parquet partition catalog and metadata extraction with DuckDB statistic fallback."""

from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import pyarrow.parquet as pq
import duckdb
from sqlalchemy.orm import Session

from pelagos.models import ParquetExportTask


def extract_parquet_partition_metadata(file_path: Path) -> Dict[str, Any]:
    """
    Extracts structural statistics and spatial bounding extents from Parquet footer.
    If column statistics are missing in the footer, falls back to a DuckDB row scan.
    Reports simple numeric bounds [-180.0, 180.0] for longitude extents.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Parquet artifact not found: {file_path}")

    meta = pq.read_metadata(file_path)
    num_rows = meta.num_rows
    num_row_groups = meta.num_row_groups
    file_size_bytes = file_path.stat().st_size

    min_lat = None
    max_lat = None
    min_lon = None
    max_lon = None
    min_obs_time = None
    max_obs_time = None
    stats_complete = True

    # Attempt to extract from PyArrow footer statistics across row groups
    for rg_idx in range(num_row_groups):
        rg = meta.row_group(rg_idx)
        for col_idx in range(rg.num_columns):
            col = rg.column(col_idx)
            name = col.path_in_schema
            stats = col.statistics

            if stats is None or not stats.has_min_max:
                stats_complete = False
                continue

            if name == "latitude":
                min_lat = min(min_lat, stats.min) if min_lat is not None else stats.min
                max_lat = max(max_lat, stats.max) if max_lat is not None else stats.max
            elif name == "longitude":
                min_lon = min(min_lon, stats.min) if min_lon is not None else stats.min
                max_lon = max(max_lon, stats.max) if max_lon is not None else stats.max
            elif name == "observation_time":
                min_obs_time = min(min_obs_time, stats.min) if min_obs_time is not None else stats.min
                max_obs_time = max(max_obs_time, stats.max) if max_obs_time is not None else stats.max

    # Fallback to DuckDB scan if statistics are missing or incomplete
    if not stats_complete or min_lat is None or min_lon is None:
        con = duckdb.connect(":memory:")
        try:
            res = con.execute(
                f"""
                SELECT 
                    min(latitude), max(latitude),
                    min(longitude), max(longitude),
                    min(observation_time), max(observation_time),
                    count(distinct mmsi)
                FROM '{file_path.as_posix()}'
                """
            ).fetchone()
            if res:
                min_lat = res[0]
                max_lat = res[1]
                min_lon = res[2]
                max_lon = res[3]
                min_obs_time = res[4]
                max_obs_time = res[5]
                distinct_mmsi = res[6]
        finally:
            con.close()
    else:
        # Compute distinct MMSI via DuckDB if not in statistics
        con = duckdb.connect(":memory:")
        try:
            distinct_mmsi = con.execute(
                f"SELECT count(distinct mmsi) FROM '{file_path.as_posix()}'"
            ).fetchone()[0]
        finally:
            con.close()

    return {
        "file_path": str(file_path.resolve()),
        "record_count": num_rows,
        "file_size_bytes": file_size_bytes,
        "row_groups_count": num_row_groups,
        "bounds": {
            "min_latitude": float(min_lat) if min_lat is not None else None,
            "max_latitude": float(max_lat) if max_lat is not None else None,
            "min_longitude": float(min_lon) if min_lon is not None else None,
            "max_longitude": float(max_lon) if max_lon is not None else None,
        },
        "time_range": {
            "start": min_obs_time.isoformat() if hasattr(min_obs_time, "isoformat") else str(min_obs_time),
            "end": max_obs_time.isoformat() if hasattr(max_obs_time, "isoformat") else str(max_obs_time),
        },
        "distinct_vessels": distinct_mmsi,
    }


def get_published_partition_catalog(db: Session) -> List[Dict[str, Any]]:
    """
    Returns catalog metadata for all published Parquet partitions registered in SQL.
    """
    published_tasks = (
        db.query(ParquetExportTask)
        .filter(
            ParquetExportTask.publication_status == "published",
            ParquetExportTask.published_artifact_path.isnot(None)
        )
        .order_by(ParquetExportTask.partition_date.asc())
        .all()
    )

    catalog_entries = []
    for task in published_tasks:
        fpath = Path(task.published_artifact_path)
        if not fpath.exists():
            continue

        try:
            meta = extract_parquet_partition_metadata(fpath)
            meta["partition_date"] = task.partition_date
            meta["published_generation"] = task.published_generation
            meta["published_sha256"] = task.published_sha256
            catalog_entries.append(meta)
        except Exception as exc:
            continue

    return catalog_entries
