#!/usr/bin/env python3
"""Pelagos Maritime Routing - MARNET Dataset Ingestion CLI (Milestone D9.4).

Executes reproducible, non-interactive acquisition and deterministic graph
construction for the Eurostat SeaRoute MARNET network.

Usage:
    python scripts/ingest_marnet.py [--force] [--cache-dir DIR]
    python scripts/ingest_marnet.py --download-only
    python scripts/ingest_marnet.py --build-only
    python scripts/ingest_marnet.py --check
    python scripts/ingest_marnet.py --source-gpkg PATH
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys

# Ensure repository root and backend are in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from pelagos.routing.ingestion import (
    DEFAULT_CACHE_DIR,
    EXPECTED_ANTIMERIDIAN_BRIDGES,
    EXPECTED_EDGE_COUNT_EXCLUDING_LOOPS,
    EXPECTED_FEATURES_COUNT,
    EXPECTED_MARNET_SHA256,
    EXPECTED_NODE_COUNT,
    EXPECTED_PARALLEL_PAIRS,
    EXPECTED_PASS_SHA256,
    EXPECTED_SELF_LOOPS,
    MARNET_COMMIT_SHA,
    GeoPackageValidator,
    MarnetAcquisition,
    MarnetGraphBuilder,
    MarnetIngestionError,
    compute_sha256,
    export_graph_artifact,
    write_manifest,
)

DISCLAIMER_BANNER = """
================================================================================
PELAGOS MARITIME ROUTING - EUROSTAT MARNET INGESTION TOOL (MILESTONE D9.4)
--------------------------------------------------------------------------------
LEGAL NOTICE: Upstream source data rights (ORNL base shipping lanes and Eurostat
AIS vessel density additions) remain legally unresolved under EUPL-1.2.
This tool downloads assets strictly to a Git-ignored local cache (.cache/) for
local analytical evaluation and deterministic graph compilation.

DO NOT COMMIT DOWNLOADED DATASETS OR DERIVED NETWORKS TO GIT.
DATA IS NOT CERTIFIED FOR ECDIS NAVIGATION OR HYDROGRAPHIC PASSAGE SAFETY.
================================================================================
"""


def setup_logging(verbose: bool = True) -> None:
    """Configure structured console logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Acquire and compile Eurostat MARNET shipping lanes for Pelagos routing."
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"Directory for local Git-ignored cache (default: {DEFAULT_CACHE_DIR})",
    )
    parser.add_argument(
        "--source-gpkg",
        type=Path,
        default=None,
        help="Optional path to existing local marnet_plus_50km.gpkg (bypasses network download)",
    )
    parser.add_argument(
        "--pass-gpkg",
        type=Path,
        default=None,
        help="Optional path to existing local pass.gpkg (bypasses network download)",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Download and verify GeoPackage checksums only; do not build graph",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Build graph from existing cache files; do not download from network",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download and re-compilation even if cache files already exist",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check validity and checksums of existing cache files without modifying anything",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Audit cached or specified GeoPackage against D9.3 regression benchmark counts",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress informational console output",
    )
    return parser.parse_args()


def audit_dataset(gpkg_path: Path) -> int:
    """Run full graph construction audit against regression expectations."""
    print(f"Auditing MARNET GeoPackage: {gpkg_path.resolve()}")
    if not gpkg_path.exists():
        print(f"ERROR: GeoPackage does not exist: {gpkg_path}")
        return 1

    sha256 = compute_sha256(gpkg_path)
    if sha256 != EXPECTED_MARNET_SHA256:
        print(f"ERROR: Checksum mismatch: {sha256} != {EXPECTED_MARNET_SHA256}")
        return 2

    builder = MarnetGraphBuilder()
    graph_data = builder.build_from_geopackage(gpkg_path, exclude_self_loops=True)
    audit = graph_data["audit_summary"]

    expected = {
        "total_input_features": EXPECTED_FEATURES_COUNT,
        "self_loops_count": EXPECTED_SELF_LOOPS,
        "parallel_edge_groups": EXPECTED_PARALLEL_PAIRS,
        "duplicate_edges_collapsed": EXPECTED_PARALLEL_PAIRS,
        "antimeridian_bridges_added": EXPECTED_ANTIMERIDIAN_BRIDGES,
        "final_node_count": EXPECTED_NODE_COUNT,
        "final_edge_count": EXPECTED_EDGE_COUNT_EXCLUDING_LOOPS,
        "connected_components_count": 1,
    }

    print("\n=== MARNET AUDIT BENCHMARK VERIFICATION ===")
    all_match = True
    for key, exp_val in expected.items():
        actual_val = audit.get(key)
        matches = (actual_val == exp_val)
        status = "[MATCH]" if matches else "[DIVERGED]"
        print(f"  {status} {key:<30}: actual={actual_val:<8} expected={exp_val}")
        if not matches:
            all_match = False

    if all_match:
        print("\nSUCCESS: All audited graph counts match commit 88a2e568 bit-for-bit.")
        return 0
    else:
        print("\nFAILURE: One or more graph counts diverged from the regression benchmark.")
        return 3


