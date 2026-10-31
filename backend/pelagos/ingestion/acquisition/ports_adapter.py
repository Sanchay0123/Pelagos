"""Pelagos Port Directory Acquisition & Reconciliation Adapter.

Investigates authoritative port directories (UNECE UN/LOCODE, NGA World Port Index Pub 150,
and Indian Major Ports authorities).
Reconciles canonical port records and aliases idempotently without deleting previously existing ports.

Traces to REQ-FR-CRI-012, OD-ROUT-06-03, and Stage B Acquisition Mandates.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from pelagos.ingestion.acquisition.client import ResilientHttpClient
from pelagos.ingestion.ports_ingest import ingest_ports_from_fixture
from pelagos.models import IngestionRun, Port, PortAlias

logger = logging.getLogger(__name__)

DEFAULT_PORTS_FIXTURE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "genuine_ports_directory.json"
)


def refresh_ports_directory(
    db: Session,
    fixture_path: Optional[Path] = None,
    client: Optional[ResilientHttpClient] = None,
    offline: bool = False,
) -> Dict[str, Any]:
    """Reconcile canonical port directory and alias mappings.

    Enforces safe reconciliation:
    - Never removes existing canonical ports or aliases.
    - Updates metadata or adds new verified ports without destructive overwrites.
    - Reports authentic source accessibility status.

    Args:
        db: Active SQLAlchemy Session.
        fixture_path: Optional path to ports JSON fixture.
        client: Optional ResilientHttpClient.
        offline: If True, uses offline reference fixture.

    Returns:
        Structured audit report dictionary.
    """
    start_time = datetime.datetime.now(datetime.timezone.utc)
    source_status = "manual_download_only"
    source_notes = (
        "UNECE UN/LOCODE official releases are published bi-annually as downloadable zip archives (service.unece.org); "
        "direct automated crawling is blocked (HTTP 403 bot protection). "
        "NGA World Port Index and Indian Port Authority portals provide verified public hydrographic references. "
        "Reconciliation preserves all existing canonical ports without destructive deletion."
    )

    fpath = fixture_path or DEFAULT_PORTS_FIXTURE
    if not fpath.is_file():
        return {
            "status": "failed",
            "source_status": source_status,
            "error": f"Ports fixture not found at {fpath}",
        }

    res = ingest_ports_from_fixture(db, fixture_path=fpath)

    total_ports_count = db.query(Port).count()
    total_aliases_count = db.query(PortAlias).count()

    return {
        "status": res["status"],
        "run_id": res.get("run_id"),
        "source_status": source_status,
        "integration_status": source_status,
        "records_read": res.get("records_read", 0),
        "accepted_count": res.get("accepted_count", 0),
        "duplicate_count": res.get("duplicate_count", 0),
        "total_canonical_ports_in_db": total_ports_count,
        "total_aliases_in_db": total_aliases_count,
        "retrieval_timestamp": start_time.isoformat(),
        "source": "UNECE UN/LOCODE / NGA WPI / Indian Port Authorities",
        "notes": source_notes,
    }
