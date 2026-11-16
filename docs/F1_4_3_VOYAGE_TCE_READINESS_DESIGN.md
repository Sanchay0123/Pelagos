# Pelagos — Stage F1.4.3 Architectural Readiness Design
## Voyage TCE Engine & Port/Canal Disbursement Settlement

**Document ID:** `DOC-F143-DESIGN-001`  
**Stage:** F1.4.3 (Design & Readiness Specification)  
**Predecessors:**  
- `REPORT-F1424-001` / `VERIFY-F1424-001` (Stage F1.4.2.4: Voyage Disbursement Integration & TCE Gating Framework)  
- `VERIFY-F1423B-001` (Stage F1.4.2.3b: Global Port Disbursement Intelligence Foundation)  
- `VERIFY-F1423A-001` (Stage F1.4.2.3a: Canal Tariff Registry & Deterministic Rule Foundation)  
**Baseline Git Commit:** `2929e61f1f0fdfeb30af1a02889071805aa29328`  
**Status:** DESIGN ONLY — Operational Calculations Remain Strictly BLOCKED  

---

## Executive Summary & Scope Boundary

Stage F1.4.3 defines the architectural framework for the **Voyage Time Charter Equivalent (TCE) Engine & Port/Canal Disbursement Settlement**. 

In predecessor stages (F1.4.2.3a, F1.4.2.3b, and F1.4.2.4), Pelagos established:
1. Reusable, source-attributed canal tariff registries for the Panama Canal Authority (ACP) and Suez Canal Authority (SCA).
2. Port tariff registries and deterministic calculation rules across Singapore (SGSIN), Rotterdam (NLRTM), and Houston (USHOU).
3. Central bank currency conversion governance (MAS, ECB, IMF) enforcing strict rejection of synthetic benchmark interpolations for operational calculations.
4. Voyage-level disbursement integration and schema persistence (`VoyageDisbursementRecord`, `VoyageDisbursementLineItem`).
5. Complete Level 3 operational isolation preventing preliminary research intelligence from entering commercial net freight, daily TCE calculations, or charter recommendation rankings.

**Critical Governance Boundary:**  
This document specifies the target architecture, safety gates, verification protocols, and data models required to achieve operational readiness for Stage F1.4.3. **No operational calculations, commercial TCE consumption, or automated freight bidding may be enabled without formal cryptographic unlocking and independent verification gate clearance.**

---

## 1. Operational TCE Prerequisites

Level 3 operational calculations (`is_operational_approved = True`, `readiness_tier = LEVEL_3_OPERATIONAL_APPROVED`) may only be executed when **all** of the following prerequisite conditions are simultaneously satisfied:

```
[Operational Unlock Matrix]
┌─────────────────────────────────┬────────────────────────────────────────────┐
│ Domain                          │ Operational Prerequisite                   │
├─────────────────────────────────┼────────────────────────────────────────────┤
│ Vessel Particulars              │ Certified certificates on file (SCNT,      │
│                                 │ PC/UMS, Class Form, Draft Survey).         │
│ Source Evidence                 │ 100% DIRECTLY_VERIFIED statutory tariffs   │
│                                 │ with unexpired validity dates.             │
│ Permission Status               │ Explicit PERMITTED_STATUTORY_DISCLOSURE    │
│                                 │ or authenticated commercial license.       │
│ Currency Conversion             │ Official statutory central bank daily      │
│                                 │ fixing matching transit date.              │
│ Dynamic Environmental Inputs    │ Real-time operational data verified        │
│                                 │ (e.g. Gatun Lake official level).          │
│ Contractual Agency & Overtime   │ Valid Bilateral Agency Appointment on file.│
│ Cryptographic Token             │ Valid, non-expired Level 3 Release Token.  │
└─────────────────────────────────┴────────────────────────────────────────────┘
```

If any single prerequisite is missing, ambiguous, expired, or unverified, the engine must immediately fail closed, logging an auditable `blocked_reason` and returning `total_disbursement_usd = None`.

---

## 2. Verified Source and Permission Rules

Commercial voyage accounting differentiates between public statutory charges and proprietary commercial fee agreements.

