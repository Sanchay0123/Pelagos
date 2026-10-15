"""SQL-backed transactional outbox, worker leasing, and cleanup synchronization."""

import os
import uuid
import logging
from datetime import datetime, timezone, timedelta, time
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
import pyarrow.parquet as pq

from pelagos.models import ParquetExportTask, VesselPosition
from pelagos.storage.writer import compute_file_sha256, write_partition_parquet, get_partition_directory
from pelagos.storage.schema import TELEMETRY_PARQUET_SCHEMA

logger = logging.getLogger(__name__)


class CleanupLockLostError(Exception):
    """Raised when a cleanup process loses its exclusive partition lock during execution."""


# ---------------------------------------------------------------------------
# Partition Record Extraction (Portable UTC Half-Open Intervals)
# ---------------------------------------------------------------------------

def get_partition_records(db: Session, partition_date_str: str) -> List[VesselPosition]:
    """
    Extracts all authoritative observations for a daily partition using
    half-open UTC timestamp intervals [start_dt, end_dt).
    Eliminates dialect-dependent date() SQL functions and leverages
    the B-Tree index on observation_time.
    """
    pdate = datetime.strptime(partition_date_str, "%Y-%m-%d").date()
    start_dt = datetime.combine(pdate, time.min).replace(tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)

    return (
        db.query(VesselPosition)
        .filter(
            VesselPosition.observation_time >= start_dt,
            VesselPosition.observation_time < end_dt,
        )
        .order_by(
            VesselPosition.observation_time.asc(),
            VesselPosition.id.asc()
        )
        .all()
    )


# ---------------------------------------------------------------------------
# Outbox Task Ingestion Staging & Reactivation
# ---------------------------------------------------------------------------

def stage_or_reactivate_outbox_task(db: Session, partition_date_str: str) -> ParquetExportTask:
    """
    Stages a new outbox task or reactivates an existing task for a daily partition.
    Must be called within the same database transaction as the ingested observations.
    Bumps target generation to invalidate stale workers while preserving the previous
    published artifact for zero-downtime analytical reading.
    """
    task = db.query(ParquetExportTask).filter(
        ParquetExportTask.partition_date == partition_date_str
    ).first()

    now_utc = datetime.now(timezone.utc)

    if task is None:
        task = ParquetExportTask(
            partition_date=partition_date_str,
            generation=1,
            completed_generation=None,
            export_status="pending",
            publication_status="none",
            published_artifact_path=None,
            published_generation=None,
            published_sha256=None,
            published_record_count=None,
            published_file_size=None,
            lease_worker_id=None,
            lease_expires_at=None,
            active_attempt_uuid=None,
            cleanup_locked_by=None,
            cleanup_locked_expires_at=None,
            attempt_count=0,
            priority=0,
            error_details=None,
            created_at=now_utc,
            updated_at=now_utc,
        )
        db.add(task)
    else:
        # Reactivate existing task for new generation
        task.generation += 1
        task.export_status = "pending"
        task.lease_worker_id = None
        task.lease_expires_at = None
        task.active_attempt_uuid = None
        task.error_details = None
        task.updated_at = now_utc
        # Note: published_artifact_path and publication_status remain 'published'
        # so readers can continue reading the last verified snapshot.

    db.flush()
    return task


# ---------------------------------------------------------------------------
# Worker Task Claiming and Completion Fencing
# ---------------------------------------------------------------------------

