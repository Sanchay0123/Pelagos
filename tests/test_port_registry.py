"""Tests for Pelagos Port, Terminal, Berth, Channel & Navigational Restriction Registry (Stage F1.3.2).

Validates:
1. Canonical port preservation and deterministic external identifier crosswalk resolution.
2. Crosswalk metadata, match methods, and confidence scoring.
3. Idempotent seed ingestion across terminals, discrete berths, channel reaches, and restrictions.
4. Robust numeric parsing of USACE string dimensions and metric conversions.
5. Vertical datum preservation (CRD, MLLW, MLT, CD, LAT, NAVD88).
6. Channel maintained depth vs authorized depth vs controlling shoaling distinction.
7. Static infrastructure air draft limits vs live dynamic sensor air gaps.
8. Domain-specific freshness policy horizons and staleness detection.
9. Missing berth data yields UNVERIFIED (never confirmed incompatibility!).
10. Confirmed draft exceedance yields INSUITABLE.
11. Under-Keel Clearance (UKC) channel reach draft addition.
12. Strict independence of Cargo DWT capacity verification from port physical compatibility.
13. Indian Port Gazettes particulars (Paradip, Visakhapatnam, JNPA, SMP Kolkata).
14. Canal transit bottlenecks (Panama Canal Neopanamax vs Panamax, Suez Canal).
"""

from __future__ import annotations

import datetime
from typing import Generator
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from pelagos.database import init_db
from pelagos.ingestion.ports_ingest import ingest_ports_from_fixture
from pelagos.models import (
    Berth,
    ChannelReach,
    FreshnessPolicy,
    NavigationalRestriction,
    Port,
    PortCrosswalk,
    RestrictionScope,
    RestrictionType,
    Terminal,
    VerificationStatus,
    Vessel,
    VesselPosition,
)
from pelagos.optimization.matcher import (
    evaluate_port_compatibility,
    evaluate_route_compatibility,
    evaluate_vessel_suitability,
    match_suitable_vessels,
)
from pelagos.optimization.schemas import (
    SuitabilityStatus,
    VesselCandidateMatch,
    VesselSuitabilityRequest,
)
from pelagos.registry.port_registry import (
    FRESHNESS_HORIZONS_HOURS,
    PortRegistry,
    convert_to_meters,
    is_restriction_fresh,
    parse_dimension_value,
)


@pytest.fixture
def mem_db() -> Generator[Session, None, None]:
    """Isolated in-memory database initialized with Pelagos schema, 43 ports, and port restriction registry seed."""
    eng = create_engine("sqlite:///:memory:")
    init_db(eng)
    SessionLocal = sessionmaker(bind=eng)
    session = SessionLocal()

    # Populate canonical 43 ports
    ingest_ports_from_fixture(session)

    # Ingest Stage F1.3.2 seed dataset
    registry = PortRegistry(session)
    registry.ingest_ports_restrictions_seed()

    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 1. Canonical Port Preservation & Deterministic Crosswalk Resolution
# ---------------------------------------------------------------------------

def test_canonical_port_preservation_and_crosswalk_resolution(mem_db: Session):
    """Verify external identifiers resolve to canonical ports without duplicate port creation."""
    registry = PortRegistry(mem_db)

    # USACE 3-letter codes that differ from UN/LOCODE
    resolutions = [
        ("KAM", "USACE_DOCKS", "USKAL", "Kalama"),
        ("TIW", "USACE_DOCKS", "USTAC", "Tacoma"),
        ("GLS", "USACE_DOCKS", "USGAL", "Galveston"),
        ("LOG", "USACE_DOCKS", "USLWW", "Longview"),
        ("SEA", "USACE_DOCKS", "USSEA", "Seattle"),
        ("PDX", "USACE_DOCKS", "USPDX", "Portland (OR)"),
        ("HOU", "USACE_DOCKS", "USHOU", "Houston"),
        ("MSY", "USACE_DOCKS", "USMSY", "New Orleans"),
        ("WWO", "USACE_DOCKS", "USMSY", "New Orleans"),  # Westwego -> New Orleans complex
        ("8762002", "NOAA_PORTS", "USMSY", "New Orleans"),  # Huey P Long bridge gauge
        ("PAPCN", "CANAL_CROSSWALK", "PAPAC", "Balboa (Panama Pacific)"),
        ("EGSUZ", "CANAL_CROSSWALK", "EGPSD", "Port Said (Suez North)"),
    ]

    initial_port_count = mem_db.query(Port).count()
    assert initial_port_count == 43

    for src_id, src_sys, expected_unlocode, expected_name_part in resolutions:
        port = registry.resolve_canonical_port(src_id, src_sys)
        assert port is not None, f"Failed to resolve {src_id} from {src_sys}"
        assert port.unlocode == expected_unlocode, f"Expected {expected_unlocode} for {src_id}, got {port.unlocode}"
        assert expected_name_part in port.name

    # Canonical port count must remain strictly 43 (no duplicate ports created)
    assert mem_db.query(Port).count() == initial_port_count


def test_crosswalk_metadata_and_confidence(mem_db: Session):
    """Verify crosswalk entries maintain match methods, confidence scores, and explanatory notes."""
    kam_cw = (
        mem_db.query(PortCrosswalk)
        .filter(PortCrosswalk.source_system == "USACE_DOCKS", PortCrosswalk.source_identifier == "KAM")
        .first()
    )
    assert kam_cw is not None
    assert kam_cw.canonical_unlocode == "USKAL"
    assert kam_cw.match_method == "EXPLICIT_VERIFIED"
    assert kam_cw.confidence_score == 1.0
    assert "Columbia River" in kam_cw.notes


# ---------------------------------------------------------------------------
# 2. Idempotent Ingestion
# ---------------------------------------------------------------------------

def test_idempotent_seed_ingestion(mem_db: Session):
    """Running seed ingestion a second time must produce zero duplicate records."""
    registry = PortRegistry(mem_db)

    cw_before = mem_db.query(PortCrosswalk).count()
    t_before = mem_db.query(Terminal).count()
    b_before = mem_db.query(Berth).count()
    r_before = mem_db.query(ChannelReach).count()
    rest_before = mem_db.query(NavigationalRestriction).count()

    # Second ingestion
    counts = registry.ingest_ports_restrictions_seed()

    assert mem_db.query(PortCrosswalk).count() == cw_before
    assert mem_db.query(Terminal).count() == t_before
    assert mem_db.query(Berth).count() == b_before
    assert mem_db.query(ChannelReach).count() == r_before
    assert mem_db.query(NavigationalRestriction).count() == rest_before


