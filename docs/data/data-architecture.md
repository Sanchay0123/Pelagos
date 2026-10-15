---
title: Pelagos Data Architecture and Source Strategy
document_id: DATA-001
type: dataset_spec
status: drafting
version: 1.0.0
created: 2026-10-14
updated: 2026-10-14
tags:
  - data
  - specification
  - datasets
  - telemetry
  - freight-indices
  - routing-networks
  - licensing
  - provenance
---

# Pelagos Data Architecture and Source Strategy

## 1. Purpose
This document establishes the data architecture, dataset inventory, source acquisition strategy, licensing boundaries, and temporal governance rules for the Pelagos platform. It provides the definitive guide for data ingestion, quality validation, schema normalization, and persistent storage across all operational maritime intelligence domains.

## 2. Scope
This specification governs:
* Categorization and evaluation of data sources across six core domains: AIS vessel telemetry, freight rates and market indices, vessel particulars and availability, ports and navigational constraints, environmental/oceanographic conditions, and geopolitical disruptions.
* Strict distinction between verified accessible sources and candidate / unverified sources.
* Source provenance metadata tagging, license verification, and copyright compliance.
* Data quality filtering, kinematic validation, and anomaly quarantine procedures.
* Multi-dimensional temporal data modeling and strict handling of timezone-naive inputs.
* Identification and mitigation of real-world data feasibility risks and coverage gaps.

## 3. Background and Context
Real-world maritime decision-making depends on diverse, heterogeneous, and frequently proprietary data streams. A major systemic vulnerability in maritime software is the tendency to assume global completeness or inject synthetic records into live analytical workflows. 

Pelagos establishes a strict policy of architectural honesty ([PROJECT_OVERVIEW.md Section 4, 6](../PROJECT_OVERVIEW.md)):
* No fabricated observations presented as real data ([REQ-NFR-DIP-001](../requirements/nonfunctional-requirements.md)).
* All data inputs must carry immutable provenance and verifiable license metadata ([REQ-FR-DII-003](../requirements/functional-requirements.md)).
* The system must transparently declare when coverage is degraded rather than interpolating speculative positions ([REQ-NFR-ARG-001](../requirements/nonfunctional-requirements.md)).

---

## 4. Requirements and Constraints

* **Zero Fabricated Data:** Absolute prohibition on inserting synthesized vessel pings or simulated fixture trades into production databases ([REQ-NFR-DIP-001](../requirements/nonfunctional-requirements.md)).
* **Mandatory Provenance Metadata:** Ingested records must record `source_name`, `license_type`, `retrieval_timestamp`, and `data_freshness` ([REQ-FR-DII-003](../requirements/functional-requirements.md)).
* **Multi-Dimensional ISO 8601 UTC Temporal Tracking:** Ingestion pipelines must preserve discrete timestamps for `observation_time`, `publication_time` (where available), `ingestion_time` (UTC arrival), and `processing_time` (UTC pipeline transformation). Timezone-naive timestamps must not be automatically assumed to be UTC unless explicitly established by interface contract; ambiguous timestamps are flagged `ANOMALY_AMBIGUOUS_TIMEZONE` and quarantined ([REQ-NFR-DIP-003](../requirements/nonfunctional-requirements.md)).
* **Kinematic Plausibility:** Telemetry records violating vessel-class speed and acceleration bounds must be quarantined with structured codes ([REQ-FR-DII-002](../requirements/functional-requirements.md)). Universal uncalibrated thresholds (such as blanket 35-knot cutoffs across all commercial vessels) are strictly prohibited. Speeds are screened against provisional operational hydrodynamic envelopes defined per vessel category (pending empirical fleet calibration):
  - Bulk Carrier / Capesize / Panamax: 22.0 kts (provisional operational baseline)
  - Tanker (Crude, Product, Chemical, LNG, LPG): 22.0 kts (provisional operational baseline)
  - Tug / Towing: 20.0 kts (provisional operational baseline)
  - Fishing: 20.0 kts (provisional operational baseline)
  - Container Liners: 32.0 kts (provisional operational baseline)
  - General Cargo: 30.0 kts (provisional operational baseline)
  - Conventional Passenger / Cruise: 32.0 kts (provisional operational baseline)
  - Sailing (including racing multihulls): 40.0 kts (provisional operational baseline)
  - Fast Craft (Pilot, Patrol, Search and Rescue, Law Enforcement, Fast Pleasure): 50.0 kts (provisional operational baseline)
  - High Speed Craft (HSC, Hydrofoils, Catamaran Ferries): 60.0 kts (provisional operational baseline)
  - Absolute Marine Craft Ceiling: 70.0 kts (provisional physical ceiling; speeds exceeding this or negative speeds are quarantined as `ANOMALY_KINEMATIC_OUTLIER`).
  - **Insufficient Particulars Policy:** Where vessel category or particulars are unverified/generic (e.g., generic commercial or missing ship type code) and available data cannot justify confident rejection, observations exceeding the standard displacement baseline (25.0 kts) are flagged and quarantined with diagnostic code `ANOMALY_INSUFFICIENT_PARTICULARS` rather than applying an unsupported universal cutoff.
