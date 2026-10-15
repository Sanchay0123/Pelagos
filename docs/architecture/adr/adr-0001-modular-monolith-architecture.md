---
title: "ADR-0001: Modular Monolith Backend Architecture"
document_id: ADR-0001
type: adr
status: drafting
version: 1.0.0
created: 2026-10-14
updated: 2026-10-14
tags:
  - architecture
  - adr
  - backend
  - modular-monolith
  - python
  - fastapi
---

# ADR-0001: Modular Monolith Backend Architecture

## 1. Purpose
This Architecture Decision Record (ADR) establishes the architectural topology of the Pelagos backend as a Python modular monolith rather than a distributed microservice mesh.

## 2. Scope
This decision governs the internal organization, packaging, process boundaries, and module communication protocols of the core Pelagos backend application.

## 3. Background and Context
The Pelagos platform encompasses diverse operational capabilities: real-time telemetry ingestion, kinematic validation, maritime routing graph traversal, route-conditioned freight rate forecasting, vessel laycan matching, and charter optimization. 

A central architectural decision is whether to partition these capabilities into separate network-isolated microservices (e.g., individual containers for Routing, Ingestion, Forecasting, and Optimization communicating over HTTP/gRPC) or organize them as well-encapsulated Python domain modules within a unified modular monolith.

The project is governed by small-team development dynamics, rapid iterative validation requirements, and confirmed constraints requiring local execution without Kubernetes ([REQ-NFR-ABM-003](../../requirements/nonfunctional-requirements.md)).

## 4. Requirements and Constraints
* [REQ-NFR-ABM-001](../../requirements/nonfunctional-requirements.md) mandates modular monolith encapsulation with strict internal module boundaries.
* [REQ-NFR-PER-002](../../requirements/nonfunctional-requirements.md) mandates low-latency interactive route calculations (p95 < 5.0s) and sub-second viewport queries.
* [DOC-002 Section 5](../../PROJECT_OVERVIEW.md) mandates Python as the compulsory backend language.
* Prohibits premature operational complexity and excessive network serialization overhead.

## 5. Design or Specification (The Decision)

### Decision
We decide to implement the Pelagos backend as a **Modular Monolith** in Python 3.11+ using the FastAPI framework.

The backend application (`pelagos/`) is structured into dedicated, decoupled domain packages:
* `pelagos.ingestion`: Telemetry and market data streaming, parsing, and anomaly quarantine.
* `pelagos.routing`: Fairway graph traversal, UKC checks, and dynamic canal passage evaluation.
* `pelagos.forecasting`: Route-conditioned freight forecasting, state machine, and drift monitoring.
* `pelagos.commercial`: Cargo demand estimation, fleet registry queries, and laycan matching.
* `pelagos.optimization`: Multi-contract evaluation, bunker calculation, and charter recommendations.
* `pelagos.disruption`: Geopolitical disruption factor family configuration and rerouting impact analysis.
* `pelagos.api`: FastAPI routers exposing clean OpenAPI contracts to the client.

### Structural Rules
1. **Strict In-Process Interface Boundaries:** Cross-module communication occurs exclusively through strongly typed Pydantic interface models and service contracts.
2. **No Cross-Subsystem Database Coupling:** No module may directly access tables owned by another module. Data queries must traverse the owning module's public service API.
3. **Dedicated Background Execution:** Heavy asynchronous workloads (continuous AIS ingestion streams, rolling ML drift evaluation) run in a decoupled worker process (`worker` container) executing the same shared codebase.

## 6. Dependencies and Interfaces
* **Web Framework:** FastAPI / Starlette / Uvicorn (ASGI).
* **Validation & Schemas:** Pydantic v2.
* **Database Access:** SQLAlchemy 2.0 (asyncio) + PostGIS extensions.
* **Upstream Consumers:** God's Eye View frontend via REST and WebSocket.

## 7. Failure Modes and Limitations
* **Process Crash Risk:** A catastrophic uncaught exception in the API server could affect multiple endpoints. Mitigated by comprehensive top-level exception handlers and process supervision under Docker Compose.
* **Language Concurrency Limits:** Python's Global Interpreter Lock (GIL) can restrict multi-threaded CPU-heavy routing or ML inference. Mitigated by running CPU-bound optimization routines in separate worker processes (`ProcessPoolExecutor`) and offloading heavy matrix math to NumPy/C-extensions.

## 8. Security, Privacy and Licensing
* Modular boundaries simplify security auditing and permission boundaries.
* All backend dependencies carry permissive open-source licenses (MIT, BSD, Apache 2.0).

## 9. Testing and Verification
* **Unit Testing:** Modules can be tested completely in-memory with zero network overhead (`pytest`).
* **Boundary Linting:** Import linter rules (`import-linter` or `flake8-import-order`) enforce that modules do not violate cross-subsystem boundary rules.

## 10. Traceability
* **Upstream:** [PROJECT_OVERVIEW.md Section 5](../../PROJECT_OVERVIEW.md) (Architecture Direction).
* **Requirements:** Implements [REQ-NFR-ABM-001](../../requirements/nonfunctional-requirements.md) (Modular Monolith Encapsulation) and supports [REQ-NFR-ARG-002](../../requirements/nonfunctional-requirements.md) (Fault Isolation).
* **Downstream:** Directly guides [ARCH-001](../system-architecture.md).

## 11. Alternatives Considered
1. **Distributed Microservice Architecture:**
   * *Rationale for Rejection:* Deploying 5–6 separate microservices introduces distributed tracing complexity, inter-service network latency, network serialization overhead, cross-service transaction failures, and substantial developer environment friction on local workstations. Deemed premature optimization.
2. **Unstructured Monolith (Single-Package Scripting):**
   * *Rationale for Rejection:* Lacks formal boundaries, leading to tangled spaghetti dependencies, circular imports, and unmaintainable technical debt.

## 12. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-10-14 | Project Agent | Initial draft of ADR-0001: Modular Monolith Backend Architecture under Milestone D3. Status: drafting. |