def check_cache(cache_dir: Path) -> int:
    """Inspect and validate existing cache files."""
    print(f"Inspecting cache directory: {cache_dir.resolve()}")
    if not cache_dir.exists():
        print(f"ERROR: Cache directory does not exist: {cache_dir}")
        return 1

    marnet_path = cache_dir / "marnet_plus_50km.gpkg"
    network_path = cache_dir / "marnet_network.json"
    manifest_path = cache_dir / "manifest.json"

    errors = 0
    if not marnet_path.exists():
        print(f"  [-] Missing source GeoPackage: {marnet_path.name}")
        errors += 1
    else:
        sha256 = compute_sha256(marnet_path)
        if sha256 == EXPECTED_MARNET_SHA256:
            print(f"  [+] Source GeoPackage valid: {marnet_path.name} (SHA-256 matches)")
        else:
            print(f"  [-] Source GeoPackage checksum mismatch: {sha256} != {EXPECTED_MARNET_SHA256}")
            errors += 1

    if not network_path.exists():
        print(f"  [-] Missing compiled network artifact: {network_path.name}")
        errors += 1
    else:
        print(f"  [+] Compiled network artifact present: {network_path.name} ({network_path.stat().st_size:,} bytes)")

    if not manifest_path.exists():
        print(f"  [-] Missing ingestion manifest: {manifest_path.name}")
        errors += 1
    else:
        print(f"  [+] Ingestion manifest present: {manifest_path.name}")

    if errors == 0:
        print("SUCCESS: All cache artifacts are present and verified.")
        return 0
    else:
        print(f"WARNING: Cache verification found {errors} missing or invalid items.")
        return 2


