"""API integration tests for Pelagos Vessel Suitability Matching (/api/v1/calculator/vessel-suitability).

Validates REST endpoint behavior:
- POST with JSON body for supported corridor (PNW-JPN-BULK / Seattle -> Yokohama)
- GET with query parameters
- Input validation (cargo tonnage <= 0)
- Candidate rankings, transparent explanations, and commercial disclaimers
Traces to Pelagos Milestone D10.2 / OPT-002.
"""

from __future__ import annotations

from typing import Generator
import pytest
from fastapi.testclient import TestClient

from pelagos.api.main import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """TestClient fixture for FastAPI application."""
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestVesselSuitabilityApi:
    """HTTP-level verification of /api/v1/calculator/vessel-suitability."""

    def test_post_seattle_to_yokohama_suitability(self, client: TestClient) -> None:
        """POST /api/v1/calculator/vessel-suitability evaluates genuine database vessels."""
        payload = {
            "origin": "USSEA",
            "destination": "JPYOK",
            "cargo_tonnes": 52000.0,
            "max_vessels": 20,
        }
        response = client.post("/api/v1/calculator/vessel-suitability", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "matched"
        assert data["loading_port_id"] == "USSEA"
        assert data["loading_port_name"] == "Seattle"
        assert data["cargo_tonnes"] == 52000.0
        assert data["target_vessel_class"] == "Panamax / Supramax"
        assert data["total_vessels_evaluated"] > 0
        assert isinstance(data["candidates"], list)

        # Check candidate structure
        if len(data["candidates"]) > 0:
            first = data["candidates"][0]
            assert "mmsi" in first
            assert "suitability_status" in first
            assert "suitability_reason" in first
            assert "distance_to_load_port_nm" in first
            assert first["laycan_feasibility"] == "UNCONFIRMED"
            assert first["commercial_availability"] == "UNCONFIRMED"
            assert first["cargo_capacity_status"] == "UNVERIFIED"
            assert first["verified_cargo_capacity_dwt"] is None
            assert first["is_stale"] is True
            assert "HISTORICAL_OBSERVATION_ONLY" in first["distance_qualification"]

        # Check disclaimers and historical data mode notices
        assert data["data_mode"] == "historical"
        assert "historical" in data["data_mode_notice"].lower()
        assert len(data["disclaimers"]) > 0
        assert any("open-tonnage" in d.lower() for d in data["disclaimers"])
        assert any("historical" in d.lower() for d in data["disclaimers"])

    def test_get_vessel_suitability(self, client: TestClient) -> None:
        """GET /api/v1/calculator/vessel-suitability with query parameters."""
        response = client.get(
            "/api/v1/calculator/vessel-suitability",
            params={
                "origin": "USSEA",
                "destination": "JPYOK",
                "cargo_tonnes": 52000.0,
                "max_vessels": 10,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "matched"
        assert data["loading_port_id"] == "USSEA"
        assert len(data["candidates"]) <= 10

    def test_invalid_cargo_tonnage_validation(self, client: TestClient) -> None:
        """POST /api/v1/calculator/vessel-suitability rejects non-positive cargo tonnage."""
        payload = {
            "origin": "USSEA",
            "destination": "JPYOK",
            "cargo_tonnes": -100.0,
        }
        response = client.post("/api/v1/calculator/vessel-suitability", json=payload)
        assert response.status_code == 422

    def test_zero_cargo_tonnage_validation(self, client: TestClient) -> None:
        """GET /api/v1/calculator/vessel-suitability rejects zero cargo tonnage."""
        response = client.get(
            "/api/v1/calculator/vessel-suitability",
            params={"cargo_tonnes": 0.0},
        )
        assert response.status_code == 422

    def test_unmonitored_corridor_suitability(self, client: TestClient) -> None:
        """Unmonitored corridor handles matching without crashing."""
        payload = {
            "origin": "NLRTM",
            "destination": "SGSIN",
            "cargo_tonnes": 50000.0,
        }
        response = client.post("/api/v1/calculator/vessel-suitability", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unmonitored_corridor_matched"
        assert data["loading_port_id"] == "NLRTM"
