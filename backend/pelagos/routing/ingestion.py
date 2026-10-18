"""Pelagos Maritime Routing - MARNET Dataset Ingestion and Graph Builder.

Provides reproducible, non-interactive acquisition, SHA-256 validation,
pure-Python GeoPackage WKB parsing, self-loop exclusion, parallel-edge
collapse, and antimeridian bridging for the Eurostat SeaRoute network.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import struct
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.request

import networkx as nx
import pyproj

logger = logging.getLogger(__name__)

# Pinned upstream source constants (D9.3 verified)
MARNET_REPO_URL = "https://github.com/eurostat/searoute"
MARNET_COMMIT_SHA = "88a2e568772a24687d60f5885c4943fcf4d7541d"
MARNET_GPKG_URL = (
    f"https://raw.githubusercontent.com/eurostat/searoute/{MARNET_COMMIT_SHA}/"
    "modules/jar/marnet/marnet_plus_50km.gpkg"
)
PASS_GPKG_URL = (
    f"https://raw.githubusercontent.com/eurostat/searoute/{MARNET_COMMIT_SHA}/"
    "modules/marnet/src/main/resources/pass.gpkg"
)

EXPECTED_MARNET_SHA256 = "44a309ffa0fbdc02e00616ed6a9e20ad92c773994b8f4502995cee9ffdf96acf"
EXPECTED_PASS_SHA256 = "3d164406871113efa3af6d38a84a661d9271d893df0d430c4495335c741a5936"

EXPECTED_MARNET_SIZE = 1552384
EXPECTED_PASS_SIZE = 98304

# Regression expectations for commit 88a2e568 (D9.3 audit)
EXPECTED_FEATURES_COUNT = 15498
EXPECTED_PASSAGES_COUNT = 12
EXPECTED_NODE_COUNT = 7390
EXPECTED_EDGE_COUNT_EXCLUDING_LOOPS = 15423
EXPECTED_SELF_LOOPS = 20
EXPECTED_PARALLEL_PAIRS = 67
EXPECTED_ANTIMERIDIAN_BRIDGES = 12

TRANSFORMATION_VERSION = "1.0.0"
DEFAULT_CACHE_DIR = Path(".cache/pelagos/routing")

LICENSING_NOTICE = {
    "repository_declared_license": "European Union Public Licence (EUPL-1.2)",
    "rights_status": "unresolved_prerequisite",
    "disclaimer": (
        "Upstream data rights for the composite ORNL shipping lane network and Eurostat AIS density "
        "additions remain legally unresolved. This dataset is retrieved for local development and "
        "analytical use only and is not authorized or approved for repository distribution or commercial "
        "redistribution without formal legal clearance."
    ),
}


class MarnetIngestionError(Exception):
    """Base exception for all MARNET ingestion failures."""


class ChecksumMismatchError(MarnetIngestionError):
    """Raised when an acquired file does not match its expected SHA-256 hash."""


class GeoPackageSchemaError(MarnetIngestionError):
    """Raised when a GeoPackage is missing required tables, columns, or CRS metadata."""


class MalformedGeometryError(MarnetIngestionError):
    """Raised when a binary geometry blob cannot be decoded or violates bounds."""


class UnsupportedGeometryTypeError(MarnetIngestionError):
    """Raised when encountering a geometry type other than LineString."""


class PassAttributeConflictError(MarnetIngestionError):
    """Raised when parallel edges between the same endpoints have conflicting chokepoint tags."""


def compute_sha256(file_path: Path) -> str:
    """Compute the SHA-256 hex digest of a local file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


