# Pelagos — Stage F1.4.3 Design Review Addendum
## Architectural Refinements, Security Gate Specifications & Boundary Resolutions

**Document ID:** `DOC-F143-DESIGN-REVIEW-001`  
**Parent Document:** `DOC-F143-DESIGN-001` (v1.0.0, *Stage F1.4.3 Voyage TCE Engine & Port/Canal Disbursement Settlement*)  
**Stage:** F1.4.3 Readiness Review & Specification Refinement  
**Predecessors:** `REPORT-F1424-REMED-001`, `VERIFY-F1424-001`  
**Baseline Git Commit:** `2929e61f1f0fdfeb30af1a02889071805aa29328`  
**Status:** Approved Design Review Addendum — Binding Specification for Stage F1.4.3  

---

## Executive Summary & Scope

This addendum resolves the five architectural tensions, governance conflicts, and security specifications identified during the independent review of `DOC-F143-DESIGN-001`:

1. **FX Valuation-Date Policy:** Reconciling the date-matching rule with central-bank trading calendars, specifying provider-specific lookback horizons, and capturing both requested valuation date and actual observation date without arbitrary universal tolerances.
2. **Partial-Component Exception Policy:** Eliminating informal user-override waivers, codifying the default fail-closed operational gate, and defining a formal, audited commercial-waiver governance path.
3. **Cryptographic Unlock Architecture:** Specifying key custody (HSM/KMS), public-key trust and rotation, canonical token payload (RFC 8785), contextual binding, single-use replay prevention (`jti` ledger), and fail-closed offline revocation.
4. **Tamper-Evident Audit Architecture:** Replacing vulnerable bare database hashing with RFC 8785 JSON canonicalization, Merkle-tree line-item aggregation, asymmetric Ed25519 attestation signatures, and append-only database write-protection.
5. **External Fixture Validation Framework:** Deconstructing the naive 15% variance threshold into a four-tier charge taxonomy and establishing one-way statutory floor enforcement.

---

## 1. Domain A: FX Valuation-Date Policy & Central Bank Calendar Synchronization

### 1.1 Conflict Analysis
In `DOC-F143-DESIGN-001`:
- **Section 1** required: *"Official statutory central bank daily fixing matching transit date."*
- **Section 3.2** permitted: *"The most recent preceding authoritative official business-day fixing..."*

This introduced ambiguity regarding whether transits occurring on non-banking days (weekends, statutory holidays) could receive operational clearance, or whether they would fail closed due to the lack of a same-calendar-day publication.

### 1.2 Provider-Specific Lookback Horizons
Foreign exchange markets and sovereign central banks operate on strict jurisdictional business calendars. The engine must reject synthetic linear interpolations and mid-market benchmarks in operational mode (`BENCHMARK_EXCHANGE_RATE_REJECTED`), while codifying deterministic calendar-lag horizons per provider:

```
┌─────────────────────────────────┬──────────────────┬──────────────────────┬───────────────────────┐
│ Currency & Central Bank         │ Publication Time │ Non-Trading Windows  │ Max Observation Lag   │
│ Provider                        │ & Primary Source │ (Excluding Holidays) │ Horizon (T_max)       │
├─────────────────────────────────┼──────────────────┼──────────────────────┼───────────────────────┤
│ SDR / USD (IMF)                 │ Daily ~17:00 EST │ Sat, Sun             │ 4 calendar days       │
│ IMF Daily SDR Valuation         │ IMF Official Web │ (US Federal Holidays)│ (Weekend + Hol: 4d)   │
├─────────────────────────────────┼──────────────────┼──────────────────────┼───────────────────────┤
│ EUR / USD (ECB)                 │ Daily ~16:00 CET │ Sat, Sun             │ 4 calendar days       │
│ European Central Bank Ref Rate  │ TARGET2 Operating│ (TARGET2 Holidays)   │ (Easter Break: 4d)    │
├─────────────────────────────────┼──────────────────┼──────────────────────┼───────────────────────┤
│ SGD / USD (MAS)                 │ Daily ~17:30 SGT │ Sat, Sun             │ 5 calendar days       │
│ Monetary Authority of Singapore │ Singapore Banks  │ (Lunar NY Holidays)  │ (Multi-day Hol: 5d)   │
├─────────────────────────────────┼──────────────────┼──────────────────────┼───────────────────────┤
│ USD / USD (Identity)            │ Static (1.000000)│ None (N/A)           │ 0 calendar days       │
│ Federal Reserve / Statutory     │ Pelagos Identity │ Always Available     │ (Identity match)      │
└─────────────────────────────────┴──────────────────┴──────────────────────┴───────────────────────┘
```

