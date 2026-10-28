"""Automated unit tests for Pelagos commercial maritime freight corridor resolution.

Validates that route endpoints (ports or hydrographic coordinates) are resolved strictly
through explicit regional and port mappings without geometric or distance inference.
Traces to REQ-FR-DII-005, REQ-FR-FRF-001, and Pelagos D9.5 Increment 1.
"""

from __future__ import annotations

from typing import Tuple
import pytest
from pelagos.routing.corridors import (
    CorridorStatus,
    CorridorResolution,
    resolve_endpoint_to_port,
    resolve_freight_corridor,
    resolve_port_region,
)
from pelagos.routing.ports import PortRepository
from pelagos.routing.schemas import PortIdentifierOrCoords
from pelagos.routing.geometry import (
    _GEOD,
    METERS_PER_NAUTICAL_MILE,
    geodesic_distance_nm,
)


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

    def test_snapping_radius_threshold_edge(self, port_repo: PortRepository) -> None:
        """Verify the 150.0 NM commercial port-snapping boundary contract:
        - Production contract: PortRepository.find_nearest_port evaluates unrounded
          `dist <= max_distance_nm` without floating-point tolerance buffers.
        - 149.9 NM: resolves to expected port (dist < 150.0 NM) and monitored corridor.
        - 150.0 NM: verifies the exact inclusive production boundary (dist == 150.0 NM <= 150.0 NM).
        - 150.1 NM: fails commercial snapping (dist > 150.0 NM) and returns unmonitored.
        """
        assert _GEOD is not None, "pyproj WGS 84 Geod required for geodesic boundary verification"
        seattle = port_repo.get_port("USSEA")
        assert seattle is not None
        slon, slat = seattle.coordinates

        # Project southwest (225° SW) into the open Pacific Ocean where no other cataloged
        # ports exist within 2,000 NM (closest is USMSY at 2,004.7 NM).
        # In the WGS 84 ellipsoidal model, projecting along azimuth 225.0° for 150.0 * 1852.0 m
        # inverts via geodesic_distance_nm to bit-exact 150.0 (0x1.2c00000000000p+7), providing
        # a deterministic test of the unrounded `<=` comparison without floating-point drift.
        azimuth_deg = 225.0

        def _project_test_point(target_distance_nm: float) -> Tuple[float, float, float]:
            target_lon, target_lat, _ = _GEOD.fwd(
                slon, slat, azimuth_deg, target_distance_nm * METERS_PER_NAUTICAL_MILE
            )
            computed_dist = geodesic_distance_nm(target_lon, target_lat, slon, slat)
            return float(target_lon), float(target_lat), computed_dist

        # Case A: 149.9 NM (Within snapping radius)
        lon_149_9, lat_149_9, unrounded_149_9 = _project_test_point(149.9)
        assert unrounded_149_9 < 150.0
        assert abs(unrounded_149_9 - 149.9) < 1e-9

        nearest_149_9 = port_repo.find_nearest_port(lon_149_9, lat_149_9, max_distance_nm=150.0)
        assert nearest_149_9 is not None
        assert nearest_149_9[0].port_id == "USSEA"
        assert nearest_149_9[1] == 149.9

        port_149_9, _, err_149_9 = resolve_endpoint_to_port([lon_149_9, lat_149_9], port_repo, max_snap_distance_nm=150.0)
        assert err_149_9 is None
        assert port_149_9 is not None and port_149_9.port_id == "USSEA"

        res_149_9 = resolve_freight_corridor(
            origin=[lon_149_9, lat_149_9],
            destination="JPYOK",
            port_repo=port_repo,
            max_snap_distance_nm=150.0,
        )
        assert res_149_9.status == CorridorStatus.MONITORED
        assert res_149_9.route_code == "PNW-JPN-BULK"
        assert res_149_9.origin_port_id == "USSEA"

        # Case B: 150.0 NM (Exact boundary - verified against unrounded 'dist <= max_distance_nm')
        lon_150_0, lat_150_0, unrounded_150_0 = _project_test_point(150.0)
        # Verify the production resolver's exact comparison predicate:
        assert unrounded_150_0 <= 150.0
        # Verify bit-exact equality to establish that the test point lands squarely on the threshold
        assert unrounded_150_0 == 150.0

        nearest_150_0 = port_repo.find_nearest_port(lon_150_0, lat_150_0, max_distance_nm=150.0)
        assert nearest_150_0 is not None
        assert nearest_150_0[0].port_id == "USSEA"
        assert nearest_150_0[1] == 150.0

        port_150_0, _, err_150_0 = resolve_endpoint_to_port([lon_150_0, lat_150_0], port_repo, max_snap_distance_nm=150.0)
        assert err_150_0 is None
        assert port_150_0 is not None and port_150_0.port_id == "USSEA"

        res_150_0 = resolve_freight_corridor(
            origin=[lon_150_0, lat_150_0],
            destination="JPYOK",
            port_repo=port_repo,
            max_snap_distance_nm=150.0,
        )
        assert res_150_0.status == CorridorStatus.MONITORED
        assert res_150_0.route_code == "PNW-JPN-BULK"
        assert res_150_0.origin_port_id == "USSEA"

        # Case C: 150.1 NM (Outside snapping radius - fails commercial port resolution)
        lon_150_1, lat_150_1, unrounded_150_1 = _project_test_point(150.1)
        assert unrounded_150_1 > 150.0
        assert abs(unrounded_150_1 - 150.1) < 1e-9

        nearest_150_1 = port_repo.find_nearest_port(lon_150_1, lat_150_1, max_distance_nm=150.0)
        assert nearest_150_1 is None

        port_150_1, coords_150_1, err_150_1 = resolve_endpoint_to_port([lon_150_1, lat_150_1], port_repo, max_snap_distance_nm=150.0)
        assert port_150_1 is None
        assert coords_150_1 == [lon_150_1, lat_150_1]

        res_150_1 = resolve_freight_corridor(
            origin=[lon_150_1, lat_150_1],
            destination="JPYOK",
            port_repo=port_repo,
            max_snap_distance_nm=150.0,
        )
        assert res_150_1.status == CorridorStatus.UNMONITORED
        assert res_150_1.route_code is None
        assert res_150_1.origin_port_id is None
        assert "does not map to any monitored commercial freight region" in res_150_1.reason
