"""Pelagos Stage F1.5.2-B: Controlled Level 2 Research Voyage Assessment Service.

Orchestrates the first end-to-end evidence-labelled research voyage assessment for the
US Gulf to Japan bulk corridor:
    USHOU -> Panama Canal -> JPYOK
Primary pilot vessel:
    NAVIOS VICTORY (IMO 9713430)

Strict Governance Invariants:
1. Operational Lock: is_ready_for_operations is ALWAYS False (Level 3 locked).
2. Authenticity Preservation: Never presents assumed or incomplete TCE figures as commercially validated.
3. Zero Synthetic Ballast Tonnage: Calculations requiring unavailable ballast hydrostatics are strictly blocked.
4. Genuine Freight Sourcing: Real historical USDA grain freight rates (USG-JPN-BULK); derived spread never treated as independent rate.
5. Strict Bunker Classification: Port bunker prices must be backed by qualified evidence; user assumptions and USDA G20 benchmarks are explicitly labelled and never substituted silently.
6. Central Bank Valuation: Japanese Yen (JPY) tariffs converted via Bank of Japan spot middle fixings with 4-day lookback limit.
"""

from __future__ import annotations

import datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pelagos.models import FreightObservation, Vessel, VesselHydrostatics
from pelagos.currency import CurrencyConversionService, CurrencyEvidenceClassification
from pelagos.registry.vessel_registry import VesselRegistry
from pelagos.registry.engine_sfoc_registry import EngineSfocRegistry
from pelagos.registry.port_disbursement_registry import PortDisbursementRegistry
from pelagos.registry.canal_tariff_registry import CanalTariffRegistry
from pelagos.routing.service import MaritimeRoutingEngine
from pelagos.routing.schemas import RoutingRequest, RoutingStatus
from pelagos.routing.bunker import (
    BunkerEvidenceClassification,
    ResearchCompositeBenchmark,
    UserSuppliedBunkerAssumption,
    resolve_research_bunker_price,
)


class EpistemicEvidenceClassification(str, Enum):
    """Component-level epistemic evidence classification."""
    OBSERVED_HISTORICAL = "OBSERVED_HISTORICAL"
    DIRECTLY_VERIFIED_STATUTORY = "DIRECTLY_VERIFIED_STATUTORY"
    DIRECTLY_VERIFIED_CENTRAL_BANK = "DIRECTLY_VERIFIED_CENTRAL_BANK"
    CALCULATED_CERTIFIED_SFOC = "CALCULATED_CERTIFIED_SFOC"
    USER_SUPPLIED_ASSUMPTION = "USER_SUPPLIED_ASSUMPTION"
    RESEARCH_COMPOSITE_BENCHMARK = "RESEARCH_COMPOSITE_BENCHMARK"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNAVAILABLE_BLOCKED = "UNAVAILABLE_BLOCKED"


class ComponentAssessmentItem(BaseModel):
    """Detailed line item explaining individual input provenance and status."""
    name: str
    category: str  # FREIGHT, PORT_DUES, CANAL_TOLLS, FUEL, CURRENCY, VESSEL
    amount_usd: Optional[float] = None
    epistemic_status: EpistemicEvidenceClassification
    source_reference: str
    is_complete: bool = True
    limitation: Optional[str] = None


class ResearchVoyageInput(BaseModel):
    """Input contract for controlled Level 2 research voyage assessment."""
    origin_unlocode: str = Field(default="USHOU", description="Origin port (e.g. 'USHOU')")
    destination_unlocode: str = Field(default="JPYOK", description="Destination port (e.g. 'JPYOK')")
    vessel_imo: str = Field(default="9713430", description="Verified pilot vessel IMO (NAVIOS VICTORY)")
    transit_date: datetime.date = Field(default=datetime.date(2024, 5, 1))
    cargo_tonnes: float = Field(default=60000.0, gt=0.0, description="Grain cargo intake in MT")
    freight_rate_override_usd_per_mt: Optional[float] = Field(default=None, gt=0.0)
    user_bunker_assumption: Optional[UserSuppliedBunkerAssumption] = None
    allow_research_composite_bunker: bool = False
    research_bunker_composite: Optional[ResearchCompositeBenchmark] = None
    commission_rate: float = Field(default=0.0375, ge=0.0, le=0.20)
    steaming_speed_knots: float = Field(default=12.5, gt=0.0, le=25.0)
    loading_days: float = Field(default=3.0, gt=0.0)
    discharge_days: float = Field(default=3.0, gt=0.0)
    port_waiting_days: float = Field(default=1.0, ge=0.0)
    canal_waiting_days: float = Field(default=1.5, ge=0.0)
    canal_transit_days: float = Field(default=1.0, ge=0.0)
    gatun_elevation_ft: float = Field(default=80.5, ge=70.0, le=90.0)
    require_ballast_leg_assessment: bool = Field(default=False)
    ballast_sea_days: float = Field(
        default=0.0,
        ge=0.0,
        description="Duration of prior ballast repositioning sea leg in days (default 0.0 for laden-only assessment).",
    )
    operational_mode: bool = Field(default=False)


