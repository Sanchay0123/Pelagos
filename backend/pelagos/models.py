"""SQLAlchemy ORM models for operational vessel telemetry and quarantine isolation."""

import datetime
from sqlalchemy import Column, Integer, BigInteger, Float, String, DateTime, Text, Index, UniqueConstraint, Boolean, Date, ForeignKey
from pelagos.database import Base


class VesselPosition(Base):
    """
    Validated operational vessel observation.
    Adheres to REQ-FR-DII-002, REQ-FR-DII-003, and REQ-NFR-DIP-003.
    """
    __tablename__ = "vessel_positions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    mmsi = Column(String(32), index=True, nullable=False)
    imo = Column(String(32), index=True, nullable=True)
    vessel_name = Column(String(128), nullable=False)
    vessel_type = Column(String(64), nullable=False)
    vessel_type_code = Column(Integer, nullable=True)
    
    # Geographic coordinates (WGS 84 sea-surface observation)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    
    # Kinematics and dimensions
    speed_knots = Column(Float, nullable=True)
    course_degrees = Column(Float, nullable=True)
    heading_degrees = Column(Float, nullable=True)
    draft_meters = Column(Float, nullable=True)
    length_meters = Column(Float, nullable=True)
    width_meters = Column(Float, nullable=True)
    destination = Column(String(128), nullable=True)
    navigational_status = Column(String(64), nullable=True)
    
    # Multi-dimensional temporal tracking (UTC)
    observation_time = Column(DateTime(timezone=True), nullable=False, index=True)
    publication_time = Column(DateTime(timezone=True), nullable=True)
    ingestion_time = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    processing_time = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    
    # Source provenance & licensing
    source_name = Column(String(128), nullable=False)
    source_partition = Column(String(128), nullable=True, index=True)
    ingestion_run_id = Column(Integer, nullable=True, index=True)
    license_type = Column(String(64), nullable=False)
    raw_record_id = Column(String(128), nullable=True)
    
    # Deterministic idempotency key for deduplication (source:mmsi:observation_time)
    idempotency_key = Column(String(256), unique=True, index=True, nullable=False)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_vessel_positions_idempotency_key"),
        Index("idx_vessel_positions_lat_lon", "latitude", "longitude"),
        Index("idx_vessel_positions_obs_time", "observation_time"),
    )


class QuarantinedRecord(Base):
    """
    Isolated anomalous or rejected records.
    Adheres to REQ-FR-DII-004 and REQ-NFR-DIP-002.
    """
    __tablename__ = "quarantined_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    source_name = Column(String(128), nullable=False)
    source_partition = Column(String(128), nullable=True, index=True)
    ingestion_run_id = Column(Integer, nullable=True, index=True)
    mmsi = Column(String(32), index=True, nullable=True)
    raw_record_id = Column(String(128), nullable=True)
    error_code = Column(String(64), index=True, nullable=False)
    error_details = Column(Text, nullable=False)
    raw_payload = Column(Text, nullable=False)
    ingestion_time = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))


class IngestionRun(Base):
    """
    Auditable execution record for batch and partition ingestion.
    Records reconciliation counters for data integrity (REQ-NFR-ARG-003).
    """
    __tablename__ = "ingestion_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    run_type = Column(String(32), nullable=False)            # "bundled" | "remote"
    source_name = Column(String(128), nullable=False)
    source_partition = Column(String(128), nullable=False, index=True)
    raw_records_received = Column(Integer, nullable=True)
    records_read = Column(Integer, nullable=False, default=0)
    accepted_count = Column(Integer, nullable=False, default=0)
    quarantined_count = Column(Integer, nullable=False, default=0)
    duplicate_count = Column(Integer, nullable=False, default=0)
    unparseable_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False)              # "completed" | "partial" | "failed"
    error_details = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))


