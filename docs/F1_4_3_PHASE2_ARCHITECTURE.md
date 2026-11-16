# Pelagos — Stage F1.4.3 Phase 2 Architecture Specification (Revision 3)
## Commercial Fixture Validation, Cryptographic Authorization, Voyage Economics & Tamper-Evident Audit

**Document ID:** `DOC-F143-PHASE2-ARCH-001`  
**Revision:** Revision 3 (`DESIGN-F143-PHASE2-REV-003`)  
**Stage:** F1.4.3 Phase 2 (Architecture Specification & Final Design Clarifications)  
**Published Baseline Commit:** `fdd70b838687366cbf74081c78ecde78990fe7bc`  
**Parent Foundation:** F1.4.3 Phase 1 — Formally Closed and Published  
**Predecessors:**  
- `VERIFY-F143-PHASE1-CLOSE-001` (`docs/F1_4_3_PHASE1_FINAL_SIGNOFF.md`)  
- `VERIFY-F143-PHASE1-RECON-001` (`docs/F1_4_3_PHASE1_FINAL_RECONCILIATION.md`)  
- `VERIFY-F143-PHASE1-001` (`docs/F1_4_3_PHASE1_INDEPENDENT_VERIFICATION.md`)  
- `REPORT-F143-PHASE1-001` (`docs/F1_4_3_PHASE1_IMPLEMENTATION_REPORT.md`)  
- `DOC-F143-DESIGN-REVIEW-001` (`docs/F1_4_3_VOYAGE_TCE_DESIGN_REVIEW.md`)  
- `DOC-F143-DESIGN-001` (`docs/F1_4_3_VOYAGE_TCE_READINESS_DESIGN.md`)  
- `DOC-F1424-001` (`docs/F1_4_2_4_VOYAGE_DISBURSEMENT_INTEGRATION.md`)  
**Status:** Approved Architecture Specification — Implementation Authorization Strictly Blocked Until Independent Gate Sign-Off  

---

## 1. Executive Summary & Scope Boundary

Stage F1.4.3 Phase 2 defines the architectural framework for bridging external commercial fixtures and voyage disbursements into deterministic voyage economics calculations, governed by cryptographic authorization and tamper-evident audit trails.

Following the final design clarifications under `DESIGN-F143-PHASE2-REV-003`, this document resolves the five remaining architectural findings:

1. **Refined Financial Canonicalization & Precision Lifecycle:** Distinguishes five lifecycle precision stages (Original Source, Internal Calculation, Statutory Rounding, Display, and Signed Canonical). Rather than forcing all values into ISO 4217 minor currency units, it defines field-specific scales (e.g. 4-6 decimals for FX fixings, 3-4 decimals for freight rates, 3 decimals for bunker/cargo tonnages) and establishes strict canonical Decimal string representation conforming to RFC 8785, with explicit validation of negative zero, trailing zeros, exponent notation, and non-finite values.
2. **Offline Revocation Threat Model & Timeline Scenarios:** Reassesses the offline exposure window, eliminating simplistic formulas. Provides five concrete timeline scenarios (revocation after refresh, revocation before issuance, disconnection after issuance, reconnect after CRL expiry, and clock rollback), formally bounding what the offline system can and cannot guarantee.
3. **Decoupled Merkle Tree vs. Chronological Audit Ledger:** Formally decouples the deterministic record-set Merkle tree (representing line-item integrity using RFC 6962 byte-level domain separation `0x00`/`0x01` and sorted leaves) from the chronological audit chain (strictly sequential append-only ledger ordered by monotonic sequence numbers, using domain tag `0x02`, never sorted lexicographically).
4. **Comprehensive Voyage-Duration Accounting:** Deconstructs voyage time into seven discrete duration components (laden sea, ballast sea, loading, discharge, port waiting, canal waiting, active canal transit). Defines how laytime and demurrage/despatch impact net revenue (numerator) while actual elapsed port time enters the voyage-day denominator. Reaffirms that conventional Voyage TCE does **not** deduct vessel OPEX or capital debt financing.
5. **Strict Phase 2 Token Restrictions:** Formally segregates Development/Test Tokens and Non-Operational Authorization Simulations from Production Operational Release Tokens. Phase 2 authorizes only in-memory mock testing; production release token issuance, key ceremonies, and Level 3 operational unlocking remain strictly prohibited.

