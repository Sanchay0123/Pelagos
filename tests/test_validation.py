"""Focused tests for telemetry validation, kinematic bounds, and temporal integrity."""

import pytest
from pelagos.ingestion.validator import validate_telemetry_record


def test_valid_telemetry_record():
    payload = {
        "mmsi": "367123456",
        "imo": "9315408",
        "vessel_name": "ATLANTIC TRADER",
        "vessel_type": "Cargo",
        "latitude": 29.5,
        "longitude": -94.2,
        "speed_knots": 12.5,
        "course_degrees": 180.0,
        "heading_degrees": 182.0,
        "draft_meters": 11.2,
        "observation_time": "2024-01-01T12:00:00Z",
        "source_name": "NOAA MarineCadastre / USCG NAIS",
        "license": "CC0 1.0 Universal",
    }
    res = validate_telemetry_record(payload)
    assert res.is_valid is True
    assert res.record is not None
    assert res.record.mmsi == "367123456"
    assert res.record.latitude == 29.5
    assert res.record.speed_knots == 12.5
    assert res.record.observation_time.tzinfo is not None


def test_reject_synthetic_data():
    """REQ-NFR-DIP-001: Zero unlabelled synthetic data in production."""
    payload = {
        "mmsi": "367123456",
        "vessel_name": "SIMULATED VESSEL",
        "latitude": 29.5,
        "longitude": -94.2,
        "observation_time": "2024-01-01T12:00:00Z",
        "is_synthetic": True,
    }
    res = validate_telemetry_record(payload)
    assert res.is_valid is False
    assert res.error_code == "ANOMALY_SYNTHETIC_DATA_REJECTED"


