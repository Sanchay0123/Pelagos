"""Pelagos D9.2 - Maritime Routing Engine Foundation Tests.

Validates:
1. Supported port pair routing with reproducible route geometry.
2. Geodesic distance precision and nautical-mile/kilometre conversion (1 NM = 1.852 km).
3. Rejection of invalid identifiers, malformed coordinates, and inland points.
4. Detection of disconnected endpoints (unsupported_route) with strictly zero straight-line fallback.
5. Deterministic bit-for-bit repeatability across repeated requests.
6. Comparison against independent hydrographic reference (NGA Pub. 151) and Pelagos corridor benchmarks.
7. Canal bypass policy enforcement (REQ-FR-OMR-003).
"""

from __future__ import annotations

import math
from typing import List

import pytest

from pelagos.routing.geometry import (
    KM_PER_NAUTICAL_MILE,
    METERS_PER_NAUTICAL_MILE,
    NAV_SAFETY_DISCLAIMER,
    geodesic_distance_nm,
    km_to_nm,
    nm_to_km,
    validate_coordinates,
)
from pelagos.routing.graph import MaritimeGraph
from pelagos.routing.ports import PortRepository
from pelagos.routing.schemas import (
    PortIdentifierOrCoords,
    RoutingRequest,
    RoutingResult,
)
from pelagos.routing.service import MaritimeRoutingEngine


@pytest.fixture
def engine() -> MaritimeRoutingEngine:
    """Fixture providing initialized MaritimeRoutingEngine."""
    return MaritimeRoutingEngine()


class TestPortFixtureAndResolution:
    """Tests for Phase 3: Port Reference Fixture and Resolution."""

    def test_fixture_loads_registered_ports(self, engine: MaritimeRoutingEngine) -> None:
        """Verify all 8 registered ports load with required Phase 3 audit metadata."""
        ports = engine.port_repo.list_ports()
        assert len(ports) == 8

        port_ids = {p.port_id for p in ports}
        assert port_ids == {
            "USMSY",
            "JPYOK",
            "USSEA",
            "SGSIN",
            "NLRTM",
            "PAPAC",
            "EGPSD",
            "CASP_TEST",
        }

        for p in ports:
            # Check coordinate bounds
            assert -180.0 <= p.coordinates[0] <= 180.0
            assert -90.0 <= p.coordinates[1] <= 90.0
            assert p.crs == "EPSG:4326"

            # Check audit metadata
            assert p.source_url.startswith("http")
            assert len(p.provenance) > 10
            assert len(p.license) > 5
            assert len(p.access_limitations) > 10
            assert p.coordinate_type in (
                "verified_marine_access_point",
                "approximate_marine_access_point",
                "unverified_marine_access_point",
                "port_point",
                "administrative_point",
            )

    def test_fairway_coordinates_distinguished_from_administrative_points(
        self, engine: MaritimeRoutingEngine
    ) -> None:
        """Verify New Orleans uses approximate marine access point (Southwest Pass) not city center."""
        port = engine.port_repo.get_port("USMSY")
        assert port is not None
        assert port.coordinate_type == "approximate_marine_access_point"
        # Southwest Pass fairway entrance is near lat 28.9, lon -89.43
        # City hall centroid is at lat 29.95, lon -90.07 (over 65 NM inland)
        assert abs(port.coordinates[0] - (-89.43)) < 0.1
        assert abs(port.coordinates[1] - 28.90) < 0.1
        assert "Southwest Pass" in port.access_limitations

    def test_lookup_by_unlocode_case_insensitive(self, engine: MaritimeRoutingEngine) -> None:
        """Verify port resolution is case-insensitive and trims whitespace."""
        p1 = engine.port_repo.get_port("usmsy")
        p2 = engine.port_repo.get_port("  USMSY  ")
        assert p1 is not None and p2 is not None
        assert p1.port_id == p2.port_id == "USMSY"

    def test_all_eight_ports_classified_accurately(self, engine: MaritimeRoutingEngine) -> None:
        """Verify the exact classification breakdown across all 8 prototype test ports:
        - 0 verified_marine_access_point (no coordinates claim verified terminal access)
        - 2 approximate_marine_access_point (USMSY, USSEA: grounded in NOAA/USCG charts but macro/approximate)
        - 5 unverified_marine_access_point (JPYOK, SGSIN, NLRTM, PAPAC, EGPSD: secondary citations without direct online verification)
        - 1 port_point (CASP_TEST: administrative centroid for inland disconnected testing)
        """
        ports = {p.port_id: p for p in engine.port_repo.list_ports()}
        assert len(ports) == 8

        # Approximate marine access points
        assert ports["USMSY"].coordinate_type == "approximate_marine_access_point"
        assert ports["USSEA"].coordinate_type == "approximate_marine_access_point"

        # Unverified marine access points
        assert ports["JPYOK"].coordinate_type == "unverified_marine_access_point"
        assert ports["SGSIN"].coordinate_type == "unverified_marine_access_point"
        assert ports["NLRTM"].coordinate_type == "unverified_marine_access_point"
        assert ports["PAPAC"].coordinate_type == "unverified_marine_access_point"
        assert ports["EGPSD"].coordinate_type == "unverified_marine_access_point"

        # Inland test port point
        assert ports["CASP_TEST"].coordinate_type == "port_point"


