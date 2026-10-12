---
title: Pelagos Documentation Index
document_id: DOC-003
type: documentation_index
status: under_review
version: 1.0.2
created: 2026-10-10
updated: 2026-10-10
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
* [Documentation Standard](./DOCUMENTATION_STANDARD.md) (Status: Under Review)
* [Project Overview](./PROJECT_OVERVIEW.md) (Status: Under Review)

### Architecture and Decisions
* Architecture Specifications (Planned)
* Architecture Decision Records (ADRs) (Planned)

### Product and Requirements
* Functional Requirements (Planned)
* Nonfunctional Requirements (Planned)

### Data and Intelligence
* Dataset and Data-Provider Specifications (Planned)
* ML and Forecasting Specifications (Planned)
* Operational Routing Specifications (Planned)

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