# ---------------------------------------------------------------------------
# 3. Numeric Parsing & Metric Conversion
# ---------------------------------------------------------------------------

def test_numeric_parsing_and_conversions():
    """Verify safe parsing of USACE string numbers, negative chart depths, and unit conversions."""
    assert parse_dimension_value("43.00") == 43.0
    assert parse_dimension_value("45.5 ft") == 45.5
    assert parse_dimension_value("-16.2") == 16.2  # Negative chart depth converted to positive depth
    assert parse_dimension_value(None) is None
    assert parse_dimension_value("N/A") is None
    assert parse_dimension_value("UNKNOWN") is None
    assert parse_dimension_value("") is None

    # Conversion to meters
    assert convert_to_meters(43.0, "feet") == 13.106
    assert convert_to_meters(50.0, "feet") == 15.240
    assert convert_to_meters(16.2, "meters") == 16.200


# ---------------------------------------------------------------------------
# 4. Vertical Datum Preservation
# ---------------------------------------------------------------------------

def test_vertical_datum_preservation(mem_db: Session):
    """Verify vertical datums (CRD, MLLW, MLT, CD, LAT, MLG) are accurately stored on respective entities."""
    # MLLW on Kalama TEMCO (USACE certified survey 40.00 ft MLLW, OBJECTID 16866)
    temco_b = mem_db.query(Berth).filter(Berth.berth_code == "0T84-B1").first()
    assert temco_b is not None
    assert "MLLW" in temco_b.vertical_datum

    # Columbia River Datum (CRD) on Kalama Bar navigation channel
    kalama_reach = mem_db.query(ChannelReach).filter(ChannelReach.reach_code == "CR-KALAMA-BAR").first()
    assert kalama_reach is not None
    assert kalama_reach.vertical_datum == "CRD"

    # MLLW on Seattle Pier 86 & Mississippi River Southwest Pass
    p86_b = mem_db.query(Berth).filter(Berth.berth_code == "P86-MAIN").first()
    assert p86_b.vertical_datum == "MLLW"

    sw_pass = mem_db.query(ChannelReach).filter(ChannelReach.reach_code == "MS-SW-PASS").first()
    assert sw_pass.vertical_datum == "MLLW"

    # Mean Low Tide (MLT) on Houston Ship Channel & Portland Terminal 4 (USACE OBJECTID 11521)
    hsc = mem_db.query(ChannelReach).filter(ChannelReach.reach_code == "HSC-BOL-MP").first()
    assert hsc.vertical_datum == "MLT"

    pdx_t4 = mem_db.query(Berth).filter(Berth.berth_code == "0T78-B414-415").first()
    assert pdx_t4 is not None
    assert "MLT" in pdx_t4.vertical_datum

    # Mean Low Gulf (MLG) on New Orleans Napoleon Avenue Wharf (USACE OBJECTID 8739)
    napoleon_b = mem_db.query(Berth).filter(Berth.berth_code == "0S9H-B1").first()
    assert napoleon_b is not None
    assert "MLG" in napoleon_b.vertical_datum

    # Chart Datum (CD) on Indian ports (Paradip, Vizag, JNPA, SMPK)
    prt_reach = mem_db.query(ChannelReach).filter(ChannelReach.reach_code == "PRT-ENTRANCE-CHNL").first()
    assert prt_reach.vertical_datum == "CD"


# ---------------------------------------------------------------------------
# 5. Maintained vs Authorized Channel Depths
# ---------------------------------------------------------------------------

def test_maintained_vs_authorized_channel_depths(mem_db: Session):
    """Verify maintained and authorized depths are stored distinctly."""
    prt_entrance = mem_db.query(ChannelReach).filter(ChannelReach.reach_code == "PRT-ENTRANCE-CHNL").first()
    assert prt_entrance.depth_maintained_meters == 17.10
    assert prt_entrance.depth_authorized_meters == 18.70

    prt_approach = mem_db.query(ChannelReach).filter(ChannelReach.reach_code == "PRT-APPROACH-CHNL").first()
    assert prt_approach.depth_maintained_meters == 18.70
    assert prt_approach.depth_authorized_meters == 19.00

    kalama = mem_db.query(ChannelReach).filter(ChannelReach.reach_code == "CR-KALAMA-BAR").first()
    assert kalama.depth_maintained_meters == 13.11
    assert kalama.depth_authorized_meters == 13.11


# ---------------------------------------------------------------------------
# 6. Static vs Dynamic Air Gap Clearances
# ---------------------------------------------------------------------------

def test_static_vs_live_air_gap_restrictions(mem_db: Session):
    """Verify static overhead clearances vs live dynamic sensor air gaps."""
    # Static Panama Canal Bridge of the Americas
    bota = mem_db.query(NavigationalRestriction).filter(NavigationalRestriction.scope_name.like("%Americas%")).first()
    assert bota is not None
    assert bota.restriction_type == RestrictionType.AIR_DRAFT_MAX
    assert bota.numeric_limit == 62.50
    assert bota.freshness_policy == FreshnessPolicy.STATIC_INFRASTRUCTURE

    # Live sensor at Huey P Long Bridge (NOAA PORTS)
    huey = mem_db.query(NavigationalRestriction).filter(NavigationalRestriction.scope_name.like("%Huey%")).first()
    assert huey is not None
    assert huey.restriction_type == RestrictionType.AIR_GAP_LIVE
    assert huey.numeric_limit == 46.47
    assert huey.freshness_policy == FreshnessPolicy.LIVE_SENSOR


# ---------------------------------------------------------------------------
# 7. Domain-Specific Freshness Policy Enforcement
# ---------------------------------------------------------------------------

