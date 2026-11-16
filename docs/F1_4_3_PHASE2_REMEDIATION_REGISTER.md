# Stage F1.4.3 Phase 2 Remediation Register

**Document ID:** `REGISTER-F143-PHASE2-001`  
**Parent Audit:** `AUDIT-F143-PHASE2-001`  
**Governing Baseline:** `fdd70b838687366cbf74081c78ecde78990fe7bc`  
**Stage:** F1.4.3 Phase 2 Post-Implementation Independent Audit  
**Status:** Formally Remediated & Closed (`REPORT-F143-PHASE2-REMED-001`)  

---

## 1. Prioritized Findings Summary

| Finding ID | Severity | Affected Module | Component | Blocks Phase 2 Closure | Status | Resolution Reference |
|---|:---:|---|---|:---:|:---:|:---:|
| `FINDING-P2-001` | **MEDIUM** | `backend/pelagos/readiness/fixture_validation.py` | P2-A: Fixture Validation | **NO** | **RESOLVED** | `REPORT-F143-PHASE2-REMED-001` §3.2 |
| `FINDING-P2-002` | **MEDIUM** | `backend/pelagos/database.py` | P2-D: Persistence / Triggers | **NO** | **RESOLVED** | `REPORT-F143-PHASE2-REMED-001` §3.5 |
| `FINDING-P2-003` | **MEDIUM** | `backend/pelagos/security/token_gate.py` | P2-B: Token Gate | **NO** | **RESOLVED** | `REPORT-F143-PHASE2-REMED-001` §3.3 |
| `FINDING-P2-004` | **LOW** | `backend/pelagos/security/merkle.py` | P2-B: Cryptographic Primitives | **NO** | **RESOLVED** | `REPORT-F143-PHASE2-REMED-001` §3.4 |
| `FINDING-P2-005` | **LOW** | `docs/F1_4_3_PHASE2_IMPLEMENTATION_REPORT.md` | Documentation / Test Inventory | **NO** | **RESOLVED** | `RECON-F143-PHASE2-001` / `REPORT-F143-PHASE2-REMED-001` §4 |
| `FINDING-P2-006` | **MEDIUM** | `backend/pelagos/api/main.py` / `tests/conftest.py` | Test Isolation / API Startup | **NO** | **RESOLVED** | `REPORT-F143-PHASE2-REMED-001` §3.1 |

---

## 2. Detailed Findings & Recommended Remediations

### FINDING-P2-001: Uninvoked Vessel Readiness Service in Commercial Fixture Engine
- **Severity:** **MEDIUM**
- **Affected Module:** `backend/pelagos/readiness/fixture_validation.py`
- **Affected Component:** `CommercialFixtureValidationEngine.__init__` (line 189) and `validate_fixture()` (lines 246–446)
- **Evidence:**
  `self.vessel_service = vessel_service or VesselReadinessService(db=db)` is instantiated on line 189. However, `self.vessel_service.evaluate_readiness()` is never called in `validate_fixture()`. Only `self.validate_vessel()` (lines 192–244) is executed.
- **Expected Behavior:** `validate_fixture()` should invoke `self.vessel_service.evaluate_readiness()` to ensure that statutory vessel certificates (SCNT, PC/UMS, class certificates) are formally verified before approving fixture readiness.
- **Actual Behavior:** `validate_vessel()` verifies IMO syntax checksum and queries the database for vessel existence and name consistency, but does not verify statutory certificate validity.
- **Potential Impact:** A vessel with expired or missing canal certificates (e.g. SCNT for Suez transits) would not be flagged with a certificate validation error within `validate_fixture()` itself unless the caller invokes `VesselReadinessService` independently.
- **Why It Does Not Block Phase 2 Closure:** `is_operational_permitted` is strictly hard-locked to `False` in Phase 2 (`is_ready_for_operations = False`), preventing any operational release.
- **Recommended Minimal Remediation:**
  In `validate_fixture()` (around line 273), invoke `self.vessel_service.evaluate_readiness()`:
  ```python
  vessel_readiness = self.vessel_service.evaluate_readiness(
      vessel_imo=payload.vessel_imo,
      canal_transits=[c.upper() for c in payload.canal_transits],
      transit_date=payload.transit_date,
      operational_mode=operational_mode,
  )
  if not vessel_readiness.is_ready_for_evaluation:
      validation_errors.extend(vessel_readiness.blocked_reasons)
  ```

---