def test_reject_invalid_coordinates():
    """REQ-FR-DII-002: Coordinate sanity check."""
    # Latitude > 90
    res1 = validate_telemetry_record({
        "mmsi": "367123456",
        "latitude": 95.0,
        "longitude": -94.2,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res1.is_valid is False
    assert res1.error_code == "ANOMALY_INVALID_COORDINATES"

    # Longitude < -180
    res2 = validate_telemetry_record({
        "mmsi": "367123456",
        "latitude": 29.0,
        "longitude": -195.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res2.is_valid is False
    assert res2.error_code == "ANOMALY_INVALID_COORDINATES"

    # Non-numeric coordinate
    res3 = validate_telemetry_record({
        "mmsi": "367123456",
        "latitude": "corrupted",
        "longitude": -94.2,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res3.is_valid is False
    assert res3.error_code == "ANOMALY_INVALID_COORDINATES"


def test_reject_missing_or_invalid_identifier():
    # Empty MMSI
    res1 = validate_telemetry_record({
        "mmsi": "",
        "latitude": 29.0,
        "longitude": -94.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res1.is_valid is False
    assert res1.error_code == "ANOMALY_MISSING_IDENTIFIER"

    # Non-numeric MMSI
    res2 = validate_telemetry_record({
        "mmsi": "ABC1234",
        "latitude": 29.0,
        "longitude": -94.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res2.is_valid is False
    assert res2.error_code == "ANOMALY_MISSING_IDENTIFIER"


def test_plausible_high_speed_observations():
    """Plausible high-speed craft, pilot boats, and fast patrol craft must be accepted."""
    # High-Speed Craft at 45.0 kts (well above former 35 kts cutoff)
    res_hsc = validate_telemetry_record({
        "mmsi": "367123456",
        "vessel_name": "FRANCISCO EXPRESS",
        "vessel_type": "High Speed Craft",
        "vessel_type_code": 40,
        "latitude": 29.0,
        "longitude": -94.0,
        "speed_knots": 45.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res_hsc.is_valid is True
    assert res_hsc.record.speed_knots == 45.0

    # Fast Catamaran Ferry at 52.0 kts
    res_ferry = validate_telemetry_record({
        "mmsi": "367987654",
        "vessel_name": "ISLAND CAT",
        "vessel_type": "Catamaran Ferry",
        "latitude": 29.0,
        "longitude": -94.0,
        "speed_knots": 52.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res_ferry.is_valid is True
    assert res_ferry.record.speed_knots == 52.0

    # Pilot Boat at 38.0 kts
    res_pilot = validate_telemetry_record({
        "mmsi": "367555444",
        "vessel_name": "PILOT 1",
        "vessel_type": "Pilot Vessel",
        "vessel_type_code": 50,
        "latitude": 29.0,
        "longitude": -94.0,
        "speed_knots": 38.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res_pilot.is_valid is True
    assert res_pilot.record.speed_knots == 38.0

    # Fast Patrol / SAR craft at 42.0 kts
    res_patrol = validate_telemetry_record({
        "mmsi": "367333222",
        "vessel_name": "USCG PATROL",
        "vessel_type": "Law Enforcement",
        "vessel_type_code": 55,
        "latitude": 29.0,
        "longitude": -94.0,
        "speed_knots": 42.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res_patrol.is_valid is True


def test_implausible_vessel_speeds():
    """Confident rejection for speeds violating provisional vessel envelopes or physical ceilings."""
    # Tanker at 28.0 kts (exceeds 22 kts envelope for Tanker)
    res_tanker = validate_telemetry_record({
        "mmsi": "367111222",
        "vessel_name": "OCEAN TANKER",
        "vessel_type": "Tanker",
        "vessel_type_code": 80,
        "latitude": 29.0,
        "longitude": -94.0,
        "speed_knots": 28.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res_tanker.is_valid is False
    assert res_tanker.error_code == "ANOMALY_KINEMATIC_OUTLIER"
    assert "Tanker" in res_tanker.error_details

    # Bulk carrier at 36.0 kts (exceeds 22 kts envelope for Bulk Carrier)
    res_bulk = validate_telemetry_record({
        "mmsi": "367222333",
        "vessel_name": "PACIFIC BULKER",
        "vessel_type": "Bulk Carrier",
        "latitude": 29.0,
        "longitude": -94.0,
        "speed_knots": 36.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res_bulk.is_valid is False
    assert res_bulk.error_code == "ANOMALY_KINEMATIC_OUTLIER"

    # Tug at 25.0 kts (exceeds 20 kts envelope for Tug)
    res_tug = validate_telemetry_record({
        "mmsi": "367333444",
        "vessel_name": "HARBOR TUG",
        "vessel_type": "Tug",
        "vessel_type_code": 52,
        "latitude": 29.0,
        "longitude": -94.0,
        "speed_knots": 25.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res_tug.is_valid is False
    assert res_tug.error_code == "ANOMALY_KINEMATIC_OUTLIER"

    # Any vessel exceeding absolute marine physical ceiling (70 kts)
    res_ceiling = validate_telemetry_record({
        "mmsi": "367444555",
        "vessel_name": "FLYING SHIP",
        "vessel_type": "High Speed Craft",
        "vessel_type_code": 40,
        "latitude": 29.0,
        "longitude": -94.0,
        "speed_knots": 85.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res_ceiling.is_valid is False
    assert res_ceiling.error_code == "ANOMALY_KINEMATIC_OUTLIER"
    assert "absolute physical ceiling" in res_ceiling.error_details

    # Negative speed
    res_neg = validate_telemetry_record({
        "mmsi": "367555666",
        "vessel_name": "BACKWARDS",
        "vessel_type": "Cargo",
        "latitude": 29.0,
        "longitude": -94.0,
        "speed_knots": -5.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res_neg.is_valid is False
    assert res_neg.error_code == "ANOMALY_KINEMATIC_OUTLIER"

    # Implausible draft
    res_draft = validate_telemetry_record({
        "mmsi": "367666777",
        "vessel_name": "SUBMARINE",
        "latitude": 29.0,
        "longitude": -94.0,
        "draft_meters": -2.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res_draft.is_valid is False
    assert res_draft.error_code == "ANOMALY_KINEMATIC_OUTLIER"


def test_insufficient_vessel_particulars():
    """Where available data cannot justify confident rejection, quarantine with structured diagnostic code."""
    # Unclassified vessel reporting 36 kts (> 25 kts displacement baseline, but unverified particulars)
    res_unverified = validate_telemetry_record({
        "mmsi": "367777888",
        "vessel_name": "MYSTERY CRAFT",
        "vessel_type": "Other",
        "vessel_type_code": None,
        "latitude": 29.0,
        "longitude": -94.0,
        "speed_knots": 36.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res_unverified.is_valid is False
    assert res_unverified.error_code == "ANOMALY_INSUFFICIENT_PARTICULARS"
    assert "insufficient" in res_unverified.error_details

    # Generic "Commercial" with no ship type code reporting 32 kts
    res_generic = validate_telemetry_record({
        "mmsi": "367888999",
        "vessel_name": "GENERIC COMMERCIAL",
        "vessel_type": "Commercial",
        "vessel_type_code": None,
        "latitude": 29.0,
        "longitude": -94.0,
        "speed_knots": 32.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res_generic.is_valid is False
    assert res_generic.error_code == "ANOMALY_INSUFFICIENT_PARTICULARS"

    # Unclassified vessel reporting plausible displacement speed (16 kts <= 25 kts)
    res_plausible_unclassified = validate_telemetry_record({
        "mmsi": "367999000",
        "vessel_name": "NORMAL BOAT",
        "vessel_type": "Other",
        "vessel_type_code": None,
        "latitude": 29.0,
        "longitude": -94.0,
        "speed_knots": 16.0,
        "observation_time": "2024-01-01T12:00:00Z",
    })
    assert res_plausible_unclassified.is_valid is True
    assert res_plausible_unclassified.record.speed_knots == 16.0


def test_timezone_naive_rejection():
    """REQ-NFR-DIP-003: Timezone-naive timestamp without contract verification."""
    payload = {
        "mmsi": "367123456",
        "latitude": 29.0,
        "longitude": -94.0,
        "observation_time": "2024-01-01 12:00:00",  # naive string without Z or offset
        "source_timezone_verified": False,
    }
    res = validate_telemetry_record(payload)
    assert res.is_valid is False
    assert res.error_code == "ANOMALY_AMBIGUOUS_TIMEZONE"