### 2.1 Public Statutory Disbursements
- **Applicability:** Port dues, anchorage dues, canal tolls, mandatory statutory pilotage, and vessel traffic service (VTS) fees promulgated by official port authorities or sovereign canal bodies (e.g., MPA Singapore, Port of Rotterdam Authority, Port of Houston Authority, ACP, SCA).
- **Classification:** `PERMITTED_STATUTORY_DISCLOSURE`.
- **Evidence Threshold:** Must be supported by official gazettes, port circulars, published statutory schedules, or official authority web portals.
- **Operational Rule:** Directly eligible for inclusion in voyage disbursement calculations provided their effective validity covers the vessel transit date.

### 2.2 Restricted & Proprietary Disbursements
- **Applicability:** Terminal handling charges (THC), proprietary stevedoring tariffs, private berthage, and commercial tug escort tariffs under private operator concessions.
- **Classification:** `RESTRICTED_COMMERCIAL_LICENSE` or `RESTRICTED_CONFIDENTIAL`.
- **Evidence Threshold:** Valid bilateral terminal license, service agreement, or terminal schedule authenticated by digital signature.
- **Operational Rule:** Research-tier systems must flag such records as `RESTRICTED_PERMISSION`. They may never be disclosed across multi-tenant feeds without explicit enterprise organization permission.

---

## 3. Central Bank & Statutory FX Fixings

Operational voyage accounting must eliminate FX slippage and unhedged currency basis risks.

### 3.1 Authorized Primary Rate Providers
Only sovereign central banks and statutory intergovernmental bodies are authorized for operational currency conversion:
1. **SDR (Special Drawing Rights):** International Monetary Fund (IMF) Daily SDR Valuation Fixings (`IMF Daily SDR Valuation`).
2. **EUR (Euro):** European Central Bank (ECB) Reference Rates (`European Central Bank (ECB) Reference Rate`).
3. **SGD (Singapore Dollar):** Monetary Authority of Singapore (MAS) Daily Rates (`Monetary Authority of Singapore (MAS) Daily Rates`).
4. **USD (United States Dollar):** Identity valuation (rate = 1.0, provider = `"IDENTITY"`).

### 3.2 Non-Trading Days and Holiday Governance
- On weekends and statutory bank holidays (e.g., European Labour Day, Good Friday, Lunar New Year), central banks do not publish daily fixings.
- **Strict Rule:** Synthetic linear interpolations and third-party benchmark feeds (e.g., WM/Reuters, Open Exchange Rates) are classified as `PARTIALLY_VERIFIED` benchmarks.
- **Operational Boundary:** In operational mode, synthetic benchmarks are **strictly rejected**. The engine must apply the officially codified standard rule: the most recent preceding authoritative official business-day fixing, or an explicit fail-closed error (`BENCHMARK_EXCHANGE_RATE_REJECTED`).

### 3.3 Line-Item Audit Provenance
Every persisted line item must store:
- `exchange_rate_to_usd` (DECIMAL(12, 6))
- `fx_rate_provider` (VARCHAR(128))
- `fx_observation_date` (DATE)

Inference of FX dates from voyage arrival or transit dates without explicit provider tracking is prohibited.

---

## 4. Vessel Particulars & SCNT Verification

Accurate tonnage measurement is paramount for canal tolls and gross-tonnage-based port dues.

### 4.1 Strict Zero-Synthetic-Tonnage Invariant
- **Panama Canal:** Requires official Length Overall (LOA), extreme beam, and registered Deadweight Tonnage (DWT) or PC/UMS certificate.
- **Suez Canal:** Requires certified Suez Canal Net Tonnage (SCNT).
- **Prohibition:** Estimating, regressing, or synthesizing SCNT from Gross Tonnage (GT) or DWT via linear ratios (e.g. $SCNT \approx 0.95 \times GT$) is strictly forbidden in both research and operational tiers.
- **Fail-Closed Gate:** When certified SCNT is absent, `SuezTollCalculationResult` returns `scnt_applied = None`, `total_toll_usd = None`, and appends `MISSING_CERTIFIED_SCNT`.

