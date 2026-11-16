# Pelagos — Stage F1.4.3 Phase 1 Implementation Report
## Voyage Readiness & Evidence Validation Foundation

**Document ID:** `REPORT-F143-PHASE1-001`  
**Stage ID:** Stage F1.4.3 (Phase 1: Readiness & Evidence Validation Foundation)  
**Parent Specifications:** `DOC-F143-DESIGN-001`, `DOC-F143-DESIGN-REVIEW-001`  
**Accepted Predecessor:** `VERIFY-F1424-REMED-001` (Stage F1.4.2.4 Final Remediation Verification)  
**Baseline Git Commit:** `2929e61f1f0fdfeb30af1a02889071805aa29328`  
**Status:** Approved Implementation Report  
**Execution Date:** 2026-11-15  

---

## Executive Summary

Stage F1.4.3 Phase 1 implements the complete, deterministic, auditable, fail-closed voyage readiness and evidence validation foundation for the Pelagos platform. Built strictly upon the accepted discovery baseline (`2929e61`) and governed by `DOC-F143-DESIGN-001` and its approved review addendum `DOC-F143-DESIGN-REVIEW-001`, this milestone establishes rigorous validation across all five foundational domains without unlocking Level 3 operational calculations or releasing commercial TCE integration.

All six authorized scopes have been implemented and verified with 100% test pass rates:
1. **Scope A — Canonical Central-Bank FX Valuation & Lookback Provenance:** Codified provider-specific calendar horizons (IMF SDR 4d, ECB EUR 4d, MAS SGD 5d, USD Identity 0d); strictly rejected future observations ($D_{\text{obs}} > D_{\text{req}}$) and stale observations ($\Delta t > T_{\text{max}}$); enforced that identity conversions are mathematically 1.0 without asserting external market observation dates; rejected synthetic mid-market benchmark substitutions in operational mode (`BENCHMARK_EXCHANGE_RATE_REJECTED`).
2. **Scope B — Vessel Statutory Particulars Readiness:** Implemented multi-dimensional evidence verification (DWT, GT, NT, LOA, Beam, Draft, SCNT, PC/UMS) across governing authorities (ACP, SCA, MPA, Havenbedrijf, PHA/Houston Pilots); strictly enforced zero synthetic tonnage (rejecting synthetic/interpolated tonnages); preserved conflicting observations without arbitrary resolution; maintained unpromoted `SOURCE_REPORTED` status for Berge Galdhopiggen.
3. **Scope C — 10-Criteria Port & Canal Tariff Applicability Validation:** Implemented systematic evaluation across 10 statutory criteria (existence, provenance, effective dates, expiry/supersession, vessel applicability, currency, source permission, component completeness, conditional charges, partial components); correctly surfaced Rotterdam missing River S-rate pilotage and Houston draft-overage partials.
4. **Scope D — Four-Tier Charge Decomposition & One-Way Statutory Floor Enforcement:** Segregated line items into Tier 1 (Statutory Dues, zero tolerance), Tier 2 (Regulated Services, tariff reconciled), Tier 3 (Contractual Remuneration, requiring explicit contract attribution), and Tier 4 (Provisional Estimates, permanently barred from operational approval); implemented one-way statutory floor enforcement with zero percentage tolerance for sub-floor valuations (`FIXTURE_VALUATION_BELOW_STATUTORY_FLOOR`), while categorizing unaccounted surplus as `UNALLOCATED_COMMERCIAL_OVERAGE`.
5. **Scope E — Unified Voyage Readiness Assessment Service:** Orchestrated Scopes A–D into a centralized service emitting definitive readiness classifications (`LEVEL_2_RESEARCH_READY`, `LEVEL_2_RESEARCH_INCOMPLETE`, `LEVEL_2_PARTIAL_COMPONENTS`, `LEVEL_2_QUALIFIED_COMMERCIAL_ESTIMATE`, `LEVEL_3_OPERATIONAL_BLOCKED`), complete with prominent statutory disclaimers for partial estimates.
6. **Scope F — Strict Operational Boundary:** Guaranteed that `is_ready_for_operations` evaluates strictly to `False` under all code paths, preserving complete isolation of preliminary research disbursements from commercial TCE engines.