class TestGeodesicGeometryAndUnits:
    """Tests for Phase 2 & 4: Geodesic distance and unit conversion."""

    def test_nautical_mile_to_kilometer_conversion(self) -> None:
        """Verify exact international standard: 1 NM = 1.852 km = 1,852 m."""
        assert KM_PER_NAUTICAL_MILE == 1.852
        assert METERS_PER_NAUTICAL_MILE == 1852.0

        # Exact conversion roundtrip
        dist_nm = 100.0
        dist_km = nm_to_km(dist_nm)
        assert dist_km == 185.2
        assert km_to_nm(dist_km) == 100.0

    def test_geodesic_distance_identical_points(self) -> None:
        """Identical points have 0 distance."""
        assert geodesic_distance_nm(139.73, 35.15, 139.73, 35.15) == 0.0

    def test_coordinate_validation(self) -> None:
        """Verify coordinate bounds and finite number checks."""
        assert validate_coordinates(0.0, 0.0)[0] is True
        assert validate_coordinates(-180.0, -90.0)[0] is True
        assert validate_coordinates(180.0, 90.0)[0] is True

        # Out of bounds
        assert validate_coordinates(180.1, 0.0)[0] is False
        assert validate_coordinates(-180.1, 0.0)[0] is False
        assert validate_coordinates(0.0, 90.1)[0] is False
        assert validate_coordinates(0.0, -90.1)[0] is False

        # Non-finite values
        assert validate_coordinates(float("nan"), 0.0)[0] is False
        assert validate_coordinates(0.0, float("inf"))[0] is False


