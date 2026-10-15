"""Focused tests for outbox worker leasing, attempt UUID fencing, cleanup mutex, and tombstone reconciliation."""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.models import Base, ParquetExportTask, VesselPosition
from pelagos.storage.outbox import (
    claim_next_export_task,
    commit_export_completion,
    stage_or_reactivate_outbox_task,
    acquire_cleanup_lock,
    renew_cleanup_lock,
    release_cleanup_lock,
    verify_cleanup_lock_active,
    safe_tombstone_file,
    safe_unlink_file,
    reconcile_outbox_partitions,
    invalidate_publication_state,
    fail_expired_worker_task,
    CleanupLockLostError,
)
from pelagos.storage.analytics import get_published_parquet_paths
from pelagos.storage.writer import compute_file_sha256


@pytest.fixture
def mem_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_worker_claim_and_completion_cas_atomicity(mem_db):
    """
    Verifies that worker task claim atomically binds a full RFC 4122 v4 UUID,
    and completion CAS atomically commits outbox export status and publication metadata.
    """
    task = stage_or_reactivate_outbox_task(mem_db, "2024-01-01")
    mem_db.commit()

    claimed = claim_next_export_task(mem_db, worker_id="worker-alpha", lease_seconds=120)
    assert claimed is not None
    claimed_task, attempt_uuid = claimed

    assert claimed_task.export_status == "in_progress"
    assert claimed_task.lease_worker_id == "worker-alpha"
    assert claimed_task.active_attempt_uuid == attempt_uuid
    assert len(attempt_uuid) == 36

    # Successful completion CAS
    success = commit_export_completion(
        db=mem_db,
        partition_date="2024-01-01",
        claimed_generation=1,
        worker_id="worker-alpha",
        attempt_uuid=attempt_uuid,
        published_path="/data/telemetry/date=2024-01-01/partition_g1_a1.parquet",
        record_count=100,
        file_size_bytes=4096,
        content_sha256="a" * 64
    )
    assert success is True

    # Check updated task in SQL
    updated = mem_db.query(ParquetExportTask).filter_by(partition_date="2024-01-01").one()
    assert updated.export_status == "completed"
    assert updated.publication_status == "published"
    assert updated.published_artifact_path == "/data/telemetry/date=2024-01-01/partition_g1_a1.parquet"
    assert updated.published_sha256 == "a" * 64
    assert updated.lease_worker_id is None
    assert updated.active_attempt_uuid is None


def test_stale_worker_attempt_fencing(mem_db):
    """
    Verifies that when Worker 1's lease expires and Worker 2 claims the task
    with a new attempt UUID, Worker 1's completion CAS affects 0 rows.
    """
    task = stage_or_reactivate_outbox_task(mem_db, "2024-01-01")
    mem_db.commit()

    claimed_1 = claim_next_export_task(mem_db, worker_id="worker-1", lease_seconds=1)
    assert claimed_1 is not None
    _, attempt_uuid_1 = claimed_1

    # Simulate Worker 1 lease expiry
    mem_db.query(ParquetExportTask).filter_by(partition_date="2024-01-01").update({
        "lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=5)
    })
    mem_db.commit()

    # Worker 2 reclaims the task with new attempt UUID
    claimed_2 = claim_next_export_task(mem_db, worker_id="worker-2", lease_seconds=120)
    assert claimed_2 is not None
    _, attempt_uuid_2 = claimed_2
    assert attempt_uuid_1 != attempt_uuid_2

    # Worker 1 wakes up and attempts completion CAS with stale attempt UUID
    worker_1_cas = commit_export_completion(
        db=mem_db,
        partition_date="2024-01-01",
        claimed_generation=1,
        worker_id="worker-1",
        attempt_uuid=attempt_uuid_1,
        published_path="/data/stale.parquet",
        record_count=10,
        file_size_bytes=1000,
        content_sha256="b" * 64
    )
    assert worker_1_cas is False, "Stale worker CAS must affect 0 rows"

    # Worker 2 completes successfully
    worker_2_cas = commit_export_completion(
        db=mem_db,
        partition_date="2024-01-01",
        claimed_generation=1,
        worker_id="worker-2",
        attempt_uuid=attempt_uuid_2,
        published_path="/data/valid.parquet",
        record_count=20,
        file_size_bytes=2000,
        content_sha256="c" * 64
    )
    assert worker_2_cas is True


