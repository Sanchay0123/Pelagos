"""Pelagos Vessel Registry Consolidation Connector.

Aggregates and synchronizes canonical vessel identities and static physical
specifications from verified AIS observations (NOAA MarineCadastre / USCG NAIS).

Traces to REQ-FR-DII-002, REQ-FR-OMR-002, and OPT-002.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from pelagos.models import IngestionRun, Vessel, VesselPosition

logger = logging.getLogger(__name__)


def infer_vessel_class(
    vessel_type: str,
    length_m: Optional[float],
    width_m: Optional[float],
) -> Optional[str]:
    """Deterministically categorize commercial dry bulk vessels based on dimensions."""
    vt = (vessel_type or "").lower()
    if "cargo" not in vt and "bulk" not in vt:
        return None

    if not length_m or not width_m:
        return "Dry Bulk General"

    if length_m >= 280.0 or width_m >= 35.0:
        return "Capesize"
    if 215.0 <= length_m < 280.0 and 31.0 <= width_m <= 33.0:
        return "Panamax"
    if 180.0 <= length_m < 215.0 and 28.0 <= width_m <= 33.0:
        return "Supramax"
    if 130.0 <= length_m < 180.0:
        return "Handymax"

    return "General Cargo / Bulk"


def sync_vessels_from_positions(
    db: Session,
    source_name: str = "NOAA MarineCadastre / USCG NAIS",
    commit: bool = True,
) -> Dict[str, Any]:
    """Populate canonical Vessel registry entities from existing vessel positions.

    Args:
        db: Active SQLAlchemy Session.
        source_name: Source attribution label.

    Returns:
        Summary dict containing reconciliation metrics.
    """
    start_time = datetime.datetime.now(datetime.timezone.utc)
    ingestion_run = IngestionRun(
        run_type="consolidated",
        source_name=source_name,
        source_partition="vessel_positions",
        started_at=start_time,
        status="running",
    )
    db.add(ingestion_run)
    db.flush()

    # Query distinct MMSIs from vessel_positions
    distinct_mmsis = [row[0] for row in db.query(VesselPosition.mmsi).distinct().all()]
    records_read = len(distinct_mmsis)
    accepted_count = 0
    duplicate_count = 0

    for mmsi in distinct_mmsis:
        # Get latest position for this MMSI to obtain most up-to-date attributes
        latest_pos = (
            db.query(VesselPosition)
            .filter(VesselPosition.mmsi == mmsi)
            .order_by(desc(VesselPosition.observation_time))
            .first()
        )
        if not latest_pos:
            continue

        vessel_class = infer_vessel_class(
            latest_pos.vessel_type,
            latest_pos.length_meters,
            latest_pos.width_meters,
        )

        existing_vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
        if existing_vessel:
            existing_vessel.imo = latest_pos.imo or existing_vessel.imo
            existing_vessel.vessel_name = latest_pos.vessel_name
            existing_vessel.vessel_type = latest_pos.vessel_type
            existing_vessel.vessel_type_code = latest_pos.vessel_type_code
            existing_vessel.length_meters = latest_pos.length_meters
            existing_vessel.width_meters = latest_pos.width_meters
            existing_vessel.draft_max_meters = latest_pos.draft_meters
            existing_vessel.vessel_class = vessel_class
            existing_vessel.source_name = source_name
            duplicate_count += 1
        else:
            vessel_entity = Vessel(
                mmsi=mmsi,
                imo=latest_pos.imo,
                vessel_name=latest_pos.vessel_name,
                vessel_type=latest_pos.vessel_type,
                vessel_type_code=latest_pos.vessel_type_code,
                vessel_class=vessel_class,
                length_meters=latest_pos.length_meters,
                width_meters=latest_pos.width_meters,
                draft_max_meters=latest_pos.draft_meters,
                flag_country=None,
                source_name=source_name,
                commercial_status="active",
            )
            db.add(vessel_entity)
            accepted_count += 1

    completed_time = datetime.datetime.now(datetime.timezone.utc)
    ingestion_run.records_read = records_read
    ingestion_run.accepted_count = accepted_count
    ingestion_run.duplicate_count = duplicate_count
    ingestion_run.quarantined_count = 0
    ingestion_run.status = "completed"
    ingestion_run.completed_at = completed_time

    if commit:
        db.commit()
    else:
        db.flush()

    return {
        "run_id": ingestion_run.id,
        "records_read": records_read,
        "accepted_count": accepted_count,
        "duplicate_count": duplicate_count,
        "status": ingestion_run.status,
    }
