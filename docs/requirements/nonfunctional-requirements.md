---
title: Pelagos Nonfunctional Requirements Specification
document_id: REQ-NFR-001
type: requirements
status: drafting
version: 1.0.3
created: 2026-10-12
updated: 2026-10-14
tags:
  - requirements
  - nonfunctional-requirements
  - specification
  - constraints
  - performance
  - security
---

# Pelagos Nonfunctional Requirements Specification

## 1. Purpose
This document establishes the canonical Nonfunctional Requirements Specification for the Pelagos platform. It defines the quality attributes, performance envelopes, data integrity standards, architectural boundaries, security safeguards, and operational constraints governing the system, ensuring compliance with the architectural principles established in the [Project Overview](../PROJECT_OVERVIEW.md) (`DOC-002`) and the [Documentation Standard](../DOCUMENTATION_STANDARD.md) (`DOC-001`).

## 2. Scope
This specification governs nonfunctional aspects across seven primary technical categories:
1. **Data Integrity, Provenance & Scientific Rigor (DIP)**
2. **System Performance & Interactive Responsiveness (PER)**
3. **Availability, Fault Isolation & Graceful Degradation (ARG)**
4. **Architectural Modularity & Storage Tier Separation (ABM)**
5. **Dependency Constraints & Technology Boundaries (DCT)**
6. **Safety Classification & Regulatory Compliance (SBL)**
7. **Observability, Auditability & System Health (OAS)**

### Out-of-Scope Items
* Application implementation code and SQL DDL schemas.
* Hardware procurement specifications.
* Specific commercial cloud provider commitments (system remains deployable via standard container runtimes).

## 3. Background and Context
Pelagos serves as a commercial decision command center operating in complex maritime environments. Its architectural decisions emphasize verifiable empirical data, local execution independence, modular maintainability, and honest handling of data uncertainty. Nonfunctional requirements must prevent common failure patterns in maritime analytics, such as fabricating synthetic data, hiding data outages behind arbitrary numbers, or introducing unverified external cloud API dependencies.

## 4. Requirements Framework and Taxonomy

### 4.1 Requirement Classification
* **Confirmed:** Core system constraints directly mandated by approved project decisions ([PROJECT_OVERVIEW.md](../PROJECT_OVERVIEW.md)) and the documentation policy ([DOCUMENTATION_STANDARD.md](../DOCUMENTATION_STANDARD.md)).
* **Proposed:** Recommended quality targets, architectural proposals, or governance policies subject to empirical benchmarking and Project Overseer authorization.
* **Unresolved:** Specific numerical thresholds where empirical evidence is currently insufficient; for these, the measurement procedure is specified, and the exact threshold is marked as an unresolved calibration target.

### 4.2 Requirement Item Schema
Every nonfunctional requirement item adheres to the canonical schema:
* **Requirement ID:** Stable canonical identifier (`REQ-NFR-XXX-NNN`).
* **Title:** Concise constraint title.
* **Maturity:** `Confirmed`, `Proposed`, or `Unresolved`.
* **Priority:** `Critical`, `High`, `Medium`, or `Low`.
* **Statement:** Normative requirement using RFC 2119 keywords (`SHALL`, `SHOULD`, `MAY`).
* **Rationale:** Engineering, operational, or legal justification.
* **Acceptance Criteria:** Verifiable conditions for acceptance.
* **Verification Method:** One of `Inspection`, `Analysis`, `Demonstration`, or `Test`.
* **Traceability:** Upstream link to [PROJECT_OVERVIEW.md](../PROJECT_OVERVIEW.md) and [REQ-FR-001](./functional-requirements.md).

---

## 5. Detailed Nonfunctional Requirements Catalog

### 5.1 Data Integrity, Provenance & Scientific Rigor (DIP)

