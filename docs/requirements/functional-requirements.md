---
title: Pelagos Functional Requirements Specification
document_id: REQ-FR-001
type: requirements
status: drafting
version: 1.0.5
created: 2026-10-12
updated: 2026-10-14
tags:
  - requirements
  - functional-requirements
  - specification
  - maritime
  - routing
  - forecasting
  - chartering
---

# Pelagos Functional Requirements Specification

## 1. Purpose
This document establishes the canonical Functional Requirements Specification for the Pelagos Maritime Intelligence Command Center platform. It defines the required functional capabilities, system behaviors, user-facing interactions, calculation services, and operational constraints necessary to fulfill the system objectives established in the [Project Overview](../PROJECT_OVERVIEW.md) (`DOC-002`).

## 2. Scope
This specification governs all functional aspects of Pelagos across six primary operational domains:
1. **Situational Awareness & 3D Visualization (SAV)**
2. **Data Ingestion & Integrity (DII)**
3. **Route-Conditioned Freight Forecasting (FRF)**
4. **Cargo Demand & Vessel Availability Modeling (CDV)**
5. **Voyage Chartering & Procurement Optimization (CPO)**
6. **Operational Maritime Routing (OMR)**
7. **Maritime Geopolitical Disruption Subsystem (GDS)**

### Boundaries and Non-Goals
* **Non-Navigation-Grade System:** Pelagos is an intelligence, forecasting, and commercial decision-support platform. It is explicitly NOT an Electronic Chart Display and Information System (ECDIS), an integrated bridge system, or a navigation-grade safety system.
* **No Direct Actuation:** Pelagos does not issue physical steering, throttle, or autopilot commands to vessels.
* **Implementation Neutrality:** This document defines what the system shall perform. It does not select specific ML training algorithms, optimization solver libraries, database table schemas, or deployment container topologies.

## 3. Background and Context
Pelagos builds upon the open-source God's Eye View repository as its interactive 3D visual foundation, repurposing it from a general geographic intelligence viewer into a focused maritime command center.

### Capability Maturity Baseline
* **Implemented Baseline:** God's Eye View 3D client baseline (CesiumJS, Vite, vanilla JS) and repository governance standards.
* **Planned Capabilities:** Real AIS telemetry pipelines, route-conditioned freight rate forecasting, vessel charter recommendation optimization, validated operational maritime routing, and explainable geopolitical disruption modeling.

## 4. Requirements Framework and Taxonomy

### 4.1 Requirement Classification
Each requirement in this specification is categorized by maturity:
* **Confirmed:** Core project requirement directly derived from approved project decisions and non-negotiable boundaries ([PROJECT_OVERVIEW.md](../PROJECT_OVERVIEW.md)).
* **Proposed:** Recommended capability or architectural decision subject to validation during architectural definition and Project Overseer review.
* **Unresolved:** Specification item requiring empirical calibration, reference environment benchmarking, or commercial licensing confirmation.

### 4.2 Requirement Item Schema
Every requirement item adheres to the following structure:
* **Requirement ID:** Stable canonical identifier (`REQ-FR-XXX-NNN`).
* **Title:** Concise capability title.
* **Maturity:** `Confirmed`, `Proposed`, or `Unresolved`.
* **Priority:** `Critical`, `High`, `Medium`, or `Low`.
* **Statement:** Normative requirement using RFC 2119 keywords (`SHALL`, `SHOULD`, `MAY`).
* **Rationale:** Operational and commercial justification.
* **Acceptance Criteria:** Verifiable conditions for acceptance.
* **Verification Method:** One of `Inspection`, `Analysis`, `Demonstration`, or `Test`.
* **Traceability:** Upstream link to [PROJECT_OVERVIEW.md](../PROJECT_OVERVIEW.md) and downstream destination.

---

## 5. Detailed Functional Requirements Catalog

### 5.1 Situational Awareness & 3D Visualization (SAV)

#### REQ-FR-SAV-001: 3D Maritime Command Viewport
* **Maturity:** Confirmed
* **Priority:** Critical
* **Statement:** The system SHALL provide an interactive 3D geospatial globe interface based on the God's Eye View foundation, capable of rendering global ocean expanses, coastlines, and maritime features with continuous camera zoom from orbital altitude to berth level.
* **Rationale:** Maritime charterers and cargo planners require global spatial awareness to contextualize vessel distributions, route bottlenecks, and weather disruptions.
* **Acceptance Criteria:**
  1. Globe renders continuous terrain, bathymetric, and imagery layers without disorientation or view clipping.
  2. Camera navigation supports pan, tilt, rotate, and smooth zoom across all standard geographic coordinates.
