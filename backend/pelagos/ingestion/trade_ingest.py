"""Pelagos Bilateral Maritime Trade Flow Ingestion Connector.

Parses, validates, and idempotently ingests authentic point-in-time trade flow
observations from official government sources (USDA FAS GATS, UN Comtrade,
Indian DGCI&S).

Traces to DATA-001 Section 5.2 and REQ-NFR-DIP-001.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from pelagos.models import IngestionRun, QuarantinedRecord, TradeObservation

logger = logging.getLogger(__name__)

DEFAULT_TRADE_FIXTURE = (
    Path(__file__).resolve().parent.parent / "data" / "genuine_trade_flows.json"
)


def validate_trade_record(item: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate trade flow observation for required attributes and value sanity."""
    for req_field in ["reporter_iso2", "partner_iso2", "commodity_code", "period_date", "volume_metric_tons"]:
        if item.get(req_field) is None:
            return False, f"Missing required trade observation field: '{req_field}'"

    try:
        vol = float(item["volume_metric_tons"])
        if vol <= 0.0:
            return False, f"Trade volume must be strictly positive: {vol}"
    except (ValueError, TypeError):
        return False, f"Invalid non-numeric volume: {item['volume_metric_tons']}"

    try:
        datetime.date.fromisoformat(item["period_date"])
    except (ValueError, TypeError):
        return False, f"Invalid ISO date format: '{item['period_date']}'"

    return True, None


def ingest_trade_observations(
    db: Session,
    fixture_path: Optional[Path] = None,
    source_name: str = "USDA FAS GATS / Indian DGCI&S",
) -> Dict[str, Any]:
    """Ingest bilateral trade observations idempotently into trade_observations table.

    Args:
        db: Active SQLAlchemy Session.
        fixture_path: Path to trade observations JSON fixture.
        source_name: Source attribution label.

    Returns:
        Summary dict containing reconciliation metrics.
    """
    path = fixture_path or DEFAULT_TRADE_FIXTURE
    if not path.is_file():
        raise FileNotFoundError(f"Trade fixture not found at {path}")

    start_time = datetime.datetime.now(datetime.timezone.utc)
    ingestion_run = IngestionRun(
        run_type="bundled",
        source_name=source_name,
        source_partition=path.name,
        started_at=start_time,
        status="running",
    )
    db.add(ingestion_run)
    db.flush()

    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    raw_records: List[Dict[str, Any]] = payload.get("observations", [])
    records_read = len(raw_records)
    accepted_count = 0
    quarantined_count = 0
    duplicate_count = 0

    for item in raw_records:
        valid, err_msg = validate_trade_record(item)
        if not valid:
            quarantined = QuarantinedRecord(
                source_name=source_name,
                source_partition=path.name,
                ingestion_run_id=ingestion_run.id,
                raw_record_id=str(item.get("commodity_code") or "unknown"),
                error_code="TRADE_VALIDATION_FAILED",
                error_details=err_msg or "Unknown validation failure",
                raw_payload=json.dumps(item),
            )
            db.add(quarantined)
            quarantined_count += 1
            continue

        reporter_iso2 = item["reporter_iso2"].strip().upper()
        partner_iso2 = item["partner_iso2"].strip().upper()
        commodity_code = item["commodity_code"].strip()
        trade_flow = item.get("trade_flow", "Export").strip()
        period_date = datetime.date.fromisoformat(item["period_date"])
        vol = float(item["volume_metric_tons"])
        val = float(item["trade_value_usd"]) if item.get("trade_value_usd") is not None else None
        unit_price = float(item["unit_price_usd_per_mt"]) if item.get("unit_price_usd_per_mt") is not None else (val / vol if val and vol else None)

        idempotency_key = f"{source_name}:{reporter_iso2}:{partner_iso2}:{commodity_code}:{trade_flow}:{period_date.isoformat()}"

        existing = (
            db.query(TradeObservation)
            .filter(TradeObservation.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            existing.volume_metric_tons = vol
            existing.trade_value_usd = val
            existing.unit_price_usd_per_mt = unit_price
            duplicate_count += 1
        else:
            obs = TradeObservation(
                reporter_country=item.get("reporter_country", reporter_iso2),
                reporter_iso2=reporter_iso2,
                partner_country=item.get("partner_country", partner_iso2),
                partner_iso2=partner_iso2,
                commodity_code=commodity_code,
                commodity_name=item.get("commodity_name", "Commodity"),
                trade_flow=trade_flow,
                period_date=period_date,
                period_type=item.get("period_type", "monthly"),
                volume_metric_tons=vol,
                trade_value_usd=val,
                unit_price_usd_per_mt=unit_price,
                source_name=item.get("source_name", source_name),
                license_type=item.get("license_type", "Public Domain / Open Government Data"),
                idempotency_key=idempotency_key,
            )
            db.add(obs)
            accepted_count += 1

    completed_time = datetime.datetime.now(datetime.timezone.utc)
    ingestion_run.records_read = records_read
    ingestion_run.accepted_count = accepted_count
    ingestion_run.quarantined_count = quarantined_count
    ingestion_run.duplicate_count = duplicate_count
    ingestion_run.status = "completed" if quarantined_count == 0 else "partial"
    ingestion_run.completed_at = completed_time

    db.commit()

    return {
        "run_id": ingestion_run.id,
        "records_read": records_read,
        "accepted_count": accepted_count,
        "quarantined_count": quarantined_count,
        "duplicate_count": duplicate_count,
        "status": ingestion_run.status,
    }