* **Permissive Open-Source Licensing:** Third-party datasets and software libraries must permit redistribution and commercial analytical use without viral copyleft contamination ([REQ-NFR-DCT-003](../requirements/nonfunctional-requirements.md)).

---

## 5. Design or Specification (Data Domain Catalog)

### 5.1 Domain 1: AIS Vessel Telemetry

AIS (Automatic Identification System) broadcasts dynamic kinematic and static voyage messages from maritime transponders.

| Source Name | Source Category | Access Method / Protocol | License / Commercial Terms | Coverage & Resolution | Freshness & Latency | Operational Uncertainties & Risks |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **AISStream.io** | **Verified Source** | WebSocket JSON stream (`wss://stream.aisstream.io/v0/stream`) | Free API tier (Open community access; API key required; non-commercial / research terms apply). | Global coastal stations; sparse open-ocean satellite coverage. | Sub-minute streaming from active receiver stations. | Terrestrial range limited to ~30–50 NM from receiver antennas; open-ocean coverage is highly degraded without satellite uplink; free tier rate limits. |
| **US Coast Guard / MarineCadastre** | **Verified Source** | Bulk HTTP Parquet / Geodatabase archives | U.S. Government Work / Public Domain (17 U.S.C. § 105; unrestricted public use with attribution). | US coastal waters and EEZ; daily 2024 Parquet partitions and historical archives from 2009–present. | Static historical archives (daily/monthly batch release). | Strictly limited to US coastal/EEZ terrestrial NAIS receiver range (~30–50 NM from coast); open-ocean gaps exist; cannot provide live operational tracking. |
| **Danish Maritime Authority (DMA)** | **Verified Source** | Bulk HTTP point archives (CSV) | Creative Commons / Danish Open Data (Free public use). | Danish and surrounding Baltic / North Sea waters; high density. | Daily / monthly historical batches. | Limited to Northern European waters; historical analysis only. |
| **Spire Maritime / ExactEarth** | **Candidate Source** | Commercial REST & WebSocket API | Commercial Enterprise Subscription (Paid commercial license). | Global terrestrial + Satellite AIS constellation (full open-ocean coverage). | Near-real-time (< 5 min latency globally). | Substantial commercial license costs; strict contractual redistribution limits; requires explicit procurement approval. |
| **MarineTraffic / Kpler** | **Candidate Source** | Commercial REST API | Commercial Pay-per-call / Subscription (Paid commercial license). | Global network of 5,000+ coastal stations + satellite partners. | Polling intervals (typically 5–60 minutes depending on tier). | High recurring API cost; strict usage limits; risk of vendor billing lock-in. |

#### Normalized Telemetry Schema Contract
```json
{
  "mmsi": 211281610,
  "imo": 9315408,
  "vessel_name": "ATLANTIC CARRIER",
  "vessel_class": "Capesize",
  "coordinates": {
    "latitude": 17.6833,
    "longitude": 83.2167
  },
  "speed_over_ground_knots": 12.4,
  "course_over_ground_deg": 184.2,
  "heading_deg": 185.0,
  "navigational_status": "under_way_using_engine",
  "draft_meters": 17.8,
  "destination": "IN VTZ",
  "temporal": {
    "observation_time": "2026-10-14T10:15:30Z",
    "publication_time": "2026-10-14T10:15:35Z",
    "ingestion_time": "2026-10-14T10:15:38Z",
    "processing_time": "2026-10-14T10:15:39Z",
    "source_timezone_verified": true
  },
  "provenance": {
    "source_name": "AISStream",
    "license": "community_research",
    "raw_record_id": "msg_984120412"
  }
}
```

