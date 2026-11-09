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
    
    # Audit-grade reconciliation fields (C1.1)
    is_reconstructed = Column(Boolean, nullable=False, default=False, server_default="0", index=True)
    original_rate_value = Column(Float, nullable=True)
    reconciliation_method = Column(String(64), nullable=True)
    reconciliation_metadata = Column(Text, nullable=True)

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


class Port(Base):
    """
    Authoritative maritime port directory entity.
    Traces to REQ-FR-CRI-012, OD-ROUT-06-03, and DATA-001 Section 5.2.
    """
    __tablename__ = "ports"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    unlocode = Column(String(5), unique=True, index=True, nullable=False)
    port_id = Column(String(32), unique=True, index=True, nullable=False)
    source_id = Column(String(128), nullable=False)
    name = Column(String(128), index=True, nullable=False)
    country = Column(String(64), index=True, nullable=False)
    country_code = Column(String(2), index=True, nullable=False)
    subdivision = Column(String(8), nullable=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    crs = Column(String(16), nullable=False, default="EPSG:4326")
    coordinate_type = Column(String(64), nullable=False, default="port_point")
    port_region = Column(String(64), index=True, nullable=True)
    is_monitored_corridor_eligible = Column(Boolean, nullable=False, default=False)
    source_url = Column(String(256), nullable=True)
    provenance = Column(Text, nullable=True)
    license = Column(String(128), nullable=False, default="Public Domain / Open Data")
    access_limitations = Column(Text, nullable=True)
    nearest_graph_node = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    __table_args__ = (
        Index("idx_ports_lat_lon", "latitude", "longitude"),
        Index("idx_ports_region", "port_region"),
    )


class PortAlias(Base):
    """
    Alternative naming, terminal designations, and local identifiers for ports.
    Facilitates deterministic entity resolution and search disambiguation.
    """
    __tablename__ = "port_aliases"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    port_unlocode = Column(String(5), ForeignKey("ports.unlocode", ondelete="CASCADE"), index=True, nullable=False)
    alias_name = Column(String(128), index=True, nullable=False)
    alias_type = Column(String(32), nullable=False, default="alternative_name")  # historical_name | local_name | terminal_name | alternative_name | code_alias
    source_name = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    __table_args__ = (
        UniqueConstraint("port_unlocode", "alias_name", name="uq_port_alias_unlocode_name"),
    )


class Vessel(Base):
    """
    Canonical maritime vessel registry and static characteristics entity.
    Traces to REQ-FR-DII-002 and REQ-FR-OMR-002.
    """
    __tablename__ = "vessels"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    mmsi = Column(String(32), unique=True, index=True, nullable=False)
    imo = Column(String(32), index=True, nullable=True)
    vessel_name = Column(String(128), index=True, nullable=False)
    vessel_type = Column(String(64), nullable=False)
    vessel_type_code = Column(Integer, nullable=True)
    vessel_class = Column(String(64), nullable=True)  # Panamax | Supramax | Handymax | Capesize
    dwt_tonnage = Column(Float, nullable=True)
    gross_tonnage = Column(Float, nullable=True)
    length_meters = Column(Float, nullable=True)
    width_meters = Column(Float, nullable=True)
    draft_max_meters = Column(Float, nullable=True)
    built_year = Column(Integer, nullable=True)
    flag_country = Column(String(64), nullable=True)
    flag_code = Column(String(8), nullable=True)
    source_name = Column(String(128), nullable=False)
    commercial_status = Column(String(32), nullable=False, default="unknown")  # active | laid_up | unknown
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))


