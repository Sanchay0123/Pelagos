"""Pelagos Vessel Registry & AIS Position Acquisition Adapter.

Investigates historical AIS data sources (NOAA MarineCadastre / USCG NAIS).
Synchronizes canonical vessel physical specifications from verified AIS positions,
strictly preserving the distinction between vessel identity, vessel positions,
and commercial charter party availability.

Traces to REQ-FR-DII-002, REQ-FR-OMR-002, and Stage B Acquisition Mandates.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from pelagos.ingestion.acquisition.client import ResilientHttpClient
from pelagos.ingestion.vessel_registry import sync_vessels_from_positions
from pelagos.models import Vessel, VesselPosition

logger = logging.getLogger(__name__)


def refresh_vessels_registry(
    db: Session,
    client: Optional[ResilientHttpClient] = None,
    offline: bool = False,
) -> Dict[str, Any]:
    """Synchronize canonical Vessel entities from verified AIS positions.

    Enforces telemetry and commercial boundaries:
    - Historical positions remain explicitly labeled historical (is_live: False).
    - Commercial charter availability remains unavailable without verified commercial fixtures.
    - Reports authentic MarineCadastre source status (bulk archives require manual download / ETL).

    Args:
        db: Active SQLAlchemy Session.
        client: Optional ResilientHttpClient.
        offline: If True, operates in offline mode.

    Returns:
        Audit report dictionary.
    """
    start_time = datetime.datetime.now(datetime.timezone.utc)
    source_status = "manual_download_only"
    source_notes = (
        "NOAA MarineCadastre AIS daily extracts (~500MB compressed/day) are published as bulk zip archives "
        "(coast.noaa.gov/htdata/CMSP/AISDataHandler/) for periodic offline ETL; no public unauthenticated live AIS API exists. "
        "Canonical vessel entities are consolidated from verified terrestrial/satellite AIS position fixes. "
        "Physical vessel suitability does NOT imply commercial spot charter availability."
    )

    sync_res = sync_vessels_from_positions(db)

    total_vessels_count = db.query(Vessel).count()
    total_positions_count = db.query(VesselPosition).count()

    return {
        "status": sync_res["status"],
        "run_id": sync_res.get("run_id"),
        "source_status": source_status,
        "integration_status": source_status,
        "records_read": sync_res.get("records_read", 0),
        "accepted_count": sync_res.get("accepted_count", 0),
        "duplicate_count": sync_res.get("duplicate_count", 0),
        "total_canonical_vessels_in_db": total_vessels_count,
        "total_vessel_positions_in_db": total_positions_count,
        "is_live": False,
        "commercial_charter_availability": "unavailable",
        "retrieval_timestamp": start_time.isoformat(),
        "source": "NOAA MarineCadastre / USCG NAIS",
        "notes": source_notes,
    }