def test_concurrent_ingestion_bumps_generation_and_fences_worker(mem_db):
    """
    Verifies that when new ingestion arrives while Worker 1 is exporting,
    target generation is incremented to 2, fencing Worker 1's generation 1 commit.
    """
    task = stage_or_reactivate_outbox_task(mem_db, "2024-01-01")
    mem_db.commit()
    assert task.generation == 1

    claimed = claim_next_export_task(mem_db, worker_id="worker-slow", lease_seconds=120)
    _, attempt_uuid = claimed

    # New ingestion arrives during export
    stage_or_reactivate_outbox_task(mem_db, "2024-01-01")
    mem_db.commit()

    task_after = mem_db.query(ParquetExportTask).filter_by(partition_date="2024-01-01").one()
    assert task_after.generation == 2
    assert task_after.export_status == "pending"

    # Worker slow attempts to commit generation 1
    cas_res = commit_export_completion(
        db=mem_db,
        partition_date="2024-01-01",
        claimed_generation=1,
        worker_id="worker-slow",
        attempt_uuid=attempt_uuid,
        published_path="/data/obsolete.parquet",
        record_count=5,
        file_size_bytes=500,
        content_sha256="d" * 64
    )
    assert cas_res is False, "Preempted generation CAS must affect 0 rows"


def test_worker_versus_cleanup_mutual_exclusion(mem_db):
    """
    Verifies worker cannot claim or complete tasks while cleanup lock is held,
    and cleanup cannot acquire lock while worker lease is active.
    """
    stage_or_reactivate_outbox_task(mem_db, "2024-01-01")
    mem_db.commit()

    # 1. Cleanup acquires lock
    assert acquire_cleanup_lock(mem_db, "2024-01-01", cleanup_id="cleaner-A") is True

    # 2. Worker attempt to claim must fail
    assert claim_next_export_task(mem_db, worker_id="worker-1") is None

    # 3. Release cleanup lock -> worker can claim
    release_cleanup_lock(mem_db, "2024-01-01", cleanup_id="cleaner-A")
    claimed = claim_next_export_task(mem_db, worker_id="worker-1", lease_seconds=120)
    assert claimed is not None

    # 4. Cleanup attempt to acquire lock while worker lease is active must fail
    assert acquire_cleanup_lock(mem_db, "2024-01-01", cleanup_id="cleaner-B") is False


def test_stale_cleanup_lock_recovery(mem_db):
    """Verifies that an expired cleanup lock does not block workers or subsequent cleanup."""
    expired_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    task = ParquetExportTask(
        partition_date="2024-01-02",
        export_status="pending",
        generation=1,
        cleanup_locked_by="crashed-cleaner",
        cleanup_locked_expires_at=expired_time
    )
    mem_db.add(task)
    mem_db.commit()

    # Worker can claim despite crashed cleaner because lock is expired
    claimed = claim_next_export_task(mem_db, worker_id="worker-recov")
    assert claimed is not None
    assert claimed[0].partition_date == "2024-01-02"