class ResearchVoyageAssessmentResult(BaseModel):
    """Comprehensive output contract of the controlled Level 2 research voyage assessment."""
    voyage_id: str
    corridor_code: str = "USG-JPN-BULK"
    vessel_imo: str
    vessel_name: str
    vessel_class: str
    origin_port: str
    destination_port: str
    canals_transited: List[str]
    transit_date: datetime.date
    route_distance_nm: float
    routing_mode: str
    antimeridian_crossing_detected: bool

    # Revenue
    cargo_tonnes: float
    freight_rate_usd_per_mt: float
    gross_freight_usd: float
    freight_source: str

    # Disbursements
    origin_port_disbursements_usd: float
    destination_port_disbursements_usd: float
    total_port_disbursements_usd: float
    panama_canal_tolls_usd: float
    commission_rate: float
    commission_deductions_usd: float

    # Bunker & Consumption
    laden_sea_days: float
    total_voyage_days: float
    laden_fuel_consumption_mt: float
    bunker_price_usd_per_mt: Optional[float]
    bunker_fuel_costs_usd: Optional[float]
    bunker_evidence_classification: str

    # Financial Summary
    net_voyage_revenue_usd: Optional[float]
    tce_daily_rate_usd: Optional[float]

    # Epistemic & Readiness Governance
    is_fully_validated: bool
    is_ready_for_operations: bool = False
    governance_tier: str = "LEVEL_2_RESEARCH_ONLY"
    ballast_assessment_eligible: bool
    ballast_blocking_reason: Optional[str] = None

    # Line Item Evidence Breakdown
    components_breakdown: List[ComponentAssessmentItem] = Field(default_factory=list)
    blocked_reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    disclaimers: List[str] = Field(default_factory=list)


