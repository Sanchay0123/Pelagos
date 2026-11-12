"""Tests for Pelagos Multi-Source Vessel Registry & Verified Particulars Foundation (Stage F1.2).

Validates canonical identity resolution, temporal MMSI tracking, field-level provenance,
deterministic conflict resolution, operational estimate isolation, seed idempotency,
and request-specific Gate 4 / matcher integration.
"""

from __future__ import annotations

import datetime
from typing import Generator
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import pelagos.models as models
from pelagos.database import Base, init_db
from pelagos.models import (
    VerificationStatus,
    Vessel,
    VesselAttributeProvenance,
    VesselMmsiHistory,
    VesselOperationalEstimates,
    VesselPosition,
    validate_imo,
)
from pelagos.optimization.chartering import assess_vessel_for_voyage
from pelagos.optimization.matcher import (
    evaluate_cargo_compatibility,
    evaluate_vessel_suitability,
    match_suitable_vessels,
)
from pelagos.optimization.schemas import SuitabilityStatus, VesselCompatibilityRequest
from pelagos.registry.vessel_registry import (
    InvalidImoError,
    MalformedVesselRecordError,
    OverlappingMmsiError,
    VesselRegistry,
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
# 1. IMO Checksum Validation
# ---------------------------------------------------------------------------

def test_imo_checksum_validation_valid_cases():
    """Verify standard 7-digit IMO numbers pass check-digit validation."""
    valid_imos = [
        "9441295",  # Egret Bulker
        "9713430",  # Navios Victory
        "9766944",  # Iris Bliss
        "9753284",  # Berge Galdhopiggen
        "9406087",  # Hadeel
        "9591301",  # Bulk Unity
        "IMO 9441295",  # Prefix handling
        "imo9713430",  # Lowercase prefix handling
    ]
    for raw in valid_imos:
        is_valid, clean_imo, err = validate_imo(raw)
        assert is_valid is True, f"Expected {raw} to be valid, got error: {err}"
        assert len(clean_imo) == 7
        assert clean_imo.isdigit()
        assert err is None


def test_imo_checksum_validation_invalid_and_nullable_cases():
    """Verify invalid check digits and formats fail, while null/empty pass as None."""
    # Invalid check digits
    assert validate_imo("9441290")[0] is False
    assert validate_imo("9713431")[0] is False

    # Invalid lengths or non-numeric
    assert validate_imo("12345")[0] is False
    assert validate_imo("abcdefg")[0] is False
    assert validate_imo("12345678")[0] is False

    # Nullable / empty handling
    assert validate_imo(None) == (True, None, None)
    assert validate_imo("") == (True, None, None)
    assert validate_imo("   ") == (True, None, None)


# ---------------------------------------------------------------------------
# 2. IMO Uniqueness and Nullable Compatibility
# ---------------------------------------------------------------------------

def test_imo_uniqueness_and_nullable_compatibility(mem_db: Session):
    """Verify multiple vessels with null IMO are accepted, while duplicate non-null IMO is rejected."""
    # Two domestic vessels without IMO numbers
    v1 = Vessel(
        mmsi="111222333",
        imo=None,
        vessel_name="Harbor Tug 1",
        vessel_type="Tug",
        source_name="test",
    )
    v2 = Vessel(
        mmsi="111222444",
        imo=None,
        vessel_name="Harbor Tug 2",
        vessel_type="Tug",
        source_name="test",
    )
    mem_db.add_all([v1, v2])
    mem_db.commit()

    assert mem_db.query(Vessel).count() == 2

    # Add vessel with valid IMO
    v3 = Vessel(
        mmsi="538003929",
        imo="9441295",
        vessel_name="Egret Bulker",
        vessel_type="Bulk Carrier",
        source_name="test",
    )
    mem_db.add(v3)
    mem_db.commit()

    # Attempting to add duplicate IMO via registry raises or fails
    reg = VesselRegistry(mem_db)
    with pytest.raises(Exception):
        v_dup = Vessel(
            mmsi="538003999",
            imo="9441295",  # Duplicate IMO
            vessel_name="Duplicate Ship",
            vessel_type="Bulk Carrier",
            source_name="test",
        )
        mem_db.add(v_dup)
        mem_db.commit()
    mem_db.rollback()


# ---------------------------------------------------------------------------
# 3. Canonical Identity Resolution
# ---------------------------------------------------------------------------

def test_canonical_identity_resolution(mem_db: Session):
    """Test resolution by IMO, primary MMSI, and historical MMSI."""
    reg = VesselRegistry(mem_db)

    # Ingest vessel
    v, created, _ = reg.upsert_vessel(
        {
            "imo": "9441295",
            "mmsi": "538003929",
            "vessel_name": "EGRET BULKER",
            "vessel_type": "Bulk Carrier",
            "dwt_tonnage": 57809.0,
        },
        default_source_name="test_registry",
    )
    assert created is True
    assert v.vessel_id == v.id

    # Add historical former MMSI
    reg.record_mmsi(
        vessel_id=v.id,
        mmsi="538001111",  # Former MMSI
        flag_country="Marshall Islands",
        valid_from=datetime.datetime(2010, 1, 1, tzinfo=datetime.timezone.utc),
        valid_to=datetime.datetime(2015, 1, 1, tzinfo=datetime.timezone.utc),
        source_name="historical_records",
    )
    mem_db.commit()

    # 1. Resolve by IMO
    res1 = reg.resolve_canonical_vessel(imo="9441295")
    assert res1 is not None
    assert res1.id == v.id

    # 2. Resolve by current primary MMSI
    res2 = reg.resolve_canonical_vessel(mmsi="538003929")
    assert res2 is not None
    assert res2.id == v.id

    # 3. Resolve by historical former MMSI
    res3 = reg.resolve_canonical_vessel(mmsi="538001111")
    assert res3 is not None
    assert res3.id == v.id

    # 4. Unknown identifiers return None
    assert reg.resolve_canonical_vessel(imo="9713430") is None
    assert reg.resolve_canonical_vessel(mmsi="999999999") is None

    # 5. Invalid IMO raises InvalidImoError
    with pytest.raises(InvalidImoError):
        reg.resolve_canonical_vessel(imo="9441290")


# ---------------------------------------------------------------------------
# 4. Historical MMSI Associations and Overlap Prevention
# ---------------------------------------------------------------------------

def test_historical_mmsi_overlap_prevention(mem_db: Session):
    """Verify invalid overlapping temporal MMSI assignments are rejected."""
    reg = VesselRegistry(mem_db)
    v, _, _ = reg.upsert_vessel(
        {"imo": "9713430", "mmsi": "356885000", "vessel_name": "NAVIOS VICTORY"},
        default_source_name="test",
    )

    t1 = datetime.datetime(2014, 1, 1, tzinfo=datetime.timezone.utc)
    t2 = datetime.datetime(2018, 1, 1, tzinfo=datetime.timezone.utc)
    t3 = datetime.datetime(2016, 1, 1, tzinfo=datetime.timezone.utc)
    t4 = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)

    # Valid bounded record
    reg.record_mmsi(
        vessel_id=v.id,
        mmsi="356111111",
        valid_from=t1,
        valid_to=t2,
        source_name="flag_survey",
        commit=True,
    )

    # Attempting to assign a distinct MMSI with overlapping dates raises OverlappingMmsiError
    with pytest.raises(OverlappingMmsiError):
        reg.record_mmsi(
            vessel_id=v.id,
            mmsi="356222222",
            valid_from=t3,  # 2016 falls inside 2014-2018
            valid_to=t4,
            source_name="flag_survey",
            commit=True,
        )

    # Attempting concurrent unbounded open-ended MMSIs raises OverlappingMmsiError
    with pytest.raises(OverlappingMmsiError):
        reg.record_mmsi(
            vessel_id=v.id,
            mmsi="356333333",
            valid_from=t4,
            valid_to=None,  # Open-ended conflict with primary 356885000
            source_name="flag_survey",
            commit=True,
        )


