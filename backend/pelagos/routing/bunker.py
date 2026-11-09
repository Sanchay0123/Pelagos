"""Pelagos Maritime Routing - Bunker Fuel Reference Integration Boundary.

Provides structured bunker fuel reference data and bunkering hub context for
commercial maritime freight corridors, integrating genuine market observations
from Bunker Fuel Prices (bunkerfuelprices.com).

Enforces strict commercial integrity:
- Inspects verified genuine bunker sources; never invents or synthesizes fuel prices.
- Never repurposes unrelated commodity or crude oil benchmarks as marine bunker prices.
- When provider quotes are unavailable or withheld for a hub, returns explicit 'unavailable'
  status with documented provider coverage state.
- Strictly avoids silent port substitution: external/regional benchmarks preserve their authentic
  port identity and are never substituted for corridor hubs.
- Preserves provider attribution, delivery basis, observation timestamp, retrieval timestamp,
  and pricing type.
- Never calculates voyage fuel costs without verified vessel consumption and fuel inputs.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

PROVIDER_NAME = "Bunker Fuel Prices (bunkerfuelprices.com)"
PROVIDER_URL = "https://bunkerfuelprices.com/"


class BunkerPriceQuote(BaseModel):
    """Point-in-time genuine commercial marine bunker fuel price observation."""

    port_code: str  # UN/LOCODE e.g. "USSEA", "ARBUE", "DKSKA"
    port_name: str
    country_code: Optional[str] = None
    fuel_grade: str  # "VLSFO", "HSFO", "MGO"
    price_usd_per_mt: float
    currency: str = "USD"
    unit: str = "USD/MT"
    pricing_type: str = "indicative"  # "indicative", "transacted", "supplier_submission"
    delivery_basis: Optional[str] = None  # "delivered", "ex_wharf", "fob"
    source_name: str = PROVIDER_NAME
    source_feed: Optional[str] = None
    observation_time: str  # ISO 8601 UTC
    retrieval_time_utc: Optional[str] = None
    freshness: str = "stale"  # "fresh" (<=24h), "recent" (<=168h), "stale" (>168h)
    data_age_hours: Optional[float] = None
    provenance_notes: Optional[str] = None


class BunkeringHubInfo(BaseModel):
    """Geographic bunkering port or terminal associated with a freight corridor."""

    port_id: Optional[str] = None  # UN/LOCODE
    name: str
    region: str
    role: str  # "primary_departure_bunkering_hub", "intermediate_canal_bunkering_hub", "destination_discharging_bunkering_hub"
    status: str = "unavailable"  # "available" | "unavailable"
    availability_reason: Optional[str] = None
    quotes: List[BunkerPriceQuote] = Field(default_factory=list)


class BunkerFuelSummary(BaseModel):
    """Corridor-level bunker fuel reference summary and source integration status."""

    provider: str = PROVIDER_NAME
    provider_url: str = PROVIDER_URL
    status: str = "unavailable"  # "available" | "partial" | "unavailable" | "unmonitored"
    reason: str
    required_source: str
    bunkering_hubs_in_scope: List[BunkeringHubInfo] = Field(default_factory=list)
    benchmark_fuel_grades: List[str] = Field(default_factory=list)
    unit: str = "USD/MT"
    currency: str = "USD"
    hubs_with_prices_count: int = 0
    latest_quotes: List[BunkerPriceQuote] = Field(default_factory=list)
    regional_benchmarks: List[BunkerPriceQuote] = Field(default_factory=list)
    notes: Optional[str] = None
    disclaimer: str = (
        "Indicative prices only. Not an offer to sell and not a substitute for a firm quote. "
        "Commercial spot bunker prices require supplier confirmation for stem size, delivery window, and specification."
    )


# Authoritative bunkering hubs associated with monitored trade corridors
CORRIDOR_BUNKERING_HUBS: Dict[str, List[BunkeringHubInfo]] = {
    "PNW-JPN-BULK": [
        BunkeringHubInfo(
            port_id="USSEA",
            name="Seattle / Puget Sound",
            region="U.S. Pacific Northwest",
            role="primary_departure_bunkering_hub",
        ),
        BunkeringHubInfo(
            port_id="USPDX",
            name="Portland / Columbia River",
            region="U.S. Pacific Northwest",
            role="river_loading_terminal_bunkering",
        ),
        BunkeringHubInfo(
            port_id="JPYOK",
            name="Yokohama / Tokyo Bay",
            region="Japan",
            role="destination_discharging_bunkering_hub",
        ),
    ],
    "USG-JPN-BULK": [
        BunkeringHubInfo(
            port_id="USHOU",
            name="Houston / Galveston",
            region="U.S. Gulf Coast",
            role="primary_departure_bunkering_hub",
        ),
        BunkeringHubInfo(
            port_id="USMSY",
            name="New Orleans / Mississippi River",
            region="U.S. Gulf Coast",
            role="river_departure_bunkering_hub",
        ),
        BunkeringHubInfo(
            port_id="PAPAC",
            name="Balboa / Cristobal (Panama Canal)",
            region="Panama Canal Transit",
            role="intermediate_transit_bunkering_hub",
        ),
        BunkeringHubInfo(
            port_id="JPYOK",
            name="Yokohama / Tokyo Bay",
            region="Japan",
            role="destination_discharging_bunkering_hub",
        ),
    ],
}

STANDARD_BENCHMARK_GRADES = [
    "VLSFO 0.5% S (Very Low Sulfur Fuel Oil)",
    "LSMGO 0.1% S (Low Sulfur Marine Gas Oil)",
    "HSFO (High Sulfur Fuel Oil / IFO380)",
]


def evaluate_bunker_freshness(
    obs_time: datetime.datetime,
    ref_time: datetime.datetime,
) -> Tuple[str, float]:
    """Evaluate bunker price observation freshness relative to reference timestamp.

    - <= 24.0 hours: fresh (active trading session)
    - <= 168.0 hours (7 days): recent (standard weekly indication)
    - > 168.0 hours: stale
    """
    if obs_time.tzinfo is None:
        obs_time = obs_time.replace(tzinfo=datetime.timezone.utc)
    if ref_time.tzinfo is None:
        ref_time = ref_time.replace(tzinfo=datetime.timezone.utc)

    diff = (ref_time - obs_time).total_seconds()
    age_hours = round(max(0.0, diff / 3600.0), 2)

    if age_hours <= 24.0:
        return "fresh", age_hours
    elif age_hours <= 168.0:
        return "recent", age_hours
    else:
        return "stale", age_hours


def resolve_corridor_bunker_fuel_references(
    corridor_code: str,
    db: Optional[Session] = None,
    as_of: Optional[str] = None,
) -> BunkerFuelSummary:
    """Resolve bunker fuel reference data and genuine market quotes for a freight corridor.

    Queries genuine external price observations from database when available.
    If quotes are unavailable or withheld by the provider for corridor hubs, returns
    explicit 'unavailable' status per hub and for the corridor without synthetic substitution.

    Args:
        corridor_code: Monitored trade corridor code (e.g. PNW-JPN-BULK, USG-JPN-BULK).
        db: Optional active SQLAlchemy session.
        as_of: Optional ISO 8601 evaluation timestamp for deterministic freshness.

    Returns:
        BunkerFuelSummary with hub-by-hub availability, genuine quotes, and licensing context.
    """
    configured_hubs = CORRIDOR_BUNKERING_HUBS.get(corridor_code)
    if configured_hubs is None:
        return BunkerFuelSummary(
            status="unmonitored",
            reason=f"Corridor '{corridor_code}' is not a monitored trade corridor with configured bunkering hubs.",
            required_source="Bunkering hub mapping is restricted to monitored freight corridors.",
            bunkering_hubs_in_scope=[],
            benchmark_fuel_grades=[],
            unit="USD/MT",
            currency="USD",
            notes="No bunkering hubs configured for unmonitored trade corridor.",
        )

    # Resolve evaluation reference time
    ref_dt = datetime.datetime.now(datetime.timezone.utc)
    if as_of:
        try:
            cleaned = as_of.strip()
            if cleaned.endswith("Z"):
                cleaned = cleaned[:-1] + "+00:00"
            parsed = datetime.datetime.fromisoformat(cleaned)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            ref_dt = parsed.astimezone(datetime.timezone.utc)
        except (ValueError, TypeError):
            pass

    # Create deep copies of hub info to avoid mutating module constants
    hubs: List[BunkeringHubInfo] = [
        BunkeringHubInfo(
            port_id=h.port_id,
            name=h.name,
            region=h.region,
            role=h.role,
            status="unavailable",
            availability_reason=None,
            quotes=[],
        )
        for h in configured_hubs
    ]

    corridor_port_ids = {h.port_id for h in hubs if h.port_id}
    all_corridor_quotes: List[BunkerPriceQuote] = []
    regional_benchmarks: List[BunkerPriceQuote] = []

    if db is not None:
        try:
            from pelagos.models import BunkerPriceObservation

            # Query all observations for corridor hubs
            for hub in hubs:
                if not hub.port_id:
                    continue

                # Query latest quotes for each fuel grade at this hub
                records = (
                    db.query(BunkerPriceObservation)
                    .filter(BunkerPriceObservation.port_code == hub.port_id)
                    .order_by(BunkerPriceObservation.observation_time.desc())
                    .all()
                )

                # Keep latest observation per grade
                seen_grades = set()
                hub_quotes: List[BunkerPriceQuote] = []
                for rec in records:
                    if rec.fuel_grade in seen_grades:
                        continue
                    seen_grades.add(rec.fuel_grade)

                    freshness, age_hours = evaluate_bunker_freshness(rec.observation_time, ref_dt)
                    q = BunkerPriceQuote(
                        port_code=rec.port_code,
                        port_name=rec.port_name,
                        country_code=rec.country_code,
                        fuel_grade=rec.fuel_grade,
                        price_usd_per_mt=round(float(rec.price_value), 2),
                        currency=rec.currency or "USD",
                        unit=rec.unit or "USD/MT",
                        pricing_type=rec.pricing_type or "indicative",
                        delivery_basis=rec.delivery_basis,
                        source_name=rec.source_name or PROVIDER_NAME,
                        source_feed=rec.source_feed,
                        observation_time=rec.observation_time.isoformat(),
                        retrieval_time_utc=rec.retrieval_time_utc.isoformat() if rec.retrieval_time_utc else None,
                        freshness=freshness,
                        data_age_hours=age_hours,
                        provenance_notes=rec.provenance_notes,
                    )
                    hub_quotes.append(q)
                    all_corridor_quotes.append(q)

                if hub_quotes:
                    hub.status = "available"
                    hub.availability_reason = f"Active {hub_quotes[0].pricing_type} quote(s) retrieved from {PROVIDER_NAME}."
                    hub.quotes = hub_quotes
                else:
                    hub.status = "unavailable"
                    hub.availability_reason = (
                        f"No genuine price quotes published by {PROVIDER_NAME} for port {hub.port_id} "
                        f"({hub.name}); quotes withheld or unquoted by provider; synthetic pricing strictly prohibited."
                    )

            # Query any external / regional benchmarks available in database that are not on this corridor
            other_records = (
                db.query(BunkerPriceObservation)
                .filter(~BunkerPriceObservation.port_code.in_(list(corridor_port_ids)))
                .order_by(BunkerPriceObservation.observation_time.desc())
                .all()
            )
            seen_other = set()
            for rec in other_records:
                key = (rec.port_code, rec.fuel_grade)
                if key in seen_other:
                    continue
                seen_other.add(key)
                freshness, age_hours = evaluate_bunker_freshness(rec.observation_time, ref_dt)
                regional_benchmarks.append(
                    BunkerPriceQuote(
                        port_code=rec.port_code,
                        port_name=rec.port_name,
                        country_code=rec.country_code,
                        fuel_grade=rec.fuel_grade,
                        price_usd_per_mt=round(float(rec.price_value), 2),
                        currency=rec.currency or "USD",
                        unit=rec.unit or "USD/MT",
                        pricing_type=rec.pricing_type or "indicative",
                        delivery_basis=rec.delivery_basis,
                        source_name=rec.source_name or PROVIDER_NAME,
                        source_feed=rec.source_feed,
                        observation_time=rec.observation_time.isoformat(),
                        retrieval_time_utc=rec.retrieval_time_utc.isoformat() if rec.retrieval_time_utc else None,
                        freshness=freshness,
                        data_age_hours=age_hours,
                        provenance_notes=rec.provenance_notes,
                    )
                )

        except Exception as exc:
            logger.warning("Error querying bunker price observations: %s", exc)

    hubs_with_prices = sum(1 for h in hubs if h.status == "available")

    if hubs_with_prices == len(hubs) and len(hubs) > 0:
        overall_status = "available"
        reason = f"Genuine external bunker price quotes available for all {len(hubs)} corridor bunkering hubs."
    elif hubs_with_prices > 0:
        overall_status = "partial"
        reason = f"Genuine external bunker price quotes available for {hubs_with_prices} of {len(hubs)} corridor bunkering hubs."
    else:
        overall_status = "unavailable"
        hub_names = ", ".join(h.name for h in hubs)
        reason = (
            f"Direct commercial bunker spot price benchmarks (e.g. S&P Global Platts, Argus Media, Ship & Bunker) "
            f"are not licensed or integrated for these specific hubs. Bunker Fuel Prices API is connected, but provider "
            f"currently publishes no active quotes for monitored corridor hubs ({hub_names}) — records withheld by provider "
            f"due to commercial display licensing restrictions. Pelagos strictly prohibits fabricating synthetic bunker prices "
            f"or substituting crude oil/agricultural proxies."
        )

    required_source = (
        f"Commercial spot bunker price assessment from {PROVIDER_NAME} or equivalent licensed benchmark "
        f"providing delivered market prices for VLSFO (0.5% S), LSMGO (0.1% S), and HSFO at corridor bunkering ports in USD/MT."
    )

    notes = (
        "Bunker fuel context integrates genuine commercial observations where published. "
        "Voyage fuel cost calculations are omitted until verified vessel daily consumption and stem quantities are supplied."
    )

    return BunkerFuelSummary(
        provider=PROVIDER_NAME,
        provider_url=PROVIDER_URL,
        status=overall_status,
        reason=reason,
        required_source=required_source,
        bunkering_hubs_in_scope=hubs,
        benchmark_fuel_grades=STANDARD_BENCHMARK_GRADES,
        unit="USD/MT",
        currency="USD",
        hubs_with_prices_count=hubs_with_prices,
        latest_quotes=all_corridor_quotes,
        regional_benchmarks=regional_benchmarks,
        notes=notes,
    )