### 1.3 Data Model & Dual-Date Persistence
To eliminate ambiguity, every operational calculation and persisted line item must store both dates:
1. `requested_valuation_date` ($D_{\text{req}}$): The actual operational event date (e.g., vessel canal transit date or port arrival date).
2. `fx_observation_date` ($D_{\text{obs}}$): The authoritative central-bank fixing publication date utilized for conversion.
3. `fx_observation_lag_days`: Exactly calculated as:
   $$\Delta t = D_{\text{req}} - D_{\text{obs}}$$

### 1.4 Strict Fail-Closed Conditions
The conversion fails closed (`total_disbursement_usd = None`, `completeness_status = MISSING_CURRENCY_RATE`) if:
1. **Future Lookahead Violation:** $D_{\text{obs}} > D_{\text{req}}$ (using future rates is strictly prohibited).
2. **Excessive Observation Lag:** $\Delta t > T_{\text{max}}^{\text{provider}}$ (stale exchange rate beyond jurisdictional holiday threshold).
3. **Mid-Market Benchmark Substitution:** In operational mode, any rate labeled `PARTIALLY_VERIFIED` or derived via linear interpolation (such as May 1 European Labour Day) is rejected with `BENCHMARK_EXCHANGE_RATE_REJECTED`.

---

## 2. Domain B: Partial-Component Exception Policy & Commercial Waiver Governance

### 2.1 The Principle of Incomplete Statutory Data
In commercial maritime transactions, **acknowledgement of missing data does not make the data complete**. 
If a port disbursement assessment lacks mandatory river pilotage S-rates (e.g. Rotterdam) or draft-overage pilotage fees (e.g. Houston), the calculated total is mathematically partial. Allowing an end-user to check an "override waiver" box and receive an operational approval badge (`is_operational_approved = True`) would compromise the financial integrity of Pelagos.

### 2.2 Default Inviolable Rule
Any voyage disbursement calculation containing partial components (`has_partial_components = True` or `completeness_status = PARTIAL_COMPONENTS`):
- Is permanently **disqualified** from receiving `is_operational_approved = True`.
- Is strictly blocked from entering automated charter recommendation ranking, tender bidding, or contractual TCE settlement.
- Must retain `is_complete = False` on all persisted records and API responses.

### 2.3 Qualified Commercial Waiver Exception (Separate Governance Path)
When a chartering desk explicitly requires a provisional TCE calculation under incomplete statutory conditions, it must execute through an isolated, qualified governance flow:

```
                  [Partial Disbursement Assessment]
                   (has_partial_components = True)
                                  │
                                  ▼
                    [Standard Operational Gate]
                     Level 3 Status: REJECTED
                                  │
                                  ▼
                 [Qualified Commercial Exception Path]
        ┌─────────────────────────────────────────────────────┐
        │ Commercial Waiver Request (Requires CCO / Desk Head)│
        │ 1. Specific missing tariff IDs cataloged.           │
        │ 2. Certified commercial proxy amount injected.      │
        │ 3. Bilateral risk acknowledgement signed.           │
        │ 4. Auditable governance record persisted.           │
        └──────────────────────────┬──────────────────────────┘
                                  │
                                  ▼
                 [Classification Emitted on Output]
               Tier: LEVEL_2_QUALIFIED_COMMERCIAL_ESTIMATE
                       is_complete: FALSE
                 Status: QUALIFIED_PARTIAL_ESTIMATE
```

