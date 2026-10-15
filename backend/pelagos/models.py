"""SQLAlchemy ORM models for operational vessel telemetry and quarantine isolation."""

import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, Text, Index
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
