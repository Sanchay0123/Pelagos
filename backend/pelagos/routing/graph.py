"""Pelagos Maritime Routing - Sea-Lane Graph and Shortest Path Pathfinder.

Constructs a local NetworkX graph from verified sea-lane fixtures and computes
deterministic geodesic maritime routes while respecting chokepoint policies.
"""

from dataclasses import dataclass
import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import networkx as nx

from pelagos.routing.geometry import (
    geodesic_distance_nm,
    validate_coordinates,
)
from pelagos.routing.ingestion import compute_sha256

logger = logging.getLogger(__name__)

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sea_lanes.json"

CANONICAL_PASSAGES: Set[str] = {
    "panama",
    "suez",
    "dover",
    "gibraltar",
    "malacca",
    "bering",
    "magellan",
    "northwest",
    "northeast",
    "babelmandeb",
    "kiel",
    "corinth",
}

POLAR_CIRCLE_LATITUDE: float = 66.5
ARCTIC_CIRCLE_LATITUDE: float = 66.5  # Backward-compatible alias
ANTARCTIC_CIRCLE_LATITUDE: float = -66.5


class MarnetCacheError(Exception):
    """Raised when the MARNET graph cache or manifest is missing, corrupt, or invalid."""


def is_polar_edge(geometry: Sequence[Sequence[float]]) -> bool:
    """Evaluate whether any vertex in an edge's geometry reaches or exceeds the polar circles (>= 66.5°N or <= -66.5°S).

    Fails closed: If geometry is missing, empty, malformed, or cannot be parsed,
    it returns True (treating the edge as unsafe/polar).
    """
    if not geometry or not isinstance(geometry, (list, tuple)):
        return True
    if len(geometry) < 2:
        return True
    try:
        for pt in geometry:
            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                return True
            lat = float(pt[1])
            lon = float(pt[0])
            if math.isnan(lat) or math.isinf(lat) or math.isnan(lon) or math.isinf(lon):
                return True
            if abs(lat) >= POLAR_CIRCLE_LATITUDE:
                return True
    except (TypeError, ValueError):
        return True
    return False


# Backward compatibility alias
is_arctic_edge = is_polar_edge


@dataclass
class MarnetPathResult:
    """Structured outcome of a MARNET graph shortest path calculation."""

    node_path: List[str]
    graph_distance_nm: float
    flat_polyline: List[List[float]]
    geometry_segments: List[List[List[float]]]
    canals_transited: List[str]
    has_antimeridian_crossing: bool