def claim_next_export_task(
    db: Session,
    worker_id: str,
    lease_seconds: int = 300
) -> Optional[Tuple[ParquetExportTask, str]]:
    """
    Atomically claims the highest-priority eligible outbox task:
    - Eligible: export_status IN ('pending', 'failed') OR (export_status == 'in_progress' AND expired)
    - Mutex: cleanup_locked_by IS NULL OR cleanup_locked_expires_at < now_utc
    - Generates full RFC 4122 v4 UUID and binds it atomically.
    Returns (claimed_task, attempt_uuid) or None.
    """
    now_utc = datetime.now(timezone.utc)
    new_lease_expiry = now_utc + timedelta(seconds=lease_seconds)

    candidate = (
        db.query(ParquetExportTask.partition_date, ParquetExportTask.generation)
        .filter(
            or_(
                ParquetExportTask.export_status.in_(["pending", "failed"]),
                and_(
                    ParquetExportTask.export_status == "in_progress",
                    ParquetExportTask.lease_expires_at < now_utc
                )
            ),
            or_(
                ParquetExportTask.cleanup_locked_by.is_(None),
                ParquetExportTask.cleanup_locked_expires_at < now_utc
            )
        )
        .order_by(
            ParquetExportTask.priority.desc(),
            ParquetExportTask.partition_date.asc()
        )
        .first()
    )

    if not candidate:
        return None

    attempt_uuid = str(uuid.uuid4())

    rows = (
        db.query(ParquetExportTask)
        .filter(
            ParquetExportTask.partition_date == candidate.partition_date,
            ParquetExportTask.generation == candidate.generation,
            or_(
                ParquetExportTask.export_status.in_(["pending", "failed"]),
                and_(
                    ParquetExportTask.export_status == "in_progress",
                    ParquetExportTask.lease_expires_at < now_utc
                )
            ),
            or_(
                ParquetExportTask.cleanup_locked_by.is_(None),
                ParquetExportTask.cleanup_locked_expires_at < now_utc
            )
        )
        .update(
            {
                "export_status": "in_progress",
                "lease_worker_id": worker_id,
                "lease_expires_at": new_lease_expiry,
                "active_attempt_uuid": attempt_uuid,
                "attempt_count": ParquetExportTask.attempt_count + 1,
                "updated_at": now_utc,
                "error_details": None,
            },
            synchronize_session=False
        )
    )
    db.commit()

    if rows == 1:
        task = db.query(ParquetExportTask).filter_by(
            partition_date=candidate.partition_date
        ).one()
        return task, attempt_uuid

    return None


def commit_export_completion(
    db: Session,
    partition_date: str,
    claimed_generation: int,
    worker_id: str,
    attempt_uuid: str,
    published_path: str,
    record_count: int,
    file_size_bytes: int,
    content_sha256: str,
) -> bool:
    """
    Atomic worker completion CAS: commits outbox export status and publication metadata.
    Guarantees that obsolete workers, expired leases, or preempted generations affect 0 rows.
    """
    now_utc = datetime.now(timezone.utc)

    rows = (
        db.query(ParquetExportTask)
        .filter(
            ParquetExportTask.partition_date == partition_date,
            ParquetExportTask.generation == claimed_generation,
            ParquetExportTask.lease_worker_id == worker_id,
            ParquetExportTask.active_attempt_uuid == attempt_uuid,
            ParquetExportTask.lease_expires_at >= now_utc,
            or_(
                ParquetExportTask.cleanup_locked_by.is_(None),
                ParquetExportTask.cleanup_locked_expires_at < now_utc
            )
        )
        .update(
            {
                "export_status": "completed",
                "completed_generation": claimed_generation,
                "active_attempt_uuid": None,
                "lease_worker_id": None,
                "lease_expires_at": None,
                "completed_at": now_utc,
                "updated_at": now_utc,
                "error_details": None,

                "publication_status": "published",
                "published_artifact_path": published_path,
                "published_generation": claimed_generation,
                "published_sha256": content_sha256,
                "published_record_count": record_count,
                "published_file_size": file_size_bytes,
            },
            synchronize_session=False
        )
    )
    db.commit()
    return rows == 1


# ---------------------------------------------------------------------------
# Recovery CAS Transitions (Guarded by Generation, Attempt UUID, & Cleanup Lock)
# ---------------------------------------------------------------------------

def invalidate_publication_state(
    db: Session,
    partition_date: str,
    expected_pub_gen: int,
    expected_pub_path: str,
    expected_pub_sha: str,
    recovery_owner_id: str,
    reason: str
) -> bool:
    """
    Atomic publication invalidation CAS: removes missing/corrupt artifact from reader view.
    Does NOT touch active worker lease fields.
    Affects 0 rows and aborts if publication state changed or lock was lost.
    """
    now_utc = datetime.now(timezone.utc)

    rows = (
        db.query(ParquetExportTask)
        .filter(
            ParquetExportTask.partition_date == partition_date,
            ParquetExportTask.publication_status == "published",
            ParquetExportTask.published_generation == expected_pub_gen,
            ParquetExportTask.published_artifact_path == expected_pub_path,
            ParquetExportTask.published_sha256 == expected_pub_sha,
            ParquetExportTask.cleanup_locked_by == recovery_owner_id,
            ParquetExportTask.cleanup_locked_expires_at >= now_utc
        )
        .update(
            {
                "publication_status": "invalidated",
                "published_artifact_path": None,
                "published_generation": None,
                "published_sha256": None,
                "published_record_count": None,
                "published_file_size": None,
                "error_details": reason,
                "updated_at": now_utc,
            },
            synchronize_session=False
        )
    )
    db.commit()
    return rows == 1


