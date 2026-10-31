"""Pelagos Maritime Routing - Port Reference Fixture and Resolution.

Provides traceable port lookups from verified hydrographic fixtures,
resolving UN/LOCODE identifiers to documented coordinates without fabrication.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
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
_CANONICAL_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "data" / "genuine_ports_directory.json"


def normalize_port_string(text: str) -> str:
    """Safely normalize a port identifier, name, or alias string.

    Converts to lowercase, collapses all leading, trailing, and repeated whitespace,
    and replaces punctuation with spaces to enable controlled token matching.
    """
    if not text or not isinstance(text, str):
        return ""
    # Replace punctuation characters with space
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    # Collapse multiple whitespaces
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_name_variants(name: str) -> List[str]:
    """Extract natural name variants from a port name.

    e.g. 'Mumbai (Bombay)' -> ['Mumbai (Bombay)', 'Mumbai', 'Bombay']
         'Portland (OR)' -> ['Portland (OR)', 'Portland']
         'Nhava Sheva (JNPT)' -> ['Nhava Sheva (JNPT)', 'Nhava Sheva', 'JNPT']
    """
    if not name:
        return []
    variants = [name]
    m = re.match(r"^([^(]+)\s*\(([^)]+)\)$", name.strip())
    if m:
        main_part = m.group(1).strip()
        paren_part = m.group(2).strip()
        if main_part:
            variants.append(main_part)
        if paren_part and len(paren_part) > 2:
            variants.append(paren_part)
    return variants


def extract_alias_variants(alias: str) -> List[str]:
    """Extract prefix/suffix variants from an alias.

    e.g. 'Port of Portland' -> ['Port of Portland', 'Portland']
         'Nhava Sheva Port' -> ['Nhava Sheva Port', 'Nhava Sheva']
         'Mumbai Port Trust' -> ['Mumbai Port Trust', 'Mumbai']
         'Visakhapatnam Port Trust' -> ['Visakhapatnam Port Trust', 'Visakhapatnam']
    """
    if not alias:
        return []
    variants = [alias]
    norm = normalize_port_string(alias)
    # Strip common port prefixes/suffixes (ensuring remaining root has >= 3 chars)
    if norm.startswith("port of "):
        sub = norm[8:].strip()
        if len(sub) >= 3:
            variants.append(sub)
    if norm.endswith(" port trust"):
        sub = norm[:-11].strip()
        if len(sub) >= 3:
            variants.append(sub)
    elif norm.endswith(" port authority"):
        sub = norm[:-15].strip()
        if len(sub) >= 3:
            variants.append(sub)
    elif norm.endswith(" port"):
        sub = norm[:-5].strip()
        if len(sub) >= 3:
            variants.append(sub)
    return variants


class PortRepository:
    """Repository of traceable ports maintaining baseline fixtures and canonical registry."""

    def __init__(self, fixture_path: Optional[Path] = None) -> None:
        self.fixture_path = fixture_path or _FIXTURE_PATH
        self._ports_by_id: Dict[str, PortRecord] = {}
        self._canonical_ports_by_id: Dict[str, PortRecord] = {}
        self._canonical_ports_by_alias: Dict[str, str] = {}
        self._normalized_alias_index: Dict[str, str] = {}
        self._load_baseline_fixture()
        self._load_canonical_registry()
        self._build_normalized_index()

    def _load_baseline_fixture(self) -> None:
        """Load deterministic baseline ports fixture."""
        if not self.fixture_path.exists():
            raise FileNotFoundError(f"Port fixture not found at {self.fixture_path}")

        with open(self.fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        ports_list = data.get("ports", [])
        for item in ports_list:
            record = PortRecord(**item)
            key = record.port_id.upper().strip()
            self._ports_by_id[key] = record

    def _load_canonical_registry(self) -> None:
        """Load expanded canonical port registry from database or canonical JSON fixture."""
        # Seed from baseline ports first
        for key, p in self._ports_by_id.items():
            self._canonical_ports_by_id[key] = p

        # Attempt database load
        db_loaded = False
        try:
            from pelagos.database import SessionLocal
            from pelagos.models import Port, PortAlias

            db = SessionLocal()
            db_ports = db.query(Port).all()
            if db_ports:
                for p in db_ports:
                    record = PortRecord(
                        port_id=p.port_id,
                        source_id=p.source_id,
                        name=p.name,
                        country=p.country,
                        country_code=p.country_code,
                        coordinates=[p.longitude, p.latitude],
                        crs=p.crs,
                        coordinate_type=p.coordinate_type,
                        source_url=p.source_url or "",
                        provenance=p.provenance or "",
                        license=p.license or "",
                        access_limitations=p.access_limitations or "",
                        nearest_graph_node=p.nearest_graph_node,
                        port_region=p.port_region,
                        subdivision=p.subdivision,
                    )
                    self._canonical_ports_by_id[p.port_id.upper().strip()] = record
                    self._canonical_ports_by_id[p.unlocode.upper().strip()] = record

                aliases = db.query(PortAlias).all()
                for a in aliases:
                    self._canonical_ports_by_alias[a.alias_name.upper().strip()] = a.port_unlocode.upper().strip()

                db_loaded = True
            db.close()
        except Exception:
            db_loaded = False

        if not db_loaded and _CANONICAL_FIXTURE_PATH.is_file():
            # Load from canonical JSON fixture
            with open(_CANONICAL_FIXTURE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

            for item in data.get("ports", []):
                coords = item.get("coordinates")
                if not coords and "longitude" in item and "latitude" in item:
                    coords = [float(item["longitude"]), float(item["latitude"])]
                clean_item = dict(item)
                clean_item["coordinates"] = coords
                clean_item["port_id"] = clean_item.get("port_id") or clean_item.get("unlocode")

                record = PortRecord(**clean_item)
                key = record.port_id.upper().strip()
                self._canonical_ports_by_id[key] = record
                if "unlocode" in item and item["unlocode"]:
                    self._canonical_ports_by_id[item["unlocode"].upper().strip()] = record

                for a in item.get("aliases", []):
                    aname = a.get("alias_name")
                    if aname:
                        self._canonical_ports_by_alias[aname.upper().strip()] = key

    def _build_normalized_index(self) -> None:
        """Construct safe normalized alias and name variant index.

        Strictly prevents ambiguous fuzzy matching: if any candidate token
        maps to more than one distinct port, it is excluded from the index.
        """
        candidates: Dict[str, set] = {}

        def _add_candidate(key: str, port_id: str) -> None:
            norm = normalize_port_string(key)
            if norm:
                candidates.setdefault(norm, set()).add(port_id)

        # 1. Index baseline and canonical ports
        pool = (
            self._canonical_ports_by_id.values()
            if self._canonical_ports_by_id
            else self._ports_by_id.values()
        )
        for port in pool:
            pid = port.port_id
            _add_candidate(pid, pid)
            if hasattr(port, "unlocode") and port.unlocode:
                _add_candidate(port.unlocode, pid)
            for nvar in extract_name_variants(port.name):
                _add_candidate(nvar, pid)
                for avar in extract_alias_variants(nvar):
                    _add_candidate(avar, pid)

        # 2. Index explicit aliases
        for alias, target_id in self._canonical_ports_by_alias.items():
            _add_candidate(alias, target_id)
            for avar in extract_alias_variants(alias):
                _add_candidate(avar, target_id)

        # 3. Only keep keys that unambiguously map to exactly one port_id
        self._normalized_alias_index = {
            norm_key: next(iter(pids))
            for norm_key, pids in candidates.items()
            if len(pids) == 1
        }

    def get_port(self, identifier: str) -> Optional[PortRecord]:
        """Look up a port by internal identifier, UN/LOCODE, name, or alias.

        Checks baseline fixture first, then canonical registry, alias index,
        and safe normalized index (case, spacing, punctuation, variants).
        """
        if not identifier or not isinstance(identifier, str):
            return None
        clean = identifier.upper().strip()
        # 1. Direct baseline fixture lookup
        if clean in self._ports_by_id:
            return self._ports_by_id[clean]
        # 2. Canonical expanded registry lookup
        if clean in self._canonical_ports_by_id:
            return self._canonical_ports_by_id[clean]
        # 3. Canonical alias index lookup
        if clean in self._canonical_ports_by_alias:
            target_id = self._canonical_ports_by_alias[clean]
            return self._canonical_ports_by_id.get(target_id) or self._ports_by_id.get(target_id)
        # 4. Search by port name
        for port in self._canonical_ports_by_id.values():
            if port.name.upper().strip() == clean:
                return port
        for port in self._ports_by_id.values():
            if port.name.upper().strip() == clean:
                return port
        # 5. Normalized index lookup (handles case, repeated spacing, punctuation, variants)
        norm_key = normalize_port_string(identifier)
        if norm_key and norm_key in self._normalized_alias_index:
            target_id = self._normalized_alias_index[norm_key]
            return self._canonical_ports_by_id.get(target_id) or self._ports_by_id.get(target_id)
        return None

    def list_ports(
        self,
        include_canonical: bool = False,
        db: Optional[Any] = None,
    ) -> List[PortRecord]:
        """Return registered ports.
        
        Args:
            include_canonical: If False, returns the baseline fixture ports (8 ports).
                               If True, returns all canonical ports (including Indian ports).
            db: Optional active DB session for fresh canonical query.
        """
        if not include_canonical:
            seen = set()
            result = []
            for p in self._ports_by_id.values():
                if p.port_id not in seen:
                    seen.add(p.port_id)
                    result.append(p)
            return result

        seen = set()
        result = []
        for p in self._canonical_ports_by_id.values():
            if p.port_id not in seen:
                seen.add(p.port_id)
                result.append(p)
        return result

    def list_all_ports(self, db: Optional[Any] = None) -> List[PortRecord]:
        """Return all deduplicated ports from the canonical multi-source registry."""
        return self.list_ports(include_canonical=True, db=db)

    def find_nearest_port(
        self,
        longitude: float,
        latitude: float,
        max_distance_nm: float = 150.0,
    ) -> Optional[Tuple[PortRecord, float]]:
        """Find the closest registered port to given WGS 84 coordinates.

        Considers both canonical registry ports and baseline prototype ports.

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

        ports_pool = (
            self._canonical_ports_by_id.values()
            if self._canonical_ports_by_id
            else self._ports_by_id.values()
        )
        seen_ids = set()
        for port in ports_pool:
            if port.port_id in seen_ids:
                continue
            seen_ids.add(port.port_id)
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

        Supports safe normalization of string identifiers and aliases:
        - Case-insensitive matching
        - Collapsing leading/trailing and repeated whitespace
        - Common punctuation variations (hyphens, commas, periods)
        - Controlled alias and name component resolution

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