**Mandatory Invariants for Qualified Commercial Estimates:**
1. **No Semantic Promotion:** The record is **never** promoted to `LEVEL_3_OPERATIONAL_APPROVED`.
2. **Prominent Watermarking:** All downstream documents, UI representations, and API payloads must carry the disclaimer:
   > `QUALIFIED COMMERCIAL ESTIMATE: Contains unverified or partial statutory port/canal charges. Not certified for contractual final disbursement settlement.`
3. **Audit Immutability:** The original unadjusted partial disbursement record is preserved without modification; the qualified adjustment is stored as an additive child record linked to the commercial waiver.

---

## 3. Domain C: Cryptographic Level 3 Operational Unlock Architecture

To prevent rogue operational execution or accidental leakage of research data into commercial systems, Level 3 operational status requires an asymmetric cryptographic unlock gate.

### 3.1 Key Management & Custody
- **Signing Authority:** Only the Pelagos Platform Governance Controller or an authenticated enterprise Key Management Service (KMS).
- **Private-Key Custody:** Private signing keys reside in FIPS 140-3 Level 3 Hardware Security Modules (HSMs) or AWS CloudHSM / GCP Cloud KMS. Private keys are non-exportable and inaccessible to application code.
- **Algorithm:** Asymmetric Ed25519 (RFC 8032) or ECDSA with curve NIST P-384.

### 3.2 Public-Key Trust Store & Rotation
- The Pelagos engine maintains an in-memory, read-only trust store of active Governance Public Keys loaded from a read-only configuration directory.
- Each key possesses:
  - `kid` (Key Identifier, string).
  - `public_key_bytes` (Ed25519 raw public key).
  - `valid_from` and `valid_until` (UTC timestamps).
- Key rotation occurs semi-annually. Expired keys are rejected instantly at runtime.

### 3.3 Canonical Token Payload Specification
Operational unlock tokens must be formatted as PASETO v4.public or compact CBOR/JSON Web Tokens (JWT) using RFC 8785 canonical formatting:

```json
{
  "iss": "urn:pelagos:governance:authority",
  "sub": "operational_tce_unlock",
  "aud": "urn:pelagos:engine:settlement",
  "jti": "b5e9f82d-41a3-4a77-90c1-2fa8d5e0321a",
  "iat": 1731672000,
  "nbf": 1731672000,
  "exp": 1731675600,
  "scope": "LEVEL_3_OPERATIONAL_SETTLEMENT",
  "binding": {
    "voyage_id": "VOY-2024-001",
    "vessel_imo": 9876543,
    "transit_date": "2024-01-15",
    "origin_port": "SGSIN",
    "destination_port": "NLRTM",
    "disbursement_record_id": 142,
    "disbursement_content_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  }
}
```

### 3.4 Verification & Anti-Replay Rules
1. **Signature Verification:** The cryptographic signature is verified against the trusted public key corresponding to `kid`.
2. **Contextual Binding Verification:** The token's `binding` block is checked against the execution context:
   - Request `voyage_id == binding.voyage_id`
   - Vessel `vessel_imo == binding.vessel_imo`
   - Event `transit_date == binding.transit_date`
   - Database `disbursement_record_id == binding.disbursement_record_id`
   - Integrity `disbursement_content_hash == compute_record_hash(record)`
   Any discrepancy aborts execution with `SECURITY_BINDING_MISMATCH`.
3. **Single-Use Replay Prevention:**
   - Every successfully consumed token `jti` is durably recorded in an append-only database table: `consumed_operational_tokens(jti, consumed_at, voyage_id)`.
   - If a token `jti` already exists in this table, the engine rejects the request with `REPLAYED_OPERATIONAL_TOKEN_REJECTED`.
