---
title: Pelagos Documentation Index
document_id: DOC-003
type: documentation_index
status: approved
version: 1.0.49
created: 2026-10-10
updated: 2026-11-17
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
* [Project Overview](./PROJECT_OVERVIEW.md) (v1.0.5, Status: Approved)
* [Documentation Index](./INDEX.md) (v1.0.27, Status: Approved)

### Architecture and Decisions
* [System Architecture Specification](./architecture/system-architecture.md) (v1.0.3, Status: Drafting)
* Architecture Decision Records (ADRs):
  * [ADR-0001: Modular Monolith Backend Architecture](./architecture/adr/adr-0001-modular-monolith-architecture.md) (v1.0.0, Status: Drafting)
  * [ADR-0002: Dual-Tier Storage Strategy](./architecture/adr/adr-0002-dual-tier-storage-strategy.md) (v1.0.0, Status: Drafting)
  * [ADR-0003: Container Orchestration via Docker Compose](./architecture/adr/adr-0003-container-orchestration-docker-compose.md) (v1.0.0, Status: Drafting)
  * [ADR-0004: Core Technology Independence & Proprietary API Exclusion](./architecture/adr/adr-0004-core-technology-independence.md) (v1.0.0, Status: Drafting)
  * [ADR-0005: Frontend Integration and Basemap Decoupling](./architecture/adr/adr-0005-frontend-integration-and-basemap-strategy.md) (v1.0.0, Status: Drafting)

### Product and Requirements
* [Functional Requirements Specification](./requirements/functional-requirements.md) (v1.0.7, Status: Drafting)
* [Nonfunctional Requirements Specification](./requirements/nonfunctional-requirements.md) (v1.0.3, Status: Drafting)