# ---------------------------------------------------------------------------
# 5. Provenance Persistence and Classification
# ---------------------------------------------------------------------------

def test_provenance_persistence_and_classification(mem_db: Session):
    """Verify field-level provenance records all verification statuses and metadata."""
    reg = VesselRegistry(mem_db)
    v, _, provs = reg.upsert_vessel(
        {
            "imo": "9766944",
            "mmsi": "563004800",
            "vessel_name": "IRIS BLISS",
            "vessel_class": "Kamsarmax",
            "dwt_tonnage": 82103.0,
            "gross_tonnage": 44047.0,
            "fields_provenance": {
                "dwt_tonnage": {
                    "status": VerificationStatus.DIRECTLY_VERIFIED,
                    "source_name": "ClassNK Register",
                    "source_record_id": "ClassNK-162819",
                    "source_url": "https://www.classnk.or.jp",
                },
                "vessel_class": {
                    "status": VerificationStatus.INFERRED,
                    "source_name": "dimensional_heuristics",
                },
            },
        },
        default_source_name="test_feed",
    )

    saved_provs = (
        mem_db.query(VesselAttributeProvenance)
        .filter(VesselAttributeProvenance.vessel_id == v.id)
        .all()
    )
    assert len(saved_provs) >= 4

    dwt_p = next(p for p in saved_provs if p.attribute_name == "dwt_tonnage")
    assert dwt_p.verification_status == VerificationStatus.DIRECTLY_VERIFIED
    assert dwt_p.source_name == "ClassNK Register"
    assert dwt_p.source_record_id == "ClassNK-162819"
    assert float(dwt_p.normalized_value) == 82103.0
    assert dwt_p.is_conflicting is False

    class_p = next(p for p in saved_provs if p.attribute_name == "vessel_class")
    assert class_p.verification_status == VerificationStatus.INFERRED


# ---------------------------------------------------------------------------
# 6. Field-Specific Conflict Retention and Selection
# ---------------------------------------------------------------------------

def test_field_specific_conflict_retention_and_selection(mem_db: Session):
    """Verify contradictory observations are preserved and resolved deterministically."""
    reg = VesselRegistry(mem_db)

    # Ingest first observation: Source A reports DWT 82,103 MT (DIRECTLY_VERIFIED)
    v, _, _ = reg.upsert_vessel(
        {
            "imo": "9766944",
            "mmsi": "563004800",
            "vessel_name": "IRIS BLISS",
            "dwt_tonnage": 82103.0,
            "fields_provenance": {
                "dwt_tonnage": {
                    "status": VerificationStatus.DIRECTLY_VERIFIED,
                    "source_name": "ClassNK",
                    "source_record_id": "NK-1",
                }
            },
        }
    )

    # Ingest second contradictory observation: Source B reports DWT 63,166 MT (SOURCE_REPORTED - genuine conflict)
    v2, created2, _ = reg.upsert_vessel(
        {
            "imo": "9766944",
            "dwt_tonnage": 63166.0,
            "fields_provenance": {
                "dwt_tonnage": {
                    "status": VerificationStatus.SOURCE_REPORTED,
                    "source_name": "SecondaryAggregator",
                    "source_record_id": "SA-1",
                }
            },
        }
    )
    assert created2 is False
    assert v2.id == v.id

    # 1. Both raw observations are preserved (never overwritten)
    all_dwt_provs = (
        mem_db.query(VesselAttributeProvenance)
        .filter(
            VesselAttributeProvenance.vessel_id == v.id,
            VesselAttributeProvenance.attribute_name == "dwt_tonnage",
        )
        .all()
    )
    assert len(all_dwt_provs) == 2
    assert any(float(p.normalized_value) == 82103.0 for p in all_dwt_provs)
    assert any(float(p.normalized_value) == 63166.0 for p in all_dwt_provs)

    # 2. Both records are flagged as conflicting
    assert all(p.is_conflicting is True for p in all_dwt_provs)

    # 3. Deterministic hierarchy selected DIRECTLY_VERIFIED (82,103 MT) as canonical
    mem_db.refresh(v)
    assert v.dwt_tonnage == 82103.0


# ---------------------------------------------------------------------------
# 7. Operational Estimate Separation
# ---------------------------------------------------------------------------

def test_operational_estimate_separation(mem_db: Session):
    """Verify engineering estimates are isolated and never alter verified statutory particulars."""
    reg = VesselRegistry(mem_db)
    v, _, _ = reg.upsert_vessel(
        {
            "imo": "9441295",
            "mmsi": "538003929",
            "vessel_name": "EGRET BULKER",
            "dwt_tonnage": 57809.0,
            "draft_max_meters": 12.80,
        }
    )

    # Record operational engineering estimate
    est = reg.record_operational_estimate(
        vessel_id=v.id,
        model_name="imo_4th_ghg_admiralty",
        model_version="2020.1",
        eco_speed_knots=12.5,
        laden_consumption_mt_per_day=26.4,
        ballast_consumption_mt_per_day=22.1,
        port_idle_consumption_mt_per_day=2.0,
        formula_reference="IMO 4th GHG Study Table 2.14 / Annex 4",
        assumptions="Admiralty coefficient Cadm=480; HFO SFOC=185 g/kWh",
        commit=True,
    )

    assert est.id is not None
    assert est.vessel_id == v.id

    # Canonical statutory particulars remain strictly untouched
    mem_db.refresh(v)
    assert v.dwt_tonnage == 57809.0
    assert v.draft_max_meters == 12.80
    assert len(v.operational_estimates) == 1
    assert v.operational_estimates[0].laden_consumption_mt_per_day == 26.4