#### 5.1.1 Historical NOAA AIS Ingestion Engine & Execution Architecture
To guarantee reproducible offline ingestion independent of operational web API lifecycles, Pelagos implements a decoupled batch ingestion architecture:

* **Operator CLI Entry Point (`pelagos.cli`):**
  - Sample Seeding: `python -m pelagos.cli ingest-sample [--dry-run]`
  - Remote Bounded Ingestion: `python -m pelagos.cli ingest-remote --date YYYY-MM-DD [--limit N] [--row-group N] [--dry-run]`
  - Operational Inspection: `python -m pelagos.cli status` and `python -m pelagos.cli coverage`
* **FastAPI Startup Isolation:** Normal application startup (`lifespan`) executes strictly zero sample seeding and zero network requests. Schema initialization (`init_db()`) verifies table and column availability via non-destructive additive DDL without blocking on data loading.
* **Bounded Partition Selection & Range Streaming:**
  - Ingestion targets daily Parquet partitions hosted on NOAA MarineCadastre (e.g., `AIS_2024_01_01.parquet`).
  - Specifying `--limit N` limits the number of candidate records parsed from the stream and memory allocated.
  - *Download Transfer Note:* Because Parquet encodes columnar data across discrete row groups, streaming Parquet over HTTP via byte-range requests requires fetching the footer and the minimum row-group column chunks. Consequently, network transfer size reflects the physical page boundaries (~5–15 MB) rather than an exact byte-per-record fraction.
* **Transaction Invariants & Counter Reconciliation:**
  Every ingestion execution calculates and reconciles record processing counters according to the strict conservation equation:
  $$\text{records\_read} = \text{accepted} + \text{quarantined} + \text{duplicate} + \text{unparseable} + \text{failed}$$
  - **Dry-Run Mode (`is_dry_run=True`):** In-memory parsing, kinematic validation, and idempotency checks execute without committing any changes to the database. *Audit Exception:* Dry-runs do NOT persist an `IngestionRun` audit row to ensure the audit log reflects strictly genuine database modifications.
  - **Audit Record Durability upon Failure:** If an unhandled exception or transaction abort occurs during batch persistence, the operational telemetry rows are rolled back. A dedicated, clean transaction immediately records an `IngestionRun` record with `status="failed"`, preserving failure diagnostics and accurate failure counters.
* **Spatial & Temporal Coverage Footprint:**
  The `CoverageService` calculates actual bounding box coordinates (`[min_lon, min_lat, max_lon, max_lat]`), earliest and latest observation timestamps, and distinct ingested partition identifiers.
  *Operational Honesty Disclaimer:* The computed spatial bounding box reflects coordinate extremes and does NOT imply uniform or continuous observation across that geometry. Observations derive from terrestrial receivers with coastal range limits (~30–50 NM), and data reflects historical mode rather than live satellite tracking.

---

### 5.2 Domain 2: Freight Market Indices & Fixture Data

Freight market data provides the grounding signals for freight rate forecasting and chartering decisions.

| Source Name | Source Category | Access Method / Protocol | License / Commercial Terms | Coverage & Resolution | Freshness & Latency | Operational Uncertainties & Risks |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Baltic Exchange (BDI, BCI, BPI, BSI)** | **Candidate Source** | Secure FTP / Member API | Commercial Proprietary License (High commercial subscription fee; strict redistribution ban). | Global standard dry bulk benchmark indices; major international route fixtures. | Daily publication at 13:00 London time. | High cost barrier; strict legal restrictions prohibiting public display of raw Baltic data without commercial distributor agreements. |
| **S&P Global Platts** | **Candidate Source** | Market Data API / Portal | Commercial Enterprise License (Paid subscription). | Comprehensive clean/dirty tanker, dry bulk, and bunker fuel spot assessments. | Daily spot updates. | Expensive commercial licensing; redistribution prohibited; access pending enterprise negotiation. |
| **Public Shipping Market Reports (Banchero Costa, Fearnleys, Allied)** | **Verified Source** | Weekly published PDF / Web Market Reports | Public Reference / Fair Use for aggregated analysis. | Weekly fixture summaries, average earnings per vessel class, representative fixture lists. | Weekly publication. | Unstructured tabular text requiring specialized scrapers; weekly aggregation lacks daily spot volatility; historical depth requires manual curation. |
| **Commodity Exchanges (SGX / EEX FFA)** | **Candidate Source** | Financial Data Feeds | Exchange Member License / Market Data Agreement. | Forward Freight Agreement (FFA) derivative forward curves across key corridors. | Daily end-of-day settlement. | Financial feed licensing costs; settlement curves may deviate from physical spot fixing conditions. |

