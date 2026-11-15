"""Focused unit and integration tests for Pelagos Machinery Registry (Stage F1.4.2.2a).

Validates:
1. Machinery field normalization, validation, and units.
2. Power consistency validation between total rated power and cylinder count * power per cylinder.
3. IMO-based canonical vessel resolution and machinery retrieval.
4. Field-level provenance for certified and unavailable candidate vessels.
5. Exact engine variant preservation.
6. Unavailable and missing machinery particulars (fail-closed behavior).
7. Conflicting machinery source observations and deterministic resolution.
8. Idempotent ingestion of candidate machinery records.
9. Existing vessel registry compatibility and operational estimate isolation.
10. Additive schema migration on pre-existing database tables.
"""

from __future__ import annotations

import datetime
from typing import Generator
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

import pelagos.models as models
from pelagos.database import Base, ensure_schema_compatibility, init_db
from pelagos.models import (
    VerificationStatus,
    Vessel,
    VesselAttributeProvenance,
    VesselMmsiHistory,
    VesselOperationalEstimates,
)
from pelagos.registry.vessel_registry import (
    VesselRegistry,
    classify_attribute_difference,
    validate_cargo_gear_evidence,
    validate_machinery_power_consistency,
)


@pytest.fixture
def mem_db() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database initialized with Pelagos schema."""
    eng = create_engine("sqlite:///:memory:")
    init_db(eng)
    SessionLocal = sessionmaker(bind=eng)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 1. Machinery Field Normalization and Units Validation
# ---------------------------------------------------------------------------

def test_machinery_field_normalization(mem_db: Session):
    """Verify normalization of machinery numeric, boolean, and text fields."""
    reg = VesselRegistry(mem_db)

    # Float numeric fields: bore, stroke, power, rpm, crane SWL
    for field in ["bore_mm", "stroke_mm", "rated_power_kw", "rated_rpm", "crane_swl_tonnes"]:
        raw, norm, method = reg.normalize_attribute(field, "600.0")
        assert norm == 600.0
        assert method == "numeric_float"

        raw, norm, method = reg.normalize_attribute(field, 8330)
        assert norm == 8330.0
        assert method == "numeric_float"

        # Invalid finite/negative
        raw, norm, method = reg.normalize_attribute(field, -100)
        assert norm is None
        assert method == "numeric_invalid_finite"

        raw, norm, method = reg.normalize_attribute(field, "invalid_num")
        assert norm is None
        assert method == "numeric_parse_failed"

    # Integer fields: cylinder_count, crane_count
    for field in ["cylinder_count", "crane_count"]:
        raw, norm, method = reg.normalize_attribute(field, "6")
        assert norm == 6
        assert method == "numeric_int"

        raw, norm, method = reg.normalize_attribute(field, 4)
        assert norm == 4
        assert method == "numeric_int"

        # Plural aliases
        raw, norm, method = reg.normalize_attribute("cylinders_count", "5")
        assert norm == 5
        raw, norm, method = reg.normalize_attribute("cranes_count", "4")
        assert norm == 4

        # Negative invalid
        raw, norm, method = reg.normalize_attribute(field, -1)
        assert norm is None
        assert method == "int_negative_invalid"

    # Boolean field: is_geared
    raw, norm, method = reg.normalize_attribute("is_geared", True)
    assert norm is True
    assert method == "boolean_direct"

    raw, norm, method = reg.normalize_attribute("is_geared", "true")
    assert norm is True
    assert method == "boolean_parsed"

    raw, norm, method = reg.normalize_attribute("is_geared", "geared")
    assert norm is True
    assert method == "boolean_parsed"

    raw, norm, method = reg.normalize_attribute("is_geared", False)
    assert norm is False
    assert method == "boolean_direct"

    raw, norm, method = reg.normalize_attribute("is_geared", "gearless")
    assert norm is False
    assert method == "boolean_parsed"


# ---------------------------------------------------------------------------
# 2. Power Consistency Validation (Total kW vs Cylinder Count * kW/Cyl)
# ---------------------------------------------------------------------------

def test_power_consistency_validation():
    """Verify consistency check between cylinder count, power per cylinder, and rated power."""
    # Iris Bliss: 6 cyl * 1610 kW = 9660 kW -> consistent
    ok, err = validate_machinery_power_consistency(
        cylinder_count=6,
        rated_power_kw=9660.0,
        power_per_cylinder_kw=1610.0,
    )
    assert ok is True
    assert err is None

    # Berge Galdhopiggen: 5 cyl * 1220 kW = 6100 kW -> consistent
    ok, err = validate_machinery_power_consistency(
        cylinder_count=5,
        rated_power_kw=6100.0,
        power_per_cylinder_kw=1220.0,
    )
    assert ok is True
    assert err is None

    # Inconsistent case: 6 cyl * 1200 kW = 7200 kW vs 9660 kW reported -> > 5% mismatch
    ok, err = validate_machinery_power_consistency(
        cylinder_count=6,
        rated_power_kw=9660.0,
        power_per_cylinder_kw=1200.0,
    )
    assert ok is False
    assert "Power inconsistency" in err

    # Non-positive values fail
    ok, err = validate_machinery_power_consistency(cylinder_count=-1, rated_power_kw=5000.0)
    assert ok is False
    assert "Non-positive" in err

    # None values pass (no inconsistency when data absent)
    ok, err = validate_machinery_power_consistency(cylinder_count=None, rated_power_kw=None)
    assert ok is True


# ---------------------------------------------------------------------------
# 3. IMO-Based Resolution and Candidate Machinery Particulars
# ---------------------------------------------------------------------------

def test_candidate_vessel_machinery_resolution(mem_db: Session):
    """Verify loading candidate bulk fleet and resolving verified machinery attributes."""
    reg = VesselRegistry(mem_db)
    load_metrics = reg.load_seed_file()
    assert load_metrics["total_candidates"] == 6
    assert load_metrics["created_count"] == 6

    # 1. IRIS BLISS (IMO 9766944) - ClassNK 162819
    iris_info = reg.get_vessel_machinery(imo="9766944")
    assert iris_info is not None
    assert iris_info["overall_machinery_status"] == VerificationStatus.DIRECTLY_VERIFIED
    fields = iris_info["machinery_fields"]
    assert fields["engine_manufacturer"] == "MAN Energy Solutions"
    assert fields["engine_model"] == "MAN B&W 6S60ME-C"
    assert fields["engine_variant"] == "6S60ME-C8.2"
    assert fields["engine_builder"] == "Kawasaki Heavy Industries, Ltd."
    assert fields["cylinder_count"] == 6
    assert fields["bore_mm"] == 600.0
    assert fields["stroke_mm"] == 2400.0
    assert fields["rated_power_kw"] == 9660.0
    assert fields["rated_rpm"] == 89.0
    assert fields["is_geared"] is False
    assert fields["crane_count"] == 0
    assert fields["crane_swl_tonnes"] is None

    # Check field-level provenance
    prov = iris_info["fields_provenance"]
    assert prov["engine_model"]["verification_status"] == VerificationStatus.DIRECTLY_VERIFIED
    assert prov["engine_model"]["source_record_id"] == "ClassNK-162819"
    assert "imo=9766944&reg_no=162819" in prov["engine_model"]["source_url"]
    assert prov["engine_variant"]["verification_status"] == VerificationStatus.SOURCE_REPORTED

    # 2. NAVIOS VICTORY (IMO 9713430) - ClassNK 145541
    navios_info = reg.get_vessel_machinery(imo="9713430")
    assert navios_info is not None
    assert navios_info["overall_machinery_status"] == VerificationStatus.DIRECTLY_VERIFIED
    n_fields = navios_info["machinery_fields"]
    assert n_fields["engine_model"] == "MAN B&W 6S60ME-C"
    assert n_fields["engine_builder"] == "Mitsui E&S Machinery Co., Ltd. / Kawasaki"
    assert n_fields["rated_power_kw"] == 8330.0  # CSR rating
    assert n_fields["rated_rpm"] == 80.5
    assert n_fields["is_geared"] is False

    # 3. BERGE GALDHOPIGGEN (IMO 9753284) - DNV 34947
    berge_info = reg.get_vessel_machinery(imo="9753284")
    assert berge_info is not None
    assert berge_info["overall_machinery_status"] == VerificationStatus.DIRECTLY_VERIFIED
    b_fields = berge_info["machinery_fields"]
    assert b_fields["engine_manufacturer"] == "WinGD / Wärtsilä"
    assert b_fields["engine_model"] == "5RT-flex50D"
    assert b_fields["engine_variant"] == "RT-flex50D Tier II"
    assert b_fields["engine_builder"] == "CSSC-MES Diesel Co., Ltd. (CMD)"
    assert b_fields["cylinder_count"] == 5
    assert b_fields["bore_mm"] == 500.0
    assert b_fields["stroke_mm"] == 2050.0
    assert b_fields["rated_power_kw"] == 6100.0
    assert b_fields["rated_rpm"] == 99.0
    assert b_fields["is_geared"] is True
    assert b_fields["crane_count"] == 4
    assert b_fields["crane_swl_tonnes"] == 30.5


# ---------------------------------------------------------------------------
# 4. Fail-Closed Preservation for Unavailable Vessels
# ---------------------------------------------------------------------------

def test_unavailable_vessels_machinery_fail_closed(mem_db: Session):
    """Verify vessels not in public ClassNK/DNV remain strictly UNAVAILABLE without synthetic guessing."""
    reg = VesselRegistry(mem_db)
    reg.load_seed_file()

    # EGRET BULKER (IMO 9441295, BV)
    egret_info = reg.get_vessel_machinery(imo="9441295")
    assert egret_info["overall_machinery_status"] == VerificationStatus.UNAVAILABLE
    e_fields = egret_info["machinery_fields"]
    assert e_fields["engine_manufacturer"] is None
    assert e_fields["engine_model"] is None
    assert e_fields["cylinder_count"] is None
    assert e_fields["rated_power_kw"] is None
    assert e_fields["is_geared"] is True  # Cargo gear is source-reported from visual inspection
    assert e_fields["crane_count"] == 4
    assert e_fields["crane_swl_tonnes"] == 30.5

    # HADEEL (IMO 9406087, BV)
    hadeel_info = reg.get_vessel_machinery(imo="9406087")
    assert hadeel_info["overall_machinery_status"] == VerificationStatus.UNAVAILABLE
    assert hadeel_info["machinery_fields"]["engine_model"] is None

    # BULK UNITY (IMO 9591301, LR)
    unity_info = reg.get_vessel_machinery(imo="9591301")
    assert unity_info["overall_machinery_status"] == VerificationStatus.UNAVAILABLE
    assert unity_info["machinery_fields"]["engine_model"] is None
    assert unity_info["machinery_fields"]["is_geared"] is False  # Certified gearless


# ---------------------------------------------------------------------------
# 5. Exact Engine Variant Preservation and Conflict Handling
# ---------------------------------------------------------------------------

def test_engine_variant_preservation_and_conflict_classification():
    """Verify variant differences are classified as measurement definition differences rather than silent overwrites."""
    # Minor naming/casing difference -> definition variance
    is_conflict, cat, reason = classify_attribute_difference(
        "engine_model",
        "MAN B&W 6S60ME-C",
        "MAN B&W 6S60ME-C8.2",
    )
    assert is_conflict is False
    assert cat == "measurement_definition_difference"
    assert "variant variance" in reason

    # Contradictory engine model -> genuine conflict
    is_conflict, cat, reason = classify_attribute_difference(
        "engine_model",
        "MAN B&W 6S60ME-C",
        "WinGD 5RT-flex50D",
    )
    assert is_conflict is True
    assert cat == "genuine_conflict"

    # Cylinder count difference -> genuine conflict (ship cannot have 5 and 6 cylinders)
    is_conflict, cat, reason = classify_attribute_difference("cylinder_count", 5, 6)
    assert is_conflict is True
    assert cat == "genuine_conflict"

    # MCR vs CSR power rating -> measurement definition difference
    # 9660 kW vs 8330 kW (diff = 1330 kW, rel_diff = 13.7%)
    is_conflict, cat, reason = classify_attribute_difference("rated_power_kw", 9660.0, 8330.0)
    assert is_conflict is False
    assert cat == "measurement_definition_difference"
    assert "CSR" in reason

    # Divergent power rating (> 25% discrepancy) -> genuine conflict
    is_conflict, cat, reason = classify_attribute_difference("rated_power_kw", 9660.0, 5000.0)
    assert is_conflict is True
    assert cat == "genuine_conflict"

    # Gearless vs Geared contradiction -> genuine conflict
    is_conflict, cat, reason = classify_attribute_difference("is_geared", True, False)
    assert is_conflict is True
    assert cat == "genuine_conflict"


# ---------------------------------------------------------------------------
# 6. Conflicting Source Observation Resolution
# ---------------------------------------------------------------------------

def test_conflicting_machinery_observation_resolution(mem_db: Session):
    """Verify deterministic resolution when a higher-ranked class register conflicts with a commercial report."""
    reg = VesselRegistry(mem_db)

    # Ingest baseline vessel with commercial reported engine
    v_data = {
        "imo": "9766944",
        "vessel_name": "IRIS BLISS",
        "vessel_type": "Bulk Carrier",
        "dwt_tonnage": 82103.0,
        "engine_model": "6S60MC",  # Erroneous commercial report
        "rated_power_kw": 9000.0,
        "source_name": "commercial_directory",
        "fields_provenance": {
            "engine_model": {
                "status": VerificationStatus.SOURCE_REPORTED,
                "source_name": "Commercial Directory",
                "raw_value": "6S60MC",
            },
            "rated_power_kw": {
                "status": VerificationStatus.SOURCE_REPORTED,
                "source_name": "Commercial Directory",
                "raw_value": "9000.0",
            }
        }
    }
    vessel, created, _ = reg.upsert_vessel(v_data)
    assert vessel.engine_model == "6S60MC"

    # Ingest certified ClassNK observation -> DIRECTLY_VERIFIED must overwrite commercial report
    classnk_data = {
        "imo": "9766944",
        "vessel_name": "IRIS BLISS",
        "vessel_type": "Bulk Carrier",
        "engine_model": "MAN B&W 6S60ME-C",
        "rated_power_kw": 9660.0,
        "source_name": "ClassNK Register of Ships",
        "source_record_id": "ClassNK-162819",
        "source_url": "https://www.classnk.or.jp/hp/en/register/regships/regships.aspx?imo=9766944&reg_no=162819",
        "fields_provenance": {
            "engine_model": {
                "status": VerificationStatus.DIRECTLY_VERIFIED,
                "source_name": "ClassNK Register of Ships",
                "source_record_id": "ClassNK-162819",
                "source_url": "https://www.classnk.or.jp/hp/en/register/regships/regships.aspx?imo=9766944&reg_no=162819",
                "raw_value": "MAN B&W 6S60ME-C",
            },
            "rated_power_kw": {
                "status": VerificationStatus.DIRECTLY_VERIFIED,
                "source_name": "ClassNK Register of Ships",
                "source_record_id": "ClassNK-162819",
                "source_url": "https://www.classnk.or.jp/hp/en/register/regships/regships.aspx?imo=9766944&reg_no=162819",
                "raw_value": "9660.0",
            }
        }
    }
    vessel2, created2, _ = reg.upsert_vessel(classnk_data)
    assert created2 is False
    assert vessel2.id == vessel.id
    assert vessel2.engine_model == "MAN B&W 6S60ME-C"
    assert vessel2.rated_power_kw == 9660.0


# ---------------------------------------------------------------------------
# 7. Additive Schema Migration on Pre-Existing Database
# ---------------------------------------------------------------------------

def test_additive_schema_migration_preserves_existing_data():
    """Verify ensure_schema_compatibility adds machinery columns to an existing table without data loss."""
    eng = create_engine("sqlite:///:memory:")

    # Create legacy vessels table without machinery columns
    with eng.connect() as conn:
        conn.execute(text("""
            CREATE TABLE vessels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mmsi VARCHAR(32) UNIQUE NOT NULL,
                imo VARCHAR(32),
                vessel_name VARCHAR(128) NOT NULL,
                vessel_type VARCHAR(64) NOT NULL,
                vessel_type_code INTEGER,
                vessel_class VARCHAR(64),
                dwt_tonnage FLOAT,
                gross_tonnage FLOAT,
                length_meters FLOAT,
                width_meters FLOAT,
                draft_max_meters FLOAT,
                built_year INTEGER,
                flag_country VARCHAR(64),
                flag_code VARCHAR(8),
                source_name VARCHAR(128) NOT NULL,
                commercial_status VARCHAR(32) DEFAULT 'unknown',
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
        """))
        # Insert a pre-existing vessel record
        conn.execute(text("""
            INSERT INTO vessels (mmsi, imo, vessel_name, vessel_type, dwt_tonnage, source_name)
            VALUES ('566448000', '9766944', 'IRIS BLISS', 'Bulk Carrier', 82103.0, 'pre_existing')
        """))
        conn.commit()

    # Verify machinery columns do not exist yet
    inspector = inspect(eng)
    cols_before = {c["name"] for c in inspector.get_columns("vessels")}
    assert "engine_model" not in cols_before
    assert "cylinder_count" not in cols_before
    assert "is_geared" not in cols_before

    # Run additive migration
    ensure_schema_compatibility(eng)

    # Verify machinery columns were added
    inspector_after = inspect(eng)
    cols_after = {c["name"] for c in inspector_after.get_columns("vessels")}
    assert "engine_manufacturer" in cols_after
    assert "engine_model" in cols_after
    assert "engine_variant" in cols_after
    assert "engine_builder" in cols_after
    assert "cylinder_count" in cols_after
    assert "bore_mm" in cols_after
    assert "stroke_mm" in cols_after
    assert "rated_power_kw" in cols_after
    assert "rated_rpm" in cols_after
    assert "is_geared" in cols_after
    assert "crane_count" in cols_after
    assert "crane_swl_tonnes" in cols_after

    # Verify pre-existing data was completely preserved
    with eng.connect() as conn:
        row = conn.execute(text("SELECT imo, vessel_name, dwt_tonnage, engine_model FROM vessels WHERE imo='9766944'")).fetchone()
        assert row[0] == "9766944"
        assert row[1] == "IRIS BLISS"
        assert row[2] == 82103.0
        assert row[3] is None  # Initialized to NULL gracefully


# ---------------------------------------------------------------------------
# 8. Operational Estimate Isolation
# ---------------------------------------------------------------------------

def test_operational_estimates_isolated_from_machinery_registry(mem_db: Session):
    """Verify recording operational estimates does not modify factual machinery particulars."""
    reg = VesselRegistry(mem_db)
    reg.load_seed_file()

    iris = reg.resolve_canonical_vessel(imo="9766944")
    original_model = iris.engine_model
    original_kw = iris.rated_power_kw

    # Record an operational engineering estimate
    est = reg.record_operational_estimate(
        vessel_id=iris.id,
        model_name="Admiralty_Holtrop_Hybrid_v1",
        model_version="1.0.0",
        eco_speed_knots=12.5,
        laden_consumption_mt_per_day=24.5,
        ballast_consumption_mt_per_day=19.8,
        port_idle_consumption_mt_per_day=1.8,
        formula_reference="P = (Delta^(2/3) * V^3) / C_adm",
        assumptions="Clean hull, calm sea, ISO 3046-1 SFOC 164.5 g/kWh",
    )
    assert est.id is not None
    assert est.vessel_id == iris.id

    # Verify machinery attributes remain strictly unmodified
    mem_db.refresh(iris)
    assert iris.engine_model == original_model
    assert iris.rated_power_kw == original_kw


# ---------------------------------------------------------------------------
# 9. Cargo Gear Evidence Semantics (Stage F1.4.2.2b Closure)
# ---------------------------------------------------------------------------

def test_cargo_gear_evidence_semantics(mem_db: Session):
    """Verify strict cargo gear evidence rules: explicit zero, missing values, no negative inference."""
    # 1. crane_swl_tonnes = None cannot be DIRECTLY_VERIFIED
    ok, err = validate_cargo_gear_evidence("crane_swl_tonnes", None, VerificationStatus.DIRECTLY_VERIFIED)
    assert ok is False
    assert "cannot be DIRECTLY_VERIFIED" in err

    # crane_swl_tonnes = None as UNAVAILABLE is valid
    ok, err = validate_cargo_gear_evidence("crane_swl_tonnes", None, VerificationStatus.UNAVAILABLE)
    assert ok is True

    # 2. crane_count = 0 cannot be DIRECTLY_VERIFIED solely from register silence/absence
    ok, err = validate_cargo_gear_evidence(
        "crane_count",
        0,
        VerificationStatus.DIRECTLY_VERIFIED,
        source_rationale="Certified gearless bulk carrier (no cargo handling deck machinery registered).",
    )
    assert ok is False
    assert "solely from absence" in err

    # crane_count = 0 with explicit certificate statement IS directly verified
    ok, err = validate_cargo_gear_evidence(
        "crane_count",
        0,
        VerificationStatus.DIRECTLY_VERIFIED,
        source_rationale="Statutory certificate explicitly confirms zero cranes / no cargo handling gear.",
    )
    assert ok is True

    # 3. is_geared = False cannot be DIRECTLY_VERIFIED from absence in register
    ok, err = validate_cargo_gear_evidence(
        "is_geared",
        False,
        VerificationStatus.DIRECTLY_VERIFIED,
        source_rationale="no cargo handling deck machinery registered",
    )
    assert ok is False
    assert "cannot be DIRECTLY_VERIFIED solely from the absence" in err

    # 4. Source-reported gearless values preserved without upgrading
    reg = VesselRegistry(mem_db)
    reg.load_seed_file()

    iris_info = reg.get_vessel_machinery(imo="9766944")
    prov = iris_info["fields_provenance"]
    assert prov["is_geared"]["verification_status"] == VerificationStatus.SOURCE_REPORTED
    assert prov["crane_count"]["verification_status"] == VerificationStatus.SOURCE_REPORTED

    # Berge Galdhopiggen explicitly verified geared
    berge_info = reg.get_vessel_machinery(imo="9753284")
    b_prov = berge_info["fields_provenance"]
    assert b_prov["is_geared"]["verification_status"] == VerificationStatus.DIRECTLY_VERIFIED
    assert b_prov["crane_count"]["verification_status"] == VerificationStatus.DIRECTLY_VERIFIED
    assert b_prov["crane_swl_tonnes"]["verification_status"] == VerificationStatus.DIRECTLY_VERIFIED


# ---------------------------------------------------------------------------
# 10. Advanced Power Consistency & Definition Compatibility
# ---------------------------------------------------------------------------

def test_power_consistency_definition_and_per_cylinder_confusion():
    """Verify detection of per-cylinder confusion and MCR vs CSR compatibility."""
    # 1. Total rated power confused with per-cylinder power
    # 6-cyl engine reported as 1610 kW total when 1610 is per-cylinder power
    ok, err = validate_machinery_power_consistency(
        cylinder_count=6,
        rated_power_kw=1610.0,
        power_per_cylinder_kw=1610.0,
    )
    assert ok is False
    assert "appears to be per-cylinder power" in err

    # 2. Incompatible power definitions: MCR vs CSR does not raise a false conflict
    # Iris Bliss MCR 9660 kW vs Navios Victory CSR 8330 kW
    ok, msg = validate_machinery_power_consistency(
        cylinder_count=6,
        rated_power_kw=8330.0,
        power_per_cylinder_kw=1610.0,  # 6 * 1610 = 9660 kW (MCR rating)
        power_definition="CSR",
        power_per_cylinder_definition="MCR",
    )
    assert ok is True
    assert "incompatible rating definitions" in msg

    # 3. Compatible definitions: MCR vs SMCR
    ok, err = validate_machinery_power_consistency(
        cylinder_count=6,
        rated_power_kw=9660.0,
        power_per_cylinder_kw=1610.0,
        power_definition="SMCR",
        power_per_cylinder_definition="MCR",
    )
    assert ok is True
    assert err is None