# ---------------------------------------------------------------------------
# 8. Seed Ingestion Idempotency
# ---------------------------------------------------------------------------

def test_seed_ingestion_idempotency(mem_db: Session):
    """Verify loading seed_bulk_vessels.json repeatedly is completely idempotent."""
    reg = VesselRegistry(mem_db)

    # Run 1: initial ingestion
    res1 = reg.load_seed_file()
    assert res1["total_candidates"] == 6
    assert res1["created_count"] == 6
    assert res1["updated_count"] == 0
    assert res1["provenance_records_recorded"] == 66

    assert mem_db.query(Vessel).count() == 6
    assert mem_db.query(VesselAttributeProvenance).count() == 66
    assert mem_db.query(VesselMmsiHistory).count() == 6

    # Run 2: repeated ingestion
    res2 = reg.load_seed_file()
    assert res2["total_candidates"] == 6
    assert res2["created_count"] == 0
    assert res2["updated_count"] == 6
    assert res2["provenance_records_recorded"] == 66

    # Assert no duplicate vessels or provenance records
    assert mem_db.query(Vessel).count() == 6
    assert mem_db.query(VesselAttributeProvenance).count() == 66
    assert mem_db.query(VesselMmsiHistory).count() == 6


# ---------------------------------------------------------------------------
# 9. Preservation of Existing Vessel & AIS Relationships
# ---------------------------------------------------------------------------