def test_stale_cleanup_cannot_release_newer_owner_lock(mem_db):
    """Verifies that Cleaner 1 cannot release Cleaner 2's active lock after expiry."""
    task = stage_or_reactivate_outbox_task(mem_db, "2024-01-03")
    mem_db.commit()

    # Cleaner 1 acquires lock
    assert acquire_cleanup_lock(mem_db, "2024-01-03", cleanup_id="cleaner-1", lock_ttl_sec=1) is True

    # Expire Cleaner 1's lock
    mem_db.query(ParquetExportTask).filter_by(partition_date="2024-01-03").update({
        "cleanup_locked_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)
    })
    mem_db.commit()

    # Cleaner 2 acquires lock
    assert acquire_cleanup_lock(mem_db, "2024-01-03", cleanup_id="cleaner-2", lock_ttl_sec=120) is True

    # Cleaner 1 attempts release -> affects 0 rows
    release_cleanup_lock(mem_db, "2024-01-03", cleanup_id="cleaner-1")

    # Cleaner 2's lock must remain intact
    task_after = mem_db.query(ParquetExportTask).filter_by(partition_date="2024-01-03").one()
    assert task_after.cleanup_locked_by == "cleaner-2"


def test_cleanup_lock_expiry_aborts_destructive_actions(mem_db, tmp_path):
    """
    Verifies that safe_tombstone_file() and safe_unlink_file() raise CleanupLockLostError
    when lock has expired, strictly preserving the original file.
    """
    partition_date = "2024-01-04"
    cleanup_id = "cleaner-abort-test"

    task = stage_or_reactivate_outbox_task(mem_db, partition_date)
    mem_db.commit()

    assert acquire_cleanup_lock(mem_db, partition_date, cleanup_id=cleanup_id, lock_ttl_sec=1) is True

    test_file = tmp_path / "test_artifact.parquet"
    content = b"PARQUET_TEST_PAYLOAD"
    test_file.write_bytes(content)

    # Expire lock
    mem_db.query(ParquetExportTask).filter_by(partition_date=partition_date).update({
        "cleanup_locked_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)
    })
    mem_db.commit()

    # Pre-action check must raise CleanupLockLostError
    with pytest.raises(CleanupLockLostError):
        safe_tombstone_file(mem_db, test_file, partition_date=partition_date, cleanup_id=cleanup_id)

    assert test_file.exists()
    assert test_file.read_bytes() == content

    with pytest.raises(CleanupLockLostError):
        safe_unlink_file(mem_db, test_file, partition_date=partition_date, cleanup_id=cleanup_id)

    assert test_file.exists()


def test_cleanup_lock_lost_at_post_rename_boundary_retains_tombstone(mem_db, tmp_path):
    """
    Verifies that when cleaning up a genuinely historical predecessor artifact:
    1. Authoritative artifact (Gen 2) remains untouched.
    2. If cleanup loses lock ownership at post-rename boundary, safe_unlink_file()
       raises CleanupLockLostError and uncertain tombstone is retained on disk.
    3. Subsequent reconciliation safely purges superseded tombstone under recovery ownership.
    """
    partition_date = "2024-01-05"
    cleaner_1 = "cleaner-uuid-1"
    cleaner_2 = "cleaner-uuid-2"

    pdir = tmp_path / "telemetry" / "year=2024" / "month=01" / "date=2024-01-05"
    pdir.mkdir(parents=True)

    historical_file = pdir / "partition_g1_a11111111-1111-1111-1111-111111111111.parquet"
    authoritative_file = pdir / "partition_g2_a22222222-2222-2222-2222-222222222222.parquet"

    historical_data = b"HISTORICAL_GEN1_DATA"
    authoritative_data = b"AUTHORITATIVE_GEN2_DATA"

    historical_file.write_bytes(historical_data)
    authoritative_file.write_bytes(authoritative_data)

    # SQL state: Gen 2 is authoritative completed artifact
    task = ParquetExportTask(
        partition_date=partition_date,
        export_status="completed",
        publication_status="published",
        generation=2,
        completed_generation=2,
        published_artifact_path=str(authoritative_file.resolve()),
        published_generation=2,
        published_sha256=compute_file_sha256(authoritative_file),
        published_file_size=len(authoritative_data),
        cleanup_locked_by=cleaner_1,
        cleanup_locked_expires_at=datetime.now(timezone.utc) + timedelta(seconds=120)
    )
    mem_db.add(task)
    mem_db.commit()

    # Cleaner 1 tombstones historical Gen 1 file (Gen 1 < Gen 2)
    tombstone_file = safe_tombstone_file(
        mem_db, historical_file, partition_date=partition_date, cleanup_id=cleaner_1
    )
    assert not historical_file.exists()
    assert tombstone_file.exists()

    # Simulate race: Cleaner 1 lock expires AND Cleaner 2 acquires lock
    mem_db.query(ParquetExportTask).filter_by(partition_date=partition_date).update({
        "cleanup_locked_by": cleaner_2,
        "cleanup_locked_expires_at": datetime.now(timezone.utc) + timedelta(seconds=120)
    })
    mem_db.commit()

    # Cleaner 1 attempts post-rename unlink -> must fail because lock was lost
    with pytest.raises(CleanupLockLostError):
        safe_unlink_file(mem_db, tombstone_file, partition_date=partition_date, cleanup_id=cleaner_1)

    # Tombstone is preserved on disk
    assert tombstone_file.exists()
    assert tombstone_file.read_bytes() == historical_data

    # Expire Cleaner 2 lock so reconciliation can acquire recovery lock
    mem_db.query(ParquetExportTask).filter_by(partition_date=partition_date).update({
        "cleanup_locked_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)
    })
    mem_db.commit()

    # Reconcile safely cleans up superseded tombstone under recovery lock
    res = reconcile_outbox_partitions(mem_db, tmp_path)
    assert authoritative_file.exists()
    assert not tombstone_file.exists()


