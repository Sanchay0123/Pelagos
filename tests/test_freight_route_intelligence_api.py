"""Automated tests for FastAPI route-level freight intelligence endpoints (/api/v1/freight/route-intelligence).

Validates REST endpoint responses for monitored and unmonitored maritime corridors,
reusing existing SQLAlchemy models, observations, and forecasting services.
Traces to REQ-FR-DII-005, REQ-FR-FRF-001, and Pelagos D9.5 Increment 1.
"""

from __future__ import annotations

from typing import Generator
import pytest
from fastapi.testclient import TestClient

from pelagos.api.main import app


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
        assert data["latest_observation"]["rate_value"] == 37.31
        assert data["latest_observation"]["unit"] == "USD/MT"
        assert data["latest_observation"]["observation_date"] == "2026-09-01"
        assert "USDA" in data["latest_observation"]["source_name"]

        # Baseline forecast from real database
        assert data["baseline_forecast"] is not None
        assert data["baseline_forecast"]["model_type"] == "persistence"
        assert data["baseline_forecast"]["predicted_value"] == 37.31
        assert data["baseline_forecast"]["origin_date"] == "2026-09-01"
        assert data["baseline_forecast"]["target_date"] == "2026-10-01"
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
        assert data["latest_observation"]["rate_value"] == 37.31

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
        assert data["latest_observation"]["rate_value"] == 37.31

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