---

## 1. Repository Safeguards & Baseline Audit

All work was conducted under strict repository safety invariants:
- **Baseline Integrity:** HEAD anchored to `2929e61f1f0fdfeb30af1a02889071805aa29328`.
- **Zero Destructive Actions:** No commits, pushes, rebase, resets, clean, or discard operations were performed.
- **Production Asset Safety:** Production database `pelagos.db`, Parquet data stores, `.env`, `.obsidian/`, and scratch validation tools were strictly preserved. All tests ran on transient in-memory or temporary SQLite instances.
- **Tracked File Reconciliations:** Tracked modifications remain strictly confined to additive schema migrations in `backend/pelagos/database.py`, additive schema columns in `backend/pelagos/models.py`, and version registry updates in `docs/INDEX.md`.

---

## 2. Scope A: Canonical FX Valuation & Lookback Provenance Engine

Implemented in `backend/pelagos/currency.py`.

### 2.1 Provider-Specific Calendar Lookback Horizons
Foreign exchange markets and sovereign central banks operate on strict jurisdictional business calendars. Synthetic linear interpolation is permanently barred from operational execution. Deterministic calendar-lag horizons are codified via `PROVIDER_MAX_LOOKBACK_CALENDAR_DAYS`:

$$\begin{aligned}
T_{\text{max}}(\text{IMF / SDR}) &= 4 \text{ calendar days (Weekend + Federal Holiday)} \\
T_{\text{max}}(\text{ECB / EUR}) &= 4 \text{ calendar days (Easter Weekend)} \\
T_{\text{max}}(\text{MAS / SGD}) &= 5 \text{ calendar days (Multi-day Lunar New Year)} \\
T_{\text{max}}(\text{Identity / USD}) &= 0 \text{ calendar days (Static identity match)}
\end{aligned}$$

### 2.2 Dual-Date Provenance Architecture
Every conversion records both dates and exact lag:
- `requested_valuation_date` ($D_{\text{req}}$): Event date (canal transit or port call).
- `actual_observation_date` ($D_{\text{obs}}$): Authoritative central bank fixing publication date.
- `observation_lag_days` ($\Delta t = D_{\text{req}} - D_{\text{obs}}$).

### 2.3 Strict Fail-Closed Validation Rules
1. **Future Lookahead Violation:** If $D_{\text{obs}} > D_{\text{req}}$, conversion immediately fails with:  
   `FUTURE_EXCHANGE_RATE_OBSERVATION_REJECTED`.
2. **Excessive Observation Lag:** If $\Delta t > T_{\text{max}}$, conversion fails with:  
   `EXPIRED_OR_STALE_CURRENCY_OBSERVATION`.
3. **Identity Provenance Purity:** Identity conversions set `rate_provider = "IDENTITY"`, `exchange_rate = 1.0`, `actual_observation_date = None`, and `effective_date = None`. Identity conversions never claim an external central bank fixing publication.
4. **Benchmark Rejection in Operational Mode:** Rates marked `is_official_fixing = False` (e.g. European Labour Day mid-market interpolation) fail closed in operational mode with `BENCHMARK_EXCHANGE_RATE_REJECTED`.

---

## 3. Scope B: Vessel Statutory Particulars Readiness Engine

Implemented in `backend/pelagos/readiness/vessel_readiness.py`.

