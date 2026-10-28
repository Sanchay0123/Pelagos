"""Unit tests for Pelagos Vessel Suitability Matching Engine (OPT-002).

Validates physical criteria matching, dimension inference, loading-port distance calculation,
stale telemetry handling, missing particulars, invalid coordinates, and ranking transparency.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.database import Base
from pelagos.models import VesselPosition
from pelagos.optimization.schemas import (
    SuitabilityStatus,
    VesselSuitabilityRequest,
    VesselSuitabilityResponse,
    SUITABILITY_COMMERCIAL_DISCLAIMERS,
)
from pelagos.optimization.matcher import (
    EARTH_RADIUS_NM,
    haversine_distance_nm,
    infer_dimensional_class,
    is_dry_bulk_compatible_type,
    evaluate_vessel_suitability,
    match_suitable_vessels,
)


@pytest.fixture
def memory_db():
    """In-memory SQLite database populated with isolated test vessel positions."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Troodos Sun (Panamax cargo match near US Pacific coast)
    session.add(
        VesselPosition(
            mmsi="212719000",
            imo="9300001",
            vessel_name="TROODOS SUN",
            vessel_type="Cargo",
            vessel_type_code=70,
            latitude=34.52975,
            longitude=-120.94588,
            speed_knots=10.4,
            course_degrees=180.0,
            heading_degrees=182.0,
            draft_meters=12.5,
            length_meters=228.0,
            width_meters=35.0,
            destination="YOKOHAMA",
            observation_time=now - timedelta(minutes=10),
            source_name="USCG NAIS",
            license_type="U.S. Public Domain",
            idempotency_key="uscg:212719000:1",
        )
    )

    # 2. MSC Athos (Post-Panamax / Large container/cargo near mouth of Columbia River)
    session.add(
        VesselPosition(
            mmsi="636022875",
            imo="9745287",
            vessel_name="MSC ATHOS",
            vessel_type="Cargo - Hazardous A",
            vessel_type_code=71,
            latitude=46.17595,
            longitude=-124.60901,
            speed_knots=15.9,
            draft_meters=10.3,
            length_meters=300.0,
            width_meters=48.0,
            observation_time=now - timedelta(minutes=5),
            source_name="USCG NAIS",
            license_type="U.S. Public Domain",
            idempotency_key="uscg:636022875:1",
        )
    )

    # 3. American Courage (Small Cargo with missing draft/width particulars)
    session.add(
        VesselPosition(
            mmsi="367121040",
            imo=None,
            vessel_name="AMERICAN COURAGE",
            vessel_type="Cargo",
            vessel_type_code=70,
            latitude=41.54617,
            longitude=-82.72934,
            speed_knots=0.0,
            draft_meters=None,
            length_meters=194.0,
            width_meters=None,  # Missing beam
            observation_time=now - timedelta(days=2),  # Stale
            source_name="USCG NAIS",
            license_type="U.S. Public Domain",
            idempotency_key="uscg:367121040:1",
        )
    )

    # 4. Tug / Towing Vessel (Incompatible type)
    session.add(
        VesselPosition(
            mmsi="367118980",
            imo=None,
            vessel_name="CAPT J A MORGAN",
            vessel_type="Towing",
            vessel_type_code=31,
            latitude=29.98534,
            longitude=-90.40674,
            speed_knots=6.0,
            draft_meters=3.0,
            length_meters=115.0,
            width_meters=34.0,
            observation_time=now - timedelta(minutes=20),
            source_name="USCG NAIS",
            license_type="U.S. Public Domain",
            idempotency_key="uscg:367118980:1",
        )
    )

    # 5. Tanker (Incompatible liquid bulk type)
    session.add(
        VesselPosition(
            mmsi="538005000",
            imo="9500002",
            vessel_name="OCEAN TANKER",
            vessel_type="Tanker",
            vessel_type_code=80,
            latitude=48.2,
            longitude=-124.5,
            speed_knots=12.0,
            draft_meters=14.0,
            length_meters=240.0,
            width_meters=42.0,
            observation_time=now - timedelta(minutes=10),
            source_name="USCG NAIS",
            license_type="U.S. Public Domain",
            idempotency_key="uscg:538005000:1",
        )
    )

    session.commit()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Haversine Distance & Coordinate Validation Tests