### 4.2 Reconciliation of Vessel Records
All vessel particulars must reconcile with verified classification society certificates on file. For split or contested records (e.g. *BERGE GALDHOPIGGEN*), unverified dimensions remain `SOURCE_REPORTED`, preventing automated operational toll clearance until verified by physical survey or builder specification.

---

## 5. Completeness & Partial Disbursement Acceptance

### 5.1 The Partial Component Problem
In ports such as Rotterdam and Houston, statutory pilotage tariffs feature decentralized, multi-tariff structures:
- **Rotterdam:** Regional base pilotage tariffs (R-rates) are published, but specific vessel distance-tier S-rates depend on unpublished internal river pilot associations.
- **Houston:** Base pilotage rates are regulated by the Board of Pilot Commissioners for Harris County Ports, but vessel-draft overage and dock-specific maneuvering surcharges vary.

### 5.2 Propagation and Gating Rules
When any tariff component is partial or estimated:
1. `PortDisbursementLineItem.is_partial_component` is flagged `True`.
2. `PortDisbursementCalculationResult.has_partial_components` evaluates to `True`.
3. `VoyageDisbursementSummary.completeness_status` is forced to `PARTIAL_COMPONENTS`.
4. `VoyageDisbursementSummary.is_complete` is forced to `False`.
5. **Operational Rejection:** The record is ineligible for Level 3 operational sign-off. TCE calculations must not execute against incomplete or partial disbursements without an explicit user override waiver.

---

## 6. Commercially Negotiated & Contractual Charges

Statutory disbursements constitute only a portion of total port disbursement accounts (PDA). Commercial voyages incur contractual agency and operational expenses that must not be hallucinated as statutory dues.

### 6.1 Agency Remuneration
- **Nature:** Commercially negotiated bilateral agreement governed by BIMCO, FONASBA, or local national ship agents' associations.
- **Governance Invariant:** Agency fees must **never** be generated automatically from public port authority tables.
- **Requirement:** Must be injected via explicit contractual parameter (`contractual_agency_fee_usd`) accompanied by commercial appointment metadata.

### 6.2 Ancillary Operational Services
- **Pilotage Overtime & Tug Surcharges:** Night-time navigation, holiday surcharges, and adverse weather tug requirements are dynamic operational costs.
- **Demurrage & Despatch:** Laytime reconciliation is contractual under charterparty terms (Gencon, Norgrain, Amwelsh) and must remain decoupled from baseline port disbursement calculations.
- **Line Handling & Mooring:** Must be categorized as either statutory (e.g., Suez Circular 1/2024 mandatory $3,500 mooring fee) or contractual private terminal linesmen services.

---

## 7. Commercial Fixture Input Validation

In Stage D10, chartering intelligence allowed external callers to supply unverified fixture estimates via `VerifiedCharterInputs` (e.g. `port_disbursements_usd`, `canal_tolls_usd`).

### 7.1 Cross-Validation Architecture
In Stage F1.4.3, external commercial inputs must be cross-referenced against the Pelagos statutory registry:

```
                  [External Commercial Input]
                    VerifiedCharterInputs
                              │
                              ▼
        ┌───────────────────────────────────────────┐
        │ Fixture Validation Engine                 │
        │ - Match port calls against UN/LOCODEs     │
        │ - Match canal routing against ACP/SCA     │
        │ - Check user estimates vs. Registry floor │
        └─────────────────────┬─────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
       [Plausible Range]              [Material Variance > 15%]
     Flagged VALIDATED_FIXTURE      Flagged VARIANCE_WARNING
     Linked to Disbursement ID      Audit Trail Required
```

### 7.2 Validation Protocols
1. **Zero-Attribution Rejection:** Inputs with missing or generic provenance (e.g. `source_provenance = "ESTIMATE"`) are rejected from operational TCE.
2. **Registry Floor Checks:** If a user supplies `canal_tolls_usd = 150000.0` for a laden Capesize Suez transit where statutory base dues alone exceed $400,000 SDR, the engine raises `FIXTURE_VALUATION_OUT_OF_BOUNDS`.
3. **Traceable ID Binding:** Operational fixtures must link directly to an existing, verified `VoyageDisbursementRecord.id`.

---

## 8. Auditability, Provenance & Record Keeping

Every disbursement assessment must provide tamper-evident cryptographic traceability.

