---
title: Pelagos Stage E1 Platform Data Overview & Detailed Data Explorer Specification
document_id: API-001
type: api_spec
status: under_review
version: 1.0.2
created: 2026-11-11
updated: 2026-11-12
tags:
  - stage-e1
  - api
  - frontend
  - data-explorer
  - provenance
  - integration
---

# Pelagos Stage E1 Platform Data Overview & Detailed Data Explorer Specification

## 1. Purpose
This specification defines the read-only backend API endpoints and integrated God's Eye View (GEV) frontend presentation architecture for **Stage E1**:
1. **Page 1 — Live Data Overview** (`#pelagos-data-overview-card` inside `#pelagos-panel`), providing genuine database record counts, counting methodology, source health, 8-domain coverage, freight forecasting horizons, and navigation into the explorer.
2. **Page 2 — Detailed Data Explorer** (`#/explorer` and `#/explorer/{domain}`), providing multi-domain inspection, filtering, searching, allowlisted sorting, server-side pagination, loading/empty/unavailable/error states, and record-level provenance inspection without destroying Cesium globe state.

---

## 2. Scope
Covers:
* Backend REST endpoints under `/api/v1/platform-data/overview` and `/api/v1/platform-data/explorer/{domain}` implemented in [`platform_data.py` routes](../../backend/pelagos/api/routes/platform_data.py) and [`platform_data.py` storage service](../../backend/pelagos/storage/platform_data.py).
* Frontend GEV SPA integration in [`applicationShell.js`](../../gods-eye-view-main/src/ui/applicationShell.js), [`explorerRouter.js`](../../gods-eye-view-main/src/ui/explorerRouter.js), [`pelagosOverview.js`](../../gods-eye-view-main/src/ui/pelagosOverview.js), [`pelagosExplorer.js`](../../gods-eye-view-main/src/ui/pelagosExplorer.js), [`pelagosExplorerDomains.js`](../../gods-eye-view-main/src/ui/pelagosExplorerDomains.js), and [`pelagosApi.js`](../../gods-eye-view-main/src/ui/pelagosApi.js).
* Strict adherence to Pelagos data integrity, zero-fabrication, temporal availability, and model baseline honesty policies.

Out of scope:
* Database writes, manual ingestion triggers, or synthetic backfilling (all Stage E1 endpoints are read-only).
* Modification of core forecasting or chartering engines ([`ML-001`](../forecasting/freight-index-and-forecasting-specification.md), [`OPT-001`](../optimization/stage-d-vessel-suitability-and-chartering.md)).

---

## 3. Background and Context
Prior Pelagos stages established ingestion pipelines (`Stage A`), MARNET maritime routing (`ROUT-004`, `ROUT-006`), freight forecasting benchmarks confirming `persistence` as the active production baseline (`Stage C1–C3.3`), and Stage D vessel suitability and chartering gates (`OPT-001`). Previously, the frontend HUD panel hardcoded summary fallbacks and lacked a full-table data provenance workspace. Stage E1 connects the frontend directly to SQLAlchemy-backed read queries across all eight operational and analytical data domains.

---

## 4. Requirements and Constraints

* **Read-Only Access:** Stage E1 executes read-only ORM/SQL queries over active sessions. No write operations, schema migrations, or external HTTP ingestion calls occur during overview or explorer requests.
* **No Fabricated Counts or Values:** Missing vessel particulars (e.g., `0/120` DWT populated), unquoted corridor bunker hubs, historical AIS windows, and absent trade-history months are reported explicitly as `null`/`unavailable`/`historical_sample`.
* **Freight vs. Trade Separation:** Trade customs unit value (`value_usd / quantity`) is labeled strictly as commodity customs unit price and never equated with ocean freight rates (`$/metric ton`).
* **Forecasting Honesty:** `DEFAULT_BASELINE_MODEL` (`persistence`) remains clearly identified as the active production baseline across monitored corridors (`PNW-JPN-BULK`, `USG-JPN-BULK`) at horizons `T+1`, `T+3`, `T+6`. Experimental C3.2 ML and C3.3 regime-aware artifacts are shown as experimental benchmarks only.
* **Non-Certified Topology Disclaimer:** Generalized MARNET 50 km shipping-lane graph connectivity and port directory points are explicitly labeled non-navigational.
* **Credential Redaction:** Secret-matching fields and patterns (`api_key`, `token`, `secret`, `password`, `bearer`, `authorization`, provider key prefixes) are scrubbed before serialization in explorer outputs (including redacted `error_details` on ingestion runs and quarantined records, plus `raw_payload_preview` on quarantined records).

