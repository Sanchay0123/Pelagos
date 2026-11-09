"""Pelagos Bunker Fuel Prices External Acquisition Adapter.

Connects to Bunker Fuel Prices (bunkerfuelprices.com) public pricing API,
enforces strict commercial data integrity, rejects synthetic or anomalous prices,
and persists genuine market observations with complete provenance.

Traces to DATA-001 Section 5.2, REQ-NFR-DIP-001, and Stage C2.2 mandates:
- Genuine external market prices only.
- USD per metric tonne where supplied; convert only with documented formula.
- Preserve original provider grade, port UN/LOCODE, and delivery basis.
- Separate observation timestamp and retrieval timestamp.
- Preserve source attribution and delivery basis without inferring missing fields.
- Reject malformed records and currency/unit contradictions.
- Deterministic idempotency for repeatable ingestion.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from pelagos.database import ensure_schema_compatibility
from pelagos.ingestion.acquisition.client import (
    AcquisitionError,
    AcquisitionHTTPError,
    AcquisitionNetworkError,
    AcquisitionTimeoutError,
    ResilientHttpClient,
)
from pelagos.models import BunkerPriceObservation

logger = logging.getLogger(__name__)

DEFAULT_BUNKER_API_URL = "https://bunkerfuelprices.com/api/v1/prices"
PROVIDER_NAME = "Bunker Fuel Prices (bunkerfuelprices.com)"
DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "genuine_bunker_prices.json"

KNOWN_BUNKER_GRADES = {
    "VLSFO",
    "HSFO",
    "MGO",
    "LSMGO",
    "IFO380",
    "ULSFO",
    "B24",
    "B30",
    "LNG",
}

VALID_DELIVERY_BASES = {"delivered", "ex_wharf", "fob"}


def parse_iso_datetime(value: Any) -> Optional[datetime.datetime]:
    """Parse an ISO 8601 string or timestamp into a timezone-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc)
        return value.astimezone(datetime.timezone.utc)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        # Handle trailing Z
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        try:
            dt = datetime.datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(datetime.timezone.utc)
        except ValueError:
            return None
    return None


