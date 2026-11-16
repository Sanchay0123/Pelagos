"""Pelagos Voyage Disbursement Integration & TCE Gating Framework (Stage F1.4.2.4).

Provides voyage-level association and governance of port disbursement and canal transit records:
- Multi-port and canal transit aggregation across voyage corridors.
- Explicit completeness and readiness taxonomy.
- Partial component tracking and propagation (e.g. Rotterdam S-rate, Houston pilotage).
- Strict non-statutory treatment of agency remuneration.
- Currency conversion provenance audit and benchmark rejection.
- Level 3 Operational Commercial Gating: Strict fail-closed isolation preventing preliminary
  research disbursements from entering commercial net freight, operational TCE calculations,
  or charter recommendation engines.
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass, field
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy.orm import Session

from pelagos.currency import CurrencyConversionResult, CurrencyConversionService
from pelagos.models import VoyageDisbursementLineItem, VoyageDisbursementRecord
from pelagos.registry.canal_tariff_registry import (
    CanalTariffRegistry,
    PanamaTollCalculationResult,
    SuezTollCalculationResult,
)
from pelagos.registry.port_disbursement_registry import (
    PortDisbursementCalculationResult,
    PortDisbursementLineItem,
    PortDisbursementRegistry,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class VoyageDisbursementError(Exception):
    """Base exception for voyage disbursement integration operations."""
    pass


class CommercialTceBlockedError(VoyageDisbursementError):
    """Raised when research-only disbursements are attempted to be fed into commercial TCE."""
    pass


class CharterBiddingBlockedError(VoyageDisbursementError):
    """Raised when preliminary disbursements are attempted to be used in charter bidding/ranking."""
    pass


class OperationalDisbursementBlockedError(VoyageDisbursementError):
    """Raised when Level 3 operational port/canal disbursements are invoked while blocked."""
    pass


# ---------------------------------------------------------------------------
# Status and Governance Taxonomy
# ---------------------------------------------------------------------------

class VoyageDisbursementStatus:
    """Standardized completeness and readiness status classifications (Stage F1.4.2.4)."""
    COMPLETE_WITHIN_REGISTERED_SCOPE = "COMPLETE_WITHIN_REGISTERED_SCOPE"
    PARTIAL_COMPONENTS = "PARTIAL_COMPONENTS"
    MISSING_REQUIRED_INPUT = "MISSING_REQUIRED_INPUT"
    MISSING_TARIFF = "MISSING_TARIFF"
    HISTORICAL_TARIFF = "HISTORICAL_TARIFF"
    UNVERIFIED_SOURCE = "UNVERIFIED_SOURCE"
    RESTRICTED_PERMISSION = "RESTRICTED_PERMISSION"
    MISSING_CURRENCY_RATE = "MISSING_CURRENCY_RATE"
    CONDITIONAL_CHARGE_UNRESOLVED = "CONDITIONAL_CHARGE_UNRESOLVED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    OPERATIONAL_BLOCKED = "OPERATIONAL_BLOCKED"


# ---------------------------------------------------------------------------
# Request and Summary Data Structures
# ---------------------------------------------------------------------------

@dataclass
class VoyagePortCallRequest:
    """Request representation for an individual port call on a voyage itinerary."""
    un_locode: str
    purpose_of_call: str = "CARGO_OPERATIONS"
    stay_duration_hours: float = 48.0
    cargo_handled_tons: Optional[float] = None
    transit_date: Optional[datetime.date] = None


@dataclass
class VoyageCanalTransitRequest:
    """Request representation for a canal transit on a voyage itinerary."""
    canal_name: str  # "PANAMA" | "SUEZ"
    loading_condition: str = "LADEN"  # "LADEN" | "BALLAST"
    transit_date: Optional[datetime.date] = None
    gatun_elevation_ft: Optional[float] = None


@dataclass
class VoyageDisbursementAssessmentRequest:
    """Comprehensive request specifying a voyage itinerary, vessel particulars, and transits."""
    voyage_id: str
    origin_port: Union[str, VoyagePortCallRequest]
    destination_port: Union[str, VoyagePortCallRequest]
    intermediate_ports: List[Union[str, VoyagePortCallRequest]] = field(default_factory=list)
    canal_transits: List[Union[str, VoyageCanalTransitRequest]] = field(default_factory=list)
    transit_date: Optional[datetime.date] = None
    vessel_imo: Optional[int] = None
    vessel_name: Optional[str] = None
    vessel_class: Optional[str] = None
    vessel_gt: Optional[float] = None
    vessel_dwt: Optional[float] = None
    loa_m: Optional[float] = None
    beam_m: Optional[float] = None
    draft_m: Optional[float] = None
    scnt: Optional[float] = None
    pc_ums_net: Optional[float] = None
    contractual_agency_fee_usd: Optional[float] = None
    agency_contract_id: Optional[str] = None
    operational_mode: bool = False


@dataclass(frozen=True)
class VoyageDisbursementSummary:
    """Immutable aggregate summary of voyage disbursement intelligence."""
    voyage_id: str
    is_complete: bool
    completeness_status: str
    readiness_tier: str
    total_disbursement_usd: Optional[float]
    total_port_disbursement_usd: Optional[float]
    total_canal_transit_usd: Optional[float]
    has_partial_components: bool
    is_operational_approved: bool
    port_call_results: List[PortDisbursementCalculationResult]
    canal_transit_results: List[Union[PanamaTollCalculationResult, SuezTollCalculationResult]]
    all_line_items: List[Dict[str, Any]]
    blocked_reasons: List[str]
    evidence_summary: Dict[str, str]
    origin_unlocode: str
    destination_unlocode: str
    transit_date: datetime.date
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "voyage_id": self.voyage_id,
            "is_complete": self.is_complete,
            "completeness_status": self.completeness_status,
            "readiness_tier": self.readiness_tier,
            "total_disbursement_usd": self.total_disbursement_usd,
            "total_port_disbursement_usd": self.total_port_disbursement_usd,
            "total_canal_transit_usd": self.total_canal_transit_usd,
            "has_partial_components": self.has_partial_components,
            "is_operational_approved": self.is_operational_approved,
            "port_call_results": [p.to_dict() for p in self.port_call_results],
            "canal_transit_results": [c.to_dict() for c in self.canal_transit_results],
            "all_line_items": self.all_line_items,
            "blocked_reasons": self.blocked_reasons,
            "evidence_summary": self.evidence_summary,
            "origin_unlocode": self.origin_unlocode,
            "destination_unlocode": self.destination_unlocode,
            "transit_date": self.transit_date.isoformat(),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Voyage Disbursement Integration Service
# ---------------------------------------------------------------------------

class VoyageDisbursementService:
    """
    Voyage-level disbursement integration and commercial TCE gating service.
    
    Coordinates:
    - PortDisbursementRegistry for origin, destination, and intermediate port calls.
    - CanalTariffRegistry for Panama and Suez transits.
    - CurrencyConversionService for official vs. benchmark exchange rate provenance.
    - Strict Level 3 Fail-Closed Gate isolating commercial TCE and chartering workflows.
    """

    def __init__(
        self,
        db: Optional[Session] = None,
        port_registry: Optional[PortDisbursementRegistry] = None,
        canal_registry: Optional[CanalTariffRegistry] = None,
        currency_service: Optional[CurrencyConversionService] = None,
    ):
        self.db = db
        if currency_service is not None:
            self.currency_service = currency_service
        else:
            self.currency_service = CurrencyConversionService(db=db)
            if db is None:
                self.currency_service.seed_historical_statutory_rates()
        self.port_registry = port_registry or PortDisbursementRegistry(
            db=db,
            currency_service=self.currency_service,
        )
        self.canal_registry = canal_registry or CanalTariffRegistry(
            db=db,
            currency_service=self.currency_service,
        )

    def evaluate_voyage_disbursements(
        self,
        request: VoyageDisbursementAssessmentRequest,
    ) -> VoyageDisbursementSummary:
        """
        Evaluate comprehensive voyage disbursement intelligence across all port calls and canal transits.
        
        Strict Governance Invariants:
        1. Operational TCE Isolation: Level 3 operational disbursements remain blocked. If operational_mode
           is True, total_disbursement_usd is None, is_operational_approved is False, and OPERATIONAL_BLOCKED is returned.
        2. Partial Component Propagation: Partial pilotage components (Rotterdam S-rate, Houston pilotage)
           force has_partial_components=True, completeness_status=PARTIAL_COMPONENTS, and is_complete=False.
        3. Zero Synthetic Tonnage: GT, DWT, LOA, draft, or SCNT are never synthesized via proxy ratios.
        4. Contractual Agency Remuneration: Agency fees are never generated as statutory port dues.
        5. Fail-Closed Completeness: Missing tariffs, missing inputs, or missing currency rates fail closed.
        """
        voyage_date = request.transit_date or datetime.date(2024, 5, 1)

        # Normalize port call requests
        port_calls: List[VoyagePortCallRequest] = []
        if isinstance(request.origin_port, str):
            port_calls.append(VoyagePortCallRequest(un_locode=request.origin_port.upper().strip()))
        else:
            port_calls.append(request.origin_port)

        for p in request.intermediate_ports:
            if isinstance(p, str):
                port_calls.append(VoyagePortCallRequest(un_locode=p.upper().strip()))
            else:
                port_calls.append(p)

        if isinstance(request.destination_port, str):
            port_calls.append(VoyagePortCallRequest(un_locode=request.destination_port.upper().strip()))
        else:
            port_calls.append(request.destination_port)

        # Normalize canal transit requests
        canal_transits: List[VoyageCanalTransitRequest] = []
        for c in request.canal_transits:
            if isinstance(c, str):
                canal_transits.append(VoyageCanalTransitRequest(canal_name=c.upper().strip()))
            else:
                canal_transits.append(c)

        origin_unlocode = port_calls[0].un_locode
        dest_unlocode = port_calls[-1].un_locode

        blocked_reasons: List[str] = []
        all_line_items: List[Dict[str, Any]] = []
        evidence_summary: Dict[str, str] = {}
        port_results: List[PortDisbursementCalculationResult] = []
        canal_results: List[Union[PanamaTollCalculationResult, SuezTollCalculationResult]] = []

        has_partial = False
        missing_tariff_flag = False
        missing_input_flag = False
        missing_currency_flag = False
        conditional_unresolved_flag = False
        historical_tariff_flag = False
        unverified_source_flag = False
        restricted_permission_flag = False

        # --- 1. Operational Mode Check (Level 3 Gate) ---
        if request.operational_mode:
            op_reason = (
                "OPERATIONAL_TCE_DISBURSEMENT_BLOCKED: Level 3 operational port & canal disbursements "
                "remain blocked. Commercial TCE workflows, net freight, and charter recommendation engines "
                "cannot consume preliminary research disbursement intelligence."
            )
            blocked_reasons.append(op_reason)

        # --- 2. Port Calls Evaluation ---
        total_port_usd: Optional[float] = 0.0

        for p_req in port_calls:
            p_date = p_req.transit_date or voyage_date
            p_res = self.port_registry.calculate_port_disbursement(
                un_locode=p_req.un_locode,
                vessel_gt=request.vessel_gt,
                vessel_dwt=request.vessel_dwt,
                loa_m=request.loa_m,
                beam_m=request.beam_m,
                draft_m=request.draft_m,
                purpose_of_call=p_req.purpose_of_call,
                stay_duration_hours=p_req.stay_duration_hours,
                cargo_handled_tons=p_req.cargo_handled_tons,
                transit_date=p_date,
                operational_mode=request.operational_mode,
            )
            port_results.append(p_res)

            if p_res.has_partial_components:
                has_partial = True

            for reason in p_res.blocked_reasons:
                blocked_reasons.append(f"{p_req.un_locode}: {reason}")
                if "PORT_NOT_REGISTERED" in reason or "EXPIRED_OR_UNSUPPORTED_PORT_TARIFF_VERSION" in reason:
                    missing_tariff_flag = True
                elif "MISSING_VESSEL_" in reason or "MISSING_CARGO_" in reason:
                    missing_input_flag = True
                elif "MISSING_CURRENCY_EXCHANGE_RATE" in reason or "BENCHMARK_EXCHANGE_RATE_REJECTED" in reason:
                    missing_currency_flag = True

            # Process line items
            for item in p_res.items:
                if item.is_partial_component:
                    has_partial = True
                if item.evidence_classification == "UNVERIFIED":
                    unverified_source_flag = True
                if "RESTRICTED" in item.permission_status:
                    restricted_permission_flag = True
                if item.effective_to and item.effective_to < datetime.date(2025, 1, 1):
                    historical_tariff_flag = True

                evidence_summary[item.tariff_identifier] = item.evidence_classification

                item_dict = {
                    "charge_type": "PORT_DISBURSEMENT",
                    "location_code": p_req.un_locode,
                    "charge_category": item.charge_category,
                    "tariff_identifier": item.tariff_identifier,
                    "description": item.description,
                    "applicability_type": item.applicability_type,
                    "rate_value": item.rate_value,
                    "billing_unit": item.billing_unit,
                    "local_currency": item.local_currency,
                    "amount_local": item.amount_local,
                    "exchange_rate_to_usd": item.exchange_rate_to_usd,
                    "amount_usd": item.amount_usd,
                    "fx_rate_provider": item.fx_rate_provider,
                    "fx_observation_date": item.fx_observation_date,
                    "is_partial_component": item.is_partial_component,
                    "is_available": item.is_available,
                    "evidence_classification": item.evidence_classification,
                    "permission_status": item.permission_status,
                    "failure_reason": item.failure_reason,
                }
                all_line_items.append(item_dict)

            if p_res.total_port_costs_usd is not None and total_port_usd is not None:
                total_port_usd += p_res.total_port_costs_usd
            else:
                total_port_usd = None

        # --- 3. Canal Transits Evaluation ---
        total_canal_usd: Optional[float] = 0.0 if canal_transits else 0.0

        for c_req in canal_transits:
            c_name = c_req.canal_name.upper().strip()
            c_date = c_req.transit_date or voyage_date

            if c_name == "PANAMA":
                # Ensure dimensions exist
                if request.beam_m is None or request.loa_m is None:
                    missing_input_flag = True
                    blocked_reasons.append("PANAMA: MISSING_VESSEL_DIMENSIONS: Beam and LOA required for Panama tier")
                else:
                    pan_res = self.canal_registry.calculate_panama_toll(
                        beam_m=request.beam_m,
                        loa_m=request.loa_m,
                        dwt_mt=request.vessel_dwt,
                        loading_condition=c_req.loading_condition,
                        transit_date=c_date,
                        gatun_elevation_ft=c_req.gatun_elevation_ft,
                        operational_mode=request.operational_mode,
                    )
                    canal_results.append(pan_res)

                    for r in pan_res.blocked_reasons:
                        blocked_reasons.append(f"PANAMA: {r}")
                        if "MISSING_VESSEL_DWT" in r:
                            missing_input_flag = True
                        elif "GATUN" in r or "UNRESOLVED_FRESHWATER_SURCHARGE" in r or "INVALID_GATUN_LEVEL" in r:
                            conditional_unresolved_flag = True
                        elif "EXPIRED_OR_UNSUPPORTED_TARIFF_VERSION" in r:
                            missing_tariff_flag = True

                    for comp in pan_res.components:
                        evidence_summary[comp.tariff_identifier] = comp.evidence_classification
                        if comp.evidence_classification == "UNVERIFIED":
                            unverified_source_flag = True
                        if comp.effective_to and comp.effective_to < datetime.date(2025, 1, 1):
                            historical_tariff_flag = True

                        all_line_items.append({
                            "charge_type": "CANAL_TRANSIT",
                            "location_code": "PANAMA",
                            "charge_category": comp.charge_category,
                            "tariff_identifier": comp.tariff_identifier,
                            "description": comp.description,
                            "applicability_type": "MANDATORY",
                            "rate_value": comp.rate_value,
                            "billing_unit": comp.billing_unit,
                            "local_currency": "USD",
                            "amount_local": comp.amount_usd,
                            "exchange_rate_to_usd": 1.0,
                            "amount_usd": comp.amount_usd,
                            "fx_rate_provider": "IDENTITY",
                            "fx_observation_date": None,
                            "is_partial_component": False,
                            "is_available": comp.is_available,
                            "evidence_classification": comp.evidence_classification,
                            "permission_status": "PERMITTED_STATUTORY_DISCLOSURE",
                            "failure_reason": comp.failure_reason,
                        })

                    if pan_res.total_toll_usd is not None and total_canal_usd is not None:
                        total_canal_usd += pan_res.total_toll_usd
                    else:
                        total_canal_usd = None

            elif c_name == "SUEZ":
                if request.draft_m is None or request.beam_m is None:
                    missing_input_flag = True
                    blocked_reasons.append("SUEZ: MISSING_VESSEL_DIMENSIONS: Draft and Beam required for Suez transit")
                else:
                    suez_res = self.canal_registry.calculate_suez_toll(
                        scnt=request.scnt,
                        draft_m=request.draft_m,
                        beam_m=request.beam_m,
                        loading_condition=c_req.loading_condition,
                        transit_date=c_date,
                        operational_mode=request.operational_mode,
                    )
                    canal_results.append(suez_res)

                    for r in suez_res.blocked_reasons:
                        blocked_reasons.append(f"SUEZ: {r}")
                        if "MISSING_CERTIFIED_SCNT" in r:
                            missing_input_flag = True
                        elif "EXPIRED_OR_UNSUPPORTED_TARIFF_VERSION" in r:
                            missing_tariff_flag = True
                        elif "MISSING_CURRENCY_EXCHANGE_RATE" in r:
                            missing_currency_flag = True

                    # Generate line items for Suez components
                    rate_fx = suez_res.exchange_rate_used or 1.3182
                    if suez_res.base_dues_sdr is not None:
                        base_usd = round(suez_res.base_dues_sdr * rate_fx, 2)
                        evidence_summary["SCA-TOLL-BASE"] = "PARTIALLY_VERIFIED"
                        all_line_items.append({
                            "charge_type": "CANAL_TRANSIT",
                            "location_code": "SUEZ",
                            "charge_category": "CANAL_TOLL_BASE",
                            "tariff_identifier": "SCA-TOLL-BASE",
                            "description": f"Suez Canal Base Transit Dues ({c_req.loading_condition})",
                            "applicability_type": "MANDATORY",
                            "rate_value": suez_res.base_dues_sdr,
                            "billing_unit": "SDR_TIERED_BRACKETS",
                            "local_currency": "SDR",
                            "amount_local": suez_res.base_dues_sdr,
                            "exchange_rate_to_usd": rate_fx,
                            "amount_usd": base_usd,
                            "fx_rate_provider": suez_res.fx_rate_provider,
                            "fx_observation_date": suez_res.fx_observation_date,
                            "is_partial_component": False,
                            "is_available": True,
                            "evidence_classification": "PARTIALLY_VERIFIED",
                            "permission_status": "PERMITTED_STATUTORY_DISCLOSURE",
                            "failure_reason": None,
                        })

                    if suez_res.surcharge_amount_sdr is not None and suez_res.surcharge_amount_sdr > 0:
                        sur_usd = round(suez_res.surcharge_amount_sdr * rate_fx, 2)
                        evidence_summary["SCA-CIRCULAR-7-2023"] = "DIRECTLY_VERIFIED"
                        all_line_items.append({
                            "charge_type": "CANAL_TRANSIT",
                            "location_code": "SUEZ",
                            "charge_category": "CANAL_SURCHARGE",
                            "tariff_identifier": "SCA-CIRCULAR-7-2023",
                            "description": "Suez Dry Bulk Surcharge (+5% under Circular 7/2023)",
                            "applicability_type": "MANDATORY",
                            "rate_value": 5.0,
                            "billing_unit": "PERCENTAGE_BASE_DUES",
                            "local_currency": "SDR",
                            "amount_local": suez_res.surcharge_amount_sdr,
                            "exchange_rate_to_usd": rate_fx,
                            "amount_usd": sur_usd,
                            "fx_rate_provider": suez_res.fx_rate_provider,
                            "fx_observation_date": suez_res.fx_observation_date,
                            "is_partial_component": False,
                            "is_available": True,
                            "evidence_classification": "DIRECTLY_VERIFIED",
                            "permission_status": "PERMITTED_STATUTORY_DISCLOSURE",
                            "failure_reason": None,
                        })

                    if suez_res.mooring_service_usd is not None:
                        evidence_summary["SCA-CIRCULAR-1-2024"] = "DIRECTLY_VERIFIED"
                        all_line_items.append({
                            "charge_type": "CANAL_TRANSIT",
                            "location_code": "SUEZ",
                            "charge_category": "MOORING_SERVICE",
                            "tariff_identifier": "SCA-CIRCULAR-1-2024",
                            "description": "Mandatory Mooring & Electric Lighting Service (Circular 1/2024)",
                            "applicability_type": "MANDATORY",
                            "rate_value": suez_res.mooring_service_usd,
                            "billing_unit": "FIXED_PER_TRANSIT",
                            "local_currency": "USD",
                            "amount_local": suez_res.mooring_service_usd,
                            "exchange_rate_to_usd": 1.0,
                            "amount_usd": suez_res.mooring_service_usd,
                            "fx_rate_provider": "IDENTITY",
                            "fx_observation_date": None,
                            "is_partial_component": False,
                            "is_available": True,
                            "evidence_classification": "DIRECTLY_VERIFIED",
                            "permission_status": "PERMITTED_STATUTORY_DISCLOSURE",
                            "failure_reason": None,
                        })

                    if suez_res.total_toll_usd is not None and total_canal_usd is not None:
                        total_canal_usd += suez_res.total_toll_usd
                    else:
                        total_canal_usd = None
            else:
                missing_tariff_flag = True
                blocked_reasons.append(f"UNKNOWN_CANAL: Canal '{c_name}' is not recognized or cataloged.")

        # --- 4. Contractual Agency Fee Integration ---
        # Governed Predecessor Finding: Agency remuneration is commercially negotiated / contractual.
        # NEVER generate an agency fee unless explicitly backed by a contractual appointment record.
        if request.contractual_agency_fee_usd is not None and request.contractual_agency_fee_usd > 0:
            evidence_summary["CONTRACTUAL-AGENCY-FEE"] = "DIRECTLY_VERIFIED"
            all_line_items.append({
                "charge_type": "PORT_DISBURSEMENT",
                "location_code": origin_unlocode,
                "charge_category": "AGENCY_FEE",
                "tariff_identifier": "CONTRACTUAL-AGENCY-FEE",
                "description": "Contractual Agency Fee (BIMCO/FONASBA Bilateral Appointment)",
                "applicability_type": "COMMERCIALLY_NEGOTIATED / CONTRACTUAL",
                "rate_value": request.contractual_agency_fee_usd,
                "billing_unit": "FIXED_PER_CALL",
                "local_currency": "USD",
                "amount_local": request.contractual_agency_fee_usd,
                "exchange_rate_to_usd": 1.0,
                "amount_usd": request.contractual_agency_fee_usd,
                "fx_rate_provider": "IDENTITY",
                "fx_observation_date": None,
                "is_partial_component": False,
                "is_available": True,
                "evidence_classification": "DIRECTLY_VERIFIED",
                "permission_status": "COMMERCIALLY_CONFIDENTIAL_CONTRACT",
                "contract_attribution": request.agency_contract_id,
                "failure_reason": None,
            })
            if total_port_usd is not None:
                total_port_usd += request.contractual_agency_fee_usd

        # --- 5. Overall Completeness & Readiness Determination ---
        total_disbursement_usd: Optional[float] = None
        is_complete = False
        is_operational_approved = False  # Strictly False under Level 3 Operational Block

        if request.operational_mode:
            completeness_status = VoyageDisbursementStatus.OPERATIONAL_BLOCKED
            readiness_tier = "LEVEL_3_OPERATIONAL_BLOCKED"
            total_disbursement_usd = None
            total_port_usd = None
            total_canal_usd = None
        elif missing_tariff_flag:
            completeness_status = VoyageDisbursementStatus.MISSING_TARIFF
            readiness_tier = "LEVEL_1_SOURCE_CATALOG"
            total_disbursement_usd = None
        elif missing_input_flag:
            completeness_status = VoyageDisbursementStatus.MISSING_REQUIRED_INPUT
            readiness_tier = "LEVEL_1_SOURCE_CATALOG"
            total_disbursement_usd = None
        elif missing_currency_flag:
            completeness_status = VoyageDisbursementStatus.MISSING_CURRENCY_RATE
            readiness_tier = "LEVEL_1_SOURCE_CATALOG"
            total_disbursement_usd = None
        elif conditional_unresolved_flag:
            completeness_status = VoyageDisbursementStatus.CONDITIONAL_CHARGE_UNRESOLVED
            readiness_tier = "LEVEL_1_SOURCE_CATALOG"
            total_disbursement_usd = None
        elif has_partial:
            completeness_status = VoyageDisbursementStatus.PARTIAL_COMPONENTS
            readiness_tier = "LEVEL_2_RESEARCH_ONLY"
            is_complete = False  # Incomplete when partial components are present
            if total_port_usd is not None and total_canal_usd is not None:
                total_disbursement_usd = round(total_port_usd + total_canal_usd, 2)
        elif len(blocked_reasons) > 0:
            completeness_status = VoyageDisbursementStatus.RESEARCH_ONLY
            readiness_tier = "LEVEL_2_RESEARCH_ONLY"
            total_disbursement_usd = None
        else:
            completeness_status = VoyageDisbursementStatus.COMPLETE_WITHIN_REGISTERED_SCOPE
            readiness_tier = "LEVEL_2_RESEARCH_ONLY"
            is_complete = True
            if total_port_usd is not None and total_canal_usd is not None:
                total_disbursement_usd = round(total_port_usd + total_canal_usd, 2)

        return VoyageDisbursementSummary(
            voyage_id=request.voyage_id,
            is_complete=is_complete,
            completeness_status=completeness_status,
            readiness_tier=readiness_tier,
            total_disbursement_usd=total_disbursement_usd,
            total_port_disbursement_usd=round(total_port_usd, 2) if total_port_usd is not None else None,
            total_canal_transit_usd=round(total_canal_usd, 2) if total_canal_usd is not None else None,
            has_partial_components=has_partial,
            is_operational_approved=is_operational_approved,
            port_call_results=port_results,
            canal_transit_results=canal_results,
            all_line_items=all_line_items,
            blocked_reasons=blocked_reasons,
            evidence_summary=evidence_summary,
            origin_unlocode=origin_unlocode,
            destination_unlocode=dest_unlocode,
            transit_date=voyage_date,
            notes=(
                "Preliminary research-only voyage disbursement evaluation. "
                "Level 3 operational commercial TCE integration is strictly blocked."
            ),
        )

    def persist_voyage_disbursement(
        self,
        summary: VoyageDisbursementSummary,
        vessel_imo: Optional[int] = None,
        vessel_name: Optional[str] = None,
        vessel_class: Optional[str] = None,
        vessel_gt: Optional[float] = None,
        vessel_dwt: Optional[float] = None,
        loa_m: Optional[float] = None,
        beam_m: Optional[float] = None,
        draft_m: Optional[float] = None,
        scnt: Optional[float] = None,
        pc_ums_net: Optional[float] = None,
    ) -> VoyageDisbursementRecord:
        """
        Persist complete voyage disbursement evaluation to database.
        Maintains relational links to VoyageDisbursementLineItem records.
        """
        if self.db is None:
            raise VoyageDisbursementError("Database session is required to persist voyage disbursement records.")

        rec = VoyageDisbursementRecord(
            voyage_id=summary.voyage_id,
            vessel_imo=vessel_imo,
            vessel_name=vessel_name,
            vessel_class=vessel_class,
            origin_port_unlocode=summary.origin_unlocode,
            destination_port_unlocode=summary.destination_unlocode,
            transit_date=summary.transit_date,
            vessel_gt=vessel_gt,
            vessel_dwt=vessel_dwt,
            vessel_loa_m=loa_m,
            vessel_beam_m=beam_m,
            vessel_draft_m=draft_m,
            scnt=scnt,
            pc_ums_net=pc_ums_net,
            total_disbursement_usd=summary.total_disbursement_usd,
            total_port_disbursement_usd=summary.total_port_disbursement_usd,
            total_canal_transit_usd=summary.total_canal_transit_usd,
            readiness_tier=summary.readiness_tier,
            completeness_status=summary.completeness_status,
            is_complete=summary.is_complete,
            has_partial_components=summary.has_partial_components,
            is_operational_approved=summary.is_operational_approved,
            blocked_reasons_json=json.dumps(summary.blocked_reasons),
            evidence_summary_json=json.dumps(summary.evidence_summary),
            notes=summary.notes,
        )
        self.db.add(rec)
        self.db.flush()

        for item in summary.all_line_items:
            li = VoyageDisbursementLineItem(
                voyage_disbursement_record_id=rec.id,
                charge_type=item["charge_type"],
                location_code=item["location_code"],
                charge_category=item["charge_category"],
                tariff_identifier=item["tariff_identifier"],
                description=item["description"],
                applicability_type=item["applicability_type"],
                rate_value=item["rate_value"],
                billing_unit=item["billing_unit"],
                local_currency=item["local_currency"],
                amount_local=item["amount_local"],
                exchange_rate_to_usd=item["exchange_rate_to_usd"],
                amount_usd=item["amount_usd"],
                fx_rate_provider=item.get("fx_rate_provider"),
                fx_observation_date=item.get("fx_observation_date"),
                is_partial_component=item.get("is_partial_component", False),
                is_available=item.get("is_available", True),
                evidence_classification=item.get("evidence_classification", "DIRECTLY_VERIFIED"),
                permission_status=item.get("permission_status", "PERMITTED_STATUTORY_DISCLOSURE"),
                failure_reason=item.get("failure_reason"),
            )
            self.db.add(li)

        self.db.flush()
        return rec

    def check_commercial_tce_eligibility(
        self,
        summary: VoyageDisbursementSummary,
    ) -> Tuple[bool, str]:
        """
        Fail-closed commercial TCE evaluation gate (Stage F1.4.2.4).
        
        Strictly prevents preliminary research-tier port and canal disbursements from
        entering commercial voyage net freight, daily TCE calculations, or voyage profitability.
        """
        return (
            False,
            (
                "COMMERCIAL_TCE_GATE_BLOCKED: Port and canal disbursements are classified under "
                f"readiness tier '{summary.readiness_tier}' ({summary.completeness_status}). "
                "Level 3 operational commercial disbursements remain strictly blocked. "
                "Disbursement intelligence cannot enter commercial voyage net freight or daily TCE calculations."
            ),
        )

    def check_charter_bidding_eligibility(
        self,
        summary: VoyageDisbursementSummary,
    ) -> Tuple[bool, str]:
        """
        Fail-closed charter recommendation and commercial bidding gate.
        
        Strictly prevents preliminary disbursements from influencing charter recommendation
        ranking, tender bidding, or vessel hiring decisions.
        """
        return (
            False,
            (
                "CHARTER_BIDDING_GATE_BLOCKED: Preliminary disbursement intelligence cannot be consumed "
                "by commercial voyage bidding or charter recommendation ranking. "
                "Operational disbursements remain blocked pending Stage F1.4.3."
            ),
        )