---

## 5. Design and Specification

### 5.1 Counting Methodology
`GET /api/v1/platform-data/overview` separates distinct persistence classes rather than double-counting derived rows:
* **Source Observations (`source_observations_excluding_derived` / `raw_observations_excluding_derived_spread`):** Direct source observations excluding derived spread series (`740` direct corridor freight + `13` trade + `13` env + `5` bunker + `120` AIS = `891`).
* **Raw Observations (`raw_observations_total`):** Direct source observations (`891`) plus derived `SPREAD-USG-PNW-JPN` (`370`) = `1,261`.
* **Physical Reference Entities (`registered_physical_entities`):** Canonical `ports` (`43`) and `vessels` (`120`) directory rows = `163`.
* **Combined DB Records (`total_genuine_records`):** Sum of `raw_observations_total` plus `registered_physical_entities` (`1,261 + 163 = 1,424`) — explicitly labeled `DB OBS + ENTITIES` in UI so it is not mistaken for independent raw observations.
* **Domain Coverage Status Counts:** `domains_with_records_count` (`8`) and backward-compatible `domains_populated_count` (`8`) count domains containing stored records, while `domains_fully_populated_count` (`7`) vs `domains_partial_count` (`1`, reflecting `bunker: "partial"`) distinguish partial coverage from full coverage (`8/8 (1 PARTIAL)` in UI).
* **Auxiliary / Derived Structures (Reported Separately):** `port_aliases` (`identifier_mappings_port_aliases`, `77`), `freight_publications` (`freight_publications_count`, `31` Parquet catalog entries), `ingestion_runs` (`ingestion_audit_runs`, `13`), `quarantined_records` (`0`), and strict-forward modeling `ForecastDataset` rows (`derived_dataset_rows` / `domains.datasets.total_dataset_rows`, `2,148` across corridors & horizons).

### 5.2 Backend REST API Specification

#### 1. `GET /api/v1/platform-data/overview`
* **Query Parameters:**
  * `use_cache` (`bool`, default `true`): Enables a 60-second (`CACHE_TTL_SECONDS = 60`) in-memory cache keyed by database bind URL and table-count/max-timestamp fingerprint. Passing `use_cache=false` forces fresh aggregation.
* **Response Sections:**
  * `status` & `generated_at`: Overall response status (`ok` or `unavailable`) and ISO 8601 UTC timestamp.
  * `cache`: `ttl_seconds`, `cached` boolean, and operational cache note.
  * `global_summary`: `total_genuine_records`, explicit `counting_methodology` text, `breakdown` (`raw_observations_total`, `raw_observations_excluding_derived_spread`, `source_observations_excluding_derived`, `freight_underlying_corridor`, `freight_derived_spread`, `freight_reconstructed`, `freight_publications_count`, `trade_observations`, `environmental_observations`, `bunker_observations`, `ais_position_observations`, `registered_physical_entities`, `canonical_ports`, `canonical_vessels`, `identifier_mappings_port_aliases`, `ingestion_audit_runs`, `quarantined_records`, `derived_dataset_rows`), `connected_operational_sources`, `total_registered_source_feeds`, `automated_remote_sources_active`, `domains_populated_count`, `domains_with_records_count`, `domains_fully_populated_count`, `domains_partial_count`, `domains_empty_or_unavailable_count`, `total_domains` (`8`), `latest_successful_ingestion_timestamp`, `latest_successful_ingestion_source`, `latest_data_retrieval_timestamp`, and `latest_data_retrieval_table`.
  * `source_health`: Per-source breakdown across USDA AgTransport (`usda_agtransport`), NOAA NDBC & INCOIS buoys, Bunker Fuel Prices, USDA FAS GATS & Indian DGCI&S trade, NOAA MarineCadastre / USCG NAIS AIS, and UNECE / WPI canonical ports.
  * `domains`: Structured metrics for all 8 domains (`freight`, `trade`, `environmental`, `bunker`, `vessels`, `ports`, `datasets`, `sources`) including `monitored_corridors`, `derived_series`, `available_forecast_horizons`, `active_production_baseline`, `corridor_hub_coverage`, `particulars_completeness`, and `total_dataset_rows`.
  * `benchmarks`: Lightweight summary loaded from promoted C3.2/C3.3 JSON artifacts under [`docs/forecasting/artifacts/`](../forecasting/artifacts/) (`production_baseline: "persistence"`, availability flags, and `c32_highlights`/`c33_highlights`; heavy per-model/statistical arrays are omitted on `/overview`).
  * `integrity_notices`: Domain-level caveats surfaced directly in UI banners.

