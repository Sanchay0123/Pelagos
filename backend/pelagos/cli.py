"""Command line interface for Pelagos ingestion, coverage, and status audit."""

import sys
import json
import argparse
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.config import settings
from pelagos.database import init_db, SessionLocal, engine as default_engine
from pelagos.models import IngestionRun
from pelagos.ingestion.service import IngestionService
from pelagos.ingestion.marinecadastre import (
    load_genuine_sample_records,
    fetch_remote_noaa_slice,
    PartitionNotFoundError,
    NetworkIngestionError,
    PartitionFormatError,
)
from pelagos.ingestion.coverage import compute_coverage_summary


def get_db_session(db_url: Optional[str] = None):
    if db_url:
        connect_args = {}
        if db_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        custom_engine = create_engine(db_url, connect_args=connect_args)
        init_db(custom_engine)
        session_factory = sessionmaker(autocommit=False, autoflush=False, bind=custom_engine)
        return session_factory()
    else:
        init_db()
        return SessionLocal()


def handle_ingest_sample(args):
    db = get_db_session(args.db_url)
    try:
        sample_records = load_genuine_sample_records(source_partition=args.partition)
        service = IngestionService(db)
        result = service.ingest_batch(
            sample_records,
            source_partition=args.partition,
            run_type="bundled",
            dry_run=args.dry_run,
        )
        print(json.dumps(result.model_dump(), indent=2))
        return 0 if result.status in ("completed", "partial", "dry_run") else 1
    except Exception as e:
        print(json.dumps({"status": "failed", "error": str(e)}, indent=2), file=sys.stderr)
        return 1
    finally:
        db.close()


