from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from pelagos.routing.geometry import (
    NAV_SAFETY_DISCLAIMER,
    geodesic_distance_nm,
    nm_to_km,
    validate_coordinates,
)
from pelagos.routing.graph import (
    MarnetCacheError,
    MarnetMaritimeGraph,
    MaritimeGraph,
)
from pelagos.routing.ports import PortRepository
from pelagos.routing.schemas import (
    PortIdentifierOrCoords,
    PortRecord,
    PortSnappingDetail,
    RouteSummary,
    RouteWaypoint,
    RoutingRequest,
    RoutingResult,
    RoutingStatus,
)

logger = logging.getLogger(__name__)

_PROVENANCE_PATH = Path(__file__).parent / "fixtures" / "provenance.json"


class MaritimeRoutingEngine:
    """Deterministic, local maritime routing service with dual-mode support."""

    def __init__(
        self,
        port_repo: Optional[PortRepository] = None,
        graph: Optional[MaritimeGraph] = None,
        provenance_path: Optional[Path] = None,
        marnet_graph: Optional[MarnetMaritimeGraph] = None,
        marnet_cache_dir: Optional[Path] = None,
    ) -> None:
        self.port_repo = port_repo or PortRepository()
        self.graph = graph or MaritimeGraph()
        self.provenance_path = provenance_path or _PROVENANCE_PATH
        self._provenance_metadata: Dict[str, str] = self._load_provenance()
        self._marnet_graph: Optional[MarnetMaritimeGraph] = marnet_graph
        self.marnet_cache_dir = marnet_cache_dir or Path(".cache/pelagos/routing")

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

    def _get_marnet_provenance(self) -> Dict[str, str]:
        """Return standardized provenance metadata for MARNET routing."""
        return {
            "dataset_name": "Eurostat SeaRoute MARNET 50km Generalized Network",
            "transformation_version": "1.0.0",
            "schema_version": "pelagos-marnet-graph-v1",
            "source_repo": "https://github.com/eurostat/searoute",
            "source_commit": "88a2e568772a24687d60f5885c4943fcf4d7541d",
            "licensing": "European Union Public Licence (EUPL-1.2)",
            "rights_status": "unresolved_prerequisite",
            "operational_qualification": "provisional_decision_support_not_certified_for_navigation",
            "disclaimer": (
                "MARNET is a generalized shipping-lane network for macro-corridor approximation, "
                "not certified for vessel navigation, shallow-water clearance, or ECDIS use."
            ),
        }

    def _get_marnet_limitations(self) -> List[str]:
        """Return operational limitations and disclaimers for MARNET responses."""
        return [
            NAV_SAFETY_DISCLAIMER,
            "Route computed using Eurostat SeaRoute MARNET 50 km generalized maritime network; not certified for navigational safety or hydrographic clearance.",
            "Snapping distance and fairway approach connectors are straight-line geometric approximations, not surveyed pilotage channels.",
            "Polar waters (|latitude| >= 66.5, encompassing Arctic Circle and Antarctic Circle) are strictly excluded; operational polar routing is formally deferred pending certified sea-ice, bathymetric, and ice-class classification data. This threshold does not guarantee absence of seasonal sea-ice or navigation hazards at lower sub-polar latitudes.",
        ]

    def _get_marnet_graph(self) -> MarnetMaritimeGraph:
        """Retrieve or lazily initialize the verified MARNET graph instance.

        Cache Integrity Lifecycle:
        Integrity verification (manifest schema, transformation version, SHA-256
        checksum, and file size) occurs strictly at graph initialization.
        Once successfully loaded into memory, the running engine instance operates
        from its validated in-memory snapshot for all subsequent routing queries,
        guaranteeing deterministic in-memory routing performance without per-query
        disk re-hashing. Disk-level cache mutations or deletions occurring after
        initialization are not detected dynamically by an active engine instance;
        cache re-verification requires engine reinitialization or process restart.

        Raises:
            MarnetCacheError: If artifact or manifest is missing, corrupt, or invalid
                at initialization time.
        """
        if self._marnet_graph is None:
            self._marnet_graph = MarnetMaritimeGraph(cache_dir=self.marnet_cache_dir)
        return self._marnet_graph

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
        """Resolve endpoint to port record, coordinates, and nearest graph node for prototype mode."""
        port, coords, err = self.port_repo.resolve_endpoint(endpoint)
        if err:
            return None, None, None, f"{label} resolution failed: {err}"

        if port is not None:
            coords = port.coordinates
            if port.nearest_graph_node and port.nearest_graph_node in self.graph.graph:
                return port.nearest_graph_node, port, coords, None

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

        Dispatches to prototype mode (synthetic fixture) or marnet mode (50 km generalized network)
        based on explicit request parameter or PELAGOS_ROUTING_MODE environment variable.
        """
        routing_mode = (request.routing_mode or os.environ.get("PELAGOS_ROUTING_MODE", "prototype")).lower().strip()
        if routing_mode not in ("prototype", "marnet"):
            return RoutingResult(
                route_id=hashlib.sha256(b"invalid_routing_mode").hexdigest(),
                status=RoutingStatus.INVALID_INPUT,
                route_method="unknown",
                routing_mode=routing_mode,
                geometry=[],
                geometry_segments=[],
                waypoints=[],
                limitations=[NAV_SAFETY_DISCLAIMER],
                data_provenance=self._provenance_metadata,
                error_message=f"Unsupported routing mode: '{routing_mode}'. Must be 'prototype' or 'marnet'.",
            )

        if routing_mode == "prototype":
            return self._calculate_prototype_route(request)
        else:
            return self._calculate_marnet_route(request)

    def _calculate_prototype_route(self, request: RoutingRequest) -> RoutingResult:
        """Compute route using committed prototype fixture (57 nodes, 58 edges)."""
        canal_policy = (request.canal_policy or "allow").lower().strip()
        if canal_policy not in ("allow", "avoid_panama", "avoid_suez", "avoid_all"):
            return RoutingResult(
                route_id=hashlib.sha256(b"invalid_canal_policy").hexdigest(),
                status=RoutingStatus.INVALID_INPUT,
                route_method="prototype_test_network_graph",
                routing_mode="prototype",
                geometry=[],
                geometry_segments=[],
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
                status=RoutingStatus.INVALID_INPUT,
                route_method="prototype_test_network_graph",
                routing_mode="prototype",
                geometry=[],
                geometry_segments=[],
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
                status=RoutingStatus.INVALID_INPUT,
                route_method="prototype_test_network_graph",
                routing_mode="prototype",
                geometry=[],
                geometry_segments=[],
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

        # 4. Handle Disconnected / Unsupported Route
        if path_result is None:
            return RoutingResult(
                route_id=route_id,
                status=RoutingStatus.UNSUPPORTED_ROUTE,
                route_method="prototype_test_network_graph",
                routing_mode="prototype",
                geometry=[],
                geometry_segments=[],
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

        # 5. Assemble Waypoint Sequence
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
            total_distance_nm=total_nm,
            total_distance_km=total_km,
            graph_distance_nm=total_nm,
            origin_snap_distance_nm=0.0,
            destination_snap_distance_nm=0.0,
            canals_transited=canals,
            canal_policy_applied=canal_policy,
        )

        return RoutingResult(
            route_id=route_id,
            status=RoutingStatus.CALCULATED,
            route_method="prototype_test_network_graph",
            routing_mode="prototype",
            summary=summary,
            geometry=polyline,
            geometry_segments=[polyline] if polyline else [],
            waypoints=waypoints,
            limitations=limitations,
            data_provenance=self._provenance_metadata,
        )

    def _calculate_marnet_route(self, request: RoutingRequest) -> RoutingResult:
        """Compute route using verified local 50 km MARNET compiled network."""
        # Multi-tier verification: load MARNET graph or fail closed
        try:
            marnet = self._get_marnet_graph()
        except (MarnetCacheError, FileNotFoundError, json.JSONDecodeError, Exception) as exc:
            logger.critical("MARNET graph cache validation failed: %s", exc)
            return RoutingResult(
                route_id=hashlib.sha256(f"configuration_error:{exc}".encode("utf-8")).hexdigest(),
                status=RoutingStatus.CONFIGURATION_ERROR,
                route_method="marnet_50km_generalized_graph",
                routing_mode="marnet",
                geometry=[],
                geometry_segments=[],
                waypoints=[],
                limitations=self._get_marnet_limitations(),
                data_provenance=self._get_marnet_provenance(),
                error_message="MARNET graph cache not found or failed integrity check. Ingestion required via 'scripts/ingest_marnet.py'.",
            )

        # 1. Validate canal avoidance policy
        canal_policy = (request.canal_policy or "allow").lower().strip()
        if canal_policy not in ("allow", "avoid_panama", "avoid_suez", "avoid_all"):
            return RoutingResult(
                route_id=hashlib.sha256(b"invalid_canal_policy").hexdigest(),
                status=RoutingStatus.INVALID_INPUT,
                route_method="marnet_50km_generalized_graph",
                routing_mode="marnet",
                geometry=[],
                geometry_segments=[],
                waypoints=[],
                limitations=self._get_marnet_limitations(),
                data_provenance=self._get_marnet_provenance(),
                error_message=f"Unsupported canal policy: '{request.canal_policy}'. Must be 'allow', 'avoid_panama', 'avoid_suez', or 'avoid_all'.",
            )

        # 2. Resolve origin endpoint
        orig_port, orig_coords, orig_err = self.port_repo.resolve_endpoint(request.origin)
        if orig_err:
            return RoutingResult(
                route_id=hashlib.sha256(orig_err.encode("utf-8")).hexdigest(),
                status=RoutingStatus.INVALID_INPUT,
                route_method="marnet_50km_generalized_graph",
                routing_mode="marnet",
                geometry=[],
                geometry_segments=[],
                waypoints=[],
                limitations=self._get_marnet_limitations(),
                data_provenance=self._get_marnet_provenance(),
                error_message=orig_err,
            )

        # 3. Resolve destination endpoint
        dest_port, dest_coords, dest_err = self.port_repo.resolve_endpoint(request.destination)
        if dest_err:
            return RoutingResult(
                route_id=hashlib.sha256(dest_err.encode("utf-8")).hexdigest(),
                status=RoutingStatus.INVALID_INPUT,
                route_method="marnet_50km_generalized_graph",
                routing_mode="marnet",
                geometry=[],
                geometry_segments=[],
                waypoints=[],
                limitations=self._get_marnet_limitations(),
                data_provenance=self._get_marnet_provenance(),
                error_message=dest_err,
            )

        assert orig_coords is not None and dest_coords is not None

        # 4. Enforce symmetric polar boundary check on endpoints (|lat| >= 66.5)
        if abs(orig_coords[1]) >= 66.5 or abs(dest_coords[1]) >= 66.5:
            return RoutingResult(
                route_id=hashlib.sha256(b"polar_waters_excluded").hexdigest(),
                status=RoutingStatus.UNSUPPORTED_ROUTE,
                route_method="marnet_50km_generalized_graph",
                routing_mode="marnet",
                geometry=[],
                geometry_segments=[],
                waypoints=[],
                limitations=self._get_marnet_limitations(),
                data_provenance=self._get_marnet_provenance(),
                error_message="Route traverses polar waters (|latitude| >= 66.5; Arctic or Antarctic Circle); rejected because comprehensive ice and hydrographic clearance cannot be verified.",
            )

        # 5. Snap endpoints to MARNET network using provisional snapping radius
        max_snap = request.max_snap_distance_nm if request.max_snap_distance_nm is not None else 50.0

        orig_snap = marnet.find_nearest_node(orig_coords[0], orig_coords[1], max_distance_nm=max_snap)
        if orig_snap is None:
            orig_label = orig_port.name if orig_port else "Origin"
            return RoutingResult(
                route_id=hashlib.sha256(f"unsnappable_origin_{orig_coords}".encode("utf-8")).hexdigest(),
                status=RoutingStatus.UNSUPPORTED_ROUTE,
                route_method="marnet_50km_generalized_graph",
                routing_mode="marnet",
                geometry=[],
                geometry_segments=[],
                waypoints=[],
                limitations=self._get_marnet_limitations(),
                data_provenance=self._get_marnet_provenance(),
                error_message=f"{orig_label} coordinates [{orig_coords[0]}, {orig_coords[1]}] are too distant from navigable MARNET sea lanes (>{max_snap} NM provisional limit) or landlocked.",
            )

        dest_snap = marnet.find_nearest_node(dest_coords[0], dest_coords[1], max_distance_nm=max_snap)
        if dest_snap is None:
            dest_label = dest_port.name if dest_port else "Destination"
            return RoutingResult(
                route_id=hashlib.sha256(f"unsnappable_dest_{dest_coords}".encode("utf-8")).hexdigest(),
                status=RoutingStatus.UNSUPPORTED_ROUTE,
                route_method="marnet_50km_generalized_graph",
                routing_mode="marnet",
                geometry=[],
                geometry_segments=[],
                waypoints=[],
                limitations=self._get_marnet_limitations(),
                data_provenance=self._get_marnet_provenance(),
                error_message=f"{dest_label} coordinates [{dest_coords[0]}, {dest_coords[1]}] are too distant from navigable MARNET sea lanes (>{max_snap} NM provisional limit) or landlocked.",
            )

        orig_node, orig_snap_dist = orig_snap
        dest_node, dest_snap_dist = dest_snap
        snapped_orig_coords = marnet.nodes_data[orig_node]
        snapped_dest_coords = marnet.nodes_data[dest_node]

        # 6. Generate deterministic route_id
        orig_key = orig_port.port_id if orig_port else f"{orig_coords[0]:.4f},{orig_coords[1]:.4f}"
        dest_key = dest_port.port_id if dest_port else f"{dest_coords[0]:.4f},{dest_coords[1]:.4f}"
        raw = f"MARNET:{orig_key.strip().upper()}->{dest_key.strip().upper()}|policy={canal_policy}|v=1.0.0"
        route_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()

        # 7. Execute shortest path calculation on MARNET graph
        path_result = marnet.compute_shortest_path(
            source_node=orig_node,
            target_node=dest_node,
            canal_policy=canal_policy,
        )

        if path_result is None:
            return RoutingResult(
                route_id=route_id,
                status=RoutingStatus.UNSUPPORTED_ROUTE,
                route_method="marnet_50km_generalized_graph",
                routing_mode="marnet",
                geometry=[],
                geometry_segments=[],
                waypoints=[],
                limitations=self._get_marnet_limitations(),
                data_provenance=self._get_marnet_provenance(),
                error_message="No connected maritime sea lane path exists between endpoints under active constraints.",
            )

        # 8. Antimeridian Safety Interlock: Reject unsegmented callers crossing antimeridian
        if path_result.has_antimeridian_crossing and not request.supports_segments:
            return RoutingResult(
                route_id=route_id,
                status=RoutingStatus.UNSUPPORTED_ROUTE,
                route_method="marnet_50km_generalized_graph",
                routing_mode="marnet",
                geometry=[],
                geometry_segments=[],
                waypoints=[],
                limitations=self._get_marnet_limitations(),
                data_provenance=self._get_marnet_provenance(),
                error_message="Antimeridian-crossing route requires segment-aware renderer; suppressed under flat geometry to prevent visual wrap artifacts.",
            )

        # 9. Assemble connector polylines and segments
        if path_result.has_antimeridian_crossing:
            geometry: List[List[float]] = []
            geometry_segments = [list(seg) for seg in path_result.geometry_segments]
            if orig_coords != snapped_orig_coords and geometry_segments:
                if not geometry_segments[0] or geometry_segments[0][0] != orig_coords:
                    geometry_segments[0].insert(0, orig_coords)
            if dest_coords != snapped_dest_coords and geometry_segments:
                if not geometry_segments[-1] or geometry_segments[-1][-1] != dest_coords:
                    geometry_segments[-1].append(dest_coords)
        else:
            flat_coords: List[List[float]] = []
            if orig_coords != snapped_orig_coords:
                flat_coords.append(orig_coords)
            for pt in path_result.flat_polyline:
                if not flat_coords or flat_coords[-1] != pt:
                    flat_coords.append(pt)
            if dest_coords != snapped_dest_coords:
                if not flat_coords or flat_coords[-1] != dest_coords:
                    flat_coords.append(dest_coords)
            geometry = flat_coords
            geometry_segments = [flat_coords] if flat_coords else []

        # 10. Assemble Waypoint Sequence and Cumulative Distances
        waypoints: List[RouteWaypoint] = []
        cumulative_nm = 0.0

        # Origin port connector waypoint
        waypoints.append(
            RouteWaypoint(
                node_id=orig_port.port_id if orig_port else "ORIGIN",
                name=orig_port.name if orig_port else "Origin Port",
                longitude=orig_coords[0],
                latitude=orig_coords[1],
                waypoint_type="port_connector_approximation" if orig_snap_dist > 0.0 else "fairway_gate",
                cumulative_distance_nm=0.0,
            )
        )

        cumulative_nm += orig_snap_dist

        for i, nid in enumerate(path_result.node_path):
            coords = marnet.nodes_data[nid]
            if i > 0:
                prev_nid = path_result.node_path[i - 1]
                edge_data = marnet.graph[prev_nid][nid]
                leg_weight = edge_data.get("weight", 0.0)
                cumulative_nm += leg_weight

            edge_pass = None
            if i < len(path_result.node_path) - 1:
                next_nid = path_result.node_path[i + 1]
                edge_pass = marnet.graph[nid][next_nid].get("pass_id")

            if edge_pass:
                w_type = "chokepoint"
            elif nid in (orig_node, dest_node):
                w_type = "fairway_gate"
            else:
                w_type = "sea_lane"

            waypoints.append(
                RouteWaypoint(
                    node_id=nid,
                    name=f"MARNET {nid}",
                    longitude=coords[0],
                    latitude=coords[1],
                    waypoint_type=w_type,
                    cumulative_distance_nm=round(cumulative_nm, 3),
                )
            )

        # Destination port connector waypoint
        cumulative_nm += dest_snap_dist
        waypoints.append(
            RouteWaypoint(
                node_id=dest_port.port_id if dest_port else "DESTINATION",
                name=dest_port.name if dest_port else "Destination Port",
                longitude=dest_coords[0],
                latitude=dest_coords[1],
                waypoint_type="port_connector_approximation" if dest_snap_dist > 0.0 else "fairway_gate",
                cumulative_distance_nm=round(cumulative_nm, 3),
            )
        )

        # 11. Disaggregated Distance Accounting & RouteSummary
        total_nm = round(orig_snap_dist + path_result.graph_distance_nm + dest_snap_dist, 3)
        total_km = nm_to_km(total_nm)

        summary = RouteSummary(
            origin_id=orig_port.port_id if orig_port else None,
            origin_name=orig_port.name if orig_port else f"Coordinates {orig_coords}",
            origin_coordinates=orig_coords,
            destination_id=dest_port.port_id if dest_port else None,
            destination_name=dest_port.name if dest_port else f"Coordinates {dest_coords}",
            destination_coordinates=dest_coords,
            total_distance_nm=total_nm,
            total_distance_km=total_km,
            graph_distance_nm=path_result.graph_distance_nm,
            origin_snap_distance_nm=orig_snap_dist,
            destination_snap_distance_nm=dest_snap_dist,
            distance_nm=total_nm,
            distance_km=total_km,
            canals_transited=path_result.canals_transited,
            canal_policy_applied=canal_policy,
        )

        # 12. Snapping Details
        snapping_details = {
            "origin": PortSnappingDetail(
                port_id=orig_port.port_id if orig_port else None,
                input_coordinates=orig_coords,
                snapped_node_id=orig_node,
                snapped_node_coordinates=snapped_orig_coords,
                snap_distance_nm=orig_snap_dist,
                coordinate_confidence=orig_port.coordinate_type if orig_port else "unverified_marine_access_point",
                connector_type="geometric_straight_line_approximation",
            ),
            "destination": PortSnappingDetail(
                port_id=dest_port.port_id if dest_port else None,
                input_coordinates=dest_coords,
                snapped_node_id=dest_node,
                snapped_node_coordinates=snapped_dest_coords,
                snap_distance_nm=dest_snap_dist,
                coordinate_confidence=dest_port.coordinate_type if dest_port else "unverified_marine_access_point",
                connector_type="geometric_straight_line_approximation",
            ),
        }

        # 13. Limitations
        limitations = self._get_marnet_limitations()
        if orig_port and orig_port.access_limitations:
            limitations.append(f"Origin access ({orig_port.port_id}): {orig_port.access_limitations}")
        if dest_port and dest_port.access_limitations:
            limitations.append(f"Destination access ({dest_port.port_id}): {dest_port.access_limitations}")

        return RoutingResult(
            route_id=route_id,
            status=RoutingStatus.CALCULATED,
            route_method="marnet_50km_generalized_graph",
            routing_mode="marnet",
            summary=summary,
            geometry=geometry,
            geometry_segments=geometry_segments,
            waypoints=waypoints,
            snapping_details=snapping_details,
            limitations=limitations,
            data_provenance=self._get_marnet_provenance(),
        )