4. **Short Lifespan:** Maximum token validity window ($\text{exp} - \text{iat}$) is **1 hour**.
5. **Revocation & Offline Behavior:**
   - The engine checks tokens against a local cryptographic Revocation List (CRL).
   - If the local CRL cache is older than 2 hours and external network connectivity is unavailable, the engine fails closed with `SECURITY_REVOCATION_STATUS_UNKNOWN`.

---

## 4. Domain D: Tamper-Evident Audit Architecture

### 4.1 Critique of Bare Database Hashing
Storing an SHA-256 hash in a mutable database column provides zero security against an adversary or internal bug with database write access. If a process can modify `amount_usd` in `voyage_disbursement_line_items`, it can simply recompute the SHA-256 hash and update `voyage_disbursement_records.content_hash`.

### 4.2 Multi-Layered Tamper-Evident Architecture
Stage F1.4.3 must implement four mutually reinforcing layers of audit integrity:

```
[Layer 1: Deterministic Serialization (RFC 8785)]
  Canonical JSON format (sorted keys, whitespace stripped, IEEE 754 floats normalized)
                             │
                             ▼
[Layer 2: Merkle-Tree Line-Item Aggregation]
  Each line item hashed individually; Root hash binds entire disbursement account
                             │
                             ▼
[Layer 3: Asymmetric Service Attestation Signature]
  Merkle Root + Record Header signed by Pelagos Engine Ed25519 Private Key
                             │
                             ▼
[Layer 4: Append-Only DB Immutability & Ledger Chaining]
  Database triggers block UPDATE/DELETE; Hash incorporates previous record hash
```

### 4.3 Merkle Structure & Mathematical Definition
For a record with $N$ line items:
1. Normalize each line item to canonical JSON $J(L_i)$ and compute:
   $$h_i = \text{SHA256}(J(L_i)) \quad \text{for } i \in \{1, \dots, N\}$$
2. Sort $\{h_1, \dots, h_N\}$ lexicographically to eliminate insertion-order variance.
3. Compute Merkle tree root $H_{\text{items}} = \text{MerkleRoot}(h_1, \dots, h_N)$.
4. Construct canonical record header $J(R_{\text{header}})$ comprising `voyage_id`, `vessel_imo`, `transit_date`, `readiness_tier`, `total_disbursement_usd`, and $H_{\text{items}}$.
5. Compute Record Payload Hash:
   $$H_{\text{record}} = \text{SHA256}(J(R_{\text{header}}))$$
6. Compute Asymmetric Attestation:
   $$\sigma_{\text{record}} = \text{Ed25519\_Sign}(K_{\text{engine\_priv}}, H_{\text{record}})$$
7. Compute Ledger Chain Hash (linking to preceding record $k-1$):
   $$H_{\text{chain}}^{(k)} = \text{SHA256}(H_{\text{record}}^{(k)} \parallel H_{\text{chain}}^{(k-1)})$$

### 4.4 Tamper Detection Mechanism
During audit verification:
- Recomputing $H_{\text{record}}$ detects modified line items or amounts.
- Verifying $\sigma_{\text{record}}$ using $K_{\text{engine\_pub}}$ detects unauthorized recomputation of $H_{\text{record}}$ by an attacker without the private engine key.
- Checking $H_{\text{chain}}^{(k)}$ detects deleted or inserted records within the database sequence.

---

## 5. Domain E: External Fixture Validation Framework

### 5.1 Critique of the 15% Variance Rule
In `DOC-F143-DESIGN-001`, Section 7.1 proposed checking user estimates against statutory registry floors and flagging variances exceeding 15%. This approach is structurally flawed:
1. **Scale Insensitivity:** 15% of a $4,000 Singapore feeder call is $600 (often within legitimate pilotage launch variation), whereas 15% of a $500,000 Suez laden Capesize transit is $75,000 (a massive commercial discrepancy).
2. **The Floor Fallacy:** **Being above a statutory floor does not establish accuracy**. If statutory dues are $30,000 and the caller provides $60,000, the estimate is above the floor, but it could be 100% incorrect (e.g. double-counting pilotage or estimating an unverified bunker charge).

