"""Ingestion service coordinating validation, deduplication, and persistence."""

import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, inspect
from sqlalchemy.exc import IntegrityError, InvalidRequestError

from pelagos.models import VesselPosition, QuarantinedRecord, IngestionRun
from pelagos.ingestion.schemas import (
    TelemetryRecord, 
    IngestionResult, 
    QuarantinedSummary
)
from pelagos.ingestion.validator import validate_telemetry_record
from pelagos.storage.outbox import stage_or_reactivate_outbox_task


def _safe_evict_failed_orm_object(db: Session, entity: Any) -> None:
    """
    Safely evicts an ORM entity from the Session after a rolled-back savepoint.
    Uses inspect(..., raiseerr=False) and catches only InvalidRequestError.
    Does not swallow arbitrary exceptions.
    """
    insp = inspect(entity, raiseerr=False)
    if insp is None or insp.session is not db or insp.detached:
        return

    try:
        db.expunge(entity)
    except InvalidRequestError:
        pass


def is_idempotency_key_violation(exc: IntegrityError, dialect_name: str = "sqlite") -> bool:
    """
    Classifies whether an IntegrityError is specifically the idempotency_key
    uniqueness violation using structured database diagnostics.
    """
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False

    # 1. PostgreSQL: Structured error codes and diagnostic record
    if dialect_name == "postgresql" or hasattr(orig, "pgcode"):
        if getattr(orig, "pgcode", None) != "23505":
            return False
        diag = getattr(orig, "diag", None)
        if diag is not None:
            cname = getattr(diag, "constraint_name", None)
            if cname:
                return cname == "uq_vessel_positions_idempotency_key"
        return "uq_vessel_positions_idempotency_key" in str(orig)

    # 2. SQLite: Structured error code and qualified column target
    if dialect_name == "sqlite" or "sqlite" in type(orig).__module__:
        err_name = getattr(orig, "sqlite_errorname", None)
        if err_name and err_name not in ("SQLITE_CONSTRAINT_UNIQUE", "SQLITE_CONSTRAINT_PRIMARYKEY"):
            return False
        orig_msg = str(orig)
        return (
            "unique constraint failed" in orig_msg.lower()
            and "vessel_positions.idempotency_key" in orig_msg
        )

    return "uq_vessel_positions_idempotency_key" in str(exc).lower()


