---
title: "ADR-0002: Dual-Tier Storage Strategy"
document_id: ADR-0002
type: adr
status: drafting
version: 1.0.0
created: 2026-10-14
updated: 2026-10-14
tags:
  - architecture
  - adr
  - storage
  - database
  - postgresql
  - postgis
  - parquet
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
We decide to adopt a **Dual-Tier Storage Architecture**:

#### Tier 1: Operational & Spatial Relational Store (PostgreSQL 16 + PostGIS)
* **Technology:** PostgreSQL 16 with PostGIS 3.4 extension.
* **Role & Data Owned:**
  * Latest vessel positions (`vessel_positions`), active vessel particulars (`vessel_registry`).
  * Port facilities, berths, and draft limits (`port_facilities`).
  * Navigational fairway routing graph nodes and edges (`fairway_network`).
  * Dynamic canal and chokepoint rules (`chokepoint_rules`).
  * Immutable optimization decision snapshots (`charter_audit_log`).
* **Query Patterns:** Primary-key lookups, spatial bounding box queries (`ST_Intersects`, `ST_DWithin`), relational joins, and ACID transactions.

#### Tier 2: Analytical & Historical Columnar Store (Apache Parquet)
* **Technology:** Apache Parquet with zstandard (zstd) / Snappy compression, queried via DuckDB or Polars.
* **Role & Data Owned:**
  * Multi-year historical AIS trajectory points partitioned by `year=YYYY/month=MM/vessel_class=CC/`.
  * Historical freight fixture records and settled market index time series.
  * Out-of-sample held-out validation test partitions for ML model evaluation gates.
* **Query Patterns:** Bulk columnar scans, chronological sliding-window aggregations, out-of-sample ML feature extraction.

### Lifecycle & Transfer Flow
Live AIS pings are validated and upserted into Tier 1 (`vessel_positions`) for immediate 3D visualization. Periodically (e.g., hourly or upon message batch closure), raw validated trajectories are flushed to immutable Parquet partition files in Tier 2. Old position history in Tier 1 is pruned beyond a 7-day rolling window, keeping PostgreSQL lean and performant.

## 6. Dependencies and Interfaces
* **PostgreSQL Drivers:** `asyncpg`, `GeoAlchemy2`, `SQLAlchemy 2.0`.
* **Analytical Engines:** `DuckDB`, `Polars`, `pyarrow`.
* **Storage Location:** Persistent Docker volumes (`pgdata`, `parquet_data`).

## 7. Failure Modes and Limitations
* **Eventual Consistency Lag:** A delay of several minutes may exist before newly ingested AIS points become available in historical Parquet analytical partitions. This is acceptable because ML forecasting and baseline model evaluations operate on historical held-out splits rather than real-time second-by-second positions.
* **File System Fragmentation:** Too many tiny Parquet files degrade scan performance. Mitigated by buffering raw batches and writing compacted chunks of &ge; 50,000 rows.

## 8. Security, Privacy and Licensing
* Both PostgreSQL/PostGIS and Apache Parquet (via pyarrow/DuckDB) carry permissive open-source licenses (PostgreSQL License, Apache 2.0).
* Tier 1 audit tables are append-only to preserve legal integrity.

## 9. Testing and Verification
* Automated integration tests verify that live spatial queries complete in < 50ms on PostGIS while 10-million-row analytical aggregations complete in < 500ms in DuckDB on Parquet files without database connection contention.

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