def test_recovery_cas_guardrail_with_generation_and_attempt_uuid(mem_db):
    """
    Recovery CAS guardrail:
    fail_expired_worker_task must match expected generation and observed active attempt UUID.
    If state changed (e.g. replacement worker claimed), update affects 0 rows.
    """
    partition_date = "2024-01-06"
    recovery_owner = "recovery-runner"

    task = ParquetExportTask(
        partition_date=partition_date,
        export_status="in_progress",
        generation=1,
        lease_worker_id="crashed-worker",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        active_attempt_uuid="attempt-uuid-old",
        cleanup_locked_by=recovery_owner,
        cleanup_locked_expires_at=datetime.now(timezone.utc) + timedelta(seconds=120)
    )
    mem_db.add(task)
    mem_db.commit()

    # Case 1: Mismatched attempt UUID affects 0 rows
    failed_wrong_uuid = fail_expired_worker_task(
        db=mem_db,
        partition_date=partition_date,
        expected_generation=1,
        expected_attempt_uuid="attempt-uuid-DIFFERENT",
        recovery_owner_id=recovery_owner,
        reason="Test recovery"
    )
    assert failed_wrong_uuid is False

    # Case 2: Matching generation and attempt UUID succeeds
    failed_correct = fail_expired_worker_task(
        db=mem_db,
        partition_date=partition_date,
        expected_generation=1,
        expected_attempt_uuid="attempt-uuid-old",
        recovery_owner_id=recovery_owner,
        reason="Worker crashed"
    )
    assert failed_correct is True

    updated_task = mem_db.query(ParquetExportTask).filter_by(partition_date=partition_date).one()
    assert updated_task.export_status == "failed"
    assert updated_task.active_attempt_uuid is None