> [!CAUTION]
> ### Strict Phase 2 Design Boundary & Operational Lock
> This specification is an **architectural assessment and design document only**. No source code, database models, migrations, or operational release tokens may be created or configured. Level 3 operational calculations remain **strictly locked** (`is_ready_for_operations = False`, `readiness_tier = LEVEL_2_RESEARCH_ONLY`). Phase 3 remains the sole authorized stage for production release.

---

## 2. Published Baseline & Repository Integrity

### 2.1 Baseline State
- **Branch:** `main` tracking `origin/main` (`https://github.com/Sanchay0123/Pelagos.git`).
- **Published Baseline Commit:** `fdd70b838687366cbf74081c78ecde78990fe7bc`.
- **Working Tree:** Zero uncommitted tracked modifications to application code or database schemas.
- **Protected Database:** Production SQLite database `pelagos.db` verified untouched at exactly **1,818,624 bytes**.
- **Untracked Preserved Assets:** 31 annual Parquet partition folders (`parquet_data/freight/year=1996` through `year=2026`) and 53 exploratory scripts in `scratch/` remain untouched.

---

## 3. Financial Precision Lifecycle & Canonicalization

A central defect in naive financial systems is forcing all numbers into 2-decimal fiat minor currency units. Commercial maritime contracts and statutory authorities operate across diverse precision domains.

### 3.1 Five Precision Lifecycle Tiers

