"""API integration tests for Pelagos Voyage Freight-Cost Calculator (/api/v1/calculator/voyage-freight).

Validates REST endpoint behavior:
- POST with JSON body for supported and unsupported trade lanes
- GET with query parameters
- Input validation (positive cargo tonnage)
- Commercial disclaimers and structured calculation results
Traces to Pelagos Milestone D10 / OPT-001.
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


class TestVoyageCalculatorApi:
    """HTTP-level verification of /api/v1/calculator/voyage-freight."""

    def test_post_seattle_to_yokohama_calculation(self, client: TestClient) -> None:
        """POST /api/v1/calculator/voyage-freight for Seattle -> Yokohama calculates gross freight cost."""
        payload = {
            "origin": "USSEA",
            "destination": "JPYOK",
            "cargo_tonnes": 52000.0,
        }
        response = client.post("/api/v1/calculator/voyage-freight", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "calculated"
        assert data["cargo_tonnes"] == 52000.0
        assert data["cargo_unit"] == "MT"
        assert data["route"]["route_code"] == "PNW-JPN-BULK"
        assert data["applicable_rate"]["rate_value"] == 38.00
        assert data["applicable_rate"]["unit"] == "USD/MT"
        assert data["applicable_rate"]["currency"] == "USD"
        assert "USDA" in data["applicable_rate"]["source_name"]

        # 52000 * 38.00 = 1,976,000.00
        assert data["gross_freight_cost"] == 1976000.0
        assert data["gross_freight_cost_currency"] == "USD"
        assert data["calculation_formula"] == "gross_freight_cost = cargo_tonnes * rate_per_tonne"

        # Distance metadata
        assert data["distance"] is not None
        assert data["distance"]["distance_unit"] == "NM"
        assert data["distance"]["distance_nm"] > 0

        # Disclaimers
        assert len(data["disclaimers"]) >= 2
        assert any("grain" in d.lower() for d in data["disclaimers"])

    def test_post_us_gulf_to_japan_calculation(self, client: TestClient) -> None:
        """POST /api/v1/calculator/voyage-freight for US Gulf -> Yokohama calculates gross freight cost."""
        payload = {
            "origin": "USMSY",
            "destination": "JPYOK",
            "cargo_tonnes": 65000.0,
        }
        response = client.post("/api/v1/calculator/voyage-freight", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "calculated"
        assert data["cargo_tonnes"] == 65000.0
        assert data["route"]["route_code"] == "USG-JPN-BULK"
        assert data["applicable_rate"]["rate_value"] == 76.00
        # 65000 * 76.00 = 4,940,000.00
        assert data["gross_freight_cost"] == 4940000.0

    def test_post_direct_route_code(self, client: TestClient) -> None:
        """POST with route_code directly calculates without requiring port endpoints."""
        payload = {
            "route_code": "PNW-JPN-BULK",
            "cargo_tonnes": 40000.0,
        }
        response = client.post("/api/v1/calculator/voyage-freight", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "calculated"
        assert data["route"]["route_code"] == "PNW-JPN-BULK"
        assert data["gross_freight_cost"] == round(40000.0 * 38.00, 2)

    def test_post_unmonitored_route_returns_unavailable(self, client: TestClient) -> None:
        """POST with unmonitored trade lane returns unavailable without inventing rates."""
        payload = {
            "origin": "NLRTM",
            "destination": "SGSIN",
            "cargo_tonnes": 50000.0,
        }
        response = client.post("/api/v1/calculator/voyage-freight", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "unavailable"
        assert data["gross_freight_cost"] is None
        assert data["applicable_rate"] is None
        assert "unmonitored" in (data["message"] or "").lower()

    def test_post_negative_cargo_tonnage_returns_422(self, client: TestClient) -> None:
        """POST with negative tonnage is rejected with 422 Unprocessable Entity."""
        payload = {
            "origin": "USSEA",
            "destination": "JPYOK",
            "cargo_tonnes": -100.0,
        }
        response = client.post("/api/v1/calculator/voyage-freight", json=payload)
        assert response.status_code == 422

    def test_get_voyage_freight_calculation(self, client: TestClient) -> None:
        """GET /api/v1/calculator/voyage-freight with query params returns valid calculation."""
        url = "/api/v1/calculator/voyage-freight?cargo_tonnes=50000&route_code=PNW-JPN-BULK"
        response = client.get(url)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "calculated"
        assert data["cargo_tonnes"] == 50000.0
        assert data["gross_freight_cost"] == 1900000.0

    def test_get_voyage_freight_zero_tonnes_rejected(self, client: TestClient) -> None:
        """GET /api/v1/calculator/voyage-freight with cargo_tonnes=0 is rejected with 422."""
        url = "/api/v1/calculator/voyage-freight?cargo_tonnes=0&route_code=PNW-JPN-BULK"
        response = client.get(url)
        assert response.status_code == 422