class IngestionService:
    def __init__(self, db: Session):
        self.db = db

    def generate_idempotency_key(self, record: TelemetryRecord) -> str:
        """Deterministic idempotency key for deduplication with microsecond precision (REQ-NFR-ARG-003)."""
        dt_utc = record.observation_time.astimezone(timezone.utc)
        obs_str = dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return f"{record.source_name}:{record.mmsi}:{obs_str}"

    def ingest_record(self, raw_data: Dict[str, Any], source_partition: str = "single-record") -> Tuple[bool, Optional[str]]:
        res = self.ingest_batch([raw_data], source_partition=source_partition)
        if res.accepted_count > 0 or res.duplicate_count > 0:
            return True, None
        err = res.quarantined_records[0].error_details if res.quarantined_records else "Ingestion failed"
        return False, err

    def ingest_batch(
        self,
        raw_records: List[Dict[str, Any]],
        source_partition: str = "bundled-sample-2024-01-01",
        run_type: str = "bundled",
        dry_run: bool = False,
        unparseable_count: int = 0,
    ) -> IngestionResult:
        """
        Process a batch of raw telemetry dictionaries:
        - Validates coordinates, kinematics, timestamps, and provenance.
        - Routes anomalies to quarantine table (REQ-FR-DII-004).
        - Enforces idempotency via deterministic keys (REQ-NFR-ARG-003).
        - Persists validated records into operational database with per-record savepoints.
        - Stages or reactivates outbox tasks in the same transaction for Parquet synchronization.
        - Records an IngestionRun audit trail (survives rollback on failure).
        - Dry-run mode validates without writing to DB and does not persist an IngestionRun.
        """
        started_at = datetime.now(timezone.utc)
        total_raw = len(raw_records)
        records_read = total_raw + unparseable_count
        accepted = 0
        quarantined = 0
        duplicates = 0
        quarantined_summaries: List[QuarantinedSummary] = []
        
        now_utc = datetime.now(timezone.utc)
        source_name = "NOAA MarineCadastre / USCG NAIS"
        if raw_records and raw_records[0].get("source_name"):
            source_name = str(raw_records[0]["source_name"])

        # In dry-run mode, validate and count without mutating database
        if dry_run:
            seen_batch_keys = set()
            for raw in raw_records:
                val_res = validate_telemetry_record(raw)
                if not val_res.is_valid:
                    quarantined += 1
                    quarantined_summaries.append(
                        QuarantinedSummary(
                            error_code=val_res.error_code or "ANOMALY_UNKNOWN",
                            error_details=val_res.error_details or "Validation failed",
                            mmsi=str(raw.get("mmsi") or ""),
                            raw_record_id=str(raw.get("raw_record_id") or ""),
                            source_partition=source_partition,
                        )
                    )
                    continue

                rec = val_res.record
                idempotency_key = self.generate_idempotency_key(rec)
                if idempotency_key in seen_batch_keys:
                    duplicates += 1
                    continue
                seen_batch_keys.add(idempotency_key)

                existing = self.db.query(VesselPosition.id).filter(
                    VesselPosition.idempotency_key == idempotency_key
                ).first()
                if existing:
                    duplicates += 1
                else:
                    accepted += 1

            return IngestionResult(
                source_partition=source_partition,
                total_received=total_raw,
                records_read=records_read,
                accepted_count=accepted,
                quarantined_count=quarantined,
                duplicate_count=duplicates,
                unparseable_count=unparseable_count,
                failed_count=0,
                status="dry_run",
                is_dry_run=True,
                run_id=None,
                quarantined_records=quarantined_summaries,
            )

        # Production execution with atomic transaction and rollback survival
        try:
            seen_batch_keys = set()
            staged_quarantine: List[QuarantinedRecord] = []
            candidates: List[Tuple[TelemetryRecord, str]] = []

            # 1. Pure in-memory pre-loop validation & filtering
            for raw in raw_records:
                val_res = validate_telemetry_record(raw)
                if not val_res.is_valid:
                    quarantined += 1
                    mmsi_val = str(raw.get("mmsi") or "")
                    raw_id_val = str(raw.get("raw_record_id") or "")
                    q_summary = QuarantinedSummary(
                        error_code=val_res.error_code or "ANOMALY_UNKNOWN",
                        error_details=val_res.error_details or "Validation failed",
                        mmsi=mmsi_val,
                        raw_record_id=raw_id_val,
                        source_partition=source_partition,
                    )
                    quarantined_summaries.append(q_summary)

                    try:
                        raw_json = json.dumps(raw, default=str)
                    except Exception:
                        raw_json = str(raw)

                    q_entry = QuarantinedRecord(
                        source_name=str(raw.get("source_name") or source_name),
                        source_partition=source_partition,
                        mmsi=mmsi_val if mmsi_val else None,
                        raw_record_id=raw_id_val if raw_id_val else None,
                        error_code=q_summary.error_code,
                        error_details=q_summary.error_details,
                        raw_payload=raw_json,
                        ingestion_time=now_utc,
                    )
                    staged_quarantine.append(q_entry)
                    continue

                rec = val_res.record
                idempotency_key = self.generate_idempotency_key(rec)

                # Intra-batch duplicate check
                if idempotency_key in seen_batch_keys:
                    duplicates += 1
                    continue
                seen_batch_keys.add(idempotency_key)

                # Pre-query check against database
                existing = self.db.query(VesselPosition.id).filter(
                    VesselPosition.idempotency_key == idempotency_key
                ).first()
                if existing:
                    duplicates += 1
                    continue

                candidates.append((rec, idempotency_key))

            # 2. Per-record isolated savepoints inside no_autoflush boundary
            accepted_vessels: List[VesselPosition] = []
            dialect = getattr(self.db.bind.dialect, "name", "sqlite") if self.db.bind else "sqlite"

            with self.db.no_autoflush:
                for rec, idempotency_key in candidates:
                    vessel_pos = VesselPosition(
                        mmsi=rec.mmsi,
                        imo=rec.imo,
                        vessel_name=rec.vessel_name,
                        vessel_type=rec.vessel_type or "Commercial",
                        vessel_type_code=rec.vessel_type_code,
                        latitude=rec.latitude,
                        longitude=rec.longitude,
                        speed_knots=rec.speed_knots,
                        course_degrees=rec.course_degrees,
                        heading_degrees=rec.heading_degrees,
                        draft_meters=rec.draft_meters,
                        length_meters=rec.length_meters,
                        width_meters=rec.width_meters,
                        destination=rec.destination,
                        navigational_status=rec.navigational_status,
                        observation_time=rec.observation_time,
                        publication_time=rec.publication_time,
                        ingestion_time=now_utc,
                        processing_time=now_utc,
                        source_name=rec.source_name,
                        source_partition=source_partition,
                        license_type=rec.license,
                        raw_record_id=rec.raw_record_id,
                        idempotency_key=idempotency_key,
                    )

                    try:
                        with self.db.begin_nested():
                            self.db.add(vessel_pos)
                            self.db.flush()
                        accepted_vessels.append(vessel_pos)
                        accepted += 1
                    except IntegrityError as exc:
                        _safe_evict_failed_orm_object(self.db, vessel_pos)
                        if is_idempotency_key_violation(exc, dialect_name=dialect):
                            duplicates += 1
                        else:
                            # Other integrity violation (e.g. NOT NULL, check constraint)
                            quarantined += 1
                            q_sum = QuarantinedSummary(
                                error_code="ANOMALY_INTEGRITY_VIOLATION",
                                error_details=str(exc),
                                mmsi=rec.mmsi,
                                raw_record_id=rec.raw_record_id or "",
                                source_partition=source_partition,
                            )
                            quarantined_summaries.append(q_sum)
                            q_rec = QuarantinedRecord(
                                source_name=rec.source_name,
                                source_partition=source_partition,
                                mmsi=rec.mmsi,
                                raw_record_id=rec.raw_record_id,
                                error_code="ANOMALY_INTEGRITY_VIOLATION",
                                error_details=str(exc),
                                raw_payload=json.dumps({"mmsi": rec.mmsi, "key": idempotency_key}),
                                ingestion_time=now_utc,
                            )
                            staged_quarantine.append(q_rec)

            completed_at = datetime.now(timezone.utc)
            if unparseable_count > 0:
                if accepted == 0 and quarantined == 0 and duplicates == 0:
                    run_status = "failed"
                else:
                    run_status = "partial"
            else:
                run_status = "completed"

            error_details = (
                "All records in partition were unparseable; zero usable records extracted."
                if run_status == "failed"
                else None
            )

            # Create IngestionRun audit record
            run_record = IngestionRun(
                run_type=run_type,
                source_name=source_name,
                source_partition=source_partition,
                raw_records_received=total_raw,
                records_read=records_read,
                accepted_count=accepted,
                quarantined_count=quarantined,
                duplicate_count=duplicates,
                unparseable_count=unparseable_count,
                failed_count=0,
                status=run_status,
                error_details=error_details,
                started_at=started_at,
                completed_at=completed_at,
            )
            self.db.add(run_record)
            self.db.flush()

            # Attach run_id to staged entities
            for v in accepted_vessels:
                v.ingestion_run_id = run_record.id
            for q in staged_quarantine:
                q.ingestion_run_id = run_record.id
                self.db.add(q)

            # 3. Stage or reactivate outbox tasks for affected partition dates in same transaction
            affected_dates = {
                v.observation_time.astimezone(timezone.utc).strftime("%Y-%m-%d")
                for v in accepted_vessels
            }
            for pdate_str in affected_dates:
                stage_or_reactivate_outbox_task(self.db, pdate_str)

            self.db.commit()

            return IngestionResult(
                source_partition=source_partition,
                total_received=total_raw,
                records_read=records_read,
                accepted_count=accepted,
                quarantined_count=quarantined,
                duplicate_count=duplicates,
                unparseable_count=unparseable_count,
                failed_count=0,
                status=run_status,
                is_dry_run=False,
                run_id=run_record.id,
                error_details=error_details,
                quarantined_records=quarantined_summaries,
            )

        except Exception as exc:
            # Rollback data transaction to prevent orphaned or partial writes
            self.db.rollback()

            # Record failed IngestionRun in a clean transaction so audit survives rollback
            failed_run_id = None
            try:
                failed_at = datetime.now(timezone.utc)
                failed_run = IngestionRun(
                    run_type=run_type,
                    source_name=source_name,
                    source_partition=source_partition,
                    records_read=records_read,
                    accepted_count=0,
                    quarantined_count=0,
                    duplicate_count=0,
                    unparseable_count=unparseable_count,
                    failed_count=total_raw,
                    status="failed",
                    error_details=str(exc),
                    started_at=started_at,
                    completed_at=failed_at,
                )
                self.db.add(failed_run)
                self.db.commit()
                failed_run_id = failed_run.id
            except Exception:
                self.db.rollback()

            return IngestionResult(
                source_partition=source_partition,
                total_received=total_raw,
                records_read=records_read,
                accepted_count=0,
                quarantined_count=0,
                duplicate_count=0,
                unparseable_count=unparseable_count,
                failed_count=total_raw,
                status="failed",
                is_dry_run=False,
                run_id=failed_run_id,
                error_details=str(exc),
                quarantined_records=[],
            )

    def get_ingestion_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        runs = self.db.query(IngestionRun).order_by(IngestionRun.id.desc()).limit(limit).all()
        return [
            {
                "id": r.id,
                "run_type": r.run_type,
                "source_name": r.source_name,
                "source_partition": r.source_partition,
                "raw_records_received": r.raw_records_received,
                "records_read": r.records_read,
                "accepted_count": r.accepted_count,
                "quarantined_count": r.quarantined_count,
                "duplicate_count": r.duplicate_count,
                "unparseable_count": r.unparseable_count,
                "failed_count": r.failed_count,
                "status": r.status,
                "error_details": r.error_details,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in runs
        ]

    def get_quarantine_records(self, limit: int = 50) -> List[Dict[str, Any]]:
        records = self.db.query(QuarantinedRecord).order_by(
            QuarantinedRecord.id.desc()
        ).limit(limit).all()
        
        return [
            {
                "id": r.id,
                "source_name": r.source_name,
                "error_code": r.error_code,
                "error_details": r.error_details,
                "ingestion_time": r.ingestion_time.isoformat() if r.ingestion_time else None,
            }
            for r in records
        ]

    def get_ingestion_status(self) -> Dict[str, Any]:
        total_positions = self.db.query(func.count(VesselPosition.id)).scalar() or 0
        distinct_vessels = self.db.query(func.count(func.distinct(VesselPosition.mmsi))).scalar() or 0
        total_quarantined = self.db.query(func.count(QuarantinedRecord.id)).scalar() or 0
        
        # Newest observation timestamp
        newest_obs = self.db.query(func.max(VesselPosition.observation_time)).scalar()
        
        return {
            "total_observations": total_positions,
            "distinct_vessels": distinct_vessels,
            "total_quarantined": total_quarantined,
            "newest_observation_time": newest_obs.isoformat() if newest_obs else None,
            "operational_status": "healthy"
        }
