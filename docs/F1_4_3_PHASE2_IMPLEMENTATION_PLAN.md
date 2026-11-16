# Pelagos — Stage F1.4.3 Phase 2 Implementation Plan (Revision 3)
## Work Breakdown Structure, Engineering Sequence, Testing Strategy & Verification Gates

**Document ID:** `PLAN-F143-PHASE2-001`  
**Revision:** Revision 3 (`DESIGN-F143-PHASE2-REV-003`)  
**Stage:** F1.4.3 Phase 2 (Implementation Planning & Final Clarifications)  
**Governing Architecture:** `DOC-F143-PHASE2-ARCH-001` (Rev 3, `docs/F1_4_3_PHASE2_ARCHITECTURE.md`)  
**Published Baseline Commit:** `fdd70b838687366cbf74081c78ecde78990fe7bc`  
**Status:** Approved Implementation Plan — Code Modifications Strictly Prohibited Until Project Owner Authorization  

---

## 1. Executive Summary & Governance Envelope

This implementation plan translates the clarified architectural requirements of `DOC-F143-PHASE2-ARCH-001` (Revision 3) into five sequential, bounded engineering increments. Each increment specifies exact file targets, database migrations, data contracts, failure modes, acceptance criteria, and explicit exclusions.

### 1.1 Strict Governance Invariants
- **Implementation Freeze:** This document establishes the implementation plan only. No source code, database models, migrations, or operational release tokens may be created or configured during Phase 2.0.
- **Fail-Closed Operational Invariant:** All implementations must enforce `is_ready_for_operations = False` and `readiness_tier = LEVEL_2_RESEARCH_ONLY`.
- **Zero Inferred Data:** No synthetic vessel particulars, benchmark FX substitutions, or unverified commercial allowances may be introduced.
- **Token Category Segregation:** Phase 2 implements Category 1 Development/Test Tokens and Category 2 Non-Operational Authorization Simulations only. Category 3 Production Operational Release Tokens are strictly prohibited.
- **Ordered Dependency Progression:** Increments execute in strict sequence: P2-A $\to$ P2-B $\to$ P2-D $\to$ P2-C $\to$ P2-ALL.
- **Independent Verification Gate:** Each increment requires independent verification sign-off before downstream code can be executed.

---

## 2. Work Breakdown Structure (WBS) & Sequencing

