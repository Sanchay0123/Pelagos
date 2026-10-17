"""Pelagos Maritime Routing - Sea-Lane Graph and Shortest Path Pathfinder.

Constructs a local NetworkX graph from verified sea-lane fixtures and computes
deterministic geodesic maritime routes while respecting chokepoint policies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import networkx as nx

from pelagos.routing.geometry import (
    geodesic_distance_nm,
    validate_coordinates,
)

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sea_lanes.json"


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
            d_u_first = geodesic_distance_nm(u_coords[0], u_coords[1], first_pt[0], first_pt[1])
            d_v_first = geodesic_distance_nm(v_coords[0], v_coords[1], first_pt[0], first_pt[1])

            segment_pts = list(edge_geom)
            if d_v_first < d_u_first:
                segment_pts.reverse()

            for pt in segment_pts:
                # Deduplicate consecutive identical vertices
                if not polyline or polyline[-1] != pt:
                    polyline.append([float(pt[0]), float(pt[1])])

        return node_path, round(total_dist_nm, 3), polyline, canals_transited