### 3.1 Multi-Dimensional Evidence Assessment
Evaluates vessel particulars across 8 key physical and statutory dimensions:
- Deadweight Tonnage (`dwt`)
- Gross Tonnage (`gt`)
- Net Tonnage (`nt`)
- Length Overall (`loa_m`)
- Extreme Beam (`beam_m`)
- Max Draft (`draft_m`)
- Certified Suez Canal Net Tonnage (`scnt`)
- Panama Canal Universal Measurement System (`pc_ums_net`)

### 3.2 Authority-Specific Dimensional Requirements
- **Panama Canal (ACP):** LOA, Beam, DWT mandatory.
- **Suez Canal (SCA):** Certified SCNT, Draft, Beam mandatory.
- **Singapore (MPA):** GT mandatory.
- **Rotterdam (Havenbedrijf):** GT mandatory (plus cargo tonnage for cargo calls).
- **Houston (PHA / Houston Pilots):** LOA, Draft, GT mandatory.

### 3.3 Zero Synthetic Tonnage & Governance Invariants
- **Zero Synthetic Tonnage Invariant:** SCNT and PC/UMS must never be synthesized or interpolated from GT or DWT. Any attempt to supply synthetic tonnages is rejected with `SYNTHETIC_TONNAGE_REJECTED`.
- **Conflict Retention:** If disparate sources report conflicting values exceeding statutory tolerances, the conflict is preserved as `CONFLICTING_PARTICULARS_RETAINED` and never resolved arbitrarily.
- **Unpromoted Evidence Classifications:** Source-reported attributes (such as Berge Galdhopiggen DWT/LOA) remain strictly `SOURCE_REPORTED` and are never promoted to `DIRECTLY_VERIFIED` without primary classification certificates.

---

## 4. Scope C: Port & Canal Tariff Applicability Validation Engine

Implemented in `backend/pelagos/readiness/tariff_validation.py`.

### 4.1 Ten Statutory Validation Criteria
The engine deterministically validates tariffs against 10 criteria:
1. **Existence:** Tariff code exists in the official statutory registry.
2. **Authority & Source Provenance:** Verified source document citation and authoritative URL.
3. **Effective-Date Applicability:** Transit date falls within published effective window ($D_{\text{eff\_from}} \le D_{\text{transit}} \le D_{\text{eff\_to}}$).
4. **Expiry / Supersession:** Tariff is not superseded by subsequent gazetted circulars.
5. **Vessel Applicability:** Vessel GT, DWT, LOA, beam, and draft fall within tier brackets.
6. **Currency Conversion:** Authoritative FX fixing is available within provider lookback horizon.
7. **Source Permission:** Verified public statutory disclosure vs. proprietary/restricted data.
8. **Component Completeness:** All mandatory component dues (e.g. fixed, variable, mooring) are present.
9. **Conditional Charges:** Dynamic operational inputs (e.g. previous-day 1200h Gatun Lake elevation) identified.
10. **Partial-Component Identification:** Missing tariff components (e.g. Rotterdam River Pilotage S-rate, Houston draft overage) explicitly flagged.

---

## 5. Scope D: Four-Tier Charge Decomposition & Statutory Floor Enforcement

Implemented in `backend/pelagos/readiness/charge_classifier.py`.

### 5.1 Four-Tier Governance Taxonomy
Every disbursement charge is decomposed into one of four distinct tiers:

| Tier | Category | Evidence Nature | Operational Clearance Rule |
| :--- | :--- | :--- | :--- |
| **Tier 1** | Statutory Dues (Port Dues, Canal Tolls, VTS) | Public Law / Official Gazettes | ZERO TOLERANCE: Must match statutory registry exactly (+/- 0.00). |
| **Tier 2** | Regulated Ancillary Services (Pilotage, Mooring, FWS) | Published Scales / Tariff Caps | TARIFF RECONCILED: Base dues and regulated rates must reconcile. |
| **Tier 3** | Contractual Remuneration (Agency Fees, Launch, Surveyor) | Bilateral Commercial Contracts | CONTRACT ATTRIBUTED: Requires valid bilateral contract/appointment ID. Missing ID produces `MISSING_CONTRACTUAL_EVIDENCE`. |
| **Tier 4** | Provisional Estimates (Demurrage, Contingencies, Extras) | Volatile Operational Provisions | QUALIFIED ESTIMATE: Permanently provisional (`is_provisional = True`), excluded from operational settlement. |