# ---------------------------------------------------------------------------

def test_haversine_distance_calculation():
    """Verify Great-Circle Haversine calculation against known geodesic distance."""
    # Seattle entrance: 48.5, -124.75
    # Yokohama entrance: 35.15, 139.73
    # Straight-line great-circle distance is ~4,066.2 NM
    dist = haversine_distance_nm(48.5, -124.75, 35.15, 139.73)
    assert dist is not None
    assert 4000.0 < dist < 4150.0

    # Coincident coordinates should yield 0.0 NM
    dist_zero = haversine_distance_nm(48.5, -124.75, 48.5, -124.75)
    assert dist_zero == 0.0


def test_haversine_handles_invalid_coordinates():
    """Invalid or missing coordinates must return None without raising exceptions."""
    assert haversine_distance_nm(None, -124.75, 48.5, -124.75) is None
    assert haversine_distance_nm(48.5, None, 48.5, -124.75) is None
    assert haversine_distance_nm(48.5, -124.75, None, None) is None
    # Out of bounds lat
    assert haversine_distance_nm(95.0, -124.75, 48.5, -124.75) is None
    # Out of bounds lon
    assert haversine_distance_nm(48.5, -185.0, 48.5, -124.75) is None
    # NaN
    assert haversine_distance_nm(float("nan"), -124.75, 48.5, -124.75) is None


# ---------------------------------------------------------------------------
# Dimensional Class Inference Tests
# ---------------------------------------------------------------------------

def test_infer_dimensional_class():
    """Test standard commercial dry bulk classification envelopes."""
    assert infer_dimensional_class(110.0, 16.0) == "Small / Coastal Cargo"
    assert infer_dimensional_class(150.0, 24.0) == "Handysize"
    assert infer_dimensional_class(190.0, 32.2) == "Handymax / Supramax"
    assert infer_dimensional_class(225.0, 32.3) == "Panamax / Kamsarmax"
    assert infer_dimensional_class(228.0, 35.0) == "Panamax / Kamsarmax"
    assert infer_dimensional_class(300.0, 48.0) == "Post-Panamax / Capesize"

    # Missing or non-positive dimensions
    assert infer_dimensional_class(None, 32.0) is None
    assert infer_dimensional_class(190.0, None) is None
    assert infer_dimensional_class(0.0, 32.0) is None
    assert infer_dimensional_class(190.0, 0.0) is None
    assert infer_dimensional_class(-100.0, 32.0) is None



# ---------------------------------------------------------------------------
# Vessel Type Filtering Tests
# ---------------------------------------------------------------------------

def test_vessel_type_filtering():
    """Ensure non-bulk cargo vessels are rejected as unsuitable for grain."""
    # Cargo valid
    ok_cargo, _ = is_dry_bulk_compatible_type("Cargo", 70)
    assert ok_cargo is True

    ok_haz_cargo, _ = is_dry_bulk_compatible_type("Cargo - Hazardous A", 71)
    assert ok_haz_cargo is True

    # Incompatible types
    ok_tug, reason_tug = is_dry_bulk_compatible_type("Towing", 31)
    assert ok_tug is False
    assert "incompatible" in reason_tug.lower() or "tug" in reason_tug.lower()

    ok_tanker, reason_tanker = is_dry_bulk_compatible_type("Tanker", 80)
    assert ok_tanker is False
    assert "tanker" in reason_tanker.lower()

    ok_fishing, _ = is_dry_bulk_compatible_type("Fishing", None)
    assert ok_fishing is False

    ok_passenger, _ = is_dry_bulk_compatible_type("Passenger", None)
    assert ok_passenger is False


# ---------------------------------------------------------------------------
# Vessel Suitability Evaluation & Missing Data Tests
# ---------------------------------------------------------------------------

