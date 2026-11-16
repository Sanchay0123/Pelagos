"""Pelagos test suite configuration.

Safeguards against accidental mutations to production database (FINDING-P2-006):
- Deferral of init_db() to FastAPI lifespan prevents import-time DDL schema mutations.
- Sets DATABASE_URL to in-memory SQLite default.
"""

import os
import sys

# Default DATABASE_URL for tests
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

# Ensure backend directory is in python path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