### Data and Intelligence
* [Data Architecture and Source Strategy](./data/data-architecture.md) (v1.0.0, Status: Drafting)
* [Freight Index Specification and Baseline Forecasting Feasibility Assessment (ML-001)](./forecasting/freight-index-and-forecasting-specification.md) (v1.3.0, Status: Approved)
* [Stage C3.1.2 Feature Coverage & Forecast Dataset Report (RES-C31-001)](./forecasting/c3-1-feature-coverage-and-forecast-dataset.md) (v1.2.0, Research Notes — Approved)
* [Stage C3.2 Candidate ML Benchmarking Report (REPORT-C32-ML-BENCHMARK-001)](./forecasting/c3-2-candidate-ml-benchmarking-report.md) (v1.0.0, Research Notes — Approved; JSON: [c32_ml_benchmark_results.json](./forecasting/artifacts/c32_ml_benchmark_results.json))
* [Stage C3.3 Implementation Specification (SPEC-C33-REGIME-001)](./forecasting/c3-3-implementation-specification.md) (v1.0.0, Research Notes — Approved)
* [Stage C3.3 Regime-Aware Forecasting Investigation Report (REPORT-C33-REGIME-001)](./forecasting/c3-3-regime-aware-forecasting-report.md) (v1.0.0, Research Notes — Approved; JSON: [c33_regime_investigation_results.json](./forecasting/artifacts/c33_regime_investigation_results.json))
* [Stage D: Vessel Suitability & Chartering Intelligence Specification (OPT-001)](./optimization/stage-d-vessel-suitability-and-chartering.md) (v1.0.0, Status: Under Review)
* [Operational Maritime Routing Readiness Assessment (ROUT-001)](./routing/operational-maritime-routing-readiness.md) (v1.1.0, Status: Drafting)
* [Real Maritime Routing Network Data Investigation (ROUT-002)](./routing/D9.3-real-network-data-investigation.md) (v1.0.1, Status: Under Review)
* [Operational Maritime Routing Foundation Implementation Plan (ROUT-003)](./routing/D9.4-implementation-plan.md) (v1.0.0, Status: Under Review)
* [Operational Maritime Routing Foundation Stage 3 Design and Test Plan (ROUT-004)](./routing/D9.4-stage-3-design.md) (v1.4.0, Status: Accepted)
* [Coordinated GUI Maritime Routing Integration (ROUT-005)](./routing/D9.4-increment-3-gui-integration.md) (v1.0.0, Status: Accepted)
* [Commercial Freight Route Intelligence Specification (ROUT-006)](./routing/D9.5-commercial-freight-route-intelligence.md) (v1.0.1, Status: Accepted)
* [Forecast Cadence Reconciliation Decision Record (OD-ROUT-06-01)](./routing/od-rout-06-01-forecasting-horizons-amendment-proposal.md) (v1.0.0, Status: Approved)
* [Marine Bunker Fuel Intelligence & Data Quality Specification (SPEC-BUNKER-001)](./BUNKER_INTELLIGENCE.md) (v1.0.0, Status: Approved)
* [Global Bunker & Voyage Economics Source Discovery (DISC-F141-001)](./F1_4_1_BUNKER_VOYAGE_ECONOMICS_SOURCE_DISCOVERY.md) (v1.0.0, Status: Completed)
* [Vessel Fuel Consumption Intelligence Discovery & Machinery Registry (DISC-F1422-001-ADDENDUM)](./F1_4_2_2_FUEL_CONSUMPTION_INTELLIGENCE_DISCOVERY.md) (v1.2.0, Status: Completed — Stage F1.4.2.2a Implemented)
* [Engine SFOC Library & Load-Curve Parameterization Specification (SPEC-F1422B-001)](./F1_4_2_2B_ENGINE_SFOC_LIBRARY.md) (v1.0.0, Status: Completed & Verified — Stage F1.4.2.2b Implemented)
* [Vessel Fuel Consumption Intelligence Specification (DOC-F1422C-001-REV3)](./F1_4_2_2C_VESSEL_FUEL_INTELLIGENCE.md) (v1.3.0, Status: Approved Statutory Evidence & Technical Consistency Standard — Stage F1.4.2.2c, F1.4.2.2c.1, F1.4.2.2c.2 & F1.4.2.2c.3 Implemented)
* [Global Port & Canal Disbursement Intelligence Discovery (DISC-F1423-001)](./F1_4_2_3_PORT_CANAL_DISBURSEMENT_DISCOVERY.md) (v1.0.0, Status: Discovery Completed — Stage F1.4.2.3 Feasibility Assessment)
* [Global Port & Canal Disbursement Coverage Matrix (MATRIX-F1423-001)](./F1_4_2_3_GLOBAL_COVERAGE_MATRIX.md) (v1.0.0, Status: Completed — Stage F1.4.2.3 Regional Feasibility Matrix)
* [Canal & Port Disbursement Discovery Closure (CLOSURE-F1423-001-REV2)](./F1_4_2_3_DISCOVERY_CLOSURE.md) (v1.2.0, Status: Approved Discovery Closure & Evidence Reconciliation)
* [Canal Tariff Registry & Deterministic Rule Foundation (DOC-F1423A-001)](./F1_4_2_3A_CANAL_TARIFF_REGISTRY.md) (v1.0.0, Status: Implemented & Approved Foundation — Stage F1.4.2.3a)
* [Global Port Disbursement Intelligence Foundation (DOC-F1423B-001)](./F1_4_2_3B_PORT_DISBURSEMENT_REGISTRY.md) (v1.0.0, Status: Implemented & Approved Foundation — Stage F1.4.2.3b)
* [Voyage Disbursement Integration & TCE Gating Framework (DOC-F1424-001)](./F1_4_2_4_VOYAGE_DISBURSEMENT_INTEGRATION.md) (v1.0.0, Status: Implemented & Approved Integration — Stage F1.4.2.4)
* [Voyage TCE Readiness Design Specification (DOC-F143-DESIGN-001)](./F1_4_3_VOYAGE_TCE_READINESS_DESIGN.md) (v1.0.0, Status: Approved Design Specification — Stage F1.4.3 Readiness)
* [Voyage TCE Design Review Addendum (DOC-F143-DESIGN-REVIEW-001)](./F1_4_3_VOYAGE_TCE_DESIGN_REVIEW.md) (v1.0.0, Status: Approved Design Review Addendum — Stage F1.4.3 Readiness)
* [Stage F1.4.3 Phase 1 Implementation Report (REPORT-F143-PHASE1-001)](./F1_4_3_PHASE1_IMPLEMENTATION_REPORT.md) (v1.0.0, Status: Implemented & Audited — Stage F1.4.3 Phase 1)
* [Stage F1.4.3 Phase 1 Independent Verification Gate Report (VERIFY-F143-PHASE1-001)](./F1_4_3_PHASE1_INDEPENDENT_VERIFICATION.md) (v1.0.0, Status: Verified & Remediated — Stage F1.4.3 Phase 1 Gate Verdict PASS)
* [Stage F1.4.3 Phase 1 Final Evidence Reconciliation Report (VERIFY-F143-PHASE1-RECON-001)](./F1_4_3_PHASE1_FINAL_RECONCILIATION.md) (v1.0.0, Status: Reconciled & Approved — Final Stage F1.4.3 Phase 1 Verdict PASS)
* [Stage F1.4.3 Phase 1 Final Regression & Evidence Closure Report (VERIFY-F143-PHASE1-CLOSE-001)](./F1_4_3_PHASE1_FINAL_SIGNOFF.md) (v1.0.0, Status: Approved Final Evidence Closure — Stage F1.4.3 Phase 1 Closed)
* [Stage F1.4.3 Phase 2 Architecture Specification (DOC-F143-PHASE2-ARCH-001)](./F1_4_3_PHASE2_ARCHITECTURE.md) (v1.2.0 / Rev 3, Status: Approved Architecture Specification — Stage F1.4.3 Phase 2)
* [Stage F1.4.3 Phase 2 Implementation Plan (PLAN-F143-PHASE2-001)](./F1_4_3_PHASE2_IMPLEMENTATION_PLAN.md) (v1.2.0 / Rev 3, Status: Approved Implementation Plan — Stage F1.4.3 Phase 2)
* [Stage F1.4.3 Phase 2 Implementation Report (REPORT-F143-PHASE2-001)](./F1_4_3_PHASE2_IMPLEMENTATION_REPORT.md) (v1.0.0, Status: Approved Implementation Report — Stage F1.4.3 Phase 2)
* [Stage F1.4.3 Phase 2 Independent Verification Report (VERIFY-F143-PHASE2-001)](./F1_4_3_PHASE2_INDEPENDENT_VERIFICATION.md) (v1.0.0, Status: Approved Independent Verification Gate Report — Stage F1.4.3 Phase 2)
* [Stage F1.4.3 Phase 2 Independent Verification Audit Report (AUDIT-F143-PHASE2-001)](./F1_4_3_PHASE2_INDEPENDENT_AUDIT.md) (v1.0.0, Status: Approved Audit Report — Stage F1.4.3 Phase 2)
* [Stage F1.4.3 Phase 2 Test Inventory Reconciliation Report (RECON-F143-PHASE2-001)](./F1_4_3_PHASE2_TEST_RECONCILIATION.md) (v1.0.0, Status: Approved Test Reconciliation Report — Stage F1.4.3 Phase 2)
* [Stage F1.4.3 Phase 2 Remediation Register (REGISTER-F143-PHASE2-001)](./F1_4_3_PHASE2_REMEDIATION_REGISTER.md) (v1.0.0, Status: Approved Remediation Register — Stage F1.4.3 Phase 2)
* [Stage F1.4.3 Phase 2 Remediation & Closure Verification Report (REPORT-F143-PHASE2-REMED-001)](./F1_4_3_PHASE2_REMEDIATION_REPORT.md) (v1.0.0, Status: Formally Remediated & Verified — Final Stage F1.4.3 Phase 2 Verdict UNCONDITIONAL PASS)
* [Stage F1.4.3 Phase 2 Controlled Publication Report (GIT-PUBLISH-F143-PHASE2-001)](./GIT_PUBLISH_F143_PHASE2_REPORT.md) (v1.0.0, Status: Approved Publication Report — Published to origin/main)
* [Stage F1.4.3 Post-Phase 2 Operational Readiness Gap Assessment (GAP-F143-001)](./F1_4_3_NEXT_PHASE_GAP_ASSESSMENT.md) (v1.0.0, Status: Drafting — Level 3 Operational Preconditions Assessment)
* [Stage F1.4.3 Phase 3 Proposal (PROP-F143-PHASE3-001)](./F1_4_3_NEXT_PHASE_PROPOSAL.md) (v1.0.0, Status: Drafting — Commercial Evidence Verification & Dual-Control Operations Foundation)
* [Stage F1.5.2-B Controlled Research Pilot Integration Report (REPORT-F152B-001)](./F1_5_2_B_CONTROLLED_RESEARCH_PILOT_INTEGRATION.md) (v1.0.0, Status: Approved Research Pilot Report — Level 2 USG–Japan Bulk Corridor)
* [Stage F1.5.3-A Research Assessment Remediation & Test Baseline Restoration Report (REPORT-F153A-001)](./F1_5_3_A_REMEDIATION_AND_TEST_RESTORATION.md) (v1.0.0, Status: Approved Remediation Report — Test Baseline 100% Restored)
* [Stage F1.5.3-B Dedicated Research Voyage Assessment API Specification (SPEC-F153B-001)](./F1_5_3_B_RESEARCH_VOYAGE_ASSESSMENT_API.md) (v1.0.1, Status: Approved API Specification — Level 2 Dedicated Endpoint)