def test_evaluate_vessel_suitability_missing_particulars():
    """Missing particulars must yield INSUFFICIENT_DATA without fabricating values."""
    now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    vessel = VesselPosition(
        mmsi="999000111",
        vessel_name="UNKNOWN CARGO",
        vessel_type="Cargo",
        latitude=48.0,
        longitude=-124.0,
        length_meters=None,  # Missing
        width_meters=None,   # Missing
        observation_time=now,
    )
    load_coords = (48.5, -124.75)  # Seattle
    match = evaluate_vessel_suitability(vessel, 52000.0, load_coords, now_utc=now)

    assert match.suitability_status == SuitabilityStatus.INSUFFICIENT_DATA
    assert "lacks verified length and beam" in match.suitability_reason
    assert match.estimated_dimensional_class is None
    assert match.distance_to_load_port_nm is not None
    assert match.laycan_feasibility == "UNCONFIRMED"


def test_evaluate_vessel_suitability_stale_flagging():
    """Observations older than 30 minutes must be flagged as stale."""
    now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    vessel = VesselPosition(
        mmsi="999000222",
        vessel_name="OLD CARGO",
        vessel_type="Cargo",
        latitude=48.0,
        longitude=-124.0,
        length_meters=225.0,
        width_meters=32.0,
        observation_time=now - timedelta(hours=2),  # 2 hours old
    )
    match = evaluate_vessel_suitability(vessel, 52000.0, (48.5, -124.75), now_utc=now)
    assert match.is_stale is True


# ---------------------------------------------------------------------------
# Match Engine & Deterministic Ranking Tests
# ---------------------------------------------------------------------------

def test_match_suitable_vessels_ranking(memory_db):
    """Test full matching workflow on memory database for PNW-JPN-BULK corridor."""
    req = VesselSuitabilityRequest(
        origin="USSEA",
        destination="JPYOK",
        cargo_tonnes=52000.0,
        max_vessels=10,
    )

    resp = match_suitable_vessels(memory_db, req)

    assert resp.status == "matched"
    assert resp.loading_port_id == "USSEA"
    assert resp.loading_port_name == "Seattle"
    assert resp.cargo_tonnes == 52000.0
    assert resp.total_vessels_evaluated == 5
    assert len(resp.candidates) == 5

    # Check that POTENTIALLY_SUITABLE vessels appear at the top
    top_candidate = resp.candidates[0]
    assert top_candidate.suitability_status == SuitabilityStatus.POTENTIALLY_SUITABLE

    # Verify that MSC ATHOS and TROODOS SUN are categorized as potentially suitable
    suitable_mmsis = {
        c.mmsi for c in resp.candidates if c.suitability_status == SuitabilityStatus.POTENTIALLY_SUITABLE
    }
    assert "636022875" in suitable_mmsis  # MSC ATHOS (~140 NM from Seattle)
    assert "212719000" in suitable_mmsis  # TROODOS SUN (~855 NM from Seattle)

    # MSC ATHOS is closer to Seattle (139.7 NM) than TROODOS SUN (855.8 NM)
    # Both are POTENTIALLY_SUITABLE, so MSC ATHOS must rank first
    assert resp.candidates[0].mmsi == "636022875"
    assert resp.candidates[1].mmsi == "212719000"

    # Verify that Capt J A Morgan (Towing) is UNSUITABLE_TYPE
    towing_match = next(c for c in resp.candidates if c.mmsi == "367118980")
    assert towing_match.suitability_status == SuitabilityStatus.UNSUITABLE_TYPE

    # Verify that Ocean Tanker (Tanker) is UNSUITABLE_TYPE
    tanker_match = next(c for c in resp.candidates if c.mmsi == "538005000")
    assert tanker_match.suitability_status == SuitabilityStatus.UNSUITABLE_TYPE

    # Verify that American Courage (missing width) is INSUFFICIENT_DATA
    missing_match = next(c for c in resp.candidates if c.mmsi == "367121040")
    assert missing_match.suitability_status == SuitabilityStatus.INSUFFICIENT_DATA


