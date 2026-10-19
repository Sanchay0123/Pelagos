"""Pelagos Commercial Maritime Freight Corridor Resolver.

Resolves maritime route endpoints (ports or hydrographic coordinates) to monitored
commercial freight trade corridors defined in the Pelagos database.

Enforces strict commercial integrity:
- Corridors are resolved ONLY through explicit regional and port mappings.
- Never infers commercial corridors from geometric path or distance alone.
- Strictly bounds agricultural grain benchmarks (Panamax/Supramax) to their
  authoritative scope and rejects general cargo, container, or tanker inferences.
- Explicitly returns UNMONITORED status for unsupported trade lanes (e.g. Rotterdam -> Singapore).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from pelagos.routing.geometry import validate_coordinates
from pelagos.routing.ports import PortRepository
from pelagos.routing.schemas import PortIdentifierOrCoords, PortRecord

logger = logging.getLogger(__name__)


class CorridorStatus(str, Enum):
    """Status indicating whether a trade lane is monitored in Pelagos."""

    MONITORED = "monitored"
    UNMONITORED = "unmonitored"


# Authoritative mapping of known ports in the Pelagos fixture to commercial trade regions.
# Traces to REQ-FR-DII-005, REQ-FR-FRF-001, and DATA-001 Section 5.2.
PORT_REGION_MAP: Dict[str, str] = {
    "USSEA": "US Pacific Northwest",
    "USMSY": "US Gulf Coast",
    "JPYOK": "Japan",
    "NLRTM": "Northern Europe",
    "SGSIN": "Southeast Asia",
    "PAPAC": "Central America",
    "EGPSD": "Mediterranean",
    "CASP_TEST": "Caspian Sea",
}

# Monitored freight corridor registry definitions matching database records
REGISTERED_CORRIDORS: Dict[Tuple[str, str], Dict[str, Any]] = {
    ("US Pacific Northwest", "Japan"): {
        "route_code": "PNW-JPN-BULK",
        "route_name": "U.S. Pacific Northwest to Japan Bulk Grain Ocean Corridor",
        "origin_region": "U.S. Pacific Northwest",
        "destination_region": "Japan",
        "commodity": "Agricultural Bulk Grain",
        "vessel_class": "Panamax / Supramax",
        "unit": "USD/MT",
        "currency": "USD",
        "benchmark_distance_nm": 4520.0,
        "description": "Benchmark dry bulk grain export rate from U.S. Pacific Northwest ports to Japan.",
    },
    ("US Gulf Coast", "Japan"): {
        "route_code": "USG-JPN-BULK",
        "route_name": "U.S. Gulf to Japan Bulk Grain Ocean Corridor",
        "origin_region": "U.S. Gulf Coast",
        "destination_region": "Japan",
        "commodity": "Agricultural Bulk Grain",
        "vessel_class": "Panamax / Supramax",
        "unit": "USD/MT",
        "currency": "USD",
        "benchmark_distance_nm": 9150.0,
        "description": "Benchmark dry bulk grain export rate from U.S. Gulf ports to Japan via Panama Canal.",
    },
}


@dataclass(frozen=True)
class CorridorResolution:
    """Deterministic result of resolving route endpoints to a freight corridor."""

    status: CorridorStatus
    route_code: Optional[str] = None
    route_name: Optional[str] = None
    origin_region: Optional[str] = None
    destination_region: Optional[str] = None
    origin_port_id: Optional[str] = None
    origin_port_name: Optional[str] = None
    destination_port_id: Optional[str] = None
    destination_port_name: Optional[str] = None
    commodity: Optional[str] = None
    vessel_class: Optional[str] = None
    unit: Optional[str] = None
    currency: Optional[str] = None
    benchmark_distance_nm: Optional[float] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert resolution result to a JSON-compatible dictionary."""
        return {
            "status": self.status.value,
            "route_code": self.route_code,
            "route_name": self.route_name,
            "origin_region": self.origin_region,
            "destination_region": self.destination_region,
            "origin_port_id": self.origin_port_id,
            "origin_port_name": self.origin_port_name,
            "destination_port_id": self.destination_port_id,
            "destination_port_name": self.destination_port_name,
            "commodity": self.commodity,
            "vessel_class": self.vessel_class,
            "unit": self.unit,
            "currency": self.currency,
            "benchmark_distance_nm": self.benchmark_distance_nm,
            "reason": self.reason,
        }


def resolve_port_region(port: PortRecord) -> Optional[str]:
    """Resolve a PortRecord to its commercial trade region.

    Uses explicit PORT_REGION_MAP lookup; does not fabricate or guess regions.
    """
    if not port or not port.port_id:
        return None
    return PORT_REGION_MAP.get(port.port_id.upper().strip())


