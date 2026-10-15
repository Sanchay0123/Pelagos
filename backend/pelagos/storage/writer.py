"""Atomic, verified Parquet partition writer with streaming SHA-256 integrity."""

import os
import hashlib
from pathlib import Path
from typing import List, Tuple, Optional
from datetime import datetime
import pyarrow.parquet as pq

from pelagos.models import VesselPosition
from pelagos.storage.schema import records_to_pyarrow_table, TELEMETRY_PARQUET_SCHEMA


def compute_file_sha256(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Computes hex-encoded SHA-256 digest of a file using streaming chunks."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_partition_directory(base_dir: Path, partition_date_str: str) -> Path:
    """
    Builds the standardized Hive-style partition directory:
    telemetry/year=YYYY/month=MM/date=YYYY-MM-DD
    """
    dt = datetime.strptime(partition_date_str, "%Y-%m-%d").date()
    pdir = (
        base_dir
        / "telemetry"
        / f"year={dt.year:04d}"
        / f"month={dt.month:02d}"
        / f"date={partition_date_str}"
    )
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


def write_partition_parquet(
    records: List[VesselPosition],
    base_dir: Path,
    partition_date_str: str,
    generation: int,
    attempt_uuid: str,
    worker_id: str,
) -> Tuple[Path, int, int, str]:
    """
    Writes a daily partition of VesselPosition records to an immutable Parquet artifact:
    1. Writes to a worker-private temporary staging file: partition_g{gen}_a{uuid}.tmp.{worker_id}
    2. Verifies readability, footer metadata, and row count matching input records.
    3. Computes exact byte size and streaming SHA-256 digest.
    4. Atomically renames staging file to final attempt artifact: partition_g{gen}_a{uuid}.parquet
    
    Returns (published_path, record_count, file_size_bytes, content_sha256).
    """
    pdir = get_partition_directory(base_dir, partition_date_str)
    
    # 1. Staging paths
    tmp_filename = f"partition_g{generation}_a{attempt_uuid}.tmp.{worker_id}"
    final_filename = f"partition_g{generation}_a{attempt_uuid}.parquet"
    
    tmp_path = pdir / tmp_filename
    final_path = pdir / final_filename

    # Convert records to PyArrow Table
    table = records_to_pyarrow_table(records)
    
    try:
        # Write Parquet with snappy compression
        pq.write_table(
            table,
            tmp_path,
            compression="SNAPPY",
            write_statistics=True,
        )

        # 2. Verification of staging artifact
        metadata = pq.read_metadata(tmp_path)
        if metadata.num_rows != len(records):
            raise ValueError(
                f"Parquet row count mismatch: expected {len(records)}, got {metadata.num_rows}"
            )

        file_size_bytes = tmp_path.stat().st_size
        content_sha256 = compute_file_sha256(tmp_path)

        # 3. Atomic rename to immutable attempt path
        os.replace(tmp_path, final_path)

        return final_path, len(records), file_size_bytes, content_sha256

    except Exception:
        # Clean up temporary staging file on failure
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
