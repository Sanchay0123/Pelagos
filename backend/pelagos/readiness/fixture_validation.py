"""
Pelagos Stage F1.4.3: Commercial Fixture Validation Engine (P2-A Core).

Validates external commercial fixtures against:
- IMO syntax (Luhn-style weighted checksum) vs. Authoritative Registry Identity vs. Statutory Certificates.
- Four-tier charge decomposition (Statutory, Regulated, Contractual, Provisional).
- One-way statutory floor enforcement and unallocated overage quarantine.
- Three-tier commission governance (Contractually Verified, User Research Assumption, Missing).
- Seven-component voyage duration contract and laytime/demurrage parameterization.
"""

from __future__ import annotations

import datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from sqlalchemy.orm import Session

from pelagos.models import validate_imo, Vessel
from pelagos.readiness.charge_classifier import (
    ChargeClassifier,
    ChargeClassificationTier,
    ClassifiedChargeItem,
    FloorCheckResult,
    _to_decimal,
)
from pelagos.readiness.tariff_validation import TariffValidationService
from pelagos.readiness.vessel_readiness import VesselReadinessService
from pelagos.registry.voyage_disbursement_service import (
    VoyageDisbursementAssessmentRequest,
    VoyageDisbursementService,
    VoyageDisbursementSummary,
)


class CommissionGovernanceTier(str, Enum):
    """Three-tier governance classification for commercial commissions."""
    CONTRACT_VERIFIED = "COMMISSION_CONTRACT_VERIFIED"
    USER_RESEARCH_ASSUMPTION = "USER_RESEARCH_ASSUMPTION"
    MISSING = "COMMISSION_MISSING"


class DurationStatus(str, Enum):
    """Validation status for voyage duration components."""
    ESTIMATED_ROUTED = "ESTIMATED_ROUTED"
    CONTRACTUAL_AGREED = "CONTRACTUAL_AGREED"
    OBSERVED_ACTUAL = "OBSERVED_ACTUAL"


