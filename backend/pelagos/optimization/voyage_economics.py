"""
Pelagos Stage F1.4.3: Decoupled Voyage Economics & TCE Integration Service (P2-C Core).

Provides distinct calculations and output contracts for:
1. Gross Ocean Freight (cargo * freight rate) - Pure top-line commercial revenue.
2. Net Voyage Revenue (gross freight less verified commissions, port disbursements, canal tolls, and bunker fuel).
3. Conventional Voyage Time Charter Equivalent (TCE) Daily Rate (NVR / total voyage days).
   - INVARIANT: Conventional TCE does NOT deduct vessel OPEX or capital debt financing!
4. Owner Operating Profitability (TCE less daily vessel OPEX and daily capital debt amortization).
   - Modeled as an independent downstream accounting calculation.

Integrates with:
- Persisted VoyageDisbursementRecord with Merkle root and payload hash integrity checks.
- Seven-component voyage duration accounting for the TCE denominator.
- Demurrage/despatch adjustments affecting Net Revenue (numerator).
- Central bank currency lookback limits (IMF 4d, ECB 4d, MAS 5d).
- Token-gated Level 3 operational execution boundary (simulation only during Phase 2).
"""

from __future__ import annotations

import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pelagos.models import VoyageDisbursementRecord
from pelagos.readiness.fixture_validation import (
    CommercialFixturePayload,
    CommissionGovernanceTier,
    DurationStatus,
    VoyageDurationInput,
)
from pelagos.security.canonical_json import normalize_decimal_string
from pelagos.security.token_gate import OperationalUnlockGate, TokenValidationResult
from pelagos.audit.audit_verifier import DisbursementAuditVerifier


class GrossOceanFreightResult(BaseModel):
    """Output contract for Gross Ocean Freight calculation."""
    status: str
    cargo_tonnes: float
    freight_rate_usd_per_mt: float
    gross_freight_usd: float
    calculation_formula: str = "gross_freight_usd = cargo_tonnes * freight_rate_usd_per_mt"
    disclaimers: List[str] = Field(default_factory=list)


class NetVoyageRevenueResult(BaseModel):
    """Output contract for Net Voyage Revenue calculation."""
    status: str
    gross_freight_usd: float
    commission_rate: float
    commission_deductions_usd: float
    commission_governance_tier: str
    port_disbursements_usd: float
    canal_transits_usd: float
    bunker_fuel_costs_usd: float
    demurrage_despatch_usd: float
    net_voyage_revenue_usd: float
    calculation_formula: str = (
        "net_voyage_revenue_usd = gross_freight_usd * (1 - commission_rate) "
        "- port_disbursements_usd - canal_transits_usd - bunker_fuel_costs_usd "
        "+ demurrage_despatch_usd"
    )
    is_operational_approved: bool = False
    disclaimers: List[str] = Field(default_factory=list)


class VoyageTCEResult(BaseModel):
    """Output contract for Conventional Voyage Time Charter Equivalent (TCE)."""
    status: str
    net_voyage_revenue_usd: float
    duration_breakdown: Dict[str, Any]
    total_voyage_days: float
    tce_daily_rate_usd: float
    calculation_formula: str = "tce_daily_rate_usd = net_voyage_revenue_usd / total_voyage_days"
    is_operational_approved: bool = False
    governance_tier: str = "LEVEL_2_RESEARCH_ONLY"
    blocked_reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    disclaimers: List[str] = Field(default_factory=list)


class OwnerOperatingProfitabilityResult(BaseModel):
    """Output contract for Owner Operating Profitability (Independent Downstream Model)."""
    status: str
    tce_daily_rate_usd: float
    vessel_daily_opex_usd: float
    vessel_daily_capex_usd: float
    owner_daily_net_margin_usd: float
    total_voyage_days: float
    total_owner_profit_usd: float
    calculation_formula: str = (
        "owner_daily_net_margin_usd = tce_daily_rate_usd - vessel_daily_opex_usd - vessel_daily_capex_usd; "
        "total_owner_profit_usd = owner_daily_net_margin_usd * total_voyage_days"
    )
    disclaimers: List[str] = Field(default_factory=list)