class TestSupportedPortPairRouting:
    """Tests for Phase 4: Supported port pairs, geometry, and canal routing."""

    def test_usmsy_to_jpyok_standard_panama_route(self, engine: MaritimeRoutingEngine) -> None:
        """Validate USMSY (New Orleans) to JPYOK (Yokohama) standard Panama Canal route."""
        req = RoutingRequest(
            origin="USMSY",
            destination="JPYOK",
            canal_policy="allow",
        )
        res = engine.calculate_route(req)

        assert res.status == "calculated"
        assert res.error_message is None
        assert res.summary is not None

        # Verify summary statistics
        assert res.summary.origin_id == "USMSY"
        assert res.summary.destination_id == "JPYOK"
        assert "Panama Canal" in res.summary.canals_transited

        # Validation matrix T1: Provisional range 9,100 - 9,350 NM
        # Aligns with USDA AgTransport corridor benchmark (9,150.0 NM) and NGA Pub. 151 (~9,125 NM)
        assert 9100.0 <= res.summary.distance_nm <= 9350.0
        assert res.summary.distance_km == nm_to_km(res.summary.distance_nm)

        # Verify geometry
        assert len(res.geometry) >= 15
        for pt in res.geometry:
            assert len(pt) == 2
            assert -180.0 <= pt[0] <= 180.0
            assert -90.0 <= pt[1] <= 90.0

        # Verify origin and destination coordinates are endpoints
        assert res.geometry[0] == [-89.43, 28.90]
        assert res.geometry[-1] == [139.73, 35.15]

        # Verify waypoints
        waypoint_ids = [w.node_id for w in res.waypoints]
        assert "usmsy_fairway" in waypoint_ids
        assert "panama_atlantic" in waypoint_ids
        assert "panama_pacific" in waypoint_ids
        assert "jpyok_fairway" in waypoint_ids

        # Verify cumulative distances are monotonically increasing
        dists = [w.cumulative_distance_nm for w in res.waypoints]
        for i in range(len(dists) - 1):
            assert dists[i] < dists[i + 1]

        # Verify limitations include safety disclaimer
        assert any("ECDIS" in lim or "navigational safety" in lim for lim in res.limitations)

    def test_ussea_to_jpyok_north_pacific_direct(self, engine: MaritimeRoutingEngine) -> None:
        """Validate USSEA (Seattle) to JPYOK (Yokohama) direct North Pacific sea lane."""
        req = RoutingRequest(
            origin="USSEA",
            destination="JPYOK",
            canal_policy="allow",
        )
        res = engine.calculate_route(req)

        assert res.status == "calculated"
        assert res.summary is not None
        assert len(res.summary.canals_transited) == 0

        # Validation matrix T2: Provisional range 4,400 - 4,650 NM
        # Aligns with USDA AgTransport corridor benchmark (4,520.0 NM) and NGA Pub. 151 (~4,536 NM)
        assert 4400.0 <= res.summary.distance_nm <= 4650.0
        assert res.summary.distance_km == nm_to_km(res.summary.distance_nm)

        # Verify geometry
        assert res.geometry[0] == [-124.75, 48.50]
        assert res.geometry[-1] == [139.73, 35.15]

    def test_canal_bypass_avoid_panama(self, engine: MaritimeRoutingEngine) -> None:
        """Validate canal bypass (REQ-FR-OMR-003): avoid_panama shifts route via Cape of Good Hope."""
        req_panama = RoutingRequest(origin="USMSY", destination="JPYOK", canal_policy="allow")
        res_panama = engine.calculate_route(req_panama)

        req_bypass = RoutingRequest(origin="USMSY", destination="JPYOK", canal_policy="avoid_panama")
        res_bypass = engine.calculate_route(req_bypass)

        assert res_bypass.status == "calculated"
        assert res_bypass.summary is not None
        assert "Panama Canal" not in res_bypass.summary.canals_transited

        # Route must transit Cape of Good Hope
        waypoint_ids = [w.node_id for w in res_bypass.waypoints]
        assert "cape_good_hope" in waypoint_ids

        # Distance must be dramatically longer than Panama route (+5,000+ NM)
        # T3: Provisional range 14,000 - 17,500 NM
        assert res_bypass.summary.distance_nm > res_panama.summary.distance_nm + 5000.0
        assert 14000.0 <= res_bypass.summary.distance_nm <= 17500.0


class TestInvalidAndMalformedInputs:
    """Tests for Phase 4: Invalid identifiers, malformed coordinates, and inland points."""

    def test_unknown_port_identifier_rejected(self, engine: MaritimeRoutingEngine) -> None:
        """Verify unknown port identifier returns status invalid_input with empty geometry."""
        req = RoutingRequest(origin="USMSY", destination="NONEXISTENT_PORT_XYZ")
        res = engine.calculate_route(req)

        assert res.status == "invalid_input"
        assert res.summary is None
        assert res.geometry == []
        assert res.waypoints == []
        assert "unknown or unsupported" in res.error_message.lower()

    def test_malformed_coordinates_rejected(self, engine: MaritimeRoutingEngine) -> None:
        """Verify out-of-bounds coordinates return status invalid_input."""
        req = RoutingRequest(
            origin=PortIdentifierOrCoords(coordinates=[200.0, 45.0]),  # lon > 180
            destination="JPYOK",
        )
        res = engine.calculate_route(req)

        assert res.status == "invalid_input"
        assert res.geometry == []
        assert "out of valid wgs 84 range" in res.error_message.lower()

    def test_inland_coordinates_rejected(self, engine: MaritimeRoutingEngine) -> None:
        """Verify deep inland coordinates (e.g. Denver, CO) are rejected as un-snappable."""
        # Denver, CO: [-104.9903, 39.7392]
        req = RoutingRequest(
            origin=PortIdentifierOrCoords(coordinates=[-104.9903, 39.7392]),
            destination="JPYOK",
        )
        res = engine.calculate_route(req)

        assert res.status == "invalid_input"
        assert res.geometry == []
        assert "too distant from navigable sea lanes" in res.error_message.lower()

    def test_invalid_canal_policy_rejected(self, engine: MaritimeRoutingEngine) -> None:
        """Verify unsupported canal policy is rejected."""
        req = RoutingRequest(origin="USMSY", destination="JPYOK", canal_policy="invalid_policy")
        res = engine.calculate_route(req)

        assert res.status == "invalid_input"
        assert res.geometry == []
        assert "unsupported canal policy" in res.error_message.lower()


