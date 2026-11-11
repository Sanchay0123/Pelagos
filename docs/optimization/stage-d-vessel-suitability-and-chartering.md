---
title: "Stage D: Vessel Suitability & Chartering Intelligence Specification (D10 / D10.2)"
document_id: OPT-001
type: architecture_spec
status: under_review
version: 1.0.0
created: 2026-11-11
updated: 2026-11-11
tags:
  - optimization
  - chartering
  - vessel-suitability
  - voyage-calculator
  - stage-d
  - milestone-d10
---

# Stage D: Vessel Suitability & Chartering Intelligence Specification

## 1. Purpose
This specification documents the implemented backend Stage D capabilities in `pelagos.optimization` and `pelagos.api.routes`, spanning **Milestone D10 (`OPT-001`)** Voyage Freight-Cost Calculation, **Milestone D10.2 (`OPT-002`)** Vessel Suitability Matching & Compatibility Hardening, and the fail-closed **`/api/v1/charter/assess`** and **`/api/v1/charter/recommend`** (`ARCH-001` Section 5.2) readiness interfaces.

Its governing design invariant is strict commercial and data integrity: physical vessel screening, port/canal checks, and freight cost analytics must run solely on genuine database records, never invent synthetic DWT, draft, port berth limits, bunker quotes, or open-tonnage availability, and withhold actionable chartering decisions (`insufficient_data` / `unavailable`) wherever prerequisite authentic data feeds are absent.

---

## 2. Scope

### Implemented & Hardened Scope
1. **Milestone D10 / `OPT-001` (`calculator.py`, `/api/v1/calculator/voyage-freight`):** Deterministic gross ocean freight calculation (`cargo_tonnes * rate_per_tonne`) over monitored agricultural dry bulk grain export corridors (`PNW-JPN-BULK`, `USG-JPN-BULK`).
2. **Milestone D10.2 / `OPT-002` (`matcher.py`, `/api/v1/calculator/vessel-suitability`):** Candidate vessel physical screening reconciled across canonical `Vessel` and `VesselPosition` records, explicit `missing_particulars` accounting, cargo compatibility, documented port channel draft enforcement, sea-lane/canal compatibility (`MaritimeRoutingEngine`), and newest-first deterministic tie-breaking.
3. **Stage D Vessel Assessment & Chartering Gate (`chartering.py`, `/api/v1/charter/assess`, `/api/v1/charter/recommend`):** Single-vessel voyage compatibility evaluation and six-gate chartering recommendation engine supporting deterministic multi-contract evaluation only when complete verified fixture/economic inputs exist.

### Explicitly Blocked / Unimplemented Scope
- Automated live open-tonnage broker fixture ingestion, live vessel charter-party/employment tracking, hold-cleanliness certification, and live port turnaround/demurrage tariffs.
- Automated maritime sanctions screening against OFAC/EU/UN watchlists ([`REQ-NFR-SBL-002`](../requirements/nonfunctional-requirements.md)).
- Persistent `charter_audit_log` snapshot storage ([`REQ-NFR-OAS-002`](../requirements/nonfunctional-requirements.md)).
- Port-pair forward cargo volume estimation ([`REQ-FR-CDV-001`](../requirements/functional-requirements.md)).

---

## 3. Background and Context

Inspection of live `pelagos.db` tables establishes the exact empirical operating boundary for Stage D:
- **`vessels` & `vessel_positions`:** `120` synced rows (`120` distinct MMSIs) from NOAA MarineCadastre / USCG NAIS (`2024-01-01 00:00:00Z..00:00:10Z`, coastal U.S. & Great Lakes sample). Only **`7` of `120`** carry AIS cargo type codes `70–79` (including Great Lakes vessels such as `EDGAR B SPEER` and `AMERICAN COURAGE`, and `3` `Cargo - Hazardous A` code `71` vessels such as `MSC ATHOS` and `COSCO KAOHSIUNG`).
- **Registry Gaps:** **`0/120`** vessels have populated `dwt_tonnage`, `gross_tonnage`, `built_year`, or `flag_country`; **`24/120`** lack `draft_meters` (including `AMERICAN COURAGE`); **`0/120`** carry AIS `destination`.
- **Registry Sync Status vs. Commercial Availability:** `vessel_registry.sync_vessels_from_positions()` persists `Vessel.commercial_status = "active"` as an AIS ingestion sync indicator (tested by existing data fusion tests), which does **not** establish commercial spot charter availability or open-tonnage readiness.
- **Forecasting Baseline:** Stage C3.2 and Stage C3.3 confirmed that no ML or regime-aware candidate significantly beats `persistence` (`DEFAULT_BASELINE_MODEL = "persistence"`), yielding identical T+1, T+3, and T+6 forecast values (`$38.00/MT` PNW, `$76.00/MT` USG as of `2026-10-01`) with zero directional timing edge.
- **Bunker Observations:** `bunker_price_observations` contains `5` external indicative quotes at `ARBUE` and `DKSKA` (`0` quotes at U.S. PNW, U.S. Gulf, or Japan corridor bunkering hubs).

