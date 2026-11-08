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

# Approved anomaly reconciliation constants (Traces to C1.1)
APPROVED_RECONCILIATION_DATES = frozenset({
    date(2007, 10, 1),
    date(2007, 11, 1),
    date(2007, 12, 1),
    date(2008, 2, 1),
    date(2008, 3, 1),
    date(2008, 4, 1),
    date(2008, 5, 1),
    date(2008, 6, 1),
    date(2008, 7, 1),
    date(2008, 8, 1),
})

RECONCILIATION_RULE_VERSION = "USDA_EHS5_YAC3_DECIMAL_SHIFT_V1"
RECONCILIATION_FORMULA = "pnw_to_japan + gulf_pnw_spread"
RECONCILIATION_METHOD = "usda_ehs5_yac3_pnw_plus_spread"
RECONCILIATION_REASON = (
    "Historical decimal-shift anomaly observed in USDA AMS ehs5-yac3 "
    "when spot freight rate >= 100 USD/MT"
)
CORROBORATION_NOTE = (
    "Corroborated by DTN/UNCTAD 2008 mid-year peak rates exceeding 130 USD/MT "
    "and USDA spread conservation"
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

    # Reconciliation audit metadata (C1.1)
    is_reconstructed: bool = False
    original_rate_value: Optional[float] = None
    reconciliation_method: Optional[str] = None
    reconciliation_metadata: Optional[str] = None


class QuarantinedFreightRecord(BaseModel):
    """Payload for isolated invalid or inconsistent freight observations."""
    source_name: str = SOURCE_NAME
    source_partition: str = DATASET_ID
    raw_record_id: Optional[str] = None
    error_code: str
    error_details: str
    raw_payload: str


class NormalizationResult(tuple):
    """Tuple subclass returning (records, unparseable_count) while carrying quarantined records."""
    def __new__(cls, records: List[NormalizedFreightRecord], unparseable: int, quarantined: Optional[List[QuarantinedFreightRecord]] = None):
        return super().__new__(cls, (records, unparseable))

    def __init__(self, records: List[NormalizedFreightRecord], unparseable: int, quarantined: Optional[List[QuarantinedFreightRecord]] = None):
        self.records = records
        self.unparseable = unparseable
        self.quarantined = quarantined or []


def parse_usda_observation_date(date_str: str) -> date:
    """Parses USDA date string (e.g. '2026-09-01T00:00:00.000' or '2026-09-01')."""
    if "T" in date_str:
        return datetime.fromisoformat(date_str.split("T")[0]).date()
    return datetime.fromisoformat(date_str).date()


def normalize_usda_record(raw: Dict[str, Any]) -> NormalizationResult:
    """
    Normalizes a single raw USDA vessel rate record into discrete route observations.
    Enforces USDA consistency relationship: gulf_to_japan ≈ pnw_to_japan + gulf_pnw_spread.
    Reconciles approved 2007-2008 decimal-shift observations; quarantines unapproved discrepancies.
    Returns: NormalizationResult (unpacks as Tuple[valid_records, unparseable_count]).
    """
    route_keys = ("gulf_to_japan", "pnw_to_japan", "gulf_pnw_spread")
    attempted_routes = sum(
        1 for k in route_keys if raw.get(k) is not None and str(raw.get(k)).strip() != ""
    )
    if attempted_routes == 0:
        attempted_routes = 1  # Minimum 1 candidate for an entirely empty or uninformative row

    date_val_str = raw.get("date")
    if not date_val_str:
        return NormalizationResult([], attempted_routes)

    try:
        obs_date = parse_usda_observation_date(date_val_str)
    except Exception:
        return NormalizationResult([], attempted_routes)

    raw_json_str = json.dumps(raw, sort_keys=True)
    gulf_raw = raw.get("gulf_to_japan")
    pnw_raw = raw.get("pnw_to_japan")
    spread_raw = raw.get("gulf_pnw_spread")

    gulf_parsed: Optional[float] = None
    pnw_parsed: Optional[float] = None
    spread_parsed: Optional[float] = None
    unparseable = 0

    if gulf_raw is not None and str(gulf_raw).strip() != "":
        try:
            val = float(gulf_raw)
            if val <= 0 or val > 500.0:
                unparseable += 1
            else:
                gulf_parsed = val
        except (ValueError, TypeError):
            unparseable += 1

    if pnw_raw is not None and str(pnw_raw).strip() != "":
        try:
            val = float(pnw_raw)
            if val <= 0 or val > 500.0:
                unparseable += 1
            else:
                pnw_parsed = val
        except (ValueError, TypeError):
            unparseable += 1

    if spread_raw is not None and str(spread_raw).strip() != "":
        try:
            val = float(spread_raw)
            spread_parsed = val
        except (ValueError, TypeError):
            unparseable += 1

    # Consistency and reconciliation evaluation
    is_reconstructed_usg = False
    canonical_usg_rate = gulf_parsed
    original_usg_rate: Optional[float] = None
    reconcile_method: Optional[str] = None
    reconcile_metadata: Optional[str] = None
    quarantined_records: List[QuarantinedFreightRecord] = []

    if gulf_parsed is not None and pnw_parsed is not None and spread_parsed is not None:
        spread_diff = abs(round(gulf_parsed - pnw_parsed, 2) - round(spread_parsed, 2))
        if spread_diff <= 0.05:
            # Passes consistency check: retain source rate as canonical rate
            pass
        else:
            # Consistency failed: evaluate against approved anomaly rule
            reconstructed_usg = round(pnw_parsed + spread_parsed, 2)
            matches_approved_rule = (
                obs_date in APPROVED_RECONCILIATION_DATES
                and reconstructed_usg >= 100.0
                and abs((gulf_parsed * 10.0) - reconstructed_usg) <= 0.10
            )

            if matches_approved_rule:
                is_reconstructed_usg = True
                canonical_usg_rate = reconstructed_usg
                original_usg_rate = gulf_parsed
                reconcile_method = RECONCILIATION_METHOD
                audit_dict = {
                    "source_dataset_id": DATASET_ID,
                    "source_record_date": obs_date.isoformat(),
                    "route_code": "USG-JPN-BULK",
                    "original_gulf_to_japan": gulf_parsed,
                    "original_pnw_to_japan": pnw_parsed,
                    "original_gulf_pnw_spread": spread_parsed,
                    "reconstructed_canonical_value": canonical_usg_rate,
                    "reconciliation_formula": RECONCILIATION_FORMULA,
                    "reconciliation_rule": RECONCILIATION_RULE_VERSION,
                    "reconciliation_reason": RECONCILIATION_REASON,
                    "reconciled_at_utc": datetime.now(timezone.utc).isoformat(),
                    "status": "reconstructed_canonical",
                    "is_directly_reported": False,
                    "corroboration": CORROBORATION_NOTE,
                }
                reconcile_metadata = json.dumps(audit_dict, sort_keys=True)
                logger.info(
                    f"Reconstructed canonical rate for {obs_date.isoformat()} USG-JPN-BULK: "
                    f"original={gulf_parsed}, canonical={canonical_usg_rate}"
                )
            else:
                # Inconsistency does not match approved rule: quarantine rather than reconstruct
                quarantined_records.append(
                    QuarantinedFreightRecord(
                        source_name=SOURCE_NAME,
                        source_partition=f"{DATASET_ID}:{obs_date.isoformat()}",
                        raw_record_id=f"USDA:{DATASET_ID}:{obs_date.isoformat()}",
                        error_code="ERR_FREIGHT_CONSERVATION_VIOLATION",
                        error_details=(
                            f"Freight conservation check failed: gulf ({gulf_parsed}) - pnw ({pnw_parsed}) "
                            f"!= spread ({spread_parsed}), diff={spread_diff:.2f}. "
                            f"Record does not match approved anomaly reconciliation criteria."
                        ),
                        raw_payload=raw_json_str,
                    )
                )

    # If quarantined due to unapproved consistency failure, abort candidate emission
    if quarantined_records:
        return NormalizationResult([], attempted_routes, quarantined=quarantined_records)

    results: List[NormalizedFreightRecord] = []

    # 1. U.S. Gulf to Japan
    if canonical_usg_rate is not None:
        results.append(
            NormalizedFreightRecord(
                route_code="USG-JPN-BULK",
                observation_date=obs_date,
                rate_value=canonical_usg_rate,
                currency="USD",
                unit_of_measure="USD/MT",
                is_derived=False,
                observation_period="monthly",
                source_name=SOURCE_NAME,
                source_dataset_id=DATASET_ID,
                license_type=LICENSE_TYPE,
                raw_payload=raw_json_str,
                idempotency_key=f"USDA:{DATASET_ID}:USG-JPN-BULK:{obs_date.isoformat()}",
                is_reconstructed=is_reconstructed_usg,
                original_rate_value=original_usg_rate,
                reconciliation_method=reconcile_method,
                reconciliation_metadata=reconcile_metadata,
            )
        )

    # 2. Pacific Northwest to Japan
    if pnw_parsed is not None:
        results.append(
            NormalizedFreightRecord(
                route_code="PNW-JPN-BULK",
                observation_date=obs_date,
                rate_value=pnw_parsed,
                currency="USD",
                unit_of_measure="USD/MT",
                is_derived=False,
                observation_period="monthly",
                source_name=SOURCE_NAME,
                source_dataset_id=DATASET_ID,
                license_type=LICENSE_TYPE,
                raw_payload=raw_json_str,
                idempotency_key=f"USDA:{DATASET_ID}:PNW-JPN-BULK:{obs_date.isoformat()}",
                is_reconstructed=False,
                original_rate_value=None,
                reconciliation_method=None,
                reconciliation_metadata=None,
            )
        )

    # 3. Gulf-PNW Spread (Derived Metric)
    if spread_parsed is not None:
        results.append(
            NormalizedFreightRecord(
                route_code="SPREAD-USG-PNW-JPN",
                observation_date=obs_date,
                rate_value=spread_parsed,
                currency="USD",
                unit_of_measure="USD/MT",
                is_derived=True,
                observation_period="monthly",
                source_name=SOURCE_NAME,
                source_dataset_id=DATASET_ID,
                license_type=LICENSE_TYPE,
                raw_payload=raw_json_str,
                idempotency_key=f"USDA:{DATASET_ID}:SPREAD-USG-PNW-JPN:{obs_date.isoformat()}",
                is_reconstructed=False,
                original_rate_value=None,
                reconciliation_method=None,
                reconciliation_metadata=None,
            )
        )

    return NormalizationResult(results, unparseable, quarantined=quarantined_records)



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
