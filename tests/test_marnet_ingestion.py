"""Tests for Milestone D9.4 - Stage 1 & Stage 2: MARNET Acquisition and Deterministic Graph Ingestion.

Validates checksum enforcement, GeoPackage schema validation, pure-Python WKB decoding,
self-loop exclusion, parallel-edge aggregation, attribute conflict rejection, antimeridian
bridging, deterministic serialization, and repository cache safety.

All unit tests use small, isolated synthetic test fixtures with zero external network access.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import struct
import tempfile
from typing import List, Tuple

import pytest

from pelagos.routing.ingestion import (
    EXPECTED_ANTIMERIDIAN_BRIDGES,
    EXPECTED_EDGE_COUNT_EXCLUDING_LOOPS,
    EXPECTED_FEATURES_COUNT,
    EXPECTED_MARNET_SHA256,
    EXPECTED_NODE_COUNT,
    EXPECTED_PARALLEL_PAIRS,
    EXPECTED_SELF_LOOPS,
    GeoPackageSchemaError,
    GeoPackageValidator,
    MalformedGeometryError,
    MarnetAcquisition,
    MarnetGraphBuilder,
    PassAttributeConflictError,
    UnsupportedGeometryTypeError,
    WkbGeometryParser,
    compute_sha256,
    export_graph_artifact,
)


def encode_test_gpkg_linestring(points: List[Tuple[float, float]], srs_id: int = 4326) -> bytes:
    """Encode a synthetic GeoPackage binary header and WKB LineString payload."""
    # GeoPackage binary header:
    # 0-1: magic 'GP' (0x47, 0x50)
    # 2: version (0)
    # 3: flags (0x01: little-endian, envelope=0)
    # 4-7: srs_id (4326)
    header = struct.pack("<BBBBI", 0x47, 0x50, 0, 1, srs_id)

    # WKB LineString:
    # byte 0: byte order (1 = little-endian)
    # byte 1-4: geom_type (2 = LineString)
    # byte 5-8: num_points
    # byte 9+: array of 16-byte double pairs (x, y)
    wkb_body = bytearray()
    wkb_body.append(1)  # little endian
    wkb_body.extend(struct.pack("<II", 2, len(points)))
    for x, y in points:
        wkb_body.extend(struct.pack("<dd", float(x), float(y)))

    return bytes(header) + bytes(wkb_body)


def create_synthetic_gpkg(
    file_path: Path,
    features: List[Tuple[int, List[Tuple[float, float]], str | None]],
    srs_id: int = 4326,
    create_metadata_tables: bool = True,
) -> None:
    """Create a minimal synthetic GeoPackage file for unit testing."""
    conn = sqlite3.connect(file_path)
    cur = conn.cursor()

    if create_metadata_tables:
        cur.execute(
            """
            CREATE TABLE gpkg_spatial_ref_sys (
                srs_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL PRIMARY KEY,
                organization TEXT NOT NULL,
                organization_coordsys_id INTEGER NOT NULL,
                definition TEXT NOT NULL,
                description TEXT
            )
            """
        )
        cur.execute(
            "INSERT INTO gpkg_spatial_ref_sys VALUES ('WGS 84', 4326, 'EPSG', 4326, '', '')"
        )

        cur.execute(
            """
            CREATE TABLE gpkg_geometry_columns (
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                geometry_type_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL,
                z TINYINT NOT NULL,
                m TINYINT NOT NULL,
                CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name)
            )
            """
        )
        cur.execute(
            "INSERT INTO gpkg_geometry_columns VALUES ('type', 'geometry', 'LINESTRING', ?, 0, 0)",
            (srs_id,),
        )

        cur.execute(
            """
            CREATE TABLE gpkg_contents (
                table_name TEXT NOT NULL PRIMARY KEY,
                data_type TEXT NOT NULL,
                identifier TEXT,
                description TEXT,
                last_change TEXT,
                min_x DOUBLE,
                min_y DOUBLE,
                max_x DOUBLE,
                max_y DOUBLE,
                srs_id INTEGER
            )
            """
        )
        cur.execute(
            "INSERT INTO gpkg_contents VALUES ('type', 'features', 'type', '', '', -180.0, -90.0, 180.0, 90.0, ?)",
            (srs_id,),
        )

    cur.execute(
        """
        CREATE TABLE type (
            fid INTEGER PRIMARY KEY,
            geometry BLOB NOT NULL,
            pass TEXT
        )
        """
    )

    for fid, pts, pass_val in features:
        blob = encode_test_gpkg_linestring(pts, srs_id=srs_id)
        cur.execute("INSERT INTO type VALUES (?, ?, ?)", (fid, blob, pass_val))

    conn.commit()
    conn.close()


class TestMarnetAcquisitionAndChecksum:
    """Unit tests for Stage 1 acquisition and SHA-256 validation."""

    def test_sha256_computation(self, tmp_path: Path) -> None:
        """Verify streaming SHA-256 computation matches known data."""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"Pelagos Maritime Routing Test")
        expected_hash = "d4c66057720a634694a6e06b4133c94ddddcf9db8c14ca02ce130d6ddfb1383b"
        assert compute_sha256(test_file) == expected_hash

    def test_download_checksum_mismatch_fails_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify acquisition fails closed and removes corrupted download on hash mismatch."""
        acquirer = MarnetAcquisition(cache_dir=tmp_path)

        # Mock urllib to return dummy data
        class MockResponse:
            def __enter__(self) -> MockResponse:
                return self

            def __exit__(self, *args) -> None:
                pass

            def read(self, *args) -> bytes:
                if not hasattr(self, "_read"):
                    self._read = True
                    return b"Corrupt or spoofed data payload"
                return b""

        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=60.0: MockResponse(),
        )

        with pytest.raises(Exception) as exc_info:
            acquirer.download_file(
                url="https://example.com/fake.gpkg",
                target_name="marnet_plus_50km.gpkg",
                expected_sha256=EXPECTED_MARNET_SHA256,
            )

        assert "Checksum mismatch" in str(exc_info.value)
        # Ensure temporary file and target file do not exist
        assert not (tmp_path / "marnet_plus_50km.gpkg").exists()
        assert not (tmp_path / "marnet_plus_50km.gpkg.tmp").exists()

    def test_cache_safety_and_gitignore(self) -> None:
        """Verify .gitignore excludes .cache/ and data/routing/ from Git."""
        repo_root = Path(__file__).resolve().parent.parent
        gitignore_path = repo_root / ".gitignore"
        assert gitignore_path.exists()
        content = gitignore_path.read_text(encoding="utf-8")
        assert ".cache/" in content
        assert "data/routing/" in content


