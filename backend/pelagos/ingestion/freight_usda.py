"""
USDA AMS AgTransport Ocean Bulk Vessel Rates data adapter.
Connects to official SODA API endpoint https://agtransport.usda.gov/resource/ehs5-yac3.json
Traces to REQ-FR-DII-003, REQ-FR-DII-005, and REQ-NFR-DIP-001.
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime, date, timezone
import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

USDA_VESSEL_RATES_ENDPOINT = "https://agtransport.usda.gov/resource/ehs5-yac3.json"
DATASET_ID = "ehs5-yac3"
SOURCE_NAME = "USDA Agricultural Marketing Service / O'Neil Commodity Consulting"
LICENSE_TYPE = "U.S. Public Domain"

DEFAULT_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "usda_vessel_rates_sample.json"
)


class RawUSDAVesselRate(BaseModel):
    """Raw record model for USDA AgTransport Vessel Rates (ehs5-yac3)."""
    date: str
    month: Optional[str] = None
    year: Optional[str] = None
    gulf_to_japan: Optional[str] = None
    pnw_to_japan: Optional[str] = None
    gulf_pnw_spread: Optional[str] = None


class NormalizedFreightRecord(BaseModel):
    """Standardized freight rate observation ready for persistence."""
    route_code: str
    observation_date: date
    rate_value: float
    currency: str = "USD"
    unit_of_measure: str = "USD/MT"
    is_derived: bool = False
    rate_high: Optional[float] = None
    rate_low: Optional[float] = None
    observation_period: str = "monthly"
    source_name: str = SOURCE_NAME
    source_dataset_id: str = DATASET_ID
    publication_time: Optional[datetime] = None
    license_type: str = LICENSE_TYPE
    raw_payload: str
    idempotency_key: str


def parse_usda_observation_date(date_str: str) -> date:
    """Parses USDA date string (e.g. '2026-09-01T00:00:00.000' or '2026-09-01')."""
    if "T" in date_str:
        return datetime.fromisoformat(date_str.split("T")[0]).date()
    return datetime.fromisoformat(date_str).date()


def normalize_usda_record(raw: Dict[str, Any]) -> Tuple[List[NormalizedFreightRecord], int]:
    """
    Normalizes a single raw USDA vessel rate record into discrete route observations.
    Returns: Tuple[valid_records, unparseable_candidates_count].
    Candidate routes:
    1. USG-JPN-BULK: U.S. Gulf to Japan bulk grain rate ($/MT, is_derived=False)
    2. PNW-JPN-BULK: Pacific Northwest to Japan bulk grain rate ($/MT, is_derived=False)
    3. SPREAD-USG-PNW-JPN: Gulf vs PNW spread ($/MT, is_derived=True)
    """
    route_keys = ("gulf_to_japan", "pnw_to_japan", "gulf_pnw_spread")
    attempted_routes = sum(
        1 for k in route_keys if raw.get(k) is not None and str(raw.get(k)).strip() != ""
    )
    if attempted_routes == 0:
        attempted_routes = 1  # Minimum 1 candidate for an entirely empty or uninformative row

    date_val_str = raw.get("date")
    if not date_val_str:
        return [], attempted_routes

    try:
        obs_date = parse_usda_observation_date(date_val_str)
    except Exception:
        return [], attempted_routes

    results: List[NormalizedFreightRecord] = []
    unparseable = 0
    raw_json_str = json.dumps(raw, sort_keys=True)

    # 1. U.S. Gulf to Japan
    gulf_raw = raw.get("gulf_to_japan")
    if gulf_raw is not None and str(gulf_raw).strip() != "":
        try:
            gulf_rate = float(gulf_raw)
            if gulf_rate <= 0 or gulf_rate > 500.0:
                unparseable += 1
            else:
                results.append(
                    NormalizedFreightRecord(
                        route_code="USG-JPN-BULK",
                        observation_date=obs_date,
                        rate_value=gulf_rate,
                        currency="USD",
                        unit_of_measure="USD/MT",
                        is_derived=False,
                        observation_period="monthly",
                        source_name=SOURCE_NAME,
                        source_dataset_id=DATASET_ID,
                        license_type=LICENSE_TYPE,
                        raw_payload=raw_json_str,
                        idempotency_key=f"USDA:{DATASET_ID}:USG-JPN-BULK:{obs_date.isoformat()}",
                    )
                )
        except (ValueError, TypeError):
            unparseable += 1

    # 2. Pacific Northwest to Japan
    pnw_raw = raw.get("pnw_to_japan")
    if pnw_raw is not None and str(pnw_raw).strip() != "":
        try:
            pnw_rate = float(pnw_raw)
            if pnw_rate <= 0 or pnw_rate > 500.0:
                unparseable += 1
            else:
                results.append(
                    NormalizedFreightRecord(
                        route_code="PNW-JPN-BULK",
                        observation_date=obs_date,
                        rate_value=pnw_rate,
                        currency="USD",
                        unit_of_measure="USD/MT",
                        is_derived=False,
                        observation_period="monthly",
                        source_name=SOURCE_NAME,
                        source_dataset_id=DATASET_ID,
                        license_type=LICENSE_TYPE,
                        raw_payload=raw_json_str,
                        idempotency_key=f"USDA:{DATASET_ID}:PNW-JPN-BULK:{obs_date.isoformat()}",
                    )
                )
        except (ValueError, TypeError):
            unparseable += 1

    # 3. Gulf-PNW Spread (Derived Metric)
    spread_raw = raw.get("gulf_pnw_spread")
    if spread_raw is not None and str(spread_raw).strip() != "":
        try:
            spread_val = float(spread_raw)
            results.append(
                NormalizedFreightRecord(
                    route_code="SPREAD-USG-PNW-JPN",
                    observation_date=obs_date,
                    rate_value=spread_val,
                    currency="USD",
                    unit_of_measure="USD/MT",
                    is_derived=True,
                    observation_period="monthly",
                    source_name=SOURCE_NAME,
                    source_dataset_id=DATASET_ID,
                    license_type=LICENSE_TYPE,
                    raw_payload=raw_json_str,
                    idempotency_key=f"USDA:{DATASET_ID}:SPREAD-USG-PNW-JPN:{obs_date.isoformat()}",
                )
            )
        except (ValueError, TypeError):
            unparseable += 1

    return results, unparseable



def fetch_usda_vessel_rates_remote(
    limit: Optional[int] = None,
    since_date: Optional[str] = None,
    timeout: float = 15.0,
    app_token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetches genuine vessel rate observations from USDA SODA API endpoint.
    Applies explicit ordering by date descending.
    """
    params = {"$order": "date desc"}
    if limit:
        params["$limit"] = str(limit)
    if since_date:
        params["$where"] = f"date >= '{since_date}'"

    headers = {}
    if app_token:
        headers["X-App-Token"] = app_token

    try:
        response = httpx.get(
            USDA_VESSEL_RATES_ENDPOINT,
            params=params,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.error(f"Failed to fetch remote USDA vessel rates from {USDA_VESSEL_RATES_ENDPOINT}: {exc}")
        raise


def load_usda_vessel_rates_sample(fixture_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Loads genuine offline sample fixture for reproducible offline testing."""
    fpath = fixture_path or DEFAULT_FIXTURE_PATH
    if not fpath.exists():
        raise FileNotFoundError(f"USDA sample fixture not found at {fpath}")
    with open(fpath, "r", encoding="utf-8") as f:
        return json.load(f)
