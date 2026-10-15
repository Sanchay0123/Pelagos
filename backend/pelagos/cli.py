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
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