* **Verification Method:** Demonstration
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 1, 3](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001` (Frontend Subsystem).

#### REQ-FR-SAV-002: Vessel Entity Visualization & Filtering
* **Maturity:** Confirmed
* **Priority:** Critical
* **Statement:** The system SHALL render individual vessel entities on the 3D globe with distinct visual symbology corresponding to vessel classification (e.g., Capesize, Panamax, Supramax, Handysize, Tanker, Container) and allow dynamic filtering by IMO number, MMSI, vessel class, deadweight tonnage (DWT), speed over ground (SOG), current draft, flag state, and operational status (underway, moored, anchored).
* **Rationale:** Commercial charterers must isolate specific vessel classes and relevant commercial candidates within target maritime basins.
* **Acceptance Criteria:**
  1. Selecting a vessel entity displays a contextual details panel showing IMO, MMSI, vessel class, dimensions (LOA, beam, draft), SOG, destination, and latest telemetry timestamp.
  2. Filtering criteria update the rendered entity set dynamically without requiring a page reload.
* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 2](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001` (Visualization Engine).

#### REQ-FR-SAV-003: Operational Voyage Trajectory Rendering
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** The system SHALL visually distinguish between historical recorded vessel tracks (AIS historical breadcrumbs) and computed operational voyage routes, displaying distinct color and line-style encoding.
* **Rationale:** Users must clearly distinguish observed historical navigation from projected or recommended voyage corridors.
* **Acceptance Criteria:**
  1. Historical tracks render as discrete point-connected trajectories with timestamp tooltips.
  2. Projected routes render as continuous maritime corridors annotated with waypoint designations and navigational constraints.
* **Verification Method:** Demonstration
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 3, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

#### REQ-FR-SAV-004: Maritime Infrastructure and Chokepoint Layering
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** The system SHALL render geospatial overlays of key maritime infrastructure, including commercial cargo ports, anchorages, canal passages (Suez, Panama, Kiel), international straits (Malacca, Hormuz, Bab-el-Mandeb, Bosphorus), and designated Traffic Separation Schemes (TSS).
* **Rationale:** Commercial routing decisions are fundamentally constrained by port facilities, canal dimensions, and international navigation chokepoints.
* **Acceptance Criteria:**
  1. Chokepoints and canals display operational status indicators and physical throughput constraints (maximum allowable draft and beam).
  2. Major commercial cargo ports display name, UN/LOCODE, maximum berth draft, and operational status.
* **Verification Method:** Inspection
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 2, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

---

### 5.2 Data Ingestion & Data Integrity (DII)

#### REQ-FR-DII-001: Multi-Dimensional Temporal Ingestion & AIS Telemetry
* **Maturity:** Confirmed
* **Priority:** Critical
* **Statement:** The system SHALL ingest real-world Automatic Identification System (AIS) messages containing vessel position reports (MMSI, latitude, longitude, SOG, COG, true heading, timestamp) and static/voyage data reports (IMO, vessel name, ship type, dimensions, draught, destination, ETA). The system SHALL preserve original source precision and distinguish event/observation time, source publication time (where provided), ingestion time, and processing time, storing all timestamps in normalized UTC representation conforming to ISO 8601 without synthesizing or assuming artificial precision.
* **Rationale:** Real AIS telemetry provides the empirical ground truth for vessel tracking and availability estimation. Accurately distinguishing temporal dimensions prevents temporal distortion, facilitates latency tracking, and maintains full auditability.
* **Acceptance Criteria:**
  1. All ingested records retain normalized UTC timestamps conforming to ISO 8601 while preserving source sub-second precision where present without synthesizing false precision.
  2. Ingested records record distinct temporal dimensions: `observation_time` (recorded vessel ping timestamp), `publication_time` (provider emission timestamp where provided), `ingestion_time` (arrival timestamp at system boundary), and `processing_time` (pipeline transformation timestamp).
  3. Records missing mandatory identifiers (MMSI) or containing coordinates outside [-90, +90] latitude or [-180, +180] longitude are quarantined and logged with structured anomaly codes.
* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 6](../PROJECT_OVERVIEW.md); Downstream: `DATA-001` (Ingestion Pipeline).

#### REQ-FR-DII-002: Vessel-Aware Telemetry Quality Validation and Anomaly Handling
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** The system SHALL execute automated quality validation on incoming AIS telemetry, performing coordinate boundary verification, timestamp monotonicity checks, duplicate message detection, and vessel-type-aware kinematic plausibility screening (evaluating speed-over-ground, rate-of-turn, and displacement between successive pings against physical performance envelopes calibrated for the specific vessel category). The system SHALL NOT discard observations solely on universal uncalibrated thresholds, but SHALL flag anomalous observations with diagnostic classification codes and quarantine them from primary routing and analytical calculations.
* **Rationale:** Corrupt, multipath, or spoofed AIS telemetry degrades the reliability of tracking and availability models. Prematurely discarding observations without diagnostic classification prevents auditability and calibration.
* **Acceptance Criteria:**
  1. Incoming messages are checked for coordinate range validity (WGS84 boundaries) and non-navigable terrestrial polygon intersection.
  2. Duplicate pings (identical MMSI, observation timestamp, and position) are identified and de-duplicated.
  3. Kinematic plausibility is evaluated per vessel category (e.g., bulk carriers, tankers, container ships, high-speed craft), with specific velocity, acceleration, and rate-of-turn thresholds marked for empirical calibration against verified fleet telemetry.
  4. Anomalous records are flagged with structured reason codes (e.g., `ANOMALY_TERRESTRIAL_COORDINATE`, `ANOMALY_KINEMATIC_OUTLIER`, `ANOMALY_DUPLICATE_TIMESTAMP`, `ANOMALY_NON_MONOTONIC_TIMESTAMP`) and quarantined in diagnostic storage rather than silently deleted.
* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 6](../PROJECT_OVERVIEW.md); Downstream: `DATA-001`.

#### REQ-FR-DII-003: Verifiable Provenance and Licensing Metadata
* **Maturity:** Confirmed
* **Priority:** Critical
* **Statement:** Every ingested data batch SHALL be immutably tagged with metadata indicating data provider, provider license tier, ingest timestamp, coverage geographic bounding box, and temporal resolution.
* **Rationale:** Enforces the core project commitment to rigorous data provenance and legal licensing compliance.
* **Acceptance Criteria:**
  1. Data entities without an associated provenance record cannot be queried by analytical or forecasting services.
  2. Provenance records are auditable via internal administrative interfaces.
* **Verification Method:** Inspection
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 6](../PROJECT_OVERVIEW.md); Downstream: `DATA-001`, `ARCH-001`.

#### REQ-FR-DII-004: Strict Prohibition of Unlabeled Synthetic Data
* **Maturity:** Confirmed
* **Priority:** Critical
* **Statement:** The system SHALL NEVER present synthetic, simulated, or fabricated observations as real-world operational observations or model-training evaluation evidence. Synthetic datasets MAY be utilized solely as explicitly isolated and labeled test fixtures for unit and integration testing.
* **Rationale:** Upholds core scientific integrity and prevents synthetic hallucinations from corrupting commercial chartering decisions.
* **Acceptance Criteria:**
  1. All test fixtures containing synthetic data reside in segregated test directories and include unambiguous header markers (`SYNTHETIC_TEST_FIXTURE`).
  2. Production ingestion pipelines reject files bearing synthetic fixture markers.
* **Verification Method:** Test & Inspection
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 4, 6](../PROJECT_OVERVIEW.md); Downstream: `DATA-001`, `TEST-001`.

