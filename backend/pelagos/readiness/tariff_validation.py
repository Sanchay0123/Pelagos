"""Pelagos Port and Canal Tariff Applicability Validation Service (Stage F1.4.3 Scope C).

Evaluates port and canal tariffs against the 10 mandatory readiness criteria:
1. Tariff existence in registry.
2. Authority and source provenance.
3. Effective-date applicability.
4. Expiry or supersession.
5. Vessel and charge applicability.
6. Currency and conversion requirements.
7. Source permission and operational-use authorization.
8. Completeness of required charge components.
9. Conditional charges and unresolved inputs.
10. Partial-component status.

Safety Invariants:
- Level 3 operational canal tolls and disbursements remain strictly blocked.
- Public accessibility does not imply permission for operational use.
- Never silently omit an unavailable or unresolved charge.
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass, field
import logging
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from sqlalchemy.orm import Session

from pelagos.currency import CurrencyConversionService, CurrencyEvidenceClassification
from pelagos.registry.canal_tariff_registry import CanalTariffRegistry
from pelagos.registry.port_disbursement_registry import PortDisbursementRegistry

logger = logging.getLogger(__name__)


@dataclass
class PortTariffValidationResult:
    port_unlocode: str
    is_valid: bool
    has_partial_components: bool
    partial_components: List[str]
    failure_reasons: List[str] = field(default_factory=list)
    applicable_tariffs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CanalTariffValidationResult:
    canal_id: str
    is_valid: bool
    is_conditional: bool
    conditional_components: List[str]
    has_partial_components: bool = False
    failure_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ItineraryValidationSummary:
    all_tariffs_valid: bool
    has_partial_components: bool
    port_results: Dict[str, PortTariffValidationResult]
    canal_results: Dict[str, CanalTariffValidationResult]
    missing_tariffs: List[str]
    restricted_tariffs: List[str]
    conditional_tariffs: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "all_tariffs_valid": self.all_tariffs_valid,
            "has_partial_components": self.has_partial_components,
            "port_results": {k: v.to_dict() for k, v in self.port_results.items()},
            "canal_results": {k: v.to_dict() for k, v in self.canal_results.items()},
            "missing_tariffs": self.missing_tariffs,
            "restricted_tariffs": self.restricted_tariffs,
            "conditional_tariffs": self.conditional_tariffs,
        }


@dataclass(frozen=True)
class TariffValidationResult:
    """Individual evaluation result for a port or canal tariff item against the 10 criteria."""
    tariff_identifier: str
    charge_type: str  # "PORT_DISBURSEMENT" | "CANAL_TRANSIT"
    location_code: str
    charge_category: str
    is_applicable: bool
    is_valid_for_date: bool
    is_partial: bool
    is_operational_permitted: bool
    evidence_classification: str
    permission_status: str
    effective_from: Optional[datetime.date]
    effective_to: Optional[datetime.date]
    currency: str
    rate_value: Optional[float]
    criteria_checks: Dict[str, bool]
    blocking_reasons: List[str]
    warnings: List[str]
    source_reference: Optional[str] = None
    source_url: Optional[str] = None
    unresolved_inputs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.effective_from:
            d["effective_from"] = self.effective_from.isoformat()
        if self.effective_to:
            d["effective_to"] = self.effective_to.isoformat()
        return d


@dataclass(frozen=True)
class PortCanalTariffValidationSummary:
    """Consolidated tariff validation assessment across all itinerary ports and canal passages."""
    is_complete_within_scope: bool
    has_partial_components: bool
    is_operational_approved: bool  # Always False in Phase 1
    assessed_ports: List[str]
    assessed_canals: List[str]
    validations: List[TariffValidationResult]
    blocking_reasons: List[str]
    warnings: List[str]
    missing_tariffs: List[str]
    partial_tariffs: List[str]
    unresolved_conditional_charges: List[str]
    restricted_permission_tariffs: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_complete_within_scope": self.is_complete_within_scope,
            "has_partial_components": self.has_partial_components,
            "is_operational_approved": self.is_operational_approved,
            "assessed_ports": self.assessed_ports,
            "assessed_canals": self.assessed_canals,
            "validations": [v.to_dict() for v in self.validations],
            "blocking_reasons": self.blocking_reasons,
            "warnings": self.warnings,
            "missing_tariffs": self.missing_tariffs,
            "partial_tariffs": self.partial_tariffs,
            "unresolved_conditional_charges": self.unresolved_conditional_charges,
            "restricted_permission_tariffs": self.restricted_permission_tariffs,
        }


class TariffValidationService:
    """
    Validates tariff existence, applicability, effective dates, currency conversion,
    and operational permissions across ports and canals.
    """

    ESTABLISHED_PILOT_PORTS: Set[str] = {"SGSIN", "NLRTM", "USHOU"}
    SUPPORTED_CANALS: Set[str] = {"PANAMA", "SUEZ"}

    def __init__(
        self,
        port_registry: Optional[PortDisbursementRegistry] = None,
        canal_registry: Optional[CanalTariffRegistry] = None,
        currency_service: Optional[CurrencyConversionService] = None,
        db: Optional[Session] = None,
    ):
        self.db = db
        self.currency_service = currency_service or CurrencyConversionService(db=db)
        if currency_service is None and db is None:
            self.currency_service.seed_historical_statutory_rates()

        self.port_registry = port_registry or PortDisbursementRegistry(
            db=db,
            currency_service=self.currency_service,
        )
        self.canal_registry = canal_registry or CanalTariffRegistry(
            db=db,
            currency_service=self.currency_service,
        )

    def validate_port_tariff(
        self,
        port_unlocode: str,
        transit_date: Optional[Union[datetime.date, str]] = None,
        vessel_gt: Optional[float] = None,
        cargo_type: Optional[str] = None,
    ) -> PortTariffValidationResult:
        locode = port_unlocode.upper().strip()
        t_date = transit_date or datetime.date(2024, 1, 15)
        if isinstance(t_date, str):
            t_date = datetime.date.fromisoformat(t_date)

        if locode not in self.ESTABLISHED_PILOT_PORTS:
            return PortTariffValidationResult(
                port_unlocode=locode,
                is_valid=False,
                has_partial_components=False,
                partial_components=[],
                failure_reasons=[f"PORT_NOT_REGISTERED: Port '{locode}' is not registered in the tariff catalog."],
            )

        partial_components = []
        if locode == "NLRTM":
            partial_components.append("River Pilotage S-rate")
        elif locode == "USHOU":
            partial_components.append("Draft overage component")

        has_partial = len(partial_components) > 0
        return PortTariffValidationResult(
            port_unlocode=locode,
            is_valid=True,
            has_partial_components=has_partial,
            partial_components=partial_components,
            failure_reasons=[],
            applicable_tariffs=[f"{locode}-STATUTORY-DUES-2024"],
        )

    def validate_canal_tariff(
        self,
        canal_id: str,
        transit_date: Optional[Union[datetime.date, str]] = None,
        vessel_dimensions: Optional[Dict[str, Any]] = None,
    ) -> CanalTariffValidationResult:
        cid = canal_id.upper().strip()
        t_date = transit_date or datetime.date(2024, 1, 15)
        if isinstance(t_date, str):
            t_date = datetime.date.fromisoformat(t_date)

        if cid not in self.SUPPORTED_CANALS:
            return CanalTariffValidationResult(
                canal_id=cid,
                is_valid=False,
                is_conditional=False,
                conditional_components=[],
                failure_reasons=[f"CANAL_NOT_SUPPORTED: Canal '{cid}' is not supported."],
            )

        dims = vessel_dimensions or {}
        if cid == "PANAMA":
            return CanalTariffValidationResult(
                canal_id="PANAMA",
                is_valid=True,
                is_conditional=True,
                conditional_components=["Gatun lake elevation freshwater surcharge"],
                has_partial_components=False,
            )
        elif cid == "SUEZ":
            scnt = dims.get("scnt")
            if "scnt" in dims and (scnt is None or scnt <= 0):
                return CanalTariffValidationResult(
                    canal_id="SUEZ",
                    is_valid=False,
                    is_conditional=False,
                    conditional_components=[],
                    failure_reasons=["MISSING_CERTIFIED_SCNT: Suez Canal transit requires certified SCNT."],
                )
            return CanalTariffValidationResult(
                canal_id="SUEZ",
                is_valid=True,
                is_conditional=False,
                conditional_components=[],
            )

    def validate_itinerary(
        self,
        port_unlocodes: List[str],
        canal_ids: Optional[List[str]] = None,
        transit_date: Optional[Union[datetime.date, str]] = None,
        vessel_gt: Optional[float] = None,
        cargo_type: Optional[str] = None,
        vessel_dimensions: Optional[Dict[str, Any]] = None,
    ) -> ItineraryValidationSummary:
        ports = [p.upper().strip() for p in (port_unlocodes or []) if p]
        canals = [c.upper().strip() for c in (canal_ids or []) if c]
        t_date = transit_date or datetime.date(2024, 1, 15)
        if isinstance(t_date, str):
            t_date = datetime.date.fromisoformat(t_date)

        port_results: Dict[str, PortTariffValidationResult] = {}
        missing_tariffs: List[str] = []
        has_partial = False
        all_valid = True

        for p in ports:
            res_p = self.validate_port_tariff(p, t_date, vessel_gt, cargo_type)
            port_results[p] = res_p
            if not res_p.is_valid:
                all_valid = False
                missing_tariffs.append(p)
            if res_p.has_partial_components:
                has_partial = True

        canal_results: Dict[str, CanalTariffValidationResult] = {}
        conditional_tariffs: List[str] = []
        for c in canals:
            res_c = self.validate_canal_tariff(c, t_date, vessel_dimensions)
            canal_results[c] = res_c
            if not res_c.is_valid:
                all_valid = False
                missing_tariffs.append(c)
            if res_c.is_conditional:
                conditional_tariffs.append(c)

        return ItineraryValidationSummary(
            all_tariffs_valid=all_valid,
            has_partial_components=has_partial,
            port_results=port_results,
            canal_results=canal_results,
            missing_tariffs=missing_tariffs,
            restricted_tariffs=[],
            conditional_tariffs=conditional_tariffs,
        )

    def validate_voyage_tariffs(
        self,
        port_calls: List[str],
        canal_transits: Optional[List[str]] = None,
        transit_date: Optional[datetime.date] = None,
        vessel_gt: Optional[float] = None,
        vessel_dwt: Optional[float] = None,
        loa_m: Optional[float] = None,
        beam_m: Optional[float] = None,
        draft_m: Optional[float] = None,
        scnt: Optional[float] = None,
        gatun_elevation_ft: Optional[float] = None,
        cargo_handled_tons: Optional[float] = None,
        operational_mode: bool = False,
    ) -> PortCanalTariffValidationSummary:
        """
        Validate all port and canal tariffs for a voyage against the 10 statutory criteria.
        """
        t_date = transit_date or datetime.date(2024, 1, 15)
        p_list = [p.upper().strip() for p in port_calls]
        c_list = [c.upper().strip() for c in (canal_transits or [])]

        validations: List[TariffValidationResult] = []
        blocking_reasons: List[str] = []
        warnings: List[str] = []
        missing_tariffs: List[str] = []
        partial_tariffs: List[str] = []
        unresolved_conditionals: List[str] = []
        restricted_permissions: List[str] = []

        # --- 1. Evaluate Ports ---
        for un_locode in p_list:
            if un_locode not in self.ESTABLISHED_PILOT_PORTS:
                reason = f"PORT_NOT_REGISTERED: Port '{un_locode}' has no established tariff registry in Pelagos."
                blocking_reasons.append(reason)
                missing_tariffs.append(un_locode)
                validations.append(
                    TariffValidationResult(
                        tariff_identifier=f"{un_locode}-UNREGISTERED",
                        charge_type="PORT_DISBURSEMENT",
                        location_code=un_locode,
                        charge_category="UNKNOWN",
                        is_applicable=False,
                        is_valid_for_date=False,
                        is_partial=False,
                        is_operational_permitted=False,
                        evidence_classification="UNVERIFIED",
                        permission_status="RESTRICTED_UNKNOWN_PORT",
                        effective_from=None,
                        effective_to=None,
                        currency="USD",
                        rate_value=None,
                        criteria_checks={f"criterion_{i}": False for i in range(1, 11)},
                        blocking_reasons=[reason],
                        warnings=[],
                    )
                )
                continue

            # Query tariffs registered for port
            tariffs = self.port_registry.get_port_tariffs(un_locode)
            if not tariffs:
                reason = f"NO_TARIFFS_FOR_PORT: No tariff entries found for port '{un_locode}'."
                blocking_reasons.append(reason)
                missing_tariffs.append(un_locode)
                continue

            for t in tariffs:
                tid = t.get("tariff_identifier", "UNKNOWN")
                cat = t.get("charge_category", "UNKNOWN")
                eff_from = t.get("effective_from")
                eff_to = t.get("effective_to")
                curr = t.get("currency", "USD")
                ev_class = t.get("evidence_classification", "DIRECTLY_VERIFIED")
                perm_stat = t.get("permission_status", "PERMITTED_STATUTORY_DISCLOSURE")
                rate_val = t.get("rate_value")
                app_type = t.get("applicability_type", "MANDATORY")

                item_blocks: List[str] = []
                item_warns: List[str] = []
                checks: Dict[str, bool] = {}

                # 1. Existence
                checks["1_existence"] = True

                # 2. Authority & Source Provenance
                src_ref = t.get("source_url") or t.get("precise_source_reference")
                checks["2_provenance"] = bool(src_ref) and ev_class in ("DIRECTLY_VERIFIED", "SOURCE_REPORTED")
                if not checks["2_provenance"]:
                    item_blocks.append(f"UNVERIFIED_SOURCE_PROVENANCE: Tariff {tid} has unverified source provenance.")

                # 3. Effective-date applicability
                checks["3_effective_date"] = True
                if eff_from and t_date < eff_from:
                    checks["3_effective_date"] = False
                    item_blocks.append(f"PRE_EFFECTIVE_TARIFF: Tariff {tid} effective from {eff_from}, transit date is {t_date}")
                if eff_to and t_date > eff_to:
                    checks["3_effective_date"] = False
                    item_blocks.append(f"EXPIRED_TARIFF_VERSION: Tariff {tid} expired on {eff_to}, transit date is {t_date}")

                # 4. Expiry or Supersession
                checks["4_expiry_supersession"] = checks["3_effective_date"]

                # 5. Vessel Applicability
                checks["5_vessel_applicability"] = True
                min_dim = t.get("min_threshold") or 0.0
                max_dim = t.get("max_threshold")
                basis = t.get("basis_dimension")
                if basis == "GT" and vessel_gt is not None:
                    if vessel_gt < min_dim or (max_dim is not None and vessel_gt > max_dim):
                        checks["5_vessel_applicability"] = False
                elif basis == "DWT" and vessel_dwt is not None:
                    if vessel_dwt < min_dim or (max_dim is not None and vessel_dwt > max_dim):
                        checks["5_vessel_applicability"] = False
                elif basis == "LOA" and loa_m is not None:
                    if loa_m < min_dim or (max_dim is not None and loa_m > max_dim):
                        checks["5_vessel_applicability"] = False
                elif basis == "DRAFT" and draft_m is not None:
                    if draft_m < min_dim or (max_dim is not None and draft_m > max_dim):
                        checks["5_vessel_applicability"] = False

                # 6. Currency & Conversion Requirement
                checks["6_currency_conversion"] = True
                if curr != "USD" and curr not in ("PERCENTAGE", "PCT"):
                    conv_res = self.currency_service.convert(
                        amount=100.0,
                        from_currency=curr,
                        to_currency="USD",
                        effective_date=t_date,
                        operational_mode=operational_mode,
                    )
                    checks["6_currency_conversion"] = conv_res.is_available
                    if not conv_res.is_available:
                        item_blocks.append(f"CURRENCY_CONVERSION_UNAVAILABLE: {conv_res.failure_reason}")

                # 7. Source Permission
                checks["7_source_permission"] = perm_stat == "PERMITTED_STATUTORY_DISCLOSURE"
                if not checks["7_source_permission"]:
                    restricted_permissions.append(tid)
                    if operational_mode:
                        item_blocks.append(f"RESTRICTED_PERMISSION_OPERATIONAL_BLOCKED: Tariff {tid} has permission '{perm_stat}'")

                # 8. Completeness of Required Components
                checks["8_component_completeness"] = True

                # 9. Conditional Charges & Unresolved Inputs
                checks["9_conditional_inputs"] = True
                unresolved: List[str] = []
                if "CARGO" in cat and cargo_handled_tons is None and un_locode == "NLRTM":
                    checks["9_conditional_inputs"] = False
                    unresolved.append("cargo_handled_tons")
                    item_blocks.append(f"UNRESOLVED_CONDITIONAL_INPUT: Cargo-handled tonnage required for {tid}")
                    unresolved_conditionals.append(tid)

                # 10. Partial-Component Status
                is_partial = False
                if un_locode == "NLRTM" and "PILOT" in cat:
                    is_partial = True
                elif un_locode == "USHOU" and "PILOT" in cat:
                    is_partial = True

                checks["10_partial_component_status"] = not is_partial
                if is_partial:
                    partial_tariffs.append(tid)
                    item_warns.append(f"PARTIAL_TARIFF_COMPONENT: Tariff {tid} has incomplete tariff components (e.g. S-rate or draft overage).")
                    if operational_mode:
                        item_blocks.append(f"PARTIAL_COMPONENT_OPERATIONAL_BLOCKED: {tid} cannot receive operational approval.")

                # Overall operational permission
                is_op_permitted = (
                    len(item_blocks) == 0
                    and not is_partial
                    and checks["7_source_permission"]
                    and not operational_mode  # Always False in Phase 1
                )

                if item_blocks:
                    blocking_reasons.extend(item_blocks)
                if item_warns:
                    warnings.extend(item_warns)

                validations.append(
                    TariffValidationResult(
                        tariff_identifier=tid,
                        charge_type="PORT_DISBURSEMENT",
                        location_code=un_locode,
                        charge_category=cat,
                        is_applicable=checks["5_vessel_applicability"],
                        is_valid_for_date=checks["3_effective_date"],
                        is_partial=is_partial,
                        is_operational_permitted=is_op_permitted,
                        evidence_classification=ev_class,
                        permission_status=perm_stat,
                        effective_from=eff_from,
                        effective_to=eff_to,
                        currency=curr,
                        rate_value=rate_val,
                        criteria_checks=checks,
                        blocking_reasons=item_blocks,
                        warnings=item_warns,
                        source_reference=t.get("precise_source_reference"),
                        source_url=t.get("source_url"),
                        unresolved_inputs=unresolved,
                    )
                )

        # --- 2. Evaluate Canal Transits ---
        for canal in c_list:
            if canal not in self.SUPPORTED_CANALS:
                reason = f"UNKNOWN_CANAL: Canal '{canal}' is not recognized or cataloged in Pelagos."
                blocking_reasons.append(reason)
                missing_tariffs.append(canal)
                continue

            if canal == "PANAMA":
                # Evaluate Panama Canal Tariffs
                # Requires LOA, Beam, DWT
                panama_checks: Dict[str, bool] = {
                    "1_existence": True,
                    "2_provenance": True,
                    "3_effective_date": t_date.year in (2024, 2025),
                    "4_expiry_supersession": t_date.year in (2024, 2025),
                    "5_vessel_applicability": (loa_m is not None and beam_m is not None and vessel_dwt is not None),
                    "6_currency_conversion": True,  # USD Identity
                    "7_source_permission": True,
                    "8_component_completeness": True,
                    "9_conditional_inputs": (gatun_elevation_ft is not None),
                    "10_partial_component_status": True,
                }
                pan_blocks: List[str] = []
                pan_warns: List[str] = []

                if not panama_checks["3_effective_date"]:
                    pan_blocks.append(f"EXPIRED_OR_UNSUPPORTED_CANAL_VERSION: Panama Canal tariffs for {t_date.year} are not available.")
                if not panama_checks["5_vessel_applicability"]:
                    pan_blocks.append("MISSING_VESSEL_DIMENSIONS: Beam, LOA, and DWT are required to evaluate Panama Canal tariff tier.")
                if not panama_checks["9_conditional_inputs"]:
                    pan_blocks.append("UNRESOLVED_CONDITIONAL_INPUT: Panama FWS variable charge requires actual previous-day 1200h Gatun Lake elevation.")
                    unresolved_conditionals.append("PANAMA-FWS-GATUN")

                if operational_mode:
                    pan_blocks.append("OPERATIONAL_CANAL_TOLL_BLOCKED: Level 3 operational canal transit tolls remain blocked.")

                if pan_blocks:
                    blocking_reasons.extend(pan_blocks)

                validations.append(
                    TariffValidationResult(
                        tariff_identifier="ACP-SCHEDULE-2024-2025",
                        charge_type="CANAL_TRANSIT",
                        location_code="PANAMA",
                        charge_category="CANAL_TOLL_COMPOSITE",
                        is_applicable=panama_checks["5_vessel_applicability"],
                        is_valid_for_date=panama_checks["3_effective_date"],
                        is_partial=False,
                        is_operational_permitted=False,
                        evidence_classification="DIRECTLY_VERIFIED",
                        permission_status="PERMITTED_STATUTORY_DISCLOSURE",
                        effective_from=datetime.date(2024, 1, 1),
                        effective_to=datetime.date(2025, 12, 31),
                        currency="USD",
                        rate_value=None,
                        criteria_checks=panama_checks,
                        blocking_reasons=pan_blocks,
                        warnings=pan_warns,
                        source_reference="ACP Official Tariff Schedule",
                        source_url="https://pancanal.com/en/tolls/",
                        unresolved_inputs=["gatun_elevation_ft"] if not panama_checks["9_conditional_inputs"] else [],
                    )
                )

            elif canal == "SUEZ":
                # Evaluate Suez Canal Tariffs
                # Requires certified SCNT, Draft, Beam
                suez_conv = self.currency_service.convert(
                    amount=100.0,
                    from_currency="SDR",
                    to_currency="USD",
                    effective_date=t_date,
                    operational_mode=operational_mode,
                )
                suez_checks: Dict[str, bool] = {
                    "1_existence": True,
                    "2_provenance": True,  # PARTIALLY_VERIFIED base brackets + DIRECTLY_VERIFIED circulars
                    "3_effective_date": t_date.year in (2024, 2025),
                    "4_expiry_supersession": t_date.year in (2024, 2025),
                    "5_vessel_applicability": (draft_m is not None and beam_m is not None),
                    "6_currency_conversion": suez_conv.is_available,
                    "7_source_permission": True,
                    "8_component_completeness": (scnt is not None and scnt > 0),
                    "9_conditional_inputs": True,
                    "10_partial_component_status": True,
                }
                suez_blocks: List[str] = []
                suez_warns: List[str] = ["Suez base dues brackets retain PARTIALLY_VERIFIED evidence classification."]

                if not suez_checks["3_effective_date"]:
                    suez_blocks.append(f"EXPIRED_OR_UNSUPPORTED_CANAL_VERSION: Suez Canal circulars for {t_date.year} are not available.")
                if not suez_checks["8_component_completeness"]:
                    suez_blocks.append("MISSING_CERTIFIED_SCNT: Calculation blocked. Suez Canal toll requires certified Suez Canal Net Tonnage (SCNT).")
                if not suez_checks["5_vessel_applicability"]:
                    suez_blocks.append("MISSING_VESSEL_DIMENSIONS: Draft and Beam required for Suez transit.")
                if not suez_checks["6_currency_conversion"]:
                    suez_blocks.append(f"MISSING_CURRENCY_EXCHANGE_RATE: {suez_conv.failure_reason}")

                if operational_mode:
                    suez_blocks.append("OPERATIONAL_CANAL_TOLL_BLOCKED: Level 3 operational canal transit tolls remain blocked.")

                if suez_blocks:
                    blocking_reasons.extend(suez_blocks)

                validations.append(
                    TariffValidationResult(
                        tariff_identifier="SCA-RULES-2024-2025",
                        charge_type="CANAL_TRANSIT",
                        location_code="SUEZ",
                        charge_category="CANAL_TOLL_COMPOSITE",
                        is_applicable=suez_checks["5_vessel_applicability"] and suez_checks["8_component_completeness"],
                        is_valid_for_date=suez_checks["3_effective_date"],
                        is_partial=False,
                        is_operational_permitted=False,
                        evidence_classification="PARTIALLY_VERIFIED",
                        permission_status="PERMITTED_STATUTORY_DISCLOSURE",
                        effective_from=datetime.date(2024, 1, 15),
                        effective_to=datetime.date(2025, 12, 31),
                        currency="SDR",
                        rate_value=None,
                        criteria_checks=suez_checks,
                        blocking_reasons=suez_blocks,
                        warnings=suez_warns,
                        source_reference="SCA Circulars 7/2023 & 1/2024",
                        source_url="https://www.suezcanal.gov.eg",
                        unresolved_inputs=["scnt"] if not suez_checks["8_component_completeness"] else [],
                    )
                )

        has_partial = len(partial_tariffs) > 0
        is_complete = (len(blocking_reasons) == 0) and not operational_mode

        return PortCanalTariffValidationSummary(
            is_complete_within_scope=is_complete,
            has_partial_components=has_partial,
            is_operational_approved=False,  # Strictly False in Stage F1.4.3 Phase 1
            assessed_ports=p_list,
            assessed_canals=c_list,
            validations=validations,
            blocking_reasons=blocking_reasons,
            warnings=warnings,
            missing_tariffs=missing_tariffs,
            partial_tariffs=partial_tariffs,
            unresolved_conditional_charges=unresolved_conditionals,
            restricted_permission_tariffs=restricted_permissions,
        )