def test_preservation_of_existing_vessel_and_ais_relationships(mem_db: Session):
    """Ensure existing legacy AIS positions and vessel references continue to function."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # Create legacy AIS position
    pos = VesselPosition(
        mmsi="367000111",
        imo="9441295",
        vessel_name="EGRET BULKER",
        vessel_type="Cargo",
        vessel_type_code=70,
        latitude=46.12,
        longitude=-123.10,
        speed_knots=12.2,
        length_meters=189.99,
        width_meters=32.30,
        draft_meters=12.80,
        observation_time=now_utc,
        source_name="NOAA MarineCadastre",
        license_type="Public Domain",
        idempotency_key="ais:367000111:2026-11-13",
    )
    mem_db.add(pos)
    mem_db.commit()

    # Load registry
    reg = VesselRegistry(mem_db)
    v, _, _ = reg.upsert_vessel(
        {
            "imo": "9441295",
            "mmsi": "367000111",
            "vessel_name": "EGRET BULKER",
            "dwt_tonnage": 57809.0,
        }
    )

    # Check synonym vessel_id matches id
    assert v.vessel_id == v.id
    by_synonym = mem_db.query(Vessel).filter(Vessel.vessel_id == v.id).first()
    assert by_synonym is not None
    assert by_synonym.mmsi == "367000111"


# ---------------------------------------------------------------------------
# 10. Verified vs Unverified DWT Matching
# ---------------------------------------------------------------------------

def test_verified_vs_unverified_dwt_matching(mem_db: Session):
    """Verify only verified DWT satisfies cargo capacity checks; unverified DWT falls back to dimensional."""
    reg = VesselRegistry(mem_db)

    # Vessel A: Verified DWT = 57,809 MT
    va, _, _ = reg.upsert_vessel(
        {
            "imo": "9441295",
            "mmsi": "538003929",
            "vessel_name": "EGRET BULKER",
            "length_meters": 189.99,
            "width_meters": 32.30,
            "dwt_tonnage": 57809.0,
            "fields_provenance": {
                "dwt_tonnage": {
                    "status": VerificationStatus.DIRECTLY_VERIFIED,
                    "source_name": "ClassNK",
                }
            },
        }
    )

    # Vessel B: Unverified DWT = 57,809 MT (INFERRED)
    vb, _, _ = reg.upsert_vessel(
        {
            "imo": "9713430",
            "mmsi": "356885000",
            "vessel_name": "UNVERIFIED SHIP",
            "length_meters": 189.99,
            "width_meters": 32.30,
            "dwt_tonnage": 57809.0,
            "fields_provenance": {
                "dwt_tonnage": {
                    "status": VerificationStatus.INFERRED,
                    "source_name": "heuristics",
                }
            },
        }
    )

    pos_dummy = VesselPosition(
        mmsi="538003929",
        vessel_name="Dummy",
        vessel_type="Cargo",
        vessel_type_code=70,
        latitude=46.0,
        longitude=-124.0,
        length_meters=189.99,
        width_meters=32.30,
        draft_meters=12.0,
        observation_time=datetime.datetime.now(datetime.timezone.utc),
        source_name="test",
        license_type="test",
        idempotency_key="dummy_pos_1",
    )

    # Match Vessel A for 52,000 MT: verified DWT check passes
    match_a = evaluate_vessel_suitability(
        vessel=pos_dummy,
        target_cargo_tonnes=52000.0,
        load_port_coords=(46.0, -124.0),
        registry_vessel=va,
    )
    assert match_a.cargo_capacity_status == "VERIFIED"
    assert match_a.verified_cargo_capacity_dwt == 57809.0
    assert match_a.cargo_compatibility.capacity_check_basis == "verified_dwt"

    # Match Vessel A for 60,000 MT: verified DWT mismatch
    match_a_exceed = evaluate_vessel_suitability(
        vessel=pos_dummy,
        target_cargo_tonnes=60000.0,
        load_port_coords=(46.0, -124.0),
        registry_vessel=va,
    )
    assert match_a_exceed.suitability_status == SuitabilityStatus.DIMENSION_MISMATCH
    assert "Verified deadweight tonnage" in match_a_exceed.suitability_reason

    # Match Vessel B for 52,000 MT: inferred DWT is rejected, capacity status is UNVERIFIED
    pos_dummy_b = VesselPosition(
        mmsi="356885000",
        vessel_name="Dummy B",
        vessel_type="Cargo",
        vessel_type_code=70,
        latitude=46.0,
        longitude=-124.0,
        length_meters=189.99,
        width_meters=32.30,
        draft_meters=12.0,
        observation_time=datetime.datetime.now(datetime.timezone.utc),
        source_name="test",
        license_type="test",
        idempotency_key="dummy_pos_2",
    )
    match_b = evaluate_vessel_suitability(
        vessel=pos_dummy_b,
        target_cargo_tonnes=52000.0,
        load_port_coords=(46.0, -124.0),
        registry_vessel=vb,
    )
    assert match_b.cargo_capacity_status == "INFERRED"
    assert match_b.verified_cargo_capacity_dwt is None
    assert match_b.cargo_compatibility.capacity_check_basis == "ais_dimensional_envelope_only"


# ---------------------------------------------------------------------------
# 11. Missing Summer Draft and Grain Capacity Behaviour
# ---------------------------------------------------------------------------

def test_missing_summer_draft_and_grain_capacity_behaviour(mem_db: Session):
    """Verify missing summer draft, holds, and grain capacity remain explicit."""
    reg = VesselRegistry(mem_db)
    v, _, _ = reg.upsert_vessel(
        {
            "imo": "9441295",
            "mmsi": "538003929",
            "vessel_name": "EGRET BULKER",
            "dwt_tonnage": 57809.0,
            "draft_max_meters": None,  # Draft missing
        }
    )

    pos = VesselPosition(
        mmsi="538003929",
        vessel_name="EGRET BULKER",
        vessel_type="Cargo",
        vessel_type_code=70,
        latitude=46.0,
        longitude=-124.0,
        length_meters=189.99,
        width_meters=32.30,
        draft_meters=None,
        observation_time=datetime.datetime.now(datetime.timezone.utc),
        source_name="test",
        license_type="test",
        idempotency_key="dummy_pos_draft_none",
    )

    match = evaluate_vessel_suitability(
        vessel=pos,
        target_cargo_tonnes=52000.0,
        load_port_coords=(46.0, -124.0),
        registry_vessel=v,
    )

    # Draft remains None, never fabricated
    assert match.draft_meters is None
    assert "draft_meters" in match.missing_particulars
    # Grain capacity and hold cleanliness remain explicit missing requirements
    assert "grain_cubic_capacity" in match.cargo_compatibility.missing_requirements
    assert "hold_cleanliness" in match.cargo_compatibility.missing_requirements


# ---------------------------------------------------------------------------
# 12. Request-Specific Gate 4 Outcomes
# ---------------------------------------------------------------------------

def test_request_specific_gate_4_outcomes(mem_db: Session):
    """Verify single-vessel voyage assessment cleanly evaluates verified seed vessels."""
    reg = VesselRegistry(mem_db)
    reg.load_seed_file()

    # Assess verified Panamax NAVIOS VICTORY (77,095 DWT) for 65,000 MT USG-JPN voyage via genuine MMSI 370090000
    req = VesselCompatibilityRequest(
        mmsi="370090000",
        cargo_tonnes=65000.0,
        commodity="Agricultural Bulk Grain",
        origin="USMSY",
        destination="JPYOK",
        route_code="USG-JPN-BULK",
    )
    result = assess_vessel_for_voyage(mem_db, req)

    assert result.status == "assessed"
    assert result.candidate is not None
    assert result.candidate.verified_cargo_capacity_dwt == 77095.0
    assert result.candidate.cargo_capacity_status == "VERIFIED"
    assert result.candidate.is_physically_compatible is True
    assert result.candidate.cargo_capacity_passed is True
    assert result.candidate.overall_suitability_passed is True
    assert result.overall_suitability == SuitabilityStatus.POTENTIALLY_SUITABLE
    assert result.candidate.cargo_compatibility.status == "potentially_compatible_unverified_capacity"
    assert any("fits within verified capacity (77,095 DWT)" in r for r in result.candidate.cargo_compatibility.reasons)


# ---------------------------------------------------------------------------
# 13. Attribute-Aware Conflict Classification
# ---------------------------------------------------------------------------

def test_attribute_aware_conflict_distinctions():
    """Verify conflict classification distinguishes rounding, measurement-definition, reporting-period, and genuine conflicts."""
    from pelagos.registry.vessel_registry import classify_attribute_difference

    # 1. Rounding differences (diff <= 0.1% or <= 0.05m)
    c1, cat1, _ = classify_attribute_difference("dwt_tonnage", 57809.0, 57810.0)
    assert c1 is False
    assert cat1 == "rounding_difference"

    c2, cat2, _ = classify_attribute_difference("draft_max_meters", 12.80, 12.82)
    assert c2 is False
    assert cat2 == "rounding_difference"

    # 2. Measurement-definition differences (e.g. 0.1%-1.5% DWT loadlines, LOA vs LBP)
    c3, cat3, _ = classify_attribute_difference("dwt_tonnage", 82103.0, 82198.0)
    assert c3 is False
    assert cat3 == "measurement_definition_difference"

    c4, cat4, _ = classify_attribute_difference("length_meters", 228.99, 222.0)
    assert c4 is False
    assert cat4 == "measurement_definition_difference"

    # 3. Reporting-period differences (re-flagging, renaming, delivery year)
    c5, cat5, _ = classify_attribute_difference("flag_country", "Singapore", "Liberia")
    assert c5 is False
    assert cat5 == "reporting_period_difference"

    c6, cat6, _ = classify_attribute_difference("built_year", 2011, 2012)
    assert c6 is False
    assert cat6 == "reporting_period_difference"

    # 4. Genuine conflicts (divergence > 1.5% DWT, > 1 year build year, incompatible vessel type)
    c7, cat7, _ = classify_attribute_difference("dwt_tonnage", 30420.0, 76602.0)
    assert c7 is True
    assert cat7 == "genuine_conflict"

    c8, cat8, _ = classify_attribute_difference("built_year", 2010, 2015)
    assert c8 is True
    assert cat8 == "genuine_conflict"

    c9, cat9, _ = classify_attribute_difference("vessel_type", "Bulk Carrier", "Crude Oil Tanker")
    assert c9 is True
    assert cat9 == "genuine_conflict"


# ---------------------------------------------------------------------------
# 14. Unresolved Canonical Field on Genuine Unresolvable Conflict
# ---------------------------------------------------------------------------

def test_unresolved_canonical_value_on_unresolvable_conflict(mem_db: Session):
    """Verify that unresolvable genuine conflicts leave the canonical vessel attribute None."""
    reg = VesselRegistry(mem_db)

    # Ingest two conflicting SOURCE_REPORTED observations without authoritative tie-breaker
    v, _, _ = reg.upsert_vessel(
        {
            "imo": "9406087",
            "vessel_name": "HADEEL",
            "dwt_tonnage": 30420.0,
            "fields_provenance": {
                "dwt_tonnage": {
                    "status": VerificationStatus.SOURCE_REPORTED,
                    "source_name": "AggregatorAlpha",
                    "source_record_id": "AA-1",
                }
            },
        }
    )

    v2, _, _ = reg.upsert_vessel(
        {
            "imo": "9406087",
            "dwt_tonnage": 76602.0,  # Massive conflict (30k vs 76k)
            "fields_provenance": {
                "dwt_tonnage": {
                    "status": VerificationStatus.SOURCE_REPORTED,
                    "source_name": "AggregatorBeta",
                    "source_record_id": "AB-1",
                }
            },
        }
    )

    mem_db.refresh(v)
    # The canonical DWT must remain unresolved (None) rather than picking an arbitrary contradictory value
    assert v.dwt_tonnage is None

    # Both observations are preserved in provenance and flagged conflicting
    provs = (
        mem_db.query(VesselAttributeProvenance)
        .filter(
            VesselAttributeProvenance.vessel_id == v.id,
            VesselAttributeProvenance.attribute_name == "dwt_tonnage",
        )
        .all()
    )
    assert len(provs) == 2
    assert all(p.is_conflicting is True for p in provs)


# ---------------------------------------------------------------------------
# 15. Matcher Verification Policy: Accredited vs Unaccredited Sources
# ---------------------------------------------------------------------------

def test_matcher_verification_policy_accredited_vs_unaccredited(mem_db: Session):
    """Verify matcher distinguishes directly verified, accredited source-reported, and unaccredited source-reported DWT."""
    reg = VesselRegistry(mem_db)

    # Vessel 1: Sourced from accredited class register (ClassNK) -> satisfies verified capacity
    v_acc, _, _ = reg.upsert_vessel(
        {
            "imo": "9713430",
            "mmsi": "356885001",
            "vessel_name": "ACCREDITED SHIP",
            "length_meters": 224.98,
            "width_meters": 32.24,
            "dwt_tonnage": 77095.0,
            "fields_provenance": {
                "dwt_tonnage": {
                    "status": VerificationStatus.SOURCE_REPORTED,
                    "source_name": "ClassNK Register of Ships",
                }
            },
        }
    )

    # Vessel 2: Sourced from unaccredited commercial aggregator (ShipSpotting) -> rejected for verified capacity
    v_unacc, _, _ = reg.upsert_vessel(
        {
            "imo": "9753284",
            "mmsi": "232007881",
            "vessel_name": "UNACCREDITED SHIP",
            "length_meters": 179.85,
            "width_meters": 32.00,
            "dwt_tonnage": 38907.0,
            "fields_provenance": {
                "dwt_tonnage": {
                    "status": VerificationStatus.SOURCE_REPORTED,
                    "source_name": "ShipSpotting / HiFleet",
                }
            },
        }
    )

    # Vessel 3: Legacy vessel with no provenance records -> rejected for verified capacity
    v_legacy = Vessel(
        mmsi="999888777",
        imo=None,
        vessel_name="LEGACY SHIP",
        vessel_type="Bulk Carrier",
        dwt_tonnage=57809.0,
        length_meters=189.99,
        width_meters=32.30,
        source_name="unverified_legacy_db",
    )
    mem_db.add(v_legacy)
    mem_db.commit()

    pos = VesselPosition(
        mmsi="999000111",
        vessel_name="Dummy",
        vessel_type="Cargo",
        vessel_type_code=70,
        latitude=46.0,
        longitude=-124.0,
        length_meters=200.0,
        width_meters=32.0,
        observation_time=datetime.datetime.now(datetime.timezone.utc),
        source_name="test",
        license_type="test",
        idempotency_key="dummy_policy_pos",
    )

    # 1. Accredited source-reported DWT passes verified capacity
    m_acc = evaluate_vessel_suitability(
        vessel=pos,
        target_cargo_tonnes=65000.0,
        load_port_coords=(46.0, -124.0),
        registry_vessel=v_acc,
    )
    assert m_acc.cargo_capacity_status == "VERIFIED"
    assert m_acc.verified_cargo_capacity_dwt == 77095.0
    assert m_acc.cargo_compatibility.capacity_check_basis == "verified_dwt"

    # 2. Unaccredited source-reported DWT is rejected; screens by dimensional envelope only
    m_unacc = evaluate_vessel_suitability(
        vessel=pos,
        target_cargo_tonnes=35000.0,
        load_port_coords=(46.0, -124.0),
        registry_vessel=v_unacc,
    )
    assert m_unacc.cargo_capacity_status == "UNVERIFIED"
    assert m_unacc.verified_cargo_capacity_dwt is None
    assert m_unacc.cargo_compatibility.capacity_check_basis == "ais_dimensional_envelope_only"
    assert any("SOURCE_REPORTED from non-accredited source" in r for r in m_unacc.cargo_compatibility.reasons)

    # 3. Legacy vessel without provenance records is rejected from verified capacity
    m_legacy = evaluate_vessel_suitability(
        vessel=pos,
        target_cargo_tonnes=50000.0,
        load_port_coords=(46.0, -124.0),
        registry_vessel=v_legacy,
    )
    assert m_legacy.cargo_capacity_status == "UNVERIFIED"
    assert m_legacy.verified_cargo_capacity_dwt is None
    assert m_legacy.cargo_compatibility.capacity_check_basis == "ais_dimensional_envelope_only"
    assert any("cannot satisfy verified capacity" in r for r in m_legacy.cargo_compatibility.reasons)


# ---------------------------------------------------------------------------
# 16. Source-and-Field Specific Accreditation Matrix
# ---------------------------------------------------------------------------

def test_source_and_field_accreditation_matrix():
    """Verify that source accreditation is strictly attribute-specific.

    - USCG PSIX is accredited for GT, Length, Beam, but NOT DWT or Draft (reports molded depth).
    - EU THETIS-MRV is accredited for GT, but NOT Summer DWT (reports extreme DWT) or Draft.
    - ClassNK / DNV are accredited across all statutory particulars.
    - Secondary scrapers (BalticShipping, VesselFinder) are not accredited for capacity or draft.
    """
    from pelagos.optimization.matcher import (
        is_source_accredited_for_attribute,
        is_authoritative_for_capacity,
    )

    # USCG PSIX attribute accreditation
    assert is_source_accredited_for_attribute("USCG PSIX", "gross_tonnage") is True
    assert is_source_accredited_for_attribute("USCG PSIX", "length_meters") is True
    assert is_source_accredited_for_attribute("USCG PSIX", "width_meters") is True
    assert is_source_accredited_for_attribute("USCG PSIX", "dwt_tonnage") is False
    assert is_source_accredited_for_attribute("USCG PSIX", "draft_max_meters") is False
    assert is_authoritative_for_capacity("USCG PSIX") is False

    # EU THETIS-MRV attribute accreditation
    assert is_source_accredited_for_attribute("EU THETIS-MRV", "gross_tonnage") is True
    assert is_source_accredited_for_attribute("EU THETIS-MRV", "dwt_tonnage") is False
    assert is_source_accredited_for_attribute("EU THETIS-MRV", "draft_max_meters") is False
    assert is_authoritative_for_capacity("EU THETIS-MRV") is False

    # ClassNK and DNV attribute accreditation
    for soc in ["ClassNK Register of Ships", "DNV Maritime", "Lloyd's Register", "American Bureau of Shipping"]:
        assert is_source_accredited_for_attribute(soc, "dwt_tonnage") is True
        assert is_source_accredited_for_attribute(soc, "draft_max_meters") is True
        assert is_source_accredited_for_attribute(soc, "gross_tonnage") is True
        assert is_source_accredited_for_attribute(soc, "length_meters") is True
        assert is_source_accredited_for_attribute(soc, "width_meters") is True
        assert is_authoritative_for_capacity(soc) is True

    # Secondary directories are unaccredited
    for sec in ["BalticShipping", "VesselFinder", "MaritimeOptima", "ShipSpotting"]:
        assert is_source_accredited_for_attribute(sec, "dwt_tonnage") is False
        assert is_source_accredited_for_attribute(sec, "draft_max_meters") is False
        assert is_authoritative_for_capacity(sec) is False


# ---------------------------------------------------------------------------
# 17. Same-Authority Unresolved Conflict Results in Null Canonical Value
# ---------------------------------------------------------------------------

def test_same_authority_unresolved_conflict_results_in_null_canonical(mem_db: Session):
    """Verify that contradictory observations from the same authority level result in null canonical."""
    reg = VesselRegistry(mem_db)

    # Ingest two conflicting ClassNK reports (e.g. 77,095 MT vs 82,103 MT)
    v, _, _ = reg.upsert_vessel(
        {
            "imo": "9713430",
            "vessel_name": "NAVIOS VICTORY",
            "dwt_tonnage": 77095.0,
            "fields_provenance": {
                "dwt_tonnage": {
                    "status": VerificationStatus.SOURCE_REPORTED,
                    "source_name": "ClassNK Survey A",
                    "source_record_id": "CNK-A",
                }
            },
        }
    )

    v2, _, _ = reg.upsert_vessel(
        {
            "imo": "9713430",
            "dwt_tonnage": 82103.0,  # Genuine conflict between two preferred class survey reports
            "fields_provenance": {
                "dwt_tonnage": {
                    "status": VerificationStatus.SOURCE_REPORTED,
                    "source_name": "ClassNK Survey B",
                    "source_record_id": "CNK-B",
                }
            },
        }
    )

    mem_db.refresh(v)
    # Because both observations come from preferred class sources, neither can break the tie authoritatively
    # Canonical value must be None and status must be CONFLICTING
    assert v.dwt_tonnage is None

    provs = (
        mem_db.query(VesselAttributeProvenance)
        .filter(
            VesselAttributeProvenance.vessel_id == v.id,
            VesselAttributeProvenance.attribute_name == "dwt_tonnage",
        )
        .all()
    )
    assert len(provs) == 2
    assert all(p.is_conflicting is True for p in provs)


# ---------------------------------------------------------------------------
# 18. Distinct Definitions Retained Separately in Provenance
# ---------------------------------------------------------------------------

def test_distinct_definitions_retained_separately_without_conflict(mem_db: Session):
    """Verify that Summer DWT and Extreme DWT are retained as distinct definition observations."""
    reg = VesselRegistry(mem_db)

    # Observation 1: ClassNK Summer DWT = 82,103 MT (DIRECTLY_VERIFIED)
    v, _, _ = reg.upsert_vessel(
        {
            "imo": "9766944",
            "vessel_name": "IRIS BLISS",
            "dwt_tonnage": 82103.0,
            "fields_provenance": {
                "dwt_tonnage": {
                    "status": VerificationStatus.DIRECTLY_VERIFIED,
                    "source_name": "ClassNK Register of Ships",
                    "source_record_id": "ClassNK-162819",
                }
            },
        }
    )

    # Observation 2: EU THETIS-MRV Extreme DWT = 82,198 MT (SOURCE_REPORTED, 0.11% diff)
    v2, _, _ = reg.upsert_vessel(
        {
            "imo": "9766944",
            "dwt_tonnage": 82198.0,
            "fields_provenance": {
                "dwt_tonnage": {
                    "status": VerificationStatus.SOURCE_REPORTED,
                    "source_name": "EU THETIS-MRV 2023",
                    "source_record_id": "MRV-9766944",
                }
            },
        }
    )

    mem_db.refresh(v)
    # Canonical remains ClassNK Summer DWT (82,103 MT)
    assert v.dwt_tonnage == 82103.0

    provs = (
        mem_db.query(VesselAttributeProvenance)
        .filter(
            VesselAttributeProvenance.vessel_id == v.id,
            VesselAttributeProvenance.attribute_name == "dwt_tonnage",
        )
        .all()
    )
    assert len(provs) == 2
    # Neither observation is marked conflicting because the difference is a recognized measurement-definition variance
    assert all(p.is_conflicting is False for p in provs)
    raw_vals = {p.raw_value for p in provs}
    assert "82103.0" in raw_vals
    assert "82198.0" in raw_vals


# ---------------------------------------------------------------------------
# 19. Cross-Vessel MMSI Overlap Rejection
# ---------------------------------------------------------------------------

def test_cross_vessel_mmsi_overlap_rejection(mem_db: Session):
    """Verify that assigning an MMSI with overlapping intervals to different vessels raises OverlappingMmsiError."""
    from pelagos.registry.vessel_registry import OverlappingMmsiError
    reg = VesselRegistry(mem_db)

    # Vessel 1: NAVIOS VICTORY registered with active open-ended MMSI 356885000
    v1, _, _ = reg.upsert_vessel(
        {
            "imo": "9713430",
            "mmsi": "356885000",
            "vessel_name": "NAVIOS VICTORY",
        }
    )

    # Vessel 2: HADEEL attempts to claim concurrent open-ended MMSI 356885000
    with pytest.raises(OverlappingMmsiError) as exc_info:
        reg.record_mmsi(
            vessel_id=999,  # distinct vessel
            mmsi="356885000",
            valid_from=None,
            valid_to=None,
        )
    assert "already actively assigned to vessel" in str(exc_info.value) or "conflict" in str(exc_info.value).lower()

    # Vessel 2 attempts to claim MMSI 356885000 with overlapping bounded interval
    with pytest.raises(OverlappingMmsiError):
        reg.record_mmsi(
            vessel_id=999,
            mmsi="356885000",
            valid_from="2022-01-01T00:00:00Z",
            valid_to="2025-01-01T00:00:00Z",
        )


# ---------------------------------------------------------------------------
# 20. Valid Sequential MMSI Reassignment
# ---------------------------------------------------------------------------

def test_valid_sequential_mmsi_reassignment(mem_db: Session):
    """Verify that legitimate sequential MMSI reassignment across vessels is permitted."""
    reg = VesselRegistry(mem_db)

    # Vessel 1: decommissioned or renamed, previous MMSI 352111222 valid until 2021-12-31
    v1, _, _ = reg.upsert_vessel(
        {
            "imo": "9406087",
            "mmsi": "352999999",
            "vessel_name": "PRIOR VESSEL",
        }
    )
    reg.record_mmsi(
        vessel_id=v1.id,
        mmsi="352111222",
        valid_from="2010-01-01T00:00:00Z",
        valid_to="2021-12-31T23:59:59Z",
        commit=True,
    )

    # Vessel 2: newly commissioned vessel legitimately assigned MMSI 352111222 starting 2022-01-01
    v2, _, _ = reg.upsert_vessel(
        {
            "imo": "9713430",
            "mmsi": "352111222",
            "valid_from": "2022-01-01T00:00:00Z",
            "valid_to": None,
            "vessel_name": "NEW VESSEL",
        }
    )

    # Verify both vessels exist independently and hold non-overlapping sequential MMSI records
    assert v1.id != v2.id
    h1 = (
        mem_db.query(VesselMmsiHistory)
        .filter(VesselMmsiHistory.vessel_id == v1.id, VesselMmsiHistory.mmsi == "352111222")
        .first()
    )
    h2 = (
        mem_db.query(VesselMmsiHistory)
        .filter(VesselMmsiHistory.vessel_id == v2.id, VesselMmsiHistory.mmsi == "352111222")
        .first()
    )
    assert h1 is not None
    assert h2 is not None
    assert h1.valid_to is not None
    assert h2.valid_from is not None
    assert h1.valid_to < h2.valid_from


# ---------------------------------------------------------------------------
# 21. Six Seed Bulk Vessels Matcher Outcomes Matrix
# ---------------------------------------------------------------------------

def test_six_seed_vessels_matcher_matrix(mem_db: Session):
    """Verify request-specific matcher outcomes across all six seeded vessels.

    - Accredited IACS DWT (ClassNK) satisfies verified capacity:
        * NAVIOS VICTORY (77,095 DWT) -> VERIFIED
        * IRIS BLISS (82,103 DWT) -> VERIFIED
    - Unaccredited SOURCE_REPORTED DWT is rejected from verified capacity:
        * EGRET BULKER (57,809 DWT from BalticShipping) -> UNVERIFIED
        * BERGE GALDHOPIGGEN (38,907 DWT from ShipSpotting) -> UNVERIFIED
        * HADEEL (30,420 DWT from VesselFinder) -> UNVERIFIED
        * BULK UNITY (205,616 DWT from EU MRV extreme DWT) -> UNVERIFIED
    - All vessels explicitly flag missing grain capacity and hold cleanliness.
    """
    reg = VesselRegistry(mem_db)
    reg.load_seed_file()

    expected_outcomes = {
        "9713430": ("VERIFIED", 77095.0),    # NAVIOS VICTORY - ClassNK
        "9766944": ("VERIFIED", 82103.0),    # IRIS BLISS - ClassNK
        "9441295": ("UNVERIFIED", None),     # EGRET BULKER - BalticShipping
        "9753284": ("UNVERIFIED", None),     # BERGE GALDHOPIGGEN - ShipSpotting
        "9406087": ("UNVERIFIED", None),     # HADEEL - VesselFinder
        "9591301": ("UNVERIFIED", None),     # BULK UNITY - EU MRV Extreme DWT
    }

    now_utc = datetime.datetime.now(datetime.timezone.utc)

    for imo, (exp_cap_status, exp_verified_dwt) in expected_outcomes.items():
        v = reg.resolve_canonical_vessel(imo=imo)
        assert v is not None, f"Vessel IMO {imo} must resolve from seed registry"

        pos = VesselPosition(
            mmsi=v.mmsi,
            imo=v.imo,
            vessel_name=v.vessel_name,
            vessel_type=v.vessel_type,
            vessel_type_code=v.vessel_type_code or 70,
            latitude=29.0,
            longitude=-94.0,
            length_meters=v.length_meters,
            width_meters=v.width_meters,
            draft_meters=v.draft_max_meters,
            observation_time=now_utc,
            source_name="test_fixture",
            license_type="public_domain",
            idempotency_key=f"fixture_pos_{imo}",
        )

        target_cargo = (v.dwt_tonnage or 50000.0) * 0.85
        match = evaluate_vessel_suitability(
            vessel=pos,
            target_cargo_tonnes=target_cargo,
            load_port_coords=(29.0, -94.0),
            registry_vessel=v,
        )

        assert match.cargo_capacity_status == exp_cap_status, (
            f"Vessel {v.vessel_name} (IMO {imo}) expected capacity status {exp_cap_status}, "
            f"got {match.cargo_capacity_status}"
        )
        assert match.verified_cargo_capacity_dwt == exp_verified_dwt, (
            f"Vessel {v.vessel_name} (IMO {imo}) expected verified DWT {exp_verified_dwt}, "
            f"got {match.verified_cargo_capacity_dwt}"
        )

        # Physical compatibility vs cargo capacity separation verification
        assert match.is_physically_compatible is True, (
            f"Vessel {v.vessel_name} (IMO {imo}) physical envelope must pass for its class parcel"
        )
        if exp_cap_status == "VERIFIED":
            assert match.cargo_capacity_passed is True
            assert match.overall_suitability_passed is True
            assert match.overall_suitability == SuitabilityStatus.POTENTIALLY_SUITABLE
            assert match.suitability_status == SuitabilityStatus.POTENTIALLY_SUITABLE
            assert match.overall_suitability_status == "physically_compatible_verified_capacity_passed"
        else:
            assert match.cargo_capacity_passed is False
            assert match.overall_suitability_passed is False
            assert match.overall_suitability == SuitabilityStatus.UNVERIFIED
            assert match.suitability_status == SuitabilityStatus.POTENTIALLY_SUITABLE
            assert match.overall_suitability_status == "physically_compatible_unverified_capacity"

        # Grain capacity and hold cleanliness must remain explicit missing requirements across all vessels
        assert "grain_cubic_capacity" in match.cargo_compatibility.missing_requirements
        assert "hold_cleanliness" in match.cargo_compatibility.missing_requirements


# ---------------------------------------------------------------------------
# 22. Bulk Unity 180,000 MT Fail-Closed Separation
# ---------------------------------------------------------------------------

def test_bulk_unity_180k_fail_closed_separation(mem_db: Session):
    """Verify BULK UNITY (IMO 9591301) for 180,000 MT parcel:
    - Physical envelope (LOA 299.9m, Beam 50m) passes as physically compatible.
    - Cargo capacity remains strictly UNVERIFIED (EU MRV extreme DWT is unaccredited).
    - Overall vessel suitability fails closed (UNVERIFIED), never a passing outcome.
    """
    reg = VesselRegistry(mem_db)
    reg.load_seed_file()

    v = reg.resolve_canonical_vessel(imo="9591301")
    assert v is not None
    assert v.vessel_name == "BULK UNITY"
    assert v.mmsi == "636021578"

    pos = VesselPosition(
        mmsi=v.mmsi,
        imo=v.imo,
        vessel_name=v.vessel_name,
        vessel_type=v.vessel_type,
        vessel_type_code=70,
        latitude=-20.0,
        longitude=118.5,
        length_meters=v.length_meters,
        width_meters=v.width_meters,
        draft_meters=v.draft_max_meters,
        observation_time=datetime.datetime.now(datetime.timezone.utc),
        source_name="test_fixture",
        license_type="public_domain",
        idempotency_key="fixture_bulk_unity_180k",
    )

    match = evaluate_vessel_suitability(
        vessel=pos,
        target_cargo_tonnes=180000.0,
        load_port_coords=(-20.3, 118.57),  # Port Hedland
        registry_vessel=v,
    )

    # 1. Physical compatibility passes: Capesize/Newcastlemax envelope fits 180,000 MT
    assert match.is_physically_compatible is True
    assert match.estimated_dimensional_class == "Post-Panamax / Capesize"

    # 2. Cargo capacity remains unverified due to unaccredited EU MRV extreme DWT source
    assert match.cargo_capacity_status == "UNVERIFIED"
    assert match.cargo_capacity_passed is False
    assert match.verified_cargo_capacity_dwt is None

    # 3. Overall vessel suitability fails closed: must not pass
    assert match.overall_suitability_passed is False
    assert match.overall_suitability == SuitabilityStatus.UNVERIFIED
    assert match.overall_suitability_status == "physically_compatible_unverified_capacity"


# ---------------------------------------------------------------------------
# 23. Verified Cargo Capacity Exceeded Separation
# ---------------------------------------------------------------------------

def test_verified_capacity_exceeded_separation(mem_db: Session):
    """Verify NAVIOS VICTORY (IMO 9713430, 77,095 DWT ClassNK verified):
    - When evaluated for 80,000 MT: physical dimensions fit Panamax, but verified DWT is exceeded.
    - Result: physically compatible=True, cargo_capacity_passed=False, overall=DIMENSION_MISMATCH.
    """
    reg = VesselRegistry(mem_db)
    reg.load_seed_file()

    v = reg.resolve_canonical_vessel(imo="9713430")
    assert v is not None

    pos = VesselPosition(
        mmsi=v.mmsi,
        imo=v.imo,
        vessel_name=v.vessel_name,
        vessel_type=v.vessel_type,
        vessel_type_code=70,
        latitude=29.0,
        longitude=-94.0,
        length_meters=v.length_meters,
        width_meters=v.width_meters,
        draft_meters=v.draft_max_meters,
        observation_time=datetime.datetime.now(datetime.timezone.utc),
        source_name="test_fixture",
        license_type="public_domain",
        idempotency_key="fixture_navios_80k",
    )

    match = evaluate_vessel_suitability(
        vessel=pos,
        target_cargo_tonnes=80000.0,
        load_port_coords=(29.0, -94.0),
        registry_vessel=v,
    )

    # Physical envelope fits Panamax parcel, but verified DWT (77,095 MT) < 80,000 MT
    assert match.is_physically_compatible is True
    assert match.cargo_capacity_status == "VERIFIED"
    assert match.cargo_capacity_passed is False
    assert match.overall_suitability_passed is False
    assert match.overall_suitability == SuitabilityStatus.DIMENSION_MISMATCH
    assert match.suitability_status == SuitabilityStatus.DIMENSION_MISMATCH
    assert match.overall_suitability_status == "physically_compatible_capacity_exceeded"


# ---------------------------------------------------------------------------
# 24. Seed Fixture MMSI Contamination Purged
# ---------------------------------------------------------------------------

def test_seed_fixture_mmsi_contamination_purged():
    """Verify mock MMSI 356885000 (SANTYA) is completely purged from seed_bulk_vessels.json,
    and genuine MMSIs are assigned with 0 artificial historical intervals.
    """
    import json
    from pathlib import Path

    seed_path = Path(__file__).resolve().parent.parent / "backend" / "pelagos" / "data" / "seed_bulk_vessels.json"
    with open(seed_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    expected_mmsis = {
        "9441295": "538003818",  # EGRET BULKER (Marshall Islands)
        "9713430": "370090000",  # NAVIOS VICTORY (Panama)
        "9766944": "566448000",  # IRIS BLISS (Singapore)
        "9753284": "232018048",  # BERGE GALDHOPIGGEN (UK/Isle of Man)
        "9406087": "352004129",  # HADEEL (Panama)
        "9591301": "636021578",  # BULK UNITY (Liberia)
    }

    assert len(data) == 6
    for item in data:
        imo = item["imo"]
        assert imo in expected_mmsis
        assert item["mmsi"] == expected_mmsis[imo]
        # No mock MMSI 356885000 present
        assert item["mmsi"] != "356885000"
        # No artificial historical intervals
        assert item["historical_mmsis"] == []

    # Verify IRIS BLISS flag is Singapore
    iris = next(item for item in data if item["imo"] == "9766944")
    assert iris["flag_country"] == "Singapore"
    assert iris["flag_code"] == "SG"