```
[Financial Precision Lifecycle]
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. Original Source Precision                                                │
│    Exact precision promulgated by statutory authorities, central banks,    │
│    or negotiated charterparties (e.g. IMF SDR 6 decimals, Worldscale 4 dec).│
├─────────────────────────────────────────────────────────────────────────────┤
│ 2. Internal Calculation Precision                                           │
│    High-precision intermediate arithmetic utilizing decimal.Decimal with    │
│    Context(prec=34), preventing intermediate truncation or rounding drift. │
├─────────────────────────────────────────────────────────────────────────────┤
│ 3. Contractual / Statutory Rounding                                         │
│    Domain-specific rounding applied at tariff boundaries (e.g. Suez Canal   │
│    Article 58 SDR rounding, MPA Singapore dues rounding to nearest cent).   │
├─────────────────────────────────────────────────────────────────────────────┤
│ 4. Display Precision                                                        │
│    Formatting applied for human interfaces and reporting (e.g. USD commas). │
├─────────────────────────────────────────────────────────────────────────────┤
│ 5. Signed Canonical Representation                                          │
│    Exact authorized commercial value serialized into RFC 8785 canonical     │
│    payload as a normalized Decimal string, preserving field-specific scale. │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Field-Specific Precision Standards

| Economic Domain / Field | Source / Standard | Internal Precision | Authorized Canonical Scale | Canonical Example |
|---|---|---|---|---|
| **Fiat Currencies (USD, EUR, SGD)** | ISO 4217 | `Decimal(prec=34)` | Exactly 2 decimal places | `"14250.50"` |
| **SDR Base Charges** | IMF / SCA Tariff | `Decimal(prec=34)` | Exactly 4 decimal places | `"452100.2500"` |
| **Central Bank FX Fixings** | IMF / ECB / MAS | `Decimal(prec=34)` | Exactly 6 decimal places | `"1.345620"` |
| **Freight Rates (USD/MT)** | USDA GTR / Baltic | `Decimal(prec=34)` | 2 to 4 decimal places | `"18.4250"` |
| **Cargo Quantities (MT)** | B/L / Draft Survey | `Decimal(prec=34)` | Exactly 3 decimal places | `"52000.000"` |
| **Fuel Quantities (MT)** | BDN / Flow Meter | `Decimal(prec=34)` | Exactly 3 decimal places | `"24.350"` |
| **Bunker Fuel Prices (USD/MT)** | Platts / Hub Quote | `Decimal(prec=34)` | Exactly 2 decimal places | `"625.50"` |
| **Daily Hire & Daily OPEX** | Charterparty / Owner | `Decimal(prec=34)` | Exactly 2 decimal places | `"15500.00"` |
| **Daily Voyage TCE (USD/day)** | Conventional Formula | `Decimal(prec=34)` | Exactly 2 decimal places | `"18240.75"` |
| **Voyage Duration Days** | Navigational Log | `Decimal(prec=34)` | 2 to 4 decimal places | `"14.2500"` |

### 3.3 Canonical Decimal String Normalization Specification
Signed financial payloads serialize all numerical values as **canonical Decimal strings** (not JSON numbers):
- **Syntax Regex:** `^-?(0|[1-9][0-9]*)(\.[0-9]+)?$`
- **Negative Zero Rejection:** `-0`, `-0.0`, `-0.00` are strictly normalized to unsigned zero matching the field's authorized scale (e.g. `"0.00"`).
- **Scale Preservation:** Trailing zeros authorized by the field's commercial scale are strictly preserved (e.g. an FX fixing of 1.34 is serialized as `"1.340000"`, never `"1.34"`).
- **Exponent Notation Forbidden:** Scientific notations (e.g. `"1.42E+4"`, `"1e-6"`) are illegal and rejected.
- **Non-Finite Value Rejection:** `NaN`, `sNaN`, `Infinity`, and `-Infinity` raise an immediate `UnserializableFinancialValue` exception.
- **Character Purity:** No thousands separators (commas), currency symbols (`$`, `€`), or leading/trailing whitespace.

---

## 4. Offline Revocation Threat Model & Analysis

When a Pelagos execution gateway operates in an offline or network-partitioned environment (such as a shipboard installation or remote port office), it cannot query authoritative online Certificate Revocation Lists (CRLs) or OCSP responders in real time.

### 4.1 System Parameters & Invariants
- **Maximum Operational Token Lifespan ($T_{\text{token}}$):** 3600 seconds (exactly 1 hour from issuance `iat` to expiration `exp`).
- **Maximum Offline CRL Freshness Horizon ($T_{\text{fresh}}$):** 7200 seconds (exactly 2 hours from CRL publication `this_update` to `next_update`).
- **Maximum Permissible Clock Skew ($\tau_{\text{skew}}$):** 30 seconds.

### 4.2 Timeline Scenarios

```
Scenario 1: Revocation Immediately After CRL Refresh
T0: Node refreshes CRL (Valid until T0 + 2h).
T0 + 1m: Key K is compromised and revoked at the Certificate Authority (CA).
T0 + 10m: Attacker uses K to sign a forged token (exp = T0 + 70m).
T0 + 15m: Attacker submits token to offline node.
-> RESULT: Node accepts token because cached CRL does not reflect the revocation.
   Exposure duration: 55 minutes (until token naturally expires at T0 + 70m).

Scenario 2: Revocation Immediately Before Token Issuance
T0: Key K compromised and revoked at CA.
T0 + 5m: Offline node (partitioned prior to T0) has CRL from T - 30m (valid until T + 90m).
T0 + 10m: Attacker issues token signed with K (exp = T0 + 70m).
T0 + 15m: Attacker submits token to offline node.
-> RESULT: Node accepts token. Offline node is blind to CA state until T + 90m.

Scenario 3: Token Issuance Before Node Disconnection
T0: Legitimate token issued for Voyage V.
T0 + 5m: Node is disconnected from the network.
T0 + 15m: Legitimate token submitted to node.
-> RESULT: Node verifies token, executes calculation, and writes token jti to local
   consumed_operational_tokens table. Subsequent replay on this node fails closed.
   (Cross-node replay limitation: If a second disconnected node exists, it cannot
   detect the consumption without ledger synchronization).

Scenario 4: Reconnection After CRL Expiry
T0: Node goes offline.
T0 + 2h 1m: Node attempts to verify a token.
-> RESULT: Cached CRL age > 2 hours. Node STRICTLY FAILS CLOSED with
   SECURITY_REVOCATION_STATUS_UNKNOWN. Zero tokens can be verified until network
   reconnects and a fresh, validly signed CRL is retrieved.