#### 2. `GET /api/v1/platform-data/explorer/{domain}`
* **Path Parameter:** `domain` in `{freight, trade, environmental, bunker, vessels, ports, datasets, sources}` (returns HTTP `404` for unsupported domains).
* **Common Query Parameters:**
  * `page` (`int >= 1`, default `1`), `page_size` (`1..100`, default `25`, `MAX_PAGE_SIZE = 100`)
  * `sort_by` (domain-allowlisted column; HTTP `400` if unrecognized), `sort_order` (`asc` or `desc`, default `desc`)
  * `search` (`string`, max length `120`, case-insensitive SQL `ILIKE` / matching across domain-appropriate columns)
* **Domain Filters & Behavior:**
  * **`freight`**: `route_code`, `series_type` (`all|underlying|spread`), `reconstructed_only`, `date_from`, `date_to`. Separates underlying corridors from `SPREAD-USG-PNW-JPN`.
  * **`trade`**: `reporter_iso2`, `partner_iso2`, `commodity_code`, `trade_flow`, `date_from`, `date_to`. Includes computed unit value explicitly labeled non-freight customs ratio.
  * **`environmental`**: `station_id`, `sea_area`, `quality_status`, `date_from`, `date_to`. Preserves missing sensor fields (`null`) and station freshness (`current|recent|stale`).
  * **`bunker`**: `port_code`, `fuel_grade`, `pricing_type`, `date_from`, `date_to`. Includes `secondary_panels.corridor_hub_coverage` marking unquoted monitored corridor hubs `unavailable`.
  * **`vessels`**: `vessel_type`, `vessel_class`, `has_imo`, `has_dwt`, `search`. Joins latest historical `VesselPosition` snapshot per vessel (`2024-01-01` MarineCadastre sample) with `telemetry_qualification: "Historical AIS Research Sample (NOT Open Tonnage / Live Fleet)"`, `is_live: false`, and `summary.commercial_charter_availability: "unavailable"` (missing DWT and particulars remain `null`).
  * **`ports`**: `country_code`, `port_region`, `corridor_eligible` (`all|true|false`), `graph_connected` (`all|true|false`), `search` (matches port fields and `port_aliases` via SQLAlchemy 2.0 `select()`). Includes `secondary_panels.routing_topology`.
  * **`datasets`**: `route_code`, `horizon` (`1|3|6`), `regime_id`, `is_labeled` (`all|true|false`), `date_from`, `date_to`. Uses `build_forecast_dataset(db=db, ..., storage_dir=Path("__pelagos_sql_session_only__"))` inside a fingerprint cache so active DB sessions remain isolated from disk `parquet_data/` when no `FreightPublication` rows exist; returns explicit `availability_status: "unavailable"` for `SPREAD-USG-PNW-JPN` or unsupported routes. Its `secondary_panels.benchmarks` payload expands upon `/overview` by exposing complete verbatim C3.2 and C3.3 artifact metadata and experiments (`c32_metadata`, `c32_experiments`, `c33_metadata`, `c33_experiments`): exact out-of-sample metrics (`mae`, `rmse`, `mape`, `mase`, `directional_accuracy_non_flat`, `relative_mae_percent_change`), DM/HLN statistics, Holm-adjusted p-values, 95% block-bootstrap CIs, explicit `lightgbm` `UNAVAILABLE` provenance, C3.2/C3.3 per-regime breakdowns, `regime_sample_audit`, and matched-subset `regime_partitioned_analysis`, while keeping `production_baseline: "persistence"` and preserving `null` values verbatim without zero-substitution.
  * **`sources`**: `view` (`runs|quarantine`), `run_status`, `run_type`, `source_name`. Inspects `ingestion_runs` vs `quarantined_records` with secret-redacted `error_details` and `raw_payload_preview` fields and `secondary_panels: {}` (freight Parquet partition counts are exposed on `/overview` via `global_summary.breakdown.freight_publications_count` and `domains.freight.freight_publications_count`).