```
[Phase 2 Sequential Implementation Roadmap]
┌─────────────────────────────────────────────────────────────────────────────┐
│ Increment 2.1: Commercial Fixture Validation Engine (P2-A Core)             │
│ - Validate IMO syntax vs. authoritative vessel identity vs. certificates    │
│ - Retain MMSI as dynamic temporal communications identifier                 │
│ - Four-tier charge decomposition & bilateral contract attribution           │
│ - One-way statutory floor enforcement & unallocated overage quarantine      │
│ - Three-tier commission governance (Contractual, Research, Missing)         │
│ - 7-component voyage duration input contract & laytime parameterization     │
├─────────────────────────────────────────────────────────────────────────────┤
│ Increment 2.2: Cryptographic Authorization Primitives (P2-B Core)           │
│ - RFC 8785 canonical JSON serializer for field-specific Decimal strings     │
│ - Rejection of IEEE 754 floating-point numbers in signed financial payloads │
│ - RFC 6962-compatible Merkle tree (0x00 leaf, 0x01 node, sorted leaves)     │
│ - Candidate signature verifiers: Ed25519 (default) & ECDSA P-384 (fallback) │
│ - Token engine supporting Dev/Test and Simulation tokens (≤1h lifespan)     │
│ - Consumed-token anti-replay table & transactional ledger                   │
│ - Offline revocation handling & 2-hour CRL freshness fail-closed gate       │
│ - STRICT EXCLUSION: Zero production release tokens or production keys       │
├─────────────────────────────────────────────────────────────────────────────┤
│ Increment 2.3: Tamper-Evident Audit Ledger & Immutability (P2-D Core)        │
│ - Multi-tiered immutability model (App rules, DB triggers, Cryptography)    │
│ - Explicit acknowledgment that SQLite triggers are not admin-proof          │
│ - voyage_disbursement_audit_log table & SHA-256 hash chaining (0x02 prefix) │
│ - Sequential monotonic order: Chronological audit events NEVER sorted!      │
│ - SQLite & PostgreSQL write-protection triggers (BEFORE UPDATE / DELETE)    │
│ - DisbursementAuditVerifier service for automated out-of-band audit scans   │
├─────────────────────────────────────────────────────────────────────────────┤
│ Increment 2.4: Voyage Economics Integration & Decoupling (P2-C Core)        │
│ - Decouple Gross Ocean Freight, Net Revenue, Conventional TCE, & Profitability│
│ - Invariant: Conventional voyage TCE does NOT deduct OPEX or capital debt   │
│ - Owner Operating Profitability modeled as separate downstream calculation  │
│ - 7-component voyage duration denominator; laytime impacts net revenue      │
│ - Persisted disbursement retrieval & Merkle root integrity verification     │
│ - Multi-currency reconciliation & provider lookback validation              │
│ - Token-gated Level 3 operational execution boundary (simulation only)      │
├─────────────────────────────────────────────────────────────────────────────┤
│ Increment 2.5: Integrated Security, Verification & Phase 2 Gate Closure     │
│ - Comprehensive adversarial test suite execution (10 attack scenarios)      │
│ - Full regression test run across all predecessor platforms (162+ tests)    │
│ - Issue formal verification gate report (VERIFY-F143-PHASE2-001)            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Detailed Increment Specifications

### 3.1 Task Increment 2.1: Commercial Fixture Validation Engine (P2-A Core)

#### 3.1.1 Objective & Scope
Implement the formal validation boundary for external commercial fixtures, parsing caller inputs into the four-tier taxonomy, separating IMO syntax from authoritative vessel identity and certificate verification, enforcing one-way statutory floors, segregating commercial commissions, and capturing the 7-component voyage duration contract.

#### 3.1.2 Dependencies
- Predecessors: Phase 1 Readiness Foundation (`ChargeClassifier`, `TariffValidationService`, `VesselReadinessService`).

#### 3.1.3 Target Source Files
- `backend/pelagos/readiness/__init__.py`
- `backend/pelagos/readiness/fixture_validation.py` (New module)
- `backend/pelagos/readiness/schemas.py` (Additive schemas)

#### 3.1.4 Data Contracts & Schemas
```python
class DurationStatus(str, Enum):
    ESTIMATED_ROUTED = "ESTIMATED_ROUTED"
    CONTRACTUAL_AGREED = "CONTRACTUAL_AGREED"
    OBSERVED_ACTUAL = "OBSERVED_ACTUAL"

class VoyageDurationInput(BaseModel):
    laden_sea_days: Decimal = Field(..., gt=0)
    ballast_sea_days: Decimal = Field(default=Decimal("0.00"), ge=0)
    loading_days: Decimal = Field(..., gt=0)
    discharge_days: Decimal = Field(..., gt=0)
    port_waiting_days: Decimal = Field(default=Decimal("0.00"), ge=0)
    canal_waiting_days: Decimal = Field(default=Decimal("0.00"), ge=0)
    canal_transit_days: Decimal = Field(default=Decimal("0.00"), ge=0)
    duration_status: DurationStatus = DurationStatus.ESTIMATED_ROUTED
    contractual_laytime_days: Optional[Decimal] = Field(default=None, gt=0)
    demurrage_rate_usd_per_day: Optional[Decimal] = Field(default=None, ge=0)
    despatch_rate_usd_per_day: Optional[Decimal] = Field(default=None, ge=0)

    @property
    def total_voyage_days(self) -> Decimal:
        return (
            self.laden_sea_days
            + self.ballast_sea_days
            + self.loading_days
            + self.discharge_days
            + self.port_waiting_days
            + self.canal_waiting_days
            + self.canal_transit_days
        )

