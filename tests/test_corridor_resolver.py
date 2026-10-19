"""Automated unit tests for Pelagos commercial maritime freight corridor resolution.

Validates that route endpoints (ports or hydrographic coordinates) are resolved strictly
through explicit regional and port mappings without geometric or distance inference.
Traces to REQ-FR-DII-005, REQ-FR-FRF-001, and Pelagos D9.5 Increment 1.
"""

from __future__ import annotations

import pytest
from pelagos.routing.corridors import (
    CorridorStatus,
    CorridorResolution,
    resolve_freight_corridor,
    resolve_port_region,
)
from pelagos.routing.ports import PortRepository
from pelagos.routing.schemas import PortIdentifierOrCoords


@pytest.fixture
def port_repo() -> PortRepository:
    """In-memory port repository fixture."""
    return PortRepository()


class TestFreightCorridorResolver:
    """Test suite for commercial freight corridor resolution."""

    def test_seattle_to_yokohama_resolves_to_pnw_jpn_bulk(self, port_repo: PortRepository) -> None:
        """Seattle (USSEA) -> Yokohama (JPYOK) resolves to PNW-JPN-BULK."""
        res = resolve_freight_corridor(origin="USSEA", destination="JPYOK", port_repo=port_repo)
        assert res.status == CorridorStatus.MONITORED
        assert res.route_code == "PNW-JPN-BULK"
        assert "Pacific Northwest to Japan" in (res.route_name or "")
        assert res.commodity == "Agricultural Bulk Grain"
        assert "Panamax" in (res.vessel_class or "")
        assert res.unit == "USD/MT"
        assert res.currency == "USD"
        assert res.benchmark_distance_nm == 4520.0
        assert res.origin_port_id == "USSEA"
        assert res.destination_port_id == "JPYOK"

    def test_us_gulf_to_japan_resolves_to_usg_jpn_bulk(self, port_repo: PortRepository) -> None:
        """New Orleans (USMSY) -> Yokohama (JPYOK) resolves to USG-JPN-BULK."""
        res = resolve_freight_corridor(origin="USMSY", destination="JPYOK", port_repo=port_repo)
        assert res.status == CorridorStatus.MONITORED
        assert res.route_code == "USG-JPN-BULK"
        assert "U.S. Gulf to Japan" in (res.route_name or "")
        assert res.commodity == "Agricultural Bulk Grain"
        assert res.benchmark_distance_nm == 9150.0
        assert res.origin_port_id == "USMSY"
        assert res.destination_port_id == "JPYOK"

    def test_rotterdam_to_singapore_is_explicitly_unmonitored(self, port_repo: PortRepository) -> None:
        """Rotterdam (NLRTM) -> Singapore (SGSIN) returns unmonitored status without fabricating data."""
        res = resolve_freight_corridor(origin="NLRTM", destination="SGSIN", port_repo=port_repo)
        assert res.status == CorridorStatus.UNMONITORED
        assert res.route_code is None
        assert res.origin_port_id == "NLRTM"
        assert res.destination_port_id == "SGSIN"
        assert "unmonitored" in res.reason.lower()
        assert "Rotterdam" in res.reason
        assert "Singapore" in res.reason

    def test_reverse_direction_japan_to_seattle_is_unmonitored(self, port_repo: PortRepository) -> None:
        """Reverse corridor Yokohama (JPYOK) -> Seattle (USSEA) is unmonitored."""
        res = resolve_freight_corridor(origin="JPYOK", destination="USSEA", port_repo=port_repo)
        assert res.status == CorridorStatus.UNMONITORED
        assert res.route_code is None
        assert "reverse" in res.reason.lower()
        assert "outbound" in res.reason.lower()

    def test_coordinate_snapping_for_seattle_and_yokohama(self, port_repo: PortRepository) -> None:
        """Explicit coordinates near Seattle and Yokohama resolve to PNW-JPN-BULK."""
        seattle_coords = [-124.75, 48.50]
        yokohama_coords = [139.73, 35.15]
        res = resolve_freight_corridor(origin=seattle_coords, destination=yokohama_coords, port_repo=port_repo)
        assert res.status == CorridorStatus.MONITORED
        assert res.route_code == "PNW-JPN-BULK"

    def test_pydantic_endpoint_objects_resolve(self, port_repo: PortRepository) -> None:
        """PortIdentifierOrCoords objects resolve identically to strings."""
        orig = PortIdentifierOrCoords(unlocode="USSEA")
        dest = PortIdentifierOrCoords(unlocode="JPYOK")
        res = resolve_freight_corridor(origin=orig, destination=dest, port_repo=port_repo)
        assert res.status == CorridorStatus.MONITORED
        assert res.route_code == "PNW-JPN-BULK"

    def test_landlocked_or_distant_coordinates_return_unmonitored(self, port_repo: PortRepository) -> None:
        """Oceanic or land coordinates far from monitored ports return unmonitored."""
        mid_atlantic = [-30.0, 30.0]
        mid_indian_ocean = [75.0, -20.0]
        res = resolve_freight_corridor(origin=mid_atlantic, destination=mid_indian_ocean, port_repo=port_repo)
        assert res.status == CorridorStatus.UNMONITORED
        assert res.route_code is None
        assert "does not map" in res.reason

    def test_no_distance_based_corridor_inference(self, port_repo: PortRepository) -> None:
        """A route with similar distance (~4500 NM) but unrelated endpoints does not match PNW-JPN-BULK."""
        res = resolve_freight_corridor(origin="NLRTM", destination="CASP_TEST", port_repo=port_repo)
        assert res.status == CorridorStatus.UNMONITORED
        assert res.route_code is None
