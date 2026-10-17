---
title: Pelagos Documentation Index
document_id: DOC-003
type: documentation_index
status: approved
version: 1.0.12
created: 2026-10-10
updated: 2026-10-17
tags:
  - index
  - navigation
---

# Pelagos Documentation Index

## 1. Purpose
This document serves as the central documentation index and navigation entry point for the Pelagos project. It maps the structure of the documentation system, distinguishing between approved sources of truth, planned specifications, and external reference notes.

## 2. Scope
This index covers all foundational policies, architectural decisions, specifications, and reference materials relevant to the Pelagos project.

## 3. Background and Context
The documentation system is organized by domain and lifecycle state. Active and planned documents govern the system's architecture and requirements, while reference materials provide historical planning context but do not supersede approved decisions. For the governing documentation policy, see the [Documentation Standard](./DOCUMENTATION_STANDARD.md).

## 4. Requirements and Constraints
N/A

## 5. Design or Specification (Document Directory)

### Core Foundations
* [Documentation Standard](./DOCUMENTATION_STANDARD.md) (v1.1.1, Status: Approved)
* [Project Overview](./PROJECT_OVERVIEW.md) (v1.0.3, Status: Approved)
* [Documentation Index](./INDEX.md) (v1.0.12, Status: Approved)

### Architecture and Decisions
* [System Architecture Specification](./architecture/system-architecture.md) (v1.0.0, Status: Drafting)
* Architecture Decision Records (ADRs):
  * [ADR-0001: Modular Monolith Backend Architecture](./architecture/adr/adr-0001-modular-monolith-architecture.md) (v1.0.0, Status: Drafting)
  * [ADR-0002: Dual-Tier Storage Strategy](./architecture/adr/adr-0002-dual-tier-storage-strategy.md) (v1.0.0, Status: Drafting)
  * [ADR-0003: Container Orchestration via Docker Compose](./architecture/adr/adr-0003-container-orchestration-docker-compose.md) (v1.0.0, Status: Drafting)
  * [ADR-0004: Core Technology Independence & Proprietary API Exclusion](./architecture/adr/adr-0004-core-technology-independence.md) (v1.0.0, Status: Drafting)
  * [ADR-0005: Frontend Integration and Basemap Decoupling](./architecture/adr/adr-0005-frontend-integration-and-basemap-strategy.md) (v1.0.0, Status: Drafting)

### Product and Requirements
* [Functional Requirements Specification](./requirements/functional-requirements.md) (v1.0.5, Status: Drafting)
* [Nonfunctional Requirements Specification](./requirements/nonfunctional-requirements.md) (v1.0.3, Status: Drafting)

### Data and Intelligence
* [Data Architecture and Source Strategy](./data/data-architecture.md) (v1.0.0, Status: Drafting)
* [Freight Index Specification and Baseline Forecasting Feasibility Assessment (ML-001)](./forecasting/freight-index-and-forecasting-specification.md) (v1.3.0, Status: Approved)
* [Operational Maritime Routing Readiness Assessment (ROUT-001)](./routing/operational-maritime-routing-readiness.md) (v1.1.0, Status: Drafting)

### Engineering and Operations
* API Specifications (Planned)
* Security Specifications (Planned)
* Testing Specifications (Planned)
* Runbooks (Planned)

### Reference Material (Not Approved Architecture)
*Note: The following materials provide historical evidence and context but do not operate as approved architectural decisions. Some of these are external references and are not hosted in this repository.*
* Core Idea and Planning Base (External Reference)
* Initial Assessment (External Reference)
* God's Eye View Arch (External Reference)
* Initial Frontend Discussion and Devops Flow (External Reference)
* Dataset Needed (External Reference)
* [God's Eye View Current State](../gods-eye-view-main/docs/CURRENT-STATE.md) (Status: Existing Reference)

*(Additional reference files pertaining specifically to the frontend repository can be found in `gods-eye-view-main/docs/`)*

### Glossary
* Glossary (Planned)

## 6. Dependencies and Interfaces
N/A

## 7. Failure Modes and Limitations
N/A

## 8. Security, Privacy and Licensing
N/A

## 9. Testing and Verification
N/A

## 10. Traceability
Links to established, governed documentation within the repository, and maps planned specifications to be authored in subsequent milestones.

## 11. Open Questions
* Finalizing the acquisition and licensing of the primary historical freight datasets.
* Evaluating and confirming basemap provider configurations and their associated billing constraints.
* Determining the exact approval state of various architectural decisions (e.g., database, basemap) with the Project Overseer.

## 12. Change History
| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-10-10 | Agent | Initial creation of the documentation index. |
| 1.0.1 | 2026-10-10 | Agent | D1-R1 corrections: Removed unportable external links, updated document type, documented unresolved questions. |
| 1.0.2 | 2026-10-10 | Agent | D1-R2 corrections: updated document type to documentation_index, refined traceability statement to distinguish established from planned specifications. |
| 1.0.3 | 2026-10-12 | Project Overseer / Agent | Formal approval following D0/D1 review. Status updated to approved, reflecting final statuses and versions across Core Foundations. |
| 1.0.4 | 2026-10-12 | Project Agent | Registered Functional Requirements (REQ-FR-001) and Nonfunctional Requirements (REQ-NFR-001) in Product and Requirements catalog under Milestone D2. |
| 1.0.5 | 2026-10-14 | Project Agent | D2-R1 update: Synchronized requirements specification version references (v1.0.1) following quality review. Kept status approved. |
| 1.0.6 | 2026-10-14 | Project Agent | D2-R2 update: Synchronized requirements specification version references (v1.0.2) following final requirements audit. Kept status approved. |
| 1.0.7 | 2026-10-14 | Project Agent | D2 final closure corrections: Synchronized requirements specification version references (v1.0.3) following final closure corrections. Kept status approved. |
| 1.0.8 | 2026-10-14 | Project Agent | D2 final review update: Synchronized Functional Requirements Specification version reference (v1.0.4) following forecasting evaluation threshold governance resolution. Kept status approved. |
| 1.0.9 | 2026-10-14 | Project Agent | Final D2 consistency pass: Synchronized Functional Requirements Specification version reference (v1.0.5) following final forecasting requirements reconciliation. Kept status approved. |
| 1.0.10 | 2026-10-14 | Project Agent | Milestone D3 launch: Registered System Architecture Specification (ARCH-001), Data Architecture Specification (DATA-001), and initial ADRs (ADR-0001 through ADR-0005). Kept status approved. |
| 1.0.11 | 2026-10-17 | Project Agent | D8.6 audit: Registered Freight Index Specification and Baseline Forecasting Feasibility Assessment (ML-001) as approved deliverable. |
| 1.0.12 | 2026-10-17 | Project Agent | Milestone D9.1 launch: Registered Operational Maritime Routing Readiness Assessment (ROUT-001) under drafting status. Kept status approved. |