def test_freshness_policy_horizons():
    """Verify domain-specific freshness horizons (LIVE_SENSOR 2h, STATIC 5yr)."""
    assert FRESHNESS_HORIZONS_HOURS[FreshnessPolicy.LIVE_SENSOR] == 2.0
    assert FRESHNESS_HORIZONS_HOURS[FreshnessPolicy.STATIC_INFRASTRUCTURE] == 1825.0 * 24.0

    now = datetime.datetime.now(datetime.timezone.utc)
    one_hour_ago = now - datetime.timedelta(hours=1)
    three_hours_ago = now - datetime.timedelta(hours=3)

    rest_fresh = NavigationalRestriction(
        freshness_policy=FreshnessPolicy.LIVE_SENSOR,
        observation_timestamp=one_hour_ago,
    )
    is_fresh, age_h, max_h = is_restriction_fresh(rest_fresh, as_of_time=now)
    assert is_fresh is True
    assert age_h <= 2.0

    rest_stale = NavigationalRestriction(
        freshness_policy=FreshnessPolicy.LIVE_SENSOR,
        observation_timestamp=three_hours_ago,
    )
    is_fresh, age_h, max_h = is_restriction_fresh(rest_stale, as_of_time=now)
    assert is_fresh is False
    assert age_h > 2.0


# ---------------------------------------------------------------------------
# 8. Missing Data Evaluates to UNVERIFIED (Never INSUITABLE)
# ---------------------------------------------------------------------------

def test_missing_berth_data_evaluates_to_unverified_never_insuitable(mem_db: Session):
    """Missing berth alongside depth evaluates strictly to UNVERIFIED and passed=False, never INSUITABLE."""
    registry = PortRegistry(mem_db)

    # USVAN (Vancouver WA) has no seed berths in this test
    res = registry.evaluate_berth_compatibility(
        port_unlocode="USVAN",
        vessel_draft=11.5,
        vessel_loa=225.0,
        vessel_beam=32.2,
    )

    assert res["passed"] is False
    assert res["status"] == "UNVERIFIED"
    assert "No certified berth records registered" in res["reason"]


def test_missing_channel_data_evaluates_to_unverified(mem_db: Session):
    """Missing channel reach data evaluates strictly to UNVERIFIED, never INSUITABLE."""
    registry = PortRegistry(mem_db)

    res = registry.evaluate_channel_compatibility(
        port_unlocode="USVAN",
        vessel_draft=11.5,
    )

    assert res["passed"] is False
    assert res["status"] == "UNVERIFIED"
    assert "No certified channel reach records registered" in res["reason"]


# ---------------------------------------------------------------------------
# 9. Confirmed Draft Exceedance Evaluates to INSUITABLE
# ---------------------------------------------------------------------------

def test_confirmed_draft_exceedance_evaluates_to_insuitable(mem_db: Session):
    """When vessel draft exceeds certified alongside depth across all berths, status is INSUITABLE."""
    registry = PortRegistry(mem_db)

    # Kalama berths max alongside depth is 13.11m - 13.72m
    # A vessel with draft 14.50m exceeds all berths at Kalama
    res = registry.evaluate_berth_compatibility(
        port_unlocode="USKAL",
        vessel_draft=14.50,
        vessel_loa=225.0,
        vessel_beam=32.2,
    )

    assert res["passed"] is False
    assert res["status"] == "INSUITABLE"
    assert "exceeds certified limits at all" in res["reason"]


# ---------------------------------------------------------------------------
# 10. Channel Clearance with Under-Keel Clearance (UKC)
# ---------------------------------------------------------------------------

def test_channel_clearance_with_ukc(mem_db: Session):
    """Verify that reach-level UKC rules are added to vessel draft."""
    registry = PortRegistry(mem_db)

    # Kalama Bar Reach has maintained depth 13.11m and UKC 0.91m (3 ft).
    # Effective required depth = vessel_draft + 0.91m.
    # Case 1: Draft 12.50m -> 12.50 + 0.91 = 13.41m > 13.11m -> INSUITABLE
    res_fail = registry.evaluate_channel_compatibility(
        port_unlocode="USKAL",
        vessel_draft=12.50,
    )
    assert res_fail["passed"] is False
    assert res_fail["status"] == "INSUITABLE"
    assert "exceeds maintained channel depth" in res_fail["reason"]

    # Case 2: Draft 11.50m -> 11.50 + 0.91 = 12.41m <= 13.11m -> SUITABLE
    res_pass = registry.evaluate_channel_compatibility(
        port_unlocode="USKAL",
        vessel_draft=11.50,
    )
    assert res_pass["passed"] is True
    assert res_pass["status"] == "SUITABLE"


# ---------------------------------------------------------------------------
# 11. Strict Independence of Cargo Capacity DWT from Port Compatibility
# ---------------------------------------------------------------------------

def test_cargo_capacity_dwt_independence_from_port_compatibility(mem_db: Session):
    """Vessel can be physically compatible with port while cargo capacity is unverified or exceeded."""
    registry = PortRegistry(mem_db)

    # Create candidate vessel with dimensions that fit Kalama (LOA 225m, Beam 32.2m, Draft 11.5m)
    vessel = VesselPosition(
        mmsi="999000111",
        imo="9441295",
        vessel_name="TEST BULKER",
        vessel_type="Bulk Carrier",
        vessel_type_code=70,
        length_meters=225.0,
        width_meters=32.2,
        draft_meters=11.5,
        observation_time=datetime.datetime.now(datetime.timezone.utc),
        source_name="TERRESTRIAL_AIS",
    )

    # Case A: Physically compatible, but cargo capacity unverified (DWT unaccredited)
    reg_unverified = Vessel(
        id=999,
        mmsi="999000111",
        imo="9441295",
        vessel_name="TEST BULKER",
        vessel_type="Bulk Carrier",
        length_meters=225.0,
        width_meters=32.2,
        draft_max_meters=11.5,
        dwt_tonnage=55000.0,
        source_name="UNACCREDITED_SCRAPER",
    )

    match_a = evaluate_vessel_suitability(
        vessel=vessel,
        target_cargo_tonnes=52000.0,
        load_port_coords=(46.01, -122.84),
        registry_vessel=reg_unverified,
        port_registry=registry,
    )

    assert match_a.is_physically_compatible is True
    assert match_a.cargo_capacity_passed is False
    assert match_a.overall_suitability_passed is False
    assert match_a.overall_suitability == SuitabilityStatus.UNVERIFIED


# ---------------------------------------------------------------------------
# 12. Indian Port Gazettes Verification
# ---------------------------------------------------------------------------