* **Standard Response Envelope:** Contains `status`, `domain`, `domain_label`, `availability_status`, `reason`, `page`, `page_size`, `total_count`, `total_pages`, `sort_by`, `sort_order`, `allowed_sort_fields`, `applied_filters`, `filter_options`, `summary`, `integrity_notices`, `items`, and `secondary_panels`.

### 5.3 Frontend Architecture & Globe Lifecycle
* **Page 1 (`#pelagos-panel`):** [`PelagosOverviewController`](../../gods-eye-view-main/src/ui/pelagosOverview.js) populates `#pelagos-data-overview-card` inside [`layer-panels.html`](../../gods-eye-view-main/src/ui/templates/layer-panels.html) using `fetchPlatformOverview()`, replacing stale fallback strings with live API metrics and offering one-click domain jumps into `#/explorer/{domain}`.
* **Page 2 (`#/explorer`):** Hash-routed via [`parseExplorerHash`](../../gods-eye-view-main/src/ui/explorerRouter.js) and mounted from [`data-explorer.html`](../../gods-eye-view-main/src/ui/templates/data-explorer.html) through [`build/application-html.js`](../../gods-eye-view-main/build/application-html.js). Managed inside [`applicationShell.js`](../../gods-eye-view-main/src/ui/applicationShell.js):
  * Preserves initialized Cesium `Viewer`, camera pose, active vessel entities, and maritime route overlays across `#/explorer` <-> `#` transitions (no viewer teardown).
  * Calls `this.services.suspendRendering?.('explorer')` (`renderGovernor.suspendRendering('explorer')`) while the explorer covers the viewport and `resumeRendering('explorer')` on exit (implemented via owner-set suspension in [`renderGovernor.js`](../../gods-eye-view-main/src/renderGovernor.js)).
  * Calls `setHashSyncPaused(true)` in [`sharelink.js`](../../gods-eye-view-main/src/sharelink.js) while `#/explorer` owns `window.location.hash`, preventing background camera movements from overwriting the SPA hash.
  * Suppresses global single-key globe shortcuts (including `Escape` search dismissal) when explorer mode is active (`body.pelagos-explorer-active` in [`applicationShortcuts.js`](../../gods-eye-view-main/src/ui/applicationShortcuts.js)); users return to the globe view via `#pelagos-explorer-return-btn` or by changing the URL hash.
  * Displays row-level provenance drawers, integrity notice banners, CSV export of current server page rows, accessible loading/empty/unavailable/error states, and a full-width Experimental Forecasting Benchmarks workspace on the `datasets` domain (corridor/horizon selectors, `ACTIVE PRODUCTION MODEL: PERSISTENCE` non-promotion guard banner, C3.2 ML benchmark table, explicit `LIGHTGBM` `UNAVAILABLE` notice, expandable DM/HLN/Holm/95%-CI statistical details with non-equivalence interpretation warning, C3.3 regime investigation table, `regime_sample_audit`, and matched-subset `regime_partitioned_analysis` without candidate-promotion controls) in [`PelagosExplorerController`](../../gods-eye-view-main/src/ui/pelagosExplorer.js).

---

## 6. Dependencies and Interfaces
* **Backend:** `pelagos.api.routes.platform_data`, `pelagos.storage.platform_data`, SQLAlchemy ORM (`pelagos.models`), `pelagos.forecasting.dataset`, `pelagos.routing`.
* **Frontend:** `gods-eye-view-main/src/ui/pelagosApi.js`, `explorerRouter.js`, `pelagosOverview.js`, `pelagosExplorerDomains.js`, `pelagosExplorer.js`, `applicationShell.js`, `renderGovernor.js`, `sharelink.js`.
* **Dev Proxy:** Vite dev server proxies `/api` requests to FastAPI (`http://127.0.0.1:8000` by default in `gods-eye-view-main/build/vite.js`).

---

