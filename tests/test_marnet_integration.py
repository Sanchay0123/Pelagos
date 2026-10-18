"""Integration test suite for MARNET real dataset ingestion and regression verification.

This suite is decoupled from offline unit testing and is opt-in.
To execute, set the environment variable:
    PELAGOS_MARNET_INTEGRATION=1

Clean repository checkouts without external MARNET assets will safely skip this test.
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from pelagos.routing.ingestion import (
    DEFAULT_CACHE_DIR,
    EXPECTED_ANTIMERIDIAN_BRIDGES,
    EXPECTED_EDGE_COUNT_EXCLUDING_LOOPS,
    EXPECTED_FEATURES_COUNT,
    EXPECTED_MARNET_SHA256,
    EXPECTED_NODE_COUNT,
    EXPECTED_PARALLEL_PAIRS,
    EXPECTED_SELF_LOOPS,
    MarnetGraphBuilder,
    compute_sha256,
)


@pytest.mark.skipif(
    not os.getenv("PELAGOS_MARNET_INTEGRATION"),
    reason="Full MARNET dataset integration test is opt-in. Set PELAGOS_MARNET_INTEGRATION=1 to run.",
)
class TestMarnetFullDatasetIntegration:
    """Verifies the exact topology regression counts against the real Eurostat MARNET asset."""

    def test_marnet_50km_inspected_revision_counts(self) -> None:
        """Verify that when the inspected MARNET GeoPackage is present in cache or scratch,
        it reproduces the audited D9.3 benchmark counts bit-for-bit.
        """
        candidate_paths = [
            DEFAULT_CACHE_DIR / "marnet_plus_50km.gpkg",
            Path(r"C:\Users\Lenovo\.gemini\antigravity-ide\brain\bad29c20-1cc9-4447-a8bf-6dc6999e9496\scratch\marnet_plus_50km.gpkg"),
        ]
        gpkg_path = next((p for p in candidate_paths if p.exists()), None)
        if not gpkg_path:
            pytest.skip("Inspected MARNET GeoPackage not found in cache or scratch")

        # Verify exact SHA-256
        assert compute_sha256(gpkg_path) == EXPECTED_MARNET_SHA256

        # Deterministic graph construction
        builder = MarnetGraphBuilder()
        graph_data = builder.build_from_geopackage(gpkg_path, exclude_self_loops=True)
        audit = graph_data["audit_summary"]

        # Regression benchmark comparison (Commit 88a2e568)
        assert audit["total_input_features"] == EXPECTED_FEATURES_COUNT
        assert audit["self_loops_count"] == EXPECTED_SELF_LOOPS
        assert audit["parallel_edge_groups"] == EXPECTED_PARALLEL_PAIRS
        assert audit["duplicate_edges_collapsed"] == EXPECTED_PARALLEL_PAIRS
        assert audit["antimeridian_bridges_added"] == EXPECTED_ANTIMERIDIAN_BRIDGES
        assert audit["final_node_count"] == EXPECTED_NODE_COUNT
        assert audit["final_edge_count"] == EXPECTED_EDGE_COUNT_EXCLUDING_LOOPS
        assert audit["connected_components_count"] == 1
        assert audit["is_fully_connected"] is True