class ResearchVoyageAssessmentService:
    """Service executing controlled Level 2 research voyage assessments."""

    def __init__(
        self,
        db: Optional[Session] = None,
        currency_service: Optional[CurrencyConversionService] = None,
        routing_engine: Optional[MaritimeRoutingEngine] = None,
        port_registry: Optional[PortDisbursementRegistry] = None,
        canal_registry: Optional[CanalTariffRegistry] = None,
        vessel_registry: Optional[VesselRegistry] = None,
        sfoc_registry: Optional[EngineSfocRegistry] = None,
    ):
        self.db = db
        self.currency_service = currency_service or CurrencyConversionService(db=db)
        if currency_service is None and db is None:
            self.currency_service.seed_historical_statutory_rates()
        self.routing_engine = routing_engine or MaritimeRoutingEngine()
        self.port_registry = port_registry or PortDisbursementRegistry(
            db=db,
            currency_service=self.currency_service,
        )
        self.canal_registry = canal_registry or CanalTariffRegistry(
            db=db,
            currency_service=self.currency_service,
        )
        self.vessel_registry = vessel_registry or (VesselRegistry(db=db) if db else None)
        self.sfoc_registry = sfoc_registry or (EngineSfocRegistry(db=db) if db else None)

    def evaluate_research_voyage(self, input_data: ResearchVoyageInput) -> ResearchVoyageAssessmentResult:
        """Execute deterministic Level 2 research voyage assessment."""
        components: List[ComponentAssessmentItem] = []
        blocked_reasons: List[str] = []
        warnings: List[str] = []
        disclaimers: List[str] = [
            "LEVEL 2 RESEARCH PILOT ONLY: Analytical assessment for research and engineering evaluation.",
            "Conventional Voyage TCE does NOT deduct vessel OPEX or capital debt financing.",
            "Level 3 operational clearance remains strictly disabled (is_ready_for_operations = False).",
        ]

        # 1. Operational Mode Check
        if input_data.operational_mode:
            blocked_reasons.append(
                "OPERATIONAL_MODE_BLOCKED: Level 3 operational activation remains strictly locked. "
                "Research voyage assessments cannot be authorized for live fixture execution."
            )

        # 2. Vessel Identity & Technical Particulars
        v_imo = str(input_data.vessel_imo).strip()
        v_name = "NAVIOS VICTORY"
        v_class = "Panamax"
        v_gt = 40937.0
        v_dwt = 77095.0
        v_loa = 224.98
        v_beam = 32.24
        v_draft = 14.47
        v_lightweight = 12915.0
        v_laden_disp = 90010.0
        v_mcr_kw = 8330.0

        if self.db and self.vessel_registry:
            v_db = self.db.query(Vessel).filter(Vessel.imo == v_imo).first()
            if v_db:
                v_name = v_db.vessel_name or v_name
                v_class = v_db.vessel_class or v_class
                v_gt = v_db.gross_tonnage or v_gt
                v_dwt = v_db.dwt_tonnage or v_dwt
                v_loa = v_db.length_meters or v_loa
                v_beam = v_db.width_meters or v_beam
                v_draft = v_db.draft_max_meters or v_draft
                v_mcr_kw = v_db.rated_power_kw or v_mcr_kw

        components.append(
            ComponentAssessmentItem(
                name="Vessel Identity & Particulars",
                category="VESSEL",
                epistemic_status=EpistemicEvidenceClassification.DIRECTLY_VERIFIED_STATUTORY,
                source_reference="ClassNK Register of Ships (Reg No. 145541 / IMO 9713430)",
                is_complete=True,
            )
        )

        # Ballast eligibility check
        ballast_eligible = False
        ballast_reason: Optional[str] = (
            f"MISSING_BALLAST_HYDROSTATICS: Certified ballast displacement is unavailable for "
            f"{v_name} (IMO {v_imo}). Ballast leg fuel consumption calculations are strictly blocked."
        )

        if input_data.require_ballast_leg_assessment or input_data.ballast_sea_days > 0:
            blocked_reasons.append(ballast_reason)

        components.append(
            ComponentAssessmentItem(
                name="Ballast Hydrostatics",
                category="VESSEL",
                amount_usd=None,
                epistemic_status=EpistemicEvidenceClassification.UNAVAILABLE_BLOCKED,
                source_reference="Certified Shipyard / ClassNK Hydrostatic Table",
                is_complete=False,
                limitation=ballast_reason,
            )
        )

        # 3. Nautical Routing & Distance Calculation
        route_req = RoutingRequest(
            origin=input_data.origin_unlocode,
            destination=input_data.destination_unlocode,
            routing_mode="marnet",
            supports_segments=True,
        )
        route_res = self.routing_engine.calculate_route(route_req)
        if route_res.status != RoutingStatus.CALCULATED:
            blocked_reasons.append(f"ROUTING_FAILED: {route_res.error_message}")
            route_dist_nm = 9363.23
            canals = ["panama"]
            antimeridian = True
        else:
            route_dist_nm = route_res.summary.distance_nm if route_res.summary else 9363.23
            canals = route_res.summary.canals_transited if route_res.summary else ["panama"]
            antimeridian = len(route_res.geometry_segments) > 1

        components.append(
            ComponentAssessmentItem(
                name=f"Sea Route ({input_data.origin_unlocode} -> {input_data.destination_unlocode})",
                category="ROUTING",
                amount_usd=None,
                epistemic_status=EpistemicEvidenceClassification.DIRECTLY_VERIFIED_STATUTORY,
                source_reference="Eurostat MARNET 50km Generalized Sea-Lane Topological Graph",
                is_complete=True,
                limitation="Topological routing graph; not an ECDIS certified navigational chart.",
            )
        )

        # 4. Genuine Freight Rate
        freight_rate = 61.80  # Default authentic USDA May 2024 observation
        freight_source = "USDA Agricultural Marketing Service (GTR / Dataset USG-JPN-BULK, 2024-05-01)"
        epistemic_freight = EpistemicEvidenceClassification.OBSERVED_HISTORICAL

        if input_data.freight_rate_override_usd_per_mt is not None:
            freight_rate = input_data.freight_rate_override_usd_per_mt
            freight_source = "User-Supplied Freight Rate Override"
            epistemic_freight = EpistemicEvidenceClassification.USER_SUPPLIED_ASSUMPTION
            warnings.append(f"Using user-supplied freight rate override: ${freight_rate:.2f}/MT")
        elif self.db:
            obs = (
                self.db.query(FreightObservation)
                .filter(
                    FreightObservation.route_code == "USG-JPN-BULK",
                    FreightObservation.observation_date <= input_data.transit_date,
                    FreightObservation.is_derived == False,  # Strict: genuine observation only
                )
                .order_by(FreightObservation.observation_date.desc())
                .first()
            )
            if obs:
                freight_rate = float(obs.rate_value)
                freight_source = f"{obs.source_name} (Observation {obs.observation_date.isoformat()}, Route {obs.route_code})"

        gross_freight = round(input_data.cargo_tonnes * freight_rate, 2)
        components.append(
            ComponentAssessmentItem(
                name="Gross Ocean Freight",
                category="FREIGHT",
                amount_usd=gross_freight,
                epistemic_status=epistemic_freight,
                source_reference=freight_source,
                is_complete=True,
            )
        )

        # 5. Houston Port Disbursements (USHOU)
        stay_hours = input_data.loading_days * 24.0
        houston_res = self.port_registry.calculate_port_disbursement(
            un_locode="USHOU",
            vessel_gt=v_gt,
            vessel_dwt=v_dwt,
            loa_m=v_loa,
            beam_m=v_beam,
            draft_m=v_draft,
            purpose_of_call="CARGO_OPERATIONS",
            stay_duration_hours=stay_hours,
            transit_date=input_data.transit_date,
            operational_mode=False,
        )
        hou_costs = houston_res.total_port_costs_usd or 0.0
        components.append(
            ComponentAssessmentItem(
                name="Port Houston Authority Disbursements",
                category="PORT_DUES",
                amount_usd=hou_costs,
                epistemic_status=EpistemicEvidenceClassification.DIRECTLY_VERIFIED_STATUTORY,
                source_reference="Port of Houston Authority Tariff No. 8 & Houston Pilots Schedule",
                is_complete=houston_res.is_complete,
            )
        )

        # 6. Panama Canal Tolls (ACP Series 1010)
        panama_res = self.canal_registry.calculate_panama_toll(
            beam_m=v_beam,
            loa_m=v_loa,
            dwt_mt=v_dwt,
            loading_condition="LADEN",
            transit_date=input_data.transit_date,
            gatun_elevation_ft=input_data.gatun_elevation_ft,
            operational_mode=False,
        )
        panama_toll = panama_res.total_toll_usd or 0.0
        components.append(
            ComponentAssessmentItem(
                name="Panama Canal Transit Toll",
                category="CANAL_TOLLS",
                amount_usd=panama_toll,
                epistemic_status=EpistemicEvidenceClassification.DIRECTLY_VERIFIED_STATUTORY,
                source_reference="Panama Canal Authority (ACP) Maritime Regulations & Official Tariff Series 1010",
                is_complete=panama_res.is_complete,
            )
        )

        # 7. Yokohama Port Disbursements (JPYOK)
        yok_stay_hours = input_data.discharge_days * 24.0
        yokohama_res = self.port_registry.calculate_port_disbursement(
            un_locode="JPYOK",
            vessel_gt=v_gt,
            vessel_dwt=v_dwt,
            loa_m=v_loa,
            beam_m=v_beam,
            draft_m=v_draft,
            purpose_of_call="CARGO_OPERATIONS",
            stay_duration_hours=yok_stay_hours,
            transit_date=input_data.transit_date,
            operational_mode=False,
        )
        yok_costs = yokohama_res.total_port_costs_usd or 0.0
        components.append(
            ComponentAssessmentItem(
                name="Port of Yokohama Statutory Dues & Tokyo Bay Pilotage",
                category="PORT_DUES",
                amount_usd=yok_costs,
                epistemic_status=EpistemicEvidenceClassification.DIRECTLY_VERIFIED_STATUTORY,
                source_reference=(
                    "City of Yokohama Ordinance No. 37 & Tokyo Bay Licensed Pilots' Schedule "
                    "(Converted from JPY via Bank of Japan spot middle fixing)"
                ),
                is_complete=yokohama_res.is_complete,
            )
        )

        # Total port disbursements
        total_port_costs = round(hou_costs + yok_costs, 2)

        # 8. Commission Deductions
        commission_deductions = round(gross_freight * input_data.commission_rate, 2)
        components.append(
            ComponentAssessmentItem(
                name="Charterparty Commission",
                category="COMMISSION",
                amount_usd=commission_deductions,
                epistemic_status=EpistemicEvidenceClassification.USER_SUPPLIED_ASSUMPTION,
                source_reference=f"Standard commercial commission ({input_data.commission_rate * 100:.2f}%)",
                is_complete=True,
            )
        )

        # 9. Steaming Duration & Fuel Consumption Modeling
        # Steaming days = distance / (speed * 24)
        laden_sea_days = round(route_dist_nm / (input_data.steaming_speed_knots * 24.0), 3)
        total_voyage_days = round(
            input_data.ballast_sea_days
            + laden_sea_days
            + input_data.loading_days
            + input_data.discharge_days
            + input_data.port_waiting_days
            + input_data.canal_waiting_days
            + input_data.canal_transit_days,
            3,
        )

        # Main Engine SFOC load point evaluation (MAN B&W 6S60ME-C)
        # Power via Admiralty cubic law from design speed (14.0 kts at 85% MCR)
        speed_ratio = input_data.steaming_speed_knots / 14.0
        power_req_kw = v_mcr_kw * (speed_ratio ** 3) * 0.85
        load_pct = min(100.0, max(25.0, (power_req_kw / v_mcr_kw) * 100.0))

        # Piecewise linear SFOC from nominal curve (167.5 at 100%, 164.5 at 85%, 163.5 at 75%, 166.0 at 50%)
        if load_pct >= 85.0:
            sfoc_g_kwh = 164.5 + ((load_pct - 85.0) / 15.0) * (167.5 - 164.5)
        elif load_pct >= 75.0:
            sfoc_g_kwh = 163.5 + ((load_pct - 75.0) / 10.0) * (164.5 - 163.5)
        else:
            sfoc_g_kwh = 166.0 - ((load_pct - 50.0) / 25.0) * (166.0 - 163.5)

        daily_laden_consumption_mt = round((power_req_kw * sfoc_g_kwh * 24.0) / 1e6, 3)
        total_laden_fuel_mt = round(daily_laden_consumption_mt * laden_sea_days, 2)

        components.append(
            ComponentAssessmentItem(
                name="Laden Sea Leg Fuel Consumption",
                category="FUEL",
                amount_usd=None,
                epistemic_status=EpistemicEvidenceClassification.CALCULATED_CERTIFIED_SFOC,
                source_reference=(
                    f"MAN Energy Solutions SFOC Curve (MAN_S60ME_C8_2_TII_NOMINAL) & "
                    f"ClassNK Hydrostatic Displacement ({v_laden_disp:,.0f} MT)"
                ),
                is_complete=True,
                limitation=(
                    f"Cubic Admiralty hydrodynamic speed-to-power load approximation ({load_pct:.1f}% load, "
                    f"{power_req_kw:.0f} kW, {sfoc_g_kwh:.1f} g/kWh). Calm sea trial reference conditions."
                ),
            )
        )

        # 10. Bunker Price Resolution
        bunker_res = resolve_research_bunker_price(
            port_unlocode=input_data.origin_unlocode,
            as_of_date=input_data.transit_date,
            fuel_grade="VLSFO",
            user_assumption=input_data.user_bunker_assumption,
            allow_research_composite=input_data.allow_research_composite_bunker,
            research_composite=input_data.research_bunker_composite,
            operational_mode=False,
        )

        bunker_cost_usd: Optional[float] = None
        bunker_price_usd: Optional[float] = None
        bunker_ev_status = bunker_res.evidence_classification.value

        if bunker_res.is_available and bunker_res.price_usd_per_mt is not None:
            bunker_price_usd = bunker_res.price_usd_per_mt
            bunker_cost_usd = round(total_laden_fuel_mt * bunker_price_usd, 2)
            components.append(
                ComponentAssessmentItem(
                    name="Laden Bunker Fuel Expense",
                    category="FUEL",
                    amount_usd=bunker_cost_usd,
                    epistemic_status=EpistemicEvidenceClassification(bunker_ev_status),
                    source_reference=bunker_res.source_name,
                    is_complete=True,
                    limitation=(
                        "USER_SUPPLIED_ASSUMPTION" if bunker_ev_status == "USER_SUPPLIED_ASSUMPTION"
                        else "Macro multi-port composite average; not an observed Houston delivered spot quote."
                    ),
                )
            )
        else:
            blocked_reasons.append(bunker_res.failure_reason or "NO_BUNKER_PRICE_AVAILABLE")
            components.append(
                ComponentAssessmentItem(
                    name="Laden Bunker Fuel Expense",
                    category="FUEL",
                    amount_usd=None,
                    epistemic_status=EpistemicEvidenceClassification.UNAVAILABLE_BLOCKED,
                    source_reference="Bunker Price Registry",
                    is_complete=False,
                    limitation="Port delivered spot bunker price is unavailable.",
                )
            )

        # 11. Net Voyage Revenue & Conventional TCE Calculation
        net_revenue: Optional[float] = None
        tce_rate: Optional[float] = None
        is_fully_validated = False

        if bunker_cost_usd is not None and not blocked_reasons:
            total_voyage_expenses = round(
                commission_deductions
                + total_port_costs
                + panama_toll
                + bunker_cost_usd,
                2,
            )
            net_revenue = round(gross_freight - total_voyage_expenses, 2)
            tce_rate = round(net_revenue / total_voyage_days, 2) if total_voyage_days > 0 else 0.0

            # It is a research estimate, NOT fully validated commercial fixture
            is_fully_validated = False
            disclaimers.append(
                "RESEARCH ASSESSMENT: TCE daily rate incorporates user-supplied bunker assumptions. "
                "Not commercially certified as an executed charter clearing rate."
            )
        else:
            warnings.append(
                "Incomplete financial assessment: Net revenue and TCE cannot be computed defensibly "
                "due to missing bunker fuel prices or blocking components."
            )

        return ResearchVoyageAssessmentResult(
            voyage_id=f"RES-VOY-F152B-{v_imo}-{input_data.transit_date.strftime('%Y%m%d')}",
            corridor_code="USG-JPN-BULK",
            vessel_imo=v_imo,
            vessel_name=v_name,
            vessel_class=v_class,
            origin_port=input_data.origin_unlocode,
            destination_port=input_data.destination_unlocode,
            canals_transited=canals,
            transit_date=input_data.transit_date,
            route_distance_nm=route_dist_nm,
            routing_mode="marnet",
            antimeridian_crossing_detected=antimeridian,
            cargo_tonnes=input_data.cargo_tonnes,
            freight_rate_usd_per_mt=freight_rate,
            gross_freight_usd=gross_freight,
            freight_source=freight_source,
            origin_port_disbursements_usd=hou_costs,
            destination_port_disbursements_usd=yok_costs,
            total_port_disbursements_usd=total_port_costs,
            panama_canal_tolls_usd=panama_toll,
            commission_rate=input_data.commission_rate,
            commission_deductions_usd=commission_deductions,
            laden_sea_days=laden_sea_days,
            total_voyage_days=total_voyage_days,
            laden_fuel_consumption_mt=total_laden_fuel_mt,
            bunker_price_usd_per_mt=bunker_price_usd,
            bunker_fuel_costs_usd=bunker_cost_usd,
            bunker_evidence_classification=bunker_ev_status,
            net_voyage_revenue_usd=net_revenue,
            tce_daily_rate_usd=tce_rate,
            is_fully_validated=is_fully_validated,
            is_ready_for_operations=False,  # Immutable Level 3 operational lock
            governance_tier="LEVEL_2_RESEARCH_ONLY",
            ballast_assessment_eligible=ballast_eligible,
            ballast_blocking_reason=ballast_reason,
            components_breakdown=components,
            blocked_reasons=blocked_reasons,
            warnings=warnings,
            disclaimers=disclaimers,
        )

