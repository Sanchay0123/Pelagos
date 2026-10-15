---
title: "ADR-0002: Dual-Tier Storage Strategy"
document_id: ADR-0002
type: adr
status: accepted
version: 1.1.0
created: 2026-10-14
updated: 2026-10-15
tags:
  - architecture
  - adr
  - storage
  - database
  - postgresql
  - postgis
  - parquet
  - duckdb
---

# ADR-0002: Dual-Tier Storage Strategy

## 1. Purpose
This Architecture Decision Record (ADR) establishes the dual-tier storage strategy for Pelagos, defining the distinct operational and analytical responsibilities allocated to PostgreSQL (with PostGIS) and Apache Parquet.

## 2. Scope
This decision governs data storage, query patterns, indexing strategies, retention policies, and data lifecycle transitions between transactional operational data and historical analytical datasets.

## 3. Background and Context
Pelagos processes two radically different categories of data:
1. **Operational, Geospatial State:** Current vessel positions, dynamic port/canal restriction rules, active waypoint networks, user session state, and audit logs. These require low-latency ACID transactions, spatial indexing (R-Tree/GIST), and sub-second bounding box queries.
2. **High-Volume Analytical Time-Series:** Multi-year historical AIS trajectories (tens to hundreds of millions of coordinates), historical Baltic/Platts freight indices, and held-out model training partitions. These require high compression, fast columnar scans, and isolation from live ingestion workloads.

Storing all historical AIS points in standard relational PostgreSQL tables results in severe table bloat, slow index updates, and analytical query locks that degrade user-facing viewport rendering ([REQ-NFR-PER-001](../../requirements/nonfunctional-requirements.md)).

## 4. Requirements and Constraints
* [REQ-NFR-ABM-002](../../requirements/nonfunctional-requirements.md) mandates separation of transactional and analytical storage tiers.
* [REQ-NFR-DIP-003](../../requirements/nonfunctional-requirements.md) mandates multi-dimensional UTC temporal tracking and genuine source precision preservation.
* [REQ-NFR-ARG-002](../../requirements/nonfunctional-requirements.md) mandates fault and performance isolation between ingestion/analytical workloads and visual command rendering.
* Must run locally on a developer workstation without multi-node cloud data warehouse infrastructure.

## 5. Design or Specification (The Decision)

### Decision
We adopt a **Dual-Tier Storage Architecture** with an asynchronous SQL Transactional Outbox, immutable Parquet generation artifacts, and a local DuckDB analytical query layer:

#### Tier 1: Operational & Spatial Relational Store (SQLite / PostgreSQL 16 + PostGIS)
* **Technology:** SQLite (zero-configuration local default) and PostgreSQL 16 with PostGIS 3.4.
* **Role & Data Owned:**
  * Latest vessel positions (`vessel_positions`), active vessel particulars (`vessel_registry`).
  * Ingestion audit logs (`ingestion_runs`) and quarantined anomaly records (`quarantined_records`).
  * Asynchronous export outbox (`parquet_export_tasks`) coordinating daily partition exports with generation bump fencing, attempt UUID leasing, and cleanup mutual exclusion locks.
  * Port facilities, berths, and draft limits (`port_facilities`).
  * Navigational fairway routing graph nodes and edges (`fairway_network`).
  * Dynamic canal and chokepoint rules (`chokepoint_rules`).
  * Immutable optimization decision snapshots (`charter_audit_log`).
* **Query Patterns:** Primary-key lookups, spatial bounding box queries (`ST_Intersects`, `ST_DWithin`), relational joins, and ACID transactions.

#### Tier 2: Analytical & Historical Columnar Store (Apache Parquet + DuckDB)
* **Technology:** Apache Parquet with zstandard (zstd) / Snappy compression and PyArrow strict 26-field typed schema, queried via in-process DuckDB.
* **Role & Data Owned:**
  * Multi-year historical AIS trajectory points partitioned by `year=YYYY/month=MM/date=YYYY-MM-DD/`.
  * Immutable artifact naming: `partition_g{generation}_a{attempt_uuid}.parquet` written to staging `.tmp.{worker_id}` files and atomically renamed upon SHA-256 verification.
  * Historical freight fixture records and settled market index time series.
  * Out-of-sample held-out validation test partitions for ML model evaluation gates.
* **Query Patterns:** Bulk columnar scans, chronological sliding-window aggregations, out-of-sample ML feature extraction, and fast trajectory extraction via DuckDB.