class MaritimeGraph:
    """Deterministic maritime graph solver using NetworkX."""

    def __init__(self, fixture_path: Optional[Path] = None) -> None:
        self.fixture_path = fixture_path or _FIXTURE_PATH
        self.graph = nx.Graph()
        self.nodes_data: Dict[str, dict] = {}
        self._load_graph()

    def _load_graph(self) -> None:
        """Load nodes and edges deterministically from the sea-lane fixture."""
        if not self.fixture_path.exists():
            raise FileNotFoundError(f"Sea-lane graph fixture not found at {self.fixture_path}")

        with open(self.fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.nodes_data = data.get("nodes", {})
        for node_id, node_info in self.nodes_data.items():
            coords = node_info.get("coords", [0.0, 0.0])
            self.graph.add_node(
                node_id,
                name=node_info.get("name", node_id),
                coords=coords,
                node_type=node_info.get("type", "sea_lane"),
            )

        edges_list = data.get("edges", [])
        for edge in edges_list:
            u = edge["u"]
            v = edge["v"]
            dist_nm = float(edge["distance_nm"])
            geom = edge.get("geometry", [self.nodes_data[u]["coords"], self.nodes_data[v]["coords"]])
            canal = edge.get("canal")
            name = edge.get("name", f"{u} to {v}")

            self.graph.add_edge(
                u,
                v,
                weight=dist_nm,
                distance_nm=dist_nm,
                geometry=geom,
                canal=canal,
                name=name,
            )

    def find_nearest_node(
        self,
        longitude: float,
        latitude: float,
        max_distance_nm: float = 300.0,
    ) -> Optional[Tuple[str, float]]:
        """Find the closest maritime network node to the given coordinates.

        Args:
            longitude: Longitude in degrees.
            latitude: Latitude in degrees.
            max_distance_nm: Maximum allowable snapping distance.

        Returns:
            Tuple of (node_id, distance_nm) or None if beyond max_distance_nm.
        """
        valid, _ = validate_coordinates(longitude, latitude)
        if not valid:
            return None

        best_node: Optional[str] = None
        best_dist = float("inf")

        for node_id, node_attr in self.graph.nodes(data=True):
            # Skip disconnected or inland test nodes when snapping open-water coordinates
            if node_attr.get("node_type") == "disconnected_water":
                continue
            coords = node_attr.get("coords")
            if not coords or len(coords) != 2:
                continue
            dist = geodesic_distance_nm(longitude, latitude, coords[0], coords[1])
            if dist < best_dist and dist <= max_distance_nm:
                best_dist = dist
                best_node = node_id

        if best_node is not None:
            return best_node, round(best_dist, 3)
        return None

    def compute_shortest_path(
        self,
        source_node: str,
        target_node: str,
        canal_policy: str = "allow",
    ) -> Optional[Tuple[List[str], float, List[List[float]], List[str]]]:
        """Compute the shortest path along the sea-lane network.

        Args:
            source_node: Starting node ID.
            target_node: Destination node ID.
            canal_policy: 'allow', 'avoid_panama', 'avoid_suez', or 'avoid_all'.

        Returns:
            Tuple of (node_path, total_distance_nm, polyline_coords, canals_transited)
            or None if no path exists.
        """
        if source_node not in self.graph or target_node not in self.graph:
            return None

        # Build active view based on canal policy
        policy = (canal_policy or "allow").lower().strip()

        def edge_filter(u: str, v: str) -> bool:
            edge_data = self.graph[u][v]
            canal = edge_data.get("canal")
            if not canal:
                return True
            if policy == "avoid_panama" and canal == "panama":
                return False
            if policy == "avoid_suez" and canal == "suez":
                return False
            if policy == "avoid_all" and canal in ("panama", "suez"):
                return False
            return True

        subgraph = nx.subgraph_view(self.graph, filter_edge=edge_filter)

        if not nx.has_path(subgraph, source_node, target_node):
            return None

        try:
            node_path = nx.shortest_path(subgraph, source=source_node, target=target_node, weight="weight")
            total_dist_nm = nx.shortest_path_length(subgraph, source=source_node, target=target_node, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

        # Assemble stitched ordered polyline coordinates and track canals
        polyline: List[List[float]] = []
        canals_transited: List[str] = []

        for i in range(len(node_path) - 1):
            u_node = node_path[i]
            v_node = node_path[i + 1]
            edge_data = subgraph[u_node][v_node]
            canal = edge_data.get("canal")
            if canal:
                canal_title = f"{canal.capitalize()} Canal"
                if canal_title not in canals_transited:
                    canals_transited.append(canal_title)

            edge_geom: Sequence[Sequence[float]] = edge_data.get("geometry", [])
            if not edge_geom:
                edge_geom = [
                    self.nodes_data[u_node]["coords"],
                    self.nodes_data[v_node]["coords"],
                ]

            # Verify geometry orientation matches traversal direction
            u_coords = self.nodes_data[u_node]["coords"]
            v_coords = self.nodes_data[v_node]["coords"]

            # If the geometry starts closer to v_node than u_node, reverse it
            first_pt = edge_geom[0]
            d_u_first = (u_coords[0] - first_pt[0]) ** 2 + (u_coords[1] - first_pt[1]) ** 2
            d_v_first = (v_coords[0] - first_pt[0]) ** 2 + (v_coords[1] - first_pt[1]) ** 2

            segment_pts = list(edge_geom)
            if d_v_first < d_u_first:
                segment_pts.reverse()

            for pt in segment_pts:
                # Deduplicate consecutive identical vertices
                if not polyline or polyline[-1] != pt:
                    polyline.append([float(pt[0]), float(pt[1])])

        return node_path, round(total_dist_nm, 3), polyline, canals_transited


# Backwards compatibility alias
PrototypeMaritimeGraph = MaritimeGraph


class MarnetMaritimeGraph:
    """Deterministic graph solver for the 50 km generalized MARNET network.

    Loads from verified local cache artifact with multi-tier manifest validation,
    comprehensive vertex-level Arctic boundary exclusion, normalized passage policy
    views, and antimeridian-safe segmentation.
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        graph_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.cache_dir = cache_dir or Path(".cache/pelagos/routing")
        self.graph = nx.Graph()
        self.nodes_data: Dict[str, List[float]] = {}
        self.metadata: Dict[str, Any] = {}
        self.excluded_polar_edge_count: int = 0
        self.excluded_arctic_edge_count: int = 0
        self.invalid_geometry_edge_count: int = 0

        if graph_data is not None:
            self._build_graph_from_data(graph_data)
        else:
            self._load_from_cache()

    def _load_from_cache(self) -> None:
        """Load and validate the local MARNET compiled artifact against manifest.json."""
        manifest_path = self.cache_dir / "manifest.json"
        network_path = self.cache_dir / "marnet_network.json"

        if not manifest_path.exists():
            raise MarnetCacheError(f"MARNET manifest not found at {manifest_path}")
        if not network_path.exists():
            raise MarnetCacheError(f"MARNET graph artifact not found at {network_path}")

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as exc:
            raise MarnetCacheError(f"Failed to read or parse manifest.json: {exc}") from exc

        schema_ver = manifest.get("schema_version")
        if schema_ver != "pelagos-marnet-graph-v1":
            raise MarnetCacheError(
                f"Incompatible manifest schema_version: '{schema_ver}', expected 'pelagos-marnet-graph-v1'"
            )

        trans_ver = manifest.get("transformation_version")
        if trans_ver != "1.0.0":
            raise MarnetCacheError(
                f"Incompatible manifest transformation_version: '{trans_ver}', expected '1.0.0'"
            )

        compiled_artifacts = manifest.get("compiled_artifacts", {})
        network_meta = compiled_artifacts.get("marnet_network_json", {})
        expected_sha256 = network_meta.get("sha256") or manifest.get("compiled_sha256")
        if not expected_sha256:
            raise MarnetCacheError("Manifest does not contain compiled artifact sha256 checksum.")

        expected_size = network_meta.get("size_bytes")
        if expected_size is not None and network_path.stat().st_size != expected_size:
            raise MarnetCacheError(
                f"Size mismatch for {network_path.name}: expected {expected_size} bytes, got {network_path.stat().st_size} bytes"
            )

        computed_sha256 = compute_sha256(network_path)
        if computed_sha256.lower() != expected_sha256.lower():
            raise MarnetCacheError(
                f"Checksum mismatch for {network_path.name}: expected {expected_sha256}, got {computed_sha256}"
            )

        try:
            with open(network_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            raise MarnetCacheError(f"Failed to read or parse {network_path.name}: {exc}") from exc

        self._build_graph_from_data(data)

    def _build_graph_from_data(self, graph_data: Dict[str, Any]) -> None:
        """Construct NetworkX graph applying vertex-level Arctic boundary exclusion and passage normalization."""
        self.metadata = graph_data.get("metadata", {})
        nodes_dict = graph_data.get("nodes", {})

        for node_id, coords in nodes_dict.items():
            lon = float(coords[0])
            lat = float(coords[1])
            self.graph.add_node(node_id, coords=[lon, lat])
            self.nodes_data[node_id] = [lon, lat]

        edges_list = graph_data.get("edges", [])
        for edge in edges_list:
            u = edge.get("u")
            v = edge.get("v")
            geom = edge.get("geometry")

            # Fail closed on missing, malformed, or invalid geometry
            if not geom or not isinstance(geom, (list, tuple)) or len(geom) < 2:
                self.invalid_geometry_edge_count += 1
                continue

            # Enforce symmetric polar exclusion against every coordinate vertex
            if is_polar_edge(geom):
                self.excluded_polar_edge_count += 1
                self.excluded_arctic_edge_count += 1
                continue

            pass_id = edge.get("pass_id")
            if pass_id is not None:
                if isinstance(pass_id, str):
                    pass_id = pass_id.strip().lower()
                    if not pass_id:
                        pass_id = None
                    elif pass_id not in CANONICAL_PASSAGES:
                        logger.warning("Unrecognized passage identifier '%s' on edge %s-%s", pass_id, u, v)
                else:
                    pass_id = None

            weight = float(edge.get("weight_nm", 0.0))
            stitched = bool(edge.get("stitched", False))

            self.graph.add_edge(
                u,
                v,
                weight=weight,
                distance_nm=weight,
                pass_id=pass_id,
                stitched=stitched,
                geometry=geom,
            )

    def find_nearest_node(
        self,
        longitude: float,
        latitude: float,
        max_distance_nm: float = 50.0,
    ) -> Optional[Tuple[str, float]]:
        """Exhaustive geodesic scan baseline over all network nodes.

        Args:
            longitude: Longitude in degrees.
            latitude: Latitude in degrees.
            max_distance_nm: Maximum allowable snapping radius (provisional ceiling: 50.0 NM).

        Returns:
            Tuple of (node_id, distance_nm) if within threshold, else None.
        """
        valid, _ = validate_coordinates(longitude, latitude)
        if not valid:
            return None

        best_node: Optional[str] = None
        best_dist = float("inf")

        for node_id, coords in self.nodes_data.items():
            if self.graph.degree(node_id) == 0:
                continue
            if abs(coords[1]) >= POLAR_CIRCLE_LATITUDE:
                continue
            dist = geodesic_distance_nm(longitude, latitude, coords[0], coords[1])
            if dist < best_dist:
                best_dist = dist
                best_node = node_id

        if best_node is not None and best_dist <= max_distance_nm:
            return best_node, round(best_dist, 3)
        return None

    def compute_shortest_path(
        self,
        source_node: str,
        target_node: str,
        canal_policy: str = "allow",
    ) -> Optional[MarnetPathResult]:
        """Compute the shortest path along the MARNET network respecting canal policy."""
        if source_node not in self.graph or target_node not in self.graph:
            return None

        policy = (canal_policy or "allow").lower().strip()

        def edge_filter(u: str, v: str) -> bool:
            edge_data = self.graph[u][v]
            p_id = edge_data.get("pass_id")
            if not p_id:
                return True
            if policy == "avoid_panama" and p_id == "panama":
                return False
            if policy == "avoid_suez" and p_id == "suez":
                return False
            if policy == "avoid_all" and p_id in ("panama", "suez"):
                return False
            return True

        subgraph = nx.subgraph_view(self.graph, filter_edge=edge_filter)

        if not nx.has_path(subgraph, source_node, target_node):
            return None

        try:
            node_path = nx.shortest_path(subgraph, source=source_node, target=target_node, weight="weight")
            total_dist_nm = nx.shortest_path_length(subgraph, source=source_node, target=target_node, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

        flat_polyline: List[List[float]] = []
        geometry_segments: List[List[List[float]]] = [[]]
        canals_transited: List[str] = []
        has_antimeridian_crossing = False

        for i in range(len(node_path) - 1):
            u_node = node_path[i]
            v_node = node_path[i + 1]
            edge_data = subgraph[u_node][v_node]

            p_id = edge_data.get("pass_id")
            if p_id and p_id not in canals_transited:
                canals_transited.append(p_id)

            stitched = edge_data.get("stitched", False)
            edge_geom = list(edge_data.get("geometry", []))
            if not edge_geom:
                edge_geom = [self.nodes_data[u_node], self.nodes_data[v_node]]

            u_coords = self.nodes_data[u_node]
            v_coords = self.nodes_data[v_node]

            d_u_first = (u_coords[0] - edge_geom[0][0]) ** 2 + (u_coords[1] - edge_geom[0][1]) ** 2
            d_v_first = (v_coords[0] - edge_geom[0][0]) ** 2 + (v_coords[1] - edge_geom[0][1]) ** 2

            segment_pts = list(edge_geom)
            if d_v_first < d_u_first:
                segment_pts.reverse()

            # Antimeridian bridge edge detection
            is_bridge = stitched or (
                len(segment_pts) == 2
                and abs(abs(segment_pts[0][0]) - 180.0) < 1e-4
                and abs(abs(segment_pts[1][0]) - 180.0) < 1e-4
                and (segment_pts[0][0] * segment_pts[1][0] < 0)
            )

            if is_bridge:
                has_antimeridian_crossing = True
                entry_pt = [float(u_coords[0]), float(u_coords[1])]
                if not geometry_segments[-1] or geometry_segments[-1][-1] != entry_pt:
                    geometry_segments[-1].append(entry_pt)
                if not flat_polyline or flat_polyline[-1] != entry_pt:
                    flat_polyline.append(entry_pt)

                exit_pt = [float(v_coords[0]), float(v_coords[1])]
                geometry_segments.append([exit_pt])
                flat_polyline.append(exit_pt)
            else:
                for pt in segment_pts:
                    coord = [float(pt[0]), float(pt[1])]
                    if not geometry_segments[-1] or geometry_segments[-1][-1] != coord:
                        geometry_segments[-1].append(coord)
                    if not flat_polyline or flat_polyline[-1] != coord:
                        flat_polyline.append(coord)

        geometry_segments = [s for s in geometry_segments if s]

        return MarnetPathResult(
            node_path=node_path,
            graph_distance_nm=round(total_dist_nm, 3),
            flat_polyline=flat_polyline,
            geometry_segments=geometry_segments,
            canals_transited=canals_transited,
            has_antimeridian_crossing=has_antimeridian_crossing,
        )
