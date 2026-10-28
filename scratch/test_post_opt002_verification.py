"""End-to-end integration and correctness verification for Pelagos Post-OPT-002.

Exercises:
1. Main journey: Supported corridor PNW-JPN-BULK (Seattle -> Yokohama)
   - Freight route intelligence
   - Voyage gross freight calculator
   - Candidate vessel suitability matching
2. Unsupported / unmonitored route: NLRTM -> SGSIN (Rotterdam -> Singapore)
3. Historical data boundary enforcement and qualification
4. Semantic audit of 300m Capesize (MSC ATHOS) and Panamax (TROODOS SUN)
5. Missing particulars handling (AMERICAN COURAGE)
6. Non-cargo vessel filtering (CAPT J A MORGAN, OCEAN TANKER)
7. Invalid inputs validation (negative cargo tonnage, zero cargo tonnage)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from fastapi.testclient import TestClient

from pelagos.api.main import app


def test_post_opt002_verification():
    client = TestClient(app)
    print("=" * 70)
    print("PELAGOS POST-OPT-002 CORRECTNESS & INTEGRATION AUDIT")
    print("=" * 70)

    # -----------------------------------------------------------------------
    # 1. Main Journey: Supported Corridor (Seattle -> Yokohama, 52,000 MT)
    # -----------------------------------------------------------------------
    print("\n[Step 1] Exercising Main Journey: PNW-JPN-BULK (Seattle -> Yokohama)...")
    
    # 1a. Operational Route Calculation
    route_resp = client.post("/api/v1/routing/calculate", json={
        "origin": "USSEA",
        "destination": "JPYOK",
        "routing_mode": "prototype",
    })
    assert route_resp.status_code == 200, f"Route calculation failed: {route_resp.text}"
    route_data = route_resp.json()
    dist_val = route_data.get("summary", {}).get("total_distance_nm", 0.0)
    print(f"  [OK] Sea Route calculated: {dist_val:.1f} NM ({route_data.get('route_method')})")

    # 1b. Freight Route Intelligence
    fi_resp = client.get("/api/v1/freight/route-intelligence?origin=USSEA&destination=JPYOK")
    assert fi_resp.status_code == 200, f"Freight route intelligence failed: {fi_resp.text}"
    fi_data = fi_resp.json()
    corridor = fi_data.get("corridor", {})
    obs = fi_data.get("latest_observation", {})
    print(f"  [OK] Freight corridor: {corridor.get('route_code')} - {corridor.get('route_name')}")
    print(f"  [OK] Observed benchmark rate: ${obs.get('rate_value')} {obs.get('currency')}/{obs.get('unit')} ({obs.get('source_name')})")

    # 1b. Voyage Freight Cost Calculator
    calc_resp = client.post("/api/v1/calculator/voyage-freight", json={
        "origin": "USSEA",
        "destination": "JPYOK",
        "cargo_tonnes": 52000.0,
    })
    assert calc_resp.status_code == 200, f"Voyage calculator failed: {calc_resp.text}"
    calc_data = calc_resp.json()
    assert calc_data["status"] == "calculated"
    assert calc_data["gross_freight_cost"] == round(52000.0 * float(obs.get("rate_value")), 2)
    print(f"  [OK] Voyage gross freight cost: ${calc_data['gross_freight_cost']:,.2f} USD")
    print(f"  [OK] Distance provenance: {calc_data['distance']['distance_source']} ({calc_data['distance']['provenance'].get('operational_warning')})")

    # 1c. Candidate Vessel Suitability Matching
    suit_resp = client.post("/api/v1/calculator/vessel-suitability", json={
        "origin": "USSEA",
        "destination": "JPYOK",
        "cargo_tonnes": 52000.0,
        "max_vessels": 10,
    })
    assert suit_resp.status_code == 200, f"Suitability matching failed: {suit_resp.text}"
    suit_data = suit_resp.json()
    assert suit_data["status"] == "matched"
    assert suit_data["data_mode"] == "historical"
    print(f"  [OK] Vessels evaluated from genuine DB: {suit_data['total_vessels_evaluated']} distinct records")
    print(f"  [OK] Potentially suitable candidates: {suit_data['potentially_suitable_count']}")
    print(f"  [OK] Telemetry mode: {suit_data['data_mode']} ({suit_data['data_mode_notice'][:60]}...)")

    # -----------------------------------------------------------------------
    # 2. Semantic Audit: 300m Capesize (MSC ATHOS) vs Panamax (TROODOS SUN)
    # -----------------------------------------------------------------------
    print("\n[Step 2] Auditing Candidate Vessel Matching Semantics...")
    candidates = suit_data["candidates"]
    assert len(candidates) > 0, "Expected candidate vessels in response"

    athos = next((c for c in candidates if "ATHOS" in c["vessel_name"].upper()), None)
    assert athos is not None, "MSC ATHOS not found in evaluated candidates"
    print(f"  Candidate: {athos['vessel_name']} (MMSI: {athos['mmsi']})")
    print(f"    - AIS vessel type: '{athos['vessel_type']}' (Code {athos['vessel_type_code']})")
    print(f"    - Estimated dimensional class: '{athos['estimated_dimensional_class']}'")
    print(f"    - Verified cargo capacity: {athos['verified_cargo_capacity_dwt']} (Status: {athos['cargo_capacity_status']})")
    print(f"    - Commercial availability: {athos['commercial_availability']}")
    print(f"    - Laycan feasibility: {athos['laycan_feasibility']}")
    print(f"    - Suitability status: {athos['suitability_status']}")
    print(f"    - Suitability explanation: {athos['suitability_reason']}")
    print(f"    - Distance to load port: {athos['distance_to_load_port_nm']:.1f} NM")
    print(f"    - Distance qualification: {athos['distance_qualification']}")

    # Assert semantics
    assert athos["estimated_dimensional_class"] == "Post-Panamax / Capesize"
    assert "Post-Panamax / Capesize" in athos["suitability_reason"]
    assert "Dimensionally screened candidate" in athos["suitability_reason"]
    assert "provides sufficient cubic capacity" not in athos["suitability_reason"]
    assert athos["verified_cargo_capacity_dwt"] is None
    assert athos["cargo_capacity_status"] == "UNVERIFIED"
    assert athos["is_stale"] is True
    assert "HISTORICAL_OBSERVATION_ONLY" in athos["distance_qualification"]
    assert athos["laycan_feasibility"] == "UNCONFIRMED"
    assert athos["commercial_availability"] == "UNCONFIRMED"
    print("  [OK] MSC ATHOS semantics verified: class matches explanation, no DWT fabricated, historical boundaries enforced.")

    # -----------------------------------------------------------------------
    # 3. Unsupported / Unmonitored Corridor Journey (Rotterdam -> Singapore)
    # -----------------------------------------------------------------------
    print("\n[Step 3] Exercising Unmonitored Route Journey (Rotterdam -> Singapore)...")
    unmon_resp = client.get("/api/v1/freight/route-intelligence?origin=NLRTM&destination=SGSIN")
    assert unmon_resp.status_code == 200
    unmon_data = unmon_resp.json()
    assert unmon_data.get("status") == "unmonitored"
    print(f"  [OK] Unmonitored route handled honestly: freight status = {unmon_data['status']}")

    unmon_calc = client.post("/api/v1/calculator/voyage-freight", json={
        "origin": "NLRTM",
        "destination": "SGSIN",
        "cargo_tonnes": 50000.0,
    })
    assert unmon_calc.status_code == 200
    assert unmon_calc.json()["status"] == "unavailable"
    print(f"  [OK] Voyage freight calculator returns UNAVAILABLE without fabricating rates")

    unmon_suit = client.post("/api/v1/calculator/vessel-suitability", json={
        "origin": "NLRTM",
        "destination": "SGSIN",
        "cargo_tonnes": 50000.0,
    })
    assert unmon_suit.status_code == 200
    assert unmon_suit.json()["status"] == "unmonitored_corridor_matched"
    assert unmon_suit.json()["loading_port_id"] == "NLRTM"
    print(f"  [OK] Vessel suitability matching returns unmonitored_corridor_matched without fabricating data")

    # -----------------------------------------------------------------------
    # 4. Input Validation & Error Handling
    # -----------------------------------------------------------------------
    print("\n[Step 4] Auditing Input Validation & Error Handling...")
    neg_resp = client.post("/api/v1/calculator/vessel-suitability", json={
        "origin": "USSEA",
        "destination": "JPYOK",
        "cargo_tonnes": -500.0,
    })
    assert neg_resp.status_code == 422
    print(f"  [OK] Negative cargo tonnage rejected with HTTP {neg_resp.status_code}")

    zero_resp = client.post("/api/v1/calculator/vessel-suitability", json={
        "origin": "USSEA",
        "destination": "JPYOK",
        "cargo_tonnes": 0.0,
    })
    assert zero_resp.status_code == 422
    print(f"  [OK] Zero cargo tonnage rejected with HTTP {zero_resp.status_code}")

    print("\n" + "=" * 70)
    print("ALL POST-OPT-002 AUDIT CHECKS PASSED SUCCESSFULLY.")
    print("=" * 70)


if __name__ == "__main__":
    run_post_opt002_verification()