---

### 5.3 Domain 3: Vessel Particulars & Commercial Registry Data

Hydrostatic dimensions and engineering particulars are strictly required to evaluate port compatibility and navigational passage feasibility ([REQ-FR-OMR-001](../requirements/functional-requirements.md)).

| Source Name | Source Category | Access Method / Protocol | License / Commercial Terms | Coverage & Resolution | Freshness & Latency | Operational Uncertainties & Risks |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **IMO Global Integrated Shipping Information System (GISIS)** | **Verified Source** | Web Portal / Public Inquiries | UN IMO Public Access (Free for individual verification; bulk extraction restricted). | Global fleet of all commercial vessels > 100 GT with assigned IMO numbers. | Updated upon flag state submission. | No public bulk REST API; web scraping rate-limited; best suited for single-vessel verification rather than bulk catalog ingestion. |
| **ITU Maritime Mobile Access and Retrieval System (MARS)** | **Verified Source** | Public HTTP Download | ITU Open Access (Free public maritime database). | Global vessel radio stations, call signs, MMSI allocations, and basic vessel types. | Weekly database dump. | Contains radio identifiers and basic tonnage; lacks detailed hydrostatic dimensions (operational draft, beam, air draft). |
| **Equasis Public Maritime Database** | **Candidate Source** | Authenticated Web Portal | Free for registered non-commercial users (Bulk commercial automated access prohibited). | Global fleet safety, classification society, flag, and physical particulars. | Monthly updates. | Strict terms of service prohibiting automated crawling or commercial platform ingestion. |
| **Classification Society Registries (DNV, Lloyd's Register, ClassNK)** | **Candidate Source** | Public Online Vessel Registers | Free online lookup per society (Terms forbid unauthorized automated scraping). | Classed commercial vessels; accurate dimensions, deadweight, lightweight, and hold geometries. | Continuous updates. | Disparate schemas across societies; lack of unified API; commercial integration requires individual bilateral agreements. |

---

### 5.4 Domain 4: Ports, Fairways, and Navigational Constraints

Navigational network graphs and authoritative passage restrictions are essential to eliminate geometric straight-line routing ([REQ-FR-OMR-001](../requirements/functional-requirements.md), [REQ-FR-OMR-002](../requirements/functional-requirements.md)).

| Source Name | Source Category | Access Method / Protocol | License / Commercial Terms | Coverage & Resolution | Freshness & Latency | Operational Uncertainties & Risks |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **World Port Index (NGA Pub 150)** | **Verified Source** | Shapefile / GeoJSON / CSV Download | US Public Domain (Unrestricted public use). | Over 3,700 global commercial ports; coordinates, harbor size, draft limitations, pilotage requirements. | Periodic updates (semiannual). | Static dataset; draft limits represent general harbor depths rather than real-time berth soundings. |
| **OpenSeaMap / OpenStreetMap Maritime** | **Verified Source** | Overpass API / OSM PBF extracts | Open Database License (ODbL). | Global sea-lanes, fairways, buoys, beacons, Traffic Separation Schemes (TSS). | Continuous community updates. | Crowd-sourced data; variable completeness across remote waters; lacks guaranteed hydrographic bathymetric precision. |
| **Global Maritime Fairway Network Graph** | **Verified Source (Derived)** | In-repo PostGIS Routing Graph | Compiled from public fairway centerlines and historical voyage AIS corridors. | Global oceanic navigation corridors and coastal approach choke-nodes. | Maintained in Pelagos repository. | Requires verified connectivity; disconnected graph nodes must explicitly trigger `unsupported-route` outcomes. |
| **Suez Canal Authority (SCA) Rules of Navigation** | **Verified Source** | Official Circulars / PDF Navigation Guides | Public Authority Guidance (Free public operational rules). | Suez Canal convoy rules, maximum beam, draft tables, and seasonal transit surcharge notices. | Updated via official navigation circulars. | Static tables must be parameterized dynamically with provenance and effective date; limits must not be hardcoded as permanent constants ([REQ-FR-OMR-001](../requirements/functional-requirements.md)). |
| **Panama Canal Authority (ACP) Advisory to Shipping** | **Verified Source** | Official Monthly Advisories / PDF | Public Authority Guidance (Free public operational rules). | Neopanamax and Panamax locks; maximum allowable draft (dynamic based on Gatun Lake water levels). | Monthly and bi-weekly advisory updates. | Draft limits fluctuate significantly based on seasonal rainfall and drought conditions; requires active monitoring of ACP advisories. |

---

### 5.5 Domain 5: Environmental and Weather Conditions

Oceanographic and meteorological conditions influence vessel speed over ground and bunker fuel consumption ([REQ-FR-OMR-004](../requirements/functional-requirements.md)).

| Source Name | Source Category | Access Method / Protocol | License / Commercial Terms | Coverage & Resolution | Freshness & Latency | Operational Uncertainties & Risks |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **NOAA GFS & WaveWatch III** | **Verified Source** | HTTP / OPeNDAP / GRIB2 | US Public Domain (Free unrestricted access). | Global coverage; 0.25&deg; grid; significant wave height, wind vectors, wave direction. | 4 daily model cycles (00z, 06z, 12z, 18z); 16-day forward projections. | Large binary file sizes (~hundreds of MB per run); requires specialized in-process GRIB decoders (xarray, cfgrib). |
| **ECMWF Open Data / ERA5** | **Verified Source** | Web API / HTTP Open Data | Creative Commons CC-BY 4.0. | Global reanalysis and open medium-range forecasts; surface wind and pressure. | 0.4&deg; grid; regular daily forecast releases. | Medium spatial resolution; historical ERA5 files are multi-gigabyte. |
| **Copernicus Marine Service (CMEMS)** | **Verified Source** | NetCDF / WMS Open API | Open access under EU Copernicus Regulations. | Global ocean surface currents (zonal and meridional velocity components). | Daily global analysis at 1/12&deg; resolution. | Requires user registration; NetCDF format requires specialized extraction. |

---

### 5.6 Domain 6: Geopolitical Disruptions & Maritime Compliance

Geopolitical risk factors and sanctions are modeled as explicit, explainable factor families ([REQ-FR-GDS-001](../requirements/functional-requirements.md), [REQ-NFR-SBL-002](../requirements/nonfunctional-requirements.md)).

| Source Name | Source Category | Access Method / Protocol | License / Commercial Terms | Coverage & Resolution | Freshness & Latency | Operational Uncertainties & Risks |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **UK Maritime Trade Operations (UKMTO)** | **Verified Source** | Official RSS / Email Advisories | Public Guidance / Crown Copyright (Free public dissemination). | Red Sea, Gulf of Aden, Arabian Sea, Persian Gulf; incident reports and hazard coordinates. | Emitted within hours of maritime incidents. | Requires unstructured text parsing and geocoding of incident coordinates. |
| **US MARAD Maritime Advisories** | **Verified Source** | Official Web / RSS Alerts | US Public Domain (Unrestricted public use). | Global maritime security alerts, threat zones, and war-risk designations. | Emitted as advisories are issued. | Textual advisories requiring manual or semi-automated boundary geofence configuration. |
| **OFAC Specially Designated Nationals (SDN) List** | **Verified Source** | CSV / XML / JSON Download | US Public Domain (Free public regulatory compliance data). | Global sanctions lists; maritime vessels identified by IMO number and flag. | Updated upon executive order/amendment. | Requires strict IMO matching logic; false positives must be handled via operator verification ([REQ-NFR-SBL-002](../requirements/nonfunctional-requirements.md)). |

---

## 6. Dependencies and Interfaces

* **Internal Persistence Tiers:**
  * PostGIS / SQLite operational tables (`vessel_positions`, `vessel_registry`, `port_facilities`, `fairway_graph`, `quarantined_records`, `ingestion_runs`, `parquet_export_tasks`).
  * Parquet columnar directory hierarchy (`parquet_data/telemetry/year=YYYY/month=MM/date=YYYY-MM-DD/`, `parquet_data/freight/`).
* **Ingestion Worker Modules:**
  * `pelagos.ingestion.ais`: Consumes AISStream WebSocket, validates kinematics, flushes to PostGIS & Redis.
  * `pelagos.ingestion.marinecadastre`: Stream-parses remote or sample NOAA MarineCadastre Parquet slices.
  * `pelagos.ingestion.service`: Orchestrates idempotent batch ingestion, microsecond UTC idempotency keys, per-record savepoints inside `no_autoflush`, safe typed ORM eviction, reconciliation metrics, transaction boundaries, and outbox staging.
  * `pelagos.ingestion.coverage`: Computes continuous spatial/temporal telemetry footprints with operational honesty disclaimers.
* **Storage and Analytical Subsystem (`pelagos.storage`):**
  * `pelagos.storage.schema`: PyArrow strict 26-field typed telemetry schema (`TELEMETRY_PARQUET_SCHEMA`) with microsecond UTC timestamps and strict nullabilities.
  * `pelagos.storage.writer`: Atomic staging writer to `.tmp.{worker_id}` files with streaming SHA-256 integrity verification, byte size, and row count verification.
  * `pelagos.storage.outbox`: Transactional outbox coordination with generation bump fencing, attempt-UUID worker leasing, atomic completion CAS, publication state isolation, recovery CAS guardrail, and tombstone reconciliation with cleanup mutex leases.
  * `pelagos.storage.catalog`: Parquet footer metadata extraction with DuckDB row-scan fallback.
  * `pelagos.storage.analytics`: High-performance analytical queries and vessel track point extraction via DuckDB.
* **CLI Commands (`pelagos.cli`):**
  * `ingest-sample`: Ingests bundled sample data with idempotency enforcement.
  * `ingest-remote`: Stream-parses partition-bounded remote MarineCadastre archives.
  * `status`: Displays database and ingestion run audit summary.
  * `coverage`: Computes spatial/temporal coverage metrics.
  * `parquet-sync`: Claims pending outbox export tasks, exports immutable generation Parquet files, verifies SHA-256, and updates publication status.
  * `parquet-catalog`: Inspects Parquet partition footers and returns catalog metadata.
  * `parquet-reconcile`: Runs multi-phase reconciliation and safe tombstone cleanup under exclusive mutex lease.
  * `analytics-summary`: Executes DuckDB analytical queries over published Parquet partitions.
* **REST API Endpoints:**
  * `/api/v1/telemetry/snapshot`: Latest vessel positions for operational UI rendering.
  * `/api/v1/analytics/summary`: DuckDB-driven fleet aggregations, vessel counts, and speed percentiles over published Parquet files.
  * `/api/v1/analytics/partitions`: Published and outbox partition status and catalog metadata.
  * `/api/v1/analytics/vessels/{mmsi}/track`: Trajectory point extraction from Parquet with spatial/temporal bounding.

---

## 7. Failure Modes and Limitations

* **AIS Gaps in Open Oceans:** Free terrestrial feeds cannot track mid-ocean transit. Mitigated by explicit `coverage: coastal` reporting; interpolation across unobserved oceanic spans is forbidden.
* **Proprietary Paywall Barriers:** Baltic Exchange raw data is commercially protected. Mitigated by architecting the system to ingest publicly available broker fixture aggregates and proxy indices until commercial enterprise agreements are established.
* **Temporal Discrepancies:** Heterogeneous sensor feeds broadcast varying timestamp conventions. Mitigated by strict contractual verification of UTC; timezone-naive data without contract is quarantined with `ANOMALY_AMBIGUOUS_TIMEZONE` ([REQ-NFR-DIP-003](../requirements/nonfunctional-requirements.md)).

---

## 8. Security, Privacy and Licensing

* **License Auditing & Statutory Terms:** NOAA MarineCadastre data is produced by the U.S. Federal Government (NOAA Office for Coastal Management and U.S. Coast Guard) and is not subject to domestic copyright protection in the United States pursuant to 17 U.S.C. § 105 (U.S. Public Domain). It is provided "as-is" without warranty, subject to attribution guidelines and terrestrial receiver coverage boundaries. CC0 1.0 Universal is not claimed where statutory U.S. Government Work provisions apply.
* **Credential Protection:** Ingestion API keys (AISStream, weather portals) are stored in server environment variables and never logged in plain text.
* **Sanctions Compliance:** Automated matching against OFAC/EU SDN lists prevents commercial recommendation of sanctioned vessels ([REQ-NFR-SBL-002](../requirements/nonfunctional-requirements.md)).

---

## 9. Testing and Verification

* **Schema Validation Tests:** Automated Pydantic schema validation tests running against sample payloads from each domain.
* **Kinematic Outlier Tests:** Test suites simulating impossible vessel velocities (>30 kts for Capesize) verifying quarantine routing ([REQ-FR-DII-002](../requirements/functional-requirements.md)).
* **Synthetic Data Prohibition Audit:** Regular CI checks scanning production tables for unlabelled synthetic fixtures ([REQ-NFR-DIP-001](../requirements/nonfunctional-requirements.md)).
* **Dual-Tier Storage & Concurrency Tests:** Focused test suites verifying worker claim/completion CAS atomicity, generation bump fencing, worker vs. cleanup mutual exclusion, stale cleanup lock recovery, tombstone retention on post-rename boundary loss, recovery CAS guardrail, and publication state reader isolation.

---

## 10. Traceability

### 10.1 Upstream Traceability
* **Project Overview:** Directly traces to [DOC-002 Section 6 (Dependencies and Interfaces)](../PROJECT_OVERVIEW.md) and Section 4 (Requirements and Constraints).
* **Functional Requirements:** Traces to [REQ-FR-DII-001 through DII-005](../requirements/functional-requirements.md) (Data Ingestion & Integrity), [REQ-FR-OMR-001](../requirements/functional-requirements.md) (Navigational Feasibility), and [REQ-FR-GDS-001](../requirements/functional-requirements.md) (Disruptions).
* **Nonfunctional Requirements:** Traces to [REQ-NFR-DIP-001](../requirements/nonfunctional-requirements.md) (Zero Fabricated Observations), [REQ-NFR-DIP-002](../requirements/nonfunctional-requirements.md) (Synthetic Test Segregation), [REQ-NFR-DIP-003](../requirements/nonfunctional-requirements.md) (Multi-Dimensional ISO 8601 UTC Temporal Consistency), and [REQ-NFR-ABM-002](../requirements/nonfunctional-requirements.md) (Storage Tier Separation).

---

## 11. Open Questions
* **OQ-DATA-01: Baltic Exchange Commercial Access Licensing:** Will the project sponsor provide commercial Baltic Exchange licensing credentials, or will Phase 1 rely on public broker fixture reports and proxy indices?
* **OQ-DATA-02: Bathymetric Sounding Resolution:** What open bathymetric dataset (e.g., GEBCO 15-arc-second grid vs. NOAA ENC soundings) provides the optimal balance between global file size and fairway draft verification precision?
* **OQ-DATA-03: Satellite AIS Trial Procurement:** Should the project evaluate a limited-duration trial of satellite AIS data (e.g., Spire Maritime) specifically for target Indian Ocean corridors?

---

## 12. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-10-14 | Project Agent | Initial draft of Pelagos Data Architecture and Source Strategy (DATA-001) under Milestone D3. Categorizes data domains, distinguishes verified from candidate sources, defines temporal governance, and documents data feasibility risks. Status: drafting. |
| 1.1.0 | 2026-10-15 | Project Agent | D5 Ingestion Architecture: added CLI workflow, reconciliation equation, partition tracking, schema migration mechanism, and statutory 17 U.S.C. § 105 licensing terms. |
| 1.2.0 | 2026-10-15 | Project Agent | D6 Storage Foundation: added Dual-Tier storage subsystem (`pelagos.storage`), PyArrow immutable artifacts, transactional outbox with generation/attempt-UUID fencing, DuckDB analytical engine, REST API endpoints, and CLI sync/catalog/reconcile commands. |
