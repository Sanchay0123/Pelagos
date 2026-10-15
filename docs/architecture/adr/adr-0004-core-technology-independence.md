---
title: "ADR-0004: Core Technology Independence"
document_id: ADR-0004
type: adr
status: drafting
version: 1.0.0
created: 2026-10-14
updated: 2026-10-14
tags:
  - architecture
  - adr
  - algorithmic-independence
  - openai-prohibition
  - optimization
  - machine-learning
---

# ADR-0004: Core Technology Independence

## 1. Purpose
This Architecture Decision Record (ADR) establishes the policy of core algorithmic independence, mandating self-contained open-source libraries for all critical execution paths and strictly prohibiting dependencies on OpenAI or proprietary closed-source SaaS APIs.

## 2. Scope
This decision governs all routing algorithms, voyage calculations, freight rate forecasting models, vessel laycan matching, and charter optimization solvers in the Pelagos platform.

## 3. Background and Context
A common pitfall in contemporary software development is delegating core deterministic domain logic (such as combinatorial routing, statistical forecasting, or constrained mathematical optimization) to closed-source Large Language Model (LLM) APIs such as OpenAI GPT-4.

In commercial maritime logistics, this approach introduces critical risks:
1. **Unpredictable Latency and Outages:** External third-party API downtime disrupts mission-critical operational decisions.
2. **Non-Deterministic Hallucinations:** LLMs lack formal mathematical guarantees and can invent nonexistent waterways, violate physical canal lock draft limits, or recommend fictitious charter rates.
3. **Severe Cost Exposure:** Per-token billing models scale poorly with high-frequency AIS telemetry and combinatorial vessel-cargo matching.
4. **Data Privacy and Trade Secrets:** Transmitting proprietary commercial fixture terms, vessel positions, and cargo volumes to third-party cloud LLM providers violates maritime industry confidentiality.

Both [PROJECT_OVERVIEW.md Section 5, 11](../../PROJECT_OVERVIEW.md) and [REQ-NFR-DCT-001](../../requirements/nonfunctional-requirements.md) mandate complete algorithmic independence and explicitly prohibit OpenAI dependencies from core execution paths.

## 4. Requirements and Constraints
* [REQ-NFR-DCT-001](../../requirements/nonfunctional-requirements.md) mandates core technology independence and strict exclusion of OpenAI dependencies.
* [REQ-NFR-OAS-002](../../requirements/nonfunctional-requirements.md) mandates deterministic, auditable decision recommendation logic.
* Core execution paths must execute locally on standard hardware without mandatory internet connectivity for internal math/optimization routines.

## 5. Design or Specification (The Decision)

### Decision
We decide that all core analytics, maritime routing, freight rate forecasting, and charter optimization pipelines SHALL be implemented using **self-hosted, open-source algorithmic and statistical frameworks**:

1. **Operational Maritime Routing:** Implemented using open graph-search algorithms (e.g., Dijkstra, A*, Contraction Hierarchies) executed in-process via NetworkX, igraph, or PostGIS `pgrouting` over verified marine fairway networks.
2. **Freight Rate Forecasting:** Implemented using classical statistical and open-source machine learning frameworks (e.g., Statsmodels for ARIMA/ETS, Scikit-Learn, LightGBM, or XGBoost) trained and inferred entirely in-process on local hardware.
3. **Charter Procurement Optimization:** Implemented using deterministic mathematical programming and constraint programming solvers (e.g., Google OR-Tools, SciPy Optimize) optimizing explicit objective functions (procurement cost, transit time, bunker fuel risk).
4. **Natural Language / GenAI Separation:** If natural language summarization or conversational interfaces are introduced in future auxiliary modules, they must run as decoupled, optional plugins (e.g., using local Ollama/vLLM models) and must NEVER participate in the primary routing, forecasting, or optimization calculation loops.

## 6. Dependencies and Interfaces
* **Permissive Open-Source Libraries:**
  * Optimization: `ortools` (Apache 2.0), `scipy` (BSD).
  * Machine Learning: `scikit-learn` (BSD), `lightgbm` (MIT), `statsmodels` (BSD).
  * Graph Routing: `networkx` (BSD), `shapely` (BSD), `pyproj` (MIT).

## 7. Failure Modes and Limitations
* **Local Compute Limits:** Highly complex Mixed-Integer Linear Programs (MILP) or deep neural networks may encounter CPU memory constraints on low-spec developer hardware. Mitigated by setting solver execution time limits (e.g., 5-second cutoff in OR-Tools) and utilizing efficient gradient-boosted trees over deep networks.

## 8. Security, Privacy and Licensing
* Eliminating external cloud LLM dependencies ensures that commercial cargo terms, vessel laycans, and charter negotiations remain strictly within the operator's self-hosted deployment boundary.
* Zero third-party data transmission risks.

## 9. Testing and Verification
* **CI Isolation Audit:** CI pipelines verify that no `openai`, `anthropic`, or proprietary closed-source SDK imports exist anywhere in `pelagos/` core packages.
* **Deterministic Replay Tests:** Tests verify that identical input snapshots submitted to the optimization solver yield identical numerical outputs across repeated executions ([REQ-NFR-OAS-002](../../requirements/nonfunctional-requirements.md)).

## 10. Traceability
* **Upstream:** [PROJECT_OVERVIEW.md Section 5, 11](../../PROJECT_OVERVIEW.md).
* **Requirements:** Realizes [REQ-NFR-DCT-001](../../requirements/nonfunctional-requirements.md) (Core Technology Independence & OpenAI Dependency Prohibition) and supports [REQ-NFR-OAS-002](../../requirements/nonfunctional-requirements.md) (Deterministic Decision Replay).
* **Architecture:** Informs [ARCH-001 Section 4.1 and 5.2](../system-architecture.md).

## 11. Alternatives Considered
1. **LLM-Based Autonomous Optimization (e.g., OpenAI Function Calling / LangChain Agents):**
   * *Rationale for Rejection:* Prohibited by confirmed project decisions. Introduces non-deterministic stochastic outputs, unacceptable commercial hallucination risk, recurring SaaS billing dependencies, and latency unpredictability.
2. **Proprietary Commercial Mathematical Solvers (Gurobi, CPLEX Cloud):**
   * *Rationale for Rejection:* Expensive proprietary commercial licensing fees; conflicts with open-source project accessibility goals.

## 12. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-10-14 | Project Agent | Initial draft of ADR-0004: Core Technology Independence under Milestone D3. Confirms self-hosted open-source analytical stack and strict exclusion of OpenAI dependencies. Status: drafting. |
