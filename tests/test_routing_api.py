"""Automated tests for FastAPI maritime routing endpoints (/api/v1/routing).

Validates REST endpoint registration, schema validation, prototype routing,
antimeridian segment support, polar boundary exclusion, fail-closed cache handling,
and opt-in MARNET integration testing. Traces to REQ-FR-OMR-001, REQ-FR-OMR-002, and ROUT-004.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator
import pytest
from fastapi.testclient import TestClient

from pelagos.api.main import app
from pelagos.api.routes.routing import get_routing_engine
from pelagos.routing.schemas import RoutingStatus
from pelagos.routing.service import MaritimeRoutingEngine


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Test client for FastAPI application with isolated engine dependency."""
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestRoutingApiEndpoints:
    """HTTP-level verification of /api/v1/routing endpoints."""

    def test_routing_health_endpoint(self, client: TestClient) -> None:
        """Readiness probe reports subsystem status without heavy graph loading."""
        response = client.get("/api/v1/routing/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "pelagos-routing"
        assert data["prototype_ready"] is True
        assert "marnet_cache_available" in data
        assert "Pelagos is NOT a navigation-grade safety system" in data["safety_disclaimer"]

    def test_calculate_validation_error_on_missing_body(self, client: TestClient) -> None:
        """Missing required origin/destination fields returns HTTP 422."""
        response = client.post("/api/v1/routing/calculate", json={})
        assert response.status_code == 422
        errors = response.json().get("detail", [])
        fields = [err.get("loc", [])[-1] for err in errors]
        assert "origin" in fields
        assert "destination" in fields

    def test_calculate_prototype_route_by_port_codes(self, client: TestClient) -> None:
        """Prototype routing calculation with valid UN/LOCODE identifiers."""
        payload = {
            "origin": "USMSY",
            "destination": "JPYOK",
            "canal_policy": "allow",
            "routing_mode": "prototype",
        }
        response = client.post("/api/v1/routing/calculate", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == RoutingStatus.CALCULATED.value
        assert data["routing_mode"] == "prototype"
        assert data["route_method"] == "prototype_test_network_graph"
        assert len(data["geometry"]) >= 2
        assert len(data["geometry_segments"]) == 1
        assert data["summary"]["total_distance_nm"] > 0
        assert any("panama" in c.lower() for c in data["summary"]["canals_transited"])
        assert len(data["waypoints"]) >= 2
        assert data["error_message"] is None

    def test_calculate_prototype_route_by_coordinates(self, client: TestClient) -> None:
        """Prototype routing calculation with explicit coordinate objects."""
        payload = {
            "origin": {"coordinates": [-89.2, 29.1]},
            "destination": {"coordinates": [139.7, 35.3]},
            "canal_policy": "allow",
            "routing_mode": "prototype",
        }
        response = client.post("/api/v1/routing/calculate", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == RoutingStatus.CALCULATED.value
        assert data["routing_mode"] == "prototype"
        assert len(data["geometry"]) >= 2

    def test_calculate_invalid_canal_policy(self, client: TestClient) -> None:
        """Unsupported canal avoidance policy fails with domain status invalid_input."""
        payload = {
            "origin": "USMSY",
            "destination": "JPYOK",
            "canal_policy": "avoid_equator",
            "routing_mode": "prototype",
        }
        response = client.post("/api/v1/routing/calculate", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == RoutingStatus.INVALID_INPUT.value
        assert data["geometry"] == []
        assert data["geometry_segments"] == []
        assert "Unsupported canal policy" in data["error_message"]

    def test_calculate_unknown_port_identifier(self, client: TestClient) -> None:
        """Unknown origin port identifier returns invalid_input with empty geometry."""
        payload = {
            "origin": "ZZZ99",
            "destination": "JPYOK",
            "routing_mode": "prototype",
        }
        response = client.post("/api/v1/routing/calculate", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == RoutingStatus.INVALID_INPUT.value
        assert data["geometry"] == []
        assert data["geometry_segments"] == []
        assert "Unknown or unsupported port identifier" in data["error_message"]

    def test_missing_marnet_cache_fails_closed_without_fallback(self, client: TestClient) -> None:
        """Missing MARNET cache returns configuration_error and never falls back to prototype."""
        # Use dependency injection to point to an empty isolated directory
        isolated_engine = MaritimeRoutingEngine(marnet_cache_dir=Path("/nonexistent/routing/cache"))
        app.dependency_overrides[get_routing_engine] = lambda: isolated_engine

        payload = {
            "origin": "USMSY",
            "destination": "JPYOK",
            "routing_mode": "marnet",
        }
        response = client.post("/api/v1/routing/calculate", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == RoutingStatus.CONFIGURATION_ERROR.value
        assert data["routing_mode"] == "marnet"
        assert data["route_method"] == "marnet_50km_generalized_graph"
        assert data["geometry"] == []
        assert data["geometry_segments"] == []
        assert "MARNET graph cache not found or failed integrity check" in data["error_message"]


class TestMarnetApiRouting:
    """MARNET routing API validation requiring compiled graph artifact."""

    @pytest.fixture(autouse=True)
    def check_marnet_cache(self) -> None:
        """Ensure MARNET cache artifact is present before running MARNET queries."""
        cache_file = Path(".cache/pelagos/routing/marnet_network.json")
        if not cache_file.exists():
            pytest.skip("MARNET cache artifact not available; run scripts/ingest_marnet.py")

    def test_marnet_polar_endpoint_rejection(self, client: TestClient) -> None:
        """Endpoint at or beyond polar boundary (|lat| >= 66.5) returns unsupported_route."""
        payload = {
            "origin": {"coordinates": [15.0, 72.0]},  # 72.0 deg N (Arctic Circle)
            "destination": {"coordinates": [3.0, 55.0]},
            "routing_mode": "marnet",
        }
        response = client.post("/api/v1/routing/calculate", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == RoutingStatus.UNSUPPORTED_ROUTE.value
        assert data["geometry"] == []
        assert data["geometry_segments"] == []
        assert "polar waters" in data["error_message"].lower()

    def test_marnet_landlocked_coordinate_rejection(self, client: TestClient) -> None:
        """Inland coordinate > 50 NM from marine grid returns unsupported_route."""
        payload = {
            "origin": {"coordinates": [-104.99, 39.74]},  # Denver, Colorado
            "destination": {"coordinates": [-89.2, 29.1]},
            "routing_mode": "marnet",
        }
        response = client.post("/api/v1/routing/calculate", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == RoutingStatus.UNSUPPORTED_ROUTE.value
        assert data["geometry"] == []
        assert data["geometry_segments"] == []
        assert "too distant from navigable marnet sea lanes" in data["error_message"].lower()

    def test_marnet_antimeridian_unsegmented_client_suppression(self, client: TestClient) -> None:
        """Antimeridian route requested with supports_segments=False is suppressed to prevent wrap chords."""
        payload = {
            "origin": "USSEA",
            "destination": "JPYOK",
            "routing_mode": "marnet",
            "supports_segments": False,
        }
        response = client.post("/api/v1/routing/calculate", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == RoutingStatus.UNSUPPORTED_ROUTE.value
        assert data["geometry"] == []
        assert data["geometry_segments"] == []
        assert "requires segment-aware renderer" in data["error_message"]

    def test_marnet_antimeridian_segmented_client_success(self, client: TestClient) -> None:
        """Antimeridian route requested with supports_segments=True returns disjoint segments."""
        payload = {
            "origin": "USSEA",
            "destination": "JPYOK",
            "routing_mode": "marnet",
            "supports_segments": True,
        }
        response = client.post("/api/v1/routing/calculate", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == RoutingStatus.CALCULATED.value
        assert data["geometry"] == []  # Continuous flat chord suppressed
        assert len(data["geometry_segments"]) >= 2
        # Verify first segment ends at antimeridian and second segment begins at antimeridian
        assert abs(abs(data["geometry_segments"][0][-1][0]) - 180.0) < 1e-4
        assert abs(abs(data["geometry_segments"][1][0][0]) - 180.0) < 1e-4

    @pytest.mark.skipif(
        not os.environ.get("PELAGOS_MARNET_INTEGRATION"),
        reason="Opt-in MARNET integration test (set PELAGOS_MARNET_INTEGRATION=1)",
    )
    def test_opt_in_marnet_full_route_calculation(self, client: TestClient) -> None:
        """Opt-in integration benchmark for Rotterdam -> Singapore via Suez Canal."""
        payload = {
            "origin": "NLRTM",
            "destination": "SGSIN",
            "canal_policy": "allow",
            "routing_mode": "marnet",
            "supports_segments": True,
        }
        response = client.post("/api/v1/routing/calculate", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == RoutingStatus.CALCULATED.value
        assert data["route_method"] == "marnet_50km_generalized_graph"
        assert abs(data["summary"]["total_distance_nm"] - 8409.79) < 2.0
        assert "suez" in data["summary"]["canals_transited"]
        assert len(data["geometry"]) >= 2
        assert len(data["geometry_segments"]) == 1

    def test_get_ports_endpoint(self, client: TestClient) -> None:
        """GET /api/v1/routing/ports returns verified port records from hydrographic fixture."""
        response = client.get("/api/v1/routing/ports")
        assert response.status_code == 200
        ports = response.json()
        assert isinstance(ports, list)
        assert len(ports) >= 8

        port_ids = {p["port_id"] for p in ports}
        assert "USSEA" in port_ids
        assert "JPYOK" in port_ids
        assert "NLRTM" in port_ids
        assert "SGSIN" in port_ids
        assert "USMSY" in port_ids

        seattle = next(p for p in ports if p["port_id"] == "USSEA")
        assert seattle["name"] == "Seattle"
        assert seattle["country"] == "United States"
        assert len(seattle["coordinates"]) == 2