### FINDING-P2-002: Scope Limitation — Absence of PostgreSQL Immutability Triggers
- **Severity:** **MEDIUM**
- **Affected Module:** `backend/pelagos/database.py`
- **Affected Component:** `install_disbursement_immutability_triggers()` (lines 347–406)
- **Evidence:**
  Line 349 checks `if eng.dialect.name == "sqlite":` and installs SQLite-specific triggers. If connected to a PostgreSQL database, the function executes no DDL statements and installs no triggers.
- **Expected Behavior:** If PostgreSQL deployment is in scope, equivalent PL/pgSQL trigger functions (`CREATE OR REPLACE FUNCTION ... BEFORE UPDATE OR DELETE ... RAISE EXCEPTION ...`) should be installed on PostgreSQL tables.
- **Actual Behavior:** Storage-level trigger write-protection is operational on SQLite only.
- **Potential Impact:** In a PostgreSQL deployment, the system would rely entirely on application ORM permissions and out-of-band cryptographic verification (`DisbursementAuditVerifier`), without database engine trigger rejection.
- **Why It Does Not Block Phase 2 Closure:** The current governed production database is strictly SQLite (`pelagos.db`). This is a documented scope limitation.
- **Recommended Minimal Remediation:**
  Add a PostgreSQL branch in `install_disbursement_immutability_triggers()`:
  ```python
  elif eng.dialect.name == "postgresql":
      conn.execute(text("""
          CREATE OR REPLACE FUNCTION trg_abort_mutation()
          RETURNS TRIGGER AS $$
          BEGIN
              RAISE EXCEPTION 'IMMUTABILITY VIOLATION: Table % is append-only.', TG_TABLE_NAME;
          END;
          $$ LANGUAGE plpgsql;
      """))
      for tbl in ["voyage_disbursement_records", "voyage_disbursement_line_items", 
                  "consumed_operational_tokens", "voyage_disbursement_audit_log"]:
          conn.execute(text(f"""
              CREATE OR REPLACE TRIGGER trg_{tbl}_immutable
              BEFORE UPDATE OR DELETE ON {tbl}
              FOR EACH ROW EXECUTE FUNCTION trg_abort_mutation();
          """))
  ```

---

### FINDING-P2-003: Unhandled IntegrityError on Concurrent Replay in Token Gate
- **Severity:** **MEDIUM**
- **Affected Module:** `backend/pelagos/security/token_gate.py`
- **Affected Component:** `OperationalUnlockGate.verify_and_consume_token()` (lines 233–268)
- **Evidence:**
  `ConsumedOperationalToken.jti` has a `unique=True` constraint. If two identical tokens are submitted concurrently, both may pass the read check (`existing = db.query(...).first()`). On the subsequent `db.flush()`, SQLite/PostgreSQL raises an `IntegrityError`. `verify_and_consume_token()` does not wrap `db.flush()` in a `try...except IntegrityError`.
- **Expected Behavior:** Under concurrent duplicate submissions, the second submission should catch `IntegrityError` and return a structured `TokenValidationResult(is_valid=False, status="CONCURRENT_REPLAY_DETECTED")`.
- **Actual Behavior:** An unhandled `sqlalchemy.exc.IntegrityError` bubbles up to the caller.
- **Potential Impact:** The system remains strictly fail-closed (the replayed token is never granted authorization, because the transaction fails). However, the failure manifests as an unhandled 500-level database exception rather than a formatted domain response.
- **Why It Does Not Block Phase 2 Closure:** Fail-closed security is preserved; the token is never consumed twice or authorized.
- **Recommended Minimal Remediation:**
  In `verify_and_consume_token()` (lines 266–268):
  ```python
  try:
      db.flush()
  except IntegrityError:
      db.rollback()
      return TokenValidationResult(
          is_valid=False,
          status="CONCURRENT_REPLAY_DETECTED",
          jti=token.jti,
          token_category=token.token_category.value,
          failure_reasons=[f"CONCURRENT_REPLAY: Token JTI '{token.jti}' consumed concurrently."],
      )
  ```

---

### FINDING-P2-004: RFC 6962 Conformance Distinction (Merkle Multi-Set vs Log Tree)
- **Severity:** **LOW**
- **Affected Module:** `backend/pelagos/security/merkle.py`
- **Affected Component:** `RecordSetMerkleTree`
- **Evidence:**
  `RecordSetMerkleTree` sorts leaf hashes lexicographically before building the tree (`self.leaf_hashes = sorted(leaves)`). In RFC 6962 Certificate Transparency, leaves preserve log arrival sequence without sorting.
