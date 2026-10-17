"""Pelagos Maritime Routing - Local Routing Engine Service.

Coordinates port resolution, topological sea-lane pathfinding, geodesic distance
calculation, and deterministic result synthesis without external SaaS or synthetic fallback.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from pelagos.routing.geometry import (
    NAV_SAFETY_DISCLAIMER,
    geodesic_distance_nm,
    nm_to_km,
    validate_coordinates,
)
from pelagos.routing.graph import MaritimeGraph
from pelagos.routing.ports import PortRepository
from pelagos.routing.schemas import (
    PortIdentifierOrCoords,
    PortRecord,
    RouteSummary,
    RouteWaypoint,
    RoutingRequest,
    RoutingResult,
)

_PROVENANCE_PATH = Path(__file__).parent / "fixtures" / "provenance.json"


class MaritimeRoutingEngine:
    """Deterministic, local maritime routing service."""

    def __init__(
        self,
        port_repo: Optional[PortRepository] = None,
        graph: Optional[MaritimeGraph] = None,
        provenance_path: Optional[Path] = None,
    ) -> None:
        self.port_repo = port_repo or PortRepository()
        self.graph = graph or MaritimeGraph()
        self.provenance_path = provenance_path or _PROVENANCE_PATH
        self._provenance_metadata: Dict[str, str] = self._load_provenance()

    def _load_provenance(self) -> Dict[str, str]:
        """Load provenance summary from local metadata fixture."""
        if self.provenance_path.exists():
            with open(self.provenance_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            return {
                "dataset_name": meta.get("dataset_name", "Pelagos Prototype Maritime Routing Network"),
                "version": meta.get("version", "1.0.0-prototype"),
                "fixture_type": meta.get("sea_lane_graph", {}).get("fixture_type", "synthetic_prototype_corridor_fixture"),
                "is_upstream_extracted": str(meta.get("sea_lane_graph", {}).get("is_upstream_extracted", False)).lower(),
                "graph_origin": meta.get("sea_lane_graph", {}).get("creation_method", "Manually defined macro corridor coordinates; NOT extracted from Eurostat marnet.gpkg."),
                "upstream_eurostat_status": meta.get("sea_lane_graph", {}).get("upstream_reference_target", {}).get("ingestion_status", "unresolved_prerequisite"),
                "independent_navigational_reference": meta.get("independent_hydrographic_references", {}).get("publication", "NGA Pub. 151"),
            }
        return {
            "dataset_name": "Pelagos Prototype Routing Network",
            "version": "1.0.0-prototype",
            "fixture_type": "synthetic_prototype_corridor_fixture",
            "is_upstream_extracted": "false",
        }

    def _generate_route_id(
        self,
        origin_key: str,
        dest_key: str,
        canal_policy: str,
    ) -> str:
        """Generate a deterministic SHA-256 identifier for a given route query."""
        raw = f"{origin_key.strip().upper()}->{dest_key.strip().upper()}|policy={canal_policy.strip().lower()}|v={self._provenance_metadata.get('version', '1.0.0')}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _resolve_and_snap(
        self,
        endpoint: Union[PortIdentifierOrCoords, str],
        label: str,
    ) -> Tuple[Optional[str], Optional[PortRecord], Optional[List[float]], Optional[str]]:
        """Resolve endpoint to port record, coordinates, and nearest graph node.

        Returns:
            Tuple of (graph_node_id, port_record, coordinates [lon, lat], error_message).
        """
        port, coords, err = self.port_repo.resolve_endpoint(endpoint)
        if err:
            return None, None, None, f"{label} resolution failed: {err}"

        # If a port record is matched
        if port is not None:
            coords = port.coordinates
            if port.nearest_graph_node and port.nearest_graph_node in self.graph.graph:
                return port.nearest_graph_node, port, coords, None

        # Snap coordinates to closest marine network node
        if coords is not None:
            snap = self.graph.find_nearest_node(coords[0], coords[1], max_distance_nm=250.0)
            if snap is None:
                return (
                    None,
                    port,
                    coords,
                    f"{label} coordinates [{coords[0]}, {coords[1]}] are too distant from navigable sea lanes (>250 NM) or landlocked",
                )
            return snap[0], port, coords, None

        return None, None, None, f"{label} could not be resolved to valid maritime coordinates"

    def calculate_route(self, request: RoutingRequest) -> RoutingResult:
        """Compute an operational maritime route between origin and destination.

        Strictly enforces:
        - Deterministic graph-based sea routing.
        - Zero straight-line or great-circle chord fallback on disconnected networks (REQ-FR-OMR-002).
        - Explicit unsupported_route or invalid_input status reporting.
        - Separation of navigation disclaimer from safe clearance claims.
        """
        canal_policy = (request.canal_policy or "allow").lower().strip()
        if canal_policy not in ("allow", "avoid_panama", "avoid_suez", "avoid_all"):
            return RoutingResult(
                route_id=hashlib.sha256(b"invalid_canal_policy").hexdigest(),
                status="invalid_input",
                geometry=[],
                waypoints=[],
                limitations=[NAV_SAFETY_DISCLAIMER],
                data_provenance=self._provenance_metadata,
                error_message=f"Unsupported canal policy: '{request.canal_policy}'. Must be 'allow', 'avoid_panama', 'avoid_suez', or 'avoid_all'.",
            )

        # 1. Resolve Origin
        orig_node, orig_port, orig_coords, orig_err = self._resolve_and_snap(request.origin, "Origin")
        if orig_err:
            return RoutingResult(
                route_id=hashlib.sha256(orig_err.encode("utf-8")).hexdigest(),
                status="invalid_input",
                geometry=[],
                waypoints=[],
                limitations=[NAV_SAFETY_DISCLAIMER],
                data_provenance=self._provenance_metadata,
                error_message=orig_err,
            )

        # 2. Resolve Destination
        dest_node, dest_port, dest_coords, dest_err = self._resolve_and_snap(request.destination, "Destination")
        if dest_err:
            return RoutingResult(
                route_id=hashlib.sha256(dest_err.encode("utf-8")).hexdigest(),
                status="invalid_input",
                geometry=[],
                waypoints=[],
                limitations=[NAV_SAFETY_DISCLAIMER],
                data_provenance=self._provenance_metadata,
                error_message=dest_err,
            )

        assert orig_coords is not None and dest_coords is not None
        assert orig_node is not None and dest_node is not None

        orig_key = orig_port.port_id if orig_port else f"{orig_coords[0]:.4f},{orig_coords[1]:.4f}"
        dest_key = dest_port.port_id if dest_port else f"{dest_coords[0]:.4f},{dest_coords[1]:.4f}"
        route_id = self._generate_route_id(orig_key, dest_key, canal_policy)

        # 3. Pathfinding on Sea-Lane Graph
        path_result = self.graph.compute_shortest_path(
            source_node=orig_node,
            target_node=dest_node,
            canal_policy=canal_policy,
        )

        # 4. Handle Disconnected / Unsupported Route (Strictly No Straight-Line Fallback)
        if path_result is None:
            return RoutingResult(
                route_id=route_id,
                status="unsupported_route",
                geometry=[],
                waypoints=[],
                limitations=[
                    NAV_SAFETY_DISCLAIMER,
                    "No connected maritime sea lane path exists between the specified endpoints under the active canal constraints.",
                ],
                data_provenance=self._provenance_metadata,
                error_message=(
                    "Operational maritime route unavailable for specified corridor and constraints; "
                    "straight-line geometry suppressed."
                ),
            )

        node_path, graph_dist_nm, polyline, canals = path_result

        # 5. Assemble Waypoint Sequence and Cumulative Distances
        waypoints: List[RouteWaypoint] = []
        cumulative_nm = 0.0

        for i, nid in enumerate(node_path):
            node_data = self.graph.nodes_data.get(nid, {})
            coords = node_data.get("coords", [0.0, 0.0])
            w_name = node_data.get("name", nid)
            w_type = node_data.get("type", "sea_lane")

            if i > 0:
                prev_coords = self.graph.nodes_data[node_path[i - 1]]["coords"]
                leg_dist = geodesic_distance_nm(prev_coords[0], prev_coords[1], coords[0], coords[1])
                cumulative_nm += leg_dist

            waypoints.append(
                RouteWaypoint(
                    node_id=nid,
                    name=w_name,
                    longitude=coords[0],
                    latitude=coords[1],
                    waypoint_type=w_type,
                    cumulative_distance_nm=round(cumulative_nm, 3),
                )
            )

        total_nm = round(graph_dist_nm, 3)
        total_km = nm_to_km(total_nm)

        # 6. Assemble Limitations and Warnings
        limitations: List[str] = [
            NAV_SAFETY_DISCLAIMER,
            "Route computed using synthetic macro-corridor prototype fixture (57 nodes, 58 edges); not extracted from Eurostat MARNET or certified hydrographic databases.",
        ]
        if orig_port and orig_port.access_limitations:
            limitations.append(f"Origin access ({orig_port.port_id}): {orig_port.access_limitations}")
        if dest_port and dest_port.access_limitations:
            limitations.append(f"Destination access ({dest_port.port_id}): {dest_port.access_limitations}")

        summary = RouteSummary(
            origin_id=orig_port.port_id if orig_port else None,
            origin_name=orig_port.name if orig_port else f"Coordinates {orig_coords}",
            origin_coordinates=orig_coords,
            destination_id=dest_port.port_id if dest_port else None,
            destination_name=dest_port.name if dest_port else f"Coordinates {dest_coords}",
            destination_coordinates=dest_coords,
            distance_nm=total_nm,
            distance_km=total_km,
            canals_transited=canals,
            canal_policy_applied=canal_policy,
        )

        return RoutingResult(
            route_id=route_id,
            status="calculated",
            route_method="prototype_test_network_graph",
            summary=summary,
            geometry=polyline,
            waypoints=waypoints,
            limitations=limitations,
            data_provenance=self._provenance_metadata,
        )