def test_indian_port_gazettes_particulars(mem_db: Session):
    """Verify certified depths and draft limits for Paradip, Vizag, JNPA, and SMP Kolkata."""
    registry = PortRegistry(mem_db)

    # Paradip: North Quay (14.5m) vs Central Quay (16.0m)
    res_prt_panamax = registry.evaluate_berth_compatibility("INPRT", vessel_draft=14.2)
    assert res_prt_panamax["passed"] is True
    assert res_prt_panamax["suitable_berths_count"] >= 2

    # Visakhapatnam: Capesize Outer Harbour (18.1m) clears 17.5m draft
    res_vtz_cape = registry.evaluate_berth_compatibility("INVTZ", vessel_draft=17.5)
    assert res_vtz_cape["passed"] is True
    assert res_vtz_cape["suitable_berths_count"] >= 1

    # JNPA: BMCT alongside depth 16.2m CD clears 15.0m baseline draft (and clears 15.2m with tide >= 3.0m)
    res_jnpa_base = registry.evaluate_berth_compatibility("INNSA", vessel_draft=15.0)
    assert res_jnpa_base["passed"] is True
    res_jnpa_tide = registry.evaluate_berth_compatibility("INNSA", vessel_draft=15.2, available_tide_height=3.2)
    assert res_jnpa_tide["passed"] is True
    assert res_jnpa_tide["status"] == "CONDITIONALLY_SUITABLE"


# ---------------------------------------------------------------------------
# 13. Canal Bottleneck Evaluations
# ---------------------------------------------------------------------------

def test_canal_bottlenecks(mem_db: Session):
    """Verify Panama Canal Neopanamax and Suez Canal navigation clearance rules."""
    registry = PortRegistry(mem_db)

    # Panama Canal Pacific Gateway (PAPAC) Bridge of the Americas air draft 62.5m
    # Vessel with air draft 60.0m passes; 65.0m fails
    air_pass = registry.evaluate_air_draft_compatibility("PAPAC", vessel_air_draft=60.0)
    assert air_pass["passed"] is True

    air_fail = registry.evaluate_air_draft_compatibility("PAPAC", vessel_air_draft=65.0)
    assert air_fail["passed"] is False
    assert air_fail["status"] == "INSUITABLE"
    assert "exceeds certified clearance" in air_fail["reason"]

    # Suez Canal Gateway (EGPSD) Al Salam Bridge air draft 68.0m
    suez_pass = registry.evaluate_air_draft_compatibility("EGPSD", vessel_air_draft=67.0)
    assert suez_pass["passed"] is True

    suez_fail = registry.evaluate_air_draft_compatibility("EGPSD", vessel_air_draft=69.5)
    assert suez_fail["passed"] is False
    assert suez_fail["status"] == "INSUITABLE"


# ---------------------------------------------------------------------------
# 14. USACE Five Reconciled Discovery Facilities (Stage F1.3.2.1 / F1.3.2.2)
# ---------------------------------------------------------------------------

def test_usace_five_reconciled_facilities(mem_db: Session):
    """Verify field-level reconciliation for all 5 priority USACE Master Docks facilities."""
    # 1. Kalama TEMCO: OBJECTID 16866, NAV_UNIT_ID 0T84
    b_kalama = mem_db.query(Berth).filter(Berth.berth_code == "0T84-B1").first()
    assert b_kalama is not None
    assert b_kalama.source_record_id == "16866"
    assert b_kalama.alongside_depth_min_meters == 12.19  # 40.00 ft
    assert "MLLW" in b_kalama.vertical_datum
    assert b_kalama.berthing_length_source_value == 840.0
    assert b_kalama.is_conflicting is False
    assert b_kalama.conflict_details is None

    # 2. Seattle Pier 46: OBJECTID 11421, NAV_UNIT_ID 0UGR
    b_sea = mem_db.query(Berth).filter(Berth.berth_code == "0UGR-B1").first()
    assert b_sea is not None
    assert b_sea.source_record_id == "11421"
    assert b_sea.alongside_depth_min_meters == 10.97  # 36.00 ft
    assert "MLLW" in b_sea.vertical_datum
    assert b_sea.berthing_length_source_value == 600.0
    assert b_sea.is_conflicting is False

    # 3. Portland Terminal 4: OBJECTID 11521, NAV_UNIT_ID 0T78 (Unified Continuous Face Berths 414 & 415)
    b_pdx = mem_db.query(Berth).filter(Berth.berth_code == "0T78-B414-415").first()
    assert b_pdx is not None
    assert b_pdx.source_record_id == "11521"
    assert b_pdx.alongside_depth_min_meters == 12.19  # 40.00 ft
    assert "MLT" in b_pdx.vertical_datum
    assert b_pdx.berthing_length_source_value == 1344.0
    assert "414 and 415" in b_pdx.berth_name
    assert b_pdx.is_conflicting is False

    # 4. Houston Fuel-Oil Dock 2: OBJECTID 11781, NAV_UNIT_ID 0SUX
    b_hou = mem_db.query(Berth).filter(Berth.berth_code == "0SUX-B1").first()
    assert b_hou is not None
    assert b_hou.source_record_id == "11781"
    assert b_hou.alongside_depth_min_meters == 12.80  # 42.00 ft
    assert "MLT" in b_hou.vertical_datum
    assert b_hou.berthing_length_source_value == 900.0
    assert b_hou.is_conflicting is False

    # 5. Napoleon Avenue Wharf: OBJECTID 8739, NAV_UNIT_ID 0S9H
    b_msy = mem_db.query(Berth).filter(Berth.berth_code == "0S9H-B1").first()
    assert b_msy is not None
    assert b_msy.source_record_id == "8739"
    assert b_msy.alongside_depth_min_meters == 10.67  # 35.00 ft
    assert "MLG" in b_msy.vertical_datum
    assert b_msy.berthing_length_source_value == 1739.0
    assert b_msy.is_conflicting is False
    assert b_msy.conflict_details is None

    # Operational restrictions verification (distinct attributes, non-conflicting)
    kalama_work_draft = (
        mem_db.query(NavigationalRestriction)
        .filter(NavigationalRestriction.port_unlocode == "USKAL", NavigationalRestriction.restriction_type == RestrictionType.DRAFT_MAX)
        .first()
    )
    assert kalama_work_draft is not None
    assert kalama_work_draft.numeric_limit == 13.11
    assert kalama_work_draft.is_conditional is True
    assert kalama_work_draft.is_conflicting is False

    msy_seas_draft = (
        mem_db.query(NavigationalRestriction)
        .filter(NavigationalRestriction.port_unlocode == "USMSY", NavigationalRestriction.restriction_type == RestrictionType.DRAFT_MAX)
        .first()
    )
    assert msy_seas_draft is not None
    assert msy_seas_draft.numeric_limit == 13.72
    assert msy_seas_draft.is_conditional is True
    assert msy_seas_draft.is_conflicting is False


