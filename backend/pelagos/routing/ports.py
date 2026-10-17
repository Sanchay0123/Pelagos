"""Pelagos Maritime Routing - Port Reference Fixture and Resolution.

Provides traceable port lookups from verified hydrographic fixtures,
resolving UN/LOCODE identifiers to documented coordinates without fabrication.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from pelagos.routing.geometry import (
    geodesic_distance_nm,
    validate_coordinates,
)
from pelagos.routing.schemas import (
    PortIdentifierOrCoords,
    PortRecord,
)

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ports.json"


class PortRepository:
    """In-memory repository of traceable ports loaded from local fixtures."""

    def __init__(self, fixture_path: Optional[Path] = None) -> None:
        self.fixture_path = fixture_path or _FIXTURE_PATH
        self._ports_by_id: Dict[str, PortRecord] = {}
        self._load_ports()

    def _load_ports(self) -> None:
        """Load ports deterministically from JSON fixture."""
        if not self.fixture_path.exists():
            raise FileNotFoundError(f"Port fixture not found at {self.fixture_path}")

        with open(self.fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        ports_list = data.get("ports", [])
        for item in ports_list:
            record = PortRecord(**item)
            key = record.port_id.upper().strip()
            self._ports_by_id[key] = record

    def get_port(self, identifier: str) -> Optional[PortRecord]:
        """Look up a port by internal identifier or UN/LOCODE code.

        Args:
            identifier: e.g. 'USMSY', 'JPYOK', 'USSEA'.

        Returns:
            Matching PortRecord or None.
        """
        if not identifier or not isinstance(identifier, str):
            return None
        return self._ports_by_id.get(identifier.upper().strip())

    def list_ports(self) -> List[PortRecord]:
        """Return a list of all traceable ports registered in the fixture."""
        return list(self._ports_by_id.values())

    def find_nearest_port(
        self,
        longitude: float,
        latitude: float,
        max_distance_nm: float = 150.0,
    ) -> Optional[Tuple[PortRecord, float]]:
        """Find the closest registered port to given WGS 84 coordinates.

        Args:
            longitude: Longitude in degrees.
            latitude: Latitude in degrees.
            max_distance_nm: Maximum search radius in nautical miles.

        Returns:
            Tuple of (PortRecord, distance_nm) if within radius, else None.
        """
        valid, err = validate_coordinates(longitude, latitude)
        if not valid:
            return None

        best_port: Optional[PortRecord] = None
        best_dist = float("inf")

        for port in self._ports_by_id.values():
            dist = geodesic_distance_nm(longitude, latitude, port.coordinates[0], port.coordinates[1])
            if dist < best_dist and dist <= max_distance_nm:
                best_dist = dist
                best_port = port

        if best_port is not None:
            return best_port, round(best_dist, 3)
        return None

    def resolve_endpoint(
        self,
        endpoint: Union[PortIdentifierOrCoords, str],
    ) -> Tuple[Optional[PortRecord], Optional[List[float]], Optional[str]]:
        """Resolve a routing request endpoint to a PortRecord or valid coordinates.

        Returns:
            Tuple of (port_record, coordinates [lon, lat], error_message).
        """
        # Case 1: Plain string identifier
        if isinstance(endpoint, str):
            port = self.get_port(endpoint)
            if port is None:
                return None, None, f"Unknown or unsupported port identifier: '{endpoint}'"
            return port, port.coordinates, None

        # Case 2: PortIdentifierOrCoords model
        if isinstance(endpoint, PortIdentifierOrCoords):
            # Check UN/LOCODE or port_id first
            code = endpoint.unlocode or endpoint.port_id
            if code:
                port = self.get_port(code)
                if port is not None:
                    return port, port.coordinates, None
                return None, None, f"Unknown or unsupported port identifier: '{code}'"

            # Check explicit coordinates
            if endpoint.coordinates is not None:
                lon, lat = endpoint.coordinates[0], endpoint.coordinates[1]
                valid, err = validate_coordinates(lon, lat)
                if not valid:
                    return None, None, f"Invalid endpoint coordinates: {err}"
                # Attempt to snap to known port if close
                nearest = self.find_nearest_port(lon, lat, max_distance_nm=50.0)
                if nearest is not None:
                    return nearest[0], [lon, lat], None
                return None, [lon, lat], None

            return None, None, "Endpoint provided neither valid port identifier nor coordinates"

        return None, None, f"Invalid endpoint type: {type(endpoint).__name__}"