### 8.1 Schema Architecture
The persistence layer comprises two relational entities linked by cascading foreign keys:
- `VoyageDisbursementRecord`: Stores voyage itinerary, vessel identification, dimensions, aggregated totals, readiness status, and complete JSON summaries (`blocked_reasons_json`, `evidence_summary_json`).
- `VoyageDisbursementLineItem`: Granular charge breakdown storing charge categories, statutory tariff codes, billing units, currency, local amounts, FX conversion details, and evidence classifications.

### 8.2 Cryptographic Content Hash
To ensure immutability:
- Each `VoyageDisbursementRecord` computes an SHA-256 payload hash across all normalized line items:
  $$\text{Payload Hash} = \text{SHA256}(\text{VoyageID} \parallel \text{TransitDate} \parallel \sum \text{LineItemHash})$$
- Modifying a persisted line item invalidates the record hash, triggering an automated audit failure.

---

## 9. Formal Cryptographic Level 3 Operational Unlock

To prevent accidental premature operational execution, Level 3 operational status requires a formal cryptographic unlock mechanism.

### 9.1 Release Token Architecture
Operational execution is blocked at runtime by an explicit token verification gate:

```python
class OperationalUnlockGate:
    """Cryptographic gate for unlocking Level 3 operational calculations."""
    
    @staticmethod
    def verify_operational_token(
        token: str,
        voyage_id: str,
        vessel_imo: int,
        public_key: str,
    ) -> bool:
        """
        Verify that the operational token is cryptographically signed by an
        authorized Pelagos Governance Auditor and has not expired.
        """
        ...
```

### 9.2 Token Requirements
1. **Asymmetric Signature:** Token signed by Pelagos Governance Authority private key (RSA-4096 or Ed25519).
2. **Contextual Binding:** Token payload explicitly binds `voyage_id`, `vessel_imo`, `transit_date`, and approved `disbursement_record_id`.
3. **Expiration:** Token valid for a maximum of 72 hours from issuance.
4. **Revocation Check:** Checked against an on-chain or local CRL (Certificate Revocation List).

Without a valid cryptographic token, any call passing `operational_mode = True` immediately fails closed, preserving research isolation.

---

## 10. Fail-Closed Boundaries & Safety Invariants

The following invariants are inviolable across the entire Pelagos platform:

1. **Safety Over Optimization:** Incomplete, unverified, or ambiguous data must always fail closed (`total_disbursement_usd = None`).
2. **Zero Synthetic Tonnage:** Never synthesize, interpolate, or extrapolate certified canal tonnages (PC/UMS, SCNT) from gross tonnage or deadweight.
3. **Zero Benchmark Substitution:** Never substitute central bank statutory fixings with third-party mid-market benchmarks or holiday interpolations in operational mode.
4. **Decoupled Components:** Never lump distinct statutory tariffs (fixed fee, capacity fee, freshwater surcharge, mooring fees, pilotage) into aggregate lump sums.
5. **Contractual Separation:** Never treat negotiated agency remuneration, demurrage, or private terminal fees as statutory port authority dues.
6. **Commercial TCE Isolation:** Research-tier disbursements (`readiness_tier = LEVEL_2_RESEARCH_ONLY`) are strictly prohibited from entering commercial TCE calculations, net freight yields, or charter recommendation rankings.
7. **Additive Immutability:** Historical audit records, source inventories, and persisted disbursement ledgers must never be overwritten, reset, or destructively modified.

---

## Implementation Roadmap & Transition to F1.4.3

```
[Phase 1: Verification Acceptance]
├── Close FINDING-F1424-M01 & FINDING-F1424-L01
├── Verify 100% test pass rate across all suites
└── Register F1.4.3 Readiness Design in docs/INDEX.md

[Phase 2: F1.4.3 Foundation Engine]
├── Implement Fixture Validation Engine
├── Implement Cryptographic Operational Unlock Gate
└── Integrate VoyageDisbursementRecord with VoyageCalculator

[Phase 3: Operational Settlement Gate]
├── Conduct multi-party independent audit
├── Issue Stage F1.4.3 verification gate report
└── Formally unlock Level 3 operational commercial TCE
```