def handle_ingest_remote(args):
    db = get_db_session(args.db_url)
    try:
        if args.limit > 5000:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "error": f"Requested limit {args.limit} exceeds maximum allowable local test cap (5000).",
                    },
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 1

        records, unparseable_count = fetch_remote_noaa_slice(
            date_str=args.date,
            max_records=args.limit,
            row_group_index=args.row_group,
            timeout_seconds=args.timeout,
        )

        partition_name = f"ais-{args.date}.parquet"
        service = IngestionService(db)
        result = service.ingest_batch(
            records,
            source_partition=partition_name,
            run_type="remote",
            dry_run=args.dry_run,
            unparseable_count=unparseable_count,
        )
        print(json.dumps(result.model_dump(), indent=2))
        return 0 if result.status in ("completed", "partial", "dry_run") else 1

    except (PartitionNotFoundError, NetworkIngestionError, PartitionFormatError, ValueError) as known_err:
        error_msg = str(known_err)
        # Attempt to log a failed IngestionRun if not a dry-run
        if not args.dry_run:
            try:
                now_utc = datetime.now(timezone.utc)
                failed_run = IngestionRun(
                    run_type="remote",
                    source_name="NOAA MarineCadastre / USCG NAIS",
                    source_partition=f"ais-{args.date}.parquet",
                    records_read=0,
                    accepted_count=0,
                    quarantined_count=0,
                    duplicate_count=0,
                    unparseable_count=0,
                    failed_count=0,
                    status="failed",
                    error_details=error_msg,
                    started_at=now_utc,
                    completed_at=now_utc,
                )
                db.add(failed_run)
                db.commit()
            except Exception:
                db.rollback()
        print(
            json.dumps(
                {
                    "source": "NOAA MarineCadastre / USCG NAIS",
                    "partition": f"ais-{args.date}.parquet",
                    "status": "failed",
                    "error": error_msg,
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    finally:
        db.close()


def handle_status(args):
    db = get_db_session(args.db_url)
    try:
        service = IngestionService(db)
        status_data = service.get_ingestion_status()
        recent_runs = service.get_ingestion_runs(limit=5)
        output = {
            **status_data,
            "recent_runs": recent_runs,
        }
        print(json.dumps(output, indent=2))
        return 0
    finally:
        db.close()


def handle_coverage(args):
    db = get_db_session(args.db_url)
    try:
        coverage_data = compute_coverage_summary(db)
        print(json.dumps(coverage_data, indent=2))
        return 0
    finally:
        db.close()


def handle_parquet_sync(args):
    """Executes outbox sync: claims tasks, writes Parquet partitions, commits CAS."""
    from pathlib import Path
    from datetime import timedelta
    from sqlalchemy import func
    from pelagos.models import VesselPosition, ParquetExportTask
    from pelagos.storage.outbox import (
        claim_next_export_task,
        get_partition_records,
        commit_export_completion,
        stage_or_reactivate_outbox_task,
    )
    from pelagos.storage.writer import write_partition_parquet

    db = get_db_session(args.db_url)
    storage_dir = Path(args.storage_dir or settings.parquet_storage_dir)
    worker_id = args.worker_id or f"worker-{datetime.now(timezone.utc).strftime('%H%M%S')}"
    processed = 0

    try:
        # Auto-stage any unindexed partition dates present in SQL vessel_positions
        existing_task_dates = {
            t[0] for t in db.query(ParquetExportTask.partition_date).all()
        }
        min_max = db.query(
            func.min(VesselPosition.observation_time),
            func.max(VesselPosition.observation_time)
        ).first()
        if min_max and min_max[0] and min_max[1]:
            min_dt = min_max[0] if isinstance(min_max[0], datetime) else datetime.fromisoformat(str(min_max[0]))
            max_dt = min_max[1] if isinstance(min_max[1], datetime) else datetime.fromisoformat(str(min_max[1]))
            cur_date = min_dt.date()
            end_date = max_dt.date()
            while cur_date <= end_date:
                pdate_str = cur_date.strftime("%Y-%m-%d")
                if pdate_str not in existing_task_dates:
                    if get_partition_records(db, pdate_str):
                        stage_or_reactivate_outbox_task(db, pdate_str)
                        db.commit()
                        existing_task_dates.add(pdate_str)
                cur_date += timedelta(days=1)

        results = []
        while processed < args.limit:
            claimed = claim_next_export_task(db, worker_id=worker_id, lease_seconds=args.lease_ttl)
            if not claimed:
                break

            task, attempt_uuid = claimed
            pdate = task.partition_date
            gen = task.generation

            # Rebuild authoritatively from SQL
            records = get_partition_records(db, pdate)
            pub_path, rec_count, file_size, sha256_hash = write_partition_parquet(
                records=records,
                base_dir=storage_dir,
                partition_date_str=pdate,
                generation=gen,
                attempt_uuid=attempt_uuid,
                worker_id=worker_id,
            )

            # Atomic completion CAS
            success = commit_export_completion(
                db=db,
                partition_date=pdate,
                claimed_generation=gen,
                worker_id=worker_id,
                attempt_uuid=attempt_uuid,
                published_path=str(pub_path.resolve()),
                record_count=rec_count,
                file_size_bytes=file_size,
                content_sha256=sha256_hash,
            )

            results.append({
                "partition_date": pdate,
                "generation": gen,
                "attempt_uuid": attempt_uuid,
                "record_count": rec_count,
                "file_size_bytes": file_size,
                "content_sha256": sha256_hash,
                "artifact_path": str(pub_path.resolve()),
                "cas_committed": success,
            })
            processed += 1

        print(json.dumps({
            "status": "completed",
            "worker_id": worker_id,
            "partitions_processed": processed,
            "tasks": results
        }, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"status": "failed", "error": str(e)}, indent=2), file=sys.stderr)
        return 1
    finally:
        db.close()


def handle_parquet_catalog(args):
    """Inspects published Parquet partitions and displays structural catalog metadata."""
    from pelagos.storage.catalog import get_published_partition_catalog

    db = get_db_session(args.db_url)
    try:
        catalog = get_published_partition_catalog(db)
        print(json.dumps({"published_partitions": len(catalog), "catalog": catalog}, indent=2))
        return 0
    finally:
        db.close()


def handle_parquet_reconcile(args):
    """Reconciles outbox tasks, heals untracked partitions, and manages tombstones."""
    from pathlib import Path
    from pelagos.storage.outbox import reconcile_outbox_partitions

    db = get_db_session(args.db_url)
    storage_dir = Path(args.storage_dir or settings.parquet_storage_dir)
    try:
        result = reconcile_outbox_partitions(
            db=db,
            base_dir=storage_dir,
            cleanup_id=args.cleanup_id,
            grace_seconds=args.grace_seconds,
        )
        print(json.dumps({"status": "completed", "reconciliation": result}, indent=2))
        return 0
    finally:
        db.close()


def handle_analytics_summary(args):
    """Runs DuckDB analytical queries over published Parquet partitions."""
    from pelagos.storage.analytics import get_analytics_summary

    db = get_db_session(args.db_url)
    try:
        summary = get_analytics_summary(db)
        print(json.dumps(summary, indent=2))
        return 0
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pelagos",
        description="Pelagos Maritime Intelligence Platform - Ingestion & Governance CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Ingest Sample
    parser_sample = subparsers.add_parser(
        "ingest-sample", 
        help="Ingest offline authentic bundled NOAA MarineCadastre sample."
    )
    parser_sample.add_argument(
        "--partition", 
        default="bundled-sample-2024-01-01", 
        help="Partition attribution label (default: bundled-sample-2024-01-01)"
    )
    parser_sample.add_argument(
        "--dry-run", 
        action="store_true", 
        help="Validate records without writing to database or logging an IngestionRun."
    )
    parser_sample.add_argument(
        "--db-url", 
        default=None, 
        help="Optional database connection URL override."
    )

    # Ingest Remote
    parser_remote = subparsers.add_parser(
        "ingest-remote", 
        help="Fetch and ingest bounded partition from NOAA MarineCadastre remote storage."
    )
    parser_remote.add_argument(
        "--date", 
        default="2024-01-01", 
        help="Partition date in YYYY-MM-DD format (within verified 2024 catalog)."
    )
    parser_remote.add_argument(
        "--limit", 
        type=int, 
        default=150, 
        help="Maximum candidate records parsed and admitted (bounds memory/records, not transfer size)."
    )
    parser_remote.add_argument(
        "--row-group", 
        type=int, 
        default=0, 
        help="Parquet row group index to read (default: 0)."
    )
    parser_remote.add_argument(
        "--timeout", 
        type=int, 
        default=30, 
        help="Network socket timeout in seconds (default: 30)."
    )
    parser_remote.add_argument(
        "--dry-run", 
        action="store_true", 
        help="Fetch and validate without writing to database or logging an IngestionRun."
    )
    parser_remote.add_argument(
        "--db-url", 
        default=None, 
        help="Optional database connection URL override."
    )

    # Status
    parser_status = subparsers.add_parser(
        "status", 
        help="Display operational ingestion status and recent execution runs."
    )
    parser_status.add_argument(
        "--db-url", 
        default=None, 
        help="Optional database connection URL override."
    )

    # Coverage
    parser_coverage = subparsers.add_parser(
        "coverage", 
        help="Compute spatial bounding box, temporal extent, and honest coverage disclaimers."
    )
    parser_coverage.add_argument(
        "--db-url", 
        default=None, 
        help="Optional database connection URL override."
    )

    # Parquet Sync
    parser_sync = subparsers.add_parser(
        "parquet-sync",
        help="Sync pending outbox tasks to authoritative Parquet partitions."
    )
    parser_sync.add_argument("--worker-id", default=None, help="Worker identifier.")
    parser_sync.add_argument("--limit", type=int, default=10, help="Max partitions to process.")
    parser_sync.add_argument("--lease-ttl", type=int, default=300, help="Lease TTL seconds.")
    parser_sync.add_argument("--storage-dir", default=None, help="Parquet base directory override.")
    parser_sync.add_argument("--db-url", default=None, help="Optional database connection URL override.")

    # Parquet Catalog
    parser_cat = subparsers.add_parser(
        "parquet-catalog",
        help="Display structural catalog and bounding box extents for published Parquet partitions."
    )
    parser_cat.add_argument("--db-url", default=None, help="Optional database connection URL override.")

    # Parquet Reconcile
    parser_rec = subparsers.add_parser(
        "parquet-reconcile",
        help="Reconcile outbox partitions, heal untracked files, and resolve tombstones."
    )
    parser_rec.add_argument("--cleanup-id", default=None, help="Cleanup runner identifier.")
    parser_rec.add_argument("--grace-seconds", type=int, default=1800, help="Safety grace period seconds.")
    parser_rec.add_argument("--storage-dir", default=None, help="Parquet base directory override.")
    parser_rec.add_argument("--db-url", default=None, help="Optional database connection URL override.")

    # Analytics Summary
    parser_analytics = subparsers.add_parser(
        "analytics-summary",
        help="Execute DuckDB analytical aggregations over published Parquet partitions."
    )
    parser_analytics.add_argument("--db-url", default=None, help="Optional database connection URL override.")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "ingest-sample":
        sys.exit(handle_ingest_sample(args))
    elif args.command == "ingest-remote":
        sys.exit(handle_ingest_remote(args))
    elif args.command == "status":
        sys.exit(handle_status(args))
    elif args.command == "coverage":
        sys.exit(handle_coverage(args))
    elif args.command == "parquet-sync":
        sys.exit(handle_parquet_sync(args))
    elif args.command == "parquet-catalog":
        sys.exit(handle_parquet_catalog(args))
    elif args.command == "parquet-reconcile":
        sys.exit(handle_parquet_reconcile(args))
    elif args.command == "analytics-summary":
        sys.exit(handle_analytics_summary(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