#### REQ-NFR-DIP-001: Zero Fabricated Observations Policy
* **Maturity:** Confirmed
* **Priority:** Critical
* **Statement:** The system SHALL NEVER present synthetic, simulated, or fabricated observations as real-world operational observations or model-training evaluation evidence. All analytical findings and predictive evaluations must originate from verifiable, recorded empirical data sources.
* **Rationale:** Preserves scientific integrity and protects charterers from financial losses caused by synthetic hallucinations presented as real market movements.
* **Acceptance Criteria:**
  1. Automated data validation checks verify that all records entering operational and training pipelines possess valid provider attribution and verifiable provenance tags.
  2. Data pipelines reject and log any input file lacking verified provenance metadata.
* **Verification Method:** Test & Inspection
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 4, 6](../PROJECT_OVERVIEW.md); Downstream: `DATA-001`.

#### REQ-NFR-DIP-002: Synthetic Test Fixture Segregation
* **Maturity:** Confirmed
* **Priority:** Critical
* **Statement:** Synthetic datasets MAY be created solely as explicitly labeled test fixtures for isolated unit, component, or integration testing. All such fixtures SHALL be stored in designated test directories, prefixed with unambiguous markers (`SYNTHETIC_TEST_FIXTURE`), and barred from ingestion by production or evaluation pipelines.
* **Rationale:** Allows thorough automated software testing without contaminating operational data stores or model evaluation baselines.
* **Acceptance Criteria:**
  1. Ingestion services fail with an error if a file bearing `SYNTHETIC_TEST_FIXTURE` headers is loaded outside the automated test harness.
  2. Test datasets are stored in dedicated test directory trees separate from production data partitions.
* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 4, 6](../PROJECT_OVERVIEW.md); Downstream: `TEST-001`.

#### REQ-NFR-DIP-003: Multi-Dimensional ISO 8601 UTC Temporal Consistency and Traceability
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** All timestamps across ingestion pipelines, databases, telemetry caches, analytical calculations, and user interfaces SHALL be recorded, stored, and exchanged in normalized ISO 8601 UTC format. The system SHALL preserve original source timestamp values and precision without synthesizing or assuming artificial sub-second or microsecond precision when the source feed provides lower resolution. Timezone-naive source timestamps SHALL NOT be automatically interpreted as UTC unless the source data contract explicitly establishes UTC semantics; where timezone interpretation is unknown, the system SHALL flag or quarantine the record until its temporal meaning can be established. The system SHALL distinguish and maintain four distinct temporal dimensions where available: event/observation time, source publication time (where provided), ingestion time, and processing time, keeping ingestion and processing timestamps separately recorded in UTC.
* **Rationale:** Maritime vessels operate across global time zones and heterogeneous sensor feeds; conflating observation time with ingestion time, fabricating unverified timezones, or synthesizing false precision distorts kinematic analysis, vessel speed calculations, and auditability.
* **Acceptance Criteria:**
  1. Ingestion parsers normalize timestamps to UTC and ISO 8601 representation (`YYYY-MM-DDTHH:MM:SSZ` or `YYYY-MM-DDTHH:MM:SS.sssZ`), preserving genuine source timestamp values and sub-second precision where present without padding with synthetic non-zero digits. Timezone-naive source timestamps are interpreted as UTC only when the provider interface contract explicitly establishes UTC semantics; records with unknown or ambiguous timezone interpretation are flagged with structured anomaly codes (`ANOMALY_AMBIGUOUS_TIMEZONE`) and quarantined until resolved.
  2. Database temporal columns support fractional seconds up to microseconds (`TIMESTAMP WITH TIME ZONE`), but store observations at their genuine source precision.
  3. Ingested entity records preserve discrete fields for `observation_time` (recorded vessel ping), `publication_time` (source feed publication timestamp where available), `ingestion_time` (arrival at system boundary recorded in UTC), and `processing_time` (pipeline transformation timestamp recorded in UTC).
* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 6](../PROJECT_OVERVIEW.md); Downstream: `DATA-001`.

---

### 5.2 System Performance & Interactive Responsiveness (PER)

#### REQ-NFR-PER-001: 3D Globe Visual Render Frame Budget
* **Maturity:** Unresolved Target (Measurement Procedure Defined)
* **Priority:** High
* **Statement:** The client-side 3D globe visualization (CesiumJS) SHALL maintain interactive performance during user pan, zoom, and tilt operations when rendering a baseline of active vessel entities and infrastructure markers.
* **Rationale:** Sluggish or stuttering rendering impairs user situational awareness and commercial command utility.
* **Measurement Method and Workload:**
  * *Workload:* Automated continuous 60-second orbital navigation script rendering 5,000 active vessel billboard entities with labels and maritime infrastructure overlays.
  * *Reference Environment:* Standardized reference developer client hardware profile (e.g., modern 8-core x86_64 CPU, 16GB RAM, WebGL2-compliant GPU) to be formally designated in D3.
  * *Metric and Aggregation:* 1st percentile low and arithmetic mean frames per second (FPS) sampled at 500ms intervals; peak browser heap memory allocation.
  * *Classification and Target:* **Proposed Calibration Target** (provisional target: mean &ge; 30 FPS, peak heap &le; 1.5 GB). These values are provisional proposals and have not yet been empirically benchmarked.
* **Acceptance Criteria:**
  1. Automated client performance harness records frame rate profile and memory consumption across the reference workload.
  2. Measured performance is documented in architectural benchmarking reports without asserting unverified compliance.
* **Verification Method:** Demonstration & Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 3, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001` (Client Performance).

#### REQ-NFR-PER-002: Interactive Query and Route Calculation Latency
* **Maturity:** Unresolved Target (Measurement Procedure Defined)
* **Priority:** High
* **Statement:** The backend calculation engines SHALL return operational maritime routing calculations, freight rate forecasts, and vessel availability filters within interactive response thresholds for standard commercial corridor queries.
* **Rationale:** Interactive chartering workflows require rapid evaluation of multiple routing and contract alternatives.
* **Measurement Method and Workload:**
  * *Workload:* Automated benchmark suite of 1,000 single-corridor route and rate forecast HTTP requests under warm cache conditions across standard commercial corridors.
  * *Reference Environment:* Containerized modular monolith backend executing on designated reference benchmarking host.
  * *Metric and Aggregation:* p50, p90, and p95 response latency from HTTP request dispatch to complete response body receipt.
  * *Classification and Target:* **Proposed Calibration Target** (provisional target: p95 &le; 2.5 seconds). This value is a provisional calibration proposal subject to empirical baseline measurement once routing graph topology and feature store sizes are finalized in D3/D4.
* **Acceptance Criteria:**
  1. Benchmark suite executes automated load runs recording p50, p90, and p95 latencies over 1,000 simulated requests.
  2. Queries exceeding 5.0 seconds return an asynchronous job identifier with progress polling rather than timing out.
* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

---

### 5.3 Availability, Fault Isolation & Graceful Degradation (ARG)

#### REQ-NFR-ARG-001: Graceful Degradation on Low Data Coverage
* **Maturity:** Confirmed
* **Priority:** Critical
* **Statement:** When data coverage for a requested route or port pair is missing, incomplete, or below quality thresholds, the system SHALL gracefully degrade by explicitly transitioning to `unavailable` or `insufficient-data` states. The system SHALL NEVER crash, produce infinite loops, or return fabricated placeholder values.
* **Rationale:** Unpredictable data gaps are an inherent operational reality in global maritime logistics; transparent degradation ensures user trust.
* **Acceptance Criteria:**
  1. Simulating empty historical databases or absent telemetry for a corridor returns a structured HTTP 200/422 response with state code `insufficient-data` and diagnostic details.
  2. UI displays dedicated informative status banners without unhandled JavaScript exceptions.
* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 4, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`, `REQ-FR-FRF-002`.

