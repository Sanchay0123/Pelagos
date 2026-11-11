---
title: Pelagos Project Overview
document_id: DOC-002
type: project_overview
status: approved
version: 1.0.5
created: 2026-10-10
updated: 2026-11-11
tags:
  - overview
  - scope
  - architecture
---

# Pelagos Project Overview

## 1. Purpose (Project Identity)
Pelagos is an intelligent maritime freight forecasting and vessel chartering optimization platform. Its core purpose is to act as a "Maritime Intelligence Command Center." By combining predictive freight rate models with vessel availability and operational constraints, it aims to recommend optimal cargo procurement and chartering strategies.

## 2. Scope (Users and Decisions)
**Geographic Scope:** Pelagos is intended to support global maritime routes according to actual data availability and validated route coverage. While an initial regional validation focus may target specific corridors (e.g., East Coast Indian ports), the architecture is not permanently restricted to these routes.
**Users and Decisions:** Pelagos supports maritime charterers, freight forecasters, and cargo procurement teams. Instead of merely outputting a generic predicted freight rate, the system translates forecasts into actionable decisions (e.g., illustrative examples such as "Wait 6 days, book a Panamax vessel on a 3-voyage contract").
**System Scope:** The planned system encompasses real-time vessel tracking visualization, freight forecasting, cargo demand modeling, and a constraint-based optimization engine. It does not replace official navigation systems and is not a navigation-grade safety system.

## 3. Background and Context (Capability Maturity)
Pelagos builds upon the open-source God's Eye View repository as its interactive 3D visual foundation, repurposing it from a general intelligence dashboard into a maritime intelligence command center.
* **Implemented & Verified Capabilities:**
  * God's Eye View frontend integration (`gods-eye-view-main`) coupled to FastAPI REST endpoints (`/api/v1/*`, `/api/ais-live`).
  * Data ingestion pipeline, quality quarantine, canonical port/alias directory, and dual-tier SQLite + Apache Parquet + DuckDB analytical storage (`pelagos.ingestion`, `pelagos.storage`).
  * Operational maritime routing across both prototype corridor graphs and compiled Eurostat MARNET 50 km graphs with dynamic canal bypass rules, antimeridian segmentation, and explicit `unsupported_route` fail-closed handling (`pelagos.routing`, `ROUT-004`, `ROUT-006`).
  * Freight index (`PEGI-USJPN`) and multi-horizon (`T+1`, `T+3`, `T+6`) forecasting baseline engine on genuine monthly U.S.–Japan grain rates (`PNW-JPN-BULK`, `USG-JPN-BULK`), with `persistence` confirmed as the active production baseline after Stage C3.1–C3.3 empirical benchmarking against ML and regime-aware candidates (`pelagos.forecasting`).
  * Stage D / Milestone D10 & D10.2 optimization foundations (`pelagos.optimization`): deterministic gross freight-cost calculation (`/api/v1/calculator/voyage-freight`), canonical `Vessel` + `VesselPosition` suitability matching with explicit missing particulars and cargo/port/canal/route compatibility (`/api/v1/calculator/vessel-suitability`, `/api/v1/charter/assess`), and the six-gate fail-closed chartering readiness engine (`/api/v1/charter/recommend`).
  * Stage E1 Platform Data Overview & Detailed Data Explorer (`pelagos.storage.platform_data`, `gods-eye-view-main`): read-only multi-domain overview (`/api/v1/platform-data/overview`) and server-paginated/filtered/sorted 8-domain explorer (`/api/v1/platform-data/explorer/{domain}`) embedded in `#pelagos-panel` and hash-routed `#/explorer` without destroying initialized Cesium globe state ([`API-001`](./architecture/platform-data-explorer.md)).
* **Partially Implemented Capabilities:**
  * Multi-contract charter comparison (`Spot`, `COA`, `TC`) and laycan matching logic are implemented and verified against explicit `VerifiedCharterInputs`, but production queries on monitored routes deterministically return `insufficient_data` because live commercial inputs are absent.
  * Oceanographic buoy and bunker price integrations are operational for available stations/quotes while explicitly flagging missing corridor hub quotes (`unavailable`) without proxy substitution.
* **Blocked by External Data & Unimplemented Capabilities:**
  * Real-time global AIS feeds (`vessel_positions` holds a `120`-row `2024-01-01` historical NOAA MarineCadastre sample), verified vessel DWT/hold-cleanliness (`0/120` DWT populated), commercial broker open-tonnage/TC/COA fixture lists, terminal berth LOA/beam limits, and live Panama/Suez canal advisories are not connected.
  * Multi-decade exogenous trade/weather/bunker/AIS histories required before non-Persistence ML forecasting can be evaluated out-of-sample across cycles.
  * Forward cargo volume projection (`REQ-FR-CDV-001`), automated geopolitical disruption workflows (`pelagos.disruption`), OFAC/EU sanctions screening (`REQ-NFR-SBL-002`), and persistent `charter_audit_log` storage (`REQ-NFR-OAS-002`) remain unimplemented.