# ---------------------------------------------------------------------------
# 15. Physical Berth Depth Governance & Fail-Closed Semantics
# ---------------------------------------------------------------------------

def test_kalama_and_napoleon_conflict_governance_fail_closed(mem_db: Session):
    """When evaluating physical berth limits, compatibility is governed fail-closed by USACE-reported minimum alongside depth."""
    registry = PortRegistry(mem_db)

    # Kalama TEMCO: USACE-reported minimum alongside depth is 12.19m (40 ft MLLW); commercial claim is 13.11m (43 ft CRD).
    # A vessel with draft 12.80m exceeds alongside depth and must FAIL closed against USACE-reported minimum alongside depth.
    res_kalama = registry.evaluate_berth_compatibility(
        port_unlocode="USKAL",
        vessel_draft=12.80,
    )
    assert res_kalama["passed"] is False
    assert res_kalama["status"] == "INSUITABLE"
    assert any("exceeds alongside depth limit (12.19m" in r for b in res_kalama["berths_evaluated"] for r in b["rejection_reasons"])

    # Napoleon Avenue Wharf: USACE-reported minimum alongside depth is 10.67m (35 ft MLG); river seasonal draft advisory is 13.72m (45 ft).
    # A vessel with draft 12.00m must FAIL closed against the USACE-reported minimum alongside depth.
    b_msy = mem_db.query(Berth).filter(Berth.berth_code == "0S9H-B1").first()
    assert b_msy is not None
    res_napoleon = registry.evaluate_berth_compatibility(
        port_unlocode="USMSY",
        vessel_draft=12.00,
        berth_id=b_msy.id,
    )
    assert res_napoleon["passed"] is False
    assert res_napoleon["status"] == "INSUITABLE"
    assert any("exceeds alongside depth limit (10.67m" in r for b in res_napoleon["berths_evaluated"] for r in b["rejection_reasons"])


# ---------------------------------------------------------------------------
# 16. Indian Port Distinctions (Depths vs Permissible Drafts vs Fairways)
# ---------------------------------------------------------------------------

def test_indian_port_depth_and_draft_separation(mem_db: Session):
    """Verify distinction between physical alongside depth, permissible draft, and channel prisms."""
    registry = PortRegistry(mem_db)

    # Paradip (INPRT):
    # - New Iron Ore / Coal Import Berths (02 & 03): alongside 17.10m CD, permissible draft 16.00m CD, LOA 300.0m, beam 46.0m
    # - Mechanized Coal Berths (05 & 06): alongside 15.00m CD, permissible draft 14.50m CD, LOA 300.0m, beam 46.0m
    # - Central Quay (CQ-1 to CQ-3): alongside 15.00m CD, permissible draft 14.50m CD, LOA 260.0m
    # - Approach Channel: maintained 18.70m CD, authorized 19.00m CD
    # - Entrance Channel: maintained 17.10m CD, authorized 18.70m CD
    prt_berths = mem_db.query(Berth).filter(Berth.port_unlocode == "INPRT").all()
    assert len(prt_berths) >= 3
    b02 = next(b for b in prt_berths if b.berth_code == "PRT-B02")
    b03 = next(b for b in prt_berths if b.berth_code == "PRT-B03")
    b0506 = next(b for b in prt_berths if b.berth_code == "PRT-B0506")
    assert b02.alongside_depth_min_meters == 17.10
    assert b02.max_vessel_draft_meters == 16.00
    assert b02.max_vessel_loa_meters == 300.0
    assert b02.max_vessel_beam_meters == 46.0

    assert b03.alongside_depth_min_meters == 17.10
    assert b03.max_vessel_draft_meters == 16.00

    assert b0506.alongside_depth_min_meters == 15.00
    assert b0506.max_vessel_draft_meters == 14.50
    assert b0506.max_vessel_loa_meters == 300.0

    prt_entrance = mem_db.query(ChannelReach).filter(ChannelReach.reach_code == "PRT-ENTRANCE-CHNL").first()
    assert prt_entrance.depth_maintained_meters == 17.10
    assert prt_entrance.depth_authorized_meters == 18.70

    prt_approach = mem_db.query(ChannelReach).filter(ChannelReach.reach_code == "PRT-APPROACH-CHNL").first()
    assert prt_approach.depth_maintained_meters == 18.70
    assert prt_approach.depth_authorized_meters == 19.00

    # Visakhapatnam (INVTZ):
    # - Outer Harbour (Capesize): alongside 19.50m, permissible draft 18.10m
    # - Inner Harbour WQ1: alongside 14.00m, permissible draft 13.50m
    # - Inner Harbour EQ1: alongside 15.00m, permissible draft 14.50m
    vtz_berths = mem_db.query(Berth).filter(Berth.port_unlocode == "INVTZ").all()
    oh = next(b for b in vtz_berths if "VGC" in b.berth_code)
    wq1 = next(b for b in vtz_berths if "WQ1" in b.berth_code)
    eq1 = next(b for b in vtz_berths if "EQ1" in b.berth_code)
    assert oh.alongside_depth_min_meters == 19.50
    assert oh.max_vessel_draft_meters == 18.10
    assert wq1.alongside_depth_min_meters == 14.00
    assert wq1.max_vessel_draft_meters == 13.50
    assert eq1.alongside_depth_min_meters == 15.00
    assert eq1.max_vessel_draft_meters == 14.50

    # JNPA (INNSA):
    # - BMCT: alongside 16.20m CD, baseline permissible draft 15.00m
    # - JNPCT: alongside 14.00m CD, permissible draft 13.50m
    # - Main Channel: 15.00m CD
    jnpa_berths = mem_db.query(Berth).filter(Berth.port_unlocode == "INNSA").all()
    bmct = next(b for b in jnpa_berths if "BMCT" in b.berth_code)
    jnpct = next(b for b in jnpa_berths if "JNPCT" in b.berth_code)
    assert bmct.alongside_depth_min_meters == 16.20
    assert bmct.max_vessel_draft_meters == 15.00
    assert jnpct.alongside_depth_min_meters == 14.00
    assert jnpct.max_vessel_draft_meters == 13.50

    jnpa_chnl = mem_db.query(ChannelReach).filter(ChannelReach.reach_code == "JNPA-MAIN-CHNL").first()
    assert jnpa_chnl.depth_maintained_meters == 15.00

    # SMP Kolkata (INCCU / INHAL):
    # - KDS Netaji Subhas Dock: alongside 7.50m CD, permissible draft 7.00m
    # - Haldia HDC: alongside 8.50m CD, permissible draft 8.50m
    kds = mem_db.query(Berth).filter(Berth.port_unlocode == "INCCU").first()
    assert kds.alongside_depth_min_meters == 7.50
    assert kds.max_vessel_draft_meters == 7.00

    hdc = mem_db.query(Berth).filter(Berth.port_unlocode == "INHAL").first()
    assert hdc.alongside_depth_min_meters == 8.50
    assert hdc.max_vessel_draft_meters == 8.50