class TradeObservation(Base):
    """
    Point-in-time bilateral maritime trade volume and value observation.
    Traces to official foreign trade statistics (USDA FAS GATS, UN Comtrade, Indian DGCI&S).
    """
    __tablename__ = "trade_observations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    reporter_country = Column(String(64), index=True, nullable=False)
    reporter_iso2 = Column(String(2), index=True, nullable=False)
    partner_country = Column(String(64), index=True, nullable=False)
    partner_iso2 = Column(String(2), index=True, nullable=False)
    commodity_code = Column(String(32), index=True, nullable=False)  # HS 6-digit code or commodity group
    commodity_name = Column(String(128), nullable=False)
    trade_flow = Column(String(32), nullable=False)  # Export | Import
    period_date = Column(Date, index=True, nullable=False)
    period_type = Column(String(16), nullable=False, default="monthly")
    volume_metric_tons = Column(Float, nullable=True)
    trade_value_usd = Column(Float, nullable=True)
    unit_price_usd_per_mt = Column(Float, nullable=True)
    source_name = Column(String(128), nullable=False)
    license_type = Column(String(64), nullable=False, default="Public Domain / Open Government Data")
    idempotency_key = Column(String(256), unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_trade_obs_idempotency_key"),
        Index("idx_trade_obs_reporter_partner", "reporter_iso2", "partner_iso2", "commodity_code"),
        Index("idx_trade_obs_period", "period_date"),
    )


class EnvironmentalObservation(Base):
    """
    Auditable oceanographic buoy and marine meteorological observation.
    Traces to NOAA NDBC, INCOIS, and official hydro-oceanographic buoy networks.
    """
    __tablename__ = "environmental_observations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    station_id = Column(String(32), index=True, nullable=False)
    station_name = Column(String(128), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    sea_area = Column(String(64), index=True, nullable=False)
    observation_time = Column(DateTime(timezone=True), index=True, nullable=False)
    retrieval_time_utc = Column(DateTime(timezone=True), nullable=True)
    wave_height_meters = Column(Float, nullable=True)
    wave_period_seconds = Column(Float, nullable=True)
    wind_speed_knots = Column(Float, nullable=True)
    wind_direction_degrees = Column(Float, nullable=True)
    sea_surface_temp_c = Column(Float, nullable=True)
    air_pressure_hpa = Column(Float, nullable=True)
    source_name = Column(String(128), nullable=False)
    license_type = Column(String(64), nullable=False, default="Public Domain / Open Government Data")
    quality_status = Column(String(32), nullable=False, default="verified")  # verified | provisional | flagged
    idempotency_key = Column(String(256), unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_env_obs_idempotency_key"),
        Index("idx_env_obs_station_time", "station_id", "observation_time"),
        Index("idx_env_obs_sea_area", "sea_area"),
    )


class BunkerPriceObservation(Base):
    """
    Auditable marine bunker fuel price observation.
    Traces to Bunker Fuel Prices (bunkerfuelprices.com) and commercial marine fuel benchmarks.
    Strictly preserves genuine provider observations, timestamps, delivery basis, and pricing type.
    """
    __tablename__ = "bunker_price_observations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    port_code = Column(String(16), index=True, nullable=False)        # UN/LOCODE e.g. "ARBUE", "DKSKA", "USSEA"
    port_name = Column(String(128), nullable=False)                  # Port name e.g. "Buenos Aires", "Skagen"
    country_code = Column(String(8), nullable=True)                  # e.g. "AR", "DK", "US"
    fuel_grade = Column(String(32), index=True, nullable=False)      # e.g. "VLSFO", "HSFO", "MGO"
    price_value = Column(Float, nullable=False)                      # Numeric price in USD/MT
    currency = Column(String(8), nullable=False, default="USD")
    unit = Column(String(16), nullable=False, default="USD/MT")
    pricing_type = Column(String(32), nullable=False, default="indicative")  # indicative | transacted | supplier_submission | derived
    delivery_basis = Column(String(32), nullable=True)               # delivered | ex_wharf | fob
    source_name = Column(String(128), nullable=False)                # Provider name
    source_feed = Column(String(256), nullable=True)                 # Feed id / ticker
    observation_time = Column(DateTime(timezone=True), index=True, nullable=False)
    retrieval_time_utc = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    provenance_notes = Column(Text, nullable=True)
    idempotency_key = Column(String(256), unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_bunker_obs_idempotency_key"),
        Index("idx_bunker_obs_port_grade", "port_code", "fuel_grade"),
        Index("idx_bunker_obs_time", "observation_time"),
    )