- **Expected Behavior:** Architectural documentation and docstrings should accurately characterize the tree as an RFC 6962-Compatible Merkle Set Aggregator (Merkle Multi-Set Hash) rather than an RFC 6962 CT log tree.
- **Actual Behavior:** The implementation uses RFC 6962 §2.1 binary split algorithms and domain separation prefixes (`0x00`, `0x01`), but adds a pre-construction sort step to achieve order invariance.
- **Potential Impact:** Conceptual ambiguity regarding RFC 6962 conformance. No functional or security impact.
- **Why It Does Not Block Phase 2 Closure:** The order-invariance property is an intentional architectural requirement for line item aggregation.
- **Recommended Minimal Remediation:**
  Update the module docstring in `backend/pelagos/security/merkle.py` to clarify that `RecordSetMerkleTree` is an "RFC 6962-Compatible Merkle Multi-Set Hash".

---

### FINDING-P2-005: Predecessor Test Inventory Scope Documentation Discrepancy
- **Severity:** **LOW**
- **Affected Module:** `docs/F1_4_3_PHASE2_IMPLEMENTATION_REPORT.md`, `VERIFY-F143-PHASE2-001`
- **Affected Component:** Test Inventory Reporting
- **Evidence:**
  Previous Phase 2 reports referenced 73 predecessor regression tests as the "predecessor regression suite". The earlier Phase 1 verification recorded an expanded 162-test platform inventory.
- **Expected Behavior:** Documentation should clearly distinguish the 73-test focused disbursement/readiness subset from the 162-test full multi-domain platform suite.
- **Actual Behavior:** Both suites exist and pass 100% (73/73 and 162/162), but the relationship was not explicitly cross-referenced.
- **Potential Impact:** Minor reporting ambiguity regarding test inventory naming.
- **Why It Does Not Block Phase 2 Closure:** Reconciled and documented in full in `docs/F1_4_3_PHASE2_TEST_RECONCILIATION.md`.
- **Recommended Minimal Remediation:**
  Closed via `RECON-F143-PHASE2-001`.

### FINDING-P2-006: Top-Level API Module Database Initialization During Test Imports
- **Severity:** **MEDIUM**
- **Affected Module:** `backend/pelagos/api/main.py:16` and `tests/conftest.py`
- **Affected Component:** Test Harness Isolation & Fast API Startup
- **Evidence:**
  `backend/pelagos/api/main.py` invokes `init_db()` at top-level module scope upon import. Tests that import the API router (e.g. `tests/test_voyage_calculator.py`, `tests/test_charter_intelligence.py`) trigger `init_db()` against the default `DATABASE_URL` (`sqlite:///./pelagos.db`) because `tests/conftest.py` does not set a test database environment variable. This caused `ensure_schema_compatibility()` to allocate empty Phase 2 tables in `pelagos.db`, changing file size from 1,818,624 to 1,875,968 bytes. Zero existing rows were modified or deleted; all original operational row counts remain identical.
- **Expected Behavior:** Test suites should execute in strict isolation from production database files (`pelagos.db`). `conftest.py` should redirect `DATABASE_URL` to an in-memory database (`sqlite:///:memory:`). `api/main.py` should invoke `init_db()` inside a lifespan context manager rather than at module import.
- **Actual Behavior:** Importing `pelagos.api.main` executes DDL against `pelagos.db` if `DATABASE_URL` is unset.
- **Potential Impact:** Unintended schema synchronization or empty table allocation in `pelagos.db` during test runs that import API routes.
- **Why It Does Not Block Phase 2 Closure:** No operational data rows were mutated, deleted, or corrupted; `is_ready_for_operations = False` remains locked.
- **Recommended Minimal Remediation:**
  1. In `tests/conftest.py`:
     ```python
     os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
     ```
  2. In `backend/pelagos/api/main.py`:
     Replace top-level `init_db()` with a FastAPI `lifespan` handler:
     ```python
     @asynccontextmanager
     async def lifespan(app: FastAPI):
         init_db()
         yield
     app = FastAPI(lifespan=lifespan, ...)
     ```

---

## 3. Governance & Remediation Authorization Notice

All findings P2-001 through P2-006 were authorized for targeted remediation under Task `REMED-F143-PHASE2-001`.
- **All 6 findings are fully resolved and verified.**
- Formal closure and verification evidence is documented in `docs/F1_4_3_PHASE2_REMEDIATION_REPORT.md` (`REPORT-F143-PHASE2-REMED-001`).
- The production database was verified 100% intact (size and SHA-256 identical).
- Level 3 operational activation remains locked (`is_ready_for_operations = False`).