# ---------------------------------------------------------------------------
# 17. Panama Canal Neopanamax LOA Revisions & Scope
# ---------------------------------------------------------------------------

def test_panama_canal_loa_revision_and_canal_transit_scope(mem_db: Session):
    """Verify Panama Canal Neopanamax LOA observations (366.0m vs 370.33m) and CANAL_TRANSIT scope."""
    papac_rests = (
        mem_db.query(NavigationalRestriction)
        .filter(
            NavigationalRestriction.port_unlocode == "PAPAC",
            NavigationalRestriction.scope_type == RestrictionScope.CANAL_TRANSIT,
            NavigationalRestriction.restriction_type == RestrictionType.LOA_MAX,
        )
        .all()
    )
    assert len(papac_rests) >= 2

    # Check for nominal 366.0m design baseline and 370.33m active operational maximum
    limits = [r.numeric_limit for r in papac_rests]
    assert 366.00 in limits
    assert 370.33 in limits

    # Verify dated observations: 366.0m baseline has valid_to in 2021; 370.33m revision has valid_from in 2021
    base_2016 = next(r for r in papac_rests if r.numeric_limit == 366.00)
    rev_2021 = next(r for r in papac_rests if r.numeric_limit == 370.33)
    assert base_2016.valid_to is not None
    assert rev_2021.valid_from is not None
    assert rev_2021.valid_to is None

    # Test active operational rule governing transit compatibility
    registry = PortRegistry(mem_db)

    # Vessel with LOA 368.0m passes Neopanamax active transit (370.33m operational maximum)
    res_pass = registry.evaluate_canal_transit_compatibility("PAPAC", vessel_loa=368.0)
    assert res_pass["passed"] is True
    assert res_pass["status"] == "SUITABLE"

    # Vessel with LOA 372.0m fails (exceeds 370.33m)
    res_fail = registry.evaluate_canal_transit_compatibility("PAPAC", vessel_loa=372.0)
    assert res_fail["passed"] is False
    assert res_fail["status"] == "INSUITABLE"
    assert "exceeds active canal maximum" in res_fail["reason"]


# ---------------------------------------------------------------------------
# 18. Air-Draft Fail-Closed Semantics
# ---------------------------------------------------------------------------

def test_air_draft_fail_closed_semantics(mem_db: Session):
    """Missing overhead data yields UNVERIFIED, confirmed unconstrained yields SUITABLE, exceedance yields INSUITABLE."""
    registry = PortRegistry(mem_db)

    # Case 1: Port with overhead restriction (USMSY - Huey P Long Bridge, 46.47m)
    # 1a. Vessel air draft unknown -> UNVERIFIED
    res_unk = registry.evaluate_air_draft_compatibility("USMSY", vessel_air_draft=None)
    assert res_unk["passed"] is False
    assert res_unk["status"] == "UNVERIFIED"
    assert "vessel air draft is unrecorded" in res_unk["reason"]

    # 1b. Vessel air draft 42.0m clears 46.47m -> SUITABLE
    res_clear = registry.evaluate_air_draft_compatibility("USMSY", vessel_air_draft=42.0)
    assert res_clear["passed"] is True
    assert res_clear["status"] == "SUITABLE"

    # 1c. Vessel air draft 48.0m exceeds 46.47m -> INSUITABLE
    res_exceed = registry.evaluate_air_draft_compatibility("USMSY", vessel_air_draft=48.0)
    assert res_exceed["passed"] is False
    assert res_exceed["status"] == "INSUITABLE"
    assert "exceeds certified clearance" in res_exceed["reason"]

    # Case 2: Port with confirmed unconstrained overhead clearance (INPRT - Paradip)
    # Explicit AIR_DRAFT_UNCONSTRAINED record -> SUITABLE even if vessel air draft is unrecorded
    res_uncon_none = registry.evaluate_air_draft_compatibility("INPRT", vessel_air_draft=None)
    assert res_uncon_none["passed"] is True
    assert res_uncon_none["status"] == "SUITABLE"
    assert "unconstrained" in res_uncon_none["reason"].lower()

    # Vessel air draft 55.0m at INPRT -> SUITABLE
    res_uncon_55 = registry.evaluate_air_draft_compatibility("INPRT", vessel_air_draft=55.0)
    assert res_uncon_55["passed"] is True
    assert res_uncon_55["status"] == "SUITABLE"

    # Case 3: Port with NO overhead restrictions AND NO unconstrained marker (USVAN)
    # Fail closed -> UNVERIFIED (never SUITABLE!)
    res_van_none = registry.evaluate_air_draft_compatibility("USVAN", vessel_air_draft=None)
    assert res_van_none["passed"] is False
    assert res_van_none["status"] == "UNVERIFIED"

    res_van_35 = registry.evaluate_air_draft_compatibility("USVAN", vessel_air_draft=35.0)
    assert res_van_35["passed"] is False
    assert res_van_35["status"] == "UNVERIFIED"


# ---------------------------------------------------------------------------
# 19. Source Traceability & Stable Document Identifiers (Stage F1.3.2.3)
# ---------------------------------------------------------------------------