#### REQ-FR-DII-005: Historical Market Freight Index Ingestion
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** The system SHALL ingest historical and periodic market freight rate indices (e.g., Baltic Dry Index, Baltic Capesize/Panamax/Supramax Indices, Platts indices) linked to standard benchmark voyage and time-charter routes.
* **Rationale:** Historical rate series are required to train and condition predictive freight forecasting engines.
* **Acceptance Criteria:**
  1. Ingested rate records maintain point-in-time publication dates, currency units (USD/day or USD/tonne), and associated standard route identifiers.
  2. Missing market dates (holidays, weekends) are represented explicitly rather than silently interpolated.
* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 6](../PROJECT_OVERVIEW.md); Downstream: `DATA-001`.

---

### 5.3 Route-Conditioned Freight Forecasting (FRF)

#### REQ-FR-FRF-001: Route-Conditioned Freight Rate Prediction
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** The system SHALL calculate route-conditioned freight rate forecasts for specified commercial port pairs and vessel classes over configurable forward forecast horizons (with 7, 15, 30, and 60 days designated as proposed evaluation horizons). Predictive validity and prediction interval calibration SHALL be evaluated against genuine held-out historical observation periods (e.g., chronological out-of-sample split, rolling walk-forward evaluation) and benchmarked against standard baselines (e.g., historical moving average, persistence, and forward freight agreement curves) with verifiable data provenance. The system SHALL NOT claim forecast accuracy or predictive reliability prior to empirical measurement.
* **Rationale:** Commercial charterers require forward-looking rate estimates to decide whether to fix voyages immediately or wait for favorable market movements. Unmeasured or synthetic accuracy claims create catastrophic financial exposure.
* **Acceptance Criteria:**
  1. Forecast outputs include predicted baseline rate (USD/tonne or USD/day), upper and lower confidence intervals, and key influencing driver factors.
  2. Predictions are conditioned on specific route characteristics and historical fixture evidence rather than an unconditioned global average.
  3. Model evaluation reports document out-of-sample error metrics (e.g., MAE, MAPE, RMSE) compared against defined baseline models on genuine held-out historical test windows.
  4. Forward horizons (7, 15, 30, 60 days) are treated as proposed evaluation horizons; predictive validity and interval coverage must be evaluated independently for each horizon before operational use.
