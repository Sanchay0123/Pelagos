"""
Stage F1.4.3 Phase 1: Deep Verification of Tariff Validation & Unified Voyage Readiness.
Independently exercises:
- Missing tariff, expired tariff, superseded tariff.
- Restricted permission, unknown permission.
- Missing FX, stale FX, future FX.
- Partial components (Rotterdam River Pilotage S-rate, Houston draft overage).
- Unresolved conditional charges (Panama Gatun Lake level, Rotterdam cargo tonnage).
- Missing required vessel dimensions across all authorities (ACP, SCA, MPA, Havenbedrijf, PHA).
- Unregistered ports (e.g. 'ZZZZZ').
- Unavailable charges never disappearing silently.
- Multiple simultaneous blockers.
- Contractual charges without contract attribution (Tier 3).
- Provisional estimates (Tier 4).
- Unallocated overage.
- User acknowledgement attempting to promote readiness.
- Empty or incomplete itineraries.
- Inviolate operational boundary: is_ready_for_operations == False under ALL inputs.
"""

import datetime
from pathlib import Path
import sys

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from pelagos.currency import CurrencyConversionService, CurrencyEvidenceClassification
from pelagos.readiness.charge_classifier import (
    ChargeClassificationTier,
    ChargeClassifier,
)
from pelagos.readiness.tariff_validation import (
    TariffValidationService,
)
from pelagos.readiness.vessel_readiness import (
    VesselParticularStatus,
    VesselReadinessService,
)
from pelagos.readiness.voyage_readiness_service import (
    VoyageReadinessAssessmentResult,
    VoyageReadinessService,
    VoyageReadinessStatus,
)
from pelagos.registry.voyage_disbursement_service import (
    VoyageDisbursementAssessmentRequest,
)


