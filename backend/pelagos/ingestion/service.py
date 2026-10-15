"""Ingestion service coordinating validation, deduplication, and persistence."""

import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func

from pelagos.models import VesselPosition, QuarantinedRecord
from pelagos.ingestion.schemas import (
    TelemetryRecord, 
    IngestionResult, 
    QuarantinedSummary
)
from pelagos.ingestion.validator import validate_telemetry_record


class IngestionService:
    def __init__(self, db: Session):
        self.db = db

    def generate_idempotency_key(self, record: TelemetryRecord) -> str:
        """Deterministic idempotency key for deduplication (REQ-NFR-ARG-003)."""
        obs_str = record.observation_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"{record.source_name}:{record.mmsi}:{obs_str}"

    def ingest_record(self, raw_data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        res = self.ingest_batch([raw_data])
        if res.accepted_count > 0 or res.duplicate_count > 0:
            return True, None
        err = res.quarantined_records[0].error_details if res.quarantined_records else "Ingestion failed"
        return False, err

    def ingest_batch(self, raw_records: List[Dict[str, Any]]) -> IngestionResult:
        """
        Process a batch of raw telemetry dictionaries:
        - Validates coordinates, kinematics, timestamps, and provenance.
        - Routes anomalies to quarantine table (REQ-FR-DII-004).
        - Enforces idempotency via deterministic keys (REQ-NFR-ARG-003).
        - Persists validated records into operational database.
        """
        total = len(raw_records)
        accepted = 0
        quarantined = 0
        duplicates = 0
        quarantined_summaries: List[QuarantinedSummary] = []
        
        now_utc = datetime.now(timezone.utc)

        for raw in raw_records:
            val_res = validate_telemetry_record(raw)
            
            if not val_res.is_valid:
                # Anomaly detected: route to quarantine
                quarantined += 1
                q_summary = QuarantinedSummary(
                    error_code=val_res.error_code or "ANOMALY_UNKNOWN",
                    error_details=val_res.error_details or "Validation failed",
                    mmsi=str(raw.get("mmsi") or ""),
                    raw_record_id=str(raw.get("raw_record_id") or "")
                )
                quarantined_summaries.append(q_summary)
                
                try:
                    raw_json = json.dumps(raw, default=str)
                except Exception:
                    raw_json = str(raw)

                q_entry = QuarantinedRecord(
                    source_name=str(raw.get("source_name") or "Unknown"),
                    error_code=q_summary.error_code,
                    error_details=q_summary.error_details,
                    raw_payload=raw_json,
                    ingestion_time=now_utc,
                )
                self.db.add(q_entry)
                continue

            rec = val_res.record
            idempotency_key = self.generate_idempotency_key(rec)
            
            # Idempotency check: verify whether this exact observation has already been ingested
            existing = self.db.query(VesselPosition.id).filter(
                VesselPosition.idempotency_key == idempotency_key
            ).first()

            if existing:
                duplicates += 1
                continue

            # Persist validated operational record
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
                license_type=rec.license,
                raw_record_id=rec.raw_record_id,
                idempotency_key=idempotency_key,
            )
            self.db.add(vessel_pos)
            accepted += 1

        self.db.commit()

        return IngestionResult(
            total_received=total,
            accepted_count=accepted,
            quarantined_count=quarantined,
            duplicate_count=duplicates,
            quarantined_records=quarantined_summaries,
        )

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