* **Verification Method:** Analysis & Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 1, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001` (Forecasting Engine), `ML-001`.

#### REQ-FR-FRF-002: Four-State Forecast Availability Lifecycle and Evaluation Gates
* **Maturity:** Confirmed
* **Priority:** Critical
* **Statement:** For any requested route, port pair, or vessel class, the forecasting service SHALL evaluate data availability, operational status, and demonstrated model predictive validity to assign one of four explicit lifecycle states: `unavailable`, `insufficient-data`, `eligible`, or `available`. The system SHALL NOT treat a route as forecast-ready solely because some data exists, and SHALL NEVER return fabricated predictions when coverage is inadequate. Model promotion to `available` SHALL require passing an empirical out-of-sample evaluation gate against reference baselines with calibrated prediction intervals. Active production forecasts SHALL be continuously monitored for predictive drift, with automated demotion back to `eligible` upon detected performance degradation.
* **Rationale:** Enforces operational and commercial transparency by strictly separating data readiness from demonstrated predictive validity, preventing misleading predictions when data coverage is missing, corridors are disrupted, models are uncalibrated, or predictive performance has degraded.
* **Acceptance Criteria:**
  1. The system assigns states and structured condition reason codes according to the formal state lifecycle definitions:
     * `unavailable`: Assigned when a corridor cannot produce a forecast due to physical, geographic, or operational impossibility.
       - Reason `REASON_UNSUPPORTED_GEOGRAPHY`: Corridor is permanently outside system scope or topologically impossible.
       - Reason `REASON_OPERATIONAL_CLOSURE`: Corridor is defined and within scope, but temporarily blocked due to operational closure (e.g., origin/destination terminal shut down, canal closed, active force majeure).
     * `insufficient-data`: Assigned when a corridor is supported and operationally open, but empirical data coverage, observation density, continuity, or recency is inadequate to support reliable modeling.
       - Reason `REASON_SPARSE_FIXTURES`: Historical trade or freight fixture volume falls below sufficiency thresholds.
       - Reason `REASON_OBSERVATION_GAP`: Substantial temporal gaps exist in the historical series.
       - Reason `REASON_STALE_INPUTS`: Live telemetry or market data feeds are stale or interrupted.
     * `eligible`: Assigned when the corridor has passed the data-readiness gate (historical observation density, continuity, and recency meet sufficiency thresholds, allowing model training), but has NOT yet passed the formal empirical predictive evaluation gate.
       - Reason `REASON_PENDING_MODEL_VALIDATION`: Model is untrained or currently undergoing validation.
       - Reason `REASON_MODEL_DRIFT_DEGRADATION`: Previously active model exhibited performance drift on rolling monitoring windows, causing demotion back to `eligible` to suppress unvalidated predictions while retraining.
     * `available`: Assigned ONLY when the corridor satisfies both data readiness AND demonstrated predictive validity. The corridor must be supported, operationally open, have verified current inputs, and be supported by a model that has passed the formal evaluation gate on genuine held-out observations against reference baselines, with calibrated prediction intervals.
  2. The system transitions between states according to the formal state transition table below:

| Current State | Precondition / Context | Condition / Trigger | Next State | Reason Code | System Action / User Display |
|---|---|---|---|---|---|
| Any | Out-of-scope corridor | Requested route undefined, non-navigable, or outside geographic scope | `unavailable` | `REASON_UNSUPPORTED_GEOGRAPHY` | Explains route impossibility; disables forecast generation. |
| `available` or `eligible` | Operationally open corridor | Terminal closed, canal blocked, or force majeure event declared | `unavailable` | `REASON_OPERATIONAL_CLOSURE` | Explains temporary disruption; suppresses forecasts until corridor reopens. |
| Any | Supported corridor | Historical observation volume, fixture continuity, or live feed recency falls below data-readiness threshold | `insufficient-data` | `REASON_SPARSE_FIXTURES` or `REASON_STALE_INPUTS` | Reports specific data deficiency metrics; suppresses rate predictions. |
| `insufficient-data` | Data insufficiency | Historical observation density, continuity, and recency meet data-readiness sufficiency thresholds | `eligible` | `REASON_PENDING_MODEL_VALIDATION` | Indicates data readiness; triggers model training/evaluation; suppresses production forecasts. |
| `eligible` | Data readiness satisfied | Model passes formal empirical evaluation gate on genuine held-out historical observations against reference baselines, with calibrated intervals | `available` | None (Active) | Displays active rate prediction, calibrated confidence intervals, and data recency indicators. |
| `available` | Active forecast in production | Continuous monitoring detects predictive error exceeding degradation/drift threshold on rolling evaluation windows | `eligible` | `REASON_MODEL_DRIFT_DEGRADATION` | Automatically suppresses live predictions; triggers model retraining; alerts system operators. |
| `available` | Active forecast in production | Live input feed (index, bunker, telemetry) is interrupted or exceeds maximum staleness window | `insufficient-data` | `REASON_STALE_INPUTS` | Flags forecast degradation; hides stale predictions; notifies user of degraded input state. |

  3. **Data-Readiness Gate (`insufficient-data` &rarr; `eligible`):**
     * Historical observation density, fixture continuity, and temporal recency must meet documented data-readiness criteria before a corridor is promoted to `eligible` for model training and evaluation.
     * A continuous historical observation series spanning a minimum of 12 months (to account for annual shipping seasonality and commodity trade cycles) is designated as a proposed provisional data-readiness parameter, subject to empirical trade-volume validation under `OQ-FR-07`.
  4. **Empirical Evaluation Gate (`eligible` &rarr; `available`):**
     * Evaluation partition must use genuine held-out historical observation periods (chronological out-of-sample split or walk-forward validation) with zero data leakage. A 90-calendar-day out-of-sample validation period is designated as a proposed provisional evaluation partition parameter, subject to calibration under `OQ-FR-05`.
     * Out-of-sample predictive accuracy evaluated via standard error metrics (primary metric: Mean Absolute Error [MAE], supported by Root Mean Squared Error [RMSE] and Mean Absolute Percentage Error [MAPE]) must demonstrate superior performance over reference baseline models (persistence, rolling 30-day historical moving average, and Forward Freight Agreement [FFA] curve). The specific statistical significance test, decision criterion / p-value threshold, and minimum percentage improvement margin over baselines have not been formally approved and are designated as provisional evaluation parameters to be calibrated during model development (`OQ-FR-05`).
     * Prediction interval calibration must be verified on the held-out test partition rather than assumed theoretically. Both prediction-interval targets—achieving &ge; 80% empirical coverage for the proposed nominal 80% (p10–p90) interval and &ge; 95% empirical coverage for the proposed nominal 95% interval on the held-out test partition—are classified as provisional calibration targets subject to empirical calibration (`OQ-FR-05`), with traceability to `REQ-FR-FRF-004`.
     * Current input feeds (freight index, bunker prices, vessel supply) must be verified and non-stale.
  5. **Continuous Drift Monitoring and Demotion Gate (`available` &rarr; `eligible`):**
     * The system must continuously evaluate production forecast errors against newly settled fixture observations across a rolling evaluation window. If predictive error significantly degrades, the system must automatically demote the corridor to `eligible` (`REASON_MODEL_DRIFT_DEGRADATION`) to suppress production forecasts until model recalibration occurs.
     * A rolling 30-day evaluation window and a 1.25&times; error degradation multiplier (rolling out-of-sample MAE exceeding 1.25&times; the benchmark baseline or initial validation error) are designated as proposed provisional drift-demotion parameters, subject to operational refinement under `OQ-FR-06`.
  6. Corridors in `unavailable` due to temporary operational closures (`REASON_OPERATIONAL_CLOSURE`) return to their previous evaluated state (`available`, `eligible`, or `insufficient-data`) once the operational disruption clears.

* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 4, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`, `ML-001`.

#### REQ-FR-FRF-003: Separation of Ordinary Forecasts from Scenario-Based Analysis
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** The system SHALL maintain a strict operational boundary between baseline statistical/ML forecasts and user-driven scenario simulations (e.g., "What if the Suez Canal closes for 14 days?", "What if fuel prices increase 25%?"). Scenario outputs SHALL be visually and textually flagged as simulated stress tests.
* **Rationale:** Prevents decision-makers from confusing hypothetical stress scenarios with empirical market forecasts.
* **Acceptance Criteria:**
  1. Scenario projections carry prominent visual badges and metadata tags indicating user-modified scenario assumptions.
  2. Baseline market forecasts remain immutable during scenario configuration.
* **Verification Method:** Demonstration
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

#### REQ-FR-FRF-004: Forecast Explainability and Uncertainty Quantification
* **Maturity:** Proposed
* **Priority:** Medium
* **Statement:** Each generated forecast SHOULD output an explicit breakdown of influencing feature families (e.g., historical rate momentum, bunker price trends, regional vessel supply density, seasonal demand) alongside statistical confidence intervals. The proposed 80% and 95% confidence intervals SHALL be empirically calibrated and evaluated on held-out historical validation sets rather than assuming theoretical Gaussian error distributions without evidence.
* **Rationale:** Chartering decisions involve multi-million-dollar commitments; black-box predictions without explanatory factors and calibrated uncertainty face user distrust.
* **Acceptance Criteria:**
  1. Forecast results include percentage contributions or directionality indicators for top feature families.
  2. Prediction intervals are evaluated for empirical coverage on held-out historical validation data, verifying that provisional calibration targets (nominal 80% p10–p90 coverage target and nominal 95% interval coverage target, tracked under `OQ-FR-05` and `REQ-FR-FRF-002`) capture their respective target proportions of real observations within calibrated tolerance.