def resolve_endpoint_to_port(
    endpoint: Union[str, PortIdentifierOrCoords, PortRecord, List[float], Tuple[float, float], Dict[str, Any], None],
    port_repo: PortRepository,
    max_snap_distance_nm: float = 150.0,
) -> Tuple[Optional[PortRecord], Optional[List[float]], Optional[str]]:
    """Resolve any supported endpoint representation to a PortRecord and coordinates.

    Returns:
        Tuple of (PortRecord or None, [lon, lat] or None, error_message or None).
    """
    if endpoint is None:
        return None, None, "Endpoint is None"

    # Direct PortRecord instance
    if isinstance(endpoint, PortRecord):
        return endpoint, endpoint.coordinates, None

    # Plain string identifier (e.g. "USSEA" or "Seattle")
    if isinstance(endpoint, str):
        cleaned = endpoint.strip().upper()
        # Check if coordinates string "lon,lat"
        if "," in cleaned:
            parts = [p.strip() for p in cleaned.split(",")]
            if len(parts) == 2:
                try:
                    lon, lat = float(parts[0]), float(parts[1])
                    valid, err = validate_coordinates(lon, lat)
                    if not valid:
                        return None, None, f"Invalid coordinate format '{endpoint}': {err}"
                    nearest = port_repo.find_nearest_port(lon, lat, max_distance_nm=max_snap_distance_nm)
                    if nearest is not None:
                        return nearest[0], [lon, lat], None
                    return None, [lon, lat], None
                except ValueError:
                    pass

        port = port_repo.get_port(cleaned)
        if port is not None:
            return port, port.coordinates, None
        return None, None, f"Unknown port identifier: '{endpoint}'"

    # Coordinate list or tuple [lon, lat]
    if isinstance(endpoint, (list, tuple)):
        if len(endpoint) == 2:
            try:
                lon, lat = float(endpoint[0]), float(endpoint[1])
                valid, err = validate_coordinates(lon, lat)
                if not valid:
                    return None, None, f"Invalid coordinate values [{lon}, {lat}]: {err}"
                nearest = port_repo.find_nearest_port(lon, lat, max_distance_nm=max_snap_distance_nm)
                if nearest is not None:
                    return nearest[0], [lon, lat], None
                return None, [lon, lat], None
            except (ValueError, TypeError) as exc:
                return None, None, f"Invalid coordinate list: {exc}"

    # Dict representation
    if isinstance(endpoint, dict):
        # Coordinates field
        coords = endpoint.get("coordinates")
        if coords and len(coords) == 2:
            return resolve_endpoint_to_port(coords, port_repo, max_snap_distance_nm=max_snap_distance_nm)
        # Separate lon/lat fields
        lon = endpoint.get("longitude") or endpoint.get("lon")
        lat = endpoint.get("latitude") or endpoint.get("lat")
        if lon is not None and lat is not None:
            return resolve_endpoint_to_port([lon, lat], port_repo, max_snap_distance_nm=max_snap_distance_nm)
        # Port id or unlocode
        pid = endpoint.get("port_id") or endpoint.get("unlocode") or endpoint.get("code") or endpoint.get("name")
        if pid:
            return resolve_endpoint_to_port(str(pid), port_repo, max_snap_distance_nm=max_snap_distance_nm)

    # Pydantic PortIdentifierOrCoords
    if isinstance(endpoint, PortIdentifierOrCoords):
        return port_repo.resolve_endpoint(endpoint)

    return None, None, f"Unsupported endpoint type: {type(endpoint).__name__}"