### 5.2 One-Way Statutory Floor Enforcement
External commercial fixtures (e.g. charterer/broker estimates) are cross-referenced against Pelagos statutory baselines:
- **Zero Tolerance on Sub-Floor Deficit:** If a user supplies a valuation below the statutory baseline calculated by Pelagos, the input fails closed immediately:
  $$V_{\text{user}} < V_{\text{statutory}} \implies \text{REJECTED: } \texttt{FIXTURE\_VALUATION\_BELOW\_STATUTORY\_FLOOR}$$
  Percentage tolerance bands are strictly rejected — even a \$1 deficit fails closed.
- **Non-Automatic Validation of Surplus:** If $V_{\text{user}} > V_{\text{statutory}}$, the excess does not prove accuracy. Excess must be itemized to Tier 2/3/4 charges. Unexplained excess is flagged as `UNALLOCATED_COMMERCIAL_OVERAGE` and barred from operational clearance until itemized.

---

## 6. Scope E: Unified Voyage Readiness Assessment Service

Implemented in `backend/pelagos/readiness/voyage_readiness_service.py`.

### 6.1 Unified Readiness Classifications
The service integrates Scopes A–D to produce definitive voyage readiness classifications:
- `LEVEL_2_RESEARCH_READY`: All vessel particulars verified/reported without synthesis, all tariffs active and complete, official central bank FX observed within lookback horizons, and charges classified with floors satisfied.
- `LEVEL_2_RESEARCH_INCOMPLETE`: Any mandatory vessel attribute (e.g. SCNT for Suez, LOA for Panama), tariff, or FX observation is missing or stale.
- `LEVEL_2_PARTIAL_COMPONENTS`: Mandatory tariffs exist but contain known partial components (e.g. Rotterdam river pilotage). Carries the mandatory disclaimer:
  > *"QUALIFIED COMMERCIAL ESTIMATE: Contains unverified or partial statutory port/canal charges. Not certified for contractual final disbursement settlement."*
- `LEVEL_2_QUALIFIED_COMMERCIAL_ESTIMATE`: Contains Tier 3 contractual items lacking contract attribution, Tier 4 provisional estimates, or unallocated commercial overage.
- `LEVEL_3_OPERATIONAL_BLOCKED`: Emitted whenever operational clearance is requested in Phase 1.

---

## 7. Scope F: Strict Operational Boundary

- **Inviolate Level 3 Lock:** `is_ready_for_operations` evaluates strictly to `False` under all conditions.
- **Fail-Closed Execution:** Any attempt to invoke operational TCE calculations in Phase 1 fails closed with `PHASE_1_OPERATIONAL_RELEASE_GATE_LOCKED`.
- **Additive Database Compatibility:** Persisted records maintain strict audit trails via additive columns on `VoyageDisbursementRecord` and `VoyageDisbursementLineItem` (`charge_tier`, `contract_attribution`, `is_provisional`, `requested_valuation_date`, `fx_observation_lag_days`, `conversion_evidence_classification`, `unallocated_overage_usd`, `disclaimer`).

---

## 8. Verification & Test Suite Execution

A dedicated, comprehensive test suite was developed in `tests/test_f143_readiness.py`, comprising 25 exhaustive unit and integration tests covering all Phase 1 requirements.