class MarnetAcquisition:
    """Non-interactive, fail-closed retriever for upstream MARNET assets."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)

    def download_file(
        self,
        url: str,
        target_name: str,
        expected_sha256: str,
        expected_size: Optional[int] = None,
        force: bool = False,
    ) -> Path:
        """Download a file with streaming SHA-256 validation.

        Fails closed on hash mismatch, network failure, or corrupt download.
        Never silently overwrites an existing validated file unless force=True.
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target_path = self.cache_dir / target_name

        if target_path.exists() and not force:
            existing_hash = compute_sha256(target_path)
            if existing_hash == expected_sha256:
                logger.info("Cache hit: %s is valid (SHA-256 matches)", target_name)
                return target_path
            else:
                logger.warning(
                    "Cached file %s has invalid checksum %s; re-downloading",
                    target_name,
                    existing_hash,
                )

        temp_path = self.cache_dir / f"{target_name}.tmp"
        logger.info("Downloading %s from %s ...", target_name, url)

        hasher = hashlib.sha256()
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Pelagos-Maritime-Routing/1.0"},
            )
            with urllib.request.urlopen(req, timeout=60.0) as resp, open(temp_path, "wb") as out_f:
                while chunk := resp.read(65536):
                    out_f.write(chunk)
                    hasher.update(chunk)
        except Exception as e:
            if temp_path.exists():
                temp_path.unlink()
            raise MarnetIngestionError(f"Failed to download {target_name} from {url}: {e}") from e

        computed_sha256 = hasher.hexdigest()
        if computed_sha256 != expected_sha256:
            if temp_path.exists():
                temp_path.unlink()
            raise ChecksumMismatchError(
                f"Checksum mismatch for {target_name}: "
                f"expected {expected_sha256}, got {computed_sha256}"
            )

        if expected_size is not None:
            actual_size = temp_path.stat().st_size
            if actual_size != expected_size:
                if temp_path.exists():
                    temp_path.unlink()
                raise MarnetIngestionError(
                    f"Size mismatch for {target_name}: expected {expected_size} bytes, got {actual_size}"
                )

        # Atomic replacement
        if target_path.exists():
            target_path.unlink()
        temp_path.rename(target_path)
        logger.info("Successfully acquired %s (SHA-256 verified)", target_name)
        return target_path

    def acquire_all(self, force: bool = False) -> Dict[str, Path]:
        """Acquire both marnet_plus_50km.gpkg and pass.gpkg."""
        marnet_path = self.download_file(
            url=MARNET_GPKG_URL,
            target_name="marnet_plus_50km.gpkg",
            expected_sha256=EXPECTED_MARNET_SHA256,
            expected_size=EXPECTED_MARNET_SIZE,
            force=force,
        )
        pass_path = self.download_file(
            url=PASS_GPKG_URL,
            target_name="pass.gpkg",
            expected_sha256=EXPECTED_PASS_SHA256,
            expected_size=EXPECTED_PASS_SIZE,
            force=force,
        )
        return {"marnet": marnet_path, "pass": pass_path}