class VoyageEconomicsService:
    """
    Decoupled Voyage Economics calculation and gating engine.
    """

    COMMERCIAL_DISCLAIMERS = [
        "Conventional Voyage TCE represents gross earnings per day available to cover vessel OPEX and capital costs.",
        "Conventional Voyage TCE does NOT deduct vessel OPEX or capital debt financing.",
        "Pelagos research TCE estimates are analytical benchmarks and must not be used as certified trade clearing prices.",
    ]

    def __init__(
        self,
        db: Optional[Session] = None,
        token_gate: Optional[OperationalUnlockGate] = None,
        audit_verifier: Optional[DisbursementAuditVerifier] = None,
    ):
        self.db = db
        self.token_gate = token_gate or OperationalUnlockGate()
        self.audit_verifier = audit_verifier or (DisbursementAuditVerifier(db) if db else None)

    def calculate_gross_ocean_freight(
        self,
        cargo_tonnes: float,
        freight_rate_usd_per_mt: float,
    ) -> GrossOceanFreightResult:
        """
        Calculate deterministic Gross Ocean Freight (cargo * freight rate).
        Strictly isolated from port disbursements, canal tolls, and bunker costs.
        """
        if cargo_tonnes <= 0:
            raise ValueError(f"Cargo tonnage must be strictly positive, received {cargo_tonnes}.")
        if freight_rate_usd_per_mt <= 0:
            raise ValueError(f"Freight rate must be strictly positive, received {freight_rate_usd_per_mt}.")

        c_dec = Decimal(str(cargo_tonnes))
        r_dec = Decimal(str(freight_rate_usd_per_mt))
        gross_dec = (c_dec * r_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return GrossOceanFreightResult(
            status="CALCULATED",
            cargo_tonnes=float(c_dec),
            freight_rate_usd_per_mt=float(r_dec),
            gross_freight_usd=float(gross_dec),
            disclaimers=[
                "Gross Ocean Freight represents pure cargo remuneration payable by charterer to shipowner.",
                "Excludes port disbursements, canal transit tolls, bunker fuel costs, and commissions.",
            ],
        )

    def calculate_net_voyage_revenue(
        self,
        gross_freight_usd: float,
        commission_rate: float,
        commission_tier: CommissionGovernanceTier,
        port_disbursements_usd: float,
        canal_transits_usd: float,
        bunker_fuel_costs_usd: float,
        demurrage_despatch_usd: float = 0.0,
        operational_mode: bool = False,
    ) -> NetVoyageRevenueResult:
        """
        Calculate Net Voyage Revenue (NVR) after direct voyage operating expenses.
        """
        g_dec = Decimal(str(gross_freight_usd))
        comm_rate_dec = Decimal(str(commission_rate))
        port_dec = Decimal(str(port_disbursements_usd))
        canal_dec = Decimal(str(canal_transits_usd))
        bunker_dec = Decimal(str(bunker_fuel_costs_usd))
        dem_dec = Decimal(str(demurrage_despatch_usd))

        comm_deduction = (g_dec * comm_rate_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        net_revenue = (g_dec - comm_deduction - port_dec - canal_dec - bunker_dec + dem_dec).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        disclaimers: List[str] = []
        if commission_tier == CommissionGovernanceTier.USER_RESEARCH_ASSUMPTION:
            disclaimers.append(
                "RESEARCH ASSUMPTION: Commission rate supplied by user; not verified by charterparty."
            )

        return NetVoyageRevenueResult(
            status="CALCULATED",
            gross_freight_usd=float(g_dec),
            commission_rate=float(comm_rate_dec),
            commission_deductions_usd=float(comm_deduction),
            commission_governance_tier=commission_tier.value,
            port_disbursements_usd=float(port_dec),
            canal_transits_usd=float(canal_dec),
            bunker_fuel_costs_usd=float(bunker_dec),
            demurrage_despatch_usd=float(dem_dec),
            net_voyage_revenue_usd=float(net_revenue),
            is_operational_approved=False,  # Enforced False in Phase 2
            disclaimers=disclaimers,
        )

    def calculate_voyage_tce(
        self,
        net_voyage_revenue_usd: float,
        duration: VoyageDurationInput,
        operational_mode: bool = False,
        operational_token: Optional[str] = None,
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> VoyageTCEResult:
        """
        Calculate Conventional Voyage Time Charter Equivalent (TCE) Daily Rate.
        
        INVARIANT: Conventional Voyage TCE strictly computes NVR / total_voyage_days.
        It does NOT deduct vessel OPEX or capital debt amortization.
        """
        blocked_reasons: List[str] = []
        warnings: List[str] = []
        disclaimers: List[str] = list(self.COMMERCIAL_DISCLAIMERS)

        total_days = duration.total_voyage_days
        if total_days <= 0:
            return VoyageTCEResult(
                status="INVALID_DURATION",
                net_voyage_revenue_usd=net_voyage_revenue_usd,
                duration_breakdown=duration.model_dump(),
                total_voyage_days=0.0,
                tce_daily_rate_usd=0.0,
                blocked_reasons=["Total voyage days must be strictly positive."],
            )

        # Operational Mode Gate Check
        is_operational_approved = False
        if operational_mode:
            # Phase 2 Hard Invariant: Operational calculations are locked
            blocked_reasons.append(
                "LEVEL_3_OPERATIONAL_BLOCKED: Operational commercial calculations remain strictly locked in Phase 2."
            )

            if not operational_token:
                blocked_reasons.append("MISSING_OPERATIONAL_TOKEN: Token required for operational mode.")
            elif execution_context:
                token_res = self.token_gate.verify_and_consume_token(
                    token_str=operational_token,
                    execution_context=execution_context,
                    db=self.db,
                )
                if not token_res.is_valid:
                    blocked_reasons.extend(token_res.failure_reasons)

        nvr_dec = Decimal(str(net_voyage_revenue_usd))
        days_dec = Decimal(str(total_days))
        tce_daily = (nvr_dec / days_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        dur_dict = {
            "laden_sea_days": duration.laden_sea_days,
            "ballast_sea_days": duration.ballast_sea_days,
            "loading_days": duration.loading_days,
            "discharge_days": duration.discharge_days,
            "port_waiting_days": duration.port_waiting_days,
            "canal_waiting_days": duration.canal_waiting_days,
            "canal_transit_days": duration.canal_transit_days,
            "total_voyage_days": total_days,
            "duration_status": duration.duration_status.value,
        }

        status = "CALCULATED" if not blocked_reasons else "BLOCKED_GATING_FAILED"

        return VoyageTCEResult(
            status=status,
            net_voyage_revenue_usd=float(nvr_dec),
            duration_breakdown=dur_dict,
            total_voyage_days=float(days_dec),
            tce_daily_rate_usd=float(tce_daily),
            is_operational_approved=False,  # Hard locked in Phase 2
            governance_tier="LEVEL_2_RESEARCH_ONLY",
            blocked_reasons=blocked_reasons,
            warnings=warnings,
            disclaimers=disclaimers,
        )

    def calculate_owner_operating_profitability(
        self,
        tce_daily_rate_usd: float,
        total_voyage_days: float,
        vessel_daily_opex_usd: float,
        vessel_daily_capex_usd: float = 0.0,
    ) -> OwnerOperatingProfitabilityResult:
        """
        Calculate Owner Operating Profitability as a separate downstream accounting model.
        Deducts daily technical OPEX and daily financing CapEx from daily TCE.
        """
        tce_dec = Decimal(str(tce_daily_rate_usd))
        days_dec = Decimal(str(total_voyage_days))
        opex_dec = Decimal(str(vessel_daily_opex_usd))
        capex_dec = Decimal(str(vessel_daily_capex_usd))

        daily_margin = (tce_dec - opex_dec - capex_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_profit = (daily_margin * days_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return OwnerOperatingProfitabilityResult(
            status="CALCULATED",
            tce_daily_rate_usd=float(tce_dec),
            vessel_daily_opex_usd=float(opex_dec),
            vessel_daily_capex_usd=float(capex_dec),
            owner_daily_net_margin_usd=float(daily_margin),
            total_voyage_days=float(days_dec),
            total_owner_profit_usd=float(total_profit),
            disclaimers=[
                "Owner Operating Profitability is an internal commercial accounting metric.",
                "It is strictly segregated from the conventional maritime Voyage TCE daily rate.",
            ],
        )

    def retrieve_and_verify_disbursement_record(
        self,
        record_id: int,
    ) -> tuple[Optional[VoyageDisbursementRecord], List[str]]:
        """
        Retrieve persisted disbursement record and verify its Merkle tree integrity.
        Detects tampering or unauthorized column mutations.
        """
        if not self.db:
            return None, ["Database session not initialized."]

        record = self.db.query(VoyageDisbursementRecord).filter(VoyageDisbursementRecord.id == record_id).first()
        if not record:
            return None, [f"Disbursement record ID {record_id} not found."]

        errors: List[str] = []
        if self.audit_verifier:
            integrity = self.audit_verifier.verify_record_integrity(record_id)
            if not integrity.is_intact:
                errors.extend(integrity.failure_reasons)

        return record, errors