class TestDisconnectedRouteAndNoStraightLineFallback:
    """Tests for Phase 4 & REQ-FR-OMR-002: Zero straight-line fallback on disconnected endpoints."""

    def test_disconnected_water_endpoint_returns_unsupported_route(
        self, engine: MaritimeRoutingEngine
    ) -> None:
        """Verify disconnected water body (Caspian Sea) returns status unsupported_route."""
        req = RoutingRequest(origin="CASP_TEST", destination="JPYOK")
        res = engine.calculate_route(req)

        assert res.status == "unsupported_route"
        assert res.summary is None
        assert res.geometry == []
        assert res.waypoints == []
        assert "straight-line geometry suppressed" in res.error_message

    def test_strictly_zero_straight_line_fallback(self, engine: MaritimeRoutingEngine) -> None:
        """Verify that under NO circumstances does the engine fabricate straight-line geometry."""
        # Failure 1: Disconnected
        res_disc = engine.calculate_route(RoutingRequest(origin="CASP_TEST", destination="JPYOK"))
        assert res_disc.geometry == []

        # Failure 2: Invalid input
        res_inv = engine.calculate_route(RoutingRequest(origin="BAD_PORT", destination="JPYOK"))
        assert res_inv.geometry == []

        # Failure 3: Avoid all canals when no oceanic bypass exists
        # In this test, an impossible constraint returns empty geometry
        graph_no_paths = MaritimeGraph()
        # Remove all edges
        graph_no_paths.graph.clear_edges()
        engine_empty = MaritimeRoutingEngine(graph=graph_no_paths)
        res_empty = engine_empty.calculate_route(RoutingRequest(origin="USMSY", destination="JPYOK"))
        assert res_empty.status == "unsupported_route"
        assert res_empty.geometry == []


class TestDeterminismAndRepeatability:
    """Tests for Phase 4: Deterministic bit-for-bit repeatability."""

    def test_repeated_requests_produce_identical_results(
        self, engine: MaritimeRoutingEngine
    ) -> None:
        """Verify 10 repeated queries produce identical route_id, distance, and coordinates."""
        req = RoutingRequest(origin="USMSY", destination="JPYOK", canal_policy="allow")

        first_res = engine.calculate_route(req)
        assert first_res.status == "calculated"

        for _ in range(10):
            res = engine.calculate_route(req)
            assert res.route_id == first_res.route_id
            assert res.summary.distance_nm == first_res.summary.distance_nm
            assert res.summary.distance_km == first_res.summary.distance_km
            assert res.geometry == first_res.geometry
            assert len(res.waypoints) == len(first_res.waypoints)
            for w1, w2 in zip(res.waypoints, first_res.waypoints):
                assert w1.node_id == w2.node_id
                assert w1.cumulative_distance_nm == w2.cumulative_distance_nm