class TestGeoPackageValidationAndWkbParser:
    """Unit tests for Stage 2 GeoPackage schema and pure-Python binary geometry parsing."""

    def test_geopackage_missing_type_table(self, tmp_path: Path) -> None:
        """Verify schema validator rejects GeoPackage missing the required 'type' table."""
        bad_gpkg = tmp_path / "bad.gpkg"
        conn = sqlite3.connect(bad_gpkg)
        conn.execute("CREATE TABLE wrong_table (id INTEGER)")
        conn.close()

        with pytest.raises(GeoPackageSchemaError) as exc_info:
            GeoPackageValidator.validate_marnet_structure(bad_gpkg)
        assert "Missing required table 'type'" in str(exc_info.value)

    def test_geopackage_missing_columns(self, tmp_path: Path) -> None:
        """Verify schema validator rejects table 'type' lacking required columns."""
        bad_gpkg = tmp_path / "missing_cols.gpkg"
        conn = sqlite3.connect(bad_gpkg)
        conn.execute("CREATE TABLE type (fid INTEGER, geometry BLOB)")  # missing 'pass'
        conn.close()

        with pytest.raises(GeoPackageSchemaError) as exc_info:
            GeoPackageValidator.validate_marnet_structure(bad_gpkg)
        assert "missing required columns" in str(exc_info.value)

    def test_geopackage_wrong_crs_rejected(self, tmp_path: Path) -> None:
        """Verify schema validator rejects layers not configured for WGS 84 (EPSG:4326)."""
        wrong_crs_gpkg = tmp_path / "wrong_crs.gpkg"
        create_synthetic_gpkg(
            wrong_crs_gpkg,
            features=[(1, [(0.0, 0.0), (1.0, 1.0)], None)],
            srs_id=3857,  # Web Mercator instead of 4326
        )

        with pytest.raises(GeoPackageSchemaError) as exc_info:
            GeoPackageValidator.validate_marnet_structure(wrong_crs_gpkg)
        assert "Expected SRS 4326" in str(exc_info.value)

    def test_geopackage_missing_geometry_columns_table(self, tmp_path: Path) -> None:
        """Verify schema validator rejects GeoPackage missing gpkg_geometry_columns."""
        bad_gpkg = tmp_path / "no_geom_cols.gpkg"
        conn = sqlite3.connect(bad_gpkg)
        conn.execute("CREATE TABLE type (fid INTEGER PRIMARY KEY, geometry BLOB, pass TEXT)")
        conn.close()

        with pytest.raises(GeoPackageSchemaError) as exc_info:
            GeoPackageValidator.validate_marnet_structure(bad_gpkg)
        assert "Missing metadata table 'gpkg_geometry_columns'" in str(exc_info.value)

    def test_geopackage_geometry_column_name_mismatch(self, tmp_path: Path) -> None:
        """Verify schema validator rejects mismatch between parsed column and metadata."""
        bad_gpkg = tmp_path / "col_mismatch.gpkg"
        conn = sqlite3.connect(bad_gpkg)
        conn.execute("CREATE TABLE type (fid INTEGER PRIMARY KEY, geometry BLOB, pass TEXT)")
        conn.execute(
            "CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT, geometry_type_name TEXT, srs_id INTEGER, z TINYINT, m TINYINT)"
        )
        conn.execute("INSERT INTO gpkg_geometry_columns VALUES ('type', 'geom', 'LINESTRING', 4326, 0, 0)")
        conn.commit()
        conn.close()

        with pytest.raises(GeoPackageSchemaError) as exc_info:
            GeoPackageValidator.validate_marnet_structure(bad_gpkg)
        assert "does not match expected 'geometry'" in str(exc_info.value)

    def test_geopackage_unsupported_3d_z_dimension(self, tmp_path: Path) -> None:
        """Verify schema validator rejects layers configured with 3D coordinates (z=1)."""
        bad_gpkg = tmp_path / "z_coord.gpkg"
        conn = sqlite3.connect(bad_gpkg)
        conn.execute("CREATE TABLE type (fid INTEGER PRIMARY KEY, geometry BLOB, pass TEXT)")
        conn.execute(
            "CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT, geometry_type_name TEXT, srs_id INTEGER, z TINYINT, m TINYINT)"
        )
        conn.execute("INSERT INTO gpkg_geometry_columns VALUES ('type', 'geometry', 'LINESTRING', 4326, 1, 0)")
        conn.commit()
        conn.close()

        with pytest.raises(GeoPackageSchemaError) as exc_info:
            GeoPackageValidator.validate_marnet_structure(bad_gpkg)
        assert "Unsupported 3D coordinate dimension" in str(exc_info.value)

    def test_pass_structure_validation(self, tmp_path: Path) -> None:
        """Verify validator checks pass.gpkg schema and detects missing columns."""
        pass_gpkg = tmp_path / "pass_test.gpkg"
        conn = sqlite3.connect(pass_gpkg)
        conn.execute("CREATE TABLE pass (fid INTEGER PRIMARY KEY, geometry BLOB, name TEXT, id TEXT)")
        conn.execute(
            "CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT, geometry_type_name TEXT, srs_id INTEGER, z TINYINT, m TINYINT)"
        )
        conn.execute("INSERT INTO gpkg_geometry_columns VALUES ('pass', 'geometry', 'POLYGON', 4326, 0, 0)")
        conn.commit()
        conn.close()

        # Valid pass structure succeeds
        GeoPackageValidator.validate_pass_structure(pass_gpkg)

        # Missing required column fails closed
        bad_pass = tmp_path / "bad_pass.gpkg"
        conn = sqlite3.connect(bad_pass)
        conn.execute("CREATE TABLE pass (fid INTEGER PRIMARY KEY, geometry BLOB, name TEXT)")
        conn.close()

        with pytest.raises(GeoPackageSchemaError) as exc_info:
            GeoPackageValidator.validate_pass_structure(bad_pass)
        assert "missing required columns" in str(exc_info.value)

    def test_wkb_parser_invalid_magic_bytes(self) -> None:
        """Verify parser rejects binary blobs with invalid GeoPackage magic bytes."""
        bad_blob = b"\x00\x00\x00\x01\x10\xe6\x00\x00\x01\x02\x00\x00\x00"
        with pytest.raises(MalformedGeometryError) as exc_info:
            WkbGeometryParser.parse_gpkg_linestring(bad_blob, fid=101)
        assert "Invalid GeoPackage magic bytes" in str(exc_info.value)

    def test_wkb_parser_header_srs_mismatch(self) -> None:
        """Verify parser rejects geometry blobs whose binary header declares non-4326 SRS."""
        header = struct.pack("<BBBBI", 0x47, 0x50, 0, 1, 3857)  # srs_id = 3857
        wkb = bytearray([1]) + struct.pack("<II", 2, 2)
        wkb += struct.pack("<dd", 10.0, 20.0)
        wkb += struct.pack("<dd", 11.0, 21.0)
        blob = bytes(header) + bytes(wkb)

        with pytest.raises(MalformedGeometryError) as exc_info:
            WkbGeometryParser.parse_gpkg_linestring(blob, fid=404)
        assert "Expected CRS 4326 in geometry header, got 3857" in str(exc_info.value)

    def test_wkb_parser_unsupported_geometry_type(self) -> None:
        """Verify parser rejects non-LineString geometries (e.g. Point or Polygon)."""
        header = struct.pack("<BBBBI", 0x47, 0x50, 0, 1, 4326)
        # WKB Point (type 1)
        wkb_point = bytearray([1]) + struct.pack("<Idd", 1, 10.0, 20.0)
        blob = bytes(header) + bytes(wkb_point)

        with pytest.raises(UnsupportedGeometryTypeError) as exc_info:
            WkbGeometryParser.parse_gpkg_linestring(blob, fid=202)
        assert "Expected LineString geometry (type 2)" in str(exc_info.value)

    def test_wkb_parser_out_of_bounds_coordinate(self) -> None:
        """Verify parser rejects coordinates exceeding WGS 84 geographic bounds."""
        header = struct.pack("<BBBBI", 0x47, 0x50, 0, 1, 4326)
        wkb = bytearray([1]) + struct.pack("<II", 2, 2)
        wkb += struct.pack("<dd", 200.0, 30.0)  # lon > 181
        wkb += struct.pack("<dd", 10.0, 30.0)
        blob = bytes(header) + bytes(wkb)

        with pytest.raises(MalformedGeometryError) as exc_info:
            WkbGeometryParser.parse_gpkg_linestring(blob, fid=303)
        assert "out of WGS 84 range" in str(exc_info.value)

    def test_wkb_parser_length_mismatch_fails_closed(self) -> None:
        """Verify parser rejects truncated WKB payloads where length != expected."""
        header = struct.pack("<BBBBI", 0x47, 0x50, 0, 1, 4326)
        # Declares 3 points (needs 9 + 48 = 57 bytes), but only provide 1 point
        wkb = bytearray([1]) + struct.pack("<II", 2, 3)
        wkb += struct.pack("<dd", 10.0, 20.0)
        blob = bytes(header) + bytes(wkb)

        with pytest.raises(MalformedGeometryError) as exc_info:
            WkbGeometryParser.parse_gpkg_linestring(blob, fid=505)
        assert "does not match expected" in str(exc_info.value)

    def test_wkb_parser_nan_inf_rejected(self) -> None:
        """Verify parser rejects geometries containing NaN or Infinity values."""
        header = struct.pack("<BBBBI", 0x47, 0x50, 0, 1, 4326)
        wkb = bytearray([1]) + struct.pack("<II", 2, 2)
        wkb += struct.pack("<dd", float("nan"), 20.0)
        wkb += struct.pack("<dd", 11.0, 21.0)
        blob = bytes(header) + bytes(wkb)

        with pytest.raises(MalformedGeometryError) as exc_info:
            WkbGeometryParser.parse_gpkg_linestring(blob, fid=606)
        assert "Coordinate contains NaN or Inf" in str(exc_info.value)

    def test_wkb_parser_excessive_points_rejected(self) -> None:
        """Verify parser guards against extreme point counts to prevent runaway allocation."""
        header = struct.pack("<BBBBI", 0x47, 0x50, 0, 1, 4326)
        wkb = bytearray([1]) + struct.pack("<II", 2, 200000)
        blob = bytes(header) + bytes(wkb)

        with pytest.raises(MalformedGeometryError) as exc_info:
            WkbGeometryParser.parse_gpkg_linestring(blob, fid=707)
        assert "exceeds safety threshold" in str(exc_info.value)