def main() -> int:
    """CLI entrypoint."""
    args = parse_args()
    setup_logging(verbose=not args.quiet)

    if not args.quiet:
        print(DISCLAIMER_BANNER)

    cache_dir = args.cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)

    if args.check:
        return check_cache(cache_dir)

    if args.audit:
        source = args.source_gpkg or (cache_dir / "marnet_plus_50km.gpkg")
        return audit_dataset(Path(source))

    marnet_gpkg_path = cache_dir / "marnet_plus_50km.gpkg"
    pass_gpkg_path = cache_dir / "pass.gpkg"

    # Stage 1: Acquisition & Verification
    if args.source_gpkg:
        source_path = Path(args.source_gpkg)
        if not source_path.exists():
            logging.error("Specified source GeoPackage does not exist: %s", source_path)
            return 1
        sha256 = compute_sha256(source_path)
        if sha256 != EXPECTED_MARNET_SHA256:
            logging.error(
                "Source GeoPackage checksum mismatch: expected %s, got %s",
                EXPECTED_MARNET_SHA256,
                sha256,
            )
            return 2
        logging.info("Using verified local GeoPackage: %s", source_path)
        target_in_cache = cache_dir / "marnet_plus_50km.gpkg"
        if source_path.resolve() != target_in_cache.resolve():
            import shutil
            shutil.copy2(source_path, target_in_cache)
            logging.info("Populated cache with verified GeoPackage: %s", target_in_cache)
            marnet_gpkg_path = target_in_cache
        else:
            marnet_gpkg_path = source_path
        if args.pass_gpkg:
            pass_source_path = Path(args.pass_gpkg)
            if pass_source_path.exists():
                sha256_pass = compute_sha256(pass_source_path)
                if sha256_pass == EXPECTED_PASS_SHA256:
                    target_pass_in_cache = cache_dir / "pass.gpkg"
                    if pass_source_path.resolve() != target_pass_in_cache.resolve():
                        import shutil
                        shutil.copy2(pass_source_path, target_pass_in_cache)
                    pass_gpkg_path = target_pass_in_cache
    elif not args.build_only:
        logging.info("Stage 1: Acquiring upstream Eurostat MARNET assets (commit %s) ...", MARNET_COMMIT_SHA[:8])
        acquirer = MarnetAcquisition(cache_dir=cache_dir)
        try:
            acquired = acquirer.acquire_all(force=args.force)
            marnet_gpkg_path = acquired["marnet"]
            pass_gpkg_path = acquired["pass"]
        except MarnetIngestionError as e:
            logging.error("Acquisition failed: %s", e)
            return 2
    else:
        if not marnet_gpkg_path.exists():
            logging.error("Cache file %s does not exist. Run without --build-only to download.", marnet_gpkg_path)
            return 1
        sha256 = compute_sha256(marnet_gpkg_path)
        if sha256 != EXPECTED_MARNET_SHA256:
            logging.error("Cached GeoPackage failed SHA-256 verification: %s", sha256)
            return 2

    # Validate GeoPackage structure
    logging.info("Validating GeoPackage SQLite schema and CRS for %s ...", marnet_gpkg_path.name)
    try:
        GeoPackageValidator.validate_marnet_structure(marnet_gpkg_path)
        if pass_gpkg_path.exists():
            GeoPackageValidator.validate_pass_structure(pass_gpkg_path)
    except MarnetIngestionError as e:
        logging.error("GeoPackage schema validation failed: %s", e)
        return 3

    if args.download_only:
        logging.info("Download and validation complete (--download-only specified).")
        return 0

    # Stage 2: Deterministic Graph Construction
    network_output_path = cache_dir / "marnet_network.json"
    if network_output_path.exists() and not args.force:
        logging.info(
            "Graph artifact %s already exists. Use --force to recompile.",
            network_output_path,
        )
        return 0

    logging.info("Stage 2: Constructing deterministic maritime graph from %s ...", marnet_gpkg_path.name)
    builder = MarnetGraphBuilder()
    try:
        graph_data = builder.build_from_geopackage(marnet_gpkg_path, exclude_self_loops=True)
    except MarnetIngestionError as e:
        logging.error("Graph construction failed: %s", e)
        return 4

    audit = graph_data["audit_summary"]
    logging.info("=== Graph Construction Summary ===")
    logging.info("  Input LineString features:      %d", audit["total_input_features"])
    logging.info("  Self-loops excluded (u == v):   %d", audit["self_loops_count"])
    logging.info("  Parallel edge groups collapsed: %d", audit["parallel_edge_groups"])
    logging.info("  Duplicate edges discarded:      %d", audit["duplicate_edges_collapsed"])
    logging.info("  Antimeridian bridges added:     %d", audit["antimeridian_bridges_added"])
    logging.info("  Final unique nodes:             %d", audit["final_node_count"])
    logging.info("  Final unique edges:             %d", audit["final_edge_count"])
    logging.info("  Connected components:           %d", audit["connected_components_count"])

    # Regression verification against commit 88a2e568 benchmark
    regression_mismatches = []
    if audit["total_input_features"] != EXPECTED_FEATURES_COUNT:
        regression_mismatches.append(f"features: {audit['total_input_features']} != {EXPECTED_FEATURES_COUNT}")
    if audit["self_loops_count"] != EXPECTED_SELF_LOOPS:
        regression_mismatches.append(f"self-loops: {audit['self_loops_count']} != {EXPECTED_SELF_LOOPS}")
    if audit["parallel_edge_groups"] != EXPECTED_PARALLEL_PAIRS:
        regression_mismatches.append(f"parallel pairs: {audit['parallel_edge_groups']} != {EXPECTED_PARALLEL_PAIRS}")
    if audit["antimeridian_bridges_added"] != EXPECTED_ANTIMERIDIAN_BRIDGES:
        regression_mismatches.append(f"bridges: {audit['antimeridian_bridges_added']} != {EXPECTED_ANTIMERIDIAN_BRIDGES}")
    if audit["final_node_count"] != EXPECTED_NODE_COUNT:
        regression_mismatches.append(f"nodes: {audit['final_node_count']} != {EXPECTED_NODE_COUNT}")
    if audit["final_edge_count"] != EXPECTED_EDGE_COUNT_EXCLUDING_LOOPS:
        regression_mismatches.append(f"edges: {audit['final_edge_count']} != {EXPECTED_EDGE_COUNT_EXCLUDING_LOOPS}")

    if regression_mismatches:
        logging.warning(
            "Regression notice: Source output differs from commit 88a2e568 expectations (%s)",
            ", ".join(regression_mismatches),
        )
    else:
        logging.info("REGRESSION VERIFIED: All graph counts match commit 88a2e568 benchmark bit-for-bit.")

    # Export graph artifact and manifest
    export_graph_artifact(graph_data, network_output_path)
    write_manifest(cache_dir, graph_data["metadata"])

    logging.info("SUCCESS: Graph ingestion completed successfully.")
    logging.info("Output artifact: %s (%d bytes)", network_output_path, network_output_path.stat().st_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