#### REQ-NFR-ARG-002: Fault Isolation between Logical Subsystems
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** Operational failures or exceptions within analytical subsystems (e.g., freight forecasting model timeout, optimization solver convergence failure, geopolitical feed interruption) SHALL NOT degrade or crash the 3D globe visualization or primary vessel tracking displays.
* **Rationale:** A charterer tracking a vessel must not lose basic situational awareness because an experimental forward forecast calculation failed.
* **Acceptance Criteria:**
  1. Analytical engines run behind isolated API boundaries with circuit breakers.
  2. Unhandled errors in the forecasting module return localized error payloads while vessel tracking APIs remain operational.
* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 3, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

#### REQ-NFR-ARG-003: Ingestion Pipeline Resumption and Idempotency
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** The telemetry ingestion pipeline SHALL be idempotent and capable of resuming after service interruptions or database restarts without creating duplicate records or corrupting historical voyage trajectories.
* **Rationale:** Network interruptions are frequent in distributed data collection; duplicate AIS records distort vessel density metrics.
* **Acceptance Criteria:**
  1. Re-ingesting an identical AIS batch results in zero duplicate records in the primary vessel position table.
  2. Pipeline checkpoints allow resumption from the exact offset of interruption.
* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 6](../PROJECT_OVERVIEW.md); Downstream: `DATA-001`.

---

### 5.4 Architectural Modularity & Storage Tier Separation (ABM)

#### REQ-NFR-ABM-001: Modular Monolith Encapsulation
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** The core Pelagos backend SHALL be structured as a modular monolith with strictly defined component boundaries (SAV, DII, FRF, CDV, CPO, OMR, GDS). Direct database table queries across module domain boundaries are prohibited; inter-module communication SHALL occur solely through public service interfaces or typed internal domain contracts.
* **Rationale:** Preserves architectural clarity, simplifies development and testing, and permits seamless decomposition into independent microservices in the future if scale justifies it.
* **Acceptance Criteria:**
  1. Static analysis tools verify that modules do not import private internal modules or private database models of other domains.
  2. Each module maintains its own isolated domain service layer.
* **Verification Method:** Inspection & Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

#### REQ-NFR-ABM-002: Separation of Transactional and Analytical Storage Tiers
* **Maturity:** Confirmed
* **Priority:** Critical
* **Statement:** The system SHALL enforce an architectural separation of storage tiers: PostgreSQL (relational/spatial) SHALL serve as the transactional, metadata, and operational entity store, while columnar Parquet files SHALL serve as the analytical and historical time-series storage tier. Heavy analytical scans SHALL NOT execute directly against the transactional database.
* **Rationale:** Running heavy analytical queries across years of AIS records on a relational OLTP database causes table locking, buffer exhaustion, and system unresponsiveness.
* **Acceptance Criteria:**
  1. High-volume time-series telemetry (historical AIS breadcrumbs, historical fixture logs) is partitioned and queried from Parquet storage.
  2. Transactional tables maintain indexing optimized strictly for real-time entity lookup and operational configuration.
