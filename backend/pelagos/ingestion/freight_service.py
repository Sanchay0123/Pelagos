"""
Transactional ingestion service for freight market observations and route registries.
Maintains strict idempotency, per-record savepoints, and IngestionRun audit tracking.
Traces to REQ-FR-DII-003, REQ-FR-DII-005, and REQ-NFR-ARG-003.
"""

import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from pelagos.models import FreightRoute, FreightObservation, IngestionRun
from pelagos.ingestion.schemas import IngestionResult
from pelagos.ingestion.service import _safe_evict_failed_orm_object, is_idempotency_key_violation
from pelagos.ingestion.freight_usda import normalize_usda_record, NormalizedFreightRecord

logger = logging.getLogger(__name__)

STANDARD_FREIGHT_ROUTES = [
    {
        "route_code": "USG-JPN-BULK",
        "route_name": "U.S. Gulf to Japan Bulk Grain Ocean Corridor",
        "origin_region": "U.S. Gulf Coast",
        "destination_region": "Japan",
        "vessel_class": "Panamax / Supramax",
        "cargo_type": "Grain / Dry Bulk",
        "unit_of_measure": "USD/MT",
        "currency": "USD",
        "benchmark_distance_nm": 9150.0,
    },
    {
        "route_code": "PNW-JPN-BULK",
        "route_name": "U.S. Pacific Northwest to Japan Bulk Grain Ocean Corridor",
        "origin_region": "U.S. Pacific Northwest",
        "destination_region": "Japan",
        "vessel_class": "Panamax / Supramax",
        "cargo_type": "Grain / Dry Bulk",
        "unit_of_measure": "USD/MT",
        "currency": "USD",
        "benchmark_distance_nm": 4520.0,
    },
    {
        "route_code": "SPREAD-USG-PNW-JPN",
        "route_name": "U.S. Gulf vs PNW to Japan Bulk Rate Spread",
        "origin_region": "U.S. Multi-Region",
        "destination_region": "Japan",
        "vessel_class": "Panamax / Supramax",
        "cargo_type": "Rate Spread",
        "unit_of_measure": "USD/MT",
        "currency": "USD",
        "benchmark_distance_nm": None,
    },
]


def ensure_standard_freight_routes(db: Session) -> None:
    """Initializes standard benchmark route definitions if they do not exist."""
    existing_codes = {r.route_code for r in db.query(FreightRoute.route_code).all()}
    now_utc = datetime.now(timezone.utc)
    for r_def in STANDARD_FREIGHT_ROUTES:
        if r_def["route_code"] not in existing_codes:
            route = FreightRoute(
                route_code=r_def["route_code"],
                route_name=r_def["route_name"],
                origin_region=r_def["origin_region"],
                destination_region=r_def["destination_region"],
                vessel_class=r_def["vessel_class"],
                cargo_type=r_def["cargo_type"],
                unit_of_measure=r_def["unit_of_measure"],
                currency=r_def["currency"],
                benchmark_distance_nm=r_def["benchmark_distance_nm"],
                is_active=True,
                created_at=now_utc,
                updated_at=now_utc,
            )
            db.add(route)
            existing_codes.add(r_def["route_code"])
    db.flush()