## 7. Failure Modes and Limitations
* **Backend Offline:** Frontend displays explicit error/unavailable banners (`Offline / Backend Unreachable`) without synthesizing placeholder records.
* **Domain Coverage Limits:** Reflected verbatim from live DB: `0/120` vessel DWT populated, `2024-01-01` historical AIS window, unquoted corridor bunker hubs (`Seattle`/`Houston`/`Yokohama` unavailable vs Buenos Aires/Skagen spot quotes), and 3-month late-2024 trade coverage (`Oct–Dec 2024`).
* **Cache Freshness:** With `use_cache=true`, `/overview` returns cached aggregates for up to 60 seconds unless underlying SQL fingerprint counts/timestamps change or `use_cache=false` is requested.

---

## 8. Security, Privacy and Licensing
* Stage E1 exposes strictly read-only metadata and observation tables already permitted for platform presentation.
* Secret redaction scrubs API keys, tokens, passwords, and authorization headers from `error_details` (ingestion runs and quarantined records) and `raw_payload_preview` (quarantined records).
* HTML rendering escapes dynamic values cleanly inside the explorer DOM (`escapeHtml` / `.textContent`).

---

## 9. Testing and Verification
* **Backend API & Integration:** [`tests/test_platform_data_api.py`](../../tests/test_platform_data_api.py) covers empty-DB honesty, overview aggregation & caching, all 8 explorer domains (pagination, sorting, filtering, secret redaction, DB isolation), error states (`400`/`404`/derived-spread `unavailable`), and parity with live SQLite counts.
* **Frontend Unit & Regression:** [`gods-eye-view-main/src/ui/pelagosDataExplorer.test.mjs`](../../gods-eye-view-main/src/ui/pelagosDataExplorer.test.mjs) verifies hash router parsing, `renderGovernor` owner-set suspension, `pelagosApi` URL construction & error handling, `PelagosOverviewController` rendering/honesty notices, and `PelagosExplorerController` domain switching, pagination, filtering, provenance selection, and degraded states.
* **UI Guardrails & Build:** Verified against `materialSymbolsSubset.test.mjs`, `applicationHtml.test.mjs`, `check-import-directions.mjs`, `check-package-boundaries.mjs`, Prettier formatting, `vite build`, and live Vite -> FastAPI proxy smoke checks.

---

## 10. Traceability
* [DOC-002: Project Overview](../PROJECT_OVERVIEW.md) Section 3
* [ARCH-001: System Architecture Specification](./system-architecture.md) Sections 5.2 & 6.1
* [ML-001: Freight Index & Forecasting Specification](../forecasting/freight-index-and-forecasting-specification.md)
* [OPT-001: Stage D Vessel Suitability & Chartering Specification](../optimization/stage-d-vessel-suitability-and-chartering.md)

---

## 11. Open Questions
* Authentication and RBAC scoping for multi-tenant deployments before exposing `ingestion_runs` / `quarantined_records` publicly beyond operator localhost sessions.
* Optional full-dataset asynchronous CSV export endpoint if exporting beyond the `MAX_PAGE_SIZE = 100` paginated slice is required in Stage E2+.

---

## 12. Change History
| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-11-11 | Implementation Agent | Initial Stage E1 API & Frontend Platform Data Overview and Detailed Data Explorer specification (`API-001`). |
| 1.0.1 | 2026-11-12 | Implementation Agent | Added additive counting clarifications (`source_observations_excluding_derived`, `freight_publications_count`, partial-domain readiness counts), cold `datasets` explorer performance optimizations, package-boundary registration updates, and aligned API/UI field and shortcut behavior (`error_details`, `raw_payload_preview`, vessel qualification wording, `sources` empty `secondary_panels`, return-button/hash navigation). |
| 1.0.2 | 2026-11-12 | Implementation Agent | Exposed full C3.2 and C3.3 experimental forecasting benchmark evidence in `GET /api/v1/platform-data/explorer/datasets` (`secondary_panels.benchmarks`) while keeping `/overview` lightweight, and added full-width experimental benchmark visibility (`ACTIVE PRODUCTION MODEL: PERSISTENCE` guard banner, corridor/horizon switching, DM/HLN/Holm/bootstrap CI details, `LIGHTGBM` `UNAVAILABLE` provenance, and C3.3 regime sufficiency/partitioned subset panels) in `#/explorer/datasets`. |