## 4. Requirements and Constraints (Limitations and Non-goals)
* **Limitations and Non-Goals:** Pelagos is explicitly NOT a navigation-grade safety system. It makes no unsupported claims of immediate global coverage.
* **Geopolitical Risk:** Disruptions and geopolitical factors are modeled as an explicit factor family or subsystem rather than a fabricated universal risk score.
* **Evidence and Integrity:** No synthetic observations may be used to fabricate training or evaluation evidence. The system will clearly identify situations with insufficient data.

## 5. Design or Specification (Architecture and Technology Direction)
**High-Level Architecture Direction:**
The system uses a proposed modular-monolith approach consisting of:
* **Confirmed:** A maritime command center frontend (built on the vanilla JS, CesiumJS, and Vite baseline of God's Eye View).
* **Confirmed:** A Python/FastAPI backend and PostgreSQL plus Parquet data strategy.
* **Confirmed:** No OpenAI dependency is permitted in the core system.
* **Confirmed:** Kubernetes is not part of the initial deployment strategy.
* **Proposed:** Separate logical engines for Data Ingestion, Forecasting (freight and demand), Vessel Analytics, and Optimization (e.g., proposing tools like OR-Tools or XGBoost).
* **Proposed/Unresolved:** Docker Compose is proposed for deployment orchestration but not strictly finalized. The primary basemap provider (e.g., Google Photorealistic 3D Tiles vs. alternatives like Cesium World Terrain) is unresolved; the existing frontend can accommodate alternative imagery sources to avoid billing dependencies.

**Operational Maritime Routing:**
Operational routes must account for vessel characteristics (draft, LOA), port and canal constraints, routeing measures, environmental conditions, and relevant disruptions. Stable maritime-network data is distinct from time-dependent operational conditions. The system does not equate geometric shortest paths with verified operational routes. If the system cannot establish a supported operational route, it must not silently present a geometric path as operationally feasible.

**Forecasting & Chartering Principles:**
Forecast eligibility and chartering optimization depend strictly on actual route-level and vessel-level data coverage and quality. The target lifecycle design distinguishes between `unavailable`, `insufficient-data`, `eligible`, and `available` states (`REQ-FR-FRF-002`); currently implemented route-intelligence, forecasting, and charter-recommendation endpoints enforce monitored/unmonitored corridor bounds and explicit `unavailable` / `insufficient_data` degradation whenever authentic market, vessel DWT, open-tonnage laycan, or bunker data are absent, retaining `persistence` as the active production forecast model (`ML-001`, C3.1–C3.3). Ordinary baseline forecasts remain separated from scenario analysis.

## 6. Dependencies and Interfaces (Data Strategy)
The platform relies on a foundation of verifiable, traceable maritime data.
* Target data domains include real-world AIS positions, Baltic/Platts freight indices, commodity prices, port capacities, and weather data.
* All datasets must include rigorous licensing, provenance, and coverage metadata. The project will not invent dataset availability or claim global completeness without underlying evidence.

## 7. Failure Modes and Limitations
N/A - Covered in Section 4.

## 8. Security, Privacy and Licensing
N/A - To be defined in the future Security Specification.

## 9. Testing and Verification
N/A - To be defined in the future Testing Specification.

## 10. Traceability
* **Upstream:** Project intent is formulated to address the core problem and objectives of SIH 26006 for intelligent freight forecasting and vessel chartering optimization (established conformity pending formal verification).
* **Next Documentation Milestones:** Refer to the [Documentation Index](./INDEX.md) for the roadmap of planned requirements, ADRs, and technical specifications.

## 11. Open Questions
* Finalizing the acquisition and licensing of the primary historical freight datasets.
* Finalizing the basemap provider strategy based on free-tier viability, given that the existing frontend supports alternative imagery sources.

## 12. Change History
| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-10-10 | Agent | Initial creation of the Pelagos Project Overview. |
| 1.0.1 | 2026-10-10 | Agent | D1-R1 corrections: clarified maturity, geographic scope, technology decisions, routing integrity, and data integrity. |
| 1.0.2 | 2026-10-10 | Agent | D1-R2 corrections: reframed SIH 26006 alignment from established conformity claim to project intent. |
| 1.0.3 | 2026-10-12 | Project Overseer / Agent | Formal approval following D0/D1 review. Status updated to approved. |
| 1.0.4 | 2026-11-11 | Implementation Agent | Reconciled Section 3 capability maturity and Section 5 forecasting/chartering principles to reflect implemented Stage A–C3 & Stage D capabilities vs. blocked/unimplemented external feeds. |
| 1.0.5 | 2026-11-11 | Implementation Agent | Added implemented Stage E1 Live Data Overview and Detailed Data Explorer (`#/explorer`, `/api/v1/platform-data/*`, `API-001`) to Section 3 Implemented & Verified Capabilities. |
