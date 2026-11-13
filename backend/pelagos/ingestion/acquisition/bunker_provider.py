"""Pelagos Bunker Fuel Prices External Acquisition & Hardening Pipeline.

Connects to Bunker Fuel Prices (bunkerfuelprices.com) public pricing API,
enforces strict commercial and currency data integrity, rejects synthetic or
anomalous prices, normalizes genuine marine fuel grades, validates port identities
without geographic substitution, and persists genuine market observations with
complete provenance and quarantine isolation.

Traces to DATA-001 Section 5.2, REQ-NFR-DIP-001, and Stage F1.4.2.1 mandates:
- Genuine external market prices only; never synthesize prices.
- Currency integrity: inspects source tickers and metadata; strictly rejects CNY/RMB as USD.
- Mass unit integrity: validates USD/MT; rejects volumetric USD/BBL and USD/GAL without density.
- Fuel grade normalization: controls VLSFO, HSFO, MGO, MDO, and LNG; keeps MDO distinct from MGO.
- Anti-substitution: strictly validates port UN/LOCODE; never substitutes nearby ports or benchmarks.
- Observation freshness: classifies into fresh (<=24h), recent (<=168h), and stale (>168h).
- Withholding transparency: tracks provider published vs withheld counts and reasons.
- Quarantine isolation: isolates invalid records without rolling back valid records.
- Deterministic idempotency: guarantees repeatable non-destructive ingestion.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from pelagos.database import ensure_schema_compatibility
from pelagos.ingestion.acquisition.client import (
    AcquisitionError,
    AcquisitionHTTPError,
    AcquisitionNetworkError,
    AcquisitionTimeoutError,
    ResilientHttpClient,
)
from pelagos.models import BunkerPriceObservation, QuarantinedRecord

logger = logging.getLogger(__name__)

DEFAULT_BUNKER_API_URL = "https://bunkerfuelprices.com/api/v1/prices"
PROVIDER_NAME = "Bunker Fuel Prices (bunkerfuelprices.com)"
DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "genuine_bunker_prices.json"

# ---------------------------------------------------------------------------
# Provider Licensing & Terms of Use Compliance (F1.4.1 / F1.4.2.1 Audit)
# ---------------------------------------------------------------------------
PROVIDER_LICENSING_STATUS = "LICENSE_RESTRICTED"
PROVIDER_PERMISSION_STATUS = "PENDING_PROVIDER_PERMISSION"
OPERATIONAL_AVAILABILITY_STATUS = "UNAVAILABLE"
OPERATIONAL_USE_PERMITTED = False
DATA_GOVERNANCE_ROLE = "HISTORICAL_AUDIT_EVIDENCE"

# Statutory MARPOL Annex VI Reference Limits (Statutory Ceilings, NOT inherent fuel grade attributes)
MARPOL_ANNEX_VI_GLOBAL_CAP_PCT = 0.50       # Global Sulfur Cap (Regulation 14.1) effective 1 January 2020
MARPOL_ANNEX_VI_ECA_LIMIT_PCT = 0.10        # Emission Control Area Limit (Regulation 14.4) effective 1 January 2015
MARPOL_HISTORICAL_PRE_2020_CAP_PCT = 3.50   # Pre-2020 Global Sulfur Cap (Regulation 14.1 historical)

PROVIDER_PERMITTED_USE: Dict[str, Any] = {
    "automated_api_retrieval": False,
    "repeated_polling": False,
    "retention_of_observations": False,
    "internal_processing_display": "conditional_evaluation_only",
    "competing_service_usage": False,
    "status": "PENDING_PROVIDER_PERMISSION",
    "operational_availability": OPERATIONAL_AVAILABILITY_STATUS,
    "operational_use_permitted": OPERATIONAL_USE_PERMITTED,
    "data_governance_role": DATA_GOVERNANCE_ROLE,
    "terms_reference": "bunkerfuelprices.com Terms of Service Section 05 (prohibits bulk extraction for competing services)",
}

# In-memory quarantine audit log across ingestion runs
_GLOBAL_QUARANTINE_AUDIT_LOG: List[Dict[str, Any]] = []

# ---------------------------------------------------------------------------
# Controlled Controlled Mappings: Marine Fuel Grades
# ---------------------------------------------------------------------------

KNOWN_BUNKER_GRADES: Set[str] = {
    "VLSFO",
    "HSFO",
    "MGO",
    "LSMGO",
    "MDO",
    "IFO380",
    "IFO180",
    "ULSFO",
    "B24",
    "B30",
    "LNG",
}

# Controlled Grade Normalization Map: preserves exact ISO 8217 category and nominal specifications
# Note: nominal_max_sulfur_spec is the standard ISO 8217 / trade grade benchmark ceiling.
# Statutory MARPOL compliance is NOT a static attribute of a fuel grade; see regulatory_assessment.
GRADE_NORMALIZATION_MAP: Dict[str, Dict[str, Any]] = {
    "VLSFO": {
        "canonical": "VLSFO",
        "category": "VLSFO",
        "nominal_max_sulfur_spec": 0.50,
        "max_sulfur_pct": 0.50,
        "nominal_iso_reference": "ISO 8217 RMG/RME 0.5% S",
        "iso_designation": "ISO 8217 RMG/RME 0.5% S",
        "fuel_type": "RESIDUAL_BLEND",
    },
    "VLSFO_05": {
        "canonical": "VLSFO",
        "category": "VLSFO",
        "nominal_max_sulfur_spec": 0.50,
        "max_sulfur_pct": 0.50,
        "nominal_iso_reference": "ISO 8217 RMG/RME 0.5% S",
        "iso_designation": "ISO 8217 RMG/RME 0.5% S",
        "fuel_type": "RESIDUAL_BLEND",
    },
    "VLSFO 0.5%": {
        "canonical": "VLSFO",
        "category": "VLSFO",
        "nominal_max_sulfur_spec": 0.50,
        "max_sulfur_pct": 0.50,
        "nominal_iso_reference": "ISO 8217 RMG/RME 0.5% S",
        "iso_designation": "ISO 8217 RMG/RME 0.5% S",
        "fuel_type": "RESIDUAL_BLEND",
    },
    "HSFO": {
        "canonical": "HSFO",
        "category": "HSFO",
        "nominal_max_sulfur_spec": 3.50,
        "max_sulfur_pct": 3.50,
        "nominal_iso_reference": "ISO 8217 RMG 380 3.5% S",
        "iso_designation": "ISO 8217 RMG 380 3.5% S",
        "fuel_type": "RESIDUAL",
    },
    "IFO380": {
        "canonical": "HSFO",
        "category": "HSFO",
        "nominal_max_sulfur_spec": 3.50,
        "max_sulfur_pct": 3.50,
        "nominal_iso_reference": "ISO 8217 RMG 380",
        "iso_designation": "ISO 8217 RMG 380",
        "fuel_type": "RESIDUAL",
    },
    "IFO 380": {
        "canonical": "HSFO",
        "category": "HSFO",
        "nominal_max_sulfur_spec": 3.50,
        "max_sulfur_pct": 3.50,
        "nominal_iso_reference": "ISO 8217 RMG 380",
        "iso_designation": "ISO 8217 RMG 380",
        "fuel_type": "RESIDUAL",
    },
    "IFO_380": {
        "canonical": "HSFO",
        "category": "HSFO",
        "nominal_max_sulfur_spec": 3.50,
        "max_sulfur_pct": 3.50,
        "nominal_iso_reference": "ISO 8217 RMG 380",
        "iso_designation": "ISO 8217 RMG 380",
        "fuel_type": "RESIDUAL",
    },
    "HFO": {
        "canonical": "HSFO",
        "category": "HSFO",
        "nominal_max_sulfur_spec": 3.50,
        "max_sulfur_pct": 3.50,
        "nominal_iso_reference": "ISO 8217 Heavy Fuel Oil",
        "iso_designation": "ISO 8217 Heavy Fuel Oil",
        "fuel_type": "RESIDUAL",
    },
    "HFO_380": {
        "canonical": "HSFO",
        "category": "HSFO",
        "nominal_max_sulfur_spec": 3.50,
        "max_sulfur_pct": 3.50,
        "nominal_iso_reference": "ISO 8217 RMG 380",
        "iso_designation": "ISO 8217 RMG 380",
        "fuel_type": "RESIDUAL",
    },
    "LSFO_380": {
        "canonical": "HSFO",
        "category": "HSFO",
        "nominal_max_sulfur_spec": 3.50,
        "max_sulfur_pct": 3.50,
        "nominal_iso_reference": "ISO 8217 RMG 380",
        "iso_designation": "ISO 8217 RMG 380",
        "fuel_type": "RESIDUAL",
    },
    "IFO180": {
        "canonical": "HSFO",
        "category": "HSFO",
        "nominal_max_sulfur_spec": 3.50,
        "max_sulfur_pct": 3.50,
        "nominal_iso_reference": "ISO 8217 RMD 180",
        "iso_designation": "ISO 8217 RMD 180",
        "fuel_type": "RESIDUAL",
    },
    "IFO 180": {
        "canonical": "HSFO",
        "category": "HSFO",
        "nominal_max_sulfur_spec": 3.50,
        "max_sulfur_pct": 3.50,
        "nominal_iso_reference": "ISO 8217 RMD 180",
        "iso_designation": "ISO 8217 RMD 180",
        "fuel_type": "RESIDUAL",
    },
    "MGO": {
        "canonical": "MGO",
        "category": "MGO",
        "nominal_max_sulfur_spec": 0.10,
        "max_sulfur_pct": 0.10,
        "nominal_iso_reference": "ISO 8217 DMA (Low Sulfur)",
        "iso_designation": "ISO 8217 DMA (Low Sulfur)",
        "fuel_type": "DISTILLATE",
    },
    "LSMGO": {
        "canonical": "MGO",
        "category": "MGO",
        "nominal_max_sulfur_spec": 0.10,
        "max_sulfur_pct": 0.10,
        "nominal_iso_reference": "ISO 8217 DMA (0.10% S ECA)",
        "iso_designation": "ISO 8217 DMA (0.10% S ECA)",
        "fuel_type": "DISTILLATE",
    },
    "LSMGO_01": {
        "canonical": "MGO",
        "category": "MGO",
        "nominal_max_sulfur_spec": 0.10,
        "max_sulfur_pct": 0.10,
        "nominal_iso_reference": "ISO 8217 DMA (0.10% S)",
        "iso_designation": "ISO 8217 DMA (0.10% S)",
        "fuel_type": "DISTILLATE",
    },
    "DMA": {
        "canonical": "MGO",
        "category": "MGO",
        "nominal_max_sulfur_spec": 0.10,
        "max_sulfur_pct": 0.10,
        "nominal_iso_reference": "ISO 8217 DMA",
        "iso_designation": "ISO 8217 DMA",
        "fuel_type": "DISTILLATE",
    },
    "DMZ": {
        "canonical": "MGO",
        "category": "MGO",
        "nominal_max_sulfur_spec": 0.10,
        "max_sulfur_pct": 0.10,
        "nominal_iso_reference": "ISO 8217 DMZ",
        "iso_designation": "ISO 8217 DMZ",
        "fuel_type": "DISTILLATE",
    },
    "MDO": {
        "canonical": "MDO",
        "category": "MDO",
        "nominal_max_sulfur_spec": 1.50,
        "max_sulfur_pct": 1.50,
        "nominal_iso_reference": "ISO 8217 DMB (Marine Diesel Oil)",
        "iso_designation": "ISO 8217 DMB (Marine Diesel Oil)",
        "fuel_type": "DISTILLATE_HEAVY",
    },
    "DMB": {
        "canonical": "MDO",
        "category": "MDO",
        "nominal_max_sulfur_spec": 1.50,
        "max_sulfur_pct": 1.50,
        "nominal_iso_reference": "ISO 8217 DMB",
        "iso_designation": "ISO 8217 DMB",
        "fuel_type": "DISTILLATE_HEAVY",
    },
    "ULSFO": {
        "canonical": "ULSFO",
        "category": "ULSFO",
        "nominal_max_sulfur_spec": 0.10,
        "max_sulfur_pct": 0.10,
        "nominal_iso_reference": "ISO 8217 RMD 80 (ULSFO 0.1% S)",
        "iso_designation": "ISO 8217 RMD 80 (ULSFO 0.1% S)",
        "fuel_type": "RESIDUAL_LIGHT",
    },
    "B24": {
        "canonical": "B24",
        "category": "BIO_BUNKER",
        "nominal_max_sulfur_spec": 0.50,
        "max_sulfur_pct": 0.50,
        "nominal_iso_reference": "Bio-Bunker B24 (24% UCOME / 76% VLSFO)",
        "iso_designation": "Bio-Bunker B24 (24% UCOME / 76% VLSFO)",
        "fuel_type": "BIO_BLEND",
    },
    "B30": {
        "canonical": "B30",
        "category": "BIO_BUNKER",
        "nominal_max_sulfur_spec": 0.50,
        "max_sulfur_pct": 0.50,
        "nominal_iso_reference": "Bio-Bunker B30 (30% UCOME / 70% VLSFO)",
        "iso_designation": "Bio-Bunker B30 (30% UCOME / 70% VLSFO)",
        "fuel_type": "BIO_BLEND",
    },
    "LNG": {
        "canonical": "LNG",
        "category": "LNG",
        "nominal_max_sulfur_spec": 0.00,
        "max_sulfur_pct": 0.00,
        "nominal_iso_reference": "ISO 23306 Liquefied Natural Gas",
        "iso_designation": "ISO 23306 Liquefied Natural Gas",
        "fuel_type": "CRYOGENIC_GAS",
    },
}

# Generic non-bunker and ambiguous fuel terms that must be strictly rejected
REJECTED_GENERIC_FUELS: Dict[str, str] = {
    "BRENT": "Crude oil benchmark (Brent) is not a delivered marine bunker fuel",
    "BRENT_CRUDE": "Crude oil benchmark (Brent) is not a delivered marine bunker fuel",
    "WTI": "Crude oil benchmark (WTI) is not a delivered marine bunker fuel",
    "CRUDE": "Crude oil is not a delivered marine bunker fuel",
    "CRUDE_OIL": "Crude oil is not a delivered marine bunker fuel",
    "DIESEL": "Retail road diesel is not a certified marine bunker fuel",
    "ROAD_DIESEL": "Retail road diesel is not a certified marine bunker fuel",
    "RETAIL_DIESEL": "Retail road diesel is not a certified marine bunker fuel",
    "AUTOMOTIVE_DIESEL": "Retail automotive diesel is not a certified marine bunker fuel",
    "ULSD": "Ultra-low sulfur road diesel is not a certified marine bunker fuel",
    "GASOLINE": "Gasoline/motor spirit is not a marine bunker fuel",
    "JET_FUEL": "Kerosene/jet fuel is not a marine bunker fuel",
    "KEROSENE": "Kerosene is not a marine bunker fuel",
    "HEATING_OIL": "Domestic heating oil is not a certified marine bunker fuel",
    "FUEL_OIL": "Ambiguous generic fuel description without grade or sulfur specification",
    "RESIDUAL": "Ambiguous generic residual description without ISO 8217 specification",
    "BUNKER": "Ambiguous generic bunker description without fuel grade",
    "HEAVY_FUEL": "Ambiguous heavy fuel description without ISO 8217 grade specification",
}

# Supported vs Unsupported Units
SUPPORTED_PRICE_UNITS: Set[str] = {
    "USD/MT",
    "$/MT",
    "MT",
    "USD/T",
    "USD/TONNE",
    "USD/METRIC TON",
}

UNSUPPORTED_PRICE_UNITS: Dict[str, str] = {
    "USD/BBL": "Unsupported volumetric unit (USD/BBL); conversion to mass requires certified API gravity and temperature density",
    "$/BBL": "Unsupported volumetric unit ($/BBL); conversion to mass requires certified API gravity and temperature density",
    "BBL": "Unsupported volumetric unit (BBL); conversion to mass requires certified API gravity and temperature density",
    "USD/GAL": "Unsupported volumetric unit (USD/GAL); volumetric gallons cannot be converted to mass without certified density",
    "$/GAL": "Unsupported volumetric unit ($/GAL); volumetric gallons cannot be converted to mass without certified density",
    "GAL": "Unsupported volumetric unit (GAL); volumetric gallons cannot be converted to mass without certified density",
    "USD/M3": "Unsupported volumetric unit (USD/M3); cubic meters require temperature-compensated density to convert to metric tonnes",
    "M3": "Unsupported volumetric unit (M3); cubic meters require temperature-compensated density to convert to metric tonnes",
}

VALID_DELIVERY_BASES: Set[str] = {"delivered", "ex_wharf", "fob"}

# Canonical Port Dictionary for Deterministic Identity Validation
KNOWN_CANONICAL_PORTS: Dict[str, Dict[str, Any]] = {
    "USSEA": {"name": "Seattle", "country": "US", "region": "US Pacific Northwest"},
    "USPDX": {"name": "Portland", "country": "US", "region": "US Pacific Northwest"},
    "USHOU": {"name": "Houston", "country": "US", "region": "US Gulf Coast"},
    "USMSY": {"name": "New Orleans", "country": "US", "region": "US Gulf Coast"},
    "JPYOK": {"name": "Yokohama", "country": "JP", "region": "Japan"},
    "ARBUE": {"name": "Buenos Aires", "country": "AR", "region": "South America East Coast"},
    "DKSKA": {"name": "Skagen", "country": "DK", "region": "North Sea / Baltic"},
    "SGSIN": {"name": "Singapore", "country": "SG", "region": "Southeast Asia"},
    "NLRTM": {"name": "Rotterdam", "country": "NL", "region": "ARA Range"},
    "AEFJR": {"name": "Fujairah", "country": "AE", "region": "Middle East Gulf"},
    "KRPUS": {"name": "Busan", "country": "KR", "region": "East Asia"},
    "GIGIB": {"name": "Gibraltar", "country": "GI", "region": "Western Mediterranean"},
    "MTMLA": {"name": "Malta", "country": "MT", "region": "Central Mediterranean"},
    "CNZOS": {"name": "Zhoushan", "country": "CN", "region": "East Asia"},
    "CNSGH": {"name": "Shanghai", "country": "CN", "region": "East Asia"},
    "USLAX": {"name": "Los Angeles", "country": "US", "region": "US West Coast"},
    "USNYC": {"name": "New York", "country": "US", "region": "US East Coast"},
    "BEANR": {"name": "Antwerp", "country": "BE", "region": "ARA Range"},
    "DEHAM": {"name": "Hamburg", "country": "DE", "region": "North Sea"},
    "GRPIR": {"name": "Piraeus", "country": "GR", "region": "Eastern Mediterranean"},
    "ESVLC": {"name": "Valencia", "country": "ES", "region": "Western Mediterranean"},
    "ESALG": {"name": "Algeciras", "country": "ES", "region": "Western Mediterranean"},
    "ESLPA": {"name": "Las Palmas", "country": "ES", "region": "Atlantic Islands"},
    "TRIST": {"name": "Istanbul", "country": "TR", "region": "Black Sea / Marmara"},
    "BRSSZ": {"name": "Santos", "country": "BR", "region": "South America East Coast"},
    "PAPAC": {"name": "Balboa / Cristobal (Panama)", "country": "PA", "region": "Panama Canal"},
}

PRIORITY_CORRIDOR_HUBS: Set[str] = {"USSEA", "USPDX", "USHOU", "USMSY", "JPYOK"}


# ---------------------------------------------------------------------------
# Helper Utility Functions
# ---------------------------------------------------------------------------

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


def evaluate_bunker_freshness(
    obs_time: Optional[datetime.datetime],
    ref_time: Optional[datetime.datetime] = None,
) -> Tuple[str, float]:
    """Classify observation age into deterministic freshness tiers.

    Tiers:
    - fresh: age <= 24.0 hours (active daily market)
    - recent: 24.0 < age <= 168.0 hours (within standard weekly trading week)
    - stale: age > 168.0 hours (older than 7 days)
    - unknown: observation timestamp missing
    """
    if obs_time is None:
        return "unknown", -1.0

    ref = ref_time or datetime.datetime.now(datetime.timezone.utc)
    if obs_time.tzinfo is None:
        obs_time = obs_time.replace(tzinfo=datetime.timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=datetime.timezone.utc)

    diff = (ref - obs_time).total_seconds()
    age_hours = round(max(0.0, diff / 3600.0), 2)

    if age_hours <= 24.0:
        return "fresh", age_hours
    elif age_hours <= 168.0:
        return "recent", age_hours
    else:
        return "stale", age_hours


# ---------------------------------------------------------------------------
# Core Record Validation Engine
# ---------------------------------------------------------------------------

def validate_bunker_record(raw: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """Validate a single marine bunker price record from provider payload.

    Enforces commercial, dimensional, and currency integrity:
    - 5-letter UN/LOCODE port identifier with strict anti-substitution.
    - Controlled marine fuel grade normalization; keeps MDO distinct from MGO.
    - Positive numeric price in USD/MT.
    - Currency integrity: inspects source ticker and metadata; rejects CNY/RMB as USD.
    - Unit integrity: rejects volumetric barrels (USD/BBL) and gallons (USD/GAL).
    - Anomaly detection: separates hard rejections from review warnings.
    - Preserves observation time, delivery basis, and pricing type.

    Returns:
        (is_valid, cleaned_dict, rejection_reason)
    """
    if not isinstance(raw, dict):
        return False, None, "Record is not a dictionary"

    # 1. Port Identification & UN/LOCODE Validation
    raw_locode = str(raw.get("locode") or raw.get("port_code") or "").strip().upper()
    if not raw_locode or len(raw_locode) != 5 or not raw_locode.isalpha():
        return False, None, f"Invalid UN/LOCODE port code: '{raw_locode}' (must be 5 alphabetic characters)"

    port_code = raw_locode
    port_name = str(raw.get("port") or raw.get("port_name") or port_code).strip()
    country_code = str(raw.get("country") or raw.get("country_code") or "").strip().upper() or None

    # Resolve against canonical port dictionary if available
    canonical_info = KNOWN_CANONICAL_PORTS.get(port_code)
    if canonical_info:
        port_name = canonical_info["name"]
        country_code = country_code or canonical_info["country"]

    # 2. Fuel Grade Normalization
    raw_grade = str(raw.get("grade") or raw.get("fuel_grade") or "").strip().upper()
    if not raw_grade:
        return False, None, "Missing fuel grade identifier"

    # Check for explicitly rejected generic or non-marine fuels
    if raw_grade in REJECTED_GENERIC_FUELS:
        return False, None, f"Unrecognized or unsupported fuel grade: '{raw_grade}' ({REJECTED_GENERIC_FUELS[raw_grade]})"

    # Match in controlled normalization map
    grade_meta = GRADE_NORMALIZATION_MAP.get(raw_grade)
    if not grade_meta:
        # Check if known in standard set
        if raw_grade in KNOWN_BUNKER_GRADES:
            canonical_grade = raw_grade
            grade_meta = {
                "canonical": raw_grade,
                "category": raw_grade,
                "nominal_max_sulfur_spec": None,
                "max_sulfur_pct": None,
                "nominal_iso_reference": f"Marine Grade {raw_grade}",
                "iso_designation": f"Marine Grade {raw_grade}",
                "fuel_type": "MARINE_FUEL",
            }
        else:
            return False, None, f"Unrecognized or unsupported fuel grade: '{raw_grade}'"
    else:
        canonical_grade = grade_meta["canonical"]

    # 3. Numeric Price Validation
    raw_price = raw.get("usdPerMt")
    if raw_price is None:
        raw_price = raw.get("amount") or raw.get("price")
    if raw_price is None:
        return False, None, "STRUCTURAL_INVALID_PRICE: Missing price value"

    try:
        price_val = float(raw_price)
    except (ValueError, TypeError):
        return False, None, f"STRUCTURAL_INVALID_PRICE: Non-numeric price value: '{raw_price}'"

    if price_val <= 0.0:
        return False, None, f"NON_POSITIVE_PRICE: Price value must be positive: {price_val}"

    # 4. Pricing Unit Validation
    raw_unit = str(raw.get("unit") or raw.get("price_unit") or "").strip().upper()
    if raw_unit:
        if raw_unit in UNSUPPORTED_PRICE_UNITS:
            return False, None, UNSUPPORTED_PRICE_UNITS[raw_unit]
        if raw_unit not in SUPPORTED_PRICE_UNITS and not any(u in raw_unit for u in ("MT", "TONNE")):
            return False, None, f"Unsupported pricing unit: '{raw_unit}' (expected mass unit USD/MT)"

    # 5. Currency Integrity & Contradiction Detection
    source_feed = str(raw.get("source") or raw.get("source_feed") or "").strip()
    raw_currency = str(raw.get("currency") or "").strip().upper()

    # Ticker suffix inspection
    feed_upper = source_feed.upper()
    has_cny_ticker = any(feed_upper.endswith(sfx) for sfx in ("_CNY", "_RMB", "CNY", "RMB")) or "_CNY_" in feed_upper
    has_cny_currency = raw_currency in ("CNY", "RMB")

    # Explicit CNY indicators must NEVER be accepted as USD
    if has_cny_ticker or has_cny_currency:
        return False, None, (
            f"Currency mismatch detected: source feed '{source_feed}' / currency '{raw_currency}' "
            f"quotes in CNY but reported as usdPerMt ({price_val}) without validated exchange conversion"
        )

    # Check for other non-USD currency tickers (e.g. _EUR, _JPY, _GBP, _SGD)
    non_usd_matches = re.findall(r"_(EUR|GBP|JPY|SGD|AUD|CAD|CHF|KRW|INR)$", feed_upper)
    if non_usd_matches and raw_currency != "USD":
        non_usd_curr = non_usd_matches[0]
        return False, None, (
            f"Currency contradiction: source feed '{source_feed}' indicates {non_usd_curr} currency "
            "without documented conversion formula"
        )

    # Missing currency handling
    if "currency" in raw and not raw_currency and not raw.get("usdPerMt"):
        return False, None, "Missing explicit currency specification; cannot assume USD without verified currency metadata"

    # Conflicting currency indicators
    if raw_currency and raw_currency not in ("USD", "$") and raw.get("usdPerMt") is not None:
        return False, None, (
            f"Conflicting currency indicators: metadata currency '{raw_currency}' "
            f"contradicts usdPerMt field value ({price_val})"
        )

    # 6. Price Review & Quarantine Classification
    # Strictly separates:
    # - Structurally invalid prices (handled in step 3 above)
    # - Non-positive prices (handled in step 3 above)
    # - Currency & unit contradictions (handled in steps 4 & 5 above)
    # - Unusual but plausible market prices ($3,000–$5,000/MT, $50–$150/MT): accepted with warning
    # - Extreme prices requiring manual review (> $5,000/MT, < $50/MT): quarantined for manual review
    anomaly_warnings: List[str] = []
    if price_val > 5000.0:
        return False, None, (
            f"EXTREME_PRICE_REQUIRES_MANUAL_REVIEW: Price of {price_val:.2f} USD/MT exceeds "
            "standard operational review threshold ($5,000/MT) and is quarantined for manual inspection "
            "rather than ingested into canonical observations"
        )
    elif price_val < 50.0:
        return False, None, (
            f"EXTREME_PRICE_REQUIRES_MANUAL_REVIEW: Price of {price_val:.2f} USD/MT is below "
            "standard operational review floor ($50/MT) and is quarantined for manual inspection"
        )
    elif price_val > 3000.0:
        anomaly_warnings.append(
            f"UNUSUAL_PRICE_LEVEL (ANOMALY_WARNING): Price ({price_val:.2f} USD/MT) is unusually high relative to historical norms "
            "but retained as genuine market quotation"
        )
    elif price_val < 150.0:
        anomaly_warnings.append(
            f"UNUSUAL_PRICE_LEVEL (ANOMALY_WARNING): Price ({price_val:.2f} USD/MT) is unusually low relative to typical market floors"
        )

    # 7. Observation Timestamp Validation
    obs_raw = raw.get("observedAt") or raw.get("observation_time") or raw.get("timestamp")
    obs_time = parse_iso_datetime(obs_raw)
    if obs_time is None:
        return False, None, f"Invalid or unparseable observation timestamp: '{obs_raw}'"

    # 8. Delivery Basis & Pricing Type
    raw_basis = raw.get("basis") or raw.get("delivery_basis")
    delivery_basis = None
    if raw_basis:
        cleaned_basis = str(raw_basis).strip().lower()
        if cleaned_basis in VALID_DELIVERY_BASES:
            delivery_basis = cleaned_basis
        else:
            delivery_basis = cleaned_basis  # strictly preserve provider string

    pricing_type = str(raw.get("kind") or raw.get("pricing_type") or "indicative").strip().lower()

    # 9. Fuel Specification Semantics (Rigorous Concept Demarcation)
    # Extracts source-reported values independently from statutory ceilings or nominal references:
    # 1. Source-reported sulfur content (from Certificate of Quality / BDN if provided)
    # 2. Fuel grade designation (canonical grade)
    # 3. ISO 8217 specification (nominal max sulfur specification limit)
    # 4. Global MARPOL sulfur limit (0.50% S post-IMO 2020)
    # 5. Applicable ECA sulfur limit (0.10% S)
    # 6. Compliance arrangements (e.g. approved EGCS / scrubber equivalence under MARPOL Annex VI Reg 4)
    source_reported_iso = raw.get("iso_designation") or raw.get("specification") or raw.get("spec")
    if source_reported_iso is not None:
        source_reported_iso = str(source_reported_iso).strip() or None

    source_reported_sulfur: Optional[float] = None
    raw_sulfur = raw.get("sulfur_pct") or raw.get("sulfur_content") or raw.get("sulfur")
    if raw_sulfur is not None:
        try:
            source_reported_sulfur = float(raw_sulfur)
        except (ValueError, TypeError):
            source_reported_sulfur = None

    verified_blend_comp = raw.get("blend_composition") or raw.get("verified_blend")
    if verified_blend_comp is not None:
        verified_blend_comp = str(verified_blend_comp).strip() or None

    nominal_max_sulfur = grade_meta.get("nominal_max_sulfur_spec") or grade_meta.get("max_sulfur_pct")
    nominal_iso_ref = grade_meta.get("nominal_iso_reference") or grade_meta.get("iso_designation")

    # Demarcate statutory MARPOL limits and compliance arrangements from fuel grade.
    # Universal regulatory compliance CANNOT be assigned solely from fuel grade:
    # - Route location / geographic coordinates (ECA vs global high seas)
    # - Operating date (post-2020 vs pre-2020)
    # - Vessel compliance arrangements (MARPOL Annex VI Reg 4 equivalence: approved EGCS / scrubber enables HSFO)
    # When route location, operating date, or compliance status is unavailable,
    # return an explicitly unknown or context-dependent regulatory assessment.
    regulatory_assessment = {
        "status": "CONTEXT_DEPENDENT",
        "global_marpol_sulfur_limit_pct": MARPOL_ANNEX_VI_GLOBAL_CAP_PCT,
        "applicable_eca_sulfur_limit_pct": MARPOL_ANNEX_VI_ECA_LIMIT_PCT,
        "compliance_arrangement": "UNKNOWN",
        "route_location_context": "UNAVAILABLE",
        "operating_date_context": obs_time.isoformat() if obs_time else "UNAVAILABLE",
        "scrubber_equivalence_applicable": True if canonical_grade == "HSFO" else False,
        "notes": (
            "Universal regulatory compliance cannot be assigned solely from fuel grade. "
            "Under MARPOL Annex VI Regulation 14, global cap is 0.50% S and ECA limit is 0.10% S. "
            "HSFO (>0.50% S) is compliant globally and within ECAs if vessel is equipped with an "
            "approved exhaust gas cleaning system (EGCS / scrubber) under MARPOL Annex VI Regulation 4. "
            "VLSFO (0.50% S) complies with the global cap but is non-compliant in ECAs without an EGCS. "
            "Where route location and vessel compliance arrangement are not provided, "
            "assessment remains CONTEXT_DEPENDENT."
        ),
    }

    # 10. Provenance & Notes Preservation
    provenance = raw.get("provenance") or raw.get("provenance_notes")
    prov_str = str(provenance).strip() if provenance else None

    # Construct structured provenance metadata
    notes_payload = {
        "original_fuel_grade": raw_grade,
        "canonical_fuel_grade": canonical_grade,
        "source_reported_iso_designation": source_reported_iso,
        "source_reported_sulfur_pct": source_reported_sulfur,
        "nominal_max_sulfur_spec": nominal_max_sulfur,
        "max_sulfur_pct": nominal_max_sulfur,  # Backward-compatible alias for nominal spec
        "regulatory_max_sulfur_pct": None,     # Explicitly None: not a universal scalar on fuel grade
        "regulatory_assessment": regulatory_assessment,
        "verified_blend_composition": verified_blend_comp,
        "nominal_iso_reference": nominal_iso_ref,
        "iso_designation": nominal_iso_ref,  # Backward-compatible alias
        "fuel_category": grade_meta.get("fuel_type") or grade_meta.get("fuel_category"),
        "original_price": price_val,
        "original_currency": raw_currency or "USD",
        "original_unit": raw_unit or "USD/MT",
        "provider_provenance": prov_str,
        "warnings": anomaly_warnings if anomaly_warnings else None,
    }

    cleaned = {
        "port_code": port_code,
        "port_name": port_name,
        "country_code": country_code,
        "fuel_grade": canonical_grade,
        "original_fuel_grade": raw_grade,
        "price_value": round(price_val, 2),
        "currency": "USD",
        "unit": "USD/MT",
        "pricing_type": pricing_type,
        "delivery_basis": delivery_basis,
        "source_name": PROVIDER_NAME,
        "source_feed": source_feed or None,
        "observation_time": obs_time,
        "provenance_notes": json.dumps(notes_payload, default=str),
        "anomaly_warnings": anomaly_warnings,
    }

    return True, cleaned, None


# ---------------------------------------------------------------------------
# Acquisition Client Interface
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Idempotent Ingestion & Quarantine Pipeline
# ---------------------------------------------------------------------------

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
    validates numeric, categorical, and currency integrity, isolates anomalies,
    and persists valid records to SQL storage with audit reconciliation metrics.

    Args:
        db: Active SQLAlchemy Session.
        offline: If True, uses authentic offline fixture without remote network calls.
        fixture_path: Optional override path to fixture JSON.
        client: Optional ResilientHttpClient instance.
        grade: Optional fuel grade filter.
        port: Optional port UN/LOCODE filter.

    Returns:
        Dictionary with execution status, withholding visibility, and reconciliation counters.
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
    coverage_by_port: Dict[str, List[str]] = {}

    for item in raw_prices:
        is_valid, cleaned, rejection_reason = validate_bunker_record(item)
        if not is_valid or cleaned is None:
            quarantined_count += 1
            entry = {
                "raw_record": item,
                "reason": rejection_reason,
                "timestamp": retrieval_time_utc.isoformat(),
            }
            quarantine_log.append(entry)
            _GLOBAL_QUARANTINE_AUDIT_LOG.append(entry)
            logger.info("Quarantined bunker record: %s (reason: %s)", item, rejection_reason)

            # Durable database quarantine persistence (REQ-FR-DII-004 & REQ-NFR-DIP-002)
            # Map rejection reason to structured error code
            err_code = "BUNKER_PRICE_ANOMALY"
            if rejection_reason:
                if "CURRENCY" in rejection_reason.upper():
                    err_code = "CURRENCY_CONTRADICTION"
                elif "VOLUMETRIC" in rejection_reason.upper():
                    err_code = "VOLUMETRIC_UNIT_NOT_SUPPORTED"
                elif "UNSUPPORTED PRICING UNIT" in rejection_reason.upper():
                    err_code = "UNSUPPORTED_PRICE_UNIT"
                elif "EXTREME_PRICE_REQUIRES_MANUAL_REVIEW" in rejection_reason.upper() or "5,000" in rejection_reason:
                    err_code = "EXTREME_PRICE_REQUIRES_MANUAL_REVIEW"
                elif "NON_POSITIVE_PRICE" in rejection_reason.upper() or "POSITIVE" in rejection_reason.upper():
                    err_code = "NON_POSITIVE_PRICE"
                elif "STRUCTURAL_INVALID_PRICE" in rejection_reason.upper() or "MISSING PRICE" in rejection_reason.upper() or "NON-NUMERIC" in rejection_reason.upper():
                    err_code = "STRUCTURAL_INVALID_PRICE"
                elif "UNRECOGNIZED" in rejection_reason.upper() or "GRADE" in rejection_reason.upper():
                    err_code = "UNSUPPORTED_FUEL_GRADE"
                elif "UN/LOCODE" in rejection_reason.upper() or "PORT" in rejection_reason.upper():
                    err_code = "INVALID_PORT_IDENTIFIER"
                elif "TIMESTAMP" in rejection_reason.upper():
                    err_code = "INVALID_TIMESTAMP"

            raw_locode = str(item.get("locode") or item.get("port_code") or "UNK").strip().upper()
            raw_grade = str(item.get("grade") or item.get("fuel_grade") or "UNK").strip().upper()
            raw_time = str(item.get("observedAt") or item.get("observation_time") or item.get("timestamp") or "UNK").strip()
            raw_rec_id = f"{raw_locode}_{raw_grade}_{raw_time}"
            feed_id = str(item.get("source") or item.get("source_feed") or "bunker_api")

            # Check if this exact payload was already quarantined to maintain idempotency
            existing_q = (
                db.query(QuarantinedRecord)
                .filter(
                    QuarantinedRecord.source_name == PROVIDER_NAME,
                    QuarantinedRecord.raw_record_id == raw_rec_id,
                    QuarantinedRecord.error_code == err_code,
                )
                .first()
            )
            if not existing_q:
                q_rec = QuarantinedRecord(
                    source_name=PROVIDER_NAME,
                    source_partition=feed_id[:128],
                    raw_record_id=raw_rec_id[:128],
                    error_code=err_code,
                    error_details=rejection_reason[:1000] if rejection_reason else "Bunker validation rejected",
                    raw_payload=json.dumps(item, default=str),
                    ingestion_time=retrieval_time_utc,
                )
                db.add(q_rec)
            continue

        # Optional client-side filtering if user supplied filters
        if port and cleaned["port_code"] != port.strip().upper():
            continue
        if grade and cleaned["fuel_grade"] != grade.strip().upper():
            continue

        # Track coverage
        coverage_by_port.setdefault(cleaned["port_code"], []).append(cleaned["fuel_grade"])

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
    unavailable_priority = sorted(PRIORITY_CORRIDOR_HUBS - set(coverage_by_port.keys()))

    return {
        "status": overall_status,
        "source_origin": source_origin,
        "provider": PROVIDER_NAME,
        "endpoint": DEFAULT_BUNKER_API_URL,
        "records_read": records_read,
        "accepted_count": accepted_count,
        "duplicate_count": duplicate_count,
        "quarantined_count": quarantined_count,
        "failed_count": failed_count,
        "withheld_by_provider": payload.get("withheld", 0),
        "withheld_reason": payload.get("withheldReason"),
        "coverage_by_port": coverage_by_port,
        "unavailable_priority_hubs": unavailable_priority,
        "quarantine_log": quarantine_log,
        "retrieval_time_utc": retrieval_time_utc.isoformat(),
        "licensing_status": PROVIDER_PERMISSION_STATUS,
        "permitted_use": PROVIDER_PERMITTED_USE,
        "operational_availability": OPERATIONAL_AVAILABILITY_STATUS,
        "operational_use_permitted": OPERATIONAL_USE_PERMITTED,
        "data_governance_role": DATA_GOVERNANCE_ROLE,
        "disclaimer": (
            "Indicative bunker prices only. Not an offer to sell and not a substitute for a firm quote. "
            "Delivered commercial spot stems require supplier confirmation for quantity, window, and specs."
        ),
    }


# ---------------------------------------------------------------------------
# Audit & Provenance Inspection Helpers
# ---------------------------------------------------------------------------

def get_quarantine_audit_log() -> List[Dict[str, Any]]:
    """Return all quarantined records recorded during the process lifetime."""
    return list(_GLOBAL_QUARANTINE_AUDIT_LOG)


def clear_quarantine_audit_log() -> None:
    """Clear in-memory quarantine audit log (used in testing)."""
    global _GLOBAL_QUARANTINE_AUDIT_LOG
    _GLOBAL_QUARANTINE_AUDIT_LOG = []