Scenario 5: Clock Rollback and Excessive Clock Skew
Attacker sets local system clock backward to prevent token expiry or CRL staleness.
-> RESULT: Mitigated by system monotonic clock delta checks (time.monotonic()) and
   enforcement of max 30-second skew on token issuance timestamps. If monotonic
   elapsed time conflicts with wall-clock time, node raises CLOCK_ANOMALY_ABORT.
```

### 4.3 Formal Guarantees and Limitations

#### What the System CAN Guarantee:
1. **Bounded Offline Operation:** Under no circumstances will an offline node accept tokens when its cached CRL is older than 2 hours. At $T_0 + 2\text{ hours}$, it fails closed.
2. **Deterministic Token Expiry:** No operational token is valid for more than 1 hour. Even with a completely compromised key, a forged token expires within 3600 seconds.
3. **Local Replay Prevention:** Within any single node, a consumed token `jti` is durably recorded in `consumed_operational_tokens` inside an ACID transaction; replay attempts fail closed.
4. **Historical Revocation Protection:** Any key revoked *prior* to the latest CRL publication is rejected instantly.

#### What the System CANNOT Guarantee (The Offline Blind Spot):
1. **Zero-Day Revocation Latency:** An offline node **cannot detect** a key revocation that occurs after its cached CRL was fetched. The maximum vulnerability window is bounded by the remaining validity of the cached CRL:
   $$W_{\text{actual}} = \min(T_{\text{token\_exp}} - T_{\text{submission}}, T_{\text{crl\_exp}} - T_{\text{submission}})$$
2. **Cross-Partition Anti-Replay:** If multiple offline nodes operate without network connectivity to each other, a single-use token could theoretically be submitted once to Node A and once to Node B. (Mitigation: Operational tokens are contextually bound to specific node/vessel identifiers).

---

## 5. Merkle Tree Aggregation vs. Chronological Audit Chain

To prevent architectural confusion, Pelagos formally separates two independent cryptographic structures:

```
[Two Independent Cryptographic Structures]
┌─────────────────────────────────────────────────────────────────────────────┐
│ Structure A: Deterministic Record-Set Merkle Tree                           │
│ - Scope: Internal line-item integrity within ONE disbursement record.       │
│ - Structure: Static binary Merkle tree.                                     │
│ - Ordering: Leaves sorted lexicographically by leaf hash.                   │
│ - Domain Separation: RFC 6962 byte-level prefixes (0x00 leaf, 0x01 node).   │
│ - Purpose: Tamper evidence across unordered disbursement line items.        │
├─────────────────────────────────────────────────────────────────────────────┤
│ Structure B: Chronological Audit Ledger                                     │
│ - Scope: System-wide sequential log of platform events across ALL voyages.  │
│ - Structure: Strictly append-only sequential hash chain.                    │
│ - Ordering: Strictly ordered by monotonic sequence number (NEVER sorted!). │
│ - Domain Separation: Byte-level prefix 0x02.                                │
│ - Purpose: Tamper evidence, ordering integrity, and deletion detection.    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5.1 Structure A: Deterministic Record-Set Merkle Tree (RFC 6962 Compatible)
- **Leaf Encoding:** Each line item $L_i \in \{L_1, \dots, L_N\}$ is serialized to canonical JSON $J(L_i)$.
- **RFC 6962 Domain Separation:**
  - Leaf Hash: $h_i = \text{SHA256}(0x00 \parallel J(L_i))$
  - Internal Node Hash: $H_{\text{node}} = \text{SHA256}(0x01 \parallel H_{\text{left}} \parallel H_{\text{right}})$
- **Leaf Ordering:** Leaves are sorted lexicographically by their raw 32-byte leaf hash:
  $$S = \text{sort}([h_1, h_2, \dots, h_N])$$
- **Exact RFC 6962 Tree Construction:**
  For any leaf array $D$ of length $N$:
  - If $N = 0$: $M([]) = \text{SHA256}(b"")$ (32-byte empty hash).
  - If $N = 1$: $M([d_0]) = d_0$.
  - If $N > 1$: Let $k = 2^{\lceil \log_2 N \rceil - 1}$ (the largest power of 2 less than $N$).
    $$M(D) = \text{SHA256}(0x01 \parallel M(D[0..k]) \parallel M(D[k..N]))$$
  This construction handles odd node counts deterministically without duplicating leaves.