def fail_expired_worker_task(
    db: Session,
    partition_date: str,
    expected_generation: int,
    expected_attempt_uuid: Optional[str],
    recovery_owner_id: str,
    reason: str
) -> bool:
    """
    Recovery CAS guardrail: transitions export task to failed ONLY when worker lease is absent
    or expired, matching expected generation and observed attempt UUID.
    Never interrupts an active worker holding a valid lease.
    """
    now_utc = datetime.now(timezone.utc)

    filters = [
        ParquetExportTask.partition_date == partition_date,
        ParquetExportTask.generation == expected_generation,
        or_(
            ParquetExportTask.lease_expires_at.is_(None),
            ParquetExportTask.lease_expires_at < now_utc
        ),
        ParquetExportTask.cleanup_locked_by == recovery_owner_id,
        ParquetExportTask.cleanup_locked_expires_at >= now_utc,
    ]

    if expected_attempt_uuid is not None:
        filters.append(ParquetExportTask.active_attempt_uuid == expected_attempt_uuid)
    else:
        filters.append(ParquetExportTask.active_attempt_uuid.is_(None))

    rows = (
        db.query(ParquetExportTask)
        .filter(*filters)
        .update(
            {
                "export_status": "failed",
                "lease_worker_id": None,
                "lease_expires_at": None,
                "active_attempt_uuid": None,
                "error_details": reason,
                "updated_at": now_utc,
            },
            synchronize_session=False
        )
    )
    db.commit()
    return rows == 1


# ---------------------------------------------------------------------------
# Cleanup Lock Ownership & Safe Destructive Fencing
# ---------------------------------------------------------------------------

def acquire_cleanup_lock(
    db: Session,
    partition_date: str,
    cleanup_id: str,
    lock_ttl_sec: int = 120
) -> bool:
    """Acquires exclusive partition maintenance lock with ownership and TTL."""
    now_utc = datetime.now(timezone.utc)
    lock_expiry = now_utc + timedelta(seconds=lock_ttl_sec)

    rows = (
        db.query(ParquetExportTask)
        .filter(
            ParquetExportTask.partition_date == partition_date,
            or_(
                ParquetExportTask.export_status != "in_progress",
                ParquetExportTask.lease_expires_at < now_utc
            ),
            or_(
                ParquetExportTask.cleanup_locked_by.is_(None),
                ParquetExportTask.cleanup_locked_expires_at < now_utc
            )
        )
        .update(
            {
                "cleanup_locked_by": cleanup_id,
                "cleanup_locked_expires_at": lock_expiry,
                "updated_at": now_utc,
            },
            synchronize_session=False
        )
    )
    db.commit()
    return rows == 1


def renew_cleanup_lock(
    db: Session,
    partition_date: str,
    cleanup_id: str,
    extend_sec: int = 120
) -> bool:
    """Extends TTL of an active cleanup lock owned by cleanup_id."""
    now_utc = datetime.now(timezone.utc)
    new_expiry = now_utc + timedelta(seconds=extend_sec)

    rows = (
        db.query(ParquetExportTask)
        .filter(
            ParquetExportTask.partition_date == partition_date,
            ParquetExportTask.cleanup_locked_by == cleanup_id,
            ParquetExportTask.cleanup_locked_expires_at >= now_utc
        )
        .update(
            {
                "cleanup_locked_expires_at": new_expiry,
                "updated_at": now_utc,
            },
            synchronize_session=False
        )
    )
    db.commit()
    return rows == 1


def release_cleanup_lock(
    db: Session,
    partition_date: str,
    cleanup_id: str
) -> bool:
    """Releases cleanup lock only if owned by cleanup_id."""
    now_utc = datetime.now(timezone.utc)

    rows = (
        db.query(ParquetExportTask)
        .filter(
            ParquetExportTask.partition_date == partition_date,
            ParquetExportTask.cleanup_locked_by == cleanup_id
        )
        .update(
            {
                "cleanup_locked_by": None,
                "cleanup_locked_expires_at": None,
                "updated_at": now_utc,
            },
            synchronize_session=False
        )
    )
    db.commit()
    return rows == 1