class CommercialFixturePayload(BaseModel):
    fixture_id: str = Field(..., min_length=3, max_length=64)
    vessel_imo: int = Field(..., description="7-digit IMO validated by syntax checksum")
    mmsi: Optional[str] = Field(default=None, description="Dynamic temporal communications identifier")
    vessel_name: Optional[str] = None
    origin_port_unlocode: str = Field(..., regex=r"^[A-Z]{5}$")
    destination_port_unlocode: str = Field(..., regex=r"^[A-Z]{5}$")
    intermediate_ports: List[str] = Field(default_factory=list)
    canal_transits: List[str] = Field(default_factory=list)
    laycan_start: date
    laycan_end: date
    transit_date: date
    cargo_tonnes: Decimal = Field(..., gt=0)
    cargo_commodity: str = Field(default="Agricultural Bulk Grain")
    
    # Financial allowances supplied by caller
    total_port_allowance_usd: Optional[Decimal] = None
    total_canal_allowance_usd: Optional[Decimal] = None
    itemized_charges: List[FixtureLineItemInput] = Field(default_factory=list)
    
    # Commission Governance
    commission_tier: CommissionGovernanceTier = CommissionGovernanceTier.MISSING
    contractual_commission_rate: Optional[Decimal] = Field(default=None, ge=0, le=Decimal("0.20"))
    commission_contract_id: Optional[str] = None
    user_research_commission_rate: Optional[Decimal] = Field(default=None, ge=0, le=Decimal("0.20"))
    
    # 7-Component Duration Accounting
    duration_input: VoyageDurationInput
    
    # Source provenance qualification
    source_organization: str = Field(..., min_length=2, max_length=128)
    broker_reference: Optional[str] = None
    provenance_classification: str = Field(default="SOURCE_REPORTED")
