import json
import sys
from pathlib import Path

def validate():
    print("=== STARTING STAGE F1.4.2.3 CLOSURE VALIDATION ===")
    
    # 1. Paths
    closure_json_path = Path("backend/pelagos/data/f1_4_2_3_discovery_closure_summary.json")
    inventory_json_path = Path("backend/pelagos/data/f1_4_2_3_disbursement_source_inventory.json")
    doc_path = Path("docs/F1_4_2_3_DISCOVERY_CLOSURE.md")
    
    assert closure_json_path.exists(), f"Missing {closure_json_path}"
    assert inventory_json_path.exists(), f"Missing {inventory_json_path}"
    assert doc_path.exists(), f"Missing {doc_path}"
    
    # 2. JSON Parser Validation
    print("Parsing closure summary JSON...")
    with open(closure_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print("--> JSON successfully loaded via standard parser.")
    
    with open(inventory_json_path, "r", encoding="utf-8") as f:
        inv_data = json.load(f)
    inventory_sources = inv_data.get("sources", [])
    inventory_source_ids = {s["source_id"] for s in inventory_sources}
    print(f"--> Inventory loaded with {len(inventory_source_ids)} source records.")
    
    # 3. Expected Top-Level Keys and Data Types
    expected_keys = {
        "$schema": str,
        "version": str,
        "stage": str,
        "title": str,
        "created_at": str,
        "auditor": str,
        "baseline_commit": str,
        "closure_metadata": dict,
        "source_metrics_reconciliation": dict,
        "geographic_taxonomy_reconciliation": dict,
        "panama_canal_verified_tariff": dict,
        "suez_canal_verified_tariff": dict,
        "seed_vessels_reconciliation_audit": list,
        "pc_ums_and_scnt_tonnage_audit": dict,
        "currency_conversion_architecture": dict,
        "readiness_decision": dict
    }
    
    for key, expected_type in expected_keys.items():
        assert key in data, f"Missing key: {key}"
        assert isinstance(data[key], expected_type), f"Key {key} expected {expected_type}, got {type(data[key])}"
    print(f"--> All {len(expected_keys)} expected top-level keys and data types validated.")
    
    # 4. Source ID Existence Check
    metrics = data["source_metrics_reconciliation"]
    total_records = metrics["total_records"]["record_ids"]
    legal_records = metrics["official_primary_legal_sources"]["record_ids"]
    operator_records = metrics["commercial_operator_sources"]["record_ids"]
    secondary_records = metrics["secondary_institutional_sources"]["record_ids"]
    tariff_records = metrics["publicly_accessible_tariff_rates_or_formulas_identified"]["record_ids"]
    
    assert len(total_records) == 31, f"Expected 31 total records, got {len(total_records)}"
    assert len(legal_records) == 26, f"Expected 26 legal records, got {len(legal_records)}"
    assert len(operator_records) == 3, f"Expected 3 operator records, got {len(operator_records)}"
    assert len(secondary_records) == 2, f"Expected 2 secondary records, got {len(secondary_records)}"
    assert len(tariff_records) == 27, f"Expected 27 tariff records, got {len(tariff_records)}"
    
    for sid in total_records:
        assert sid in inventory_source_ids, f"Referenced source ID {sid} not in inventory!"
    for sid in legal_records:
        assert sid in inventory_source_ids, f"Legal source ID {sid} not in inventory!"
    for sid in operator_records:
        assert sid in inventory_source_ids, f"Operator source ID {sid} not in inventory!"
    for sid in secondary_records:
        assert sid in inventory_source_ids, f"Secondary source ID {sid} not in inventory!"
    for sid in tariff_records:
        assert sid in inventory_source_ids, f"Tariff source ID {sid} not in inventory!"
    print("--> All referenced source IDs exist in original source inventory.")
    
    # 5. Geographic Counts Check
    geo = data["geographic_taxonomy_reconciliation"]
    states = geo["sovereign_coastal_states"]
    clusters = geo["selected_operational_maritime_jurisdictions"]
    macro = geo["macro_geographic_regions"]
    standards = geo["international_benchmark_organizations"]
    
    assert states["count"] == 17, f"Expected 17 sovereign states, got {states['count']}"
    assert len(states["members"]) == 17, f"Expected 17 member states, got {len(states['members'])}"
    assert clusters["count"] == 13, f"Expected 13 operational clusters, got {clusters['count']}"
    assert len(clusters["members"]) == 13, f"Expected 13 cluster members, got {len(clusters['members'])}"
    assert macro["count"] == 11, f"Expected 11 macro regions, got {macro['count']}"
    assert len(macro["members"]) == 11, f"Expected 11 macro members, got {len(macro['members'])}"
    assert standards["count"] == 2, f"Expected 2 standards, got {standards['count']}"
    assert len(standards["members"]) == 2, f"Expected 2 standards members, got {len(standards['members'])}"
    print("--> Geographic taxonomy counts and enumeration strictly match (17 states, 13 clusters, 11 macro, 2 standards).")
    
    # 6. Seed Vessels Audit Check
    vessels = data["seed_vessels_reconciliation_audit"]
    assert len(vessels) == 6, f"Expected 6 vessels, got {len(vessels)}"
    vessel_imos = {v["imo_number"] for v in vessels}
    expected_imos = {"9713430", "9766944", "9753284", "9441295", "9406087", "9591301"}
    assert vessel_imos == expected_imos, f"IMO mismatch: {vessel_imos} != {expected_imos}"
    
    # Check specific vessels
    navios = next(v for v in vessels if v["imo_number"] == "9713430")
    assert navios["verified_summer_dwt_mt"] == 77095.0
    assert navios["classification_society"] == "ClassNK"
    assert navios["reconciliation_verdict"] == "UPHELD_DIRECTLY_VERIFIED"
    
    iris = next(v for v in vessels if v["imo_number"] == "9766944")
    assert iris["verified_summer_dwt_mt"] == 82103.0
    assert iris["classification_society"] == "ClassNK"
    assert iris["reconciliation_verdict"] == "UPHELD_DIRECTLY_VERIFIED"
    
    berge = next(v for v in vessels if v["imo_number"] == "9753284")
    assert berge["verified_summer_dwt_mt"] == 38907.0
    assert "DNV" in berge["classification_society"]
    assert berge["reconciliation_verdict"] == "UPHELD_DIRECTLY_VERIFIED"
    
    egret = next(v for v in vessels if v["imo_number"] == "9441295")
    assert egret["reconciliation_verdict"] == "UPHELD_SOURCE_REPORTED"
    
    hadeel = next(v for v in vessels if v["imo_number"] == "9406087")
    assert hadeel["reconciliation_verdict"] == "PRESERVED_SOURCE_REPORTED_WITH_UNRESOLVED_CONFLICT"
    
    unity = next(v for v in vessels if v["imo_number"] == "9591301")
    assert unity["reconciliation_verdict"] == "PRESERVED_SOURCE_REPORTED_WITH_UNRESOLVED_CONFLICT"
    print("--> All 6 seed vessels verified and reconciliation statuses confirmed.")
    
    # 7. Panama & Suez Tariff Validation
    panama = data["panama_canal_verified_tariff"]
    assert panama["source_id"] == "SRC-DISB-PANAMA-ACP-001"
    fixed_fees = panama["item_1010_fixed_fees_per_transit"]["tiers"]
    assert len(fixed_fees) == 3
    assert fixed_fees[0]["fixed_fee_usd"] == 60000.0
    assert fixed_fees[1]["fixed_fee_usd"] == 100000.0
    assert fixed_fees[2]["fixed_fee_usd"] == 300000.0
    
    capacity_rates = panama["item_1010_dry_bulk_capacity_rates"]["rates_schedule"]
    assert len(capacity_rates) == 3
    assert capacity_rates[0]["rate_2024_usd_per_dwt"] == 1.25
    assert capacity_rates[0]["rate_2025_usd_per_dwt"] == 1.65
    assert capacity_rates[1]["rate_2024_usd_per_dwt"] == 1.15
    assert capacity_rates[1]["rate_2025_usd_per_dwt"] == 1.50
    assert capacity_rates[2]["rate_2024_usd_per_dwt"] == 0.50
    assert capacity_rates[2]["rate_2025_usd_per_dwt"] == 0.80
    assert panama["ballast_factor_substantiation"]["multiplier"] == 0.85
    
    suez = data["suez_canal_verified_tariff"]
    assert suez["source_id"] == "SRC-DISB-SUEZ-SCA-001"
    sdr_tiers = suez["seven_sdr_brackets_schedule"]["tiers"]
    assert len(sdr_tiers) == 7
    assert sdr_tiers[0]["laden_rate_sdr"] == 10.13
    assert sdr_tiers[1]["laden_rate_sdr"] == 7.74
    assert sdr_tiers[2]["laden_rate_sdr"] == 6.12
    assert sdr_tiers[3]["laden_rate_sdr"] == 2.24
    assert sdr_tiers[4]["laden_rate_sdr"] == 1.97
    assert sdr_tiers[5]["laden_rate_sdr"] == 1.85
    assert sdr_tiers[6]["laden_rate_sdr"] == 1.77
    assert len(suez["escort_tug_thresholds"]["threshold_rules"]) == 6
    print("--> Panama and Suez verified tariff structures and numerical schedules validated.")
    
    # 8. Readiness Verdicts Validation
    readiness = data["readiness_decision"]
    assert readiness["panama_source_evidence"] == "READY_FOR_DETERMINISTIC_TARIFF_REGISTRY"
    assert readiness["suez_source_evidence"] == "READY_FOR_DETERMINISTIC_TARIFF_REGISTRY"
    assert readiness["vessel_particulars"] == "CONDITIONALLY_READY"
    assert readiness["pc_ums_and_scnt_tonnages"] == "BLOCKED_FOR_OPERATIONAL_CANAL_CALCULATIONS"
    assert readiness["currency_conversion"] == "CONDITIONALLY_READY"
    assert readiness["overall_f1_4_2_3a_readiness_verdict"] == "CONDITIONALLY_READY"
    print("--> Readiness decision fields and separate verdicts validated.")
    
    # 9. Cross-Check with Markdown Document
    with open(doc_path, "r", encoding="utf-8") as f:
        doc_text = f.read()
    
    assert "77,095" in doc_text
    assert "82,103" in doc_text
    assert "38,907" in doc_text
    assert "1010.FR03" in doc_text
    assert "1010.FS01" in doc_text
    assert "1010.FN02" in doc_text
    assert "1010.DR01" in doc_text
    assert "1010.DS01" in doc_text
    assert "1010.DN01" in doc_text
    assert "Circular No. 7/2023" in doc_text
    assert "Article 58" in doc_text
    assert "CONDITIONALLY_READY" in doc_text
    print("--> Markdown report cross-check passed.")
    
    print("=== VALIDATION COMPLETED SUCCESSFULLY (EXIT 0) ===")
    return 0

if __name__ == "__main__":
    sys.exit(validate())