class FixtureLineItemInput(BaseModel):
    """Detailed line item supplied in an external commercial fixture recap."""
    tariff_identifier: Optional[str] = None
    description: str = Field(..., min_length=2, max_length=256)
    charge_category: str = Field(..., min_length=2, max_length=64)
    amount_usd: float = Field(..., ge=0.0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    applicability_type: str = Field(default="MANDATORY")
    contract_attribution: Optional[str] = Field(default=None, max_length=128)
    statutory_basis: Optional[str] = Field(default=None, max_length=256)


class VoyageDurationInput(BaseModel):
    """
    Seven-component voyage duration input contract.
    Ensures transparent decomposition into sea, port, waiting, and canal times.
    """
    laden_sea_days: float = Field(..., gt=0, description="Steaming time under laden condition")
    ballast_sea_days: float = Field(default=0.0, ge=0, description="Ballast repositioning steaming time")
    loading_days: float = Field(..., gt=0, description="Working time at loading port")
    discharge_days: float = Field(..., gt=0, description="Working time at discharging port")
    port_waiting_days: float = Field(default=0.0, ge=0, description="Anchorage / congestion waiting time")
    canal_waiting_days: float = Field(default=0.0, ge=0, description="Canal convoy staging waiting time")
    canal_transit_days: float = Field(default=0.0, ge=0, description="Active canal passage time")
    duration_status: DurationStatus = DurationStatus.ESTIMATED_ROUTED
    contractual_laytime_days: Optional[float] = Field(default=None, gt=0)
    demurrage_rate_usd_per_day: Optional[float] = Field(default=None, ge=0)
    despatch_rate_usd_per_day: Optional[float] = Field(default=None, ge=0)

    @property
    def total_voyage_days(self) -> float:
        """Sum of all seven duration components forming the TCE denominator."""
        return round(
            self.laden_sea_days
            + self.ballast_sea_days
            + self.loading_days
            + self.discharge_days
            + self.port_waiting_days
            + self.canal_waiting_days
            + self.canal_transit_days,
            4,
        )


class CommercialFixturePayload(BaseModel):
    """Validated schema replacing raw VerifiedCharterInputs for commercial fixtures."""
    fixture_id: str = Field(..., min_length=3, max_length=64)
    vessel_imo: int = Field(..., description="7-digit IMO number strictly validated by checksum")
    mmsi: Optional[str] = Field(default=None, description="Dynamic temporal communications identifier")
    vessel_name: Optional[str] = None
    origin_port_unlocode: str = Field(..., pattern=r"^[A-Z]{5}$")
    destination_port_unlocode: str = Field(..., pattern=r"^[A-Z]{5}$")
    intermediate_ports: List[str] = Field(default_factory=list)
    canal_transits: List[str] = Field(default_factory=list)
    laycan_start: datetime.date
    laycan_end: datetime.date
    transit_date: datetime.date
    cargo_tonnes: float = Field(..., gt=0.0)
    cargo_commodity: str = Field(default="Agricultural Bulk Grain")

    # Financial allowances supplied by caller
    total_port_allowance_usd: Optional[float] = Field(default=None, ge=0.0)
    total_canal_allowance_usd: Optional[float] = Field(default=None, ge=0.0)
    itemized_charges: List[FixtureLineItemInput] = Field(default_factory=list)

    # Commission Governance
    commission_tier: CommissionGovernanceTier = CommissionGovernanceTier.MISSING
    contractual_commission_rate: Optional[float] = Field(default=None, ge=0.0, le=0.20)
    commission_contract_id: Optional[str] = Field(default=None, max_length=128)
    user_research_commission_rate: Optional[float] = Field(default=None, ge=0.0, le=0.20)

    # Seven-component voyage duration
    duration_input: VoyageDurationInput

    # Optional binding to an existing Pelagos VoyageDisbursementRecord
    disbursement_record_id: Optional[int] = None
    disbursement_content_hash: Optional[str] = None

    # Source provenance qualification
    source_organization: str = Field(..., min_length=2, max_length=128)
    broker_reference: Optional[str] = Field(default=None, max_length=64)
    provenance_classification: str = Field(
        default="SOURCE_REPORTED",
        description="DIRECTLY_VERIFIED, SOURCE_REPORTED, or RESTRICTED_COMMERCIAL_LICENSE",
    )


class ValidatedFixtureResult(BaseModel):
    """Output contract produced by CommercialFixtureValidationEngine."""
    fixture_id: str
    is_valid: bool
    status: str
    is_operational_permitted: bool
    vessel_validation: Dict[str, Any]
    statutory_floor_usd: float
    total_fixture_usd: float
    variance_usd: float
    unallocated_overage_usd: float
    tier_1_statutory_usd: float
    tier_2_regulated_usd: float
    tier_3_contractual_usd: float
    tier_4_provisional_usd: float
    commission_summary: Dict[str, Any]
    duration_summary: Dict[str, Any]
    vessel_readiness_summary: Optional[Dict[str, Any]] = None
    validation_errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    disclaimers: List[str] = Field(default_factory=list)
    classified_line_items: List[Dict[str, Any]] = Field(default_factory=list)


class CommercialFixtureValidationEngine:
    """
    Validates external commercial fixture payloads against:
    - IMO format/checksum vs. authoritative vessel identity vs. certificates.
    - One-way statutory floors across port dues and canal tolls.
    - Contract attribution on Tier 3 items.
    - Three-tier commission governance without implicit defaults.
    - Seven-component voyage duration contracts.
    """

    COMMISSION_RESEARCH_DISCLAIMER = (
        "RESEARCH ASSUMPTION: Commission rate supplied by caller for exploratory purposes; "
        "not verified by commercial charterparty agreement."
    )

    def __init__(
        self,
        db: Optional[Session] = None,
        disbursement_service: Optional[VoyageDisbursementService] = None,
        charge_classifier: Optional[ChargeClassifier] = None,
        tariff_service: Optional[TariffValidationService] = None,
        vessel_service: Optional[VesselReadinessService] = None,
    ):
        self.db = db
        self.charge_classifier = charge_classifier or ChargeClassifier()
        self.tariff_service = tariff_service or TariffValidationService()
        self.vessel_service = vessel_service or VesselReadinessService(db=db)
        self.disbursement_service = disbursement_service or VoyageDisbursementService(db=db)

    def validate_vessel(
        self,
        vessel_imo: int,
        vessel_name: Optional[str] = None,
        mmsi: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate IMO syntax vs. authoritative registry identity vs. communications identifier.
        """
        # 1. Syntax Checksum Validation
        is_syntax_valid, _, imo_err = validate_imo(vessel_imo)
        if not is_syntax_valid:
            return {
                "vessel_imo": vessel_imo,
                "syntax_status": "SYNTAX_INVALID",
                "identity_status": "IDENTITY_REJECTED",
                "is_verified": False,
                "error": imo_err or f"Invalid IMO number {vessel_imo}: Checksum validation failed.",
            }

        # 2. Authoritative Registry Identity Reconcile
        db_vessel: Optional[Vessel] = None
        if self.db:
            db_vessel = self.db.query(Vessel).filter(Vessel.imo == vessel_imo).first()

        identity_status = "IDENTITY_VERIFIED" if db_vessel else "IDENTITY_UNMATCHED"
        matched_name = db_vessel.vessel_name if db_vessel else None
        vessel_class = db_vessel.vessel_class if db_vessel else None
        dwt_val = getattr(db_vessel, "dwt_tonnage", None) or getattr(db_vessel, "dwt", None) if db_vessel else None
        dwt = float(dwt_val) if dwt_val is not None else None
        gt_val = getattr(db_vessel, "gross_tonnage", None) if db_vessel else None
        gross_tonnage = float(gt_val) if gt_val is not None else None

        # Check name consistency if provided
        name_mismatch = False
        if db_vessel and vessel_name:
            if db_vessel.vessel_name.strip().upper() != vessel_name.strip().upper():
                name_mismatch = True
                identity_status = "IDENTITY_NAME_MISMATCH"

        return {
            "vessel_imo": vessel_imo,
            "mmsi": mmsi,
            "syntax_status": "SYNTAX_VALID",
            "identity_status": identity_status,
            "matched_vessel_name": matched_name,
            "vessel_class": vessel_class,
            "dwt": dwt,
            "gross_tonnage": gross_tonnage,
            "name_mismatch": name_mismatch,
            "is_verified": bool(db_vessel and not name_mismatch),
            "communications_id": mmsi,
        }

    def validate_fixture(
        self,
        payload: CommercialFixturePayload,
        operational_mode: bool = False,
    ) -> ValidatedFixtureResult:
        """
        Execute comprehensive validation across vessel identity, statutory floors,
        charge tiers, commission governance, and voyage durations.
        """
        validation_errors: List[str] = []
        warnings: List[str] = []
        disclaimers: List[str] = []

        # -------------------------------------------------------------
        # 1. Vessel Identity & Syntax Validation
        # -------------------------------------------------------------
        vessel_val = self.validate_vessel(
            vessel_imo=payload.vessel_imo,
            vessel_name=payload.vessel_name,
            mmsi=payload.mmsi,
        )
        if vessel_val["syntax_status"] != "SYNTAX_VALID":
            validation_errors.append(vessel_val.get("error", "IMO syntax checksum failed."))

        if operational_mode and not vessel_val["is_verified"]:
            validation_errors.append(
                f"OPERATIONAL_IDENTITY_UNMATCHED: Vessel IMO {payload.vessel_imo} not found in authoritative registry."
            )

        # -------------------------------------------------------------
        # 1b. Statutory Vessel Particulars Readiness Assessment (P2-001)
        # -------------------------------------------------------------
        canals = [c.upper() for c in payload.canal_transits]
        all_ports = [payload.origin_port_unlocode, payload.destination_port_unlocode] + payload.intermediate_ports

        vessel_readiness = self.vessel_service.evaluate_readiness(
            vessel_imo=payload.vessel_imo,
            canal_transits=canals,
            port_calls=all_ports if operational_mode else None,
            transit_date=payload.transit_date,
            operational_mode=operational_mode,
        )

        if not vessel_readiness.is_ready_for_evaluation:
            for reason in vessel_readiness.blocked_reasons:
                if reason not in validation_errors:
                    validation_errors.append(reason)

        if operational_mode and not vessel_readiness.is_ready_for_operations:
            for reason in vessel_readiness.blocking_reasons:
                if reason not in validation_errors:
                    validation_errors.append(reason)

        for w in vessel_readiness.warnings:
            if w not in warnings:
                warnings.append(w)

        # -------------------------------------------------------------
        # 2. Commission Governance Evaluation
        # -------------------------------------------------------------
        commission_summary: Dict[str, Any] = {
            "tier": payload.commission_tier.value,
            "rate": None,
            "contract_id": payload.commission_contract_id,
            "is_operational_permitted": False,
        }

        if payload.commission_tier == CommissionGovernanceTier.CONTRACT_VERIFIED:
            if payload.contractual_commission_rate is None:
                validation_errors.append("MISSING_COMMISSION_RATE: Contractual commission tier requires rate.")
            elif not payload.commission_contract_id:
                validation_errors.append("MISSING_COMMISSION_CONTRACT_ID: Contractual commission requires contract reference.")
            else:
                commission_summary["rate"] = payload.contractual_commission_rate
                commission_summary["is_operational_permitted"] = True

        elif payload.commission_tier == CommissionGovernanceTier.USER_RESEARCH_ASSUMPTION:
            if payload.user_research_commission_rate is None:
                validation_errors.append("MISSING_RESEARCH_COMMISSION_RATE: Research assumption requires specified rate.")
            else:
                commission_summary["rate"] = payload.user_research_commission_rate
                commission_summary["is_operational_permitted"] = False
                disclaimers.append(self.COMMISSION_RESEARCH_DISCLAIMER)
                if operational_mode:
                    validation_errors.append(
                        "RESEARCH_COMMISSION_BLOCKED: Research commission assumption cannot be used in operational mode."
                    )

        elif payload.commission_tier == CommissionGovernanceTier.MISSING:
            commission_summary["rate"] = 0.0
            commission_summary["is_operational_permitted"] = False
            warnings.append("COMMISSION_UNSPECIFIED: No commercial commission rate provided (defaults to 0.0% in research).")
            if operational_mode:
                validation_errors.append(
                    "MISSING_COMMERCIAL_COMMISSION_CONTRACT: Operational settlement requires verified commission contract."
                )

        # -------------------------------------------------------------
        # 3. Seven-Component Duration Accounting
        # -------------------------------------------------------------
        dur = payload.duration_input
        dur_summary = {
            "laden_sea_days": dur.laden_sea_days,
            "ballast_sea_days": dur.ballast_sea_days,
            "loading_days": dur.loading_days,
            "discharge_days": dur.discharge_days,
            "port_waiting_days": dur.port_waiting_days,
            "canal_waiting_days": dur.canal_waiting_days,
            "canal_transit_days": dur.canal_transit_days,
            "total_voyage_days": dur.total_voyage_days,
            "duration_status": dur.duration_status.value,
            "contractual_laytime_days": dur.contractual_laytime_days,
        }

        if dur.total_voyage_days <= 0:
            validation_errors.append("INVALID_VOYAGE_DURATION: Total voyage days must be strictly positive.")

        if operational_mode and dur.duration_status == DurationStatus.ESTIMATED_ROUTED:
            validation_errors.append(
                "ESTIMATED_DURATION_BLOCKED: Operational TCE requires CONTRACTUAL_AGREED or OBSERVED_ACTUAL durations."
            )

        # -------------------------------------------------------------
        # 4. Itinerary & Statutory Tariff Applicability
        # -------------------------------------------------------------
        all_ports = [payload.origin_port_unlocode, payload.destination_port_unlocode] + payload.intermediate_ports
        canals = [c.upper() for c in payload.canal_transits]

        tariff_summary = self.tariff_service.validate_itinerary(
            port_unlocodes=all_ports,
            canal_ids=canals,
            transit_date=payload.transit_date,
            vessel_gt=vessel_val.get("gross_tonnage"),
            cargo_type=payload.cargo_commodity,
        )
        if not tariff_summary.all_tariffs_valid:
            for p_code, res in tariff_summary.port_results.items():
                if not res.is_valid:
                    validation_errors.append(f"PORT_TARIFF_INVALID [{p_code}]: {'; '.join(res.failure_reasons)}")
            for c_id, res in tariff_summary.canal_results.items():
                if not res.is_valid:
                    validation_errors.append(f"CANAL_TARIFF_INVALID [{c_id}]: {'; '.join(res.failure_reasons)}")

        # -------------------------------------------------------------
        # 5. One-Way Statutory Floor & Four-Tier Charge Decomposition
        # -------------------------------------------------------------
        # Evaluate baseline statutory disbursements
        disb_req = VoyageDisbursementAssessmentRequest(
            voyage_id=payload.fixture_id,
            origin_port=payload.origin_port_unlocode,
            destination_port=payload.destination_port_unlocode,
            intermediate_ports=payload.intermediate_ports,
            canal_transits=canals,
            transit_date=payload.transit_date,
            vessel_gt=vessel_val.get("gross_tonnage"),
            vessel_dwt=vessel_val.get("dwt"),
            operational_mode=operational_mode,
        )
        disb_summary = self.disbursement_service.evaluate_voyage_disbursements(disb_req)

        # Statutory base dues from Pelagos registries
        statutory_floor_usd = 0.0
        if disb_summary and disb_summary.total_disbursement_usd:
            statutory_floor_usd = disb_summary.total_disbursement_usd

        # Aggregate caller supplied fixture allowances
        caller_port_allowance = payload.total_port_allowance_usd or 0.0
        caller_canal_allowance = payload.total_canal_allowance_usd or 0.0
        total_fixture_usd = caller_port_allowance + caller_canal_allowance

        # Classify any itemized line items
        classified_items: List[ClassifiedChargeItem] = []
        for it in payload.itemized_charges:
            classified_items.append(
                self.charge_classifier.classify_line_item(it.model_dump())
            )
        charge_summary = self.charge_classifier.summarize_charges(classified_items)

        # If caller provided itemized charges but no explicit lump sum, use itemized total
        if total_fixture_usd == 0.0 and charge_summary.total_disbursement_usd > 0.0:
            total_fixture_usd = charge_summary.total_disbursement_usd

        # Execute One-Way Statutory Floor Check
        itemized_dict_list = [it.model_dump() for it in payload.itemized_charges]
        floor_result = self.charge_classifier.evaluate_statutory_floor(
            user_valuation_usd=total_fixture_usd,
            statutory_floor_usd=statutory_floor_usd,
            itemized_charges=itemized_dict_list,
        )

        if not floor_result.is_floor_satisfied:
            for r in floor_result.failure_reasons:
                validation_errors.append(r)

        unallocated_overage_usd = floor_result.unallocated_overage_usd
        if unallocated_overage_usd > 0.0:
            warnings.append(
                f"UNALLOCATED_COMMERCIAL_OVERAGE: ${unallocated_overage_usd:,.2f} exceeds statutory baseline "
                "without itemized Tier 2/3/4 allocation. Quarantined from operational clearance."
            )

        # Check contract attribution on Tier 3 items
        if charge_summary.has_missing_contractual_evidence:
            validation_errors.append("MISSING_CONTRACTUAL_EVIDENCE: Tier 3 items lack bilateral contract attribution.")

        # Determine overall validity and operational permission
        is_valid = len(validation_errors) == 0
        is_operational_permitted = (
            is_valid
            and vessel_val["is_verified"]
            and commission_summary["is_operational_permitted"]
            and dur.duration_status != DurationStatus.ESTIMATED_ROUTED
            and unallocated_overage_usd == 0.0
            and not charge_summary.has_provisional_estimates
            and not disb_summary.has_partial_components
            and not operational_mode  # Hard invariant: Locked during Phase 2
        )

        # Determine status string
        if not is_valid:
            status = "REJECTED_VALIDATION_FAILURE"
        elif unallocated_overage_usd > 0.0:
            status = "QUARANTINED_UNALLOCATED_OVERAGE"
        elif charge_summary.has_provisional_estimates:
            status = "QUALIFIED_PROVISIONAL_ESTIMATE"
        else:
            status = "VALIDATED_COMMERCIAL_FIXTURE"

        return ValidatedFixtureResult(
            fixture_id=payload.fixture_id,
            is_valid=is_valid,
            status=status,
            is_operational_permitted=is_operational_permitted,
            vessel_validation=vessel_val,
            statutory_floor_usd=round(statutory_floor_usd, 2),
            total_fixture_usd=round(total_fixture_usd, 2),
            variance_usd=round(total_fixture_usd - statutory_floor_usd, 2),
            unallocated_overage_usd=round(unallocated_overage_usd, 2),
            tier_1_statutory_usd=round(charge_summary.tier_1_statutory_total_usd, 2),
            tier_2_regulated_usd=round(charge_summary.tier_2_regulated_total_usd, 2),
            tier_3_contractual_usd=round(charge_summary.tier_3_contractual_total_usd, 2),
            tier_4_provisional_usd=round(charge_summary.tier_4_provisional_total_usd, 2),
            commission_summary=commission_summary,
            duration_summary=dur_summary,
            vessel_readiness_summary=vessel_readiness.to_dict(),
            validation_errors=validation_errors,
            warnings=warnings,
            disclaimers=disclaimers,
            classified_line_items=[it.__dict__ for it in classified_items],
        )