def test_source_traceability_and_document_identifiers(mem_db: Session):
    """Verify official source documents, direct URLs, stable document IDs, and retrieval dates."""
    # 1. Indian Port Authorities (Paradip, Vizag, JNPA, SMPK)
    prt_term = mem_db.query(Terminal).filter(Terminal.port_unlocode == "INPRT", Terminal.terminal_code == "INPRT-NQ").first()
    assert prt_term is not None
    assert prt_term.raw_attributes_json is not None
    import json
    raw = json.loads(prt_term.raw_attributes_json)
    assert "paradipport.gov.in" in raw.get("source_url", "")
    assert "PPA-MD-CIRCULAR-2023-22" in raw.get("source_identifier", "")
    assert "Table 1" in raw.get("source_publication_reference", "")

    # Channels
    prt_chnl = mem_db.query(ChannelReach).filter(ChannelReach.reach_code == "PRT-APPROACH-CHNL").first()
    assert prt_chnl is not None
    assert prt_chnl.depth_maintained_meters == 18.70
    assert prt_chnl.depth_authorized_meters == 19.00

    # 2. AIR_DRAFT_UNCONSTRAINED affirmative documents
    vtz_uncon = (
        mem_db.query(NavigationalRestriction)
        .filter(NavigationalRestriction.port_unlocode == "INVTZ", NavigationalRestriction.restriction_type == RestrictionType.AIR_DRAFT_UNCONSTRAINED)
        .first()
    )
    assert vtz_uncon is not None
    assert vtz_uncon.source_url is not None and "vizagport.com" in vtz_uncon.source_url
    assert "VPA Port Information Guide 2024" in vtz_uncon.source_publication_reference
    assert vtz_uncon.verification_status == VerificationStatus.DIRECTLY_VERIFIED
    assert vtz_uncon.retrieval_timestamp is not None

    prt_uncon = (
        mem_db.query(NavigationalRestriction)
        .filter(NavigationalRestriction.port_unlocode == "INPRT", NavigationalRestriction.restriction_type == RestrictionType.AIR_DRAFT_UNCONSTRAINED)
        .first()
    )
    assert prt_uncon is not None
    assert prt_uncon.source_url is not None and "paradipport.gov.in" in prt_uncon.source_url
    assert "PPA Information Handbook 2024" in prt_uncon.source_publication_reference
    assert prt_uncon.verification_status == VerificationStatus.DIRECTLY_VERIFIED

    # 3. Panama Canal dated rule observations
    base_2016 = (
        mem_db.query(NavigationalRestriction)
        .filter(NavigationalRestriction.port_unlocode == "PAPAC", NavigationalRestriction.numeric_limit == 366.00)
        .first()
    )
    assert base_2016 is not None
    assert "N01-2016.pdf" in base_2016.source_url
    assert "ACP Notice to Shipping No. N-1-2016" in base_2016.source_publication_reference
    assert base_2016.valid_from is not None
    assert base_2016.valid_to is not None

    rev_2021 = (
        mem_db.query(NavigationalRestriction)
        .filter(NavigationalRestriction.port_unlocode == "PAPAC", NavigationalRestriction.numeric_limit == 370.33)
        .first()
    )
    assert rev_2021 is not None
    assert "ADV19-2021.pdf" in rev_2021.source_url
    assert "ACP Advisory to Shipping No. A-19-2021" in rev_2021.source_publication_reference
    assert rev_2021.valid_from is not None
    assert rev_2021.valid_to is None


# ---------------------------------------------------------------------------
# 20. Conditional Tidal Compatibility & UKC Mathematics (Stage F1.3.2.3)
# ---------------------------------------------------------------------------