def verify_cleanup_lock_active(
    db: Session,
    partition_date: str,
    cleanup_id: str
) -> bool:
    """Verifies that cleanup_id currently holds an unexpired lock."""
    now_utc = datetime.now(timezone.utc)
    is_valid = db.query(
        db.query(ParquetExportTask).filter(
            ParquetExportTask.partition_date == partition_date,
            ParquetExportTask.cleanup_locked_by == cleanup_id,
            ParquetExportTask.cleanup_locked_expires_at >= now_utc
        ).exists()
    ).scalar()
    return bool(is_valid)


def safe_tombstone_file(
    db: Session,
    file_path: Path,
    partition_date: str,
    cleanup_id: str
) -> Path:
    """
    Atomically renames file to tombstone ONLY if cleanup lock is currently active.
    Aborts with CleanupLockLostError if lock was lost or expired.
    """
    if not verify_cleanup_lock_active(db, partition_date, cleanup_id):
        raise CleanupLockLostError(
            f"Cleanup process '{cleanup_id}' lost active lock on partition '{partition_date}'; "
            f"refusing to tombstone file '{file_path.name}'."
        )

    tombstone_path = file_path.with_name(f"{file_path.name}.tombstone.{cleanup_id}")
    file_path.rename(tombstone_path)
    return tombstone_path


def safe_unlink_file(
    db: Session,
    file_path: Path,
    partition_date: str,
    cleanup_id: str
) -> None:
    """
    Unlinks a tombstoned file ONLY if cleanup lock is currently active.
    Aborts with CleanupLockLostError if lock was lost or expired.
    """
    if not verify_cleanup_lock_active(db, partition_date, cleanup_id):
        raise CleanupLockLostError(
            f"Cleanup process '{cleanup_id}' lost active lock on partition '{partition_date}'; "
            f"refusing to unlink file '{file_path.name}'."
        )

    file_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Reconciliation & Tombstone Recovery
# ---------------------------------------------------------------------------

def parse_artifact_filename(filename: str) -> Optional[Tuple[int, str]]:
    """Extracts (generation, attempt_uuid) from partition_g{gen}_a{uuid}.parquet."""
    parts = filename.split(".")[0].split("_")
    gen = None
    uuid_str = None
    for p in parts:
        if p.startswith("g") and p[1:].isdigit():
            gen = int(p[1:])
        elif p.startswith("a"):
            uuid_str = p[1:]
    if gen is not None and uuid_str is not None:
        return gen, uuid_str
    return None