```

#### 3.1.5 Target Test Files
- `tests/test_commercial_fixture_validation.py`
  - `test_imo_syntax_vs_identity_separation`: Valid IMO checksum with non-existent registry record returns `SYNTAX_VALID` but `IDENTITY_UNMATCHED`.
  - `test_sub_floor_fixture_rejection`: Total fixture allowance below statutory floor fails closed with `FIXTURE_VALUATION_BELOW_STATUTORY_FLOOR`.
  - `test_exact_floor_match`: Exact statutory floor match passes with zero unallocated surplus.
  - `test_above_floor_unallocated_overage_quarantine`: Unallocated surplus above floor is quarantined and barred from operational clearance.
  - `test_commission_governance_tiers`: Contractual commission permitted for operational mode; research assumption watermarked and barred from operational mode; missing commission fails closed in operational mode.
  - `test_duration_breakdown_validation`: 7 duration components sum exactly to total voyage days; missing duration components fail closed.
  - `test_tier3_missing_contract_attribution`: Agency fee without contract attribution flagged as `MISSING_CONTRACTUAL_EVIDENCE`.

#### 3.1.6 Acceptance Criteria
- [ ] Passing IMO checksum does not automatically grant vessel identity verification.
- [ ] Sub-floor fixtures fail closed immediately with zero tolerance.
- [ ] Unallocated fixture overages are quarantined with `UNALLOCATED_COMMERCIAL_OVERAGE`.
- [ ] Zero implicit commission defaults (no automatic 3.75% default).
- [ ] Duration input strictly decomposes into 7 components; missing durations fail closed.

#### 3.1.7 Explicit Exclusions
- Does not connect to live commercial broker APIs.
- Does not modify existing `VerifiedCharterInputs` schema (maintains backward compatibility).

---

### 3.2 Task Increment 2.2: Cryptographic Authorization Primitives (P2-B Core)

#### 3.2.1 Objective & Scope
Implement mathematical and cryptographic primitives for RFC 8785 canonical JSON serialization with exact Decimal representation, RFC 6962-compatible SHA-256 Merkle tree aggregation with byte-level domain separation (`0x00`/`0x01`), candidate signature verifiers (Ed25519 & ECDSA P-384), operational token validation (Category 1 & Category 2 tokens only), single-use anti-replay ledgers, and offline CRL fail-closed governance.

#### 3.2.2 Dependencies
- Predecessor: Increment 2.1 (P2-A Core).

#### 3.2.3 Target Source Files
- `backend/pelagos/security/__init__.py`
- `backend/pelagos/security/canonical_json.py`
- `backend/pelagos/security/merkle.py`
- `backend/pelagos/security/signature_engine.py`
- `backend/pelagos/security/token_gate.py`

#### 3.2.4 Database Changes & Migration Requirements
- Migration Script: `versions/f143_p2_001_consumed_tokens.py`
- Table: `consumed_operational_tokens`
  ```sql
  CREATE TABLE consumed_operational_tokens (
      jti VARCHAR(64) PRIMARY KEY,
      consumed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
      voyage_id VARCHAR(64) NOT NULL,
      vessel_imo INTEGER NOT NULL,
      disbursement_record_id INTEGER NOT NULL,
      caller_identity VARCHAR(128) NOT NULL,
      signature_hash VARCHAR(64) NOT NULL
  );
  CREATE INDEX idx_consumed_tokens_voyage ON consumed_operational_tokens(voyage_id);
  ```

#### 3.2.5 Target Test Files
- `tests/test_canonical_json.py`: Tests RFC 8785 compliance, field-specific Decimal monetary string normalization (`"123.40"`, `"1.345600"`), rejection of scientific notation, negative zero normalization, and rejection of `NaN`/`Infinity`.
- `tests/test_merkle_engine.py`: Tests RFC 6962 byte-level domain separation (`0x00` leaf, `0x01` node), lexicographical leaf ordering, empty tree constant, odd-node elevation without leaf duplication, and inclusion proof verification.
- `tests/test_signature_engine.py`: Tests Ed25519 and ECDSA P-384 signature verification, key rotation expiry, and algorithm downgrade rejection (`alg: "none"`, `alg: "HS256"`).
- `tests/test_token_gate.py`: Tests token lifespan validation ($\le 3600\text{s}$), clock skew tolerance (30s), contextual binding checks, and single-use replay rejection via `consumed_operational_tokens`.
- `tests/test_offline_crl_governance.py`: Tests offline CRL cache freshness across the 5 timeline scenarios; verifies that cache age $> 2\text{ hours}$ triggers fail-closed `SECURITY_REVOCATION_STATUS_UNKNOWN`.

#### 3.2.6 Acceptance Criteria
- [ ] Financial values are serialized strictly as canonical Decimal strings matching authorized field precision.
- [ ] Merkle root calculation implements exact RFC 6962 construction with byte-level domain separation.
- [ ] Replayed tokens fail closed with `REPLAYED_OPERATIONAL_TOKEN_REJECTED`.
- [ ] Tokens exceeding 1-hour lifespan fail closed with `INVALID_TOKEN_LIFESPAN`.
- [ ] Stale CRL cache ($> 2\text{ hours}$) in offline mode fails closed with `SECURITY_REVOCATION_STATUS_UNKNOWN`.
- [ ] Zero production release tokens or production signing keys are generated or configured.

#### 3.2.7 Explicit Exclusions
- Category 3 Production Operational Release Tokens are strictly barred.
- Production private signing keys and production HSM credentials are completely excluded.

---

### 3.3 Task Increment 2.3: Tamper-Evident Audit Ledger & Immutability (P2-D Core)

#### 3.3.1 Objective & Scope
Establish multi-tiered storage immutability, append-only SHA-256 hash-chained audit logging (strictly chronological, never sorted lexicographically), database write-protection triggers, and automated out-of-band audit verification.

#### 3.3.2 Dependencies
- Predecessors: Increment 2.1 (P2-A Core), Increment 2.2 (P2-B Core).

#### 3.3.3 Target Source Files
- `backend/pelagos/audit/__init__.py`
- `backend/pelagos/audit/audit_ledger.py`
- `backend/pelagos/audit/audit_verifier.py`
- `backend/pelagos/models.py` (Additive entity `VoyageDisbursementAuditLog`)
- `backend/pelagos/database.py` (Additive trigger setup functions)

#### 3.3.4 Database Changes & Migration Requirements
- Migration Script: `versions/f143_p2_002_audit_ledger.py`
- Table: `voyage_disbursement_audit_log`
  ```sql
  CREATE TABLE voyage_disbursement_audit_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sequence_number BIGINT NOT NULL UNIQUE,
      record_id INTEGER NOT NULL REFERENCES voyage_disbursement_records(id),
      voyage_id VARCHAR(64) NOT NULL,
      vessel_imo INTEGER NOT NULL,
      event_type VARCHAR(32) NOT NULL,
      merkle_root VARCHAR(64) NOT NULL,
      record_payload_hash VARCHAR(64) NOT NULL,
      attestation_signature TEXT NOT NULL,
      signing_key_id VARCHAR(64) NOT NULL,
      previous_chain_hash VARCHAR(64) NOT NULL,
      chain_hash VARCHAR(64) NOT NULL,
      created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
  CREATE INDEX idx_vydisb_audit_seq ON voyage_disbursement_audit_log(sequence_number);
  ```
- Storage Triggers:
  - SQLite & PostgreSQL `BEFORE UPDATE` and `BEFORE DELETE` triggers on `voyage_disbursement_records`, `voyage_disbursement_line_items`, and `consumed_operational_tokens`.

#### 3.3.5 Target Test Files
- `tests/test_audit_ledger.py`
  - `test_hash_chain_sequential_integrity`: Verifies unbroken SHA-256 hash chaining back to genesis with domain tag `0x02`.
  - `test_audit_events_are_chronological_not_sorted`: Verifies that audit events strictly follow monotonic sequence order.
  - `test_sqlite_triggers_block_application_mutation`: Verifies SQLite trigger aborts attempted standard `UPDATE` or `DELETE`.
  - `test_tamper_detection_on_line_item_modification`: Recomputes Merkle root and payload hash; detects altered amount and raises `TAMPER_DETECTED_RECORD_CORRUPTED`.
  - `test_tamper_detection_on_audit_chain_break`: Detects deleted or out-of-order audit entries and raises `TAMPER_DETECTED_CHAIN_BROKEN`.

#### 3.3.6 Acceptance Criteria
- [ ] Database triggers block application-level updates and deletions.
- [ ] Documentation explicitly records that database triggers do not constitute administrator-proof immutability.
- [ ] Audit log maintains unbroken sequential SHA-256 chain hash ordered by monotonic sequence number.
- [ ] Automated verifier successfully detects simulated disk and column tampering.

#### 3.3.7 Explicit Exclusions
- Does not implement remote WORM / S3 Object Lock integration (modeled as external custody interface).

---

### 3.4 Task Increment 2.4: Voyage Economics Integration & Decoupling (P2-C Core)

#### 3.4.1 Objective & Scope
Implement the decoupled voyage economics service, strictly separating Gross Ocean Freight, Net Voyage Revenue, conventional Voyage TCE, and Owner Operating Profitability. Enforce token authorization (simulations only), immutable disbursement retrieval, 7-component duration denominator accounting, and multi-currency reconciliation.

#### 3.4.2 Dependencies
- Predecessors: Increment 2.1 (P2-A Core), Increment 2.2 (P2-B Core), Increment 2.3 (P2-D Core).

#### 3.4.3 Target Source Files
- `backend/pelagos/optimization/voyage_economics.py` (New module)
- `backend/pelagos/optimization/calculator.py` (Keep gross ocean freight calculation isolated)
- `backend/pelagos/optimization/chartering.py` (Consume verified fixture payloads)
- `backend/pelagos/api/routes/calculator.py` (Expose net economics endpoints)

#### 3.4.4 Key Output Contracts & Interfaces
```python
class GrossOceanFreightResult(BaseModel):
    status: str
    cargo_tonnes: Decimal
    freight_rate_usd_per_mt: Decimal
    gross_freight_usd: Decimal
    calculation_formula: str = "gross_freight_usd = cargo_tonnes * freight_rate_usd_per_mt"
    disclaimers: List[str]