class GeoPackageValidator:
    """Validates OGC GeoPackage structure, CRS, and required tables/columns."""

    @staticmethod
    def validate_marnet_structure(gpkg_path: Path | str) -> None:
        """Validate that the GeoPackage is valid SQLite and matches the MARNET schema."""
        gpkg_path = Path(gpkg_path)
        if not gpkg_path.exists():
            raise FileNotFoundError(f"GeoPackage file not found: {gpkg_path}")

        try:
            conn = sqlite3.connect(gpkg_path)
            cur = conn.cursor()

            # 1. Check SQLite integrity
            cur.execute("PRAGMA quick_check")
            check_result = cur.fetchone()
            if not check_result or check_result[0] != "ok":
                raise GeoPackageSchemaError(f"Corrupt SQLite database in {gpkg_path}: {check_result}")

            # 2. Check for required table 'type'
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='type'")
            if not cur.fetchone():
                raise GeoPackageSchemaError(f"Missing required table 'type' in {gpkg_path}")

            # 3. Check for required columns in 'type': fid, geometry, pass
            cur.execute("PRAGMA table_info(type)")
            columns = {row[1] for row in cur.fetchall()}
            required_cols = {"fid", "geometry", "pass"}
            missing_cols = required_cols - columns
            if missing_cols:
                raise GeoPackageSchemaError(
                    f"Table 'type' in {gpkg_path} is missing required columns: {missing_cols}"
                )

            # 4. Check gpkg_geometry_columns metadata table and column agreement
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gpkg_geometry_columns'")
            if not cur.fetchone():
                raise GeoPackageSchemaError(f"Missing metadata table 'gpkg_geometry_columns' in {gpkg_path}")

            cur.execute(
                "SELECT column_name, geometry_type_name, srs_id, z, m "
                "FROM gpkg_geometry_columns WHERE table_name='type'"
            )
            geom_col_row = cur.fetchone()
            if not geom_col_row:
                raise GeoPackageSchemaError("No geometry column registered in gpkg_geometry_columns for table 'type'")

            col_name, geom_type_name, srs_id, z, m = geom_col_row
            if col_name != "geometry":
                raise GeoPackageSchemaError(
                    f"Geometry column in metadata '{col_name}' does not match expected 'geometry'"
                )
            if geom_type_name.upper() != "LINESTRING":
                raise GeoPackageSchemaError(
                    f"Expected geometry type 'LINESTRING' in metadata, got '{geom_type_name}'"
                )
            if srs_id != 4326:
                raise GeoPackageSchemaError(f"Expected SRS 4326 in gpkg_geometry_columns, found {srs_id}")
            if z != 0:
                raise GeoPackageSchemaError(f"Unsupported 3D coordinate dimension (z={z}) in metadata")

            # 5. Check gpkg_contents metadata
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gpkg_contents'")
            if cur.fetchone():
                cur.execute("SELECT data_type, srs_id FROM gpkg_contents WHERE table_name='type'")
                contents_row = cur.fetchone()
                if contents_row:
                    data_type, contents_srs = contents_row
                    if data_type != "features":
                        raise GeoPackageSchemaError(f"Expected data_type 'features' in gpkg_contents, got '{data_type}'")
                    if contents_srs != 4326:
                        raise GeoPackageSchemaError(f"Expected SRS 4326 in gpkg_contents, found {contents_srs}")

            # 6. Check gpkg_spatial_ref_sys
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gpkg_spatial_ref_sys'")
            if cur.fetchone():
                cur.execute(
                    "SELECT organization, organization_coordsys_id "
                    "FROM gpkg_spatial_ref_sys WHERE srs_id=4326"
                )
                srs_row = cur.fetchone()
                if not srs_row:
                    raise GeoPackageSchemaError("SRS 4326 is not defined in gpkg_spatial_ref_sys")
                org, org_id = srs_row
                if org.upper() != "EPSG" or org_id != 4326:
                    raise GeoPackageSchemaError(
                        f"Expected EPSG:4326 in gpkg_spatial_ref_sys, found {org}:{org_id}"
                    )

            conn.close()
        except sqlite3.DatabaseError as e:
            raise GeoPackageSchemaError(f"Failed to read GeoPackage {gpkg_path}: {e}") from e

    @staticmethod
    def validate_pass_structure(gpkg_path: Path | str) -> None:
        """Validate that the pass GeoPackage matches the expected chokepoints schema."""
        gpkg_path = Path(gpkg_path)
        if not gpkg_path.exists():
            raise FileNotFoundError(f"Pass GeoPackage file not found: {gpkg_path}")

        try:
            conn = sqlite3.connect(gpkg_path)
            cur = conn.cursor()

            # 1. Integrity check
            cur.execute("PRAGMA quick_check")
            check_result = cur.fetchone()
            if not check_result or check_result[0] != "ok":
                raise GeoPackageSchemaError(f"Corrupt SQLite database in {gpkg_path}: {check_result}")

            # 2. Table check
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pass'")
            if not cur.fetchone():
                raise GeoPackageSchemaError(f"Missing required table 'pass' in {gpkg_path}")

            # 3. Columns check: fid, geometry, name, id
            cur.execute("PRAGMA table_info(pass)")
            columns = {row[1] for row in cur.fetchall()}
            required_cols = {"fid", "geometry", "name", "id"}
            missing_cols = required_cols - columns
            if missing_cols:
                raise GeoPackageSchemaError(
                    f"Table 'pass' in {gpkg_path} is missing required columns: {missing_cols}"
                )

            # 4. Geometry metadata check
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gpkg_geometry_columns'")
            if cur.fetchone():
                cur.execute(
                    "SELECT column_name, geometry_type_name, srs_id "
                    "FROM gpkg_geometry_columns WHERE table_name='pass'"
                )
                row = cur.fetchone()
                if row:
                    col_name, geom_type, srs_id = row
                    if col_name != "geometry":
                        raise GeoPackageSchemaError(f"Pass geometry column '{col_name}' does not match 'geometry'")
                    if srs_id != 4326:
                        raise GeoPackageSchemaError(f"Expected SRS 4326 for pass layer, found {srs_id}")

            conn.close()
        except sqlite3.DatabaseError as e:
            raise GeoPackageSchemaError(f"Failed to read GeoPackage {gpkg_path}: {e}") from e