class TestNavigationalReferenceEvidence:
    """Tests for Phase 4: Deterministic solver validation against calibrated corridor benchmarks."""

    def test_nga_pub_151_comparison(self, engine: MaritimeRoutingEngine) -> None:
        """Verify computed USMSY -> JPYOK distance matches calibrated prototype test corridor (~9,105.57 NM).

        Independent Reference Evidence Assessment:
        - 9,131 NM is an UNVERIFIED RECONSTRUCTED COMPONENT SUM (1,399 + 50 + 7,682 NM),
          NOT an authoritative single-entry published fairway distance. NGA Pub. 151 does NOT
          publish a single direct port-to-port table row for New Orleans SW Pass to Yokohama.
        - Component Arithmetic Breakdown:
            Reconstructed fairway sum: 1,399 + 50 + 7,682 = 9,131 NM (unverified reconstructed sum)
            Reconstructed lock sum:    1,399 + 44 + 7,682 = 9,125 NM (unverified reconstructed sum)
            Reconstructed city berth:  1,458 + 50 + 7,682 = 9,190 NM (unverified reconstructed sum)
        - Arithmetic Correctness vs. Input Verification:
            Arithmetic correctness of the addition (1,399 + 50 + 7,682 = 9,131 vs 1,399 + 44 + 7,682 = 9,125,
            explaining the 6 NM difference between 50 NM canal fairway transit and 44 NM lock channel)
            is distinct from verification of the input distances themselves. The input values and
            fairway endpoints are not independently verified against specific page/table records.
        - Pelagos Commercial Freight Corridor Benchmark (FreightRoute USG-JPN-BULK):
            9,150.0 NM (USDA AMS AgTransport commercial grain trading fixture)
        - Computed Prototype Engine Distance: 9,105.57 NM (calibrated test fixture)

        Strict Evidence Separation:
        - The 9,131 NM, 9,125 NM, and 9,190 NM totals are unverified reconstructed component sums.
        - USDA corridor benchmark represents broader commercial grain loading/discharge ranges.
        - Computed distance is the deterministic mathematical result of 19 prototype test
          waypoints on the synthetic graph.
        - This test validates synthetic pathfinding determinism and geodesic distance summation
          against the calibrated test corridor; it does NOT assert unsupported hydrographic facts
          or claim operational route validation.
        """
        req = RoutingRequest(origin="USMSY", destination="JPYOK")
        res = engine.calculate_route(req)

        unverified_reconstructed_reference_nm = 9131.0
        computed_nm = res.summary.distance_nm

        # Relative difference is under 0.5% against the 9,131 NM unverified reconstructed reference corridor
        rel_diff = abs(computed_nm - unverified_reconstructed_reference_nm) / unverified_reconstructed_reference_nm
        assert rel_diff < 0.005, f"Computed {computed_nm} NM deviates >0.5% from unverified reconstructed reference ({unverified_reconstructed_reference_nm} NM)"

    def test_provenance_explicitly_reports_synthetic_prototype(
        self, engine: MaritimeRoutingEngine
    ) -> None:
        """Verify engine explicitly reports synthetic prototype fixture status without claiming MARNET extraction."""
        req = RoutingRequest(origin="USMSY", destination="JPYOK")
        res = engine.calculate_route(req)

        # Must report prototype method and provenance flags
        assert res.route_method == "prototype_test_network_graph"
        assert res.data_provenance.get("fixture_type") == "synthetic_prototype_corridor_fixture"
        assert res.data_provenance.get("is_upstream_extracted") == "false"
        assert "unresolved_prerequisite" in res.data_provenance.get("upstream_eurostat_status", "")

        # Must explicitly disclose prototype fixture in limitations
        assert any("prototype" in lim.lower() for lim in res.limitations)

    def test_pnw_japan_nga_pub_151_comparison(self, engine: MaritimeRoutingEngine) -> None:
        """Verify computed USSEA -> JPYOK distance matches calibrated North Pacific test corridor.

        Independent Reference Data (NGA Pub. 151, 12th Ed., 2019):
        - Table 2 (Pacific Ports to East Asia):
            Seattle, Wash. (inner harbor) to Yokohama: 4,312 NM (Great Circle), 4,797 NM (Rhumb line)
        - Note on 4,536 NM: The previously cited 4,536 NM figure represents an unverified
          commercial winter route estimate south of the Aleutians; it is NOT an official published
          table row in NGA Pub. 151 and is withdrawn as an official citation.
        - Endpoint Divergence Note:
            NGA Pub. 151's published 4,312 NM originates from Seattle inner harbor through Puget Sound.
            The prototype fixture's USSEA coordinate is at Cape Flattery (48°30'N, 124°45'W), which is
            125 NM west of Seattle at the oceanic boundary of the Strait of Juan de Fuca. Because
            endpoints do not match, published figures do not constitute direct empirical validation.
        - Pelagos Commercial Freight Corridor Benchmark (FreightRoute PNW-JPN-BULK):
            4,520.0 NM (USDA AMS AgTransport grain fixture benchmark)
        - Computed Prototype Engine Distance: 4,403.41 NM (calibrated test fixture)

        Strict Evidence Separation:
        - This test validates deterministic solver execution and corridor bounds (4,400 to 4,650 NM)
          across the 9 prototype test fixture waypoints, NOT empirical real-world passage accuracy.
        """
        req = RoutingRequest(origin="USSEA", destination="JPYOK")
        res = engine.calculate_route(req)

        computed_nm = res.summary.distance_nm
        # Within provisional corridor bounds
        assert 4400.0 <= computed_nm <= 4650.0

    def test_rotterdam_to_singapore_suez_route(self, engine: MaritimeRoutingEngine) -> None:
        """Validate NLRTM (Rotterdam) to SGSIN (Singapore) route via Suez Canal on prototype test graph.

        Reference Corridor Data (NGA Pub. 151, 12th Ed., 2019):
        - Published inner berth distance: ~8,387 NM.
        - Unverified composite fairway sum: ~8,288 NM, reconstructed from intermediate tables:
            Rotterdam to Gibraltar (1,845 NM) + Gibraltar to Port Said (1,925 NM)
            + Suez Canal transit (104 NM) + Suez to Bab-el-Mandeb (1,308 NM)
            + Bab-el-Mandeb to Singapore Western Fairway (3,106 NM) = 8,288 NM.
        - Substantiation status: The 8,288 NM figure is an unverified composite sum across
          intermediate tables with approximate endpoints, not an authoritative single-entry
          published table value with matching coordinates.
        - Computed Prototype Engine Distance: 8,243.18 NM (calibrated test fixture)

        Strict Evidence Separation:
        - Validates multi-chokepoint pathfinding (English Channel, Gibraltar, Suez, Bab-el-Mandeb,
          Malacca Strait) and canal-policy logic on the prototype graph. It verifies algorithm
          determinism and canal inclusion, NOT real-world hydrographic accuracy.
        """
        req = RoutingRequest(origin="NLRTM", destination="SGSIN", canal_policy="allow")
        res = engine.calculate_route(req)

        assert res.status == "calculated"
        assert res.summary is not None
        assert "Suez Canal" in res.summary.canals_transited
        assert res.summary.origin_id == "NLRTM"
        assert res.summary.destination_id == "SGSIN"

        # Prototype distance (8,243.18 NM) within calibrated corridor bounds (8,100 to 8,450 NM)
        assert 8100.0 <= res.summary.distance_nm <= 8450.0