def reconcile_outbox_partitions(
    db: Session,
    base_dir: Path,
    cleanup_id: Optional[str] = None,
    grace_seconds: int = 1800
) -> Dict[str, Any]:
    """
    Multi-phase reconciliation:
    1. Reconciles missing outbox partitions (heals partitions with SQL observations).
    2. Recovers or purges tombstoned artifacts according to destination state matrix.
    3. Cleans unreferenced, superseded attempt files under safe ownership.
    """
    cid = cleanup_id or f"reconciler-{uuid.uuid4().hex[:8]}"
    telemetry_dir = base_dir / "telemetry"
    if not telemetry_dir.exists():
        return {"reconciled_tasks": 0, "restored_tombstones": 0, "purged_tombstones": 0, "conflicts": 0}

    now_utc = datetime.now(timezone.utc)
    now_ts = now_utc.timestamp()

    reconciled_count = 0
    restored_count = 0
    purged_count = 0
    conflict_count = 0

    # Scan partition folders
    for date_dir in telemetry_dir.glob("year=*/month=*/date=*"):
        if not date_dir.is_dir():
            continue
        pdate_str = date_dir.name.split("=")[1]

        # 1. Check outbox task existence
        task = db.query(ParquetExportTask).filter_by(partition_date=pdate_str).first()
        if task is None:
            # Check if SQL records exist for this date
            sql_records = get_partition_records(db, pdate_str)
            if sql_records:
                task = stage_or_reactivate_outbox_task(db, pdate_str)
                db.commit()
                reconciled_count += 1
            else:
                # No SQL records: protect fresh files (< 24h grace)
                continue

        # Acquire lock to perform maintenance
        if not acquire_cleanup_lock(db, pdate_str, cleanup_id=cid, lock_ttl_sec=120):
            continue

        try:
            # 2. Inspect tombstone files
            for t_file in list(date_dir.glob("*.tombstone.*")):
                dest_name = t_file.name.split(".tombstone.")[0]
                dest_file = date_dir / dest_name
                dest_str = str(dest_file.resolve())

                # Parse generation and attempt UUID
                parsed = parse_artifact_filename(dest_name)
                t_gen = parsed[0] if parsed else 0

                # Destination State 1: Destination Absent
                if not dest_file.exists():
                    if (
                        task.publication_status == "published"
                        and task.published_artifact_path
                        and Path(task.published_artifact_path).resolve() == dest_file.resolve()
                    ):
                        # SQL requires this exact artifact: verify content before restoring
                        try:
                            meta = pq.read_metadata(t_file)
                            t_size = t_file.stat().st_size
                            t_sha = compute_file_sha256(t_file)

                            size_match = (task.published_file_size is None or t_size == task.published_file_size)
                            rows_match = (task.published_record_count is None or meta.num_rows == task.published_record_count)
                            sha_match = (task.published_sha256 is None or t_sha == task.published_sha256)

                            if size_match and rows_match and sha_match:
                                os.replace(t_file, dest_file)
                                restored_count += 1
                            else:
                                # Content invalid: invalidate publication and quarantine tombstone
                                invalidate_publication_state(
                                    db, pdate_str,
                                    expected_pub_gen=task.published_generation,
                                    expected_pub_path=task.published_artifact_path,
                                    expected_pub_sha=task.published_sha256,
                                    recovery_owner_id=cid,
                                    reason=f"Tombstone failed integrity: size={size_match}, rows={rows_match}, sha={sha_match}"
                                )
                                conflict_count += 1
                        except Exception as exc:
                            conflict_count += 1
                            logger.error(f"Failed to inspect tombstone {t_file}: {exc}")
                    else:
                        # Destination absent and SQL does not require this artifact
                        if task.completed_generation and t_gen < task.completed_generation:
                            t_file.unlink(missing_ok=True)
                            purged_count += 1

                # Destination State 2 & 3: Destination Exists
                else:
                    dest_size = dest_file.stat().st_size
                    tomb_size = t_file.stat().st_size

                    # Check cryptographic SHA-256 equivalence
                    if dest_size == tomb_size:
                        dest_sha = compute_file_sha256(dest_file)
                        tomb_sha = compute_file_sha256(t_file)

                        if dest_sha == tomb_sha:
                            # State 2: Equivalent - safely purge redundant tombstone
                            t_file.unlink(missing_ok=True)
                            purged_count += 1
                        else:
                            # State 3: Differs - retain both and report conflict
                            conflict_count += 1
                            logger.warning(
                                f"RECONCILIATION_CONFLICT: SHA-256 mismatch between {dest_file} ({dest_sha}) "
                                f"and {t_file} ({tomb_sha}). Both retained."
                            )
                    else:
                        # State 3: Differing sizes - retain both
                        conflict_count += 1
                        logger.warning(
                            f"RECONCILIATION_CONFLICT: Size mismatch between {dest_file} ({dest_size}) "
                            f"and {t_file} ({tomb_size}). Both retained."
                        )

            # 3. Purge superseded published attempt files older than grace window
            for p_file in list(date_dir.glob("partition_g*.parquet")):
                parsed = parse_artifact_filename(p_file.name)
                if not parsed:
                    continue
                file_gen, _ = parsed
                file_mtime = p_file.stat().st_mtime

                # Invariant: Never touch active or completed generation
                if (
                    task.published_artifact_path
                    and Path(task.published_artifact_path).resolve() == p_file.resolve()
                ):
                    continue

                if file_gen >= task.generation:
                    continue

                if task.completed_generation and file_gen == task.completed_generation:
                    continue

                # Must be older than retention grace period
                if (now_ts - file_mtime) >= grace_seconds:
                    tomb = safe_tombstone_file(db, p_file, pdate_str, cleanup_id=cid)
                    tomb.unlink(missing_ok=True)
                    purged_count += 1

        finally:
            release_cleanup_lock(db, pdate_str, cleanup_id=cid)

    return {
        "reconciled_tasks": reconciled_count,
        "restored_tombstones": restored_count,
        "purged_tombstones": purged_count,
        "conflicts": conflict_count,
    }