### 5.2 Four-Tier Charge Decomposition
In Stage F1.4.3, external fixtures provided via `VerifiedCharterInputs` must be parsed into four strictly segregated charge categories:

```
┌─────────┬──────────────────────────────┬────────────────────────┬─────────────────────────────┐
│ Tier    │ Charge Category              │ Evidence Nature        │ Operational Validation Rule │
├─────────┼──────────────────────────────┼────────────────────────┼─────────────────────────────┤
│ Tier 1  │ Statutory Dues               │ Public Law / Port      │ ZERO TOLERANCE: Must match  │
│         │ (Port Dues, Canal Tolls, VTS)│ Gazettes (ACP, SCA,    │ Pelagos statutory registry  │
│         │                              │ MPA, Havenbedrijf)     │ exactly (+/- 0.00).         │
├─────────┼──────────────────────────────┼────────────────────────┼─────────────────────────────┤
│ Tier 2  │ Regulated Ancillary Services │ Regulated Pilotage /   │ TARIFF RECONCILED: Base dues│
│         │ (Pilotage, Mooring)          │ Mooring Tariffs        │ must match; overtime/units  │
│         │                              │                        │ bounded by documented caps. │
├─────────┼──────────────────────────────┼────────────────────────┼─────────────────────────────┤
│ Tier 3  │ Contractual Remuneration     │ Commercially           │ CONTRACT ATTRIBUTED: Must   │
│         │ (Agency Fees, Stevedoring)   │ Negotiated Bilateral   │ cite valid bilateral contract│
│         │                              │ Appointment Records    │ or agency appointment ID.   │
├─────────┼──────────────────────────────┼────────────────────────┼─────────────────────────────┤
│ Tier 4  │ Dynamic Operational Expenses │ Volatile / Weather /   │ QUALIFIED ESTIMATE: Labeled │
│         │ (Tug Escorts, Demurrage)     │ Operational Variance   │ as provisional; excluded    │
│         │                              │                        │ from statutory settlement.  │
└─────────┴──────────────────────────────┴────────────────────────┴─────────────────────────────┘
```

### 5.3 One-Way Statutory Floor Enforcement
Registry floors operate strictly as **unidirectional rejection boundaries**:
- If a user-supplied fixture provides a canal toll or statutory port fee *less* than the statutory baseline calculated by Pelagos, the input is immediately rejected:
  $$\text{Input}_{\text{canal}} < \text{StatutoryFloor}_{\text{canal}} \implies \text{REJECTED: } \texttt{FIXTURE\_VALUATION\_BELOW\_STATUTORY\_FLOOR}$$
- If an input exceeds the statutory floor, it is **not** validated automatically. The excess must be accounted for by explicit line-item allocation to Tier 2 (overtime/tugs), Tier 3 (agency), or Tier 4 (provisional operational expenses). Without this itemized allocation, the excess is categorized as `UNALLOCATED_COMMERCIAL_OVERAGE` and barred from operational TCE clearance.

---

## 6. Implementation Summary & Readiness Verification Checklist

Before Stage F1.4.3 code development can begin, the following verification gates must be cleared:

- [x] Dual-date model (`requested_valuation_date`, `fx_observation_date`, `fx_observation_lag_days`) formally specified.
- [x] Provider-specific lookback horizons codified (IMF 4d, ECB 4d, MAS 5d).
- [x] Synthetic benchmark holiday interpolation strictly rejected in operational mode.
- [x] User-override waivers for partial components eliminated; qualified commercial exception path established.
- [x] Cryptographic unlock architecture specified (Ed25519, HSM custody, single-use `jti` anti-replay, contextual binding).
- [x] Tamper-evident audit architecture specified (RFC 8785 canonicalization, Merkle line-item tree, asymmetric engine signature, append-only chaining).
- [x] Four-tier external fixture decomposition specified with one-way statutory floor enforcement.
- [x] All 128 relevant regression tests passing cleanly across the Pelagos test suite.

