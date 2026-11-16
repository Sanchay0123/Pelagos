"""
Verification Script for Section 5: Statutory Floor Applicability (VERIFY-F143-PHASE1-RECON-001)
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

import datetime
from pelagos.readiness.charge_classifier import (
    ChargeClassifier,
    ChargeClassificationTier,
)
from pelagos.registry.port_disbursement_registry import PortDisbursementRegistry
from pelagos.registry.canal_tariff_registry import CanalTariffRegistry
from pelagos.currency import CurrencyConversionService

def run_statutory_floor_tests():
    print("=" * 70)
    print("RUNNING STATUTORY FLOOR APPLICABILITY & CONCESSION VERIFICATION")
    print("=" * 70)

    # 1. Singapore Standard Port Dues vs Bunkering Concession
    port_reg = PortDisbursementRegistry()
    
    # Vessel 60,000 GT cargo stay 24h
    res_sgsin_cargo = port_reg.calculate_port_disbursement(
        un_locode="SGSIN",
        vessel_gt=60000.0,
        vessel_dwt=75000.0,
        loa_m=225.0,
        beam_m=32.2,
        draft_m=12.5,
        purpose_of_call="CARGO_OPERATIONS",
        stay_duration_hours=24.0,
        transit_date=datetime.date(2024, 1, 15),
    )
    cargo_dues = [it for it in res_sgsin_cargo.items if it.tariff_identifier == "SGSIN-PORT-DUES-CARGO-2023"]
    assert len(cargo_dues) == 1, "Cargo dues line item expected"
    print(f"[PASS] SGSIN Cargo Call (24h): Applied {cargo_dues[0].tariff_identifier} = SGD {cargo_dues[0].amount_local:,.2f}")

    # Vessel 60,000 GT bunkering stay 24h (concession eligible)
    res_sgsin_bunker_24h = port_reg.calculate_port_disbursement(
        un_locode="SGSIN",
        vessel_gt=60000.0,
        vessel_dwt=75000.0,
        loa_m=225.0,
        beam_m=32.2,
        draft_m=12.5,
        purpose_of_call="BUNKERING_ONLY",
        stay_duration_hours=24.0,
        transit_date=datetime.date(2024, 1, 15),
    )
    bunker_dues = [it for it in res_sgsin_bunker_24h.items if it.tariff_identifier == "SGSIN-PORT-DUES-BUNKER-2023"]
    assert len(bunker_dues) == 1, "Bunker concession line item expected"
    assert bunker_dues[0].amount_local < cargo_dues[0].amount_local, "Bunkering concession must be lower than cargo dues"
    print(f"[PASS] SGSIN Bunkering Call (<=24h): Applied {bunker_dues[0].tariff_identifier} = SGD {bunker_dues[0].amount_local:,.2f} (concession valid)")

    # Vessel 60,000 GT bunkering stay 36h (concession disqualified by stay duration)
    res_sgsin_bunker_36h = port_reg.calculate_port_disbursement(
        un_locode="SGSIN",
        vessel_gt=60000.0,
        vessel_dwt=75000.0,
        loa_m=225.0,
        beam_m=32.2,
        draft_m=12.5,
        purpose_of_call="BUNKERING_ONLY",
        stay_duration_hours=36.0,
        transit_date=datetime.date(2024, 1, 15),
    )
    cargo_dues_36h = [it for it in res_sgsin_bunker_36h.items if it.tariff_identifier == "SGSIN-PORT-DUES-CARGO-2023"]
    bunker_dues_36h = [it for it in res_sgsin_bunker_36h.items if it.tariff_identifier == "SGSIN-PORT-DUES-BUNKER-2023"]
    assert len(cargo_dues_36h) == 1, "Cargo dues must apply when stay > 24h"
    assert len(bunker_dues_36h) == 0, "Bunker concession must NOT apply when stay > 24h"
    print(f"[PASS] SGSIN Bunkering Call (>24h stay): Disqualified from concession, reverted to {cargo_dues_36h[0].tariff_identifier} (stay_days={2})")

    # 2. Suez Canal Laden vs Ballast Tariff Treatment
    canal_reg = CanalTariffRegistry()
    toll_laden = canal_reg.calculate_suez_toll(
        scnt=92000.0,
        draft_m=18.0,
        beam_m=45.0,
        loading_condition="LADEN",
        transit_date=datetime.date(2024, 1, 15),
    )
    toll_ballast = canal_reg.calculate_suez_toll(
        scnt=92000.0,
        draft_m=18.0,
        beam_m=45.0,
        loading_condition="BALLAST",
        transit_date=datetime.date(2024, 1, 15),
    )
    assert toll_ballast.base_dues_sdr < toll_laden.base_dues_sdr, "Ballast base toll must be lower than laden base toll"
    assert toll_laden.escort_tug_requirement["required_tugs_count"] == 2, "SCNT 92,000 laden requires 2 escort tugs"
    assert toll_ballast.escort_tug_requirement["required_tugs_count"] == 0, "SCNT 92,000 ballast (beam 45m = 147ft <= 218ft) requires 0 escort tugs"
    print(f"[PASS] Suez Laden vs Ballast: Laden = SDR {toll_laden.base_dues_sdr:,.2f} (2 tugs), Ballast = SDR {toll_ballast.base_dues_sdr:,.2f} (0 tugs)")

    # 3. Charge Classifier: Sub-Floor Rejection & Monetary Precision
    classifier = ChargeClassifier()
    
    # Exact sub-floor rejection
    res_sub = classifier.evaluate_statutory_floor(user_valuation_usd=99999.99, statutory_floor_usd=100000.00)
    assert res_sub.is_floor_satisfied is False
    assert res_sub.status == "REJECTED_BELOW_STATUTORY_FLOOR"
    assert "FIXTURE_VALUATION_BELOW_STATUTORY_FLOOR" in res_sub.flags
    print("[PASS] Sub-floor valuation ($0.01 deficit): Strictly REJECTED fail-closed")

    # Floating point precision check: 450000.10 - 450000.10 == 0.0
    res_exact = classifier.evaluate_statutory_floor(user_valuation_usd=450000.10, statutory_floor_usd=450000.10)
    assert res_exact.is_floor_satisfied is True
    assert res_exact.variance_usd == 0.0
    assert res_exact.status == "EXACT_STATUTORY_MATCH"
    print("[PASS] Floating-point precision matching: EXACT_STATUTORY_MATCH")

    # Unallocated surplus overage quarantine
    res_overage = classifier.evaluate_statutory_floor(user_valuation_usd=125000.00, statutory_floor_usd=100000.00)
    assert res_overage.is_floor_satisfied is True
    assert res_overage.unallocated_overage_usd == 25000.00
    assert res_overage.is_operational_permitted is False  # Cannot clear operations without allocation!
    assert "UNALLOCATED_COMMERCIAL_OVERAGE" in res_overage.flags
    print("[PASS] Surplus overage ($25,000 unallocated): Quarantined and barred from operational clearance")

    # Surplus itemized and reconciled
    res_itemized = classifier.evaluate_statutory_floor(
        user_valuation_usd=125000.00,
        statutory_floor_usd=100000.00,
        itemized_charges=[
            {"charge_category": "PILOTAGE", "amount_usd": 15000.00},
            {"charge_category": "AGENCY_FEE", "amount_usd": 10000.00, "contract_id": "AGY-001"}
        ]
    )
    assert res_itemized.is_floor_satisfied is True
    assert res_itemized.unallocated_overage_usd == 0.0
    assert res_itemized.status == "SURPLUS_ITEMIZED_RECONCILED"
    assert res_itemized.is_operational_permitted is True
    print("[PASS] Surplus itemized ($25,000 reconciled): SURPLUS_ITEMIZED_RECONCILED")

    print("=" * 70)
    print("ALL STATUTORY FLOOR APPLICABILITY TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    run_statutory_floor_tests()