### 8.1 F1.4.3 Readiness Test Suite Results
```
tests/test_f143_readiness.py::test_fx_identity_conversion_provenance PASSED
tests/test_f143_readiness.py::test_fx_provider_lookback_within_limit PASSED
tests/test_f143_readiness.py::test_fx_mas_lookback_5_days_permitted PASSED
tests/test_f143_readiness.py::test_fx_future_observation_rejected PASSED
tests/test_f143_readiness.py::test_fx_stale_observation_exceeding_lookback_rejected PASSED
tests/test_f143_readiness.py::test_fx_benchmark_rejected_in_operational_mode PASSED
tests/test_f143_readiness.py::test_vessel_readiness_complete_capesize PASSED
tests/test_f143_readiness.py::test_vessel_readiness_suez_missing_scnt_fails_closed PASSED
tests/test_f143_readiness.py::test_vessel_readiness_zero_synthetic_tonnage_enforcement PASSED
tests/test_f143_readiness.py::test_vessel_readiness_conflicting_particulars_retained PASSED
tests/test_f143_readiness.py::test_vessel_readiness_source_reported_never_promoted PASSED
tests/test_f143_readiness.py::test_tariff_validation_valid_itinerary PASSED
tests/test_f143_readiness.py::test_tariff_validation_unregistered_port_fails PASSED
tests/test_f143_readiness.py::test_tariff_validation_conditional_freshwater_surcharge PASSED
tests/test_f143_readiness.py::test_tariff_validation_partial_pilotage_components PASSED
tests/test_f143_readiness.py::test_charge_tier_decomposition PASSED
tests/test_statutory_floor_below_floor_fails_closed PASSED
tests/test_statutory_floor_zero_tolerance_on_subfloor PASSED
tests/test_statutory_floor_unallocated_overage_flagged PASSED
tests/test_statutory_floor_surplus_itemized_reconciled PASSED
tests/test_voyage_readiness_clean_research_ready PASSED
tests/test_voyage_readiness_missing_vessel_scnt_incomplete PASSED
tests/test_voyage_readiness_partial_components_status_and_disclaimer PASSED
tests/test_voyage_readiness_operational_mode_strictly_blocked PASSED
tests/test_voyage_readiness_serialization_to_dict PASSED

============================= 25 passed in 1.82s ==============================
```

### 8.2 Full Platform Regression Execution
All 10 core test suites were executed concurrently, covering 162 total tests:
- `tests/test_canal_tariffs.py` (17 tests) — PASSED
- `tests/test_port_disbursements.py` (11 tests) — PASSED
- `tests/test_voyage_disbursements.py` (20 tests) — PASSED
- `tests/test_f143_readiness.py` (25 tests) — PASSED
- `tests/test_vessel_registry.py` (14 tests) — PASSED
- `tests/test_vessel_fuel_model.py` (14 tests) — PASSED
- `tests/test_voyage_calculator.py` (22 tests) — PASSED
- `tests/test_charter_intelligence.py` (21 tests) — PASSED
- `tests/test_machinery_registry.py` (9 tests) — PASSED
- `tests/test_engine_sfoc.py` (9 tests) — PASSED

**Result:** `162 passed in 31.67s (100% pass rate, zero regressions)`.

---

## 9. Deliverables & Version Registry

1. **Source Implementations:**
   - `backend/pelagos/currency.py` (Scope A)
   - `backend/pelagos/readiness/vessel_readiness.py` (Scope B)
   - `backend/pelagos/readiness/tariff_validation.py` (Scope C)
   - `backend/pelagos/readiness/charge_classifier.py` (Scope D)
   - `backend/pelagos/readiness/voyage_readiness_service.py` (Scope E)
   - `backend/pelagos/readiness/__init__.py`
   - `backend/pelagos/models.py` (Additive schema columns)
   - `backend/pelagos/database.py` (Additive migration execution)
2. **Test Suites:**
   - `tests/test_f143_readiness.py` (25 tests)
3. **Documentation Deliverables:**
   - `docs/F1_4_3_PHASE1_IMPLEMENTATION_REPORT.md` (`REPORT-F143-PHASE1-001`)
   - `docs/INDEX.md` (bumped to v1.0.37)

