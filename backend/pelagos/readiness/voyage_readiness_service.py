"""
Pelagos Stage F1.4.3: Unified Voyage Readiness Assessment Service.
Aggregates and orchestrates:
  - Scope A: Central bank statutory FX valuation, provider lookback limits, and holiday calendars.
  - Scope B: Multi-dimensional vessel particulars readiness and zero synthetic tonnage enforcement.
  - Scope C: 10-criteria port and canal tariff applicability and completeness validation.
  - Scope D: Four-tier charge decomposition and one-way statutory floor enforcement.
  - Scope E: Unified voyage readiness classification (Research Ready, Incomplete, Partial, Qualified Estimate, Operational Blocked).
  - Scope F: Strict operational isolation - operational calculations remain strictly fail-closed during Phase 1.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from sqlalchemy.orm import Session

from pelagos.currency import (
    CurrencyConversionService,
    PROVIDER_MAX_LOOKBACK_CALENDAR_DAYS,
)
from pelagos.readiness.charge_classifier import (
    ChargeClassificationSummary,
    ChargeClassificationTier,
    ChargeClassifier,
    ClassifiedChargeItem,
    FloorCheckResult,
)
from pelagos.readiness.tariff_validation import (
    PortCanalTariffValidationSummary,
    TariffValidationService,
)
from pelagos.readiness.vessel_readiness import (
    VesselParticularEvidence,
    VesselReadinessAssessment,
    VesselReadinessService,
)
from pelagos.registry.voyage_disbursement_service import (
    VoyageDisbursementAssessmentRequest,
    VoyageDisbursementService,
    VoyageDisbursementSummary,
)


class VoyageReadinessStatus(str, Enum):
    """Unified voyage disbursement readiness classifications."""
    LEVEL_2_RESEARCH_READY = "LEVEL_2_RESEARCH_READY"
    LEVEL_2_RESEARCH_INCOMPLETE = "LEVEL_2_RESEARCH_INCOMPLETE"
    LEVEL_2_PARTIAL_COMPONENTS = "LEVEL_2_PARTIAL_COMPONENTS"
    LEVEL_2_QUALIFIED_COMMERCIAL_ESTIMATE = "LEVEL_2_QUALIFIED_COMMERCIAL_ESTIMATE"
    LEVEL_3_OPERATIONAL_BLOCKED = "LEVEL_3_OPERATIONAL_BLOCKED"


@dataclass
class VoyageReadinessAssessmentResult:
    """Complete multi-dimensional readiness assessment for a voyage."""
    voyage_id: str
    vessel_imo: Optional[int]
    vessel_name: Optional[str]
    origin_port: str
    destination_port: str
    canals: List[str]
    transit_date: datetime.date
    readiness_status: VoyageReadinessStatus
    readiness_tier: str  # Always LEVEL_2_RESEARCH_ONLY in Phase 1
    is_ready_for_research: bool
    is_ready_for_operations: bool  # Strictly False in Phase 1
    is_complete: bool
    has_partial_components: bool
    has_provisional_estimates: bool
    vessel_assessment: VesselReadinessAssessment
    tariff_summary: PortCanalTariffValidationSummary
    charge_summary: Optional[ChargeClassificationSummary] = None
    floor_check_result: Optional[FloorCheckResult] = None
    fx_conversions: Dict[str, Any] = field(default_factory=dict)
    disbursement_summary: Optional[VoyageDisbursementSummary] = None
    blocked_reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    disclaimer: Optional[str] = None

    @property
    def blocking_reasons(self) -> List[str]:
        """Alias for blocked_reasons for interface consistency."""
        return self.blocked_reasons

    def to_dict(self) -> Dict[str, Any]:
        """Convert assessment result to serialized dictionary."""
        return {
            "voyage_id": self.voyage_id,
            "vessel_imo": self.vessel_imo,
            "vessel_name": self.vessel_name,
            "origin_port": self.origin_port,
            "destination_port": self.destination_port,
            "canals": self.canals,
            "transit_date": self.transit_date.isoformat(),
            "readiness_status": self.readiness_status.value,
            "readiness_tier": self.readiness_tier,
            "is_ready_for_research": self.is_ready_for_research,
            "is_ready_for_operations": self.is_ready_for_operations,
            "is_complete": self.is_complete,
            "has_partial_components": self.has_partial_components,
            "has_provisional_estimates": self.has_provisional_estimates,
            "vessel_assessment": self.vessel_assessment.to_dict(),
            "tariff_summary": {
                "all_tariffs_valid": self.tariff_summary.all_tariffs_valid,
                "has_partial_components": self.tariff_summary.has_partial_components,
                "missing_tariffs": self.tariff_summary.missing_tariffs,
                "conditional_tariffs": self.tariff_summary.conditional_tariffs,
                "restricted_tariffs": self.tariff_summary.restricted_tariffs,
                "port_results": {k: v.to_dict() for k, v in self.tariff_summary.port_results.items()},
                "canal_results": {k: v.to_dict() for k, v in self.tariff_summary.canal_results.items()},
            },
            "charge_summary": {
                "tier_1_statutory_total_usd": self.charge_summary.tier_1_statutory_total_usd if self.charge_summary else 0.0,
                "tier_2_regulated_total_usd": self.charge_summary.tier_2_regulated_total_usd if self.charge_summary else 0.0,
                "tier_3_contractual_total_usd": self.charge_summary.tier_3_contractual_total_usd if self.charge_summary else 0.0,
                "tier_4_provisional_total_usd": self.charge_summary.tier_4_provisional_total_usd if self.charge_summary else 0.0,
                "total_disbursement_usd": self.charge_summary.total_disbursement_usd if self.charge_summary else 0.0,
                "has_provisional_estimates": self.charge_summary.has_provisional_estimates if self.charge_summary else False,
                "has_missing_contractual_evidence": self.charge_summary.has_missing_contractual_evidence if self.charge_summary else False,
            } if self.charge_summary else None,
            "floor_check_result": {
                "is_floor_satisfied": self.floor_check_result.is_floor_satisfied,
                "user_amount_usd": self.floor_check_result.user_amount_usd,
                "statutory_floor_usd": self.floor_check_result.statutory_floor_usd,
                "variance_usd": self.floor_check_result.variance_usd,
                "unallocated_overage_usd": self.floor_check_result.unallocated_overage_usd,
                "status": self.floor_check_result.status,
            } if self.floor_check_result else None,
            "fx_conversions": self.fx_conversions,
            "blocked_reasons": self.blocked_reasons,
            "blocking_reasons": self.blocked_reasons,
            "warnings": self.warnings,
            "disclaimer": self.disclaimer,
        }


class VoyageReadinessService:
    """
    Unified voyage readiness assessment service orchestrating Scopes A-F.
    
    Governance Invariants:
    1. Operational Calculations Strictly Blocked (Phase 1):
       is_ready_for_operations is ALWAYS False under all execution modes.
    2. Zero Synthetic Tonnage:
       SCNT or PC/UMS tonnages must never be synthesized or interpolated.
    3. Canonical Central Bank FX:
       Zero benchmark substitution in operational mode. Stale observations exceeding
       provider lookback horizons fail closed.
    4. One-Way Statutory Floor:
       Any fixture estimate below the statutory floor fails closed with
       FIXTURE_VALUATION_BELOW_STATUTORY_FLOOR. Unitemized overage is flagged.
    5. Partial Component Watermarking:
       Incomplete or partial components disqualify operational sign-off and require
       the prominent qualified commercial estimate disclaimer.
    """

    PARTIAL_ESTIMATE_DISCLAIMER = (
        "QUALIFIED COMMERCIAL ESTIMATE: Contains unverified or partial statutory "
        "port/canal charges. Not certified for contractual final disbursement settlement."
    )

    def __init__(
        self,
        db_session: Optional[Session] = None,
        currency_service: Optional[CurrencyConversionService] = None,
        vessel_service: Optional[VesselReadinessService] = None,
        tariff_service: Optional[TariffValidationService] = None,
        charge_classifier: Optional[ChargeClassifier] = None,
        disbursement_service: Optional[VoyageDisbursementService] = None,
    ):
        self.db = db_session
        self.currency_service = currency_service or CurrencyConversionService(db=db_session)
        if currency_service is None and db_session is None:
            self.currency_service.seed_historical_statutory_rates()
        self.vessel_service = vessel_service or VesselReadinessService()
        self.tariff_service = tariff_service or TariffValidationService()
        self.charge_classifier = charge_classifier or ChargeClassifier()
        self.disbursement_service = disbursement_service or VoyageDisbursementService(
            db=db_session,
            currency_service=self.currency_service,
        )

    def assess_voyage_readiness(
        self,
        voyage_id: str,
        transit_date: Union[datetime.date, str],
        origin_port_unlocode: str,
        destination_port_unlocode: str,
        intermediate_ports: Optional[List[str]] = None,
        canal_transits: Optional[List[str]] = None,
        vessel_particulars: Optional[Dict[str, Any]] = None,
        user_fixture_valuation_usd: Optional[float] = None,
        contractual_agency_fee_usd: Optional[float] = None,
        agency_contract_id: Optional[str] = None,
        operational_mode: bool = False,
        itemized_charges: Optional[List[Dict[str, Any]]] = None,
        cargo_type: Optional[str] = None,
    ) -> VoyageReadinessAssessmentResult:
        """
        Evaluate full readiness across Scopes A-F for a given voyage route and vessel.
        """
        if isinstance(transit_date, str):
            t_date = datetime.date.fromisoformat(transit_date)
        else:
            t_date = transit_date

        v_parts = vessel_particulars or {}
        vessel_imo = v_parts.get("imo") or v_parts.get("vessel_imo")
        vessel_name = v_parts.get("vessel_name")
        canals = [c.upper() for c in (canal_transits or [])]
        intermediate = [p.upper() for p in (intermediate_ports or [])]
        origin = origin_port_unlocode.upper().strip()
        destination = destination_port_unlocode.upper().strip()
        all_ports = [origin, destination] + intermediate

        blocked_reasons: List[str] = []
        warnings: List[str] = []

        if not origin or not destination:
            blocked_reasons.append("INVALID_ITINERARY: Origin and destination port UN/LOCODEs are required.")

        # -------------------------------------------------------------
        # Scope F: Strict Level 3 Operational Release Gate Invariant
        # -------------------------------------------------------------
        is_ready_for_operations = False
        if operational_mode:
            blocked_reasons.append("LEVEL_3_OPERATIONAL_BLOCKED: Operational calculations locked in Phase 1")

        # -------------------------------------------------------------
        # Scope B: Vessel Statutory Particulars Readiness
        # -------------------------------------------------------------
        # Map required authorities from itinerary
        required_authorities: List[str] = []
        for c in canals:
            if c in ("PANAMA", "SUEZ"):
                required_authorities.append(c)
        for p in all_ports:
            if p in ("SGSIN", "NLRTM", "USHOU"):
                required_authorities.append(p)

        vessel_assessment = self.vessel_service.assess_readiness(
            vessel_data=v_parts,
            required_authorities=required_authorities,
        )

        for err in vessel_assessment.validation_errors:
            blocked_reasons.append(f"VESSEL_PARTICULARS_ERROR: {err}")
        warnings.extend(vessel_assessment.warnings)

        # -------------------------------------------------------------
        # Scope C: Port & Canal Tariff Applicability Validation
        # -------------------------------------------------------------
        vessel_gt = v_parts.get("gt") or v_parts.get("gross_tonnage")
        tariff_summary = self.tariff_service.validate_itinerary(
            port_unlocodes=all_ports,
            canal_ids=canals,
            transit_date=t_date,
            vessel_gt=vessel_gt,
            cargo_type=cargo_type,
            vessel_dimensions=v_parts,
        )

        if not tariff_summary.all_tariffs_valid:
            for port_code, res in tariff_summary.port_results.items():
                if not res.is_valid:
                    blocked_reasons.append(f"PORT_TARIFF_INVALID [{port_code}]: {'; '.join(res.failure_reasons)}")
            for canal_id, res in tariff_summary.canal_results.items():
                if not res.is_valid:
                    blocked_reasons.append(f"CANAL_TARIFF_INVALID [{canal_id}]: {'; '.join(res.failure_reasons)}")

        if tariff_summary.restricted_tariffs:
            for t_id in tariff_summary.restricted_tariffs:
                blocked_reasons.append(f"TARIFF_PERMISSION_RESTRICTED: Tariff {t_id} is not permitted for commercial disclosure")

        if tariff_summary.conditional_tariffs:
            warnings.append(f"CONDITIONAL_TARIFFS_PRESENT: {', '.join(tariff_summary.conditional_tariffs)}")

        # -------------------------------------------------------------
        # Scope A: Canonical FX Valuation & Lookback Provenance
        # -------------------------------------------------------------
        # Determine required currencies for route
        required_currencies: set[str] = set()
        if "SUEZ" in canals:
            required_currencies.add("SDR")
        if "NLRTM" in all_ports:
            required_currencies.add("EUR")
        if "SGSIN" in all_ports:
            required_currencies.add("SGD")
        if "PANAMA" in canals or "USHOU" in all_ports:
            required_currencies.add("USD")

        fx_conversions: Dict[str, Any] = {}
        fx_has_missing_or_stale = False

        for curr in sorted(required_currencies):
            conv_res = self.currency_service.convert(
                amount=1.0,
                from_currency=curr,
                to_currency="USD",
                valuation_date=t_date,
                operational_mode=operational_mode,
            )
            fx_data = {
                "currency": curr,
                "rate": conv_res.exchange_rate,
                "provider": conv_res.rate_provider,
                "requested_date": conv_res.requested_valuation_date.isoformat() if conv_res.requested_valuation_date else None,
                "observation_date": conv_res.actual_observation_date.isoformat() if conv_res.actual_observation_date else None,
                "lag_days": conv_res.observation_lag_days,
                "evidence_classification": (
                    conv_res.conversion_evidence_classification.value
                    if hasattr(conv_res.conversion_evidence_classification, "value")
                    else conv_res.conversion_evidence_classification
                ),
                "is_valid": conv_res.is_valid,
                "failure_reason": conv_res.failure_reason,
            }
            fx_conversions[curr] = fx_data

            if not conv_res.is_valid:
                fx_has_missing_or_stale = True
                blocked_reasons.append(f"FX_CONVERSION_FAILED [{curr}]: {conv_res.failure_reason}")

        # -------------------------------------------------------------
        # Evaluate Actual Voyage Disbursements & Charge Classification
        # -------------------------------------------------------------
        disbursement_summary: Optional[VoyageDisbursementSummary] = None
        charge_summary: Optional[ChargeClassificationSummary] = None
        floor_check_result: Optional[FloorCheckResult] = None

        try:
            req = VoyageDisbursementAssessmentRequest(
                voyage_id=voyage_id,
                origin_port=origin,
                destination_port=destination,
                intermediate_ports=intermediate,
                canal_transits=canals,
                transit_date=t_date,
                vessel_gt=vessel_gt,
                vessel_dwt=v_parts.get("dwt") or v_parts.get("dwt_tonnage") or v_parts.get("vessel_dwt"),
                loa_m=v_parts.get("loa_m") or v_parts.get("length_meters") or v_parts.get("vessel_loa_m"),
                beam_m=v_parts.get("beam_m") or v_parts.get("width_meters") or v_parts.get("vessel_beam_m"),
                draft_m=v_parts.get("draft_m") or v_parts.get("draft_max_meters") or v_parts.get("vessel_draft_m"),
                scnt=v_parts.get("scnt"),
                pc_ums_net=v_parts.get("pc_ums_net"),
                contractual_agency_fee_usd=contractual_agency_fee_usd,
                agency_contract_id=agency_contract_id,
                operational_mode=operational_mode,
            )
            disbursement_summary = self.disbursement_service.evaluate_voyage_disbursements(req)

            # Propagate blocked reasons from underlying port and canal evaluations
            if disbursement_summary and disbursement_summary.blocked_reasons:
                for br in disbursement_summary.blocked_reasons:
                    if br not in blocked_reasons:
                        blocked_reasons.append(br)

            # Classify all generated line items across the four tiers
            classified_items: List[ClassifiedChargeItem] = []
            for it in disbursement_summary.line_items:
                classified_items.append(self.charge_classifier.classify_line_item(it))

            # Add any external itemized charges if supplied
            if itemized_charges:
                for it in itemized_charges:
                    classified_items.append(self.charge_classifier.classify_line_item(it))

            charge_summary = self.charge_classifier.summarize_charges(classified_items)

            # ---------------------------------------------------------
            # Scope D: One-Way Statutory Floor Enforcement
            # ---------------------------------------------------------
            statutory_floor_usd = charge_summary.tier_1_statutory_total_usd
            if user_fixture_valuation_usd is not None:
                floor_check_result = self.charge_classifier.evaluate_statutory_floor(
                    user_valuation_usd=user_fixture_valuation_usd,
                    statutory_floor_usd=statutory_floor_usd,
                    itemized_charges=itemized_charges,
                )
                if not floor_check_result.is_floor_satisfied:
                    for r in floor_check_result.failure_reasons:
                        blocked_reasons.append(r)
                if floor_check_result.unallocated_overage_usd > 0.0:
                    warnings.append(
                        f"UNALLOCATED_COMMERCIAL_OVERAGE: ${floor_check_result.unallocated_overage_usd:,.2f} "
                        "exceeds statutory baseline without itemized operational allocation."
                    )

        except Exception as exc:
            blocked_reasons.append(f"DISBURSEMENT_EVALUATION_ERROR: {str(exc)}")

        # -------------------------------------------------------------
        # Scope E: Unified Voyage Readiness Classification
        # -------------------------------------------------------------
        has_partial = (
            tariff_summary.has_partial_components
            or (charge_summary.has_partial_components if charge_summary else False)
            or (disbursement_summary.has_partial_components if disbursement_summary else False)
        )
        has_provisional = (
            (charge_summary.has_provisional_estimates if charge_summary else False)
            or (floor_check_result.unallocated_overage_usd > 0.0 if floor_check_result else False)
        )
        has_missing_contract = (
            charge_summary.has_missing_contractual_evidence if charge_summary else False
        )

        is_complete = (
            vessel_assessment.is_complete
            and tariff_summary.all_tariffs_valid
            and not fx_has_missing_or_stale
            and not has_partial
        )

        # Status Hierarchy:
        # 1. Any hard missing data or valuation failure -> LEVEL_2_RESEARCH_INCOMPLETE
        # 2. Operational requested -> LEVEL_3_OPERATIONAL_BLOCKED
        # 3. Partial components -> LEVEL_2_PARTIAL_COMPONENTS
        # 4. Qualified commercial estimate (Tier 3/4 unverified or overage) -> LEVEL_2_QUALIFIED_COMMERCIAL_ESTIMATE
        # 5. All criteria cleanly met -> LEVEL_2_RESEARCH_READY

        disclaimer: Optional[str] = None

        if (
            not origin
            or not destination
            or not vessel_assessment.is_ready_for_research
            or not tariff_summary.all_tariffs_valid
            or fx_has_missing_or_stale
            or (floor_check_result and not floor_check_result.is_floor_satisfied)
        ):
            readiness_status = VoyageReadinessStatus.LEVEL_2_RESEARCH_INCOMPLETE
            is_ready_for_research = False

        elif operational_mode:
            readiness_status = VoyageReadinessStatus.LEVEL_3_OPERATIONAL_BLOCKED
            is_ready_for_research = True
            disclaimer = self.PARTIAL_ESTIMATE_DISCLAIMER if has_partial else None

        elif has_partial:
            readiness_status = VoyageReadinessStatus.LEVEL_2_PARTIAL_COMPONENTS
            is_ready_for_research = True
            disclaimer = self.PARTIAL_ESTIMATE_DISCLAIMER

        elif has_provisional or has_missing_contract:
            readiness_status = VoyageReadinessStatus.LEVEL_2_QUALIFIED_COMMERCIAL_ESTIMATE
            is_ready_for_research = True
            disclaimer = self.PARTIAL_ESTIMATE_DISCLAIMER

        else:
            readiness_status = VoyageReadinessStatus.LEVEL_2_RESEARCH_READY
            is_ready_for_research = True

        return VoyageReadinessAssessmentResult(
            voyage_id=voyage_id,
            vessel_imo=vessel_imo,
            vessel_name=vessel_name,
            origin_port=origin,
            destination_port=destination,
            canals=canals,
            transit_date=t_date,
            readiness_status=readiness_status,
            readiness_tier="LEVEL_2_RESEARCH_ONLY",
            is_ready_for_research=is_ready_for_research,
            is_ready_for_operations=False,  # Invariant: Strictly False during Phase 1
            is_complete=is_complete,
            has_partial_components=has_partial,
            has_provisional_estimates=has_provisional,
            vessel_assessment=vessel_assessment,
            tariff_summary=tariff_summary,
            charge_summary=charge_summary,
            floor_check_result=floor_check_result,
            fx_conversions=fx_conversions,
            disbursement_summary=disbursement_summary,
            blocked_reasons=blocked_reasons,
            warnings=warnings,
            disclaimer=disclaimer,
        )
