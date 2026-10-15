---
title: "ADR-0003: Container Orchestration via Docker Compose"
document_id: ADR-0003
type: adr
status: drafting
version: 1.0.0
created: 2026-10-14
updated: 2026-10-14
tags:
  - architecture
  - adr
  - deployment
  - docker-compose
  - containers
  - kubernetes-exclusion
---

# ADR-0003: Container Orchestration via Docker Compose

## 1. Purpose
This Architecture Decision Record (ADR) establishes the container deployment baseline for Pelagos, confirming Docker Compose as the sole deployment orchestrator and explicitly excluding Kubernetes from the initial baseline architecture.

## 2. Scope
This decision governs container definitions, service networking, port bindings, volume persistence, and runtime orchestration for developer workstations, demonstration harnesses, and initial operational deployments.

## 3. Background and Context
Modern web platforms often default to Kubernetes (K8s) or distributed cloud container clusters (EKS, GKE). However, for a focused prototype and MVP platform, Kubernetes introduces substantial operational friction: cluster maintenance, Helm chart complexity, ingress controllers, persistent volume claim overhead, and high minimum compute/memory costs that prevent local developer reproducibility.

The Project Overview ([DOC-002 Section 5](../../PROJECT_OVERVIEW.md)) and Nonfunctional Requirements Specification ([REQ-NFR-ABM-003](../../requirements/nonfunctional-requirements.md)) explicitly establish that Kubernetes is not part of the initial deployment strategy and that containerized execution must run cleanly on a single standard host.

## 4. Requirements and Constraints
* [REQ-NFR-ABM-003](../../requirements/nonfunctional-requirements.md) mandates containerized execution via Docker Compose and strictly excludes Kubernetes.
* [REQ-NFR-DCT-001](../../requirements/nonfunctional-requirements.md) mandates platform independence runnable on local developer hardware without cloud-provider lock-in.
* Deployment must be reproducible with a single declarative command (`docker compose up`).

## 5. Design or Specification (The Decision)

### Decision
We decide to standardize on **Docker Compose** as the exclusive container orchestration baseline for Pelagos.

The platform topology is declared in a root `docker-compose.yml` defining five isolated container services:
1. **`web`**: Static web server (Nginx / Vite preview) serving the repurposed God's Eye View client assets on host port 80/5173.
2. **`api`**: Python 3.11+ ASGI web application (FastAPI / Uvicorn) exposing REST and WebSocket endpoints on host port 8000.
3. **`worker`**: Python 3.11+ background worker executing scheduled data ingestion, batch validation, and rolling drift monitoring.
4. **`db`**: PostgreSQL 16 container with PostGIS 3.4 extensions, backed by persistent Docker volume `pgdata`.
5. **`cache`**: Redis 7 Alpine container for active geospatial vessel caching and pub/sub message brokering, backed by persistent volume `redis_data`.

### Environment and Network Configuration
* All containers share an internal bridge network (`pelagos-net`).
* Only `web` (HTTP port 80) and `api` (HTTP/WS port 8000) bind ports to the host interface; `db` and `cache` remain inaccessible from outside the Docker bridge network in production mode.
* Configuration is managed via declarative environment files (`.env`).

## 6. Dependencies and Interfaces
* **Prerequisites:** Docker Engine 24.0+ and Docker Compose v2.20+.
* **Cross-Container Networking:** DNS resolution via service names (`db:5432`, `cache:6379`, `api:8000`).

## 7. Failure Modes and Limitations
* **Single-Host Failure Boundary:** If the host machine fails, all containers stop. High availability (HA) multi-node failover is not provided in this baseline. This trade-off is accepted for MVP/prototyping phases.
* **Vertical Resource Limits:** All services compete for host RAM and CPU cores. Addressed by setting explicit resource limits (`deploy.resources.limits`) in `docker-compose.yml` to prevent the background worker from starving the web or database processes.

## 8. Security, Privacy and Licensing
* Official Docker base images (e.g., `python:3.11-slim`, `postgres:16-bookworm`, `redis:7-alpine`) are used to minimize attack surfaces.
* Database passwords and API keys are passed via `.env` files and never committed to Git.

## 9. Testing and Verification
* Automated CI workflow runs `docker compose config` to validate compose file syntax and tests end-to-end container startup (`docker compose up -d`) in ephemeral GitHub Actions runners.

## 10. Traceability
* **Upstream:** [PROJECT_OVERVIEW.md Section 5](../../PROJECT_OVERVIEW.md) (Confirmed Kubernetes exclusion).
* **Requirements:** Realizes [REQ-NFR-ABM-003](../../requirements/nonfunctional-requirements.md) (Containerized Deployment Capability & Kubernetes Exclusion).
* **Architecture:** Directly informs [ARCH-001 Section 5.6](../system-architecture.md).

## 11. Alternatives Considered
1. **Kubernetes (K8s) / K3s Deployment:**
   * *Rationale for Rejection:* Prohibited by confirmed project decisions. Introduces unnecessary YAML bloat, cluster maintenance overhead, and breaks zero-friction developer onboarding on standard laptops.
2. **Bare-Metal Multi-Process Execution (systemd / pm2):**
   * *Rationale for Rejection:* Fails to guarantee reproducible dependencies across differing operating systems (Windows vs Linux vs macOS), leading to "works on my machine" issues with PostgreSQL/PostGIS extensions.

## 12. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-10-14 | Project Agent | Initial draft of ADR-0003: Container Orchestration via Docker Compose under Milestone D3. Establishes compose baseline and confirms Kubernetes exclusion. Status: drafting. |