* **Verification Method:** Analysis
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 1, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`, `ML-001`.

---

### 5.4 Cargo Demand & Vessel Availability Modeling (CDV)

#### REQ-FR-CDV-001: Regional & Port-Pair Cargo Demand Estimation
* **Maturity:** Proposed
* **Priority:** High
* **Statement:** The system SHALL estimate cargo volume demand across designated commodity trade corridors based on historical trade flow trends, seasonal export calendars, and import terminal processing rates.
* **Rationale:** Understanding cargo demand dynamics helps anticipate regional rate surges and freight shortages.
* **Acceptance Criteria:**
  1. System presents forward projected cargo volumes by commodity type (e.g., thermal coal, coking coal, iron ore, bauxite) across target corridors.
  2. Unresolved data domains (e.g., lack of granular customs trade data) are explicitly marked as unverified estimates.
* **Verification Method:** Analysis
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 2, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

#### REQ-FR-CDV-002: Commercial Vessel Availability & Laycan Matching
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** The system SHALL compute vessel availability within reach of a target loading port by calculating estimated time of arrival (ETA) for candidate vessels against the specified cargo Laycan (Laydays and Cancelling) window.
* **Rationale:** Only vessels physically capable of arriving before the laycan cancelling date are viable chartering options.
* **Acceptance Criteria:**
  1. Candidate vessel list filters out vessels whose ETA exceeds the cancelling date under operational speed assumptions.
  2. Vessels currently laden or undergoing drydock/repairs are excluded from spot laycan candidate lists.
* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 2, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

---

### 5.5 Voyage Chartering & Procurement Optimization (CPO)

#### REQ-FR-CPO-001: Actionable Chartering Decision Recommendation
* **Maturity:** Confirmed
* **Priority:** Critical
* **Statement:** The system SHALL generate actionable charter procurement recommendations that combine freight rate forecasts with vessel availability and cargo requirements (e.g., "Wait 6 days, fix Panamax vessel on a 3-voyage contract", "Fix immediately in spot market").
* **Rationale:** Pelagos differentiates itself from pure freight indices by translating predictive models into optimal operational procurement decisions.
* **Acceptance Criteria:**
  1. Recommendations provide a concrete action: Action Type (`Fix Now`, `Wait`, `Hedge`), Contract Structure (`Spot Single Voyage`, `Consecutive Voyage Charter (COA)`, `Time Charter (TC)`), Recommended Vessel Class, and Expected Cost Benefit.
  2. Each recommendation details the underlying cost-benefit calculation and market timing rationale.
* **Verification Method:** Demonstration & Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 1, 2](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001` (Optimization Engine).

#### REQ-FR-CPO-002: Multi-Contract Horizon Optimization
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** The optimization service SHALL evaluate total procurement cost across multiple contract horizons, comparing immediate spot fixture against consecutive voyage contracts (CVC) and short period time charters (e.g., 3-month to 1-year period TC).
* **Rationale:** When forward freight rates are forecasted to rise, locking consecutive voyages or short period charter shields charterers from spot market inflation.
* **Acceptance Criteria:**
  1. Optimization engine outputs total projected cost, variance risk, and vessel utilization across each contract structure.
  2. Assumptions regarding bunker price escalation and port turnaround delays are explicitly parameterized.
* **Verification Method:** Analysis
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 2, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

#### REQ-FR-CPO-003: Operational Voyage Constraint Evaluation
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** The charter optimization engine SHALL enforce commercial and physical voyage constraints, including load/discharge port draft limits, terminal berth LOA limits, maximum cargo capacity, daily bunker fuel consumption curves, port turnaround times, and demurrage rate exposure.
* **Rationale:** Unconstrained recommendations can suggest vessels that cannot physically berth or that incur punitive demurrage costs.
* **Acceptance Criteria:**
  1. Any candidate vessel violating berth draft, beam, or LOA limits at either loading or discharging ports is automatically pruned from the recommendation set.
  2. Total cost calculation incorporates estimated bunker fuel consumption, canal tolls, and port disbursement costs.
* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 2, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

---

### 5.6 Operational Maritime Routing (OMR)

#### REQ-FR-OMR-001: Evidence-Conditioned Navigational Feasibility Routing
* **Maturity:** Confirmed
* **Priority:** Critical
* **Statement:** The system SHALL evaluate and calculate maritime voyage routes strictly conditional upon the presence of verified navigational network graph coverage, adequate vessel hydrostatic particulars (operational draft, beam, LOA, air draft, displacement), and applicable authoritative fairway depth and passage restriction rules. Passage evaluations SHALL apply governing restriction rules based on vessel class, restriction conditions, source provenance, effective date, and rule freshness, rather than treating dimensional limits as permanent or universal constants. If navigational graph coverage, bathymetric data, vessel particulars, or governing passage restriction rules are inadequate, unverified, or expired for any portion of the corridor, the system SHALL output an explicit `unsupported-route` or `insufficient-data` outcome identifying the specific missing inputs. The system SHALL NOT imply complete global navigational data coverage or guarantee safe passage.
* **Rationale:** Real-world vessel routing depends on physical constraints (under-keel clearance, canal lock geometry, seasonal draft limits, bridge vertical clearances) that vary dynamically over time and by authority. Asserting route feasibility without verified data or assuming static dimensional limits creates unacceptable operational hazards and violates project safety boundaries.
* **Acceptance Criteria:**
  1. Route feasibility evaluations verify vessel draft against authoritative bathymetric sounding data and under-keel clearance (UKC) rules for the active waterway.
  2. Canal, strait, and lock passage feasibility is evaluated against applicable authoritative passage restriction rules incorporating vessel class, dynamic restriction conditions, source provenance, effective date, and rule freshness.
  3. When required fairway soundings, governing passage rules, or vessel dimensions are missing, unverified, or expired, the system outputs an explicit `unsupported-route` or `insufficient-data` outcome and suppresses feasibility assertions.