- **Inclusion Proofs:** Standard audit paths $(p_1, \dots, p_m)$ allow proving inclusion of any line item in $O(\log N)$ steps.

### 5.2 Structure B: Chronological Audit Ledger
- **Strict Monotonic Ordering:** Events are **never** sorted lexicographically. They are appended in exact chronological order assigned by an atomic database sequence ($seq = 1, 2, 3, \dots$).
- **Chain Hashing:** For audit event $k$:
  $$H_{\text{chain}}^{(k)} = \text{SHA256}(0x02 \parallel H_{\text{chain}}^{(k-1)} \parallel \text{canonicalize\_json}(E_k))$$
  Where $H_{\text{chain}}^{(0)} = \text{SHA256}(b"\text{PELAGOS\_GENESIS\_AUDIT\_ROOT\_2024}")$.
- **Audit Event Payload ($E_k$):** Contains `sequence_number`, `event_id`, `timestamp_utc`, `actor_identity`, `event_type`, `record_id`, `merkle_root`, and `payload_hash`.
- **Concurrent-Write Handling:** In SQLite and PostgreSQL, sequence numbers are allocated via atomic primary-key auto-increment / `SERIAL`, protected by serialized transactions.
- **Tamper Detection:** Traversal of $k = 1 \dots M$ immediately detects any deleted, reordered, or modified audit record.

---

## 6. Comprehensive Voyage-Duration Accounting

A major deficiency in voyage estimators is treating voyage time as a monolithic duration. Pelagos formally deconstructs voyage duration into seven discrete components:

### 6.1 Duration Decomposition

```
[Voyage Duration Breakdown (Days)]
T_voyage = T_laden_sea + T_ballast_sea + T_loading + T_discharge 
           + T_port_waiting + T_canal_waiting + T_canal_transit
```

1. **Laden Sea Passage ($T_{\text{laden\_sea}}$):** Steaming time under laden condition from loading port pilot station to discharge port pilot station (or canal staging).
2. **Ballast Sea Passage ($T_{\text{ballast\_sea}}$):** Steaming time from previous discharge port to current loading port (allocated per charterparty terms).
3. **Loading Working Time ($T_{\text{loading}}$):** Elapsed working time from all-fast to hoses-off/lashing completed.
4. **Discharge Working Time ($T_{\text{discharge}}$):** Elapsed working time from all-fast to cargo completion.
5. **Port Waiting / Congestion ($T_{\text{port\_waiting}}$):** Anchorage waiting time between Notice of Readiness (NOR) tendering and berthing.
6. **Canal Staging & Waiting ($T_{\text{canal\_waiting}}$):** Anchorage staging for convoy slot allocation (Panama Cristobal/Balboa, Suez Port Said/Port Suez).
7. **Canal Transit Passage ($T_{\text{canal\_transit}}$):** Active pilotage passage through the canal waterway.

### 6.2 Laytime, Demurrage/Despatch & Denominator Accounting
A critical distinction is enforced between calendar duration (denominator) and contractual delay penalties (numerator):
- **Port Working Time Used:** $T_{\text{port\_used}} = T_{\text{loading}} + T_{\text{discharge}}$
- **Contractual Laytime ($T_{\text{laytime}}$):** Contractually agreed time allowed for cargo operations (e.g. 5 Weather Working Days, SHEX/SHINC).
- **Laytime Net Variance:** $\Delta T_{\text{laytime}} = T_{\text{port\_used}} - T_{\text{laytime}}$
  - If $\Delta T > 0$: Demurrage incurred. Monetary impact: $+\Delta T \times \text{DemurrageRate}$ added to Net Voyage Revenue (numerator).
  - If $\Delta T < 0$: Despatch earned. Monetary impact: $-|\Delta T| \times \text{DespatchRate}$ deducted from Net Voyage Revenue (numerator).
- **Denominator Rule:** Regardless of demurrage or despatch, the **actual calendar days** spent performing the voyage ($T_{\text{voyage}}$) form the denominator of the daily TCE formula:
  $$TCE = \frac{\text{Net Voyage Revenue}}{T_{\text{voyage}}}$$