---

## 4. Requirements and Constraints Traceability

| Requirement / Reference | Module / Endpoint | Stage D Enforcement |
|---|---|---|
| `REQ-FR-DII-002`, `REQ-FR-DII-003` | `matcher.py`, `chartering.py` | Preserves provenance, timestamps, historical AIS flags, and non-fabricated attributes. |
| `REQ-FR-OMR-001`, `REQ-FR-OMR-002` | `matcher.py`, `MaritimeRoutingEngine` | Rejects disconnected/landlocked routes (`CASP_TEST -> JPYOK`) without geometric fallback; marks unverified berth/canal limits as `insufficient_data`. |
| `REQ-FR-CDV-002` | `chartering.py` | Enforces open/clean fixture check (`laden`/`drydock` pruned) and laycan ETA cutoff when verified fixtures are supplied; strictly `UNCONFIRMED`/`unavailable` on raw AIS. |
| `REQ-FR-CPO-001`, `REQ-FR-CPO-002` | `chartering.py` (`/api/v1/charter/recommend`) | Evaluates six readiness gates; emits `Fix Now`/`Wait`/`Hedge` and Spot vs. COA vs. TC comparisons strictly when all prerequisite inputs are verified, else returns `insufficient_data` or `unavailable`. |
| `REQ-FR-CPO-003` | `matcher.py`, `chartering.py` | Enforces documented port draft limits, canal lock geometry, verified DWT capacity, and explicit berth limits; refuses unverified berth clearance assertions. |
| `REQ-NFR-DIP-001`, `REQ-NFR-ARG-001` | All `pelagos.optimization` modules | Zero synthetic DWT/bunker/fixture substitution; graceful degradation to structured missing-data states. |

---

## 5. Design & Component Specification

### 5.1 Milestone D10 / `OPT-001` — Voyage Freight-Cost Calculator
Implemented in [`calculator.py`](../../backend/pelagos/optimization/calculator.py) and exposed at `GET`/`POST /api/v1/calculator/voyage-freight` ([`routes/calculator.py`](../../backend/pelagos/api/routes/calculator.py)):
- Validates positive cargo tonnage (`cargo_tonnes > 0`) and mass units (`MT`).
- Resolves either `route_code` or `(origin, destination)` via [`corridors.py`](../../backend/pelagos/routing/corridors.py); rejects derived spreads (`SPREAD-USG-PNW-JPN`) and unmonitored lanes (`NLRTM -> SGSIN`) with `CalculationStatus.UNAVAILABLE`.
- Fetches latest non-derived [`FreightObservation`](../../backend/pelagos/models.py) up to optional `as_of_date`, validates currency/unit (`USD/MT`), calculates `gross_freight_cost = cargo_tonnes * rate_per_tonne`, attaches routing/benchmark distance (`NM`/`KM`), and explicitly omits Net TCE, bunker assumptions, or speculative timing advice.

### 5.2 Milestone D10.2 / `OPT-002` — Vessel Suitability Matching & Compatibility Hardening
Implemented in [`matcher.py`](../../backend/pelagos/optimization/matcher.py) and exposed at `GET`/`POST /api/v1/calculator/vessel-suitability`:

1. **Canonical `Vessel` + `VesselPosition` Reconciliation:**
   - Queries deduplicated latest `VesselPosition` records joined by `mmsi` with canonical `Vessel` rows.
   - Reconciles dimensions without fabrication (`VesselPosition` first, canonical `Vessel.draft_max_meters` fallback where present). Uses `Vessel.dwt_tonnage` when genuinely populated (`cargo_capacity_status = "VERIFIED"`, rejecting `cargo_tonnes > dwt_tonnage` as `DIMENSION_MISMATCH` / `verified_dwt_insufficient`); preserves `verified_cargo_capacity_dwt = None` (`UNVERIFIED`) when null.
   - Surfacing `registry_vessel_class` and `registry_commercial_status` separately prevents conflating `Vessel.vessel_class` or AIS registry presence (`"active"`) with `estimated_dimensional_class` or `commercial_availability`.

2. **Explicit Missing Particulars (`ParticularsCompletenessSummary`):**
   - `build_particulars_completeness()` classifies each vessel as `complete`, `partial`, or `insufficient_data` and enumerates `verified_particulars` vs. `missing_particulars` (explicitly listing `dwt_tonnage`, `draft_meters` when missing, `length_meters`/`width_meters`, `gross_tonnage`, `built_year`, `flag_country`, `hold_cleanliness`, `grain_cubic_capacity`, and `commercial_fixture_status`).

3. **Vessel-to-Cargo Compatibility (`CargoCompatibilityAssessment`):**
   - Filters non-cargo/liquid-bulk AIS vessel classes (`Towing`, `Tug`, `Tanker`, `Fishing`, `Passenger`, etc.).
   - Evaluates `cargo_tonnes` against verified `dwt_tonnage` if available, or screens AIS LOA/beam envelope (`Small / Coastal Cargo`, `Handysize`, `Handymax / Supramax`, `Panamax / Kamsarmax`, `Post-Panamax / Capesize`).
   - Preserves backward-compatible screening behavior while surfacing explicit warning notes on AIS Category `71` (`Cargo - Hazardous A`) vessels that food-grade clean grain hold readiness is unverified.

4. **Vessel-to-Route, Port & Canal Compatibility (`RouteCompatibilityAssessment`):**
   - **Operational Sea-Lane Connectivity:** Invokes [`MaritimeRoutingEngine`](../../backend/pelagos/routing/service.py) (`prototype` mode connectivity check) between `origin` and `destination`. Landlocked or disconnected routes (e.g., `CASP_TEST -> JPYOK`) yield `route_navigable = False`, `status = "unavailable"`, and `potentially_suitable_count = 0`.
   - **Documented Port Draft Limits (`extract_port_draft_limit_meters`):** Extracts only explicit numeric draft/project-depth limits documented in canonical [`Port`](../../backend/pelagos/models.py) `access_limitations` strings:
     - `USKAL` (Kalama): `43-foot draft` &rarr; **`13.11 m`** (e.g., prunes `COSCO KAOHSIUNG` at `13.8 m` draft; flags missing draft on `AMERICAN COURAGE` as `INSUFFICIENT_DATA`).
     - `USBTR` (Baton Rouge): `45-foot project depth` &rarr; **`13.72 m`**.
     - `INMUN` (Mundra): **`17.5 m`**.
     - Ports without documented numeric berth limits (`USSEA`, `JPYOK`, `USMSY`, `INPRT` entrance channel depth without UKC, etc.) explicitly report `status = "insufficient_data"` with missing berth draft/LOA/beam fields rather than fabricated defaults.
   - **Canal Passage Boundaries:** Evaluates `canals_transited` (`Panama Canal`, `Suez Canal`):
     - Flags Panama Canal beam > `32.31 m` (`TRADITIONAL_PANAMAX_MAX_BEAM_M`) as requiring Neopanamax locks and verified live ACP draft/slot advisories (`REQ-FR-OMR-001`), prunes structural exceedance above `51.25 m` beam or `366.0 m` LOA (`NEOPANAMAX_MAX_BEAM_M` / `NEOPANAMAX_MAX_LOA_M`), and marks Suez Canal passages subject to unverified live SCA convoy/security advisories.

5. **Deterministic Ranking & Tie-Breaker:**
   - Orders candidates by: (1) `suitability_status` tier (`POTENTIALLY_SUITABLE` > `INSUFFICIENT_DATA` > `DIMENSION_MISMATCH` > `UNSUITABLE_TYPE`), (2) `distance_to_load_port_nm` ascending, (3) newest `observation_time` first (`-timestamp`), and (4) `mmsi` ascending.