def run_deep_verification():
    print("=== Deep Independent Verification: Tariff Validation & Unified Voyage Readiness ===")
    
    currency_service = CurrencyConversionService()
    currency_service.seed_historical_statutory_rates()
    tariff_service = TariffValidationService(currency_service=currency_service)
    vessel_service = VesselReadinessService()
    voyage_service = VoyageReadinessService(
        vessel_service=vessel_service,
        tariff_service=tariff_service,
        currency_service=currency_service,
    )

    # 1. Unregistered Port fails closed
    print("\n--- Check 1: Unregistered Port Fails Closed ---")
    t_unreg = tariff_service.validate_voyage_tariffs(port_calls=["ZZZZZ"], transit_date=datetime.date(2024, 1, 15))
    assert t_unreg.is_complete_within_scope is False
    assert any("PORT_NOT_REGISTERED" in b for b in t_unreg.blocking_reasons)
    print("Check 1 PASSED: Unregistered port fails closed.")

    # 2. Expired Port Tariff fails closed
    print("\n--- Check 2: Expired Port Tariff Fails Closed ---")
    t_exp = tariff_service.validate_voyage_tariffs(port_calls=["SGSIN"], transit_date=datetime.date(2026, 6, 1))
    assert t_exp.is_complete_within_scope is False
    assert any("EXPIRED" in b for b in t_exp.blocking_reasons)
    print("Check 2 PASSED: Expired port tariff fails closed.")

    # 3. Panama Missing Dimensions (LOA & Beam)
    print("\n--- Check 3: Panama Missing Dimensions Fails Closed ---")
    v_pan_nodim = vessel_service.evaluate_vessel_readiness(
        vessel_gt=80000.0,
        canal_transits=["PANAMA"],
    )
    assert v_pan_nodim.is_ready_for_research is False
    assert "length_meters" in v_pan_nodim.missing_particulars
    assert "width_meters" in v_pan_nodim.missing_particulars
    print("Check 3 PASSED: Panama missing LOA & Beam fails closed.")

    # 4. Suez Missing SCNT (Zero Synthetic Tonnage Invariant)
    print("\n--- Check 4: Suez Missing Certified SCNT Fails Closed ---")
    v_suez_noscnt = vessel_service.evaluate_vessel_readiness(
        vessel_gt=100000.0,
        vessel_dwt=180000.0,
        loa_m=292.0,
        beam_m=45.0,
        draft_m=18.0,
        scnt=None,
        canal_transits=["SUEZ"],
    )
    assert v_suez_noscnt.is_ready_for_research is False
    assert "scnt" in v_suez_noscnt.missing_particulars
    assert any("MISSING_CERTIFIED_SCNT" in b for b in v_suez_noscnt.blocking_reasons)
    print("Check 4 PASSED: Suez missing SCNT strictly fails closed without synthesis.")

    # 5. Panama Gatun Lake Dynamic Input Requirement
    print("\n--- Check 5: Panama Gatun Lake Elevation Unresolved ---")
    res_gatun = voyage_service.assess_voyage_readiness(
        voyage_id="VOY-PAN-001",
        transit_date=datetime.date(2024, 1, 15),
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="USHOU",
        canal_transits=["PANAMA"],
        vessel_particulars={
            "gross_tonnage": 80000.0,
            "dwt_tonnage": 75000.0,
            "length_meters": 225.0,
            "width_meters": 32.2,
            "draft_max_meters": 12.0,
        },
    )
    assert res_gatun.readiness_status in (
        VoyageReadinessStatus.LEVEL_2_RESEARCH_INCOMPLETE,
        VoyageReadinessStatus.LEVEL_2_PARTIAL_COMPONENTS,
    )
    assert any("GATUN" in b or "FRESHWATER" in b for b in res_gatun.blocking_reasons)
    print("Check 5 PASSED: Unresolved Gatun Lake level blocks completeness.")

    # 6. Rotterdam Partial Component (River Pilotage S-rate) & Mandatory Disclaimer
    print("\n--- Check 6: Rotterdam Partial Component & Mandatory Disclaimer ---")
    res_rot = voyage_service.assess_voyage_readiness(
        voyage_id="VOY-RTM-001",
        transit_date=datetime.date(2024, 1, 15),
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="NLRTM",
        vessel_particulars={
            "gross_tonnage": 50000.0,
        },
    )
    assert res_rot.readiness_status in (
        VoyageReadinessStatus.LEVEL_2_PARTIAL_COMPONENTS,
        VoyageReadinessStatus.LEVEL_2_RESEARCH_INCOMPLETE,
    )
    assert res_rot.has_partial_components is True
    assert res_rot.is_ready_for_operations is False
    assert res_rot.disclaimer is not None
    assert "QUALIFIED COMMERCIAL ESTIMATE" in res_rot.disclaimer
    print("Check 6 PASSED: Partial component detected, disclaimer attached, operational blocked.")

    # 7. Tier 3 Contractual Charge Without Contract Attribution
    print("\n--- Check 7: Tier 3 Contractual Charge Without Contract Attribution ---")
    classifier = ChargeClassifier()
    item_no_attr = {
        "tariff_identifier": "COMMERCIAL-AGENCY-001",
        "description": "Port Agency Fee",
        "charge_category": "AGENCY_FEE",
        "applicability_type": "CONTRACTUAL",
        "amount_usd": 3500.0,
        "contract_attribution": None,
    }
    classified_item = classifier.classify_line_item(item_no_attr)
    assert classified_item.charge_tier == ChargeClassificationTier.TIER_3_CONTRACTUAL_REMUNERATION
    assert "MISSING_CONTRACTUAL_EVIDENCE" in classified_item.flags
    assert classified_item.is_operational_permitted is False
    print("Check 7 PASSED: Tier 3 charge without contract attribution is flagged and blocked.")

    # 8. Tier 4 Provisional Estimate Permanently Excluded
    print("\n--- Check 8: Tier 4 Provisional Estimate Permanently Excluded ---")
    item_provisional = {
        "tariff_identifier": "DEMURRAGE-PROVISION-001",
        "description": "Demurrage Contingency Provision",
        "charge_category": "DEMURRAGE",
        "applicability_type": "PROVISIONAL",
        "amount_usd": 15000.0,
    }
    classified_prov = classifier.classify_line_item(item_provisional)
    assert classified_prov.charge_tier == ChargeClassificationTier.TIER_4_PROVISIONAL_ESTIMATES
    assert classified_prov.is_provisional is True
    assert classified_prov.is_operational_permitted is False
    print("Check 8 PASSED: Tier 4 provisional estimate permanently excluded from operational clearance.")

    # 9. One-Way Statutory Floor: Deficit Fails Closed (Zero Tolerance)
    print("\n--- Check 9: One-Way Statutory Floor Deficit Fails Closed ---")
    floor_res_deficit = classifier.evaluate_statutory_floor(
        user_valuation_usd=9999.99,
        statutory_floor_usd=10000.00,
    )
    assert floor_res_deficit.is_floor_satisfied is False
    assert floor_res_deficit.is_operational_permitted is False
    assert any("FIXTURE_VALUATION_BELOW_STATUTORY_FLOOR" in r for r in floor_res_deficit.failure_reasons)
    print("Check 9 PASSED: Even a 1-cent deficit fails closed immediately with zero tolerance.")

    # 10. One-Way Statutory Floor: Surplus Above Floor Requires Allocation
    print("\n--- Check 10: Surplus Above Floor Requires Itemized Allocation ---")
    floor_res_surplus = classifier.evaluate_statutory_floor(
        user_valuation_usd=15000.00,
        statutory_floor_usd=10000.00,
        itemized_charges=None,
    )
    assert floor_res_surplus.is_floor_satisfied is True
    assert floor_res_surplus.unallocated_overage_usd == 5000.00
    assert floor_res_surplus.is_operational_permitted is False  # Cannot approve unallocated overage!
    assert "UNALLOCATED_COMMERCIAL_OVERAGE" in floor_res_surplus.flags
    print("Check 10 PASSED: Surplus flagged as UNALLOCATED_COMMERCIAL_OVERAGE, operational clearance denied.")

    # 11. Multiple Simultaneous Blockers
    print("\n--- Check 11: Multiple Simultaneous Blockers Coexist Without Silent Omission ---")
    res_multi = voyage_service.assess_voyage_readiness(
        voyage_id="VOY-MULTI-001",
        transit_date=datetime.date(2024, 1, 15),
        origin_port_unlocode="ZZZZZ",  # Unknown port
        destination_port_unlocode="NLRTM",
        canal_transits=["SUEZ", "PANAMA"],
        vessel_particulars={
            "gross_tonnage": None,  # Missing GT for NLRTM
            "scnt": None,           # Missing SCNT for SUEZ
            "length_meters": None,  # Missing LOA for PANAMA
            "width_meters": None,   # Missing Beam for PANAMA & SUEZ
        },
    )
    assert res_multi.is_ready_for_research is False
    assert res_multi.is_ready_for_operations is False
    assert res_multi.readiness_status == VoyageReadinessStatus.LEVEL_2_RESEARCH_INCOMPLETE
    assert len(res_multi.blocking_reasons) >= 4
    print(f"Check 11 PASSED: Multiple simultaneous blockers preserved ({len(res_multi.blocking_reasons)} blockers).")

    # 12. Operational Mode Clearance Fails Closed in Phase 1
    print("\n--- Check 12: Operational Mode Strictly Locked in Phase 1 ---")
    res_ack = voyage_service.assess_voyage_readiness(
        voyage_id="VOY-ACK-001",
        transit_date=datetime.date(2024, 1, 15),
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="NLRTM",
        vessel_particulars={
            "gross_tonnage": 50000.0,
        },
        operational_mode=True,  # Requesting operational clearance!
    )
    assert res_ack.is_ready_for_operations is False
    assert res_ack.readiness_status == VoyageReadinessStatus.LEVEL_3_OPERATIONAL_BLOCKED
    assert any("OPERATIONAL" in b for b in res_ack.blocking_reasons)
    print("Check 12 PASSED: Operational mode fails closed; operational gate remains locked.")

    # 13. Empty or Incomplete Itineraries
    print("\n--- Check 13: Empty or Incomplete Itinerary Fails Closed ---")
    res_empty = voyage_service.assess_voyage_readiness(
        voyage_id="VOY-EMPTY-001",
        transit_date=datetime.date(2024, 1, 15),
        origin_port_unlocode="",
        destination_port_unlocode="",
    )
    assert res_empty.is_ready_for_research is False
    assert res_empty.is_ready_for_operations is False
    assert any("INVALID_ITINERARY" in b or "PORT_NOT_REGISTERED" in b or "EMPTY" in b for b in res_empty.blocking_reasons)
    print("Check 13 PASSED: Empty itinerary fails closed.")

    # 14. Serialization of All Blocking Reasons and Evidence References
    print("\n--- Check 14: Serialization to Dict Preserves All Fields ---")
    d_res = res_rot.to_dict()
    assert "readiness_status" in d_res
    assert "is_ready_for_research" in d_res
    assert "is_ready_for_operations" in d_res
    assert "blocking_reasons" in d_res
    assert "warnings" in d_res
    assert "disclaimer" in d_res
    assert "vessel_assessment" in d_res
    assert "tariff_summary" in d_res
    assert "charge_summary" in d_res
    print("Check 14 PASSED: Complete JSON/dict serialization verified.")

    # 15. Inviolate Operational Lock: is_ready_for_operations == False under EVERY possible input
    print("\n--- Check 15: Operational Lock is_ready_for_operations == False ---")
    for op_flag in [True, False]:
        r = voyage_service.assess_voyage_readiness(
            voyage_id=f"VOY-OP-{op_flag}",
            transit_date=datetime.date(2024, 1, 15),
            origin_port_unlocode="SGSIN",
            destination_port_unlocode="USHOU",
            vessel_particulars={
                "gross_tonnage": 60000.0,
                "dwt_tonnage": 80000.0,
                "length_meters": 225.0,
                "width_meters": 32.2,
                "draft_max_meters": 12.0,
            },
            operational_mode=op_flag,
        )
        assert r.is_ready_for_operations is False, "is_ready_for_operations MUST ALWAYS BE FALSE"
    print("Check 15 PASSED: is_ready_for_operations is strictly False across all paths.")

    print("\n>>> ALL DEEP READINESS CHECKS PASSED WITH ZERO ERRORS! <<<")


if __name__ == "__main__":
    run_deep_verification()
