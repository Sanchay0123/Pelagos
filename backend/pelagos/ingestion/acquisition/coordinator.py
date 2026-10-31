"""Pelagos Multi-Source Automated Data Acquisition Coordinator.

Orchestrates automated acquisition and reconciliation across all maritime data sources:
- Environmental buoy observations (NOAA NDBC / INCOIS)
- Bilateral maritime commodity trade (USDA FAS GATS / DGCI&S)
- Port directory and aliases (UNECE UN/LOCODE / NGA WPI)
- Vessel registry and AIS telemetry (NOAA MarineCadastre / USCG NAIS)

Traces to DATA-001 Section 5.2, REQ-NFR-DIP-001, and Stage B Acquisition Mandates.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from pelagos.ingestion.acquisition.client import ResilientHttpClient
from pelagos.ingestion.acquisition.noaa_ndbc import refresh_environmental_observations
from pelagos.ingestion.acquisition.ports_adapter import refresh_ports_directory
from pelagos.ingestion.acquisition.trade_adapter import refresh_trade_observations
from pelagos.ingestion.acquisition.vessels_adapter import refresh_vessels_registry

logger = logging.getLogger(__name__)

ALL_SOURCES = ["environmental", "trade", "ports", "vessels"]


def run_sources_refresh(
    db: Session,
    sources: Optional[List[str]] = None,
    offline: bool = False,
    max_observations: int = 12,
    client: Optional[ResilientHttpClient] = None,
) -> Dict[str, Any]:
    """Execute multi-source data acquisition and reconciliation run.

    Args:
        db: Active SQLAlchemy Session.
        sources: Subset of sources to refresh (defaults to all).
        offline: If True, operates in offline mode without remote calls.
        max_observations: Limit for observation records per source station/feed.
        client: Optional ResilientHttpClient instance.

    Returns:
        Structured audit dictionary detailing per-source outcomes and global status.
    """
    target_sources = [s.lower().strip() for s in (sources or ALL_SOURCES)]
    start_time = datetime.datetime.now(datetime.timezone.utc)
    source_results: Dict[str, Any] = {}

    http_client = client or ResilientHttpClient()

    # 1. Environmental
    if "environmental" in target_sources or "env" in target_sources:
        try:
            source_results["environmental"] = refresh_environmental_observations(
                db=db,
                max_per_station=max_observations,
                client=http_client,
                offline=offline,
            )
        except Exception as exc:
            logger.error(f"Environmental refresh failed: {exc}")
            source_results["environmental"] = {"status": "failed", "error": str(exc)}

    # 2. Trade
    if "trade" in target_sources:
        try:
            source_results["trade"] = refresh_trade_observations(
                db=db,
                client=http_client,
                offline=offline,
            )
        except Exception as exc:
            logger.error(f"Trade refresh failed: {exc}")
            source_results["trade"] = {"status": "failed", "error": str(exc)}

    # 3. Ports
    if "ports" in target_sources or "port" in target_sources:
        try:
            source_results["ports"] = refresh_ports_directory(
                db=db,
                client=http_client,
                offline=offline,
            )
        except Exception as exc:
            logger.error(f"Ports refresh failed: {exc}")
            source_results["ports"] = {"status": "failed", "error": str(exc)}

    # 4. Vessels
    if "vessels" in target_sources or "vessel" in target_sources:
        try:
            source_results["vessels"] = refresh_vessels_registry(
                db=db,
                client=http_client,
                offline=offline,
            )
        except Exception as exc:
            logger.error(f"Vessels refresh failed: {exc}")
            source_results["vessels"] = {"status": "failed", "error": str(exc)}

    completed_time = datetime.datetime.now(datetime.timezone.utc)
    duration_sec = (completed_time - start_time).total_seconds()

    # Determine overall status
    failures = [k for k, v in source_results.items() if v.get("status") == "failed"]
    if not failures:
        overall_status = "completed"
    elif len(failures) < len(source_results):
        overall_status = "partial_success"
    else:
        overall_status = "failed"

    return {
        "overall_status": overall_status,
        "mode": "offline" if offline else "automated",
        "started_at": start_time.isoformat(),
        "completed_at": completed_time.isoformat(),
        "duration_seconds": round(duration_sec, 3),
        "sources_executed": list(source_results.keys()),
        "failed_sources": failures,
        "results": source_results,
    }