class TestRoutingEdgeCasesAndHelpers:
    """Tests for helper utilities, explicit coordinates, and error handling."""

    def test_explicit_coordinate_objects(self, engine: MaritimeRoutingEngine) -> None:
        """Verify routing works with explicit PortIdentifierOrCoords coordinate objects."""
        req = RoutingRequest(
            origin=PortIdentifierOrCoords(coordinates=[-89.43, 28.90]),
            destination=PortIdentifierOrCoords(coordinates=[139.73, 35.15]),
            canal_policy="allow",
        )
        res = engine.calculate_route(req)
        assert res.status == "calculated"
        assert res.summary is not None
        assert 9100.0 <= res.summary.distance_nm <= 9350.0

    def test_missing_fixture_raises_filenotfound(self, tmp_path) -> None:
        """Verify missing fixture paths raise FileNotFoundError."""
        from pathlib import Path
        non_existent = tmp_path / "does_not_exist.json"

        with pytest.raises(FileNotFoundError):
            PortRepository(fixture_path=non_existent)

        with pytest.raises(FileNotFoundError):
            MaritimeGraph(fixture_path=non_existent)

    def test_nearest_port_beyond_threshold(self, engine: MaritimeRoutingEngine) -> None:
        """Verify points far from any port return None."""
        # Mid-South Pacific (lon -140, lat -45)
        res = engine.port_repo.find_nearest_port(
            longitude=-140.0,
            latitude=-45.0,
            max_distance_nm=50.0,
        )
        assert res is None

    def test_nearest_node_invalid_coords(self, engine: MaritimeRoutingEngine) -> None:
        """Verify invalid coordinates to find_nearest_node return None."""
        assert engine.graph.find_nearest_node(longitude=300.0, latitude=0.0) is None

    def test_path_cumulative_distance_helper(self) -> None:
        """Verify path_cumulative_distance_nm handles empty and multi-segment paths."""
        from pelagos.routing.geometry import path_cumulative_distance_nm

        assert path_cumulative_distance_nm([]) == 0.0
        assert path_cumulative_distance_nm([[-89.43, 28.90]]) == 0.0
        dist = path_cumulative_distance_nm([[-89.43, 28.90], [-88.00, 26.50]])
        assert dist > 100.0