* **Verification Method:** Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 4, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001` (Routing Subsystem).

#### REQ-FR-OMR-002: Strict Prohibition of Geometric Shortest Path Equivalency
* **Maturity:** Confirmed
* **Priority:** Critical
* **Statement:** The system SHALL NEVER present a geometric shortest path (such as a straight line, great-circle arc, or naive Euclidean sea path) as a verified operational route. If a verified operational route cannot be calculated due to missing network data or constraint violations, the system SHALL explicitly output an `unsupported-route` state.
* **Rationale:** Geometric lines ignore navigational hazards, landmasses, shallow straits, and routing regulations. Presenting them as operational routes is dangerous and violates core project policy.
* **Acceptance Criteria:**
  1. All calculated routes follow approved maritime corridors and navigation network waypoints.
  2. If the routing engine fails to resolve a feasible maritime path, it displays an explicit error ("Operational route unavailable for specified vessel dimensions or corridor network data") and suppresses trajectory rendering.
* **Verification Method:** Test & Inspection
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 4, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

#### REQ-FR-OMR-003: Dynamic Maritime Chokepoint & Canal Constraints
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** The routing engine SHALL incorporate time-dependent operational constraints at key maritime chokepoints (such as Suez Canal convoy scheduling, Panama Canal booking slot availability and seasonal draft restrictions, and Malacca Strait depth limits), conditional upon documented data provider coverage. When dynamic chokepoint data is unavailable, the system SHALL state the data absence and apply documented baseline transit rules.
* **Rationale:** Canal throughput and transit delays directly alter voyage duration, fuel burn, and optimal route selection.
* **Acceptance Criteria:**
  1. Route transit calculations apply canal transit fees and expected transit delay days based on documented schedules.
  2. When canal transit is restricted, alternative bypass routes (e.g., Cape of Good Hope, Cape Horn) are automatically computed and compared.
* **Verification Method:** Analysis & Demonstration
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

#### REQ-FR-OMR-004: Environmental and Weather Routing Influences
* **Maturity:** Proposed
* **Priority:** Medium
* **Statement:** The routing engine SHOULD incorporate adverse meteorological and oceanographic conditions (significant wave height, surface currents, gale-force winds, tropical cyclones), conditional upon documented weather data provider coverage, adjusting vessel speed-through-water and fuel consumption accordingly.
* **Rationale:** Heavy seas reduce vessel speed and dramatically increase fuel consumption, influencing voyage time and charter economics.
* **Acceptance Criteria:**
  1. Storm avoidance buffers route tracks around severe tropical cyclones where forecast grids are available.
  2. Wave resistance formulas adjust estimated voyage duration with documented model assumptions.
* **Verification Method:** Analysis
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

---

### 5.7 Maritime Geopolitical Disruption Subsystem (GDS)

#### REQ-FR-GDS-001: Dedicated Disruption Factor Family Modeling
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** The system SHALL model geopolitical and operational disruptions as an explicit, structured factor family (covering chokepoint blockages, military conflict zones, piracy risk areas, canal draught restrictions, port strikes, and maritime sanctions), rather than collapsing risk into an opaque or fabricated universal risk score.
* **Rationale:** Decision-makers require transparent, categorized operational risk factors to evaluate insurance premiums, crew risk, and rerouting costs.
* **Acceptance Criteria:**
  1. Disruption events are stored with structured attributes: Disruption Type, Geographic Boundary (polygon), Severity/Status, Active Date Range, Affected Corridors, and Verifiable Source Citation.
  2. The system does not output arbitrary single-number global "safety indexes" without underlying component attribution.
* **Verification Method:** Inspection & Test
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 4, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001` (Risk Subsystem).

#### REQ-FR-GDS-002: Explainable Disruption Impact & Rerouting Evaluation
* **Maturity:** Confirmed
* **Priority:** High
* **Statement:** When a route traverses an active disruption zone, the system SHALL quantify the operational and commercial impact of both continuing through the zone and executing an alternative bypass corridor, presenting explicit differentials in distance, transit days, fuel expenditure, insurance surcharge, and overall freight cost.
* **Rationale:** Allows charterers to weigh the financial penalty of rerouting (e.g., +10 to 14 days around the Cape of Good Hope) against war-risk insurance premiums and security hazards in the Red Sea.
* **Acceptance Criteria:**
  1. The UI displays side-by-side comparison cards for standard route vs detour route detailing: Distance Differential (+NM), Time Differential (+Days), Fuel Cost Differential (+USD), and Additional Risk Notes.
  2. Each calculated differential traces to specific speed and fuel price assumptions.
* **Verification Method:** Demonstration
* **Traceability:** Upstream: [PROJECT_OVERVIEW.md Section 2, 5](../PROJECT_OVERVIEW.md); Downstream: `ARCH-001`.

---

## 6. Dependencies and Interfaces

### 6.1 Internal Module Interfaces
* **SAV &larr; DII:** Visual entity renderer ingests filtered vessel position telemetry streams.
* **OMR &larr; DII:** Routing graph consumes port/canal restriction tables and vessel hydrostatic dimensions.
* **FRF &larr; DII:** Forecasting service consumes historical market freight indices and trade flows.
* **CPO &larr; SAV, FRF, CDV, OMR, GDS:** Optimization engine consumes candidate vessel positions, predicted forward rates, cargo requirements, operational route costs, and disruption differentials to compute recommendations.

### 6.2 External Data Provider Interface Boundaries
* **AIS Telemetry Feeds:** Ingestion boundary accepting standard NMEA 0183 / AIS Type 1, 2, 3, 5 messages and normalized JSON/CSV streams.
* **Freight Market Feeds:** Ingestion boundary accepting Baltic/Platts market rate reports.
* **Geospatial Tiles / Imagery:** Pluggable web mapping interface supporting Cesium 3D Tiles, OpenStreetMap, and satellite raster imagery without hard-coded provider lock-in.

---

## 7. Failure Modes and Degradation Behavior

| Failure Mode | Root Cause | System Response & Degradation Behavior |
|---|---|---|
| AIS Stream Interruption | Provider downtime, satellite link loss | Flag vessel telemetry as stale with visual indicator; freeze availability calculation; notify user. |
| Missing Historical Market Data | Inadequate fixture history for niche port pair | Transition route to `insufficient-data` state; display data requirement threshold; suppress rate forecast. |
| Routing Calculation Failure | Path blocked by bathymetric shallow or canal closure | Output `unsupported-route` state; explain constraint violation; prohibit straight-line geometric fallback. |
| Disruption Feed Outage | Geopolitical feed unavailable | Display latest verified timestamp; flag disruption status as "Unverified since [Timestamp]"; maintain baseline routing. |