### 6.3 Duration Status Governance
Every duration field carries a strict status attribute:
- `ESTIMATED_ROUTED`: Calculated via maritime routing engine or speed/distance tables. Permitted in Level 2 research mode with prominent disclaimers.
- `CONTRACTUAL_AGREED`: Specified in signed charterparty terms.
- `OBSERVED_ACTUAL`: Sourced from certified Statement of Facts (SOF) or port logs.
- **Fail-Closed Rule:** Missing duration components are **never** silently filled with default assumptions or shortest-path approximations. In Level 3 operational mode, missing durations fail closed with `MISSING_VOYAGE_DURATION`.

---

## 7. Phase 2 Cryptographic Token Restrictions

To eliminate ambiguity regarding testing versus production capabilities, Phase 2 categorizes tokens into three distinct governance domains:

```
┌─────────────────────────────────┬────────────────────────────────────────────┬────────────────────────────┐
│ Token Category                  │ Description & Key Custody                  │ Phase 2 Authorization      │
├─────────────────────────────────┼────────────────────────────────────────────┼────────────────────────────┤
│ Category 1: Development / Test  │ In-memory mock keys generated during test  │ FULLY AUTHORIZED in        │
│ Tokens                          │ execution (test_ed25519_key_fixture).      │ automated test suites.     │
│                                 │ Never persisted to config or databases.    │                            │
├─────────────────────────────────┼────────────────────────────────────────────┼────────────────────────────┤
│ Category 2: Non-Operational     │ Automated simulation harness validating    │ FULLY AUTHORIZED for       │
│ Authorization Simulations       │ signature checking, anti-replay, and CRLs. │ integration testing.       │
│                                 │ Hardcoded is_operational_approved = False. │                            │
├─────────────────────────────────┼────────────────────────────────────────────┼────────────────────────────┤
│ Category 3: Production Release  │ Cryptographically signed tokens issued by  │ STRICTLY PROHIBITED        │
│ Tokens                          │ Governance Authority via hardware HSM/KMS. │ IN PHASE 2.                │
│                                 │ Unlocks Level 3 commercial calculations.   │ Reserved for Phase 3.      │
└─────────────────────────────────┴────────────────────────────────────────────┴────────────────────────────┘
```

### Strict Restrictions Governing Phase 2:
1. **Zero Production Keys:** No production token signing keys, private keys, or HSM credentials may be generated, stored, or configured in the repository.
2. **Zero Operational Authorization:** Phase 2 shall **never** issue Category 3 Production Release Tokens.
3. **Hard Operational Lock:** `is_ready_for_operations` remains hardcoded to `False` across all execution branches.
4. **Commercial Gating:** Commercial freight bidding, operational charter recommendation ranking, and live settlement remain strictly blocked.
5. **Phase 3 Boundary:** Production key ceremony, HSM provisioning, and Level 3 operational unlocking are exclusively deferred to Phase 3.

---

## 8. Summary of Revisions (Rev 2 $\to$ Rev 3)

| Section | Revision Description | Architectural Impact |
|---|---|---|
| **Section 3** | Formulated 5-tier precision lifecycle and field-specific scales. | Replaced blunt 2-decimal constraint with domain-specific precision (FX 6d, SDR 4d, Cargo/Bunker 3d). |
| **Section 4** | Reassessed offline revocation threat model with 5 timeline scenarios. | Replaced simplistic bound with rigorous derivation; codified what the system can and cannot guarantee offline. |
| **Section 5** | Separated RFC 6962 Merkle tree (set integrity) from chronological audit chain (append-only ledger). | Enforced byte-level domain separation (`0x00`/`0x01`/`0x02`); prohibited sorting chronological audit events. |
| **Section 6** | Expanded voyage duration to 7 components; separated laytime from calendar days. | Clarified that demurrage affects the numerator while calendar port days enter the TCE denominator. |
| **Section 7** | Categorized tokens into Dev/Test, Simulation, and Production. | Strictly barred production keys and release tokens from Phase 2; authorized in-memory test mocks only. |
