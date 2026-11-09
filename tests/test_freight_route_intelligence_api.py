"""Automated tests for FastAPI route-level freight intelligence endpoints (/api/v1/freight/route-intelligence).

Validates REST endpoint responses for monitored and unmonitored maritime corridors,
reusing existing SQLAlchemy models, observations, and forecasting services.
Traces to REQ-FR-DII-005, REQ-FR-FRF-001, and Pelagos D9.5 Increment 1.
"""

from __future__ import annotations

import datetime
from typing import Generator
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from pelagos.api.main import app
from pelagos.database import SessionLocal
from pelagos.routing.environmental import (
    classify_freshness,
    resolve_corridor_environmental_conditions,
)
from pelagos.routing.bunker import resolve_corridor_bunker_fuel_references


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Test client for FastAPI application."""
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestFreightRouteIntelligenceApi:
    """HTTP-level verification of /api/v1/freight/route-intelligence endpoints."""

    def test_get_seattle_to_yokohama_intelligence(self, client: TestClient) -> None:
        """Supported route Seattle -> Yokohama returns monitored status, latest rate, and baseline forecast."""
        response = client.get("/api/v1/freight/route-intelligence?origin=USSEA&destination=JPYOK")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "monitored"
        assert data["corridor"] is not None
        assert data["corridor"]["route_code"] == "PNW-JPN-BULK"
        assert "Pacific Northwest to Japan" in data["corridor"]["route_name"]
        assert "Grain" in data["corridor"]["cargo_type"]
        assert data["corridor"]["unit"] == "USD/MT"
        assert data["corridor"]["currency"] == "USD"

        # Observed rate from real database
        assert data["latest_observation"] is not None
        assert data["latest_observation"]["rate_value"] == 38.00
        assert data["latest_observation"]["unit"] == "USD/MT"
        assert data["latest_observation"]["observation_date"] == "2026-10-01"
        assert "USDA" in data["latest_observation"]["source_name"]

        # Baseline forecast from real database
        assert data["baseline_forecast"] is not None
        assert data["baseline_forecast"]["model_type"] == "persistence"
        assert data["baseline_forecast"]["predicted_value"] == 38.00
        assert data["baseline_forecast"]["origin_date"] == "2026-10-01"
        assert data["baseline_forecast"]["target_date"] == "2026-11-01"
        assert data["baseline_forecast"]["horizon_months"] == 1
        assert data["baseline_forecast"]["status"] == "forecast_generated"

        # Vessel availability is explicitly unavailable
        assert data["vessel_availability"]["status"] == "unavailable"
        assert "no live ais" in data["vessel_availability"]["reason"].lower() or "not connected" in data["vessel_availability"]["reason"].lower()
        assert data["vessel_availability"]["available_vessels_count"] is None

        # Mandatory disclaimers
        assert len(data["disclaimers"]) >= 2
        assert any("grain" in d.lower() for d in data["disclaimers"])

    def test_get_rotterdam_to_singapore_intelligence_is_unmonitored(self, client: TestClient) -> None:
        """Unsupported route Rotterdam -> Singapore returns unmonitored status without fabricating rates."""
        response = client.get("/api/v1/freight/route-intelligence?origin=NLRTM&destination=SGSIN")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "unmonitored"
        assert data["corridor"] is None
        assert data["latest_observation"] is None
        assert data["baseline_forecast"] is None
        assert data["vessel_availability"]["status"] == "unavailable"
        assert "unmonitored" in (data["message"] or "").lower()
        assert "Rotterdam" in (data["message"] or "")
        assert "Singapore" in (data["message"] or "")

    def test_get_intelligence_by_coordinates(self, client: TestClient) -> None:
        """Coordinates near Seattle and Yokohama resolve to PNW-JPN-BULK."""
        url = "/api/v1/freight/route-intelligence?origin_lon=-124.75&origin_lat=48.50&dest_lon=139.73&dest_lat=35.15"
        response = client.get(url)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "monitored"
        assert data["corridor"]["route_code"] == "PNW-JPN-BULK"
        assert data["latest_observation"]["rate_value"] == 38.00

    def test_get_intelligence_by_direct_route_code(self, client: TestClient) -> None:
        """Direct query by route_code returns corridor intelligence."""
        response = client.get("/api/v1/freight/route-intelligence?route_code=USG-JPN-BULK")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "monitored"
        assert data["corridor"]["route_code"] == "USG-JPN-BULK"
        assert data["latest_observation"] is not None
        assert data["baseline_forecast"] is not None

    def test_post_intelligence_with_json_body(self, client: TestClient) -> None:
        """POST endpoint with JSON body returns identical results."""
        payload = {
            "origin": {"coordinates": [-124.75, 48.50]},
            "destination": {"coordinates": [139.73, 35.15]},
            "model_type": "persistence",
        }
        response = client.post("/api/v1/freight/route-intelligence", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "monitored"
        assert data["corridor"]["route_code"] == "PNW-JPN-BULK"
        assert data["latest_observation"]["rate_value"] == 38.00

    def test_missing_endpoints_returns_unsupported_input(self, client: TestClient) -> None:
        """Query without origin or destination returns unsupported_input status."""
        response = client.get("/api/v1/freight/route-intelligence")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "unsupported_input"
        assert data["corridor"] is None
        assert "Missing" in (data["message"] or "")

    def test_invalid_route_code_returns_unmonitored(self, client: TestClient) -> None:
        """Direct query with nonexistent route_code returns unmonitored without crashing."""
        response = client.get("/api/v1/freight/route-intelligence?route_code=NONEXISTENT-ROUTE")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "unmonitored"
        assert data["corridor"] is None

    def test_dynamic_canonical_port_resolution_pnw_to_japan(self, client: TestClient) -> None:
        """Dynamic database-backed ports in PNW and Japan resolve to PNW-JPN-BULK."""
        # Portland (OR) to Nagoya
        res1 = client.get("/api/v1/freight/route-intelligence?origin=USPDX&destination=JPNGO")
        assert res1.status_code == 200
        d1 = res1.json()
        assert d1["status"] == "monitored"
        assert d1["corridor"]["route_code"] == "PNW-JPN-BULK"
        assert d1["origin_port"]["port_id"] == "USPDX"
        assert d1["origin_port"]["region"] == "U.S. Pacific Northwest"
        assert d1["destination_port"]["port_id"] == "JPNGO"
        assert d1["destination_port"]["region"] == "Japan"

        # Kalama to Kobe
        res2 = client.get("/api/v1/freight/route-intelligence?origin=USKAL&destination=JPUKB")
        assert res2.status_code == 200
        d2 = res2.json()
        assert d2["status"] == "monitored"
        assert d2["corridor"]["route_code"] == "PNW-JPN-BULK"

        # Houston to Osaka (US Gulf Coast to Japan)
        res3 = client.get("/api/v1/freight/route-intelligence?origin=USHOU&destination=JPOSA")
        assert res3.status_code == 200
        d3 = res3.json()
        assert d3["status"] == "monitored"
        assert d3["corridor"]["route_code"] == "USG-JPN-BULK"

    def test_alias_resolution_route_intelligence(self, client: TestClient) -> None:
        """Supported port aliases resolve to monitored corridors via database aliases."""
        response = client.get("/api/v1/freight/route-intelligence?origin=Port+of+Portland&destination=Port+of+Nagoya")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "monitored"
        assert data["corridor"]["route_code"] == "PNW-JPN-BULK"
        assert data["origin_port"]["name"] == "Portland (OR)"
        assert data["destination_port"]["name"] == "Nagoya"

    def test_multi_horizon_all_three_in_default_response(self, client: TestClient) -> None:
        """Default query returns multi-horizon forecasts for T+1, T+3, and T+6."""
        response = client.get("/api/v1/freight/route-intelligence?origin=USPDX&destination=JPNGO")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "monitored"
        # Primary baseline_forecast retains T+1 backward compatibility
        assert data["baseline_forecast"]["horizon_months"] == 1
        assert data["baseline_forecast"]["origin_date"] == "2026-10-01"
        assert data["baseline_forecast"]["target_date"] == "2026-11-01"
        assert data["baseline_forecast"]["predicted_value"] == 38.00

        # All three horizons present in forecasts list
        forecasts = data["forecasts"]
        assert forecasts is not None
        assert len(forecasts) == 3

        horizons = [f["horizon_months"] for f in forecasts]
        target_dates = [f["target_date"] for f in forecasts]
        assert horizons == [1, 3, 6]
        assert target_dates == ["2026-11-01", "2027-01-01", "2027-04-01"]

        # Persistence model property: identical rates across horizons without fabrication
        rates = [f["predicted_value"] for f in forecasts]
        assert rates == [38.00, 38.00, 38.00]
        assert all(f["model_type"] == "persistence" for f in forecasts)
        assert all(f["unit"] == "USD/MT" for f in forecasts)

        # Multi-horizon mapping dictionary
        multi_map = data["multi_horizon_forecasts"]
        assert multi_map is not None
        assert set(multi_map.keys()) == {"T+1", "T+3", "T+6"}
        assert multi_map["T+3"]["target_date"] == "2027-01-01"
        assert multi_map["T+6"]["target_date"] == "2027-04-01"

    def test_multi_horizon_explicit_query_params(self, client: TestClient) -> None:
        """Explicit horizon query parameter returns requested horizon forecast."""
        # T+1
        r1 = client.get("/api/v1/freight/route-intelligence?origin=USSEA&destination=JPYOK&horizon=1")
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["baseline_forecast"]["horizon_months"] == 1
        assert d1["baseline_forecast"]["target_date"] == "2026-11-01"
        assert d1["baseline_forecast"]["predicted_value"] == 38.00

        # T+3 (one quarter ahead)
        r3 = client.get("/api/v1/freight/route-intelligence?origin=USSEA&destination=JPYOK&horizon=3")
        assert r3.status_code == 200
        d3 = r3.json()
        assert d3["baseline_forecast"]["horizon_months"] == 3
        assert d3["baseline_forecast"]["target_date"] == "2027-01-01"
        assert d3["baseline_forecast"]["predicted_value"] == 38.00

        # T+6 (half-year ahead)
        r6 = client.get("/api/v1/freight/route-intelligence?origin=USSEA&destination=JPYOK&horizon=6")
        assert r6.status_code == 200
        d6 = r6.json()
        assert d6["baseline_forecast"]["horizon_months"] == 6
        assert d6["baseline_forecast"]["target_date"] == "2027-04-01"
        assert d6["baseline_forecast"]["predicted_value"] == 38.00

    def test_invalid_horizon_returns_422(self, client: TestClient) -> None:
        """Unsupported horizon values return HTTP 422 with supported options."""
        r_inv = client.get("/api/v1/freight/route-intelligence?origin=USSEA&destination=JPYOK&horizon=2")
        assert r_inv.status_code == 422
        assert "Unsupported forecast horizon" in r_inv.json()["detail"]

    def test_unsupported_port_and_reverse_route(self, client: TestClient) -> None:
        """Unsupported ports or unmonitored routes cleanly return unmonitored status."""
        # Unknown port
        r_unk = client.get("/api/v1/freight/route-intelligence?origin=XYZ99&destination=JPYOK")
        assert r_unk.status_code == 200
        assert r_unk.json()["status"] == "unmonitored"
        assert r_unk.json()["baseline_forecast"] is None
        assert r_unk.json()["forecasts"] is None

        # Registered port with unmonitored trade corridor (India -> Japan)
        r_in = client.get("/api/v1/freight/route-intelligence?origin=INBOM&destination=JPYOK")
        assert r_in.status_code == 200
        assert r_in.json()["status"] == "unmonitored"
        assert r_in.json()["baseline_forecast"] is None

        # Inbound reverse route (Japan -> US Pacific Northwest)
        r_rev = client.get("/api/v1/freight/route-intelligence?origin=JPYOK&destination=USSEA")
        assert r_rev.status_code == 200
        assert r_rev.json()["status"] == "unmonitored"
        assert "Reverse corridor" in r_rev.json()["message"]

    def test_post_intelligence_with_explicit_horizon(self, client: TestClient) -> None:
        """POST endpoint correctly handles explicit horizon in JSON body."""
        payload = {
            "origin": "USPDX",
            "destination": "JPNGO",
            "horizon": 3,
        }
        response = client.post("/api/v1/freight/route-intelligence", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "monitored"
        assert data["baseline_forecast"]["horizon_months"] == 3
        assert data["baseline_forecast"]["target_date"] == "2027-01-01"
        assert data["baseline_forecast"]["predicted_value"] == 38.00


class TestEnvironmentalAndBunkerDataFusion:
    """Verification of Stage C2.2 NOAA buoy environmental observations and bunker fuel references."""

    def test_freshness_classification_tiers(self) -> None:
        """Verify observation age is categorized into deterministic operational freshness tiers."""
        ref_time = datetime.datetime(2026, 11, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)

        # Fresh tier: <= 24 hours
        t_fresh = ref_time - datetime.timedelta(hours=2)
        fresh_status, fresh_age = classify_freshness(t_fresh, as_of=ref_time)
        assert fresh_status == "fresh"
        assert fresh_age == 2.0

        # Boundary fresh: exactly 24 hours
        t_boundary = ref_time - datetime.timedelta(hours=24)
        b_status, b_age = classify_freshness(t_boundary, as_of=ref_time)
        assert b_status == "fresh"
        assert b_age == 24.0

        # Recent tier: 24 < age <= 168 hours (7 days)
        t_recent = ref_time - datetime.timedelta(days=3)
        recent_status, recent_age = classify_freshness(t_recent, as_of=ref_time)
        assert recent_status == "recent"
        assert recent_age == 72.0

        # Stale tier: 168 < age <= 720 hours (30 days)
        t_stale = ref_time - datetime.timedelta(days=14)
        stale_status, stale_age = classify_freshness(t_stale, as_of=ref_time)
        assert stale_status == "stale"
        assert stale_age == 336.0

        # Historical tier: > 720 hours
        t_hist = ref_time - datetime.timedelta(days=60)
        hist_status, hist_age = classify_freshness(t_hist, as_of=ref_time)
        assert hist_status == "historical"
        assert hist_age == 1440.0

    def test_station_corridor_association_pnw(self) -> None:
        """Station 46041 (Cape Elizabeth) associates with PNW-JPN-BULK corridor."""
        db = SessionLocal()
        try:
            summary = resolve_corridor_environmental_conditions(
                db=db,
                corridor_code="PNW-JPN-BULK",
                origin_coords=(-122.68, 45.52),  # Portland (OR)
                as_of="2026-10-30T17:00:00Z",
            )
            assert summary.status == "available"
            assert summary.coverage_status == "departure_waters_monitored"
            assert summary.coverage_extent == "coastal_departure_fairway"
            assert summary.stations_count == 1
            assert len(summary.stations) == 1

            st = summary.stations[0]
            assert st.station_id == "46041"
            assert "Cape Elizabeth" in st.station_name
            assert "Aberdeen" in st.station_name
            assert "Disappointment" not in st.station_name
            assert "Washington Coast" in st.sea_area or "North Pacific" in st.sea_area
            assert st.coordinates.latitude == pytest.approx(47.351, rel=1e-3)
            assert st.coordinates.longitude == pytest.approx(-124.741, rel=1e-3)
            # Fairway distance from Cape Elizabeth (47.351, -124.741) to PNW fairway (46.25, -124.25) is ~69.12 NM
            assert st.distance_to_corridor_fairway_nm == pytest.approx(69.12, abs=1.0)
            # Origin distance to Portland (45.52, -122.68) is ~139.25 NM
            assert st.distance_to_origin_port_nm == pytest.approx(139.25, abs=2.0)
            assert st.freshness == "fresh"
            assert st.data_age_hours < 1.0

            # Verified physical variables and units
            vars_map = st.variables
            assert vars_map["wind_speed_knots"].value == pytest.approx(19.44, rel=1e-2)
            assert vars_map["wind_speed_knots"].unit == "knots"
            assert vars_map["wave_height_meters"].value == pytest.approx(1.3, rel=1e-2)
            assert vars_map["wave_height_meters"].unit == "meters"
            assert vars_map["sea_surface_temp_c"].value == pytest.approx(13.2, rel=1e-2)
            assert vars_map["sea_surface_temp_c"].unit == "degC"
            assert vars_map["air_pressure_hpa"].value == pytest.approx(1011.1, rel=1e-2)
            assert vars_map["air_pressure_hpa"].unit == "hPa"

            # Aggregation block explicit disclaimer
            assert summary.aggregation is not None
            assert summary.aggregation.method == "individual_station_reporting"
            assert "omitted" in summary.aggregation.summary or "no spatial interpolation" in summary.aggregation.summary.lower()
        finally:
            db.close()

    def test_station_corridor_association_usg(self) -> None:
        """Station 42001 (Mid Gulf) associates with USG-JPN-BULK corridor."""
        db = SessionLocal()
        try:
            summary = resolve_corridor_environmental_conditions(
                db=db,
                corridor_code="USG-JPN-BULK",
                origin_coords=(-90.07, 29.95),  # New Orleans
                as_of="2026-10-30T17:00:00Z",
            )
            assert summary.status == "available"
            assert summary.coverage_status == "departure_waters_monitored"
            assert summary.stations_count == 1

            st = summary.stations[0]
            assert st.station_id == "42001"
            assert "Mid Gulf" in st.station_name
            assert "Gulf of Mexico" in st.sea_area
            assert st.coordinates.latitude == pytest.approx(25.897, rel=1e-3)
            assert st.coordinates.longitude == pytest.approx(-89.663, rel=1e-3)
            # Fairway distance is ~181.9 NM
            assert st.distance_to_corridor_fairway_nm == pytest.approx(181.9, abs=2.0)
            assert st.distance_to_origin_port_nm == pytest.approx(243.48, abs=3.0)
            assert st.freshness == "fresh"

            # Check that missing wave height in latest record is cleanly handled as None
            vars_map = st.variables
            assert vars_map["wave_height_meters"].value is None
            assert vars_map["wave_height_meters"].unit == "meters"
            assert vars_map["wind_speed_knots"].value == pytest.approx(11.66, rel=1e-2)
            assert vars_map["sea_surface_temp_c"].value == pytest.approx(27.0, rel=1e-2)
        finally:
            db.close()

    def test_arabian_sea_station_excluded_from_transpacific_corridors(self) -> None:
        """Station AD01 (Arabian Sea) is strictly excluded from Pacific grain corridors."""
        db = SessionLocal()
        try:
            summary_pnw = resolve_corridor_environmental_conditions(db=db, corridor_code="PNW-JPN-BULK")
            station_ids_pnw = [s.station_id for s in summary_pnw.stations]
            assert "AD01" not in station_ids_pnw

            summary_usg = resolve_corridor_environmental_conditions(db=db, corridor_code="USG-JPN-BULK")
            station_ids_usg = [s.station_id for s in summary_usg.stations]
            assert "AD01" not in station_ids_usg
        finally:
            db.close()

    def test_unmonitored_or_empty_corridor_environmental_summary(self) -> None:
        """Unmonitored trade corridor returns structured unavailable summary."""
        db = SessionLocal()
        try:
            summary = resolve_corridor_environmental_conditions(db=db, corridor_code="ROT-SIN-BULK")
            assert summary.status == "unavailable"
            assert summary.coverage_status == "unmonitored"
            assert summary.stations_count == 0
            assert summary.stations == []
        finally:
            db.close()

    def test_multiple_stations_individual_reporting(self) -> None:
        """When multiple stations exist for a corridor, each is reported individually without interpolation."""
        from unittest.mock import MagicMock
        from pelagos.models import EnvironmentalObservation

        # Mock DB with two stations
        mock_db = MagicMock()
        mock_db.query.return_value.distinct.return_value.all.return_value = [("46041",), ("46029",)]

        obs1 = MagicMock(spec=EnvironmentalObservation)
        obs1.station_id = "46041"
        obs1.station_name = "Cape Elizabeth - 45NM NW of Aberdeen, WA"
        obs1.sea_area = "North Pacific / Washington Coast"
        obs1.latitude = 47.351
        obs1.longitude = -124.741
        obs1.observation_time = datetime.datetime(2026, 10, 30, 16, 50, tzinfo=datetime.timezone.utc)
        obs1.wave_height_meters = 1.3
        obs1.wave_period_seconds = 11.0
        obs1.wind_speed_knots = 19.44
        obs1.wind_direction_degrees = 290.0
        obs1.sea_surface_temp_c = 13.2
        obs1.air_pressure_hpa = 1011.1
        obs1.source_name = "NOAA NDBC"
        obs1.quality_status = "verified"

        obs2 = MagicMock(spec=EnvironmentalObservation)
        obs2.station_id = "46029"
        obs2.station_name = "Columbia River Bar"
        obs2.sea_area = "North Pacific / Columbia River Approach"
        obs2.latitude = 46.144
        obs2.longitude = -124.511
        obs2.observation_time = datetime.datetime(2026, 10, 30, 16, 30, tzinfo=datetime.timezone.utc)
        obs2.wave_height_meters = 1.5
        obs2.wave_period_seconds = 10.5
        obs2.wind_speed_knots = 18.0
        obs2.wind_direction_degrees = 285.0
        obs2.sea_surface_temp_c = 13.0
        obs2.air_pressure_hpa = 1011.3
        obs2.source_name = "NOAA NDBC"
        obs2.quality_status = "verified"

        # Successive calls to .first() return obs1 then obs2
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [obs1, obs2]

        summary = resolve_corridor_environmental_conditions(
            db=mock_db,
            corridor_code="PNW-JPN-BULK",
            as_of="2026-10-30T17:00:00Z",
        )

        assert summary.status == "available"
        assert summary.stations_count == 2
        assert len(summary.stations) == 2
        ids = [s.station_id for s in summary.stations]
        assert "46041" in ids
        assert "46029" in ids
        assert summary.aggregation.method == "individual_station_reporting"
        assert "2 associated station(s)" in summary.aggregation.summary

    def test_bunker_fuel_reference_boundary(self) -> None:
        """Bunker fuel references return structured unavailable boundary with required sources and hubs."""
        # PNW corridor
        bnk_pnw = resolve_corridor_bunker_fuel_references("PNW-JPN-BULK")
        assert bnk_pnw.status == "unavailable"
        assert "not licensed or integrated" in bnk_pnw.reason
        assert "VLSFO" in bnk_pnw.required_source
        assert bnk_pnw.unit == "USD/MT"
        assert bnk_pnw.currency == "USD"
        hub_ids_pnw = [h.port_id for h in bnk_pnw.bunkering_hubs_in_scope]
        assert "USSEA" in hub_ids_pnw
        assert "USPDX" in hub_ids_pnw
        assert "JPYOK" in hub_ids_pnw

        # USG corridor
        bnk_usg = resolve_corridor_bunker_fuel_references("USG-JPN-BULK")
        assert bnk_usg.status == "unavailable"
        hub_ids_usg = [h.port_id for h in bnk_usg.bunkering_hubs_in_scope]
        assert "USHOU" in hub_ids_usg
        assert "USMSY" in hub_ids_usg
        assert "PAPAC" in hub_ids_usg
        assert "JPYOK" in hub_ids_usg

        # Unmonitored corridor
        bnk_unk = resolve_corridor_bunker_fuel_references("UNKNOWN-CORRIDOR")
        assert bnk_unk.status == "unmonitored"
        assert bnk_unk.bunkering_hubs_in_scope == []

    def test_api_pnw_route_environmental_and_bunker_fusion(self, client: TestClient) -> None:
        """GET /route-intelligence for Seattle -> Yokohama enriches response with environmental and fuel context."""
        # Default request evaluates against actual reference time (November 9, 2026) -> stale freshness
        response = client.get("/api/v1/freight/route-intelligence?origin=USSEA&destination=JPYOK")
        assert response.status_code == 200
        data = response.json()

        # Freight intelligence preserved
        assert data["status"] == "monitored"
        assert data["corridor"]["route_code"] == "PNW-JPN-BULK"
        assert data["latest_observation"]["rate_value"] == 38.00
        assert len(data["forecasts"]) == 3

        # Environmental conditions present with verified Cape Elizabeth NOAA metadata
        env = data["environmental_conditions"]
        assert env is not None
        assert env["status"] == "available"
        assert env["coverage_status"] == "departure_waters_monitored"
        assert env["stations_count"] == 1
        st = env["stations"][0]
        assert st["station_id"] == "46041"
        assert "Cape Elizabeth" in st["station_name"]
        assert "Disappointment" not in st["station_name"]
        assert st["coordinates"]["latitude"] == pytest.approx(47.351, rel=1e-3)
        assert st["coordinates"]["longitude"] == pytest.approx(-124.741, rel=1e-3)
        assert st["distance_to_corridor_fairway_nm"] == pytest.approx(69.12, abs=1.0)
        assert st["distance_to_origin_port_nm"] == pytest.approx(68.98, abs=2.0)
        assert st["variables"]["wind_speed_knots"]["value"] == pytest.approx(19.44, rel=1e-2)

        # Freshness is stale under actual November 9 reference (> 7 days from Oct 30)
        assert st["freshness"] == "stale"
        assert st["data_age_hours"] > 168.0

        # Bunker fuel references present
        bunker = data["bunker_fuel_references"]
        assert bunker is not None
        assert bunker["status"] == "unavailable"
        assert len(bunker["bunkering_hubs_in_scope"]) == 3
        assert "commercial bunker spot price" in bunker["reason"].lower()

        # Data coverage summary present with honest stale status
        cov = data["data_coverage"]
        assert cov is not None
        assert cov["status"] == "partial"
        assert cov["freight_market"]["status"] == "active"
        assert cov["forecasts"]["status"] == "active"
        assert cov["environmental"]["status"] == "stale"
        assert cov["bunker_fuel"]["status"] == "unavailable"
        assert cov["vessel_fleet"]["status"] == "unavailable"

    def test_api_usg_route_environmental_and_bunker_fusion(self, client: TestClient) -> None:
        """GET /route-intelligence for Houston -> Yokohama enriches response with US Gulf buoy observations."""
        response = client.get("/api/v1/freight/route-intelligence?origin=USHOU&destination=JPYOK")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "monitored"
        assert data["corridor"]["route_code"] == "USG-JPN-BULK"

        env = data["environmental_conditions"]
        assert env is not None
        assert env["status"] == "available"
        st = env["stations"][0]
        assert st["station_id"] == "42001"
        assert "Mid Gulf" in st["station_name"]
        assert st["distance_to_corridor_fairway_nm"] == pytest.approx(181.9, abs=2.0)
        assert st["distance_to_origin_port_nm"] == pytest.approx(380.26, abs=3.0)
        assert st["freshness"] == "stale"
        assert st["data_age_hours"] > 168.0
        assert st["variables"]["wave_height_meters"]["value"] is None  # Missing wave handled cleanly
        assert st["variables"]["wind_speed_knots"]["value"] == pytest.approx(11.66, rel=1e-2)

        bunker = data["bunker_fuel_references"]
        assert bunker is not None
        assert bunker["status"] == "unavailable"
        hub_ports = [h["port_id"] for h in bunker["bunkering_hubs_in_scope"]]
        assert "USHOU" in hub_ports
        assert "PAPAC" in hub_ports

        assert data["data_coverage"]["environmental"]["status"] == "stale"

    def test_api_unmonitored_route_suppresses_environmental_and_bunker(self, client: TestClient) -> None:
        """Unmonitored trade routes suppress environmental observations and bunker references."""
        response = client.get("/api/v1/freight/route-intelligence?origin=NLRTM&destination=SGSIN")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "unmonitored"
        assert data["corridor"] is None
        assert data["latest_observation"] is None
        assert data["baseline_forecast"] is None
        assert data["environmental_conditions"] is None
        assert data["bunker_fuel_references"] is None
        assert data["data_coverage"]["status"] == "unmonitored"
        assert data["data_coverage"]["freight_market"]["status"] == "unmonitored"
        assert data["data_coverage"]["environmental"]["status"] == "unmonitored"

    def test_api_partial_source_failure_preserves_freight_intelligence(self, client: TestClient) -> None:
        """Partial environmental failure does NOT break freight market intelligence response."""
        with patch(
            "pelagos.api.routes.freight.resolve_corridor_environmental_conditions",
            side_effect=RuntimeError("Simulated remote buoy telemetry failure"),
        ):
            response = client.get("/api/v1/freight/route-intelligence?origin=USSEA&destination=JPYOK")
            assert response.status_code == 200
            data = response.json()

            # Core freight market intelligence is fully intact
            assert data["status"] == "monitored"
            assert data["corridor"]["route_code"] == "PNW-JPN-BULK"
            assert data["latest_observation"]["rate_value"] == 38.00
            assert len(data["forecasts"]) == 3
            # Environmental conditions degraded gracefully to None
            assert data["environmental_conditions"] is None
            # Bunker fuel references still returned
            assert data["bunker_fuel_references"]["status"] == "unavailable"
