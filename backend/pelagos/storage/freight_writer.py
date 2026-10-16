"""
Atomic Parquet partition writer for historical freight observations.
Follows the verified ADR-0002 pattern: staging to .tmp.{worker_id}, SHA-256 verification,
and atomic rename to immutable partition files.
"""

import os
import re
import logging
import hashlib
import uuid
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional, Union
import pyarrow.parquet as pq
from sqlalchemy import or_, and_
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

from pelagos.models import FreightObservation
from pelagos.storage.schema import freight_records_to_pyarrow_table, FREIGHT_PARQUET_SCHEMA
from pelagos.storage.writer import compute_file_sha256


def get_freight_partition_directory(base_dir: Path, year: int) -> Path:
    """Builds standardized partition directory: freight/year=YYYY"""
    pdir = base_dir / "freight" / f"year={year:04d}"
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


def write_freight_partition_parquet(
    records: List[FreightObservation],
    base_dir: Path,
    year: int,
    generation: int = 1,
    attempt_uuid: Optional[str] = None,
    worker_id: Optional[str] = None,
) -> Tuple[Path, int, int, str]:
    """
    Writes a yearly partition of FreightObservation records to an immutable Parquet artifact:
    1. Generates unique attempt UUID (8-char hex) and worker ID if not supplied to prevent clobbering.
    2. Writes to worker-private temporary staging file: partition_g{gen}_a{uuid}.tmp.{worker_id}
    3. Verifies readability and row count matching input records.
    4. Computes exact byte size and streaming SHA-256 digest.
    5. Atomically renames staging file to final attempt artifact: partition_g{gen}_a{uuid}.parquet

    Design Rationale & Concurrency Guarantees:
    - Low-cadence macro dataset: Freight rates are assessed monthly (low-frequency tabular data)
      unlike high-velocity streaming AIS telemetry. Synchronous export avoids heavy outbox daemon
      polling overhead while maintaining strict publication safety.
    - Staging safety: Private worker temporary paths prevent staging collisions across parallel workers.
    - Immutability: Each run publishes a uniquely identified attempt artifact (never overwrites in-place).
    - Reader deduplication: Analytical readers group by year and select the latest authoritative partition.

    Returns (published_path, record_count, file_size_bytes, content_sha256).
    """
    attempt_token = attempt_uuid or uuid.uuid4().hex[:8]
    worker_token = worker_id or f"freight-worker-{uuid.uuid4().hex[:6]}"

    pdir = get_freight_partition_directory(base_dir, year)

    tmp_filename = f"partition_g{generation}_a{attempt_token}.tmp.{worker_token}"
    final_filename = f"partition_g{generation}_a{attempt_token}.parquet"

    tmp_path = pdir / tmp_filename
    final_path = pdir / final_filename

    table = freight_records_to_pyarrow_table(records)

    try:
        pq.write_table(
            table,
            tmp_path,
            compression="SNAPPY",
            write_statistics=True,
        )

        metadata = pq.read_metadata(tmp_path)
        if metadata.num_rows != len(records):
            raise ValueError(
                f"Parquet row count mismatch: expected {len(records)}, got {metadata.num_rows}"
            )

        file_size_bytes = tmp_path.stat().st_size
        content_sha256 = compute_file_sha256(tmp_path)

        os.replace(tmp_path, final_path)
        return final_path, len(records), file_size_bytes, content_sha256

    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def commit_freight_publication(
    db,
    year: int,
    claimed_generation: Optional[int] = None,
    published_path: Union[Path, str] = "",
    record_count: int = 0,
    file_size_bytes: int = 0,
    content_sha256: str = "",
    snapshot_watermark_id: Optional[int] = None,
    expected_generation: Optional[int] = None,
) -> bool:
    """
    Atomically commits publication metadata into the database catalog using a database-enforced
    conditional compare-and-swap (CAS) update and safe initial publication handling.

    Guarantees:
    1. Atomic Concurrency Control: Uses conditional SQL UPDATE with WHERE clause verifying
       (year == :year AND generation == :expected_gen AND (snapshot_watermark < :incoming_watermark OR ...)).
       Publication success strictly requires rows_updated == 1.
    2. Stale Snapshot Protection: An older SQL snapshot (with lower watermark) cannot match the CAS
       condition, even if it attempts publication after a newer snapshot committed.
    3. Initial Publication Safety: When no row exists for the year partition, an atomic INSERT is
       attempted. If a concurrent exporter inserts first, the resulting UNIQUE constraint IntegrityError
       is caught, rolled back, and the runner safely falls through to the CAS update path.
    4. Deterministic Generation Alignment: The physical attempt artifact on disk is aligned to match
       the authoritative generation allocated by the database.
    5. Reader Isolation: Readers query the publication catalog directly and only observe committed artifacts.
    """
    from datetime import datetime, timezone
    from pelagos.models import FreightPublication

    now_utc = datetime.now(timezone.utc)
    resolved_path = Path(published_path).resolve()

    # Expire session identity map to ensure live read from database
    try:
        db.expire_all()
    except Exception:
        pass

    # Resolve expected generation baseline for CAS:
    # If not explicitly provided, infer from claimed_generation or current DB catalog
    if expected_generation is None:
        if claimed_generation is not None and claimed_generation > 1:
            expected_generation = claimed_generation - 1
        else:
            try:
                row = db.query(FreightPublication.generation).filter_by(year=year).first()
                expected_generation = row[0] if row else None
            except Exception:
                expected_generation = None

    # -----------------------------------------------------------------------
    # Case 1: Initial publication for this year (no catalog row exists yet)
    # -----------------------------------------------------------------------
    if expected_generation is None or expected_generation == 0:
        new_gen = 1
        # Align artifact filename to generation 1 if needed
        if resolved_path.exists():
            curr_name = resolved_path.name
            if f"partition_g{new_gen}_" not in curr_name and re.search(r"partition_g\d+_", curr_name):
                new_name = re.sub(r"partition_g\d+_", f"partition_g{new_gen}_", curr_name)
                new_path = resolved_path.parent / new_name
                if new_path != resolved_path:
                    os.replace(resolved_path, new_path)
                    resolved_path = new_path

        pub_path_str = str(resolved_path.resolve())

        new_pub = FreightPublication(
            year=year,
            generation=new_gen,
            published_artifact_path=pub_path_str,
            published_sha256=content_sha256,
            record_count=record_count,
            file_size_bytes=file_size_bytes,
            snapshot_watermark_id=snapshot_watermark_id,
            published_at=now_utc,
        )

        try:
            with db.begin_nested():
                db.add(new_pub)
                db.flush()
            db.commit()
            return True
        except IntegrityError:
            # Initial publication race: another session inserted year partition concurrently!
            db.rollback()
            # Fall through to CAS update path below using current database generation
            try:
                row = db.query(FreightPublication.generation).filter_by(year=year).first()
                if not row:
                    return False
                expected_generation = row[0]
            except Exception:
                return False
        except Exception as exc:
            db.rollback()
            logger.error(f"Failed initial publication insert for year {year}: {exc}")
            return False

    # -----------------------------------------------------------------------
    # Case 2: Atomic Conditional Compare-and-Swap (CAS) Update
    # -----------------------------------------------------------------------
    new_gen = expected_generation + 1

    # Rename physical file to reflect authoritative generation if candidate generation differed
    if resolved_path.exists():
        curr_name = resolved_path.name
        if f"partition_g{new_gen}_" not in curr_name and re.search(r"partition_g\d+_", curr_name):
            new_name = re.sub(r"partition_g\d+_", f"partition_g{new_gen}_", curr_name)
            new_path = resolved_path.parent / new_name
            if new_path != resolved_path:
                os.replace(resolved_path, new_path)
                resolved_path = new_path

    pub_path_str = str(resolved_path.resolve())

    # Build atomic conditional filters
    cas_filters = [
        FreightPublication.year == year,
        FreightPublication.generation == expected_generation,
    ]

    if snapshot_watermark_id is not None:
        freshness_filter = or_(
            FreightPublication.snapshot_watermark_id.is_(None),
            FreightPublication.snapshot_watermark_id < snapshot_watermark_id,
            and_(
                FreightPublication.snapshot_watermark_id == snapshot_watermark_id,
                FreightPublication.record_count < record_count,
            ),
        )
        cas_filters.append(freshness_filter)
    elif claimed_generation is not None:
        cas_filters.append(FreightPublication.generation < claimed_generation)

    try:
        rows_updated = (
            db.query(FreightPublication)
            .filter(*cas_filters)
            .update(
                {
                    "generation": new_gen,
                    "published_artifact_path": pub_path_str,
                    "published_sha256": content_sha256,
                    "record_count": record_count,
                    "file_size_bytes": file_size_bytes,
                    "snapshot_watermark_id": snapshot_watermark_id,
                    "published_at": now_utc,
                },
                synchronize_session=False,
            )
        )

        if rows_updated == 1:
            db.commit()
            return True
        else:
            # CAS failed: either expected_generation differed (concurrent publisher won)
            # or snapshot_watermark was stale (older snapshot rejected)
            db.rollback()
            return False
    except Exception as e:
        db.rollback()
        logger.error(f"CAS publication update failed for year {year}: {e}")
        return False