class TestGraphConstructionRules:
    """Unit tests for Stage 2 graph construction rules."""

    def test_linestring_intermediate_vertices_geodesic_sum(self, tmp_path: Path) -> None:
        """Verify edge weight is the cumulative geodesic distance across intermediate vertices,

        not merely the direct geodesic distance between start and end coordinates.
        """
        # Triangular waypoint path: (0, 0) -> (0, 10) -> (10, 10)
        # Direct geodesic distance between endpoints (0, 0) and (10, 10) is ~852.7 NM.
        # Cumulative distance along the two legs is ~600.0 + ~590.9 = ~1190.9 NM.
        points = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0)]
        gpkg_path = tmp_path / "intermediate.gpkg"
        create_synthetic_gpkg(gpkg_path, features=[(1, points, None)])

        builder = MarnetGraphBuilder()
        graph_data = builder.build_from_geopackage(gpkg_path)

        edge = graph_data["edges"][0]
        cumulative_nm = builder.calculate_linestring_length_nm(points)

        # Endpoint-only distance
        _, _, direct_meters = builder.geod.inv(points[0][0], points[0][1], points[-1][0], points[-1][1])
        direct_nm = direct_meters / 1852.0

        # Verify cumulative strictly exceeds endpoint-only distance
        assert cumulative_nm > direct_nm + 100.0
        # Verify edge weight matches the cumulative geodesic sum
        assert edge["weight_nm"] == round(cumulative_nm, 4)
        assert edge["weight_nm"] != round(direct_nm, 4)

    def test_self_loop_exclusion(self, tmp_path: Path) -> None:
        """Verify self-loop features (u == v) are excluded from graph edges."""
        gpkg_path = tmp_path / "self_loops.gpkg"
        features = [
            # Feature 1: Regular valid line
            (1, [(-10.0, 20.0), (-5.0, 25.0)], None),
            # Feature 2: Self-loop closed ring (start == end)
            (2, [(30.0, 40.0), (32.0, 42.0), (30.0, 40.0)], None),
        ]
        create_synthetic_gpkg(gpkg_path, features)

        builder = MarnetGraphBuilder()
        graph_data = builder.build_from_geopackage(gpkg_path, exclude_self_loops=True)

        audit = graph_data["audit_summary"]
        assert audit["total_input_features"] == 2
        assert audit["self_loops_count"] == 1
        assert audit["self_loops_excluded"] is True
        # Only the non-loop feature should become an edge
        assert audit["final_edge_count"] == 1
        assert len(graph_data["edges"]) == 1

    def test_parallel_edge_collapse_and_chokepoint_preservation(self, tmp_path: Path) -> None:
        """Verify duplicate endpoint pairs are collapsed to minimum length while preserving pass tag."""
        gpkg_path = tmp_path / "parallel.gpkg"
        features = [
            # Feature 1: longer route (3 intermediate vertices)
            (1, [(-80.0, 9.0), (-79.8, 9.2), (-79.5, 9.0)], "panama"),
            # Feature 2: shorter direct route between identical endpoints
            (2, [(-80.0, 9.0), (-79.5, 9.0)], "panama"),
        ]
        create_synthetic_gpkg(gpkg_path, features)

        builder = MarnetGraphBuilder()
        graph_data = builder.build_from_geopackage(gpkg_path)

        audit = graph_data["audit_summary"]
        assert audit["total_input_features"] == 2
        assert audit["parallel_edge_groups"] == 1
        assert audit["duplicate_edges_collapsed"] == 1
        assert audit["final_edge_count"] == 1

        edge = graph_data["edges"][0]
        assert edge["pass_id"] == "panama"
        # Minimum length feature (Feature 2) should be retained
        assert edge["weight_nm"] < 35.0

    def test_pass_attribute_conflict_raises_error(self, tmp_path: Path) -> None:
        """Verify conflicting non-null chokepoint tags between identical endpoints raise an error."""
        gpkg_path = tmp_path / "conflict.gpkg"
        features = [
            (1, [(0.0, 10.0), (1.0, 11.0)], "panama"),
            (2, [(0.0, 10.0), (1.0, 11.0)], "suez"),  # conflicting pass
        ]
        create_synthetic_gpkg(gpkg_path, features)

        builder = MarnetGraphBuilder()
        with pytest.raises(PassAttributeConflictError) as exc_info:
            builder.build_from_geopackage(gpkg_path)
        assert "conflicting pass attributes" in str(exc_info.value)

    def test_antimeridian_connector_generation_and_idempotency(self, tmp_path: Path) -> None:
        """Verify 12 zero-weight antimeridian bridge edges are added with exact latitudinal parity."""
        gpkg_path = tmp_path / "antimeridian.gpkg"
        features = [
            # Segment West of Date Line (terminates at +180.0, 40.0)
            (1, [(170.0, 40.0), (180.0, 40.0)], None),
            # Segment East of Date Line (starts at -180.0, 40.0)
            (2, [(-180.0, 40.0), (-170.0, 40.0)], None),
            # Segment without matching counterpart (e.g. at lat -15.0)
            (3, [(175.0, -15.0), (180.0, -15.0)], None),
        ]
        create_synthetic_gpkg(gpkg_path, features)

        builder = MarnetGraphBuilder()
        graph_data = builder.build_from_geopackage(gpkg_path)

        audit = graph_data["audit_summary"]
        # Exactly 1 bridge added (at latitude 40.0)
        assert audit["antimeridian_bridges_added"] == 1
        # Base edges = 3, bridge = 1 -> total = 4
        assert audit["final_edge_count"] == 4

        # Find the stitched edge
        stitched_edges = [e for e in graph_data["edges"] if e["stitched"]]
        assert len(stitched_edges) == 1
        assert stitched_edges[0]["weight_nm"] == 0.0

    def test_deterministic_output(self, tmp_path: Path) -> None:
        """Verify building the graph twice from identical input produces bit-for-bit identical JSON."""
        gpkg_path = tmp_path / "det.gpkg"
        features = [
            (1, [(-120.0, 30.0), (-125.0, 32.0)], None),
            (2, [(-125.0, 32.0), (-130.0, 35.0)], "dover"),
        ]
        create_synthetic_gpkg(gpkg_path, features)

        builder = MarnetGraphBuilder()
        run1 = builder.build_from_geopackage(gpkg_path)
        run2 = builder.build_from_geopackage(gpkg_path)

        out1 = tmp_path / "out1.json"
        out2 = tmp_path / "out2.json"

        # Freeze generated_at for exact byte comparison
        run1["metadata"]["generated_at"] = "2026-10-18T00:00:00Z"
        run2["metadata"]["generated_at"] = "2026-10-18T00:00:00Z"

        export_graph_artifact(run1, out1)
        export_graph_artifact(run2, out2)

        assert out1.read_bytes() == out2.read_bytes()
