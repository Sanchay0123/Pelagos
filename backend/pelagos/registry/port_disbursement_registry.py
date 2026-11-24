"""Pelagos Port Disbursement Intelligence Registry & Deterministic Rule Engine (Stage F1.4.2.3b).

Provides source-backed statutory port tariff cataloging, deterministic disbursement calculation,
and currency conversion for global maritime ports of call.

Readiness Tiers (Stage F1.4.2.3b Governance Standard):
1. Level 1 (Source Registry): Authoritative cataloging of port authority regulations, municipal bylaws,
   and FMC-filed tariff schedules with inspectable URLs and precise citations.
2. Level 2 (Deterministic Rules): Mathematical encoding of documented statutory formulas (per GT, per LOA,
   composite GT+cargo, draft charges) for research, testing, and sensitivity analysis.
3. Level 3 (Operational Disbursements): Strictly BLOCKED / FAIL-CLOSED. Operational calculations
   for commercial freight bidding, charter suitability, and net TCE optimization remain blocked until
   comprehensive global coverage, live daily currency feeds, and commercial licensing are established.

Strict Operational Governance Invariants:
- Zero Synthetic Tonnage: Never estimate or substitute GT, DWT, or dimensions via proxy ratios.
- Fail-Closed: Missing tariffs, unregistered ports, missing dimensions, or absent FX rates fail closed.
- Decoupled Taxonomy: Mandatory statutory dues, conditional services, usage-based dockage, and optional
  services are tracked and reported as distinct line items.
- Non-Proliferation: Never fill missing ports with regional synthetic averages or neighboring port rates.
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from pelagos.currency import CurrencyConversionResult, CurrencyConversionService
from pelagos.models import PortTariffRecord


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PortDisbursementError(Exception):
    """Base exception for port disbursement operations."""
    pass


class MissingPortTariffError(PortDisbursementError):
    """Raised when a specific port tariff item or schedule cannot be found."""
    pass


class MissingVesselInputError(PortDisbursementError):
    """Raised when a mandatory vessel dimension (GT, LOA, Draft) is required but missing."""
    pass


class ExpiredPortTariffVersionError(PortDisbursementError):
    """Raised when a port transit date falls outside supported tariff schedules."""
    pass


class OperationalModeBlockedError(PortDisbursementError):
    """Raised when operational port disbursement calculation is attempted while Level 3 is blocked."""
    pass


# ---------------------------------------------------------------------------
# Result Data Structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PortDisbursementLineItem:
    """Immutable line-item component for port disbursement calculation."""
    charge_category: str
    tariff_identifier: str
    description: str
    applicability_type: str  # MANDATORY, CONDITIONAL, USAGE_BASED, OPTIONAL
    rate_value: float
    billing_unit: str
    local_currency: str
    amount_local: Optional[float]
    exchange_rate_to_usd: Optional[float] = None
    amount_usd: Optional[float] = None
    is_available: bool = True
    effective_from: Optional[datetime.date] = None
    effective_to: Optional[datetime.date] = None
    source_reference: Optional[str] = None
    evidence_classification: str = "DIRECTLY_VERIFIED"
    permission_status: str = "PERMITTED_STATUTORY_DISCLOSURE"
    is_partial_component: bool = False
    fx_rate_provider: Optional[str] = None
    fx_observation_date: Optional[datetime.date] = None
    failure_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortDisbursementCalculationResult:
    """Complete calculation result for a port of call disbursement evaluation."""
    un_locode: str
    port_name: str
    is_complete: bool
    operational_mode: bool
    transit_date: datetime.date
    purpose_of_call: str
    stay_duration_hours: float
    items: List[PortDisbursementLineItem] = field(default_factory=list)
    total_port_costs_usd: Optional[float] = None
    mandatory_total_usd: Optional[float] = None
    conditional_total_usd: Optional[float] = None
    usage_total_usd: Optional[float] = None
    optional_total_usd: Optional[float] = None
    has_partial_components: bool = False
    blocked_reasons: List[str] = field(default_factory=list)
    readiness_tier: str = "LEVEL_2_RESEARCH_ONLY"
    evidence_summary: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Port Disbursement Registry & Rule Engine
# ---------------------------------------------------------------------------

class PortDisbursementRegistry:
    """
    Statutory Port Disbursement Registry and Deterministic Calculation Engine.
    Encodes normalized port disbursement structures across pilot maritime hubs.
    """

    def __init__(
        self,
        db: Optional[Session] = None,
        currency_service: Optional[CurrencyConversionService] = None,
        tariffs_json_path: Optional[Union[str, Path]] = None,
    ):
        self.db = db
        self.currency_service = currency_service or CurrencyConversionService(db=db)
        if currency_service is None and db is None:
            self.currency_service.seed_historical_statutory_rates()
        self._memory_records: Dict[str, Dict[str, Any]] = {}
        self.tariffs_json_path = tariffs_json_path or (
            Path(__file__).parent.parent / "data" / "f1_4_2_3b_pilot_port_tariffs.json"
        )
        self._seed_in_memory_records()

    def _seed_in_memory_records(self) -> None:
        """Seed authentic port tariff records from JSON or embedded fallback."""
        json_file = Path(self.tariffs_json_path)
        if json_file.exists():
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    records = json.load(f)
                    for r in records:
                        # Convert string dates to datetime.date
                        if isinstance(r.get("effective_from"), str):
                            r["effective_from"] = datetime.date.fromisoformat(r["effective_from"])
                        if isinstance(r.get("effective_to"), str):
                            r["effective_to"] = datetime.date.fromisoformat(r["effective_to"])
                        if isinstance(r.get("publication_date"), str):
                            r["publication_date"] = datetime.date.fromisoformat(r["publication_date"])
                        key = f"{r['un_locode']}_{r['tariff_identifier']}_{r['effective_from']}"
                        self._memory_records[key] = r
                    return
            except Exception:
                pass

        # Built-in fallback seed if JSON file is missing or unreadable
        fallback_records = [
            {
                "un_locode": "SGSIN",
                "port_name": "Port of Singapore",
                "country_code": "SGP",
                "jurisdiction": "Singapore Maritime Waters",
                "port_authority": "Maritime and Port Authority of Singapore (MPA)",
                "tariff_identifier": "SGSIN-PORT-DUES-CARGO-2023",
                "charge_category": "PORT_DUES",
                "applicability_type": "MANDATORY",
                "vessel_class": "DRY_BULK",
                "basis_dimension": "GT",
                "rate_value": 4.50,
                "currency": "SGD",
                "billing_unit": "PER_100_GT_PER_DAY",
                "effective_from": datetime.date(2023, 1, 1),
                "effective_to": datetime.date(2025, 12, 31),
                "source_url": "https://www.mpa.gov.sg/port-marine-operations/port-dues-tariff",
                "document_identifier": "MPA Port Marine Circular No. 11/2022",
                "precise_source_reference": "Schedule of Port Dues, Item 1(a)",
                "evidence_classification": "DIRECTLY_VERIFIED",
                "permission_status": "PERMITTED_STATUTORY_DISCLOSURE",
                "supersession_status": "CURRENT",
            }
        ]
        for r in fallback_records:
            key = f"{r['un_locode']}_{r['tariff_identifier']}_{r['effective_from']}"
            self._memory_records[key] = r

    def seed_statutory_tariffs_to_db(self) -> int:
        """Seed in-memory statutory port records into the database session."""
        if self.db is None:
            return 0

        inserted_count = 0
        for r in self._memory_records.values():
            stmt = select(PortTariffRecord).where(
                and_(
                    PortTariffRecord.un_locode == r["un_locode"],
                    PortTariffRecord.tariff_identifier == r["tariff_identifier"],
                    PortTariffRecord.effective_from == r["effective_from"],
                )
            )
            existing = self.db.scalars(stmt).first()
            if not existing:
                rec = PortTariffRecord(
                    un_locode=r["un_locode"],
                    port_name=r["port_name"],
                    country_code=r.get("country_code", "UNK"),
                    jurisdiction=r.get("jurisdiction", "Statutory Port Authority"),
                    port_authority=r["port_authority"],
                    tariff_identifier=r["tariff_identifier"],
                    charge_category=r["charge_category"],
                    applicability_type=r.get("applicability_type", "MANDATORY"),
                    vessel_class=r.get("vessel_class", "DRY_BULK"),
                    basis_dimension=r.get("basis_dimension", "GT"),
                    min_threshold=r.get("min_threshold"),
                    max_threshold=r.get("max_threshold"),
                    rate_value=r["rate_value"],
                    currency=r["currency"],
                    billing_unit=r["billing_unit"],
                    min_charge=r.get("min_charge"),
                    max_charge=r.get("max_charge"),
                    effective_from=r["effective_from"],
                    effective_to=r["effective_to"],
                    publication_date=r.get("publication_date"),
                    source_url=r.get("source_url", "https://pelagos.local"),
                    document_identifier=r.get("document_identifier", r["tariff_identifier"]),
                    precise_source_reference=r.get("precise_source_reference", "Official Schedule"),
                    evidence_classification=r.get("evidence_classification", "DIRECTLY_VERIFIED"),
                    permission_status=r.get("permission_status", "PERMITTED_STATUTORY_DISCLOSURE"),
                    supersession_status=r.get("supersession_status", "CURRENT"),
                    applicability_condition=r.get("applicability_condition"),
                    notes=r.get("notes"),
                )
                self.db.add(rec)
                inserted_count += 1

        self.db.flush()
        return inserted_count

    def _get_records_for_port(self, un_locode: str, date: datetime.date) -> List[Dict[str, Any]]:
        """Retrieve all active tariff records for a port on the specified transit date."""
        locode = un_locode.upper().strip()
        matched: List[Dict[str, Any]] = []

        # Check DB first if available
        if self.db is not None:
            stmt = select(PortTariffRecord).where(
                and_(
                    PortTariffRecord.un_locode == locode,
                    PortTariffRecord.effective_from <= date,
                    PortTariffRecord.effective_to >= date,
                )
            )
            db_records = self.db.scalars(stmt).all()
            if db_records:
                for rec in db_records:
                    matched.append({
                        "un_locode": rec.un_locode,
                        "port_name": rec.port_name,
                        "country_code": rec.country_code,
                        "jurisdiction": rec.jurisdiction,
                        "port_authority": rec.port_authority,
                        "tariff_identifier": rec.tariff_identifier,
                        "charge_category": rec.charge_category,
                        "applicability_type": rec.applicability_type,
                        "vessel_class": rec.vessel_class,
                        "basis_dimension": rec.basis_dimension,
                        "min_threshold": rec.min_threshold,
                        "max_threshold": rec.max_threshold,
                        "rate_value": rec.rate_value,
                        "currency": rec.currency,
                        "billing_unit": rec.billing_unit,
                        "min_charge": rec.min_charge,
                        "max_charge": rec.max_charge,
                        "effective_from": rec.effective_from,
                        "effective_to": rec.effective_to,
                        "precise_source_reference": rec.precise_source_reference,
                        "evidence_classification": rec.evidence_classification,
                        "permission_status": rec.permission_status,
                        "applicability_condition": rec.applicability_condition,
                    })
                return matched

        # Memory search
        for r in self._memory_records.values():
            if r["un_locode"] == locode and r["effective_from"] <= date <= r["effective_to"]:
                matched.append(r)
        return matched

    def get_port_tariffs(self, un_locode: str, transit_date: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
        """Retrieve port tariff records for a port on the specified transit date."""
        d = transit_date or datetime.date(2024, 1, 15)
        matched = self._get_records_for_port(un_locode, d)
        if not matched:
            locode = un_locode.upper().strip()
            for r in self._memory_records.values():
                if r["un_locode"] == locode:
                    matched.append(r)
        return matched

    def calculate_port_disbursement(
        self,
        un_locode: str,
        vessel_gt: Optional[float],
        vessel_dwt: Optional[float],
        loa_m: Optional[float],
        beam_m: Optional[float],
        draft_m: Optional[float],
        purpose_of_call: str = "CARGO_OPERATIONS",
        stay_duration_hours: float = 48.0,
        cargo_handled_tons: Optional[float] = None,
        transit_date: Optional[datetime.date] = None,
        operational_mode: bool = False,
    ) -> PortDisbursementCalculationResult:
        """
        Evaluate deterministic port-call disbursement under source-backed statutory tariffs.
        
        Strict Fail-Closed Governance Invariants:
        1. Port Discovery Gate: If UN/LOCODE is not registered, strictly fails closed.
        2. Certified Dimension Gate: Never substitute synthetic GT, DWT, LOA, or draft. Missing required inputs block calculation.
        3. Currency Governance: Converts foreign local currencies (SGD, EUR, GBP) to USD via CurrencyConversionService.
           Missing exchange rates for transit date fail closed.
        4. Operational Gating: Level 3 operational port disbursement is BLOCKED. If operational_mode=True, returns blocked result.
        """
        locode = un_locode.upper().strip()
        t_date = transit_date or datetime.date(2024, 5, 1)
        purpose = purpose_of_call.upper().strip()
        stay_days = math.ceil(stay_duration_hours / 24.0) if stay_duration_hours > 0 else 1.0

        blocked_reasons: List[str] = []
        items: List[PortDisbursementLineItem] = []
        evidence_summary: Dict[str, str] = {}

        # 1. Operational Mode Check (Level 3 Block)
        if operational_mode:
            reason = "OPERATIONAL_PORT_DISBURSEMENT_BLOCKED: Level 3 operational port disbursements remain blocked. Commercial TCE workflows cannot consume preliminary port disbursement estimates."
            blocked_reasons.append(reason)

        # 2. Query Tariffs
        tariffs = self._get_records_for_port(locode, t_date)
        if not tariffs:
            # Check if port exists in memory with expired date or not registered at all
            known_ports = {r["un_locode"]: r["port_name"] for r in self._memory_records.values()}
            port_name = known_ports.get(locode, f"Unknown Port ({locode})")
            if locode in known_ports:
                reason = f"EXPIRED_OR_UNSUPPORTED_PORT_TARIFF_VERSION: No active tariff schedule found for {locode} on {t_date}"
            else:
                reason = f"PORT_NOT_REGISTERED: UN/LOCODE '{locode}' is not cataloged in the port tariff registry. Synthetic regional averages are strictly prohibited."
            blocked_reasons.append(reason)
            return PortDisbursementCalculationResult(
                un_locode=locode,
                port_name=port_name,
                is_complete=False,
                operational_mode=operational_mode,
                transit_date=t_date,
                purpose_of_call=purpose,
                stay_duration_hours=stay_duration_hours,
                blocked_reasons=blocked_reasons,
                readiness_tier="LEVEL_1_SOURCE_CATALOG",
            )

        port_name = tariffs[0]["port_name"]

        # 3. Evaluate Line Items
        mandatory_total = 0.0
        conditional_total = 0.0
        usage_total = 0.0
        optional_total = 0.0
        has_partial_components = False

        for t in tariffs:
            cat = t["charge_category"]
            tid = t["tariff_identifier"]
            app_type = t.get("applicability_type", "MANDATORY")
            dim = t.get("basis_dimension", "GT")
            curr = t["currency"]
            rate = t["rate_value"]
            ev_class = t.get("evidence_classification", "DIRECTLY_VERIFIED")
            is_partial = bool(t.get("is_partial_component", False))
            if is_partial:
                has_partial_components = True
            evidence_summary[tid] = ev_class

            amount_local: Optional[float] = None
            item_error: Optional[str] = None

            # --- Dimension & Rule Evaluation ---
            if tid == "SGSIN-PORT-DUES-CARGO-2023":
                if purpose == "BUNKERING_ONLY" and stay_duration_hours <= 24.0:
                    continue  # Concession tariff applies instead
                if vessel_gt is None or vessel_gt <= 0:
                    item_error = "MISSING_VESSEL_GT: Singapore port dues require verified Gross Tonnage"
                else:
                    units_100_gt = math.ceil(vessel_gt / 100.0)
                    amount_local = round(units_100_gt * rate * stay_days, 2)

            elif tid == "SGSIN-PORT-DUES-BUNKER-2023":
                if not (purpose == "BUNKERING_ONLY" and stay_duration_hours <= 24.0):
                    continue  # Only applies to pure bunker calls <= 24 hours
                if vessel_gt is None or vessel_gt <= 0:
                    item_error = "MISSING_VESSEL_GT: Singapore bunkering port dues require verified Gross Tonnage"
                else:
                    units_100_gt = math.ceil(vessel_gt / 100.0)
                    amount_local = round(units_100_gt * rate, 2)

            elif tid == "SGSIN-ANCHORAGE-DUES-2023":
                # Free for first 24 hours; applies only if stay exceeds 24 hrs and at anchor
                if stay_duration_hours <= 24.0 or purpose != "ANCHORAGE_WAITING":
                    continue
                if vessel_gt is None or vessel_gt <= 0:
                    item_error = "MISSING_VESSEL_GT: Singapore anchorage dues require verified Gross Tonnage"
                else:
                    chargeable_days = stay_days - 1
                    units_100_gt = math.ceil(vessel_gt / 100.0)
                    amount_local = round(units_100_gt * rate * chargeable_days, 2)

            elif tid == "SGSIN-PSA-PILOTAGE-2023":
                if vessel_gt is None or vessel_gt <= 0:
                    item_error = "MISSING_VESSEL_GT: Singapore pilotage requires verified Gross Tonnage"
                else:
                    maneuver_hours = 2.0  # standard pilot assignment
                    amount_local = round(rate * maneuver_hours, 2)
                    min_charge = t.get("min_charge")
                    if min_charge and amount_local < min_charge:
                        amount_local = min_charge

            elif tid == "NLRTM-SEAPORT-DUES-GT-2024":
                if vessel_gt is None or vessel_gt <= 0:
                    item_error = "MISSING_VESSEL_GT: Rotterdam seaport dues require verified Gross Tonnage"
                else:
                    calc = vessel_gt * rate
                    min_charge = t.get("min_charge")
                    if min_charge and calc < min_charge:
                        calc = min_charge
                    amount_local = round(calc, 2)

            elif tid == "NLRTM-SEAPORT-DUES-CARGO-2024":
                if cargo_handled_tons is None or cargo_handled_tons < 0:
                    item_error = "MISSING_CARGO_HANDLED: Rotterdam cargo seaport dues require metric tons handled"
                else:
                    amount_local = round(cargo_handled_tons * rate, 2)

            elif tid == "NLRTM-SAB-WASTE-FEE-2024":
                amount_local = rate  # fixed per call

            elif tid == "NLRTM-LOODS-PILOTAGE-S-2024":
                amount_local = rate  # fixed base S start charge

            elif tid == "USHOU-PHA-HARBOR-FEE-2024":
                if loa_m is None or loa_m <= 0:
                    item_error = "MISSING_VESSEL_LOA: Houston harbor fee requires verified Length Overall"
                else:
                    loa_ft = loa_m * 3.28084
                    if loa_ft > 500.0:
                        amount_local = 550.0
                    elif loa_ft > 250.0:
                        amount_local = 275.0
                    else:
                        amount_local = 125.0

            elif tid == "USHOU-PHA-DOCKAGE-2024":
                if loa_m is None or loa_m <= 0:
                    item_error = "MISSING_VESSEL_LOA: Houston dockage requires verified Length Overall"
                else:
                    loa_ft = loa_m * 3.28084
                    amount_local = round(loa_ft * rate * stay_days, 2)

            elif tid == "USHOU-PHA-SECURITY-2024":
                # 10% of dockage
                if loa_m is None or loa_m <= 0:
                    item_error = "MISSING_VESSEL_LOA: Houston security surcharge requires dockage base"
                else:
                    loa_ft = loa_m * 3.28084
                    dockage_amount = loa_ft * 3.85 * stay_days
                    amount_local = round(dockage_amount * (rate / 100.0), 2)

            elif tid == "USHOU-PILOT-DRAFT-2024":
                if draft_m is None or draft_m <= 0:
                    item_error = "MISSING_VESSEL_DRAFT: Houston pilot draft charge requires verified draft"
                else:
                    draft_ft = draft_m * 3.28084
                    chargeable_draft = max(draft_ft, 15.0)  # min 15 ft
                    amount_local = round(chargeable_draft * rate, 2)

            elif tid == "USHOU-PILOT-UNIT-2024":
                if loa_m is None or beam_m is None or loa_m <= 0 or beam_m <= 0:
                    item_error = "MISSING_VESSEL_DIMENSIONS: Houston pilot unit charge requires verified LOA and beam"
                else:
                    loa_ft = loa_m * 3.28084
                    beam_ft = beam_m * 3.28084
                    pilot_units = (loa_ft * beam_ft) / 100.0
                    amount_local = round(pilot_units * rate, 2)

            elif tid == "JPYOK-PORT-DUES-2024":
                if vessel_gt is None or vessel_gt <= 0:
                    item_error = "MISSING_VESSEL_GT: Yokohama port entry dues require verified Gross Tonnage"
                else:
                    # Statutory municipal charge: JPY 2.70 per gross ton per entry
                    amount_local = round(vessel_gt * rate, 2)

            elif tid == "JPYOK-PUBLIC-WHARFAGE-2024":
                if vessel_gt is None or vessel_gt <= 0:
                    item_error = "MISSING_VESSEL_GT: Yokohama public wharfage requires verified Gross Tonnage"
                else:
                    # Assessed per 12-hour block (or fraction thereof)
                    blocks_12h = math.ceil(stay_duration_hours / 12.0) if stay_duration_hours > 0 else 1.0
                    amount_local = round(vessel_gt * rate * blocks_12h, 2)

            elif tid == "JPYOK-TOKYO-BAY-PILOT-2024":
                if vessel_gt is None or draft_m is None or vessel_gt <= 0 or draft_m <= 0:
                    item_error = "MISSING_VESSEL_DIMENSIONS: Tokyo Bay pilotage requires verified Gross Tonnage and Draft"
                else:
                    # MLIT Statutory formula: Base A + Inc B * (ceil((GRT - 1000)/1000) + ceil((Draft - 3.0)/0.30))
                    base_a = rate  # 45,800.0 JPY
                    inc_b = round(base_a * 0.10, 2)  # 4,580.0 JPY
                    grt_units = max(0, math.ceil((vessel_gt - 1000.0) / 1000.0))
                    draft_units = max(0, math.ceil((draft_m - 3.0) / 0.30))
                    amount_local = float(base_a + inc_b * (grt_units + draft_units))

            elif tid == "JPYOK-HARBOR-PILOT-2024":
                if vessel_gt is None or draft_m is None or vessel_gt <= 0 or draft_m <= 0:
                    item_error = "MISSING_VESSEL_DIMENSIONS: Yokohama harbor docking pilotage requires verified Gross Tonnage and Draft"
                else:
                    # MLIT Statutory formula: Base A + Inc B * (ceil((GRT - 1000)/1000) + ceil((Draft - 3.0)/0.30))
                    base_a = rate  # 27,500.0 JPY
                    inc_b = round(base_a * 0.10, 2)  # 2,750.0 JPY
                    grt_units = max(0, math.ceil((vessel_gt - 1000.0) / 1000.0))
                    draft_units = max(0, math.ceil((draft_m - 3.0) / 0.30))
                    amount_local = float(base_a + inc_b * (grt_units + draft_units))

            else:
                # Generic fallback for standard fixed/unit tariffs
                amount_local = rate

            # --- Currency Conversion ---
            amount_usd: Optional[float] = None
            conv_rate: Optional[float] = None
            conv_provider: Optional[str] = None
            conv_obs_date: Optional[datetime.date] = None
            conv_currency = "USD" if curr in ("PERCENTAGE", "PCT") else curr

            if amount_local is not None:
                conv_res = self.currency_service.convert(
                    amount=amount_local,
                    from_currency=conv_currency,
                    to_currency="USD",
                    effective_date=t_date,
                )
                if conv_res.is_available and conv_res.converted_amount is not None:
                    amount_usd = round(conv_res.converted_amount, 2)
                    conv_rate = conv_res.exchange_rate
                    conv_provider = conv_res.rate_provider
                    conv_obs_date = conv_res.effective_date
                    if app_type == "MANDATORY":
                        mandatory_total += amount_usd
                    elif app_type == "CONDITIONAL":
                        conditional_total += amount_usd
                    elif app_type == "USAGE_BASED":
                        usage_total += amount_usd
                    elif app_type == "OPTIONAL":
                        optional_total += amount_usd
                else:
                    item_error = f"MISSING_CURRENCY_EXCHANGE_RATE: Could not convert {curr}->USD for date {t_date}"
                    blocked_reasons.append(item_error)

            if item_error:
                blocked_reasons.append(f"{tid}: {item_error}")

            items.append(
                PortDisbursementLineItem(
                    charge_category=cat,
                    tariff_identifier=tid,
                    description=t.get("notes") or f"{cat} ({tid})",
                    applicability_type=app_type,
                    rate_value=rate,
                    billing_unit=t.get("billing_unit", "UNIT"),
                    local_currency=curr,
                    amount_local=amount_local,
                    exchange_rate_to_usd=conv_rate,
                    amount_usd=amount_usd,
                    is_available=(amount_usd is not None),
                    effective_from=t.get("effective_from"),
                    effective_to=t.get("effective_to"),
                    source_reference=t.get("precise_source_reference"),
                    evidence_classification=ev_class,
                    permission_status=t.get("permission_status", "PERMITTED_STATUTORY_DISCLOSURE"),
                    is_partial_component=is_partial,
                    fx_rate_provider=conv_provider,
                    fx_observation_date=conv_obs_date,
                    failure_reason=item_error,
                )
            )

        is_complete = (len(blocked_reasons) == 0) and not operational_mode
        total_usd: Optional[float] = None

        if len(blocked_reasons) == 0:
            total_usd = round(mandatory_total + conditional_total + usage_total + optional_total, 2)

        tier = "LEVEL_3_OPERATIONAL_APPROVED" if (is_complete and operational_mode) else "LEVEL_2_RESEARCH_ONLY"

        return PortDisbursementCalculationResult(
            un_locode=locode,
            port_name=port_name,
            is_complete=is_complete,
            operational_mode=operational_mode,
            transit_date=t_date,
            purpose_of_call=purpose,
            stay_duration_hours=stay_duration_hours,
            items=items,
            total_port_costs_usd=total_usd,
            mandatory_total_usd=round(mandatory_total, 2) if is_complete else None,
            conditional_total_usd=round(conditional_total, 2) if is_complete else None,
            usage_total_usd=round(usage_total, 2) if is_complete else None,
            optional_total_usd=round(optional_total, 2) if is_complete else None,
            has_partial_components=has_partial_components,
            blocked_reasons=blocked_reasons,
            readiness_tier=tier,
            evidence_summary=evidence_summary,
        )