def test_conditional_tidal_compatibility_mathematics(mem_db: Session):
    """Verify fail-closed semantics, UKC mathematics, and high-tide extensions across Indian ports."""
    registry = PortRegistry(mem_db)

    # -------------------------------------------------------------------------
    # A. Paradip Berth 03 (New Coal Import Berth): Baseline 16.00m, Extension 16.50m (Tide >= 1.50m)
    # -------------------------------------------------------------------------
    # Case A1: Draft 16.30m (exceeds 16.00m baseline), tide data MISSING -> UNVERIFIED
    res_b03_missing = registry.evaluate_berth_compatibility("INPRT", vessel_draft=16.30, berth_id=None, available_tide_height=None)
    assert res_b03_missing["passed"] is False
    assert res_b03_missing["status"] == "UNVERIFIED"
    assert "missing/unrecorded" in res_b03_missing["reason"]

    # Case A2: Draft 16.30m, tide below threshold (1.20m < 1.50m) -> INSUITABLE
    res_b03_low_tide = registry.evaluate_berth_compatibility("INPRT", vessel_draft=16.30, berth_id=None, available_tide_height=1.20)
    assert res_b03_low_tide["passed"] is False
    assert res_b03_low_tide["status"] == "INSUITABLE"
    assert "below required high-tide threshold" in res_b03_low_tide["reason"]

    # Case A3: Draft 16.30m, tide meets threshold (1.80m >= 1.50m) -> CONDITIONALLY_SUITABLE
    res_b03_ok = registry.evaluate_berth_compatibility("INPRT", vessel_draft=16.30, berth_id=None, available_tide_height=1.80)
    assert res_b03_ok["passed"] is True
    assert res_b03_ok["status"] == "CONDITIONALLY_SUITABLE"
    assert "conditional upon high-water tidal laycan" in res_b03_ok["reason"]

    # Case A4: Draft 16.80m (exceeds max 16.50m extension) -> INSUITABLE
    res_b03_exceed = registry.evaluate_berth_compatibility("INPRT", vessel_draft=16.80, berth_id=None, available_tide_height=2.00)
    assert res_b03_exceed["passed"] is False
    assert res_b03_exceed["status"] == "INSUITABLE"

    # -------------------------------------------------------------------------
    # B. Paradip Berths 05/06 (Mechanized Coal): Baseline 14.50m, Extension 16.00m (Tide >= 1.50m)
    # -------------------------------------------------------------------------
    b0506 = mem_db.query(Berth).filter(Berth.berth_code == "PRT-B0506").first()
    assert b0506 is not None

    # Draft 15.50m, tide missing -> UNVERIFIED
    res_b0506_missing = registry.evaluate_berth_compatibility("INPRT", vessel_draft=15.50, berth_id=b0506.id, available_tide_height=None)
    assert res_b0506_missing["passed"] is False
    assert res_b0506_missing["status"] == "UNVERIFIED"

    # Draft 15.50m, tide meets threshold (1.60m >= 1.50m) -> CONDITIONALLY_SUITABLE
    res_b0506_ok = registry.evaluate_berth_compatibility("INPRT", vessel_draft=15.50, berth_id=b0506.id, available_tide_height=1.60)
    assert res_b0506_ok["passed"] is True
    assert res_b0506_ok["status"] == "CONDITIONALLY_SUITABLE"

    # -------------------------------------------------------------------------
    # C. JNPA BMCT: Baseline 15.00m, Extension 15.50m (Tide >= 3.00m)
    # -------------------------------------------------------------------------
    # Draft 15.30m, tide missing -> UNVERIFIED
    res_bmct_missing = registry.evaluate_berth_compatibility("INNSA", vessel_draft=15.30, available_tide_height=None)
    assert res_bmct_missing["passed"] is False
    assert res_bmct_missing["status"] == "UNVERIFIED"

    # Draft 15.30m, tide below threshold (2.50m < 3.00m) -> INSUITABLE
    res_bmct_low = registry.evaluate_berth_compatibility("INNSA", vessel_draft=15.30, available_tide_height=2.50)
    assert res_bmct_low["passed"] is False
    assert res_bmct_low["status"] == "INSUITABLE"

    # Draft 15.30m, tide meets threshold (3.20m >= 3.00m) -> CONDITIONALLY_SUITABLE
    res_bmct_ok = registry.evaluate_berth_compatibility("INNSA", vessel_draft=15.30, available_tide_height=3.20)
    assert res_bmct_ok["passed"] is True
    assert res_bmct_ok["status"] == "CONDITIONALLY_SUITABLE"

    # -------------------------------------------------------------------------
    # D. JNPA Main Channel: Maintained 15.00m, UKC 1.50m (Draft 15.00m requires tide >= 2.20m)
    # -------------------------------------------------------------------------
    # D1. Draft 15.00m + UKC 1.50m = 16.50m. Tide missing -> UNVERIFIED (fail closed)
    res_chnl_missing = registry.evaluate_channel_compatibility("INNSA", vessel_draft=15.00, available_tide_height=None)
    assert res_chnl_missing["passed"] is False
    assert res_chnl_missing["status"] == "UNVERIFIED"
    assert "operational tide height data is unrecorded/missing" in res_chnl_missing["reason"]

    # D2. Draft 15.00m, tide below threshold (1.80m < 2.20m) -> INSUITABLE
    res_chnl_low_tide = registry.evaluate_channel_compatibility("INNSA", vessel_draft=15.00, available_tide_height=1.80)
    assert res_chnl_low_tide["passed"] is False
    assert res_chnl_low_tide["status"] == "INSUITABLE"
    assert "below statutory required threshold" in res_chnl_low_tide["reason"]

    # D3. Draft 15.00m, tide meets threshold (2.20m) -> CONDITIONALLY_SUITABLE (total depth 17.20m >= 16.50m)
    res_chnl_ok = registry.evaluate_channel_compatibility("INNSA", vessel_draft=15.00, available_tide_height=2.20)
    assert res_chnl_ok["passed"] is True
    assert res_chnl_ok["status"] == "CONDITIONALLY_SUITABLE"
    assert "UKC 1.50m satisfied" in res_chnl_ok["reason"]

    # D4. UKC Violated: e.g. draft 15.00m + required UKC 2.50m = 17.50m > total depth 17.20m (with 2.20m tide) -> INSUITABLE
    res_chnl_ukc_fail = registry.evaluate_channel_compatibility("INNSA", vessel_draft=15.00, required_ukc=2.50, available_tide_height=2.20)
    assert res_chnl_ukc_fail["passed"] is False
    assert res_chnl_ukc_fail["status"] == "INSUITABLE"
    assert "Under-Keel Clearance" in res_chnl_ukc_fail["reason"] and "violated" in res_chnl_ukc_fail["reason"]


# ---------------------------------------------------------------------------
# 21. Panama Canal Historical vs Active Temporal Evaluations (Stage F1.3.2.3)
# ---------------------------------------------------------------------------

def test_panama_canal_historical_vs_active_temporal_evaluations(mem_db: Session):
    """Verify historical assessment enforces 366m, current assessment enforces 370.33m, and invalid timestamp defaults safely."""
    registry = PortRegistry(mem_db)

    # 1. Historical Assessment (as of 2018-05-15, within 2016-06-26 to 2021-05-31 validity period)
    # 1a. Vessel LOA 365.0m clears historical 366.00m rule
    hist_pass = registry.evaluate_canal_transit_compatibility("PAPAC", vessel_loa=365.0, as_of_time="2018-05-15T12:00:00Z")
    assert hist_pass["passed"] is True
    assert hist_pass["status"] == "SUITABLE"

    # 1b. Vessel LOA 368.0m exceeds historical 366.00m rule -> INSUITABLE
    hist_fail = registry.evaluate_canal_transit_compatibility("PAPAC", vessel_loa=368.0, as_of_time="2018-05-15T12:00:00Z")
    assert hist_fail["passed"] is False
    assert hist_fail["status"] == "INSUITABLE"
    assert "exceeds active canal maximum (366.00m)" in hist_fail["reason"]

    # 2. Current Assessment (as_of_time=None -> current time 2026)
    # 2a. Vessel LOA 368.0m clears active operational 370.33m rule
    curr_pass = registry.evaluate_canal_transit_compatibility("PAPAC", vessel_loa=368.0, as_of_time=None)
    assert curr_pass["passed"] is True
    assert curr_pass["status"] == "SUITABLE"

    # 2b. Vessel LOA 372.0m exceeds active 370.33m rule -> INSUITABLE
    curr_fail = registry.evaluate_canal_transit_compatibility("PAPAC", vessel_loa=372.0, as_of_time=None)
    assert curr_fail["passed"] is False
    assert curr_fail["status"] == "INSUITABLE"
    assert "exceeds active canal maximum (370.33m)" in curr_fail["reason"]

    # 3. Missing or Invalid as_of_time does NOT accidentally select historical rule
    # Safely defaults to current time (evaluating against 370.33m)
    invalid_date_eval = registry.evaluate_canal_transit_compatibility("PAPAC", vessel_loa=368.0, as_of_time="invalid-date-string")
    assert invalid_date_eval["passed"] is True
    assert invalid_date_eval["status"] == "SUITABLE"

    int_eval = registry.evaluate_canal_transit_compatibility("PAPAC", vessel_loa=368.0, as_of_time=999999)
    assert int_eval["passed"] is True
    assert int_eval["status"] == "SUITABLE"