---

## 8. Security, Privacy and Licensing

* **Strict License Tagging:** Every data feed ingested into Pelagos must have its commercial license boundary validated and indexed in accordance with [DOCUMENTATION_STANDARD.md Section 7](../DOCUMENTATION_STANDARD.md).
* **Quarantine of Synthetic Data:** Under no circumstances shall test fixtures or synthetic observations be leaked into analytical data stores.
* **Zero External OpenAI Dependency:** All functional processing, forecast inference, route optimization, and text generation shall execute locally or via approved private infrastructure without piping data to OpenAI APIs.

---

## 9. Verification Matrix

| Requirement ID | Requirement Title | Priority | Verification Method | Verification Procedure |
|---|---|---|---|---|
| `REQ-FR-SAV-001` | 3D Maritime Viewport | Critical | Demonstration | Verify continuous zoom and globe interaction on God's Eye View baseline. |
| `REQ-FR-SAV-002` | Vessel Entity Visualization | Critical | Test | Verify entity rendering, tooltip data fields, and dynamic filtering by class/flag/draft. |
| `REQ-FR-SAV-003` | Voyage Trajectory Rendering | High | Demonstration | Confirm visual distinction between recorded breadcrumbs and projected corridors. |
| `REQ-FR-SAV-004` | Infrastructure Overlays | High | Inspection | Inspect port, canal, and chokepoint markers for accuracy of physical dimensions. |
| `REQ-FR-DII-001` | Multi-Dimensional Temporal Ingestion | Critical | Test | Execute ingestion test suite on raw AIS packets; verify multi-dimensional timestamps (observation, publication, ingestion, processing), UTC normalization, and preservation of source precision. |
| `REQ-FR-DII-002` | AIS Quality Filtering | High | Test | Ingest anomalous AIS records (kinematic outliers, land coordinates, non-monotonic timestamps); verify quarantine isolation and structured reason codes. |
| `REQ-FR-DII-003` | Verifiable Provenance Tagging | Critical | Inspection | Inspect metadata records of ingested datasets for source, license, and timestamp. |
| `REQ-FR-DII-004` | Prohibition of Synthetic Data | Critical | Test & Inspection | Audit test directories for synthetic fixture markers; verify rejection by ingestion pipeline. |
| `REQ-FR-DII-005` | Freight Index Ingestion | High | Test | Ingest sample Baltic index tables; verify route alignment and handling of non-trading days. |
| `REQ-FR-FRF-001` | Route-Conditioned Forecasting | High | Analysis & Test | Validate forward rate projections against historical corridor baseline datasets. |
| `REQ-FR-FRF-002` | Four-State Availability Lifecycle | Critical | Test | Simulate missing data, operational disruptions, and model drift; verify state transitions across unavailable, insufficient-data, eligible, and available with proper reason codes and evaluation gates. |
| `REQ-FR-FRF-003` | Separation of Forecasts & Scenarios | High | Demonstration | Verify that user scenario simulations are visually badged and do not alter baseline forecast. |
| `REQ-FR-FRF-004` | Forecast Explainability | Medium | Analysis | Verify calculation of feature contribution weights and confidence bands. |
| `REQ-FR-CDV-001` | Cargo Demand Estimation | High | Analysis | Verify calculation of corridor trade demand trends against seasonal calendars. |
| `REQ-FR-CDV-002` | Vessel Availability & Laycan | High | Test | Test vessel filtering against laycan cancelling dates under operational speed limits. |
| `REQ-FR-CPO-001` | Charter Decision Recommendation | Critical | Demonstration & Test | Execute test cases simulating market scenarios; verify actionable procurement recommendation output. |
| `REQ-FR-CPO-002` | Multi-Contract Horizon Optimization | High | Analysis | Verify total procurement cost comparison across spot, CVC, and time charter contracts. |
| `REQ-FR-CPO-003` | Operational Voyage Constraints | High | Test | Verify that vessels violating port draft/LOA limits are pruned from recommendation candidate sets. |
| `REQ-FR-OMR-001` | Navigational Feasibility Routing | Critical | Test | Test route evaluation against verified vessel particulars and authoritative passage rules; verify unsupported or insufficient-data outcome when rules are unverified. |
| `REQ-FR-OMR-002` | Prohibition of Geometric Paths | Critical | Test & Inspection | Test routing on disconnected graphs; confirm `unsupported-route` state and zero straight-line fallback. |
| `REQ-FR-OMR-003` | Chokepoint & Canal Constraints | High | Analysis | Validate transit delay and toll cost application for Suez/Panama passages. |
| `REQ-FR-OMR-004` | Environmental Routing Influences | Medium | Analysis | Inspect wave height and current impact calculations on voyage duration. |
| `REQ-FR-GDS-001` | Disruption Factor Family Modeling | High | Inspection | Verify structured attributes of disruption zones (boundary, severity, verifiable citation). |
| `REQ-FR-GDS-002` | Explainable Disruption Impact | High | Demonstration | Demonstrate side-by-side comparison of standard vs rerouted corridors showing cost/time delta. |

---

## 10. Traceability Matrix