class NetVoyageRevenueResult(BaseModel):
    status: str
    gross_freight_usd: Decimal
    commission_deductions_usd: Decimal
    port_disbursements_usd: Decimal
    canal_transits_usd: Decimal
    bunker_fuel_costs_usd: Decimal
    demurrage_despatch_usd: Decimal
    net_voyage_revenue_usd: Decimal
    commission_governance: str

class VoyageTCEResult(BaseModel):
    status: str
    net_voyage_revenue_usd: Decimal
    duration_breakdown: VoyageDurationInput
    total_voyage_days: Decimal
    tce_daily_rate_usd: Decimal
    calculation_formula: str = "tce_daily_rate_usd = net_voyage_revenue_usd / total_voyage_days"
    is_operational_approved: bool
    governance_tier: str
    disclaimers: List[str]

class OwnerOperatingProfitabilityResult(BaseModel):
    status: str
    tce_daily_rate_usd: Decimal
    vessel_daily_opex_usd: Decimal
    vessel_daily_capex_usd: Decimal
    owner_daily_net_margin_usd: Decimal
    owner_voyage_profit_usd: Decimal
    calculation_formula: str = "daily_net_margin = tce_daily_rate - opex_daily - capex_daily"
```

#### 3.4.5 Target Test Files
- `tests/test_voyage_economics.py`
  - `test_gross_freight_isolation`: Gross ocean freight remains completely independent of disbursements and bunker costs.
  - `test_conventional_tce_does_not_deduct_opex`: Verifies that conventional Voyage TCE does NOT subtract vessel OPEX or capital debt.
  - `test_owner_profitability_as_separate_model`: Verifies that Owner Operating Profitability correctly deducts OPEX and CapEx downstream.
  - `test_laytime_impact_on_numerator_and_denominator`: Verifies demurrage adds to Net Revenue while actual port working days enter the duration denominator.
  - `test_operational_mode_locked_without_token`: Verifies that `operational_mode = True` fails closed without valid release token.
  - `test_currency_lookback_verification`: Rejects disbursements using stale exchange rates exceeding provider limits (IMF 4d, ECB 4d, MAS 5d).
  - `test_partial_component_rejection`: Any partial tariff component blocks operational TCE clearance.

#### 3.4.6 Acceptance Criteria
- [ ] Gross Ocean Freight, Net Revenue, Conventional TCE, and Owner Profitability emit distinct schemas.
- [ ] Conventional Voyage TCE never deducts vessel OPEX or capital costs.
- [ ] Total voyage duration strictly accounts for all 7 components.
- [ ] Level 3 operational mode fails closed with `LEVEL_3_OPERATIONAL_BLOCKED` without valid release token.
- [ ] Disbursed line items with stale central bank FX observations fail closed.

#### 3.4.7 Explicit Exclusions
- Does not enable operational commercial bidding or automatic charterparty execution.

---

### 3.5 Task Increment 2.5: Integrated Security & Verification Gate (P2-ALL)

#### 3.5.1 Objective & Scope
Execute end-to-end integration tests, 10 adversarial security attacks, and complete regression test runs across all predecessor domains, issuing the formal Phase 2 verification gate report.

#### 3.5.2 Dependencies
- Predecessors: All prior increments (2.1 through 2.4).

#### 3.5.3 Adversarial Attack Test Matrix (10 Scenarios)

| # | Attack Vector | Injection Point | Expected Fail-Closed Result |
|---|---|---|---|
| 1 | **Token Replay Attack** | Submit previously consumed `jti` | `REPLAYED_OPERATIONAL_TOKEN_REJECTED` |
| 2 | **Context Tampering** | Mutate `vessel_imo` in token claims | `SECURITY_BINDING_MISMATCH` |
| 3 | **Expired Token** | Submit token with $\text{now} > \text{exp}$ | `EXPIRED_OPERATIONAL_TOKEN` |
| 4 | **Excessive Lifespan** | Submit token with $\text{exp} - \text{iat} = 7200\text{s}$ | `INVALID_TOKEN_LIFESPAN` |
| 5 | **Direct Database Tampering** | Directly modify `amount_usd` in SQLite file | `TAMPER_DETECTED_RECORD_CORRUPTED` |
| 6 | **Audit Chain Deletion** | Delete middle record from audit log | `TAMPER_DETECTED_CHAIN_BROKEN` |
| 7 | **Sub-Floor Fixture Bypass** | Submit fixture \$50k below statutory canal toll | `FIXTURE_VALUATION_BELOW_STATUTORY_FLOOR` |
| 8 | **Unallocated Surplus Smuggling** | Submit above-floor fixture with no line items | `UNALLOCATED_COMMERCIAL_OVERAGE` quarantine |
| 9 | **Offline Stale CRL Attack** | Disconnect network with CRL cache age $> 2\text{h}$ | `SECURITY_REVOCATION_STATUS_UNKNOWN` |
| 10 | **Algorithm Downgrade Attack** | Submit token with `alg: "none"` or `alg: "HS256"` | `SECURITY_UNAPPROVED_ALGORITHM` |

#### 3.5.4 Regression Suite Reproduction
Execute the full platform test suite across all predecessor domains:
- Currency conversion & central bank lookbacks (8 tests)
- Port & canal tariff registries (35 tests)
- Vessel particulars & engine SFOC curves (40 tests)
- Voyage readiness & four-tier charge classification (45 tests)
- Full platform regression suite (162+ tests)

---

## 4. Phase 2 Boundary & Transition to Phase 3

```
[Phase 2.0: Architecture & Planning] (COMPLETED - Awaiting PO Approval)
├── DOC-F143-PHASE2-ARCH-001 (Rev 3)
├── PLAN-F143-PHASE2-001 (Rev 3)
└── docs/INDEX.md (v1.0.43)
            │
            ▼ [AWAIT EXPLICIT PROJECT OWNER APPROVAL]
[Phase 2 Implementation]
├── Increment 2.1: Commercial Fixture Validation Engine (P2-A Core)
├── Increment 2.2: Cryptographic Authorization Primitives (P2-B Core)
├── Increment 2.3: Tamper-Evident Audit Ledger & Immutability (P2-D Core)
├── Increment 2.4: Voyage Economics Integration & Decoupling (P2-C Core)
└── Increment 2.5: Integrated Security & Verification Gate (P2-ALL)
            │
            ▼ [AWAIT PHASE 2 GATE SIGN-OFF]
[Phase 3: Operational Settlement Gate & Release]
├── Multi-party independent audit review
├── Production Key Ceremony & HSM Configuration
└── Formal Level 3 Commercial TCE Unlocking
```

---

## 5. Verification Sign-Off Checklist

Before any code implementation is merged or promoted:

- [ ] All 10 adversarial security test scenarios pass with zero bypasses.
- [ ] Database immutability triggers verified on isolated test databases.
- [ ] 100% of predecessor regression tests continue to pass cleanly.
- [ ] `is_ready_for_operations` verified strictly `False` across all execution branches.
- [ ] Zero production private keys present in repository or test fixtures.
- [ ] Production database `pelagos.db` verified untouched and unmodified.
