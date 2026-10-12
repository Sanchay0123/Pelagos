---
title: Pelagos Project Overview
document_id: DOC-002
type: project_overview
status: under_review
version: 1.0.2
created: 2026-10-10
updated: 2026-10-10
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
Pelagos builds upon the open-source God's Eye View repository as its interactive 3D visual foundation, repurposing it from a general intelligence dashboard into a focused maritime command center. 
* **Implemented Capabilities:** The God's Eye View frontend foundation and the initial documentation standards exist. This foundation does not yet constitute integrated real-time AIS tracking or validated maritime routing.
* **Planned/Researched Capabilities:** Integrated real-time AIS tracking, validated operational maritime routing, Freight Forecasting, Cargo Demand Forecasting, Vessel Availability, and an Optimization Engine are planned but not yet implemented.

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

**Forecasting Principles:**
Forecast eligibility is heavily dependent on actual route-level data coverage and quality. The intended design explicitly distinguishes between `unavailable`, `insufficient-data`, `eligible`, and `available` forecasting states (though these states are not yet implemented). Ordinary forecasts will be distinguished from scenario-based analysis where relevant.

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
