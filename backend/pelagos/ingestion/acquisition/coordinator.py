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
from pelagos.ingestion.acquisition.bunker_provider import refresh_bunker_prices

logger = logging.getLogger(__name__)

ALL_SOURCES = ["environmental", "trade", "ports", "vessels", "bunker"]


def _persist_failed_source_audit(
    db: Session,
    source_name: str,
    source_partition: str,
    error_message: str,
    started_at: datetime.datetime,
    run_type: str = "automated",
) -> Optional[int]:
    """Persist a failed IngestionRun in an isolated transaction.

    Ensures the audit record survives source rollback without contaminating
    or committing caller session state. If persistence fails, raises an
    exception so coordinator can record it accurately without silent swallowing.
    """
    from pelagos.models import IngestionRun
    from sqlalchemy.orm import sessionmaker

    bind = db.get_bind()
    AuditSession = sessionmaker(bind=bind, autocommit=False, autoflush=False)
    audit_session = AuditSession()
    try:
        failed_run = IngestionRun(
            run_type=run_type,
            source_name=source_name,
            source_partition=source_partition,
            records_read=0,
            accepted_count=0,
            quarantined_count=0,
            duplicate_count=0,
            unparseable_count=0,
            failed_count=1,
            status="failed",
            error_details=error_message,
            started_at=started_at,
            completed_at=datetime.datetime.now(datetime.timezone.utc),
        )
        audit_session.add(failed_run)
        audit_session.commit()
        return failed_run.id
    except Exception as exc:
        audit_session.rollback()
        raise RuntimeError(f"Audit record persistence failed: {exc}") from exc
    finally:
        audit_session.close()


def run_sources_refresh(
    db: Session,
    sources: Optional[List[str]] = None,
    offline: bool = False,
    max_observations: int = 12,
    client: Optional[ResilientHttpClient] = None,
) -> Dict[str, Any]:
    """Execute multi-source data acquisition and reconciliation run.

    Coordinator owns transaction boundaries:
    - Begins independent transactions per source.
    - Commits on source success; rolls back on source failure.
    - Persists failed audit records in an isolated transaction so they survive rollback
      without committing unrelated caller data.
    - Aggregates overall status across all executed sources.

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

    def _execute_source(
        source_key: str,
        adapter_fn,
        source_name: str,
        source_partition: str,
    ):
        source_start = datetime.datetime.now(datetime.timezone.utc)
        try:
            res = adapter_fn()
            if res.get("status") == "failed":
                db.rollback()
                err_msg = res.get("error") or res.get("error_details") or "Source adapter returned failed status"
                audit_err = None
                try:
                    run_id = _persist_failed_source_audit(
                        db=db,
                        source_name=source_name,
                        source_partition=source_partition,
                        error_message=err_msg,
                        started_at=source_start,
                    )
                    res["run_id"] = run_id
                except Exception as a_exc:
                    audit_err = str(a_exc)
                    logger.error(f"Failed to persist failure audit record for {source_key}: {a_exc}")
                if audit_err:
                    res["error"] = f"{err_msg}; {audit_err}"
                source_results[source_key] = res
            else:
                db.commit()
                source_results[source_key] = res
        except Exception as exc:
            db.rollback()
            err_msg = str(exc)
            audit_err = None
            try:
                _persist_failed_source_audit(
                    db=db,
                    source_name=source_name,
                    source_partition=source_partition,
                    error_message=err_msg,
                    started_at=source_start,
                )
            except Exception as a_exc:
                audit_err = str(a_exc)
                logger.error(f"Failed to persist failure audit record for {source_key}: {a_exc}")

            final_err = f"{err_msg} (audit persistence failure: {audit_err})" if audit_err else err_msg
            logger.error(f"{source_key.capitalize()} refresh failed: {final_err}")
            source_results[source_key] = {"status": "failed", "error": final_err}

    # 1. Environmental
    if "environmental" in target_sources or "env" in target_sources:
        _execute_source(
            source_key="environmental",
            adapter_fn=lambda: refresh_environmental_observations(
                db=db,
                max_per_station=max_observations,
                client=http_client,
                offline=offline,
            ),
            source_name="NOAA National Data Buoy Center (NDBC)",
            source_partition="realtime_buoy_stream",
        )

    # 2. Trade
    if "trade" in target_sources:
        _execute_source(
            source_key="trade",
            adapter_fn=lambda: refresh_trade_observations(
                db=db,
                client=http_client,
                offline=offline,
            ),
            source_name="USDA FAS GATS / Indian DGCI&S",
            source_partition="trade_observations",
        )

    # 3. Ports
    if "ports" in target_sources or "port" in target_sources:
        _execute_source(
            source_key="ports",
            adapter_fn=lambda: refresh_ports_directory(
                db=db,
                client=http_client,
                offline=offline,
            ),
            source_name="UNECE UN/LOCODE / NGA WPI",
            source_partition="genuine_ports_directory.json",
        )

    # 4. Vessels
    if "vessels" in target_sources or "vessel" in target_sources:
        _execute_source(
            source_key="vessels",
            adapter_fn=lambda: refresh_vessels_registry(
                db=db,
                client=http_client,
                offline=offline,
            ),
            source_name="NOAA MarineCadastre / USCG NAIS",
            source_partition="vessel_positions",
        )

    # 5. Bunker Fuel
    if "bunker" in target_sources or "fuel" in target_sources:
        _execute_source(
            source_key="bunker",
            adapter_fn=lambda: refresh_bunker_prices(
                db=db,
                client=http_client,
                offline=offline,
            ),
            source_name="Bunker Fuel Prices (bunkerfuelprices.com)",
            source_partition="bunker_price_observations",
        )

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