class WkbGeometryParser:
    """Decodes OGC GeoPackage binary geometry headers and WKB LineStrings."""

    @staticmethod
    def parse_gpkg_linestring(blob: bytes, fid: Optional[int] = None) -> List[Tuple[float, float]]:
        """Parse an OGC GeoPackage binary header and unpack WKB LineString coordinates.

        Header Specification (OGC 12-128r17):
        - Byte 0-1: Magic 'GP' (0x47, 0x50)
        - Byte 2: Version (0x00)
        - Byte 3: Flags (bit 0: endianness, bits 1-3: envelope indicator, bit 4: empty flag)
        - Byte 4-7: SRS ID (4326)
        - Bytes 8 to (8 + envelope_size): Envelope bounds
        - Remaining bytes: Standard ISO/OGC WKB

        Returns list of (round(lon, 6), round(lat, 6)) coordinate pairs.
        Raises MalformedGeometryError or UnsupportedGeometryTypeError on invalid data.
        """
        if not isinstance(blob, (bytes, bytearray)):
            raise MalformedGeometryError(f"FID {fid}: Expected binary geometry BLOB, got {type(blob)}")

        if len(blob) < 8:
            raise MalformedGeometryError(f"FID {fid}: Geometry BLOB truncated (length {len(blob)} < 8)")

        # 1. Validate GeoPackage binary magic bytes
        if blob[0] != 0x47 or blob[1] != 0x50:
            raise MalformedGeometryError(
                f"FID {fid}: Invalid GeoPackage magic bytes (0x{blob[0]:02x}, 0x{blob[1]:02x})"
            )

        # Version byte
        if blob[2] != 0x00:
            raise MalformedGeometryError(f"FID {fid}: Unsupported GeoPackage binary header version {blob[2]}")

        # 2. Extract flags and CRS
        flags = blob[3]
        header_endian = "<" if (flags & 0x01) == 1 else ">"

        # CRS / SRS ID validation in header (bytes 4-7)
        header_srs_id = struct.unpack(f"{header_endian}i", blob[4:8])[0]
        if header_srs_id != 4326:
            raise MalformedGeometryError(
                f"FID {fid}: Expected CRS 4326 in geometry header, got {header_srs_id}"
            )

        # Empty geometry check (bit 4)
        if (flags >> 4) & 0x01 == 1:
            raise MalformedGeometryError(f"FID {fid}: Unexpected empty geometry in shipping network layer")

        # Envelope indicator from flags
        envelope_indicator = (flags >> 1) & 0x07
        env_sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
        if envelope_indicator not in env_sizes:
            raise MalformedGeometryError(
                f"FID {fid}: Unsupported envelope indicator {envelope_indicator}"
            )

        env_size = env_sizes[envelope_indicator]
        wkb_offset = 8 + env_size

        if len(blob) < wkb_offset + 9:
            raise MalformedGeometryError(
                f"FID {fid}: Blob too short for WKB header (length {len(blob)} < {wkb_offset + 9})"
            )

        # 3. Parse WKB
        wkb = blob[wkb_offset:]
        byte_order = wkb[0]
        if byte_order not in (0, 1):
            raise MalformedGeometryError(f"FID {fid}: Invalid WKB byte order {byte_order}")

        endian = "<" if byte_order == 1 else ">"
        geom_type = struct.unpack(f"{endian}I", wkb[1:5])[0]

        # Geometry type 2 is standard 2D LineString
        if geom_type != 2:
            raise UnsupportedGeometryTypeError(
                f"FID {fid}: Expected LineString geometry (type 2), got type {geom_type}"
            )

        num_points = struct.unpack(f"{endian}I", wkb[5:9])[0]
        if num_points < 2:
            raise MalformedGeometryError(
                f"FID {fid}: LineString requires at least 2 points, found {num_points}"
            )

        if num_points > 100000:
            raise MalformedGeometryError(
                f"FID {fid}: Point count {num_points} exceeds safety threshold (100,000)"
            )

        expected_wkb_len = 9 + (num_points * 16)
        if len(wkb) != expected_wkb_len:
            raise MalformedGeometryError(
                f"FID {fid}: WKB length {len(wkb)} does not match expected {expected_wkb_len} for {num_points} points"
            )

        points: List[Tuple[float, float]] = []
        offset = 9
        for pt_idx in range(num_points):
            x, y = struct.unpack(f"{endian}dd", wkb[offset : offset + 16])
            # Strict coordinate validation
            if math.isnan(x) or math.isnan(y) or math.isinf(x) or math.isinf(y):
                raise MalformedGeometryError(f"FID {fid}, point {pt_idx}: Coordinate contains NaN or Inf")

            # Boundary vertices in MARNET allow up to ~180.75 deg (FID 4312 intermediate vertex)
            if not (-181.0 <= x <= 181.0 and -90.0 <= y <= 90.0):
                raise MalformedGeometryError(
                    f"FID {fid}, point {pt_idx}: Coordinate [{x}, {y}] out of WGS 84 range"
                )

            points.append((round(x, 6), round(y, 6)))
            offset += 16

        return points