def resolve_freight_corridor(
    origin: Union[str, PortIdentifierOrCoords, PortRecord, List[float], Tuple[float, float], Dict[str, Any], None],
    destination: Union[str, PortIdentifierOrCoords, PortRecord, List[float], Tuple[float, float], Dict[str, Any], None],
    port_repo: Optional[PortRepository] = None,
    max_snap_distance_nm: float = 150.0,
) -> CorridorResolution:
    """Resolve origin and destination endpoints to a commercial freight corridor.

    Args:
        origin: Origin port identifier, coordinates, or PortRecord.
        destination: Destination port identifier, coordinates, or PortRecord.
        port_repo: Optional PortRepository instance (instantiated if omitted).
        max_snap_distance_nm: Snapping radius in NM to match coordinates to known ports.

    Returns:
        CorridorResolution with status MONITORED or UNMONITORED.
    """
    repo = port_repo or PortRepository()

    orig_port, orig_coords, orig_err = resolve_endpoint_to_port(
        origin, repo, max_snap_distance_nm=max_snap_distance_nm
    )
    dest_port, dest_coords, dest_err = resolve_endpoint_to_port(
        destination, repo, max_snap_distance_nm=max_snap_distance_nm
    )

    orig_port_id = orig_port.port_id if orig_port else None
    orig_port_name = orig_port.name if orig_port else None
    dest_port_id = dest_port.port_id if dest_port else None
    dest_port_name = dest_port.name if dest_port else None

    # Handle unresolvable endpoints
    if orig_port is None and orig_coords is None:
        return CorridorResolution(
            status=CorridorStatus.UNMONITORED,
            reason=f"Origin could not be resolved to a recognized maritime port or coordinates ({orig_err or 'unknown'}).",
        )
    if dest_port is None and dest_coords is None:
        return CorridorResolution(
            status=CorridorStatus.UNMONITORED,
            reason=f"Destination could not be resolved to a recognized maritime port or coordinates ({dest_err or 'unknown'}).",
        )

    orig_region = resolve_port_region(orig_port) if orig_port else None
    dest_region = resolve_port_region(dest_port) if dest_port else None

    # Case: Coordinates did not resolve to a registered port with known commercial region
    if not orig_region or not dest_region:
        orig_desc = orig_port_name or (f"Coordinates [{orig_coords[0]:.2f}, {orig_coords[1]:.2f}]" if orig_coords else "Unknown")
        dest_desc = dest_port_name or (f"Coordinates [{dest_coords[0]:.2f}, {dest_coords[1]:.2f}]" if dest_coords else "Unknown")
        return CorridorResolution(
            status=CorridorStatus.UNMONITORED,
            origin_port_id=orig_port_id,
            origin_port_name=orig_port_name,
            origin_region=orig_region,
            destination_port_id=dest_port_id,
            destination_port_name=dest_port_name,
            destination_region=dest_region,
            reason=(
                f"Route {orig_desc} -> {dest_desc} does not map to any monitored commercial freight region. "
                "Pelagos freight monitoring is currently limited to U.S.-Japan dry bulk grain export benchmarks (PNW-JPN-BULK, USG-JPN-BULK)."
            ),
        )

    # Check for direct corridor match in the registered corridors table
    key = (orig_region, dest_region)
    if key in REGISTERED_CORRIDORS:
        spec = REGISTERED_CORRIDORS[key]
        return CorridorResolution(
            status=CorridorStatus.MONITORED,
            route_code=spec["route_code"],
            route_name=spec["route_name"],
            origin_region=spec["origin_region"],
            destination_region=spec["destination_region"],
            origin_port_id=orig_port_id,
            origin_port_name=orig_port_name,
            destination_port_id=dest_port_id,
            destination_port_name=dest_port_name,
            commodity=spec["commodity"],
            vessel_class=spec["vessel_class"],
            unit=spec["unit"],
            currency=spec["currency"],
            benchmark_distance_nm=spec["benchmark_distance_nm"],
            reason=spec["description"],
        )

    # Check specifically for reverse direction (e.g. Japan -> PNW or Japan -> USG)
    reverse_key = (dest_region, orig_region)
    if reverse_key in REGISTERED_CORRIDORS:
        fwd_spec = REGISTERED_CORRIDORS[reverse_key]
        return CorridorResolution(
            status=CorridorStatus.UNMONITORED,
            origin_port_id=orig_port_id,
            origin_port_name=orig_port_name,
            origin_region=orig_region,
            destination_port_id=dest_port_id,
            destination_port_name=dest_port_name,
            destination_region=dest_region,
            reason=(
                f"Reverse corridor {orig_port_name} ({orig_region}) -> {dest_port_name} ({dest_region}) is not monitored. "
                f"The {fwd_spec['route_code']} benchmark specifically applies to outbound U.S. agricultural grain exports, "
                "not transpacific inbound backhaul."
            ),
        )

    # Unsupported commercial corridor (e.g. Rotterdam -> Singapore, Northern Europe -> Southeast Asia)
    return CorridorResolution(
        status=CorridorStatus.UNMONITORED,
        origin_port_id=orig_port_id,
        origin_port_name=orig_port_name,
        origin_region=orig_region,
        destination_port_id=dest_port_id,
        destination_port_name=dest_port_name,
        destination_region=dest_region,
        reason=(
            f"Trade lane {orig_port_name} ({orig_region}) -> {dest_port_name} ({dest_region}) is unmonitored. "
            "Pelagos freight observations and forecasting models are currently limited to U.S.-Japan bulk grain export corridors (PNW-JPN-BULK, USG-JPN-BULK)."
        ),
    )