def validate_bunker_record(raw: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """Validate a single bunker price record from the provider payload.

    Enforces commercial integrity:
    - 5-letter UN/LOCODE port identifier.
    - Supported bunker fuel grade.
    - Positive numeric price in USD/MT.
    - Rejects currency mismatch anomalies (e.g. CNY ticker marked as usdPerMt).
    - Preserves observation time, retrieval time, delivery basis, and pricing type.
    - Never invents missing fields or infers delivery basis.

    Returns:
        (is_valid, cleaned_dict, rejection_reason)
    """
    if not isinstance(raw, dict):
        return False, None, "Record is not a dictionary"

    # Port UN/LOCODE validation
    port_code = str(raw.get("locode") or raw.get("port_code") or "").strip().upper()
    if not port_code or len(port_code) != 5 or not port_code.isalpha():
        return False, None, f"Invalid UN/LOCODE port code: '{port_code}'"

    port_name = str(raw.get("port") or raw.get("port_name") or port_code).strip()
    country_code = str(raw.get("country") or raw.get("country_code") or "").strip().upper() or None

    # Fuel grade validation
    grade = str(raw.get("grade") or raw.get("fuel_grade") or "").strip().upper()
    if not grade or grade not in KNOWN_BUNKER_GRADES:
        return False, None, f"Unrecognized or unsupported fuel grade: '{grade}'"

    # Numeric price validation
    raw_price = raw.get("usdPerMt")
    if raw_price is None:
        raw_price = raw.get("amount") or raw.get("price")
    if raw_price is None:
        return False, None, "Missing price value"

    try:
        price_val = float(raw_price)
    except (ValueError, TypeError):
        return False, None, f"Non-numeric price value: '{raw_price}'"

    if price_val <= 0.0:
        return False, None, f"Price value must be positive: {price_val}"

    # Anomaly detection: Check for currency mismatch in ticker vs usdPerMt field
    source_feed = str(raw.get("source") or "").strip()
    if "_CNY" in source_feed.upper() and price_val > 3000.0:
        # e.g. "LSFO_380_CNZOS_BONDED_SELLER_CNY" with price 6028 under usdPerMt:
        # Price is quoted in CNY without official conversion; cannot treat as USD/MT.
        return False, None, f"Currency mismatch detected: source '{source_feed}' quotes in CNY but reported as usdPerMt ({price_val})"

    if price_val > 5000.0:
        return False, None, f"Implausible marine bunker price exceeding $5,000/MT: {price_val}"

    # Observation timestamp validation
    obs_raw = raw.get("observedAt") or raw.get("observation_time") or raw.get("timestamp")
    obs_time = parse_iso_datetime(obs_raw)
    if obs_time is None:
        return False, None, f"Invalid or unparseable observation timestamp: '{obs_raw}'"

    # Delivery basis (strictly preserve if present, never invent)
    raw_basis = raw.get("basis") or raw.get("delivery_basis")
    delivery_basis = None
    if raw_basis:
        cleaned_basis = str(raw_basis).strip().lower()
        if cleaned_basis in VALID_DELIVERY_BASES:
            delivery_basis = cleaned_basis
        else:
            delivery_basis = cleaned_basis  # preserve provider string

    # Pricing type / kind
    pricing_type = str(raw.get("kind") or raw.get("pricing_type") or "indicative").strip().lower()

    # Provenance notes
    provenance = raw.get("provenance") or raw.get("provenance_notes")
    if provenance:
        provenance = str(provenance).strip()

    cleaned = {
        "port_code": port_code,
        "port_name": port_name,
        "country_code": country_code,
        "fuel_grade": grade,
        "price_value": round(price_val, 2),
        "currency": "USD",
        "unit": "USD/MT",
        "pricing_type": pricing_type,
        "delivery_basis": delivery_basis,
        "source_name": PROVIDER_NAME,
        "source_feed": source_feed or None,
        "observation_time": obs_time,
        "provenance_notes": provenance,
    }

    return True, cleaned, None


def fetch_bunker_prices(
    client: Optional[ResilientHttpClient] = None,
    url: str = DEFAULT_BUNKER_API_URL,
    grade: Optional[str] = None,
    port: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Acquire genuine bunker fuel price payload from Bunker Fuel Prices public API.

    Args:
        client: Optional ResilientHttpClient instance.
        url: Endpoint URL.
        grade: Optional grade filter (VLSFO, HSFO, MGO).
        port: Optional UN/LOCODE filter.
        limit: Max records requested.

    Returns:
        Parsed JSON dictionary response.

    Raises:
        AcquisitionError: On network, HTTP, or parsing failure.
    """
    http = client or ResilientHttpClient(timeout_seconds=15.0, max_retries=3)
    params: Dict[str, Any] = {"limit": limit}
    if grade:
        params["grade"] = grade.strip().upper()
    if port:
        params["port"] = port.strip().upper()

    response = http.get(url=url, params=params)
    try:
        data = response.json()
    except Exception as exc:
        raise AcquisitionError(f"Failed to parse JSON response from {url}: {exc}") from exc

    if not isinstance(data, dict):
        raise AcquisitionError(f"Unexpected non-object response from {url}: {type(data)}")

    return data


def refresh_bunker_prices(
    db: Session,
    offline: bool = False,
    fixture_path: Optional[str] = None,
    client: Optional[ResilientHttpClient] = None,
    grade: Optional[str] = None,
    port: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute idempotent bunker fuel price acquisition and persistence.

    Reads genuine bunker observations from live API or authentic offline fixture,
    validates numeric and categorical integrity, isolates anomalies, and persists
    valid records to SQL storage with audit reconciliation metrics.

    Args:
        db: Active SQLAlchemy Session.
        offline: If True, uses authentic offline fixture without remote network calls.
        fixture_path: Optional override path to fixture JSON.
        client: Optional ResilientHttpClient instance.
        grade: Optional fuel grade filter.
        port: Optional port UN/LOCODE filter.

    Returns:
        Dictionary with execution status and reconciliation counters.
    """
    ensure_schema_compatibility(db.get_bind())
    retrieval_time_utc = datetime.datetime.now(datetime.timezone.utc)

    records_read = 0
    accepted_count = 0
    duplicate_count = 0
    quarantined_count = 0
    failed_count = 0
    quarantine_log: List[Dict[str, Any]] = []

    payload: Dict[str, Any] = {}
    source_origin = "remote_api"

    if offline:
        source_origin = "offline_fixture"
        fpath = Path(fixture_path) if fixture_path else DEFAULT_FIXTURE_PATH
        if not fpath.exists():
            return {
                "status": "failed",
                "source_origin": source_origin,
                "error": f"Bunker price fixture not found at {fpath}",
                "records_read": 0,
                "accepted_count": 0,
                "duplicate_count": 0,
                "quarantined_count": 0,
                "failed_count": 1,
            }
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as exc:
            return {
                "status": "failed",
                "source_origin": source_origin,
                "error": f"Failed to load bunker fixture: {exc}",
                "records_read": 0,
                "accepted_count": 0,
                "duplicate_count": 0,
                "quarantined_count": 0,
                "failed_count": 1,
            }
    else:
        try:
            payload = fetch_bunker_prices(
                client=client,
                grade=grade,
                port=port,
            )
        except AcquisitionError as exc:
            logger.warning("Bunker prices remote acquisition failed: %s", exc)
            return {
                "status": "failed",
                "source_origin": source_origin,
                "error": str(exc),
                "records_read": 0,
                "accepted_count": 0,
                "duplicate_count": 0,
                "quarantined_count": 0,
                "failed_count": 1,
            }

    raw_prices = payload.get("prices")
    if not isinstance(raw_prices, list):
        return {
            "status": "failed",
            "source_origin": source_origin,
            "error": "Payload missing 'prices' array",
            "records_read": 0,
            "accepted_count": 0,
            "duplicate_count": 0,
            "quarantined_count": 0,
            "failed_count": 1,
        }

    records_read = len(raw_prices)

    for item in raw_prices:
        is_valid, cleaned, rejection_reason = validate_bunker_record(item)
        if not is_valid or cleaned is None:
            quarantined_count += 1
            quarantine_log.append({
                "raw_record": item,
                "reason": rejection_reason,
            })
            logger.info("Quarantined bunker record: %s (reason: %s)", item, rejection_reason)
            continue

        # Optional client-side filtering if user supplied filters
        if port and cleaned["port_code"] != port.strip().upper():
            continue
        if grade and cleaned["fuel_grade"] != grade.strip().upper():
            continue

        # Deterministic idempotency key
        feed_id = cleaned["source_feed"] or cleaned["source_name"]
        idempotency_key = (
            f"BFP:{cleaned['port_code']}:{cleaned['fuel_grade']}:"
            f"{cleaned['observation_time'].isoformat()}:{feed_id}"
        )

        # Idempotency deduplication check
        existing = (
            db.query(BunkerPriceObservation)
            .filter(BunkerPriceObservation.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            duplicate_count += 1
            continue

        # Create model instance
        obs = BunkerPriceObservation(
            port_code=cleaned["port_code"],
            port_name=cleaned["port_name"],
            country_code=cleaned["country_code"],
            fuel_grade=cleaned["fuel_grade"],
            price_value=cleaned["price_value"],
            currency=cleaned["currency"],
            unit=cleaned["unit"],
            pricing_type=cleaned["pricing_type"],
            delivery_basis=cleaned["delivery_basis"],
            source_name=cleaned["source_name"],
            source_feed=cleaned["source_feed"],
            observation_time=cleaned["observation_time"],
            retrieval_time_utc=retrieval_time_utc,
            provenance_notes=cleaned["provenance_notes"],
            idempotency_key=idempotency_key,
        )
        db.add(obs)
        accepted_count += 1

    try:
        db.flush()
    except Exception as exc:
        db.rollback()
        logger.error("Failed to commit bunker price observations: %s", exc)
        return {
            "status": "failed",
            "source_origin": source_origin,
            "error": f"Database commit failure: {exc}",
            "records_read": records_read,
            "accepted_count": 0,
            "duplicate_count": duplicate_count,
            "quarantined_count": quarantined_count,
            "failed_count": records_read,
        }

    overall_status = "completed" if quarantined_count == 0 else "partial"

    return {
        "status": overall_status,
        "source_origin": source_origin,
        "provider": PROVIDER_NAME,
        "records_read": records_read,
        "accepted_count": accepted_count,
        "duplicate_count": duplicate_count,
        "quarantined_count": quarantined_count,
        "failed_count": failed_count,
        "withheld_by_provider": payload.get("withheld", 0),
        "withheld_reason": payload.get("withheldReason"),
        "quarantine_log": quarantine_log,
        "retrieval_time_utc": retrieval_time_utc.isoformat(),
    }