class MarnetGraphBuilder:
    """Deterministic maritime graph constructor from verified MARNET GeoPackage."""

    def __init__(self) -> None:
        self.geod = pyproj.Geod(ellps="WGS84")

    def calculate_linestring_length_nm(self, points: List[Tuple[float, float]]) -> float:
        """Calculate the cumulative WGS 84 geodesic distance along LineString vertices in NM."""
        total_meters = 0.0
        for i in range(len(points) - 1):
            p1, p2 = points[i], points[i + 1]
            _, _, d = self.geod.inv(p1[0], p1[1], p2[0], p2[1])
            total_meters += d
        return total_meters / 1852.0

    def build_from_geopackage(
        self,
        marnet_gpkg_path: Path | str,
        exclude_self_loops: bool = True,
    ) -> Dict[str, Any]:
        """Extract features, aggregate edges, add antimeridian bridges, and compile graph.

        Returns a dictionary containing graph data, node lookups, and audit metadata.
        """
        marnet_gpkg_path = Path(marnet_gpkg_path)
        GeoPackageValidator.validate_marnet_structure(marnet_gpkg_path)

        conn = sqlite3.connect(marnet_gpkg_path)
        cur = conn.cursor()
        cur.execute("SELECT fid, geometry, pass FROM type")
        rows = cur.fetchall()
        conn.close()

        total_features = len(rows)
        features_by_pair: Dict[Tuple[Tuple[float, float], Tuple[float, float]], List[Dict[str, Any]]] = (
            defaultdict(list)
        )
        self_loops: List[Dict[str, Any]] = []

        for fid, geom_blob, pass_val in rows:
            pts = WkbGeometryParser.parse_gpkg_linestring(geom_blob, fid=fid)
            u = pts[0]
            v = pts[-1]
            length_nm = self.calculate_linestring_length_nm(pts)

            feat = {
                "fid": fid,
                "u": u,
                "v": v,
                "pass": pass_val,
                "length_nm": round(length_nm, 4),
                "num_points": len(pts),
                "points": pts,
            }

            if u == v:
                self_loops.append(feat)
                if exclude_self_loops:
                    continue

            pair = (min(u, v), max(u, v))
            features_by_pair[pair].append(feat)

        # Collapse duplicate/parallel edges deterministically
        parallel_pairs = 0
        duplicate_edges_collapsed = 0
        G = nx.Graph()

        for pair, feat_list in features_by_pair.items():
            u, v = pair
            if len(feat_list) > 1:
                parallel_pairs += 1
                duplicate_edges_collapsed += len(feat_list) - 1

                # Check for conflicting chokepoint/pass attributes
                passes = {f["pass"] for f in feat_list if f["pass"]}
                if len(passes) > 1:
                    raise PassAttributeConflictError(
                        f"Parallel features between {u} and {v} have conflicting pass attributes: {passes}"
                    )

            # Select minimum geodesic length feature with fid tie-breaker for strict determinism
            best_feat = min(feat_list, key=lambda f: (f["length_nm"], f["fid"]))
            # Resolve pass tag deterministically (preserve passage tag if present)
            valid_passes = sorted({f["pass"] for f in feat_list if f["pass"]})
            resolved_pass = valid_passes[0] if valid_passes else None

            G.add_edge(
                best_feat["u"],
                best_feat["v"],
                weight=best_feat["length_nm"],
                pass_id=resolved_pass,
                stitched=False,
                geometry=best_feat["points"],
                fid=best_feat["fid"],
            )

        base_edge_count = G.number_of_edges()

        # Generate antimeridian connectors across Date Line (+-180 deg)
        antimeridian_bridges = self._add_antimeridian_connectors(G)

        # Analyze graph connectivity
        components = list(nx.connected_components(G))
        largest_component_size = max(len(c) for c in components) if components else 0
        is_fully_connected = len(components) == 1

        # Build stable node index
        sorted_nodes = sorted(G.nodes(), key=lambda n: (n[0], n[1]))
        node_to_id = {node: f"node_{i}" for i, node in enumerate(sorted_nodes)}
        id_to_coords = {f"node_{i}": [node[0], node[1]] for i, node in enumerate(sorted_nodes)}

        # Build stable edges list
        edges_output = []
        for u, v in G.edges():
            edge_data = G[u][v]
            edges_output.append(
                {
                    "u": node_to_id[u],
                    "v": node_to_id[v],
                    "weight_nm": edge_data["weight"],
                    "pass_id": edge_data.get("pass_id"),
                    "stitched": edge_data.get("stitched", False),
                    "geometry": [[pt[0], pt[1]] for pt in edge_data.get("geometry", [u, v])],
                }
            )

        # Sort edges for deterministic JSON export
        edges_output.sort(key=lambda e: (e["u"], e["v"]))

        audit_summary = {
            "total_input_features": total_features,
            "self_loops_count": len(self_loops),
            "self_loops_excluded": exclude_self_loops,
            "parallel_edge_groups": parallel_pairs,
            "duplicate_edges_collapsed": duplicate_edges_collapsed,
            "base_edges_count": base_edge_count,
            "antimeridian_bridges_added": antimeridian_bridges,
            "final_node_count": G.number_of_nodes(),
            "final_edge_count": G.number_of_edges(),
            "connected_components_count": len(components),
            "largest_component_nodes": largest_component_size,
            "is_fully_connected": is_fully_connected,
        }

        metadata = {
            "schema_version": "pelagos-marnet-graph-v1",
            "transformation_version": TRANSFORMATION_VERSION,
            "source_repo": MARNET_REPO_URL,
            "source_commit": MARNET_COMMIT_SHA,
            "source_sha256": compute_sha256(marnet_gpkg_path),
            "crs": "EPSG:4326",
            "distance_unit": "nautical_mile",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "audit_summary": audit_summary,
            "licensing": LICENSING_NOTICE,
        }

        return {
            "metadata": metadata,
            "nodes": id_to_coords,
            "edges": edges_output,
            "audit_summary": audit_summary,
        }

    def _add_antimeridian_connectors(self, G: nx.Graph) -> int:
        """Connect nodes at +180.0 and -180.0 with exact latitudinal parity.

        Inserts zero-weight bridge edges idempotently.
        Returns the number of bridge edges added.
        """
        pos_180 = [n for n in G.nodes() if abs(n[0] - 180.0) < 1e-5]
        neg_180 = [n for n in G.nodes() if abs(n[0] - (-180.0)) < 1e-5]

        bridges_added = 0
        for p in pos_180:
            for m in neg_180:
                if abs(p[1] - m[1]) < 1e-5:
                    if not G.has_edge(p, m):
                        G.add_edge(
                            p,
                            m,
                            weight=0.0,
                            pass_id=None,
                            stitched=True,
                            geometry=[p, m],
                        )
                        bridges_added += 1

        return bridges_added


def export_graph_artifact(graph_data: Dict[str, Any], output_path: Path) -> Path:
    """Export the constructed graph to a deterministic JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.parent / f"{output_path.name}.tmp"

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(graph_data, f, indent=2, sort_keys=True)

    if output_path.exists():
        output_path.unlink()
    temp_path.rename(output_path)
    logger.info("Exported graph artifact to %s", output_path)
    return output_path


def write_manifest(cache_dir: Path, metadata: Dict[str, Any]) -> Path:
    """Write an auditable manifest.json to the local cache directory."""
    manifest_path = cache_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, sort_keys=True)
    logger.info("Wrote manifest to %s", manifest_path)
    return manifest_path