def test_match_unmonitored_corridor(memory_db):
    """Unmonitored corridors should still evaluate vessels without fabricating rates."""
    req = VesselSuitabilityRequest(
        origin="NLRTM",
        destination="SGSIN",
        cargo_tonnes=50000.0,
    )
    resp = match_suitable_vessels(memory_db, req)
    assert resp.status == "unmonitored_corridor_matched"
    assert resp.loading_port_id == "NLRTM"
    assert resp.total_vessels_evaluated == 5


def test_match_invalid_cargo_tonnage():
    """Negative or zero cargo tonnage must fail validation."""
    with pytest.raises(ValueError):
        VesselSuitabilityRequest(cargo_tonnes=0.0)

    with pytest.raises(ValueError):
        VesselSuitabilityRequest(cargo_tonnes=-500.0)


def test_laycan_and_commercial_disclaimers(memory_db):
    """Ensure all responses strictly preserve commercial boundaries and unconfirmed laycans."""
    req = VesselSuitabilityRequest(origin="USSEA", destination="JPYOK", cargo_tonnes=52000.0)
    resp = match_suitable_vessels(memory_db, req)

    for cand in resp.candidates:
        assert cand.laycan_feasibility == "UNCONFIRMED"
        assert "commercial" in cand.laycan_feasibility_note.lower()
        assert cand.commercial_availability == "UNCONFIRMED"
        assert cand.cargo_capacity_status == "UNVERIFIED"
        assert cand.verified_cargo_capacity_dwt is None

    assert any("open-tonnage" in d.lower() for d in resp.disclaimers)
    assert any("laycan" in d.lower() for d in resp.disclaimers)
    assert any("historical" in d.lower() for d in resp.disclaimers)
    assert resp.data_mode == "historical"
    assert "historical" in resp.data_mode_notice.lower()


# ---------------------------------------------------------------------------
# Semantic Tightening & Historical Boundary Tests (OPT-002 Review)
# ---------------------------------------------------------------------------

def test_separation_of_ais_type_class_and_verified_capacity():
    """Verify clean separation of observed AIS vessel type, dimensional class, and unverified capacity."""
    now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    vessel = VesselPosition(
        mmsi="212719000",
        vessel_name="TROODOS SUN",
        vessel_type="Cargo",
        vessel_type_code=70,
        latitude=34.5,
        longitude=-120.9,
        length_meters=228.0,
        width_meters=35.0,
        draft_meters=12.5,
        observation_time=now - timedelta(minutes=10),
    )
    match = evaluate_vessel_suitability(vessel, 52000.0, (48.5, -124.75), now_utc=now)

    # 1. Observed AIS broadcast type is preserved
    assert match.vessel_type == "Cargo"
    assert match.vessel_type_code == 70

    # 2. Dimensional class is estimated, not claimed as verified registration
    assert match.estimated_dimensional_class == "Panamax / Kamsarmax"

    # 3. Verified cargo capacity is strictly UNVERIFIED and None
    assert match.verified_cargo_capacity_dwt is None
    assert match.cargo_capacity_status == "UNVERIFIED"
    assert match.screening_stage == "dimensionally_screened"

    # 4. Reason explains dimensional screening without claiming confirmed capacity
    assert "Dimensionally screened candidate" in match.suitability_reason
    assert "Classified as Panamax / Kamsarmax" in match.suitability_reason
    assert "Verified deadweight tonnage (DWT)" in match.suitability_reason


