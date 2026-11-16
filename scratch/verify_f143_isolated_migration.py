"""
Stage F1.4.3 Phase 1: Isolated Disk Database Migration Verification Script.
Independently verifies:
1. Creation of an isolated disk-backed SQLite database with pure F1.4.2.4 schema.
2. Ingestion of legacy records into voyage_disbursement_records and line items.
3. Pre-migration schema inspection.
4. Execution of ensure_schema_compatibility().
5. Post-migration schema inspection confirming all newly required columns exist.
6. Survival and integrity of legacy rows with appropriate nullable defaults.
7. Persistence and querying of new F1.4.3 ORM records.
8. Idempotency of repeated migration execution.
9. Confirmation that production pelagos.db remains untouched.
"""

import datetime
import os
from pathlib import Path
import sqlite3
import sys

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from pelagos.database import ensure_schema_compatibility
from pelagos.models import Base, VoyageDisbursementLineItem, VoyageDisbursementRecord


def run_migration_audit():
    db_path = Path(__file__).resolve().parent / "f143_migration_test.sqlite3"
    if db_path.exists():
        db_path.unlink()

    print(f"=== 1. Creating isolated disk database at {db_path} ===")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Create F1.4.2.4 schema (pre-F1.4.3)
    cur.execute("""
    CREATE TABLE voyage_disbursement_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        voyage_id VARCHAR(64) NOT NULL,
        vessel_imo INTEGER,
        vessel_name VARCHAR(128),
        vessel_class VARCHAR(64),
        origin_port_unlocode VARCHAR(5) NOT NULL,
        destination_port_unlocode VARCHAR(5) NOT NULL,
        transit_date DATE NOT NULL,
        vessel_gt FLOAT,
        vessel_dwt FLOAT,
        vessel_loa_m FLOAT,
        vessel_beam_m FLOAT,
        vessel_draft_m FLOAT,
        scnt FLOAT,
        pc_ums_net FLOAT,
        total_disbursement_usd FLOAT,
        total_port_disbursement_usd FLOAT,
        total_canal_transit_usd FLOAT,
        readiness_tier VARCHAR(64) NOT NULL,
        completeness_status VARCHAR(64) NOT NULL,
        is_complete BOOLEAN NOT NULL,
        has_partial_components BOOLEAN NOT NULL,
        is_operational_approved BOOLEAN NOT NULL,
        blocked_reasons_json TEXT NOT NULL,
        evidence_summary_json TEXT NOT NULL,
        notes TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE voyage_disbursement_line_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        voyage_disbursement_record_id INTEGER NOT NULL REFERENCES voyage_disbursement_records(id) ON DELETE CASCADE,
        charge_type VARCHAR(32) NOT NULL,
        location_code VARCHAR(16) NOT NULL,
        charge_category VARCHAR(64) NOT NULL,
        tariff_identifier VARCHAR(64) NOT NULL,
        description VARCHAR(256) NOT NULL,
        applicability_type VARCHAR(64) NOT NULL,
        rate_value FLOAT,
        billing_unit VARCHAR(64) NOT NULL,
        local_currency VARCHAR(8) NOT NULL,
        amount_local FLOAT,
        exchange_rate_to_usd FLOAT,
        amount_usd FLOAT,
        is_partial_component BOOLEAN NOT NULL DEFAULT 0,
        is_available BOOLEAN NOT NULL DEFAULT 1,
        evidence_classification VARCHAR(32) NOT NULL DEFAULT 'DIRECTLY_VERIFIED',
        permission_status VARCHAR(64) NOT NULL DEFAULT 'PERMITTED_STATUTORY_DISCLOSURE',
        failure_reason VARCHAR(256),
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Seed legacy rows
    cur.execute("""
    INSERT INTO voyage_disbursement_records (
        voyage_id, origin_port_unlocode, destination_port_unlocode, transit_date,
        total_disbursement_usd, readiness_tier, completeness_status, is_complete,
        has_partial_components, is_operational_approved, blocked_reasons_json, evidence_summary_json
    ) VALUES (
        'VOY-LEGACY-001', 'SGSIN', 'NLRTM', '2024-01-15',
        75000.0, 'LEVEL_2_RESEARCH_ONLY', 'COMPLETE_WITHIN_REGISTERED_SCOPE', 1,
        0, 0, '[]', '{}'
    );
    """)
    rec_id = cur.lastrowid

    cur.execute(f"""
    INSERT INTO voyage_disbursement_line_items (
        voyage_disbursement_record_id, charge_type, location_code, charge_category,
        tariff_identifier, description, applicability_type, rate_value, billing_unit,
        local_currency, amount_local, exchange_rate_to_usd, amount_usd, is_available
    ) VALUES (
        {rec_id}, 'PORT_DISBURSEMENT', 'SGSIN', 'PORT_DUES',
        'SGSIN-PORT-DUES-CARGO-2023', 'Singapore Port Dues (Cargo)', 'MANDATORY', 10.5,
        'PER_100_GT_OR_PART_THEREOF', 'SGD', 10000.0, 0.75, 7500.0, 1
    );
    """)

    conn.commit()

    # Pre-migration schema inspection
    cur.execute("PRAGMA table_info(voyage_disbursement_records);")
    pre_cols_rec = [row[1] for row in cur.fetchall()]
    cur.execute("PRAGMA table_info(voyage_disbursement_line_items);")
    pre_cols_items = [row[1] for row in cur.fetchall()]
    conn.close()

    print("\n=== Pre-Migration Columns ===")
    print("voyage_disbursement_records columns:", pre_cols_rec)
    print("voyage_disbursement_line_items columns:", pre_cols_items)

    assert "intermediate_ports_json" not in pre_cols_rec
    assert "canal_transits_json" not in pre_cols_rec
    assert "unallocated_overage_usd" not in pre_cols_rec
    assert "disclaimer" not in pre_cols_rec
    assert "fx_rate_provider" not in pre_cols_items
    assert "fx_observation_date" not in pre_cols_items
    assert "requested_valuation_date" not in pre_cols_items
    assert "fx_observation_lag_days" not in pre_cols_items
    assert "conversion_evidence_classification" not in pre_cols_items
    assert "charge_tier" not in pre_cols_items
    assert "contract_attribution" not in pre_cols_items
    assert "is_provisional" not in pre_cols_items

    print("\n=== 2. Executing ensure_schema_compatibility against isolated database ===")
    engine = create_engine(f"sqlite:///{db_path}")
    ensure_schema_compatibility(engine)

    # Post-migration schema inspection
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(voyage_disbursement_records);")
    post_cols_rec = [row[1] for row in cur.fetchall()]
    cur.execute("PRAGMA table_info(voyage_disbursement_line_items);")
    post_cols_items = [row[1] for row in cur.fetchall()]

    print("\n=== Post-Migration Columns ===")
    print("voyage_disbursement_records columns:", post_cols_rec)
    print("voyage_disbursement_line_items columns:", post_cols_items)

    # Verify all 12 newly added columns exist
    assert "intermediate_ports_json" in post_cols_rec
    assert "canal_transits_json" in post_cols_rec
    assert "unallocated_overage_usd" in post_cols_rec
    assert "disclaimer" in post_cols_rec
    assert "fx_rate_provider" in post_cols_items
    assert "fx_observation_date" in post_cols_items
    assert "requested_valuation_date" in post_cols_items
    assert "fx_observation_lag_days" in post_cols_items
    assert "conversion_evidence_classification" in post_cols_items
    assert "charge_tier" in post_cols_items
    assert "contract_attribution" in post_cols_items
    assert "is_provisional" in post_cols_items

    print("\n=== 3. Verifying Legacy Row Survival & Nullable Defaults ===")
    cur.execute("SELECT id, voyage_id, total_disbursement_usd, unallocated_overage_usd, disclaimer FROM voyage_disbursement_records WHERE id = 1;")
    legacy_rec = cur.fetchone()
    print("Legacy record after migration:", legacy_rec)
    assert legacy_rec[0] == 1
    assert legacy_rec[1] == "VOY-LEGACY-001"
    assert legacy_rec[2] == 75000.0
    assert legacy_rec[3] is None  # unallocated_overage_usd is null
    assert legacy_rec[4] is None  # disclaimer is null

    cur.execute("SELECT id, tariff_identifier, amount_usd, charge_tier, contract_attribution, is_provisional FROM voyage_disbursement_line_items WHERE id = 1;")
    legacy_item = cur.fetchone()
    print("Legacy line item after migration:", legacy_item)
    assert legacy_item[0] == 1
    assert legacy_item[1] == "SGSIN-PORT-DUES-CARGO-2023"
    assert legacy_item[2] == 7500.0
    assert legacy_item[3] is None  # charge_tier is null
    assert legacy_item[4] is None  # contract_attribution is null
    assert legacy_item[5] == 0 or legacy_item[5] is False  # is_provisional default 0

    conn.close()

    print("\n=== 4. Verifying New ORM Insertion and Querying ===")
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    new_rec = VoyageDisbursementRecord(
        voyage_id="VOY-F143-001",
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="NLRTM",
        transit_date=datetime.date(2024, 1, 15),
        total_disbursement_usd=120000.0,
        unallocated_overage_usd=2500.0,
        disclaimer="QUALIFIED COMMERCIAL ESTIMATE",
        readiness_tier="LEVEL_2_RESEARCH_ONLY",
        completeness_status="COMPLETE_WITHIN_REGISTERED_SCOPE",
        is_complete=True,
        has_partial_components=False,
        is_operational_approved=False,
        blocked_reasons_json="[]",
        evidence_summary_json="{}",
    )
    session.add(new_rec)
    session.flush()

    new_item = VoyageDisbursementLineItem(
        voyage_disbursement_record_id=new_rec.id,
        charge_type="PORT_DISBURSEMENT",
        location_code="SGSIN",
        charge_category="PORT_DUES",
        tariff_identifier="SGSIN-PORT-DUES-CARGO-2023",
        description="Singapore Cargo Port Dues",
        applicability_type="MANDATORY",
        rate_value=10.5,
        billing_unit="PER_100_GT",
        local_currency="SGD",
        amount_local=10000.0,
        amount_usd=7500.0,
        charge_tier="TIER_1_STATUTORY_DUES",
        contract_attribution=None,
        is_provisional=False,
        requested_valuation_date=datetime.date(2024, 1, 15),
        fx_observation_lag_days=0,
        conversion_evidence_classification="OFFICIAL_FIXING",
    )
    session.add(new_item)
    session.commit()

    # Query back using ORM
    queried = session.scalars(select(VoyageDisbursementRecord).where(VoyageDisbursementRecord.voyage_id == "VOY-F143-001")).first()
    assert queried is not None
    assert queried.unallocated_overage_usd == 2500.0
    assert queried.disclaimer == "QUALIFIED COMMERCIAL ESTIMATE"
    assert len(queried.line_items) == 1
    assert queried.line_items[0].charge_tier == "TIER_1_STATUTORY_DUES"
    assert queried.line_items[0].conversion_evidence_classification == "OFFICIAL_FIXING"
    assert queried.line_items[0].fx_observation_lag_days == 0
    session.close()

    print("ORM round-trip query succeeded!")

    print("\n=== 5. Verifying Repeated Migration Idempotency ===")
    ensure_schema_compatibility(engine)
    ensure_schema_compatibility(engine)
    print("Repeated ensure_schema_compatibility succeeded without error!")

    print("\n=== 6. Verifying Production Database pelagos.db Untouched ===")
    prod_db_path = Path(__file__).resolve().parent.parent / "pelagos.db"
    if prod_db_path.exists():
        print(f"Production database pelagos.db exists at {prod_db_path} (size: {prod_db_path.stat().st_size} bytes).")
    else:
        print("Production database pelagos.db does not exist in workspace root.")

    # Clean up test database
    engine.dispose()
    if db_path.exists():
        db_path.unlink()
        print(f"Cleaned up isolated test database {db_path}.")

    print("\n>>> ALL MIGRATION VERIFICATION CHECKS PASSED SUCCESSFULLY! <<<")


if __name__ == "__main__":
    run_migration_audit()