def test_publication_invalidation_cas_with_expected_metadata(mem_db):
    """
    Verifies that invalidate_publication_state matches expected generation, path, and sha256.
    If a replacement worker committed a newer generation, update affects 0 rows.
    """
    partition_date = "2024-01-07"
    recovery_owner = "recovery-runner"

    task = ParquetExportTask(
        partition_date=partition_date,
        publication_status="published",
        published_artifact_path="/data/gen1.parquet",
        published_generation=1,
        published_sha256="1" * 64,
        cleanup_locked_by=recovery_owner,
        cleanup_locked_expires_at=datetime.now(timezone.utc) + timedelta(seconds=120)
    )
    mem_db.add(task)
    mem_db.commit()

    # Mismatched expected sha affects 0 rows
    res_mismatch = invalidate_publication_state(
        db=mem_db,
        partition_date=partition_date,
        expected_pub_gen=1,
        expected_pub_path="/data/gen1.parquet",
        expected_pub_sha="2" * 64,
        recovery_owner_id=recovery_owner,
        reason="Corrupt"
    )
    assert res_mismatch is False

    # Matching metadata succeeds
    res_match = invalidate_publication_state(
        db=mem_db,
        partition_date=partition_date,
        expected_pub_gen=1,
        expected_pub_path="/data/gen1.parquet",
        expected_pub_sha="1" * 64,
        recovery_owner_id=recovery_owner,
        reason="Corrupt"
    )
    assert res_match is True

    updated_task = mem_db.query(ParquetExportTask).filter_by(partition_date=partition_date).one()
    assert updated_task.publication_status == "invalidated"
    assert updated_task.published_artifact_path is None


def test_publication_state_reader_isolation_and_zero_downtime(mem_db, tmp_path):
    """
    Verifies that while replacement generation is pending/in-progress,
    readers continue reading the previous published generation without downtime;
    and uncommitted attempt files are completely invisible to readers.
    """
    pdir = tmp_path / "telemetry" / "year=2024" / "month=01" / "date=2024-01-08"
    pdir.mkdir(parents=True)
    gen1_file = pdir / "partition_g1_a1.parquet"
    gen1_file.write_bytes(b"GEN1_DATA")

    # Gen 1 is published
    task = ParquetExportTask(
        partition_date="2024-01-08",
        export_status="completed",
        publication_status="published",
        generation=1,
        completed_generation=1,
        published_artifact_path=str(gen1_file.resolve()),
        published_generation=1,
        published_sha256="1" * 64,
        published_file_size=len(b"GEN1_DATA")
    )
    mem_db.add(task)
    mem_db.commit()

    # Readers see Gen 1
    paths_before = get_published_parquet_paths(mem_db)
    assert len(paths_before) == 1
    assert paths_before[0] == gen1_file.as_posix()

    # New ingestion arrives -> bumps generation to 2, export_status to pending
    stage_or_reactivate_outbox_task(mem_db, "2024-01-08")
    mem_db.commit()

    # Even though export is pending, publication remains published on Gen 1 (Zero Downtime)
    paths_during_pending = get_published_parquet_paths(mem_db)
    assert len(paths_during_pending) == 1
    assert paths_during_pending[0] == gen1_file.as_posix()

    # Worker claims Gen 2 and creates attempt file
    claimed = claim_next_export_task(mem_db, worker_id="worker-gen2")
    assert claimed is not None
    _, attempt_uuid = claimed

    gen2_attempt_file = pdir / f"partition_g2_a{attempt_uuid}.parquet"
    gen2_attempt_file.write_bytes(b"GEN2_DATA_UNCOMMITTED")

    # Readers still see ONLY Gen 1; uncommitted Gen 2 attempt is completely invisible
    paths_during_export = get_published_parquet_paths(mem_db)
    assert len(paths_during_export) == 1
    assert paths_during_export[0] == gen1_file.as_posix()

    # Worker commits Gen 2 CAS
    commit_export_completion(
        db=mem_db,
        partition_date="2024-01-08",
        claimed_generation=2,
        worker_id="worker-gen2",
        attempt_uuid=attempt_uuid,
        published_path=str(gen2_attempt_file.resolve()),
        record_count=100,
        file_size_bytes=len(b"GEN2_DATA_UNCOMMITTED"),
        content_sha256="2" * 64
    )

    # Readers now smoothly transition to Gen 2
    paths_after = get_published_parquet_paths(mem_db)
    assert len(paths_after) == 1
    assert paths_after[0] == gen2_attempt_file.as_posix()