* **Verification Method:** Inspection & Analysis
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`, `DATA-001`.

#### REQ-NFR-ABM-003: Containerized Deployment Capability & Kubernetes Exclusion
* **Maturity:** Proposed (Confirmed Kubernetes Exclusion)
* **Priority:** High
* **Statement:** Kubernetes SHALL NOT be required for baseline operation or initial deployment (Confirmed constraint). Standard container orchestration via Docker Compose is proposed for deployment orchestration and local developer workflows, subject to finalization during architectural definition in D3 (Proposed decision).
* **Rationale:** Precludes premature operational complexity, eliminates managed Kubernetes overhead, and preserves rapid local development reproducibility.
* **Acceptance Criteria:**
  1. The application stack is deployable locally without Kubernetes or cloud orchestrator dependencies.
  2. If Docker Compose is selected in D3, executing standard container orchestration commands brings up all required services into a healthy operational state.
* **Verification Method:** Demonstration
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

---

### 5.5 Dependency Constraints & Technology Boundaries (DCT)

#### REQ-NFR-DCT-001: Core Technology Independence & OpenAI Dependency Prohibition
* **Maturity:** Confirmed (Proposed Execution Model)
* **Priority:** Critical
* **Statement:** The core Pelagos system (data ingestion, routing algorithms, freight forecasting models, vessel optimization engines, and decision logic) SHALL NOT depend on OpenAI APIs, external proprietary cloud LLM services, or closed third-party SaaS calculation engines (Confirmed constraint). The execution model for core calculation engines (mandatory strict local workstation execution vs. private self-hosted or dedicated private cloud infrastructure) is a proposed architectural decision subject to review in D3 (Proposed decision).
* **Rationale:** Protects intellectual property, prevents vendor lock-in, eliminates recurring external API operational costs, and ensures operational sovereignty.
* **Acceptance Criteria:**
  1. The codebase contains zero imports of the OpenAI SDK or outbound network calls to `api.openai.com`.
  2. Core forecasting and optimization test suites execute successfully without external cloud LLM dependencies.
* **Verification Method:** Test & Inspection
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

#### REQ-NFR-DCT-002: Basemap and Imagery Provider Decoupling
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** The 3D frontend visualization SHALL decouple from any single proprietary basemap provider (such as Google Photorealistic 3D Tiles) by maintaining configurable support for standard open geospatial terrain and imagery providers (e.g., Cesium World Terrain, OpenStreetMap, Sentinel-2 imagery, or local offline raster tiles).
* **Rationale:** Prevents system unviability if commercial tile quotas expire or billing limits are reached.
* **Acceptance Criteria:**
  1. Switching between basemap providers requires only updating a client configuration setting without code modification.
  2. The frontend renders correctly when configured with free-tier / open imagery sources.
* **Verification Method:** Demonstration
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 5, 11](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

#### REQ-NFR-DCT-003: Proposed Permissive Open-Source Licensing Policy
* **Maturity:** Proposed
* **Priority:** High
* **Statement:** All third-party libraries, packages, and frameworks incorporated into the Pelagos codebase SHOULD possess permissive open-source licenses (e.g., MIT, Apache 2.0, BSD, ISC). The prohibition of copyleft licenses (e.g., GPLv3, AGPL) in core services is a proposed licensing governance policy requiring explicit confirmation by the Project Overseer.
* **Rationale:** Protects commercial flexibility and ensures legal compliance for future operational deployments.
* **Acceptance Criteria:**
  1. Automated license scanning tools verify dependency licenses during CI pipeline execution.
  2. Licensing policy confirmation is documented in architectural review before adding new dependencies.
* **Verification Method:** Test
* **Traceability:** Upstream: [DOCUMENTATION_STANDARD.md Section 8](../DOCUMENTATION_STANDARD.md); Downstream: `TEST-001`.

---

### 5.6 Safety Boundary & Legal Compliance (SBL)

#### REQ-NFR-SBL-001: Non-Navigation-Grade Disclaimer and Visual Boundary
* **Maturity:** Confirmed
* **Priority:** Critical
* **Statement:** The system SHALL prominently display a persistent visual disclaimer across all user interfaces, reports, and exports stating: *"PELAGOS IS A COMMERCIAL DECISION SUPPORT AND INTELLIGENCE PLATFORM. IT IS NOT AN OFFICIAL NAVIGATION SYSTEM, ECDIS, OR NAVIGATION-GRADE SAFETY AID. OFFICIAL NAUTICAL CHARTS AND LICENSED BRIDGE CREW GOVERN ALL NAVIGATION ACTIONS."*
* **Rationale:** Mitigates maritime legal liability and establishes clear operational boundaries under international maritime law (SOLAS regulations).
* **Acceptance Criteria:**
  1. Disclaimer is visible on the primary viewport interface and included in all exported PDF/CSV recommendations.
  2. System documentation explicitly reinforces the non-safety classification.
* **Verification Method:** Inspection
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 2, 4](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

#### REQ-NFR-SBL-002: Maritime Sanction and Regulatory Compliance Handling
* **Maturity:** Proposed
* **Priority:** Medium
* **Statement:** Vessel and corridor data handling SHOULD comply with international maritime data privacy and sanction disclosure standards, ensuring that entities subject to active international maritime sanctions are explicitly annotated without compromising audit logs.
* **Rationale:** Assures corporate compliance for maritime trading houses and cargo owners.
* **Acceptance Criteria:**
  1. Sanction status tags are auditable and reflect verifiable regulatory gazettes (e.g., OFAC, EU maritime sanctions).
* **Verification Method:** Inspection
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 8](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

---

### 5.7 Observability, Auditability & System Health (OAS)

#### REQ-NFR-OAS-001: Structured JSON Logging with Trace Correlation
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** All backend service events, API requests, calculation jobs, and pipeline errors SHALL be logged as structured JSON objects containing an ISO 8601 UTC timestamp, log severity level, correlation ID, module identifier, and contextual event payload.
* **Rationale:** Facilitates automated log parsing, distributed tracing, and rapid diagnostics during operational failures.
* **Acceptance Criteria:**
  1. All log entries output valid JSON parseable by standard log aggregators.
  2. Every API request generates or propagates a unique correlation ID across all internal module invocations.
* **Verification Method:** Test
* **Traceability:** Upstream: [DOCUMENTATION_STANDARD.md Section 8](../DOCUMENTATION_STANDARD.md); Downstream: `ARCH-001`.

#### REQ-NFR-OAS-002: Deterministic Decision Recommendation Audit Trail
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** Every generated charter recommendation and route calculation SHALL generate an immutable audit record capturing the input parameter snapshot, active model versions, constraint values, calculation duration, and generated output.
* **Rationale:** Commercial charter fixtures involve legal and financial obligations; charterers require post-hoc auditability to defend decision rationales.
* **Acceptance Criteria:**
  1. Audit records are stored in append-only tables or immutable audit logs.
  2. Rerunning a historical calculation with identical logged parameters and model versions produces identical recommendations.
* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

---

## 6. Dependencies and Interfaces

* **Operating System / Runtime Environment:** Standard 64-bit Linux / Windows environments supporting container runtimes (e.g., Docker Engine 24+) and Python 3.11+.
* **Browser Runtime Environment:** Modern WebGL2-capable browser (Chromium-based 110+, Firefox 115+, Safari 16+) with hardware acceleration enabled.
* **Database Runtimes:** PostgreSQL 16+ with PostGIS extensions; Parquet format v2.0+ with Snappy/Zstandard compression.

---

## 7. Failure Modes and Recovery Policies

| Failure Scenario | Severity | Automated Recovery Policy |
|---|---|---|
| PostgreSQL Connection Loss | Critical | Connection pool auto-reconnects with exponential backoff; cached queries serve read-only data where valid. |
| Ingestion Out-of-Memory (OOM) | High | Worker process restarts via Docker healthcheck; batches process in chunks &le; 50,000 records. |
| Browser WebGL Context Lost | Medium | Cesium viewer automatically catches context loss, notifies user, and attempts context restoration without full page reload. |
| Corrupt Parquet Partition | High | Query service catches read error, flags partition for reconstruction from backup, and logs diagnostic correlation ID. |

---

## 8. Security, Privacy and Licensing

* **Access Control:** All administration and pipeline triggers require authenticated role-based access control.
* **Credential Protection:** Zero hardcoded API keys, database passwords, or private machine paths in source repositories or configuration files ([DOCUMENTATION_STANDARD.md Section 8](../DOCUMENTATION_STANDARD.md)).
* **Data License Boundary Isolation:** Commercial proprietary datasets must reside in access-restricted volumes separated from public or demo distributions.

---

## 9. Verification Matrix

| Requirement ID | Requirement Title | Priority | Verification Method | Verification Procedure |
|---|---|---|---|---|
| `REQ-NFR-DIP-001` | Zero Fabricated Observations | Critical | Test & Inspection | Audit training pipelines; test ingestion rejection of unverified datasets. |
| `REQ-NFR-DIP-002` | Synthetic Fixture Segregation | Critical | Test | Verify synthetic fixture header checks and isolation from production pipelines. |
| `REQ-NFR-DIP-003` | Multi-Dimensional UTC Consistency | High | Test | Verify multi-dimensional timestamp serialization (observation, publication, ingestion, processing), UTC normalization, and source precision preservation. |
| `REQ-NFR-PER-001` | 3D Globe Render Frame Budget | High | Demonstration | Profile CesiumJS frame rendering rate under 5,000 entity load using Chrome DevTools. |
| `REQ-NFR-PER-002` | Query & Routing Latency | High | Test | Execute automated latency benchmark suite over 1,000 corridor route calculations. |
| `REQ-NFR-ARG-001` | Graceful Degradation on Low Data | Critical | Test | Test route calculation on empty dataset; verify structured `insufficient-data` response. |
| `REQ-NFR-ARG-002` | Subsystem Fault Isolation | High | Test | Inject exceptions into forecasting engine; verify 3D globe tracking remains operational. |
| `REQ-NFR-ARG-003` | Ingestion Idempotency | High | Test | Ingest identical batch twice; verify zero duplicate records in primary tables. |
| `REQ-NFR-ABM-001` | Modular Monolith Encapsulation | High | Inspection | Run static import analysis to verify zero unauthorized cross-domain private imports. |
| `REQ-NFR-ABM-002` | Storage Tier Separation | Critical | Inspection | Audit database queries to ensure zero analytical scans on transactional OLTP tables. |
| `REQ-NFR-ABM-003` | Local Containerized Deployment | High | Demonstration | Verify containerized execution without Kubernetes; if Docker Compose is confirmed in D3, test standard compose startup. |
| `REQ-NFR-DCT-001` | OpenAI Dependency Prohibition | Critical | Test & Inspection | Grep codebase for `openai` imports; test core execution in offline network mode. |
| `REQ-NFR-DCT-002` | Basemap Provider Decoupling | High | Demonstration | Switch client configuration from Google 3D Tiles to OpenStreetMap; verify render. |
| `REQ-NFR-DCT-003` | Permissive OSS Licensing | High | Test | Run automated license audit tool across all package dependencies. |
| `REQ-NFR-SBL-001` | Non-Navigation Safety Disclaimer | Critical | Inspection | Inspect all UI viewports and generated PDF/CSV reports for persistent disclaimer. |
| `REQ-NFR-SBL-002` | Sanction Compliance Handling | Medium | Inspection | Verify sanction tag attribution against regulatory notices. |
| `REQ-NFR-OAS-001` | Structured JSON Logging | High | Test | Verify JSON parseability and correlation ID propagation across log streams. |
| `REQ-NFR-OAS-002` | Recommendation Audit Trail | High | Test | Re-run logged historical calculation; verify exact reproducibility of recommendation. |

---

## 10. Traceability Matrix

### 10.1 Upstream Traceability to Project Overview (`DOC-002`)
* `REQ-NFR-DIP-001` to `DIP-003` &larr; [DOC-002 Section 4, 6](../PROJECT_OVERVIEW.md) (Data integrity, zero synthetic data, verified provenance).
* `REQ-NFR-PER-001` to `PER-002` &larr; [DOC-002 Section 3, 5](../PROJECT_OVERVIEW.md) (God's Eye View client responsiveness, interactive command performance).
* `REQ-NFR-ARG-001` to `ARG-003` &larr; [DOC-002 Section 4, 5](../PROJECT_OVERVIEW.md) (Dynamic availability states, graceful degradation, transparent data availability).
* `REQ-NFR-ABM-001` to `ABM-003` &larr; [DOC-002 Section 5](../PROJECT_OVERVIEW.md) (Modular monolith, PostgreSQL + Parquet strategy, local Docker Compose).
* `REQ-NFR-DCT-001` to `DCT-003` &larr; [DOC-002 Section 5, 11](../PROJECT_OVERVIEW.md) (No OpenAI dependency, basemap provider decoupling).
* `REQ-NFR-SBL-001` to `SBL-002` &larr; [DOC-002 Section 2, 4](../PROJECT_OVERVIEW.md) (Non-navigation safety boundary, legal compliance).
* `REQ-NFR-OAS-001` to `OAS-002` &larr; [DOC-002 Section 5](../PROJECT_OVERVIEW.md) (Decision explainability and operational auditability).

### 10.2 Downstream Traceability to Future Specifications
* `REQ-NFR-ABM-001` & `REQ-NFR-ABM-002` &rarr; Planned Architecture Specification `ARCH-001`.
* `REQ-NFR-DIP-001` & `REQ-NFR-DIP-003` &rarr; Planned Dataset Specification `DATA-001`.
* `REQ-NFR-DCT-001` &rarr; Planned ML Specification `ML-001` and Security Specification `SEC-001`.
* All NFRs &rarr; Planned Test Specification `TEST-001`.

---

## 11. Open Questions
* **OQ-NFR-01: Baseline Hardware Profiling Environment:** What standard hardware benchmark profile (CPU core count, GPU model, memory) should be designated as the reference system for client-side FPS and query latency targets?
* **OQ-NFR-02: Parquet Partitioning Scheme:** Should historical telemetry Parquet partitions be sharded by vessel class and temporal month, or by geographic H3 spatial index hexagonal cells?
* **OQ-NFR-03: Offline Cache Storage Limits:** What is the maximum allowable client-side storage cache for offline vessel and port data on end-user machines?
* **OQ-NFR-04: Deployment Orchestration Finalization:** Should standard Docker Compose be confirmed as the official deployment baseline for local and non-Kubernetes environments during D3?
* **OQ-NFR-05: Formal Third-Party OSS Licensing Policy Approval:** Does the Project Overseer confirm the proposed prohibition of copyleft (e.g., AGPL) licenses for core backend packages?

---

## 12. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-10-12 | Project Agent | Initial draft of Pelagos Nonfunctional Requirements Specification (REQ-NFR-001) under Milestone D2. |
| 1.0.1 | 2026-10-14 | Project Agent | D2-R1 quality review revisions: Clarified provisional status of performance targets with explicit measurement procedures; distinguished confirmed constraints (Kubernetes exclusion, OpenAI prohibition) from proposed decisions (Docker Compose, strict local execution, AGPL prohibition); added open questions and change history. Kept status drafting. |
| 1.0.2 | 2026-10-14 | Project Agent | D2-R2 final requirements audit revisions: Refined REQ-NFR-DIP-003 to require multi-dimensional temporal tracking (observation, publication, ingestion, processing), documented UTC normalization, and source precision preservation without synthesizing false microsecond precision; aligned verification matrix procedures. Kept status drafting. |
| 1.0.3 | 2026-10-14 | Project Agent | D2 final closure corrections: Updated REQ-NFR-DIP-003 to forbid automatic UTC interpretation of timezone-naive source timestamps unless contractually established, quarantine ambiguous timestamps, preserve original values/precision, and separate ingestion/processing timestamps; removed duplicated verification and traceability lines. Kept status drafting. |
