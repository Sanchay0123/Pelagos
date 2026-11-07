"""Pelagos Maritime Port Directory Ingestion Connector.

Parses, validates, and idempotently populates canonical port and port alias
entities from verified hydrographic directories (UNECE UN/LOCODE, NGA WPI,
and official national port authority fixtures).

Traces to REQ-FR-CRI-012, OD-ROUT-06-03, and DATA-001 Section 5.2.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from pelagos.models import IngestionRun, Port, PortAlias, QuarantinedRecord

logger = logging.getLogger(__name__)

DEFAULT_PORTS_FIXTURE = (
    Path(__file__).resolve().parent.parent / "data" / "genuine_ports_directory.json"
)


def validate_port_record(item: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate a raw port directory dictionary for essential fields and coordinate bounds."""
    unlocode = item.get("unlocode")
    if not unlocode or not isinstance(unlocode, str) or len(unlocode.strip()) < 4:
        return False, f"Invalid or missing UN/LOCODE: '{unlocode}'"

    name = item.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        return False, f"Missing port name for UN/LOCODE '{unlocode}'"

    lat = item.get("latitude")
    lon = item.get("longitude")
    if lat is None or lon is None:
        return False, f"Missing coordinates for UN/LOCODE '{unlocode}'"

    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (ValueError, TypeError):
        return False, f"Non-numeric coordinates [{lon}, {lat}] for UN/LOCODE '{unlocode}'"

    if not (-90.0 <= lat_f <= 90.0):
        return False, f"Latitude out of bounds ({lat_f}) for UN/LOCODE '{unlocode}'"
    if not (-180.0 <= lon_f <= 180.0):
        return False, f"Longitude out of bounds ({lon_f}) for UN/LOCODE '{unlocode}'"

    return True, None


def ingest_ports_from_fixture(
    db: Session,
    fixture_path: Optional[Path] = None,
    source_name: str = "UNECE UN/LOCODE / NGA WPI",
    commit: bool = True,
) -> Dict[str, Any]:
    """Ingest ports and aliases idempotently into canonical relational tables.

    Args:
        db: Active SQLAlchemy Session.
        fixture_path: Path to ports JSON file. Defaults to DEFAULT_PORTS_FIXTURE.
        source_name: Provenance label for auditing.

    Returns:
        Summary dict containing ingestion reconciliation metrics.
    """
    path = fixture_path or DEFAULT_PORTS_FIXTURE
    if not path.is_file():
        raise FileNotFoundError(f"Ports fixture not found at {path}")

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

    raw_ports: List[Dict[str, Any]] = payload.get("ports", [])
    records_read = len(raw_ports)
    accepted_count = 0
    quarantined_count = 0
    duplicate_count = 0

    for item in raw_ports:
        valid, err_msg = validate_port_record(item)
        if not valid:
            quarantined = QuarantinedRecord(
                source_name=source_name,
                source_partition=path.name,
                ingestion_run_id=ingestion_run.id,
                raw_record_id=str(item.get("unlocode") or "unknown"),
                error_code="VALIDATION_FAILED",
                error_details=err_msg or "Unknown validation failure",
                raw_payload=json.dumps(item),
            )
            db.add(quarantined)
            quarantined_count += 1
            continue

        unlocode = item["unlocode"].strip().upper()
        port_id = item.get("port_id", unlocode).strip().upper()

        existing_port = db.query(Port).filter(Port.unlocode == unlocode).first()
        if existing_port:
            # Update mutable fields idempotently
            existing_port.name = item["name"].strip()
            existing_port.country = item.get("country", existing_port.country).strip()
            existing_port.country_code = item.get("country_code", existing_port.country_code).strip().upper()
            existing_port.subdivision = item.get("subdivision")
            existing_port.latitude = float(item["latitude"])
            existing_port.longitude = float(item["longitude"])
            existing_port.coordinate_type = item.get("coordinate_type", existing_port.coordinate_type)
            existing_port.port_region = item.get("port_region", existing_port.port_region)
            existing_port.is_monitored_corridor_eligible = bool(item.get("is_monitored_corridor_eligible", False))
            existing_port.source_url = item.get("source_url", existing_port.source_url)
            existing_port.provenance = item.get("provenance", existing_port.provenance)
            existing_port.license = item.get("license", existing_port.license)
            existing_port.access_limitations = item.get("access_limitations", existing_port.access_limitations)
            existing_port.nearest_graph_node = item.get("nearest_graph_node", existing_port.nearest_graph_node)
            port_entity = existing_port
            duplicate_count += 1
        else:
            port_entity = Port(
                unlocode=unlocode,
                port_id=port_id,
                source_id=item.get("source_id", f"{unlocode} (UN/LOCODE)"),
                name=item["name"].strip(),
                country=item.get("country", "").strip(),
                country_code=item.get("country_code", "").strip().upper(),
                subdivision=item.get("subdivision"),
                latitude=float(item["latitude"]),
                longitude=float(item["longitude"]),
                crs=item.get("crs", "EPSG:4326"),
                coordinate_type=item.get("coordinate_type", "port_point"),
                port_region=item.get("port_region"),
                is_monitored_corridor_eligible=bool(item.get("is_monitored_corridor_eligible", False)),
                source_url=item.get("source_url"),
                provenance=item.get("provenance"),
                license=item.get("license", "Public Domain / Open Data"),
                access_limitations=item.get("access_limitations"),
                nearest_graph_node=item.get("nearest_graph_node"),
            )
            db.add(port_entity)
            accepted_count += 1

        db.flush()

        # Ingest aliases
        raw_aliases: List[Dict[str, Any]] = item.get("aliases", [])
        for alias_item in raw_aliases:
            alias_name = alias_item.get("alias_name", "").strip()
            if not alias_name:
                continue
            existing_alias = (
                db.query(PortAlias)
                .filter(PortAlias.port_unlocode == unlocode, PortAlias.alias_name == alias_name)
                .first()
            )
            if not existing_alias:
                alias_entity = PortAlias(
                    port_unlocode=unlocode,
                    alias_name=alias_name,
                    alias_type=alias_item.get("alias_type", "alternative_name"),
                    source_name=alias_item.get("source_name", source_name),
                )
                db.add(alias_entity)

    completed_time = datetime.datetime.now(datetime.timezone.utc)
    ingestion_run.records_read = records_read
    ingestion_run.accepted_count = accepted_count
    ingestion_run.quarantined_count = quarantined_count
    ingestion_run.duplicate_count = duplicate_count
    ingestion_run.status = "completed" if quarantined_count == 0 else "partial"
    ingestion_run.completed_at = completed_time

    if commit:
        db.commit()
    else:
        db.flush()

    return {
        "run_id": ingestion_run.id,
        "records_read": records_read,
        "accepted_count": accepted_count,
        "quarantined_count": quarantined_count,
        "duplicate_count": duplicate_count,
        "status": ingestion_run.status,
    }