def test_300m_vessel_explanation_agrees_with_classification():
    """Verify that a 300m vessel is classified as Post-Panamax / Capesize and explanation matches."""
    now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    vessel = VesselPosition(
        mmsi="636022875",
        vessel_name="MSC ATHOS",
        vessel_type="Cargo - Hazardous A",
        vessel_type_code=71,
        latitude=46.17,
        longitude=-124.60,
        length_meters=300.0,
        width_meters=48.0,
        draft_meters=10.3,
        observation_time=now - timedelta(minutes=5),
    )
    match = evaluate_vessel_suitability(vessel, 52000.0, (48.5, -124.75), now_utc=now)

    # Dimensional classification must be Post-Panamax / Capesize
    assert match.estimated_dimensional_class == "Post-Panamax / Capesize"

    # Explanation must explicitly state the Post-Panamax / Capesize classification
    assert "Classified as Post-Panamax / Capesize" in match.suitability_reason
    assert "Dimensionally screened candidate" in match.suitability_reason

    # Must NOT claim that cubic capacity is confirmed or verified
    assert "provides sufficient cubic capacity" not in match.suitability_reason
    assert match.verified_cargo_capacity_dwt is None
    assert match.cargo_capacity_status == "UNVERIFIED"
    assert match.screening_stage == "dimensionally_screened_oversized"


def test_class_boundaries_and_explanation_consistency():
    """Verify that class boundary transitions produce explanations agreeing with computed class."""
    now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    load_coords = (48.5, -124.75)

    cases = [
        # (LOA, Beam, Expected Class, Parcel, Expected Status)
        (129.0, 16.0, "Small / Coastal Cargo", 25000.0, SuitabilityStatus.POTENTIALLY_SUITABLE),
        (130.0, 20.0, "Handysize", 28000.0, SuitabilityStatus.POTENTIALLY_SUITABLE),
        (170.0, 27.0, "Handymax / Supramax", 50000.0, SuitabilityStatus.POTENTIALLY_SUITABLE),
        (205.0, 32.2, "Handymax / Supramax", 52000.0, SuitabilityStatus.POTENTIALLY_SUITABLE),
        (206.0, 32.2, "Panamax / Kamsarmax", 52000.0, SuitabilityStatus.POTENTIALLY_SUITABLE),
        (240.0, 35.0, "Panamax / Kamsarmax", 60000.0, SuitabilityStatus.POTENTIALLY_SUITABLE),
        (240.0, 38.0, "Post-Panamax / Capesize", 75000.0, SuitabilityStatus.POTENTIALLY_SUITABLE),
        (241.0, 32.2, "Post-Panamax / Capesize", 90000.0, SuitabilityStatus.POTENTIALLY_SUITABLE),
    ]

    for loa, beam, expected_class, parcel, expected_status in cases:
        vessel = VesselPosition(
            mmsi="999000001",
            vessel_name="BOUNDARY TESTER",
            vessel_type="Cargo",
            vessel_type_code=70,
            latitude=48.0,
            longitude=-124.0,
            length_meters=loa,
            width_meters=beam,
            observation_time=now,
        )
        match = evaluate_vessel_suitability(vessel, parcel, load_coords, now_utc=now)
        assert match.estimated_dimensional_class == expected_class, (
            f"Failed for LOA={loa}, Beam={beam}: expected {expected_class}, got {match.estimated_dimensional_class}"
        )
        assert match.suitability_status == expected_status
        assert f"Classified as {expected_class}" in match.suitability_reason, (
            f"Explanation did not mention computed class {expected_class}: {match.suitability_reason}"
        )


def test_historical_distance_qualification():
    """Verify that stale / historical observations qualify distance as non-current."""
    now = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
    historical_fix = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)  # >36 hours old

    vessel = VesselPosition(
        mmsi="212719000",
        vessel_name="TROODOS SUN",
        vessel_type="Cargo",
        vessel_type_code=70,
        latitude=34.52975,
        longitude=-120.94588,
        length_meters=228.0,
        width_meters=35.0,
        observation_time=historical_fix,
    )
    match = evaluate_vessel_suitability(vessel, 52000.0, (48.5, -124.75), now_utc=now)

    assert match.is_stale is True
    assert match.data_mode == "historical"
    assert match.observation_age_seconds is not None
    assert match.observation_age_seconds > 86400.0  # >1 day
    assert "HISTORICAL_OBSERVATION_ONLY" in match.distance_qualification
    assert "Current vessel position and proximity to loading port are unknown" in match.distance_qualification

