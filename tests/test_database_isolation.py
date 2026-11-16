"""tests/test_database_isolation.py
Verification of FINDING-P2-006: Test Environment Database Isolation.
Ensures that importing application modules, FastAPI routes, and running tests
does not alter, connect to, or write schema alterations to production pelagos.db.
"""

import os
import sys
import hashlib
from pathlib import Path
import pytest


def test_test_environment_database_isolation():
    """Verify that DATABASE_URL defaults to in-memory during tests."""
    assert os.environ.get("DATABASE_URL") == "sqlite:///:memory:"


def test_import_api_main_does_not_mutate_production_db():
    """
    Verify that importing pelagos.api.main does not invoke init_db()
    at module level or modify the production database file.
    """
    db_path = Path("pelagos.db")
    if not db_path.exists():
        pytest.skip("Production pelagos.db not present in working tree.")

    initial_size = db_path.stat().st_size
    initial_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()

    # Re-import or inspect pelagos.api.main
    import pelagos.api.main

    # Ensure app is created but lifespan was not triggered
    assert hasattr(pelagos.api.main, "app")
    assert hasattr(pelagos.api.main, "lifespan")

    current_size = db_path.stat().st_size
    current_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()

    assert current_size == initial_size, f"pelagos.db size changed from {initial_size} to {current_size}"
    assert current_hash == initial_hash, "pelagos.db SHA-256 hash changed after importing pelagos.api.main"