### 5.3 Vessel Assessment & Chartering Readiness Gate (`chartering.py`)
Exposed via [`routes/charter.py`](../../backend/pelagos/api/routes/charter.py) and registered at `/api/v1` in [`main.py`](../../backend/pelagos/api/main.py):

- **`GET` / `POST /api/v1/charter/assess` (`assess_vessel_for_voyage`):**
  Evaluates an individual vessel (either DB lookup by `mmsi` across `VesselPosition`/`Vessel` or transient `VesselParticularsInput`) against target cargo (`cargo_tonnes`, `commodity`) and corridor (`origin`, `destination`, `route_code`, `canal_policy`), returning separate `ais_observation_status`, `commercial_availability_status`, `particulars_completeness`, `cargo_compatibility`, and `route_compatibility` sections. Returns `vessel_not_found` for unknown MMSIs without creating synthetic records.

- **`GET` / `POST /api/v1/charter/recommend` (`recommend_charter_strategy`):**
  Implements a deterministic six-gate readiness audit before allowing any actionable charter recommendation:

| Gate ID | Upstream Data & Service | Live DB Outcome (`PNW-JPN-BULK` / `USG-JPN-BULK`) | Blocking Missing Items on Default DB |
|---|---|---|---|
| `corridor_and_freight_market` | `calculate_voyage_freight` (`FreightObservation`) | `available` (`$38.00/MT` / `$76.00/MT`) | None (`unavailable` on unmonitored corridors) |
| `directional_forecast_edge` | `forecast_next_route_rate` (`persistence`, T+1/T+3/T+6) | `insufficient_data` | `calibrated_directional_forward_rate_edge` |
| `operational_route_and_ports` | `MaritimeRoutingEngine`, `extract_port_draft_limit_meters` | `partial` | `{PORT}_verified_berth_*`, `panama_canal_live_authority_draft_and_slot_rules` |
| `vessel_physical_particulars` | `match_suitable_vessels` (`Vessel` + `VesselPosition`) | `insufficient_data` (`0` verified DWT) | `verified_vessel_dwt_tonnage`, `verified_hold_cleanliness`, `verified_grain_cubic_capacity`, `complete_cargo_vessel_draft_particulars` |
| `commercial_open_tonnage_and_laycan` | Commercial fixture / live AIS boundary | `unavailable` (Jan 2024 historical AIS) | `commercial_open_tonnage_list`, `live_vessel_ais_positions`, `laycan_itinerary_confirmation` |
| `bunker_and_voyage_economics` | `resolve_corridor_bunker_fuel_references`, charter quotes | `unavailable` (`0/3` or `0/4` hubs quoted) | `corridor_hub_bunker_price_quotes`, `vessel_fuel_consumption_curve`, `port_disbursements_and_canal_tolls`, `coa_and_time_charter_quotes` |

  **Decision Semantics:**
  - Unnavigable route or unmonitored corridor &rarr; `status = "unavailable"`, `recommendation_issued = False`, `recommended_action = None`.
  - Default `pelagos.db` monitored corridor (`verified_commercial_inputs = None`, or incomplete port limits) &rarr; `status = "insufficient_data"`, `recommendation_issued = False`, `recommended_action = None`, while exposing verified gross freight benchmark, persistence forecasts, bunker/environmental coverage, and dimensionally screened AIS candidates in `verified_supporting_analytics`.
  - Complete explicit `VerifiedCharterInputs` supplied: filters non-`open`/unclean vessels, checks `verified_dwt >= cargo_tonnes`, enforces port draft/LOA/beam & canal constraints (`REQ-FR-CPO-003`), prunes vessels arriving after `laycan_end` (`REQ-FR-CDV-002`), returns `unavailable` if zero candidates survive, or compares Spot Single Voyage vs. Consecutive Voyage Charter (COA) vs. Time Charter (TC) total and USD/MT costs to emit a deterministic `status = "recommended"` decision when every gate passes.

---

## 6. Dependencies and Interfaces