### Engineering and Operations
* [Stage E1 Platform Data Overview & Detailed Data Explorer Specification (API-001)](./architecture/platform-data-explorer.md) (v1.0.2, Status: Under Review)
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
| 1.0.13 | 2026-10-18 | Implementation Agent | Registered Real Maritime Routing Network Data Investigation (ROUT-002) as under_review following D9.3 evidence corrections. Kept status approved. |
| 1.0.14 | 2026-10-18 | Implementation Agent | Registered Operational Maritime Routing Foundation Implementation Plan (ROUT-003) as under_review. Kept status approved. |
| 1.0.15 | 2026-10-18 | Implementation Agent | Registered Operational Maritime Routing Foundation Stage 3 Design and Test Plan (ROUT-004) as under_review. Kept status approved. |
| 1.0.16 | 2026-10-18 | Implementation Agent | Synchronized ROUT-004 version reference (v1.1.0) following Project Overseer design review revisions. Kept status approved. |
| 1.0.17 | 2026-10-18 | Implementation Agent | Synchronized ROUT-004 version reference (v1.2.0) following Project Overseer final design correction pass. Kept status approved. |
| 1.0.18 | 2026-10-18 | Implementation Agent | Synchronized ROUT-004 version reference (v1.3.0) following Project Overseer final evidence correction pass. Kept status approved. |
| 1.0.19 | 2026-10-18 | Implementation Agent | Synchronized ROUT-004 version reference (v1.4.0) following Increment 2 final corrective pass (symmetric polar boundary exclusion and cache integrity lifecycle specification). Kept status approved. |
| 1.0.20 | 2026-10-18 | Implementation Agent | Milestone D9.4 Increment 3 launch: Registered Coordinated GUI Maritime Routing Integration (ROUT-005) under_review. Kept status approved. |
| 1.0.21 | 2026-10-21 | Implementation Agent | D9.4 final status reconciliation: Synchronized ROUT-004 and ROUT-005 status references to Accepted following Project Overseer manual verification and acceptance audit. Kept status approved. |
| 1.0.22 | 2026-10-21 | Implementation Agent | Milestone D9.5 launch: Registered Commercial Freight Route Intelligence Specification (ROUT-006) under drafting status. Synchronized index version reference. Kept status approved. |
| 1.0.23 | 2026-10-21 | Implementation Agent | Synchronized ROUT-006 status reference to Accepted following independent final review and Project Owner formal specification acceptance. Synchronized index version reference. Kept status approved. |
| 1.0.24 | 2026-10-24 | Implementation Agent | Registered OD-ROUT-06-01 decision record; synchronized Functional Requirements (v1.0.6), System Architecture (v1.0.1), and ROUT-006 (v1.0.1) following formal forecast cadence reconciliation. Status: approved. |
| 1.0.25 | 2026-11-11 | Implementation Agent | Registered promoted Stage C3 research documentation (`RES-C31-001`, `REPORT-C32-ML-BENCHMARK-001`, `SPEC-C33-REGIME-001`, `REPORT-C33-REGIME-001` plus JSON artifacts) and Stage D specification (`OPT-001`); synchronized Project Overview (v1.0.4), System Architecture (v1.0.2), and Functional Requirements (v1.0.7). Status: approved. |
| 1.0.26 | 2026-11-11 | Implementation Agent | Registered Stage E1 Platform Data Overview & Detailed Data Explorer Specification (`API-001`, v1.0.0); synchronized Project Overview (v1.0.5) and System Architecture Specification (v1.0.3). Status: approved. |
| 1.0.27 | 2026-11-12 | Implementation Agent | Synchronized `API-001` reference (`v1.0.1`, updated `2026-11-12`) after Stage E1 documentation-code alignment pass. Status: approved. |
| 1.0.28 | 2026-11-12 | Implementation Agent | Synchronized `API-001` reference (`v1.0.2`, updated `2026-11-12`) following C3.2/C3.3 experimental benchmark visibility integration inside `#/explorer/datasets`. Status: approved. |
| 1.0.29 | 2026-11-15 | Implementation Agent | Registered Stage F1.4.2.2c.2 Vessel Fuel Consumption Intelligence Specification (`DOC-F1422C-001-REV2`, v1.2.0) with statutory evidence distinction, CSR brake power governance, unverified 15% sea margin, and continuous rated-power boundary overload semantics. Status: approved. |
| 1.0.30 | 2026-11-15 | Implementation Agent | Registered Stage F1.4.2.2c.3 Vessel Fuel Consumption Intelligence Specification (`DOC-F1422C-001-REV3`, v1.3.0) with CSR 85% MCR derivation investigation, flywheel brake power physical consistency, and qualified overload boundary semantics. Status: approved. |
| 1.0.31 | 2026-11-15 | Implementation Agent | Registered Stage F1.4.2.3 Discovery Closure (`CLOSURE-F1423-001-REV2`, v1.2.0) with reconciled evidence and vessel particulars. Status: approved. |
| 1.0.32 | 2026-11-15 | Implementation Agent | Registered Stage F1.4.2.3a Canal Tariff Registry & Deterministic Rule Foundation (`DOC-F1423A-001`, v1.0.0). Status: approved. |
| 1.0.33 | 2026-11-15 | Implementation Agent | Registered Stage F1.4.2.3b Global Port Disbursement Intelligence Foundation (`DOC-F1423B-001`, v1.0.0). Status: approved. |
| 1.0.34 | 2026-11-15 | Implementation Agent | Registered Stage F1.4.2.4 Voyage Disbursement Integration & TCE Gating Framework (`DOC-F1424-001`, v1.0.0). Status: approved. |
| 1.0.35 | 2026-11-15 | Implementation Agent | Registered Stage F1.4.3 Voyage TCE Readiness Design Specification (`DOC-F143-DESIGN-001`, v1.0.0). Status: approved. |
| 1.0.36 | 2026-11-15 | Verification Agent | Registered Stage F1.4.3 Voyage TCE Design Review Addendum (`DOC-F143-DESIGN-REVIEW-001`, v1.0.0) resolving FX lookback, partial waiver governance, cryptographic unlock, and tamper-evident audit architecture. Status: approved. |
| 1.0.37 | 2026-11-15 | Implementation Agent | Registered Stage F1.4.3 Phase 1 Implementation Report (`REPORT-F143-PHASE1-001`, v1.0.0) establishing deterministic, auditable, fail-closed voyage readiness foundation across Scopes A-F. Status: approved. |
| 1.0.38 | 2026-11-15 | Verification Agent | Registered Stage F1.4.3 Phase 1 Independent Verification Gate Report (`VERIFY-F143-PHASE1-001`, v1.0.0) with comprehensive independent audit, narrow remediations, isolated migration validation, and final gate verdict PASS. Status: approved. |
| 1.0.39 | 2026-11-15 | Verification Agent | Registered Stage F1.4.3 Phase 1 Final Evidence Reconciliation Report (`VERIFY-F143-PHASE1-RECON-001`, v1.0.0) resolving provider-specific FX lookbacks, complete 12-column additive migration inventory, source permission governance, statutory concession applicability, and platform regression verification. Status: approved. |
| 1.0.40 | 2026-11-15 | Verification Agent | Registered Stage F1.4.3 Phase 1 Final Regression & Evidence Closure Report (`VERIFY-F143-PHASE1-CLOSE-001`, v1.0.0) recording reproduction of original and expanded inventories, source permission taxonomy, Decimal monetary precision remediation, and Suez Article 58 evidence closure. Status: approved. |
| 1.0.41 | 2026-11-16 | Architecture Agent | Registered Stage F1.4.3 Phase 2 Architecture Specification (DOC-F143-PHASE2-ARCH-001, v1.0.0) and Phase 2 Implementation Plan (PLAN-F143-PHASE2-001, v1.0.0) establishing detailed designs, schemas, cryptographic gates, voyage calculator integration, tamper-evident audit ledger, and operational TCE gap analysis. Status: approved. |
| 1.0.42 | 2026-11-16 | Architecture Agent | Revised Stage F1.4.3 Phase 2 Architecture (DOC-F143-PHASE2-ARCH-001 Rev 2) and Plan (PLAN-F143-PHASE2-001 Rev 2) under DESIGN-F143-PHASE2-REV-001: separated four voyage economics concepts (Gross Freight, Net Revenue, Conventional TCE excluding OPEX, and Owner Profitability), defined RFC 8785 canonical Decimal monetary representation, corrected multi-tiered immutability model (disclaiming SQLite admin-proof status), separated IMO syntax from identity and statutory certificates, established candidate cryptographic policy (Ed25519 and P-384 pending evidence), removed implicit commission defaults, defined 1h token / 2h CRL offline window, and ordered implementation sequence (P2-A -> P2-B -> P2-D -> P2-C -> P2-ALL). Status: approved. |
| 1.0.43 | 2026-11-16 | Architecture Agent | Finalized Stage F1.4.3 Phase 2 Architecture (DOC-F143-PHASE2-ARCH-001 Rev 3) and Plan (PLAN-F143-PHASE2-001 Rev 3) under DESIGN-F143-PHASE2-REV-003: codified 5-tier financial precision lifecycle, refined field-specific canonical Decimal string scales, derived 5 timeline scenarios for offline CRL revocation threat model, separated RFC 6962 record Merkle tree (0x00/0x01) from chronological monotonic audit chain (0x02), expanded 7-component voyage duration denominator accounting, and restricted Phase 2 to dev/simulation tokens while barring production release tokens. Status: approved. |
| 1.0.44 | 2026-11-16 | Implementation & Verification Agent | Registered Stage F1.4.3 Phase 2 Implementation Report (REPORT-F143-PHASE2-001, v1.0.0) and Independent Verification Report (VERIFY-F143-PHASE2-001, v1.0.0) establishing unified implementation and verified closure across P2-A (Commercial Fixture Engine), P2-B (Cryptographic Primitives), P2-D (Tamper-Evident Audit Ledger), P2-C (Decoupled Voyage Economics), and 10-scenario Adversarial Security Attack Matrix; Level 3 operational status locked inactive. Status: approved. |
| 1.0.45 | 2026-11-16 | Independent Audit Agent | Registered Stage F1.4.3 Phase 2 Independent Audit Report (AUDIT-F143-PHASE2-001, v1.0.0), Test Reconciliation Report (RECON-F143-PHASE2-001, v1.0.0), and Remediation Register (REGISTER-F143-PHASE2-001, v1.0.0); independently audited source code across Areas A–F, reconciled 200 distinct platform tests (38 Phase 2 + 162 expanded predecessor), verified 10 adversarial attack scenarios, confirmed zero database mutations, and cataloged five medium/low findings under CONDITIONAL PASS. Status: approved. |
| 1.0.46 | 2026-11-16 | Remediation Agent | Registered Stage F1.4.3 Phase 2 Remediation & Closure Verification Report (REPORT-F143-PHASE2-REMED-001, v1.0.0) closing findings P2-001 through P2-006; verified production DB isolation (pelagos.db intact), vessel readiness statutory integration, concurrent token replay handling, Merkle multi-set terminology, and 206 platform tests (100% passing) under UNCONDITIONAL PASS. Status: approved. |
| 1.0.47 | 2026-11-16 | Publication Agent | Registered Stage F1.4.3 Phase 2 Controlled Publication Report (GIT-PUBLISH-F143-PHASE2-001, v1.0.0) documenting formal verification and publication of Phase 2 implementation, tests, and documentation to origin/main. Status: approved. |
| 1.0.48 | 2026-11-16 | Planning Agent | Registered Stage F1.4.3 Post-Phase 2 Operational Readiness Gap Assessment (GAP-F143-001, v1.0.0) and Phase 3 Proposal (PROP-F143-PHASE3-001, v1.0.0) under SCOPE-F143-NEXT-001; cataloged 22 Level 3 blockers across 5 domains, proposed Stage F1.4.3 Phase 3 covering dual-control multi-sig, PostgreSQL append-only persistence, top 15 bulk ports, automated statutory ingestion, and real voyage benchmarking; Level 3 activation strictly locked. Status: approved. |
| 1.0.49 | 2026-11-17 | Implementation Agent | Registered Stage F1.5.2-B Controlled Research Pilot Integration Report (REPORT-F152B-001, v1.0.0) establishing Level 2 research voyage assessment for USG–Japan bulk corridor (USHOU -> Panama -> JPYOK) with NAVIOS VICTORY; Level 3 operational activation strictly locked. Status: approved. |
| 1.0.50 | 2026-11-17 | Implementation Agent | Registered Stage F1.5.3-A Research Assessment Remediation & Test Baseline Restoration Report (REPORT-F153A-001, v1.0.0) resolving ballast sea days schema/duration, currency service memory seeding isolation, and charter DWT capacity reconciliation; verified 245/245 runnable tests passing across 18 platform suites, zero database mutations; Level 3 activation strictly locked. Status: approved. |
| 1.0.51 | 2026-11-17 | Implementation Agent | Registered Stage F1.5.3-B Dedicated Research Voyage Assessment API Specification (SPEC-F153B-001, v1.0.0) exposing POST/GET `/api/v1/research/voyage-assessment` decoupled from commercial charter screening; verified 256/256 runnable tests passing (100%), zero database mutations; Level 3 activation strictly locked. Status: approved. |
| 1.0.52 | 2026-11-17 | Implementation Agent | Stage F1.5.3-B.1 Acceptance Remediation: Updated SPEC-F153B-001 to v1.0.1 with captured live API output ($72,501.08 TCE) and documented error sanitization; sanitized HTTP 500 error shielding in routes/research.py; strengthened test_research_api.py with exact assertions and sensitive detail shielding tests; verified 256/256 runnable tests passing (100%), zero database mutations. Status: approved. |
