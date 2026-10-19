"""Pelagos D9.4 Stage 3 - MARNET Routing Integration Automated Test Suite.

Validates:
1. Prototype-mode regression and default behavior.
2. Successful loading of a valid MARNET artifact.
3. Missing, corrupted, or checksum-mismatched artifact.
4. No silent prototype fallback when MARNET mode is selected.
5. Port snapping limits and unsupported endpoints (50.0 NM provisional ceiling).
6. Polar edges identified by endpoint vertices and exact boundary conditions (+/- 66.5°).
7. Polar edges crossing boundaries via intermediate vertices (Arctic and Antarctic).
8. Existing southern graph edges reaching 70°S and 80°S excluded from routing and snapping.
9. Missing or invalid edge geometry failing closed.
10. Panama and Suez avoidance applied to actual path edges.
11. `avoid_all` excluding both classified canal passages.
12. Unsupported routes when no compliant path exists.
13. Empty geometry on every failure status (REQ-FR-OMR-002).
14. Antimeridian geometry segmentation and rejection for clients without segment support.
15. Deterministic output for identical inputs and graph artifacts.
16. Cache integrity lifecycle (initialization validation, in-memory snapshot consistency, fresh restart rejection).

Includes opt-in full MARNET integration benchmarks (PELAGOS_MARNET_INTEGRATION=1).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List
import pytest

from pelagos.routing.geometry import (
    NAV_SAFETY_DISCLAIMER,
    geodesic_distance_nm,
)
from pelagos.routing.graph import (
    ANTARCTIC_CIRCLE_LATITUDE,
    ARCTIC_CIRCLE_LATITUDE,
    CANONICAL_PASSAGES,
    MarnetCacheError,
    MarnetMaritimeGraph,
    MaritimeGraph,
    POLAR_CIRCLE_LATITUDE,
    PrototypeMaritimeGraph,
    is_arctic_edge,
    is_polar_edge,
)
from pelagos.routing.ingestion import compute_sha256
from pelagos.routing.ports import PortRepository
from pelagos.routing.schemas import (
    PortIdentifierOrCoords,
    PortSnappingDetail,
    RouteSummary,
    RoutingRequest,
    RoutingResult,
    RoutingStatus,
)
from pelagos.routing.service import MaritimeRoutingEngine

DEFAULT_CACHE_DIR = Path(".cache/pelagos/routing")


# ---------------------------------------------------------------------------
# Test Fixtures & Synthetic Data Helpers
# ---------------------------------------------------------------------------

def create_synthetic_marnet_data() -> Dict[str, Any]:
    """Create a minimal, valid synthetic MARNET dataset for isolated unit testing.

    Layout:
      - node_0: [0.0, 0.0]
      - node_1: [10.0, 0.0] (connected to node_0, weight=600.0)
      - node_panama: [10.0, 10.0] (connected to node_0 with pass_id='panama')
      - node_suez: [0.0, 10.0] (connected to node_0 with pass_id='suez')
      - node_alt: [5.0, 5.0] (bypass node without passage tags)
      - node_target: [20.0, 20.0] (target reachable via panama, suez, or alt)
      - node_east_180: [180.0, 50.0]
      - node_west_180: [-180.0, 50.0] (connected to node_east_180 with stitched=True, weight=0.0)
      - node_pacific_east: [170.0, 50.0] (connected to node_east_180)
      - node_pacific_west: [-170.0, 50.0] (connected to node_west_180)
    """
    nodes = {
        "node_0": [0.0, 0.0],
        "node_1": [10.0, 0.0],
        "node_panama": [10.0, 10.0],
        "node_suez": [0.0, 10.0],
        "node_alt": [5.0, 5.0],
        "node_target": [20.0, 20.0],
        "node_east_180": [180.0, 50.0],
        "node_west_180": [-180.0, 50.0],
        "node_pacific_east": [170.0, 50.0],
        "node_pacific_west": [-170.0, 50.0],
    }

    edges = [
        {
            "u": "node_0",
            "v": "node_1",
            "weight_nm": 600.0,
            "pass_id": None,
            "stitched": False,
            "geometry": [[0.0, 0.0], [5.0, 0.0], [10.0, 0.0]],
        },
        # Panama corridor
        {
            "u": "node_0",
            "v": "node_panama",
            "weight_nm": 700.0,
            "pass_id": "panama",
            "stitched": False,
            "geometry": [[0.0, 0.0], [10.0, 10.0]],
        },
        {
            "u": "node_panama",
            "v": "node_target",
            "weight_nm": 700.0,
            "pass_id": None,
            "stitched": False,
            "geometry": [[10.0, 10.0], [20.0, 20.0]],
        },
        # Suez corridor
        {
            "u": "node_0",
            "v": "node_suez",
            "weight_nm": 750.0,
            "pass_id": "suez",
            "stitched": False,
            "geometry": [[0.0, 0.0], [0.0, 10.0]],
        },
        {
            "u": "node_suez",
            "v": "node_target",
            "weight_nm": 750.0,
            "pass_id": None,
            "stitched": False,
            "geometry": [[0.0, 10.0], [20.0, 20.0]],
        },
        # Alternative bypass corridor
        {
            "u": "node_0",
            "v": "node_alt",
            "weight_nm": 900.0,
            "pass_id": None,
            "stitched": False,
            "geometry": [[0.0, 0.0], [5.0, 5.0]],
        },
        {
            "u": "node_alt",
            "v": "node_target",
            "weight_nm": 900.0,
            "pass_id": None,
            "stitched": False,
            "geometry": [[5.0, 5.0], [20.0, 20.0]],
        },
        # Transpacific antimeridian corridor
        {
            "u": "node_pacific_east",
            "v": "node_east_180",
            "weight_nm": 400.0,
            "pass_id": None,
            "stitched": False,
            "geometry": [[170.0, 50.0], [180.0, 50.0]],
        },
        {
            "u": "node_east_180",
            "v": "node_west_180",
            "weight_nm": 0.0,
            "pass_id": None,
            "stitched": True,
            "geometry": [[180.0, 50.0], [-180.0, 50.0]],
        },
        {
            "u": "node_west_180",
            "v": "node_pacific_west",
            "weight_nm": 400.0,
            "pass_id": None,
            "stitched": False,
            "geometry": [[-180.0, 50.0], [-170.0, 50.0]],
        },
    ]

    return {
        "metadata": {
            "schema_version": "pelagos-marnet-graph-v1",
            "transformation_version": "1.0.0",
        },
        "nodes": nodes,
        "edges": edges,
    }


def write_test_cache(cache_dir: Path, data: Dict[str, Any]) -> None:
    """Write valid synthetic MARNET files to a test cache directory."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    net_path = cache_dir / "marnet_network.json"
    with open(net_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    sha = compute_sha256(net_path)
    size = net_path.stat().st_size

    manifest = {
        "schema_version": "pelagos-marnet-graph-v1",
        "transformation_version": "1.0.0",
        "generated_at": "2026-10-18T12:00:00Z",
        "compiled_artifacts": {
            "marnet_network_json": {
                "filename": "marnet_network.json",
                "sha256": sha,
                "size_bytes": size,
            }
        },
    }
    with open(cache_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


# ---------------------------------------------------------------------------
# Focused Automated Tests (1 to 14)
# ---------------------------------------------------------------------------

class TestMarnetRoutingIntegration:
    """Focused automated test suite validating the 14 mandatory Stage 3 integration criteria."""

    def test_01_prototype_mode_regression_and_default(self) -> None:
        """1. Prototype-mode regression and default behavior."""
        engine = MaritimeRoutingEngine()

        # Unspecified mode defaults to prototype
        req_default = RoutingRequest(origin="NLRTM", destination="SGSIN")
        res_default = engine.calculate_route(req_default)
        assert res_default.status == RoutingStatus.CALCULATED
        assert res_default.routing_mode == "prototype"
        assert res_default.route_method == "prototype_test_network_graph"
        assert "Suez Canal" in res_default.summary.canals_transited

        # Explicit prototype mode
        req_explicit = RoutingRequest(origin="NLRTM", destination="SGSIN", routing_mode="prototype")
        res_explicit = engine.calculate_route(req_explicit)
        assert res_explicit.route_id == res_default.route_id
        assert res_explicit.summary.distance_nm == res_default.summary.distance_nm
        assert res_explicit.geometry == res_default.geometry

        # Environment variable default override
        old_env = os.environ.get("PELAGOS_ROUTING_MODE")
        try:
            os.environ["PELAGOS_ROUTING_MODE"] = "prototype"
            res_env = engine.calculate_route(RoutingRequest(origin="USMSY", destination="JPYOK"))
            assert res_env.routing_mode == "prototype"
        finally:
            if old_env is not None:
                os.environ["PELAGOS_ROUTING_MODE"] = old_env
            else:
                os.environ.pop("PELAGOS_ROUTING_MODE", None)

    def test_02_successful_loading_valid_marnet_artifact(self, tmp_path: Path) -> None:
        """2. Successful loading of a valid MARNET artifact."""
        cache_dir = tmp_path / "cache"
        data = create_synthetic_marnet_data()
        write_test_cache(cache_dir, data)

        graph = MarnetMaritimeGraph(cache_dir=cache_dir)
        assert len(graph.nodes_data) == 10
        assert graph.graph.number_of_nodes() == 10
        assert graph.graph.number_of_edges() == 10
        assert graph.excluded_polar_edge_count == 0
        assert graph.excluded_arctic_edge_count == 0
        assert graph.invalid_geometry_edge_count == 0

    def test_03_missing_corrupted_checksum_mismatch(self, tmp_path: Path) -> None:
        """3. Missing, corrupted, or checksum-mismatched artifact fails closed."""
        cache_dir = tmp_path / "cache"
        data = create_synthetic_marnet_data()
        write_test_cache(cache_dir, data)

        # 3a. Missing manifest.json
        cache_missing_manifest = tmp_path / "cache_missing_manifest"
        cache_missing_manifest.mkdir()
        with open(cache_missing_manifest / "marnet_network.json", "w", encoding="utf-8") as f:
            json.dump(data, f)
        with pytest.raises(MarnetCacheError, match="manifest not found"):
            MarnetMaritimeGraph(cache_dir=cache_missing_manifest)

        # 3b. Missing marnet_network.json
        cache_missing_network = tmp_path / "cache_missing_network"
        cache_missing_network.mkdir()
        with open(cache_missing_network / "manifest.json", "w", encoding="utf-8") as f:
            json.dump({"schema_version": "pelagos-marnet-graph-v1", "transformation_version": "1.0.0"}, f)
        with pytest.raises(MarnetCacheError, match="artifact not found"):
            MarnetMaritimeGraph(cache_dir=cache_missing_network)

        # 3c. Size mismatch
        cache_size_mismatch = tmp_path / "cache_size_mismatch"
        write_test_cache(cache_size_mismatch, data)
        with open(cache_size_mismatch / "marnet_network.json", "a", encoding="utf-8") as f:
            f.write("\n")
        with pytest.raises(MarnetCacheError, match="Size mismatch"):
            MarnetMaritimeGraph(cache_dir=cache_size_mismatch)

        # 3d. Checksum mismatch with identical size
        cache_corrupted = tmp_path / "cache_corrupted"
        write_test_cache(cache_corrupted, data)
        net_file = cache_corrupted / "marnet_network.json"
        content = bytearray(net_file.read_bytes())
        # Flip a character in-place to preserve exact file length
        content[10] = ord("X") if content[10] != ord("X") else ord("Y")
        net_file.write_bytes(content)
        with pytest.raises(MarnetCacheError, match="Checksum mismatch"):
            MarnetMaritimeGraph(cache_dir=cache_corrupted)

        # 3e. Incompatible schema version
        cache_bad_schema = tmp_path / "cache_bad_schema"
        write_test_cache(cache_bad_schema, data)
        with open(cache_bad_schema / "manifest.json", "r+", encoding="utf-8") as f:
            m = json.load(f)
            m["schema_version"] = "incompatible-v2"
            f.seek(0)
            f.truncate()
            json.dump(m, f)
        with pytest.raises(MarnetCacheError, match="Incompatible manifest schema_version"):
            MarnetMaritimeGraph(cache_dir=cache_bad_schema)

    def test_04_no_silent_prototype_fallback(self, tmp_path: Path) -> None:
        """4. No silent prototype fallback when MARNET mode is selected."""
        empty_cache = tmp_path / "empty_cache"
        empty_cache.mkdir()

        engine = MaritimeRoutingEngine(marnet_cache_dir=empty_cache)
        req = RoutingRequest(origin="NLRTM", destination="SGSIN", routing_mode="marnet")
        res = engine.calculate_route(req)

        assert res.status == RoutingStatus.CONFIGURATION_ERROR
        assert res.routing_mode == "marnet"
        assert res.route_method == "marnet_50km_generalized_graph"
        assert res.geometry == []
        assert res.geometry_segments == []
        assert res.waypoints == []
        assert "not found or failed integrity check" in res.error_message

    def test_05_port_snapping_limits_and_unsupported_endpoints(self, tmp_path: Path) -> None:
        """5. Port snapping limits and unsupported endpoints (50.0 NM ceiling)."""
        data = create_synthetic_marnet_data()
        graph = MarnetMaritimeGraph(graph_data=data)
        engine = MaritimeRoutingEngine(marnet_graph=graph)

        # Node 0 is at [0.0, 0.0]
        # 1 degree of latitude is ~60.0 NM
        # Point at [0.0, 0.70] is ~42.0 NM -> Snaps successfully
        req_near = RoutingRequest(
            origin=PortIdentifierOrCoords(coordinates=[0.0, 0.70]),
            destination=PortIdentifierOrCoords(coordinates=[10.0, 0.0]),
            routing_mode="marnet",
        )
        res_near = engine.calculate_route(req_near)
        assert res_near.status == RoutingStatus.CALCULATED
        assert res_near.summary.origin_snap_distance_nm > 0.0
        assert res_near.summary.origin_snap_distance_nm < 50.0
        assert "origin" in res_near.snapping_details

        # Point at [0.0, 1.0] is ~60.0 NM -> Beyond 50.0 NM ceiling -> Unsupported
        req_far = RoutingRequest(
            origin=PortIdentifierOrCoords(coordinates=[0.0, 1.0]),
            destination=PortIdentifierOrCoords(coordinates=[10.0, 0.0]),
            routing_mode="marnet",
        )
        res_far = engine.calculate_route(req_far)
        assert res_far.status == RoutingStatus.UNSUPPORTED_ROUTE
        assert res_far.geometry == []
        assert res_far.geometry_segments == []
        assert "provisional limit" in res_far.error_message

    def test_06_polar_edges_identified_by_endpoint_vertices_and_exact_boundaries(self) -> None:
        """6. Polar edges identified by endpoint vertices and exact boundary conditions."""
        # 6a. Northern boundary
        # Vertex > 66.5°N
        assert is_polar_edge([[-10.0, 60.0], [-10.0, 66.51]]) is True
        # Vertex exactly at 66.5°N
        assert is_polar_edge([[-10.0, 60.0], [-10.0, 66.5]]) is True
        # Vertex strictly below 66.5°N
        assert is_polar_edge([[-10.0, 60.0], [-10.0, 66.49]]) is False

        # 6b. Southern boundary
        # Vertex < -66.5°S
        assert is_polar_edge([[-10.0, -60.0], [-10.0, -66.51]]) is True
        # Vertex exactly at -66.5°S
        assert is_polar_edge([[-10.0, -60.0], [-10.0, -66.5]]) is True
        # Vertex strictly above -66.5°S
        assert is_polar_edge([[-10.0, -60.0], [-10.0, -66.49]]) is False

        # 6c. Endpoints exactly on boundaries rejected in routing service
        engine = MaritimeRoutingEngine()
        # Origin exactly at 66.5°N
        res_north = engine.calculate_route(
            RoutingRequest(
                origin=PortIdentifierOrCoords(coordinates=[0.0, 66.5]),
                destination=PortIdentifierOrCoords(coordinates=[0.0, 10.0]),
                routing_mode="marnet",
            )
        )
        assert res_north.status == RoutingStatus.UNSUPPORTED_ROUTE
        assert res_north.geometry == []
        assert "polar waters" in res_north.error_message

        # Origin exactly at -66.5°S
        res_south = engine.calculate_route(
            RoutingRequest(
                origin=PortIdentifierOrCoords(coordinates=[0.0, -66.5]),
                destination=PortIdentifierOrCoords(coordinates=[0.0, 10.0]),
                routing_mode="marnet",
            )
        )
        assert res_south.status == RoutingStatus.UNSUPPORTED_ROUTE
        assert res_south.geometry == []
        assert "polar waters" in res_south.error_message

        # Destination exactly at 66.5°N
        res_dest_n = engine.calculate_route(
            RoutingRequest(
                origin=PortIdentifierOrCoords(coordinates=[0.0, 10.0]),
                destination=PortIdentifierOrCoords(coordinates=[0.0, 66.5]),
                routing_mode="marnet",
            )
        )
        assert res_dest_n.status == RoutingStatus.UNSUPPORTED_ROUTE

        # Destination exactly at -66.5°S
        res_dest_s = engine.calculate_route(
            RoutingRequest(
                origin=PortIdentifierOrCoords(coordinates=[0.0, 10.0]),
                destination=PortIdentifierOrCoords(coordinates=[0.0, -66.5]),
                routing_mode="marnet",
            )
        )
        assert res_dest_s.status == RoutingStatus.UNSUPPORTED_ROUTE

        # 6d. Graph builder excludes both northern and southern boundary edges
        data = {
            "metadata": {},
            "nodes": {
                "n_n1": [-10.0, 60.0], "n_n2": [-10.0, 66.5],
                "n_s1": [-10.0, -60.0], "n_s2": [-10.0, -66.5],
                "n_ok": [0.0, 0.0], "n_ok2": [1.0, 1.0],
            },
            "edges": [
                {"u": "n_n1", "v": "n_n2", "weight_nm": 400.0, "geometry": [[-10.0, 60.0], [-10.0, 66.5]]},
                {"u": "n_s1", "v": "n_s2", "weight_nm": 400.0, "geometry": [[-10.0, -60.0], [-10.0, -66.5]]},
                {"u": "n_ok", "v": "n_ok2", "weight_nm": 50.0, "geometry": [[0.0, 0.0], [1.0, 1.0]]},
            ],
        }
        graph = MarnetMaritimeGraph(graph_data=data)
        assert graph.excluded_polar_edge_count == 2
        assert graph.graph.number_of_edges() == 1
        assert graph.graph.has_edge("n_ok", "n_ok2")

    def test_07_polar_edges_crossing_boundaries_via_intermediate_vertices(self) -> None:
        """7. Polar edges crossing northern or southern boundaries via intermediate vertices."""
        # 7a. Northern intermediate crossing (Iceland corridor: endpoints < 66.5°N, intermediate at 66.565313°N)
        north_geom = [
            [-16.581563, 66.439922],  # south of 66.5°N
            [-16.152813, 66.565313],  # NORTH of 66.5°N (ARCTIC)
            [-15.778125, 66.475469],  # south of 66.5°N
        ]
        assert north_geom[0][1] < 66.5
        assert north_geom[-1][1] < 66.5
        assert is_polar_edge(north_geom) is True

        # 7b. Southern intermediate crossing: endpoints > -66.5°S, intermediate at -66.7°S
        south_geom = [
            [-65.0, -66.3],  # north of -66.5°S
            [-65.5, -66.7],  # SOUTH of -66.5°S (ANTARCTIC)
            [-66.0, -66.4],  # north of -66.5°S
        ]
        assert south_geom[0][1] > -66.5
        assert south_geom[-1][1] > -66.5
        assert is_polar_edge(south_geom) is True

        # 7c. Graph builder excludes both intermediate crossing edges
        data = {
            "metadata": {},
            "nodes": {
                "n_n1": north_geom[0], "n_n2": north_geom[-1],
                "n_s1": south_geom[0], "n_s2": south_geom[-1],
            },
            "edges": [
                {"u": "n_n1", "v": "n_n2", "weight_nm": 23.27, "geometry": north_geom},
                {"u": "n_s1", "v": "n_s2", "weight_nm": 30.0, "geometry": south_geom},
            ],
        }
        graph = MarnetMaritimeGraph(graph_data=data)
        assert graph.excluded_polar_edge_count == 2
        assert graph.graph.number_of_edges() == 0

    def test_08_existing_southern_graph_edges_reaching_70s_and_80s(self) -> None:
        """8. The existing southern MARNET graph edges reaching 70°S and 80°S."""
        # node_1 -> node_0 reaches 80°S ([-180.0, -60.0] to [-180.0, -80.0])
        geom_80s = [[-180.0, -60.0], [-180.0, -80.0]]
        assert is_polar_edge(geom_80s) is True

        # node_7376 -> node_7375 reaches 70°S ([180.0, -60.0] to [180.0, -70.0])
        geom_70s = [[180.0, -60.0], [180.0, -70.0]]
        assert is_polar_edge(geom_70s) is True

        data = {
            "metadata": {},
            "nodes": {
                "node_0": [-180.0, -80.0],
                "node_1": [-180.0, -60.0],
                "node_7375": [180.0, -70.0],
                "node_7376": [180.0, -60.0],
            },
            "edges": [
                {"u": "node_1", "v": "node_0", "weight_nm": 1204.68, "geometry": geom_80s},
                {"u": "node_7376", "v": "node_7375", "weight_nm": 602.00, "geometry": geom_70s},
            ],
        }
        graph = MarnetMaritimeGraph(graph_data=data)
        assert graph.excluded_polar_edge_count == 2
        assert graph.graph.number_of_edges() == 0
        assert not graph.graph.has_edge("node_1", "node_0")
        assert not graph.graph.has_edge("node_7376", "node_7375")

        # Verify nearest-node search refuses to snap to polar nodes (-80°S or -70°S)
        assert graph.find_nearest_node(-180.0, -80.0, max_distance_nm=50.0) is None
        assert graph.find_nearest_node(180.0, -70.0, max_distance_nm=50.0) is None

    def test_09_missing_or_invalid_edge_geometry_fails_closed(self) -> None:
        """9. Missing or invalid edge geometry failing closed."""
        assert is_polar_edge([]) is True
        assert is_polar_edge([[0.0, 0.0]]) is True  # single point
        assert is_polar_edge(None) is True  # type: ignore
        assert is_polar_edge([[0.0, "bad_coord"]]) is True  # non-numeric
        assert is_polar_edge([[0.0, float("nan")]]) is True  # NaN
        assert is_polar_edge([[0.0, float("inf")]]) is True  # Inf
        assert is_polar_edge([[float("-inf"), 0.0]]) is True  # -Inf
        assert is_polar_edge([[0.0]]) is True  # point len < 2
        assert is_polar_edge([[0.0, 0.0], "not_a_point"]) is True  # invalid point type

        data = {
            "metadata": {},
            "nodes": {"n1": [0.0, 0.0], "n2": [1.0, 1.0]},
            "edges": [
                {"u": "n1", "v": "n2", "weight_nm": 50.0, "geometry": None},
                {"u": "n1", "v": "n2", "weight_nm": 50.0, "geometry": []},
                {"u": "n1", "v": "n2", "weight_nm": 50.0, "geometry": [[0.0, 0.0]]},
                {"u": "n1", "v": "n2", "weight_nm": 50.0, "geometry": [[0.0, float("nan")], [1.0, 1.0]]},
            ],
        }
        graph = MarnetMaritimeGraph(graph_data=data)
        assert graph.invalid_geometry_edge_count == 3  # None, [], single point
        assert graph.excluded_polar_edge_count == 1   # NaN geometry fails closed via is_polar_edge
        assert graph.graph.number_of_edges() == 0

    def test_10_panama_and_suez_avoidance_on_path_edges(self) -> None:
        """10. Panama and Suez avoidance applied to actual path edges."""
        data = create_synthetic_marnet_data()
        graph = MarnetMaritimeGraph(graph_data=data)

        # Baseline: shortest path to node_target uses Panama (700 + 700 = 1400 NM)
        p_allow = graph.compute_shortest_path("node_0", "node_target", canal_policy="allow")
        assert p_allow is not None
        assert "panama" in p_allow.canals_transited
        assert p_allow.graph_distance_nm == 1400.0

        # Avoid Panama: routes via Suez (750 + 750 = 1500 NM)
        p_avoid_panama = graph.compute_shortest_path("node_0", "node_target", canal_policy="avoid_panama")
        assert p_avoid_panama is not None
        assert "panama" not in p_avoid_panama.canals_transited
        assert "suez" in p_avoid_panama.canals_transited
        assert p_avoid_panama.graph_distance_nm == 1500.0

        # Avoid Suez: routes via Panama (1400 NM)
        p_avoid_suez = graph.compute_shortest_path("node_0", "node_target", canal_policy="avoid_suez")
        assert p_avoid_suez is not None
        assert "suez" not in p_avoid_suez.canals_transited
        assert "panama" in p_avoid_suez.canals_transited
        assert p_avoid_suez.graph_distance_nm == 1400.0

    def test_11_avoid_all_excludes_both_canals(self) -> None:
        """11. avoid_all excluding both classified canal passages."""
        data = create_synthetic_marnet_data()
        graph = MarnetMaritimeGraph(graph_data=data)

        # Avoid All: routes via node_alt (900 + 900 = 1800 NM)
        p_avoid_all = graph.compute_shortest_path("node_0", "node_target", canal_policy="avoid_all")
        assert p_avoid_all is not None
        assert "panama" not in p_avoid_all.canals_transited
        assert "suez" not in p_avoid_all.canals_transited
        assert p_avoid_all.graph_distance_nm == 1800.0
        assert "node_alt" in p_avoid_all.node_path

    def test_12_unsupported_route_when_no_path_exists(self) -> None:
        """12. Unsupported routes when no compliant path exists."""
        data = create_synthetic_marnet_data()
        # Remove the alternative bypass edge
        data["edges"] = [e for e in data["edges"] if e["u"] != "node_alt" and e["v"] != "node_alt"]
        graph = MarnetMaritimeGraph(graph_data=data)
        engine = MaritimeRoutingEngine(marnet_graph=graph)

        req = RoutingRequest(
            origin=PortIdentifierOrCoords(coordinates=[0.0, 0.0]),
            destination=PortIdentifierOrCoords(coordinates=[20.0, 20.0]),
            canal_policy="avoid_all",
            routing_mode="marnet",
        )
        res = engine.calculate_route(req)
        assert res.status == RoutingStatus.UNSUPPORTED_ROUTE
        assert res.geometry == []
        assert res.geometry_segments == []
        assert "No connected maritime sea lane path exists" in res.error_message

    def test_13_empty_geometry_on_every_failure_status(self) -> None:
        """13. Empty geometry on every failure status (REQ-FR-OMR-002)."""
        engine = MaritimeRoutingEngine()

        # 13a. Invalid canal policy -> invalid_input
        r_bad_policy = engine.calculate_route(
            RoutingRequest(origin="NLRTM", destination="SGSIN", canal_policy="destroy_canals")
        )
        assert r_bad_policy.status == RoutingStatus.INVALID_INPUT
        assert r_bad_policy.geometry == []
        assert r_bad_policy.geometry_segments == []

        # 13b. Invalid coordinates -> invalid_input
        r_bad_coords = engine.calculate_route(
            RoutingRequest(
                origin=PortIdentifierOrCoords(coordinates=[999.0, 999.0]),
                destination="SGSIN",
            )
        )
        assert r_bad_coords.status == RoutingStatus.INVALID_INPUT
        assert r_bad_coords.geometry == []
        assert r_bad_coords.geometry_segments == []

        # 13c. Arctic endpoint coordinates (>= 66.5°N) -> unsupported_route
        r_polar_n = engine.calculate_route(
            RoutingRequest(
                origin=PortIdentifierOrCoords(coordinates=[33.0, 68.9]),  # Murmansk
                destination="SGSIN",
                routing_mode="marnet",
            )
        )
        assert r_polar_n.status == RoutingStatus.UNSUPPORTED_ROUTE
        assert r_polar_n.geometry == []
        assert r_polar_n.geometry_segments == []
        assert "polar waters" in r_polar_n.error_message

        # 13d. Antarctic endpoint coordinates (<= -66.5°S) -> unsupported_route
        r_polar_s = engine.calculate_route(
            RoutingRequest(
                origin=PortIdentifierOrCoords(coordinates=[0.0, -70.0]),  # Antarctic waters
                destination="SGSIN",
                routing_mode="marnet",
            )
        )
        assert r_polar_s.status == RoutingStatus.UNSUPPORTED_ROUTE
        assert r_polar_s.geometry == []
        assert r_polar_s.geometry_segments == []
        assert "polar waters" in r_polar_s.error_message

    def test_14_antimeridian_segmentation_and_safety_interlock(self) -> None:
        """14. Antimeridian geometry segmentation and rejection for unsegmented clients."""
        data = create_synthetic_marnet_data()
        graph = MarnetMaritimeGraph(graph_data=data)
        engine = MaritimeRoutingEngine(marnet_graph=graph)

        # Transpacific route between node_pacific_east (170°E) and node_pacific_west (-170°W)
        # 14a. Legacy / unsegmented caller (supports_segments=False) -> MUST FAIL CLOSED
        req_legacy = RoutingRequest(
            origin=PortIdentifierOrCoords(coordinates=[170.0, 50.0]),
            destination=PortIdentifierOrCoords(coordinates=[-170.0, 50.0]),
            routing_mode="marnet",
            supports_segments=False,
        )
        res_legacy = engine.calculate_route(req_legacy)
        assert res_legacy.status == RoutingStatus.UNSUPPORTED_ROUTE
        assert res_legacy.geometry == []
        assert res_legacy.geometry_segments == []
        assert "Antimeridian-crossing route requires segment-aware renderer" in res_legacy.error_message

        # 14b. Segment-aware caller (supports_segments=True) -> CALCULATED with disjoint segments
        req_segmented = RoutingRequest(
            origin=PortIdentifierOrCoords(coordinates=[170.0, 50.0]),
            destination=PortIdentifierOrCoords(coordinates=[-170.0, 50.0]),
            routing_mode="marnet",
            supports_segments=True,
        )
        res_segmented = engine.calculate_route(req_segmented)
        assert res_segmented.status == RoutingStatus.CALCULATED
        assert res_segmented.geometry == []  # Suppressed flat geometry protects CesiumJS
        assert len(res_segmented.geometry_segments) == 2

        seg1 = res_segmented.geometry_segments[0]
        seg2 = res_segmented.geometry_segments[1]
        # Segment 1 is in the Eastern hemisphere (ends at +180.0)
        assert seg1[-1][0] == 180.0
        # Segment 2 is in the Western hemisphere (starts at -180.0)
        assert seg2[0][0] == -180.0

        # Zero-weight bridge: distance is 400 + 0 + 400 = 800 NM
        assert res_segmented.summary.graph_distance_nm == 800.0

        # 14c. Reverse direction West -> East (West -> East antimeridian bridge traversal)
        # Verifies that bridge edge orientation does not invert entry/exit coordinates
        req_w2e = RoutingRequest(
            origin=PortIdentifierOrCoords(coordinates=[-170.0, 50.0]),
            destination=PortIdentifierOrCoords(coordinates=[170.0, 50.0]),
            routing_mode="marnet",
            supports_segments=True,
        )
        res_w2e = engine.calculate_route(req_w2e)
        assert res_w2e.status == RoutingStatus.CALCULATED
        assert res_w2e.geometry == []
        assert len(res_w2e.geometry_segments) == 2

        w2e_seg1 = res_w2e.geometry_segments[0]
        w2e_seg2 = res_w2e.geometry_segments[1]
        # Segment 1 is in Western hemisphere (ends at -180.0)
        assert w2e_seg1[0][0] == -170.0
        assert w2e_seg1[-1][0] == -180.0
        # Segment 2 is in Eastern hemisphere (starts at +180.0)
        assert w2e_seg2[0][0] == 180.0
        assert w2e_seg2[-1][0] == 170.0

        # Assert no segment in either direction contains an intra-segment antimeridian discontinuity (|lon[i+1] - lon[i]| > 180)
        for s_idx, seg in enumerate(res_w2e.geometry_segments):
            for i in range(len(seg) - 1):
                d_lon = abs(seg[i + 1][0] - seg[i][0])
                assert d_lon <= 180.0, f"Discontinuity in w2e seg {s_idx} between {seg[i]} and {seg[i+1]}"
        for s_idx, seg in enumerate(res_segmented.geometry_segments):
            for i in range(len(seg) - 1):
                d_lon = abs(seg[i + 1][0] - seg[i][0])
                assert d_lon <= 180.0, f"Discontinuity in e2w seg {s_idx} between {seg[i]} and {seg[i+1]}"

    def test_15_deterministic_output_identical_inputs(self) -> None:
        """15. Deterministic output for identical inputs and graph artifacts."""
        data = create_synthetic_marnet_data()
        graph = MarnetMaritimeGraph(graph_data=data)
        engine = MaritimeRoutingEngine(marnet_graph=graph)

        req = RoutingRequest(
            origin=PortIdentifierOrCoords(coordinates=[0.0, 0.0]),
            destination=PortIdentifierOrCoords(coordinates=[20.0, 20.0]),
            canal_policy="avoid_panama",
            routing_mode="marnet",
        )

        first_res = engine.calculate_route(req)
        for _ in range(10):
            res = engine.calculate_route(req)
            assert res.route_id == first_res.route_id
            assert res.status == first_res.status
            assert res.summary.total_distance_nm == first_res.summary.total_distance_nm
            assert res.geometry == first_res.geometry
            assert len(res.waypoints) == len(first_res.waypoints)

    def test_16_cache_integrity_lifecycle(self, tmp_path: Path) -> None:
        """16. Cache integrity lifecycle:
        - Manifest and artifact are validated before graph is loaded.
        - After successful initialization, the engine consistently uses its validated
          in-memory snapshot even if the disk file subsequently changes.
        - A newly initialized engine rejects the changed artifact with configuration_error.
        - Missing or invalid MARNET artifacts never trigger prototype fallback.
        """
        cache_dir = tmp_path / "cache_lifecycle"
        data = create_synthetic_marnet_data()
        write_test_cache(cache_dir, data)

        # Step 1: Successful initial load validates manifest and artifact
        engine = MaritimeRoutingEngine(marnet_cache_dir=cache_dir)
        req = RoutingRequest(
            origin=PortIdentifierOrCoords(coordinates=[0.0, 0.0]),
            destination=PortIdentifierOrCoords(coordinates=[10.0, 0.0]),
            routing_mode="marnet",
        )
        res_initial = engine.calculate_route(req)
        assert res_initial.status == RoutingStatus.CALCULATED
        assert res_initial.routing_mode == "marnet"
        assert res_initial.route_method == "marnet_50km_generalized_graph"
        assert res_initial.summary.distance_nm > 0.0

        # Step 2: Mutate the artifact file on disk (append corrupting bytes)
        net_path = cache_dir / "marnet_network.json"
        with open(net_path, "a", encoding="utf-8") as f:
            f.write("\ncorrupted_trailing_content")

        # Step 3: Active engine continues to serve using its validated in-memory snapshot
        res_active = engine.calculate_route(req)
        assert res_active.status == RoutingStatus.CALCULATED
        assert res_active.routing_mode == "marnet"
        assert res_active.route_id == res_initial.route_id
        assert res_active.summary.total_distance_nm == res_initial.summary.total_distance_nm
        assert res_active.geometry == res_initial.geometry

        # Step 4: A newly initialized engine rejects the mutated disk artifact with configuration_error
        fresh_engine = MaritimeRoutingEngine(marnet_cache_dir=cache_dir)
        res_rejected = fresh_engine.calculate_route(req)
        assert res_rejected.status == RoutingStatus.CONFIGURATION_ERROR
        assert res_rejected.routing_mode == "marnet"
        assert res_rejected.geometry == []
        assert res_rejected.geometry_segments == []
        assert "not found or failed integrity check" in res_rejected.error_message

        # Step 5: Missing or invalid MARNET artifacts never trigger prototype fallback
        assert res_rejected.status != RoutingStatus.CALCULATED
        assert res_rejected.route_method != "prototype_test_network_graph"

        # Step 6: Missing manifest also triggers configuration_error without prototype fallback
        (cache_dir / "manifest.json").unlink()
        missing_manifest_engine = MaritimeRoutingEngine(marnet_cache_dir=cache_dir)
        res_missing = missing_manifest_engine.calculate_route(req)
        assert res_missing.status == RoutingStatus.CONFIGURATION_ERROR
        assert res_missing.routing_mode == "marnet"
        assert res_missing.geometry == []
        assert res_missing.route_method != "prototype_test_network_graph"


# ---------------------------------------------------------------------------
# Opt-In Real MARNET Integration Benchmarks (PELAGOS_MARNET_INTEGRATION=1)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_marnet_graph() -> MarnetMaritimeGraph:
    """Load and cache the real compiled MARNET graph artifact."""
    if not (DEFAULT_CACHE_DIR / "marnet_network.json").exists():
        pytest.skip("marnet_network.json not found in cache")
    return MarnetMaritimeGraph(cache_dir=DEFAULT_CACHE_DIR)


@pytest.mark.skipif(
    not os.getenv("PELAGOS_MARNET_INTEGRATION"),
    reason="Full MARNET dataset integration test is opt-in. Set PELAGOS_MARNET_INTEGRATION=1 to run.",
)
class TestMarnetRealDatasetIntegration:
    """Verifies Dijkstra routing benchmarks on the real local compiled MARNET artifact."""

    def test_real_artifact_manifest_integrity_and_polar_count(
        self, real_marnet_graph: MarnetMaritimeGraph
    ) -> None:
        """Verify the exact 435 polar edge exclusion on the compiled artifact:
        - 433 Arctic edges (>= 66.5°N)
        - 2 Antarctic edges (<= -66.5°S: node_1-node_0 at -80°S, node_7376-node_7375 at -70°S)
        - 15,423 base edges - 435 polar edges = 14,988 navigable edges.
        """
        assert len(real_marnet_graph.nodes_data) == 7390
        assert real_marnet_graph.excluded_polar_edge_count == 435
        assert real_marnet_graph.graph.number_of_edges() == 14988
        # Specifically verify intermediate-crossing Arctic edge is excluded
        assert not real_marnet_graph.graph.has_edge("node_2497", "node_2522")
        # Specifically verify southern edges reaching 70°S and 80°S are excluded
        assert not real_marnet_graph.graph.has_edge("node_1", "node_0")
        assert not real_marnet_graph.graph.has_edge("node_7376", "node_7375")

    def test_rotterdam_singapore_standard_and_suez_bypass(
        self, real_marnet_graph: MarnetMaritimeGraph
    ) -> None:
        """Verify Rotterdam to Singapore Dijkstra benchmark:
        - allow: ~8,399 NM (via Suez)
        - avoid_suez: ~11,855 NM (Cape of Good Hope bypass at lat -35°S)
        """
        engine = MaritimeRoutingEngine(marnet_graph=real_marnet_graph)

        # NLRTM to SGSIN standard
        res_allow = engine.calculate_route(
            RoutingRequest(origin="NLRTM", destination="SGSIN", canal_policy="allow", routing_mode="marnet")
        )
        assert res_allow.status == RoutingStatus.CALCULATED
        assert 8350.0 <= res_allow.summary.graph_distance_nm <= 8450.0
        assert "suez" in res_allow.summary.canals_transited

        # NLRTM to SGSIN avoid_suez
        res_bypass = engine.calculate_route(
            RoutingRequest(origin="NLRTM", destination="SGSIN", canal_policy="avoid_suez", routing_mode="marnet")
        )
        assert res_bypass.status == RoutingStatus.CALCULATED
        assert 11800.0 <= res_bypass.summary.graph_distance_nm <= 11900.0
        assert "suez" not in res_bypass.summary.canals_transited

        # Verify physical Cape of Good Hope bypass (lat <= -34.0°S)
        min_lat = min(pt[1] for pt in res_bypass.geometry)
        assert min_lat <= -34.0

    def test_new_orleans_yokohama_panama_avoidance_benchmarks(
        self, real_marnet_graph: MarnetMaritimeGraph
    ) -> None:
        """Verify New Orleans to Yokohama Dijkstra benchmarks:
        - allow: ~9,104 NM graph distance (via Panama, crossing antimeridian -> requires supports_segments=True)
        - allow (unsegmented): fails closed with unsupported_route
        - avoid_panama: ~14,404 NM (transatlantic via Suez/Malacca)
        - avoid_all: ~15,583 NM (African circumnavigation)
        """
        engine = MaritimeRoutingEngine(marnet_graph=real_marnet_graph)

        # USMSY to JPYOK standard (legacy unsegmented caller fails closed)
        res_unseg = engine.calculate_route(
            RoutingRequest(origin="USMSY", destination="JPYOK", canal_policy="allow", routing_mode="marnet", supports_segments=False)
        )
        assert res_unseg.status == RoutingStatus.UNSUPPORTED_ROUTE
        assert res_unseg.geometry == []
        assert res_unseg.geometry_segments == []
        assert "segment-aware" in res_unseg.error_message

        # USMSY to JPYOK standard (segmented caller succeeds with disjoint polylines)
        res_allow = engine.calculate_route(
            RoutingRequest(origin="USMSY", destination="JPYOK", canal_policy="allow", routing_mode="marnet", supports_segments=True)
        )
        assert res_allow.status == RoutingStatus.CALCULATED
        assert 9050.0 <= res_allow.summary.graph_distance_nm <= 9150.0
        assert "panama" in res_allow.summary.canals_transited
        assert len(res_allow.geometry_segments) >= 2
        assert res_allow.geometry == []

        # USMSY to JPYOK avoid_panama (eastward transatlantic via Suez/Malacca)
        res_avoid_panama = engine.calculate_route(
            RoutingRequest(origin="USMSY", destination="JPYOK", canal_policy="avoid_panama", routing_mode="marnet")
        )
        assert res_avoid_panama.status == RoutingStatus.CALCULATED
        assert 14350.0 <= res_avoid_panama.summary.graph_distance_nm <= 14450.0
        assert "panama" not in res_avoid_panama.summary.canals_transited
        assert "suez" in res_avoid_panama.summary.canals_transited

        # USMSY to JPYOK avoid_all (eastward African bypass, zero canal transits)
        res_avoid_all = engine.calculate_route(
            RoutingRequest(origin="USMSY", destination="JPYOK", canal_policy="avoid_all", routing_mode="marnet")
        )
        assert res_avoid_all.status == RoutingStatus.CALCULATED
        assert 15500.0 <= res_avoid_all.summary.graph_distance_nm <= 15650.0
        assert "panama" not in res_avoid_all.summary.canals_transited
        assert "suez" not in res_avoid_all.summary.canals_transited

    def test_seattle_yokohama_antimeridian_safeties(
        self, real_marnet_graph: MarnetMaritimeGraph
    ) -> None:
        """Verify transpacific route crossing antimeridian:
        - supports_segments=False -> rejected with unsupported_route
        - supports_segments=True -> calculated with geometry_segments
        """
        engine = MaritimeRoutingEngine(marnet_graph=real_marnet_graph)

        # Unsegmented client -> rejected
        res_legacy = engine.calculate_route(
            RoutingRequest(origin="USSEA", destination="JPYOK", routing_mode="marnet", supports_segments=False)
        )
        assert res_legacy.status == RoutingStatus.UNSUPPORTED_ROUTE
        assert res_legacy.geometry == []
        assert res_legacy.geometry_segments == []

        # Segmented client -> calculated
        res_segmented = engine.calculate_route(
            RoutingRequest(origin="USSEA", destination="JPYOK", routing_mode="marnet", supports_segments=True)
        )
        assert res_segmented.status == RoutingStatus.CALCULATED
        assert res_segmented.geometry == []
        assert len(res_segmented.geometry_segments) == 2

        # West-to-East (Seattle -> Yokohama):
        # Segment 0 must be in Western hemisphere (negative longitudes, ending at -180.0)
        seg0 = res_segmented.geometry_segments[0]
        assert seg0[-1][0] == -180.0
        assert all(pt[0] <= 0.0 for pt in seg0)

        # Segment 1 must be in Eastern hemisphere (positive longitudes, starting at +180.0)
        seg1 = res_segmented.geometry_segments[1]
        assert seg1[0][0] == 180.0
        assert all(pt[0] >= 0.0 for pt in seg1)

        # Zero intra-segment antimeridian jumps across all segments
        for s_idx, seg in enumerate(res_segmented.geometry_segments):
            for i in range(len(seg) - 1):
                d_lon = abs(seg[i + 1][0] - seg[i][0])
                assert d_lon <= 180.0, f"Discontinuity in Seattle->Yokohama seg {s_idx} at {i}: {seg[i]} -> {seg[i+1]}"

        # Reverse direction East-to-West (Yokohama -> Seattle):
        res_rev = engine.calculate_route(
            RoutingRequest(origin="JPYOK", destination="USSEA", routing_mode="marnet", supports_segments=True)
        )
        assert res_rev.status == RoutingStatus.CALCULATED
        assert res_rev.geometry == []
        assert len(res_rev.geometry_segments) == 2

        rev_seg0 = res_rev.geometry_segments[0]
        assert rev_seg0[-1][0] == 180.0
        assert all(pt[0] >= 0.0 for pt in rev_seg0)

        rev_seg1 = res_rev.geometry_segments[1]
        assert rev_seg1[0][0] == -180.0
        assert all(pt[0] <= 0.0 for pt in rev_seg1)

        for s_idx, seg in enumerate(res_rev.geometry_segments):
            for i in range(len(seg) - 1):
                d_lon = abs(seg[i + 1][0] - seg[i][0])
                assert d_lon <= 180.0, f"Discontinuity in Yokohama->Seattle seg {s_idx} at {i}: {seg[i]} -> {seg[i+1]}"