- **Internal Dependencies:** [`pelagos.models`](../../backend/pelagos/models.py), [`pelagos.routing`](../../backend/pelagos/routing/service.py), [`pelagos.forecasting.service`](../../backend/pelagos/forecasting/service.py), [`pelagos.optimization.calculator`](../../backend/pelagos/optimization/calculator.py), [`pelagos.optimization.matcher`](../../backend/pelagos/optimization/matcher.py), [`pelagos.optimization.chartering`](../../backend/pelagos/optimization/chartering.py).
- **REST API Endpoints:** `/api/v1/calculator/voyage-freight`, `/api/v1/calculator/vessel-suitability`, `/api/v1/charter/assess`, `/api/v1/charter/recommend`.

---

## 7. Failure Modes, Limitations & Data Dependencies

1. **Historical AIS Only:** All `120` vessel records in `pelagos.db` come from a 10-second `2024-01-01` U.S. coastal sample; distance to loading port is qualified as `HISTORICAL_OBSERVATION_ONLY` and cannot confirm present proximity or laycan ETA.
2. **Absent Fleet & Commercial Registry Data:** Genuine DWT, grain cubic capacity, hold cleanliness, vessel open/laden/drydock schedules, daily fuel consumption curves, and COA/TC hire rates are absent from connected feeds.
3. **Sparse Port & Canal Constraints:** Only `USKAL`, `USBTR`, and `INMUN` have documented channel/draft limits in canonical port metadata; terminal LOA/beam limits and dynamic ACP/SCA canal advisories remain unconnected.
4. **Corridor Bunker Coverage Gap:** `bunkerfuelprices.com` integration holds quotes for `ARBUE` and `DKSKA` only (`0` corridor hubs quoted).
5. **Unimplemented Compliance & Replay Modules:** Sanctions list matching (`REQ-NFR-SBL-002`) and immutable `charter_audit_log` persistence (`REQ-NFR-OAS-002`) remain unimplemented.

---

## 8. Security, Privacy & Licensing

- Vessel and port data originate from NOAA MarineCadastre / USCG NAIS, UNECE UN/LOCODE, NGA WPI, and Indian port authorities with provenance fields preserved cleanly; no credentials are embedded in optimization modules.

---

## 9. Testing and Verification

Verified against the backend repository test suites (post-final Stage D edits):
- [`tests/test_charter_intelligence.py`](../../tests/test_charter_intelligence.py) (`11` tests), [`tests/test_vessel_suitability.py`](../../tests/test_vessel_suitability.py), [`tests/test_vessel_suitability_api.py`](../../tests/test_vessel_suitability_api.py), [`tests/test_voyage_calculator.py`](../../tests/test_voyage_calculator.py), [`tests/test_voyage_calculator_api.py`](../../tests/test_voyage_calculator_api.py): **`52 passed, 1 warning`** (global `sklearn`/`scipy` NumPy version `UserWarning`).
- Full backend test suite (`pytest tests/ -q`): **`519 passed, 6 skipped, 1 warning`** (the 6 skipped cases are pre-existing opt-in `PELAGOS_MARNET_INTEGRATION=1` MARNET tests).

---

## 10. Traceability

- **Upstream:** [`PROJECT_OVERVIEW.md`](../PROJECT_OVERVIEW.md), [`ARCH-001`](../architecture/system-architecture.md), [`REQ-FR-001`](../requirements/functional-requirements.md) (`CDV-002`, `CPO-001..003`, `OMR-001..002`), [`REQ-NFR-001`](../requirements/nonfunctional-requirements.md) (`DIP-001`, `ARG-001`, `SBL-002`, `OAS-002`), [`ROUT-006`](../routing/D9.5-commercial-freight-route-intelligence.md), [`ML-001`](../forecasting/freight-index-and-forecasting-specification.md).

---

## 11. Open Questions

- **OQ-OPT-01 (Commercial Broker & Registry Integration):** Which authenticated vessel fixture feed and ship-registry dataset (providing verified DWT, grain capacity, fuel curves, and open-tonnage laycan dates) will supply production Stage D feeds?
- **OQ-OPT-02 (Authoritative Berth & Canal Rule Feeds):** Which machine-readable sources will populate terminal berth LOA/beam/draft limits and live ACP/SCA passage advisories?

---

## 12. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-11-11 | Implementation Agent | Created permanent Stage D specification documenting Milestone D10 (`OPT-001`), D10.2 (`OPT-002`), compatibility hardening, and fail-closed `/api/v1/charter/{assess,recommend}` gates. |