### 10.1 Upstream Traceability to Project Overview (`DOC-002`)
* `REQ-FR-SAV-001` to `SAV-004` &larr; [DOC-002 Section 1, 2, 3](../PROJECT_OVERVIEW.md) (Maritime Intelligence Command Center, God's Eye View visual foundation).
* `REQ-FR-DII-001` to `DII-005` &larr; [DOC-002 Section 4, 6](../PROJECT_OVERVIEW.md) (Verifiable real-world data foundation, licensing metadata, zero synthetic data).
* `REQ-FR-FRF-001` to `FRF-004` &larr; [DOC-002 Section 1, 5](../PROJECT_OVERVIEW.md) (Route-conditioned forecasting, four-state availability machine).
* `REQ-FR-CDV-001` to `CDV-002` &larr; [DOC-002 Section 2, 5](../PROJECT_OVERVIEW.md) (Cargo demand modeling, vessel availability estimation).
* `REQ-FR-CPO-001` to `CPO-003` &larr; [DOC-002 Section 1, 2, 5](../PROJECT_OVERVIEW.md) (Optimization engine, actionable charter procurement recommendations; alignment with SIH 26006 problem statement reflects project intent, formal compliance pending verification).
* `REQ-FR-OMR-001` to `OMR-004` &larr; [DOC-002 Section 4, 5](../PROJECT_OVERVIEW.md) (Operational routing vs geometric lines, physical vessel constraints, evidence-conditioned feasibility).
* `REQ-FR-GDS-001` to `GDS-002` &larr; [DOC-002 Section 4, 5](../PROJECT_OVERVIEW.md) (Geopolitical disruption factor family, explainable rerouting impact).

### 10.2 Downstream Traceability to Future Specifications
* All `REQ-FR-SAV` &rarr; Planned Architecture Specification `ARCH-001` (Visualization Subsystem).
* All `REQ-FR-DII` &rarr; Planned Dataset Specification `DATA-001` and Test Specification `TEST-001`.
* All `REQ-FR-FRF` &rarr; Planned ML Model Specification `ML-001`.
* All `REQ-FR-OMR` &rarr; Planned Operational Maritime Routing Specification `ARCH-001` / Routing Module.
* All `REQ-FR-CPO` &rarr; Planned Optimization Engine Specification `ARCH-001` / Optimization Module.

---

## 11. Open Questions
* **OQ-FR-01: Commercial AIS Ingestion Latency:** What is the acceptable maximum latency for real-time AIS updates under commercial API cost constraints (e.g., 5-minute vs hourly polling)?
* **OQ-FR-02: Initial Regional Corridor Prioritization:** Should Phase 1 validation focus strictly on East Coast Indian dry bulk corridors (e.g., Australia/Indonesia &rarr; Paradip/Vizag) before opening arbitrary global corridors?
* **OQ-FR-03: Historical Freight Data Granularity:** Are daily fixture rate records available from target data providers, or will weekly index aggregates form the baseline?
* **OQ-FR-04: Vessel Kinematic Plausibility Threshold Calibration:** How will velocity, acceleration, and rate-of-turn plausibility thresholds per vessel class be empirically established against verified historical AIS datasets?
* **OQ-FR-05: Forecast Evaluation Gate Calibration and Baseline Benchmarks:** Which standard baseline models (e.g., persistence, rolling 30-day historical moving average, FFA forward curve), primary error metrics (MAE, supported by MAPE/RMSE), minimum out-of-sample evaluation duration (proposed provisional 90-day window), specific statistical significance tests and decision criteria, required performance improvement margins over baselines, and prediction interval calibration tolerances (proposed provisional 80% p10–p90 coverage target and proposed provisional 95% interval coverage target, tracing to `REQ-FR-FRF-004`) will constitute the formal empirical gate for promoting corridors from `eligible` to `available`?
* **OQ-FR-06: Model Drift Detection and Demotion Protocol:** What continuous monitoring rolling evaluation windows (proposed provisional 30 days), statistical drift detection metrics, and error degradation multipliers (proposed provisional 1.25&times; benchmark baseline or initial validation error) will trigger the automatic demotion from `available` back to `eligible`?
* **OQ-FR-07: Historical Data Depth and Sufficiency Thresholds for Corridor Eligibility:** What minimum observation history depth (proposed provisional 12-month continuous historical series to capture seasonal demand cycles), fixture density, and maximum allowable temporal data gaps will define the data-readiness threshold for transitioning corridors from `insufficient-data` to `eligible`?

---

## 12. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-10-12 | Project Agent | Initial draft of Pelagos Functional Requirements Specification (REQ-FR-001) under Milestone D2. |
| 1.0.1 | 2026-10-14 | Project Agent | D2-R1 quality review revisions: Replaced hardcoded AIS speed filter with vessel-type-aware kinematic validation and anomaly quarantine; made route feasibility conditional on verified vessel particulars and network coverage; clarified forecasting four-state semantics (separating data readiness from predictive validity); required out-of-sample held-out evaluation against baselines; updated open questions and change history. Kept status drafting. |
| 1.0.2 | 2026-10-14 | Project Agent | D2-R2 final requirements audit revisions: Refined REQ-FR-DII-001/002 to distinguish multi-dimensional timestamps (observation, publication, ingestion, processing) and preserve source precision; formalized REQ-FR-FRF-002 four-state lifecycle semantics, reason codes (distinguishing permanent vs temporary unavailability, data insufficiency, and pending validation), empirical evaluation gate criteria, and automatic drift demotion; added OQ-FR-06. Kept status drafting. |
| 1.0.3 | 2026-10-14 | Project Agent | D2 final closure corrections: Updated REQ-FR-OMR-001 to remove universal constant canal limits and require dynamic evaluation against authoritative passage rules (vessel class, restriction conditions, provenance, effective date, freshness) with explicit unsupported or insufficient-data outcomes when unverified; updated verification procedure. Kept status drafting. |
| 1.0.4 | 2026-10-14 | Project Agent | D2 final closure refinement: Governed forecasting evaluation thresholds in REQ-FR-FRF-002, distinguishing confirmed architectural requirements (four-state lifecycle, out-of-sample evaluation, baseline comparisons, interval calibration, automated drift demotion) from provisional proposed evaluation parameters (12-month data readiness, 90-day held-out test window, baseline performance margin, 80% p10–p90 interval coverage, and 1.25x rolling drift multiplier); updated OQ-FR-05, OQ-FR-06, and added OQ-FR-07. Kept status drafting. |
| 1.0.5 | 2026-10-14 | Project Agent | Final D2 consistency pass: Reconciled REQ-FR-FRF-002 baseline comparison requirement by marking statistical significance tests, criteria, and margins as provisional parameters tracked in OQ-FR-05; consistently classified both 80% and 95% prediction interval coverage targets as provisional calibration targets in REQ-FR-FRF-002, REQ-FR-FRF-004, and OQ-FR-05; aligned drift threshold reference wording with OQ-FR-06. Kept status drafting. |