class ParquetExportTask(Base):
    """
    SQL-backed transactional outbox for dual-tier storage synchronization.
    Separates export-task scheduling lifecycle from analytical reader publication status.
    Adheres to ADR-0002, REQ-NFR-ABM-002, and REQ-NFR-ARG-003.
    """
    __tablename__ = "parquet_export_tasks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    partition_date = Column(String(32), unique=True, index=True, nullable=False)  # YYYY-MM-DD
    generation = Column(Integer, nullable=False, default=1)
    completed_generation = Column(Integer, nullable=True)

    # Export task scheduling lifecycle (outbox worker state)
    export_status = Column(String(32), nullable=False, default="pending", index=True)  # pending | in_progress | completed | failed

    # Analytical publication state (reader availability state)
    publication_status = Column(String(32), nullable=False, default="none", index=True)  # none | published | invalidated
    published_artifact_path = Column(String(512), nullable=True)
    published_generation = Column(Integer, nullable=True)
    published_sha256 = Column(String(64), nullable=True)
    published_record_count = Column(Integer, nullable=True)
    published_file_size = Column(BigInteger, nullable=True)

    # Worker lease & attempt fencing
    lease_worker_id = Column(String(64), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    active_attempt_uuid = Column(String(36), nullable=True)

    # Maintenance & cleanup mutual exclusion lock
    cleanup_locked_by = Column(String(64), nullable=True)
    cleanup_locked_expires_at = Column(DateTime(timezone=True), nullable=True)

    attempt_count = Column(Integer, nullable=False, default=0)
    priority = Column(Integer, nullable=False, default=0)
    error_details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_parquet_export_tasks_export_status", "export_status"),
        Index("idx_parquet_export_tasks_pub_status", "publication_status"),
        Index("idx_parquet_export_tasks_lease", "lease_worker_id", "lease_expires_at"),
    )


class FreightRoute(Base):
    """
    Standard commercial maritime trade route / corridor definition.
    Traces to REQ-FR-DII-005, REQ-FR-FRF-001, and DATA-001 Section 5.2.
    """
    __tablename__ = "freight_routes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    route_code = Column(String(64), unique=True, index=True, nullable=False)  # e.g. "USG-JPN-BULK", "PNW-JPN-BULK"
    route_name = Column(String(256), nullable=False)
    origin_region = Column(String(128), nullable=False)
    destination_region = Column(String(128), nullable=False)
    vessel_class = Column(String(64), nullable=True)  # e.g. "Panamax", "Capesize", "Supramax"
    cargo_type = Column(String(64), nullable=True)    # e.g. "Grain", "Coking Coal", "Dry Bulk"
    unit_of_measure = Column(String(32), nullable=False, default="USD/MT")
    currency = Column(String(8), nullable=False, default="USD")
    benchmark_distance_nm = Column(Float, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))


class FreightObservation(Base):
    """
    Canonical point-in-time freight market observation or assessed rate.
    Strictly preserves provenance, observation date, units, and derivation state.
    Traces to REQ-FR-DII-005, REQ-NFR-DIP-001, and REQ-NFR-DIP-003.
    """
    __tablename__ = "freight_observations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    route_code = Column(String(64), index=True, nullable=False)
    observation_date = Column(Date, index=True, nullable=False)
    rate_value = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default="USD")
    unit_of_measure = Column(String(32), nullable=False, default="USD/MT")
    is_derived = Column(Boolean, nullable=False, default=False)
    rate_high = Column(Float, nullable=True)
    rate_low = Column(Float, nullable=True)
    observation_period = Column(String(16), nullable=False, default="monthly")  # monthly | weekly | daily
    source_name = Column(String(128), nullable=False)
    source_dataset_id = Column(String(64), nullable=True)
    publication_time = Column(DateTime(timezone=True), nullable=True)
    ingestion_time = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    processing_time = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    license_type = Column(String(64), nullable=False, default="U.S. Public Domain")
    raw_payload = Column(Text, nullable=True)
    idempotency_key = Column(String(256), unique=True, index=True, nullable=False)
    ingestion_run_id = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("route_code", "observation_date", name="uq_freight_obs_route_date"),
        Index("idx_freight_obs_date", "observation_date"),
        Index("idx_freight_obs_route_code", "route_code"),
    )


class FreightPublication(Base):
    """
    Database-backed publication catalog for yearly freight Parquet partitions.
    Provides generation fencing, CAS publication safety, and reader isolation.
    Traces to ADR-0002 and REQ-NFR-ABM-002.
    """
    __tablename__ = "freight_publications"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    year = Column(Integer, unique=True, index=True, nullable=False)
    generation = Column(Integer, nullable=False, default=1)
    published_artifact_path = Column(String(512), nullable=False)
    published_sha256 = Column(String(64), nullable=False)
    record_count = Column(Integer, nullable=False)
    file_size_bytes = Column(BigInteger, nullable=False)
    snapshot_watermark_id = Column(Integer, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))