### Lifecycle & Transfer Flow
1. **Live Ingestion:** Telemetry pings are validated and upserted into Tier 1 (`vessel_positions`) inside per-record savepoints using microsecond UTC idempotency keys.
2. **Outbox Staging:** Ingestion commits stage or bump pending export outbox tasks in `parquet_export_tasks` for affected partitions with generation fencing (`generation = generation + 1`).
3. **Worker Lease & Fencing:** Distributed or background export workers claim partitions via conditional CAS updates (`active_generation`, `active_attempt_uuid`, `worker_lease_expires_at`).
4. **Immutable Artifact Export:** Workers extract UTC half-open intervals `[start_dt, end_dt)` from SQL, write PyArrow tables to `.tmp.{worker_id}` files, compute SHA-256 checksums, verify row counts and byte sizes, and atomically rename to `partition_g{gen}_a{uuid}.parquet`.
5. **Atomic Publication CAS:** Successful exports atomically transition outbox status to `completed` and update `publication_status = 'published'`, updating `published_artifact_path`, `published_generation`, and `published_sha256`. Readers maintain continuous visibility to the prior verified published snapshot during exports.
6. **Reconciliation & Mutex Cleanup:** Periodic reconciliation claims an exclusive cleanup mutex lease (`cleanup_locked_by`, `cleanup_locked_at`), verifies destination state against SHA-256 hashes, reconciles tombstones, and cleans up superseded artifacts without deleting authoritative active files.

## 6. Dependencies and Interfaces
* **Database Drivers:** `SQLAlchemy 2.0`, `sqlite3`, `asyncpg`, `GeoAlchemy2`.
* **Analytical Engines & I/O:** `duckdb >= 1.0.0`, `pyarrow >= 14.0.0`.
* **Storage Location:** Configurable persistent storage roots (`parquet_data/telemetry/`).

## 7. Failure Modes and Limitations
* **Eventual Consistency Lag:** A delay exists between SQL ingestion and Parquet export. Analytical reader queries access the latest published snapshot via `published_artifact_path`.
* **Worker Lease Expiry / Stale Attempts:** Mitigated by strict attempt-UUID and generation fencing in all completion CAS and failure CAS updates; stale workers cannot mutate newer worker state.
* **Time-of-Check / Time-of-Use Cleanup Races:** Addressed by immutable generation/attempt filenames and exclusive cleanup mutex leases with strict pre- and post-action verification.

## 8. Security, Privacy and Licensing
* SQLite, PostgreSQL, PyArrow, and DuckDB carry permissive open-source licenses (MIT, PostgreSQL License, Apache 2.0).
* Tier 1 audit tables and Tier 2 Parquet artifacts are append-only / immutable to preserve regulatory integrity.

## 9. Testing and Verification
* **Ingestion Idempotency & Savepoint Eviction:** Verified across repeated ingestions and failed entity rollbacks without ORM session leakage.
* **Worker Claim & Completion CAS:** Verified against concurrent claims and stale worker attempts.
* **Generation Fencing:** Verified that concurrent ingestions bump generation and safely fence active worker completion.
* **Worker vs. Cleanup Mutual Exclusion:** Verified that workers cannot claim partitions during active cleanup and vice versa.
* **Recovery CAS Guardrail:** Verified that failure recovery updates fail if generation or attempt UUID has changed.
* **DuckDB Analytical Query Layer:** Verified summary KPI extraction and vessel track point queries against Parquet artifacts.

## 10. Traceability
* **Upstream:** [PROJECT_OVERVIEW.md Section 5](../../PROJECT_OVERVIEW.md).
* **Requirements:** Realizes [REQ-NFR-ABM-002](../../requirements/nonfunctional-requirements.md) (Storage Tier Separation) and [REQ-NFR-PER-002](../../requirements/nonfunctional-requirements.md) (Interactive Latency).
* **Related Specifications:** [ARCH-001 Section 5.3](../system-architecture.md), [DATA-001 Section 6](../../data/data-architecture.md).

## 11. Alternatives Considered
1. **Single Relational Database (All data in PostgreSQL):**
   * *Rationale for Rejection:* Storing 100M+ historical AIS points in standard PostgreSQL tables creates massive index bloat (tens of gigabytes), degrades vacuuming performance, and causes analytical queries to contend with live ingestion transactions.
2. **Pure Data Lake / NoSQL Document Store (e.g., MongoDB or Elasticsearch):**
   * *Rationale for Rejection:* Lacks native spatial network routing extensions (PostGIS `pgrouting`), lacks ACID compliance for commercial audit logs, and introduces unnecessary operational overhead.

## 12. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-10-14 | Project Agent | Initial draft of ADR-0002: Dual-Tier Storage Strategy under Milestone D3. Defines PostgreSQL/PostGIS and Apache Parquet roles and boundaries. Status: drafting. |
| 1.1.0 | 2026-10-15 | Project Agent | D6 Implementation: Adopted dual-tier architecture with SQL transactional outbox, immutable generation Parquet artifacts, attempt-UUID fencing, cleanup mutex leases, DuckDB analytics, and recovery CAS guardrails. Status: accepted. |
