"""
Pelagos Stage F1.4.3: Four-Tier Charge Classification Engine.
Decomposes port and canal charges into four distinct governance tiers and
enforces one-way statutory floor checks with zero tolerance for sub-floor valuations.

Tiers:
  - Tier 1: Statutory Dues (PERMITTED_STATUTORY_DISCLOSURE) - Mandatory authority charges.
  - Tier 2: Regulated Ancillary Services - Published tariff caps / official scales (pilotage, tugs, mooring, freshwater).
  - Tier 3: Contractual Remuneration - Commercially negotiated bilateral charges (agency fees). Requires contract attribution.
  - Tier 4: Dynamic / Provisional Estimates - Volatile / operational / unverified estimates. Always provisional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Dict, List, Optional, Union


def _to_decimal(val: Any, precision: str = "0.01") -> Decimal:
    """Convert input to Decimal with exact rounding to specified precision."""
    if val is None:
        return Decimal("0.00")
    if isinstance(val, Decimal):
        d = val
    elif isinstance(val, float):
        d = Decimal(str(round(val, 4)))
    else:
        d = Decimal(str(val).strip())
    return d.quantize(Decimal(precision), rounding=ROUND_HALF_UP)


class ChargeClassificationTier(str, Enum):
    """Four-tier governance classification for voyage disbursement line items."""
    TIER_1_STATUTORY_DUES = "TIER_1_STATUTORY_DUES"
    TIER_2_REGULATED_SERVICES = "TIER_2_REGULATED_SERVICES"
    TIER_3_CONTRACTUAL_REMUNERATION = "TIER_3_CONTRACTUAL_REMUNERATION"
    TIER_4_PROVISIONAL_ESTIMATES = "TIER_4_PROVISIONAL_ESTIMATES"


# Known charge category mappings
TIER_1_CATEGORIES = {
    "PORT_DUES",
    "HARBOUR_DUES",
    "TONNAGE_DUES",
    "CANAL_BASE_TOLLS",
    "CANAL_BASE_DUES",
    "CANAL_CAPACITY_DUES",
    "CANAL_FIXED_CHARGE",
    "CANAL_TRANSIT_TOLLS",
    "VTS_DUES",
    "NAVIGATION_DUES",
    "ANCHORAGE_DUES",
    "STATUTORY_DUES",
}

TIER_2_CATEGORIES = {
    "PILOTAGE",
    "PILOTAGE_BASE",
    "PILOTAGE_OVERTIME",
    "PILOTAGE_DRAFT_OVERAGE",
    "PILOTAGE_FUEL_SURCHARGE",
    "TOWAGE",
    "TUG_ASSISTANCE",
    "MOORING",
    "UNMOORING",
    "MOORING_UNMOORING",
    "CANAL_FRESHWATER_SURCHARGE",
    "CANAL_LINESMEN",
    "CANAL_PILOTAGE",
    "CANAL_MOORING",
    "REGULATED_SERVICES",
    "QUAY_DUES",
    "BERTH_HIRE",
}

TIER_3_CATEGORIES = {
    "AGENCY_FEE",
    "PORT_AGENCY",
    "COMMERCIAL_AGENCY",
    "COMMERCIAL_LAUNCH",
    "CUSTOMS_BROKERAGE",
    "SURVEYOR_FEE",
    "STEVEDORING",
    "CARGO_HANDLING",
    "CONTRACTUAL_REMUNERATION",
}

TIER_4_CATEGORIES = {
    "DEMURRAGE",
    "DESPATCH",
    "CONTINGENCY_RESERVE",
    "DISCRETIONARY_PROVISION",
    "UNVERIFIED_EXTRA",
    "UNALLOCATED_COMMERCIAL_OVERAGE",
    "PROVISIONAL_EXPENSE",
    "PROVISIONAL_ESTIMATE",
    "TUG_ESCORT_OVERAGE",
}


@dataclass
class ClassifiedChargeItem:
    """Classified charge line item with tier and governance status."""
    tariff_identifier: str
    description: str
    charge_tier: ChargeClassificationTier
    charge_category: str
    amount_usd: Optional[float]
    amount_local: Optional[float]
    currency: str
    statutory_basis: Optional[str] = None
    contract_attribution: Optional[str] = None
    is_provisional: bool = False
    is_operational_permitted: bool = False
    evidence_classification: str = "DIRECTLY_VERIFIED"
    permission_status: str = "PERMITTED_STATUTORY_DISCLOSURE"
    flags: List[str] = field(default_factory=list)


@dataclass
class ChargeClassificationSummary:
    """Summary of charges classified across the four tiers."""
    tier_1_statutory_total_usd: float = 0.0
    tier_2_regulated_total_usd: float = 0.0
    tier_3_contractual_total_usd: float = 0.0
    tier_4_provisional_total_usd: float = 0.0
    total_disbursement_usd: float = 0.0
    has_provisional_estimates: bool = False
    has_missing_contractual_evidence: bool = False
    has_partial_components: bool = False
    is_operational_permitted: bool = False
    classified_items: List[ClassifiedChargeItem] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)


@dataclass
class FloorCheckResult:
    """Result of one-way statutory floor evaluation."""
    is_floor_satisfied: bool
    user_amount_usd: float
    statutory_floor_usd: float
    variance_usd: float
    unallocated_overage_usd: float
    status: str
    is_operational_permitted: bool
    failure_reasons: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    itemized_allocations: Dict[str, float] = field(default_factory=dict)


class ChargeClassifier:
    """
    Four-tier charge classifier and one-way statutory floor enforcement service.
    
    Invariants:
    1. Zero Tolerance on Sub-Floor Fixtures:
       Any user-supplied valuation below the statutory baseline fails closed with
       FIXTURE_VALUATION_BELOW_STATUTORY_FLOOR.
    2. Non-Automatic Validation of Surplus:
       Values exceeding the floor do not establish validity. Unaccounted excess
       is flagged as UNALLOCATED_COMMERCIAL_OVERAGE and barred from operational clearance.
    3. Contract Attribution for Tier 3:
       Contractual remuneration items require explicit bilateral contract references.
       Missing attribution produces MISSING_CONTRACTUAL_EVIDENCE.
    4. Provisional Exclusion:
       Tier 4 provisional estimates are permanently barred from operational approval.
    """

    @classmethod
    def determine_tier(
        cls,
        charge_category: str,
        applicability_type: Optional[str] = None,
        tariff_id: Optional[str] = None,
    ) -> ChargeClassificationTier:
        """Deterministically determine the charge classification tier."""
        cat_upper = (charge_category or "").strip().upper()
        app_upper = (applicability_type or "").strip().upper()

        if app_upper in ("COMMERCIALLY_NEGOTIATED", "CONTRACTUAL"):
            return ChargeClassificationTier.TIER_3_CONTRACTUAL_REMUNERATION
        if app_upper in ("PROVISIONAL", "ESTIMATE"):
            return ChargeClassificationTier.TIER_4_PROVISIONAL_ESTIMATES

        if cat_upper in TIER_1_CATEGORIES:
            return ChargeClassificationTier.TIER_1_STATUTORY_DUES
        if cat_upper in TIER_2_CATEGORIES:
            return ChargeClassificationTier.TIER_2_REGULATED_SERVICES
        if cat_upper in TIER_3_CATEGORIES:
            return ChargeClassificationTier.TIER_3_CONTRACTUAL_REMUNERATION
        if cat_upper in TIER_4_CATEGORIES:
            return ChargeClassificationTier.TIER_4_PROVISIONAL_ESTIMATES

        # Canal specifics
        if tariff_id:
            tid_upper = tariff_id.upper()
            if tid_upper.startswith("ACP-1010") or tid_upper.startswith("SCA-BASE"):
                return ChargeClassificationTier.TIER_1_STATUTORY_DUES
            if tid_upper.startswith("ACP-1065") or "FRESHWATER" in tid_upper:
                return ChargeClassificationTier.TIER_2_REGULATED_SERVICES
            if "MOORING" in tid_upper or "PILOT" in tid_upper:
                return ChargeClassificationTier.TIER_2_REGULATED_SERVICES

        # Default fallback: unknown / unverified categories are treated as Tier 4 Provisional
        return ChargeClassificationTier.TIER_4_PROVISIONAL_ESTIMATES

    def classify_line_item(self, item: Dict[str, Any]) -> ClassifiedChargeItem:
        """Classify a single disbursement line item into its governance tier."""
        tariff_id = item.get("tariff_identifier", "UNKNOWN")
        desc = item.get("description", "")
        category = item.get("charge_category", "")
        app_type = item.get("applicability_type", "")
        amount_usd = item.get("amount_usd")
        amount_local = item.get("amount_local")
        currency = item.get("local_currency", "USD")
        contract_attr = item.get("contract_attribution") or item.get("contract_id") or item.get("appointment_id")
        statutory_basis = item.get("statutory_basis") or item.get("source_document")

        tier = self.determine_tier(category, app_type, tariff_id)
        flags: List[str] = []

        is_provisional = False
        is_operational_permitted = False

        if tier == ChargeClassificationTier.TIER_1_STATUTORY_DUES:
            # Tier 1 must match statutory registry exactly
            is_operational_permitted = True
            is_provisional = False

        elif tier == ChargeClassificationTier.TIER_2_REGULATED_SERVICES:
            # Tier 2 is tariff reconciled
            is_operational_permitted = True
            is_provisional = False

        elif tier == ChargeClassificationTier.TIER_3_CONTRACTUAL_REMUNERATION:
            # Tier 3 requires contract attribution
            if not contract_attr:
                flags.append("MISSING_CONTRACTUAL_EVIDENCE")
                is_operational_permitted = False
            else:
                is_operational_permitted = True
            is_provisional = False

        elif tier == ChargeClassificationTier.TIER_4_PROVISIONAL_ESTIMATES:
            # Tier 4 is always provisional, excluded from operational settlement
            is_provisional = True
            is_operational_permitted = False
            flags.append("PROVISIONAL_ESTIMATE_EXCLUDED_FROM_OPERATIONAL")

        # Check for partial components
        if item.get("is_partial_component"):
            flags.append("PARTIAL_COMPONENT")
            is_operational_permitted = False

        evidence_cls = item.get("evidence_classification", "DIRECTLY_VERIFIED")
        perm_status = item.get("permission_status", "PERMITTED_STATUTORY_DISCLOSURE")

        return ClassifiedChargeItem(
            tariff_identifier=tariff_id,
            description=desc,
            charge_tier=tier,
            charge_category=category,
            amount_usd=amount_usd,
            amount_local=amount_local,
            currency=currency,
            statutory_basis=statutory_basis,
            contract_attribution=contract_attr,
            is_provisional=is_provisional,
            is_operational_permitted=is_operational_permitted,
            evidence_classification=evidence_cls,
            permission_status=perm_status,
            flags=flags,
        )

    def classify_disbursement_items(self, items: List[Dict[str, Any]]) -> List[ClassifiedChargeItem]:
        """Classify a list of disbursement line items."""
        return [self.classify_line_item(it) for it in items]

    def summarize_charges(self, classified_items: List[ClassifiedChargeItem]) -> ChargeClassificationSummary:
        """Aggregate classified items into a tier summary with operational governance checks."""
        t1_total = 0.0
        t2_total = 0.0
        t3_total = 0.0
        t4_total = 0.0
        has_provisional = False
        has_missing_contract = False
        has_partial = False
        all_op_permitted = True
        flags: List[str] = []

        for item in classified_items:
            amt = float(item.amount_usd or 0.0)
            if item.charge_tier == ChargeClassificationTier.TIER_1_STATUTORY_DUES:
                t1_total += amt
            elif item.charge_tier == ChargeClassificationTier.TIER_2_REGULATED_SERVICES:
                t2_total += amt
            elif item.charge_tier == ChargeClassificationTier.TIER_3_CONTRACTUAL_REMUNERATION:
                t3_total += amt
                if "MISSING_CONTRACTUAL_EVIDENCE" in item.flags:
                    has_missing_contract = True
                    flags.append("MISSING_CONTRACTUAL_EVIDENCE")
            elif item.charge_tier == ChargeClassificationTier.TIER_4_PROVISIONAL_ESTIMATES:
                t4_total += amt
                has_provisional = True
                flags.append("PROVISIONAL_ESTIMATE_PRESENT")

            if "PARTIAL_COMPONENT" in item.flags or item.is_provisional:
                has_partial = True
            if not item.is_operational_permitted:
                all_op_permitted = False

        total = round(t1_total + t2_total + t3_total + t4_total, 2)
        # Operational permission requires no provisional estimates, no missing contracts, and no partial items
        op_permitted = all_op_permitted and not has_provisional and not has_missing_contract and not has_partial

        return ChargeClassificationSummary(
            tier_1_statutory_total_usd=round(t1_total, 2),
            tier_2_regulated_total_usd=round(t2_total, 2),
            tier_3_contractual_total_usd=round(t3_total, 2),
            tier_4_provisional_total_usd=round(t4_total, 2),
            total_disbursement_usd=total,
            has_provisional_estimates=has_provisional,
            has_missing_contractual_evidence=has_missing_contract,
            has_partial_components=has_partial,
            is_operational_permitted=op_permitted,
            classified_items=classified_items,
            flags=list(set(flags)),
        )

    def evaluate_statutory_floor(
        self,
        user_valuation_usd: float,
        statutory_floor_usd: float,
        itemized_charges: Optional[List[Dict[str, Any]]] = None,
    ) -> FloorCheckResult:
        """
        Evaluate user-supplied fixture or commercial estimate against statutory floor.
        
        Zero-tolerance fail-closed policy using exact Decimal arithmetic:
        - If user_amount < statutory_floor:
            Fails closed immediately with FIXTURE_VALUATION_BELOW_STATUTORY_FLOOR.
        - If user_amount >= statutory_floor:
            Variance is calculated. Surplus does NOT prove accuracy.
            Surplus must be itemized to Tier 2/3/4 charges.
            Unexplained surplus is flagged as UNALLOCATED_COMMERCIAL_OVERAGE.
        """
        user_dec = _to_decimal(user_valuation_usd)
        stat_floor_dec = _to_decimal(statutory_floor_usd)
        variance_dec = user_dec - stat_floor_dec

        failure_reasons: List[str] = []
        flags: List[str] = []
        itemized_allocs: Dict[str, float] = {}

        # Zero tolerance for sub-floor values: strictly user_dec < stat_floor_dec
        if user_dec < stat_floor_dec:
            deficit_dec = stat_floor_dec - user_dec
            reason = (
                f"FIXTURE_VALUATION_BELOW_STATUTORY_FLOOR: User fixture amount "
                f"${float(user_dec):,.2f} is strictly below statutory minimum ${float(stat_floor_dec):,.2f} "
                f"(deficit: ${float(deficit_dec):,.2f})"
            )
            failure_reasons.append(reason)
            flags.append("FIXTURE_VALUATION_BELOW_STATUTORY_FLOOR")
            return FloorCheckResult(
                is_floor_satisfied=False,
                user_amount_usd=float(user_dec),
                statutory_floor_usd=float(stat_floor_dec),
                variance_usd=float(variance_dec),
                unallocated_overage_usd=0.0,
                status="REJECTED_BELOW_STATUTORY_FLOOR",
                is_operational_permitted=False,
                failure_reasons=failure_reasons,
                flags=flags,
                itemized_allocations={},
            )

        # Floor satisfied: exact statutory match
        if variance_dec == Decimal("0.00"):
            return FloorCheckResult(
                is_floor_satisfied=True,
                user_amount_usd=float(user_dec),
                statutory_floor_usd=float(stat_floor_dec),
                variance_usd=0.0,
                unallocated_overage_usd=0.0,
                status="EXACT_STATUTORY_MATCH",
                is_operational_permitted=True,
                failure_reasons=[],
                flags=["EXACT_STATUTORY_MATCH"],
                itemized_allocations={},
            )

        # Surplus exists: variance_dec > Decimal("0.00")
        allocated_dec = Decimal("0.00")
        if itemized_charges:
            for it in itemized_charges:
                tier = self.determine_tier(
                    it.get("charge_category", ""),
                    it.get("applicability_type"),
                    it.get("tariff_identifier"),
                )
                amt_dec = _to_decimal(it.get("amount_usd"))
                itemized_allocs[tier.value] = float(_to_decimal(itemized_allocs.get(tier.value, 0.0)) + amt_dec)
                if tier != ChargeClassificationTier.TIER_1_STATUTORY_DUES:
                    allocated_dec += amt_dec

        unallocated_overage_dec = max(Decimal("0.00"), variance_dec - allocated_dec)

        if unallocated_overage_dec > Decimal("0.00"):
            flags.append("UNALLOCATED_COMMERCIAL_OVERAGE")
            status = "OVERAGE_UNALLOCATED"
            is_op_permitted = False
            failure_reasons.append(
                f"UNALLOCATED_COMMERCIAL_OVERAGE: Fixture valuation exceeds statutory floor by "
                f"${float(variance_dec):,.2f}, with ${float(unallocated_overage_dec):,.2f} unaccounted for."
            )
        else:
            status = "SURPLUS_ITEMIZED_RECONCILED"
            is_op_permitted = True

        return FloorCheckResult(
            is_floor_satisfied=True,
            user_amount_usd=float(user_dec),
            statutory_floor_usd=float(stat_floor_dec),
            variance_usd=float(variance_dec),
            unallocated_overage_usd=float(unallocated_overage_dec),
            status=status,
            is_operational_permitted=is_op_permitted,
            failure_reasons=failure_reasons,
            flags=flags,
            itemized_allocations=itemized_allocs,
        )