class FreightIngestionService:
    """Orchestrates validation, idempotency, and persistence of freight market data."""

    def __init__(self, db: Session):
        self.db = db

    def ingest_usda_batch(
        self,
        raw_records: List[Dict[str, Any]],
        source_partition: str = "usda-vessel-rates",
        run_type: str = "usda_vessel_rates",
        dry_run: bool = False,
    ) -> IngestionResult:
        """
        Ingests a batch of raw USDA AgTransport records into canonical freight tables.
        Adheres to the strict conservation equation:
        records_read = accepted + quarantined + duplicate + unparseable + failed
        """
        started_at = datetime.now(timezone.utc)
        ensure_standard_freight_routes(self.db)

        total_raw = len(raw_records)
        accepted = 0
        duplicate = 0
        quarantined = 0
        unparseable = 0
        failed = 0

        normalized_candidates: List[NormalizedFreightRecord] = []

        # 1. Parsing & Normalization
        for raw in raw_records:
            try:
                norm_recs, unp = normalize_usda_record(raw)
                normalized_candidates.extend(norm_recs)
                unparseable += unp
            except Exception as e:
                unparseable += 1
                logger.warning(f"Unparseable USDA freight record: {e}")

        # Total domain observation candidates evaluated
        records_read = len(normalized_candidates) + unparseable

        # 2. Database Idempotency Check & Savepoint Insertion
        now_utc = datetime.now(timezone.utc)
        dialect = getattr(self.db.bind.dialect, "name", "sqlite") if self.db.bind else "sqlite"
        accepted_observations: List[FreightObservation] = []

        with self.db.no_autoflush:
            for rec in normalized_candidates:
                # Pre-check idempotency key
                existing = self.db.query(FreightObservation.id).filter(
                    FreightObservation.idempotency_key == rec.idempotency_key
                ).first()
                if existing:
                    duplicate += 1
                    continue

                obs = FreightObservation(
                    route_code=rec.route_code,
                    observation_date=rec.observation_date,
                    rate_value=rec.rate_value,
                    currency=rec.currency,
                    unit_of_measure=rec.unit_of_measure,
                    is_derived=rec.is_derived,
                    rate_high=rec.rate_high,
                    rate_low=rec.rate_low,
                    observation_period=rec.observation_period,
                    source_name=rec.source_name,
                    source_dataset_id=rec.source_dataset_id,
                    publication_time=rec.publication_time,
                    ingestion_time=now_utc,
                    processing_time=now_utc,
                    license_type=rec.license_type,
                    raw_payload=rec.raw_payload,
                    idempotency_key=rec.idempotency_key,
                )

                if dry_run:
                    accepted += 1
                    continue

                try:
                    with self.db.begin_nested():
                        self.db.add(obs)
                        self.db.flush()
                    accepted_observations.append(obs)
                    accepted += 1
                except IntegrityError as exc:
                    _safe_evict_failed_orm_object(self.db, obs)
                    if is_idempotency_key_violation(exc, dialect_name=dialect):
                        duplicate += 1
                    else:
                        quarantined += 1
                        logger.warning(f"Freight observation integrity collision: {exc}")
                except Exception as exc:
                    _safe_evict_failed_orm_object(self.db, obs)
                    failed += 1
                    logger.error(f"Unexpected freight insertion failure: {exc}")

        completed_at = datetime.now(timezone.utc)

        if dry_run:
            return IngestionResult(
                source_partition=source_partition,
                status="dry_run",
                is_dry_run=True,
                total_received=total_raw,
                records_read=records_read,
                accepted_count=accepted,
                quarantined_count=quarantined,
                duplicate_count=duplicate,
                unparseable_count=unparseable,
                failed_count=failed,
                quarantined_records=[],
            )

        # 3. Audit logging in IngestionRun
        status_str = "completed" if (quarantined == 0 and unparseable == 0 and failed == 0) else "partial"
        run_record = IngestionRun(
            source_name="USDA AgTransport",
            source_partition=source_partition,
            run_type=run_type,
            raw_records_received=total_raw,
            records_read=records_read,
            accepted_count=accepted,
            quarantined_count=quarantined,
            duplicate_count=duplicate,
            unparseable_count=unparseable,
            failed_count=failed,
            status=status_str,
            started_at=started_at,
            completed_at=completed_at,
        )
        self.db.add(run_record)
        self.db.flush()

        for obs in accepted_observations:
            obs.ingestion_run_id = run_record.id

        self.db.commit()

        return IngestionResult(
            source_partition=source_partition,
            status=status_str,
            is_dry_run=False,
            total_received=total_raw,
            records_read=records_read,
            accepted_count=accepted,
            quarantined_count=quarantined,
            duplicate_count=duplicate,
            unparseable_count=unparseable,
            failed_count=failed,
            run_id=run_record.id,
            quarantined_records=[],
        )
