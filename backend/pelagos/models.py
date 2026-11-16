"""SQLAlchemy ORM models for operational vessel telemetry and quarantine isolation."""

import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    Float,
    String,
    DateTime,
    Text,
    Index,
    UniqueConstraint,
    Boolean,
    Date,
    ForeignKey,
)
from sqlalchemy.orm import relationship, synonym
from pelagos.database import Base


class VerificationStatus:
    """Standardized verification status for maritime entity particulars (Stage F1.2)."""
    DIRECTLY_VERIFIED = "DIRECTLY_VERIFIED"
    SOURCE_REPORTED = "SOURCE_REPORTED"
    INFERRED = "INFERRED"
    UNVERIFIED = "UNVERIFIED"
    CONFLICTING = "CONFLICTING"
    UNAVAILABLE = "UNAVAILABLE"

    ALL = {
        DIRECTLY_VERIFIED,
        SOURCE_REPORTED,
        INFERRED,
        UNVERIFIED,
        CONFLICTING,
        UNAVAILABLE,
    }


class CurveApplicabilityScope:
    """Applicability scope of an engine SFOC curve (Stage F1.4.2.2b)."""
    EXACT_VARIANT = "EXACT_VARIANT"
    FAMILY_REFERENCE = "FAMILY_REFERENCE"
    AGGREGATE_RESEARCH = "AGGREGATE_RESEARCH"
    UNAVAILABLE = "UNAVAILABLE"

    ALL = {
        EXACT_VARIANT,
        FAMILY_REFERENCE,
        AGGREGATE_RESEARCH,
        UNAVAILABLE,
    }


class SourceLicensingStatus:
    """Licensing and accessibility status for technical machinery documentation (Stage F1.4.2.2b)."""
    OPEN_OPERATIONAL = "OPEN_OPERATIONAL"
    OPEN_RESEARCH_ONLY = "OPEN_RESEARCH_ONLY"
    REGISTRATION_REQUIRED = "REGISTRATION_REQUIRED"
    LICENSE_RESTRICTED = "LICENSE_RESTRICTED"
    AGGREGATE_ONLY = "AGGREGATE_ONLY"
    NOT_ACCESSIBLE = "NOT_ACCESSIBLE"
    UNVERIFIED = "UNVERIFIED"

    ALL = {
        OPEN_OPERATIONAL,
        OPEN_RESEARCH_ONLY,
        REGISTRATION_REQUIRED,
        LICENSE_RESTRICTED,
        AGGREGATE_ONLY,
        NOT_ACCESSIBLE,
        UNVERIFIED,
    }


class SfocPermittedUse:
    """Permitted operational and research use classifications for SFOC data (Stage F1.4.2.2b.1)."""
    OPERATIONAL_PERMITTED = "OPERATIONAL_PERMITTED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    RESTRICTED_PENDING_PERMISSION = "RESTRICTED_PENDING_PERMISSION"
    AGGREGATE_RESEARCH_ONLY = "AGGREGATE_RESEARCH_ONLY"

    ALL = {
        OPERATIONAL_PERMITTED,
        RESEARCH_ONLY,
        RESTRICTED_PENDING_PERMISSION,
        AGGREGATE_RESEARCH_ONLY,
    }


def validate_imo(imo: Optional[Union[str, int]]) -> Tuple[bool, Optional[str], Optional[str]]:
    """Validate a 7-digit maritime IMO ship identification number against the modulo-10 algorithm.

    Returns:
        Tuple[bool, Optional[str], Optional[str]]: (is_valid, cleaned_7_digit_str, error_message)
    """
    if imo is None:
        return True, None, None
    s = str(imo).strip().upper()
    if not s:
        return True, None, None
    if s.startswith("IMO"):
        s = s[3:].strip()
    if len(s) != 7 or not s.isdigit():
        return False, None, f"IMO number must be exactly 7 decimal digits, got '{imo}'"
    digits = [int(c) for c in s]
    chk = sum(d * (8 - i) for i, d in enumerate(digits[:6], 1)) % 10
    if chk != digits[6]:
        return False, None, f"Invalid IMO check digit: expected {chk}, got {digits[6]} in '{imo}'"
    return True, s, None


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

    # Port restriction and hydrography hierarchy relationships (Stage F1.3.2)
    terminals = relationship("Terminal", back_populates="port", cascade="all, delete-orphan", foreign_keys="[Terminal.port_id]")
    channel_reaches = relationship("ChannelReach", back_populates="port", cascade="all, delete-orphan", foreign_keys="[ChannelReach.port_id]")
    restrictions = relationship("NavigationalRestriction", back_populates="port", cascade="all, delete-orphan", foreign_keys="[NavigationalRestriction.port_id]")
    crosswalks = relationship("PortCrosswalk", back_populates="port", cascade="all, delete-orphan", foreign_keys="[PortCrosswalk.canonical_unlocode]")

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


class RestrictionType:
    """Standardized operational and hydrographic restriction categories."""
    DRAFT_MAX = "DRAFT_MAX"
    LOA_MAX = "LOA_MAX"
    BEAM_MAX = "BEAM_MAX"
    AIR_DRAFT_MAX = "AIR_DRAFT_MAX"
    AIR_GAP_LIVE = "AIR_GAP_LIVE"
    AIR_DRAFT_UNCONSTRAINED = "AIR_DRAFT_UNCONSTRAINED"
    UKC_MIN = "UKC_MIN"
    TIDAL_WINDOW = "TIDAL_WINDOW"
    SPEED_MAX = "SPEED_MAX"
    DAYLIGHT_ONLY = "DAYLIGHT_ONLY"


class RestrictionScope:
    """Scope of navigational applicability."""
    PORT = "PORT"
    TERMINAL = "TERMINAL"
    BERTH = "BERTH"
    CHANNEL_REACH = "CHANNEL_REACH"
    BRIDGE = "BRIDGE"
    CANAL_TRANSIT = "CANAL_TRANSIT"


class FreshnessPolicy:
    """Domain-specific validity and audit horizons."""
    STATIC_INFRASTRUCTURE = "STATIC_INFRASTRUCTURE"      # e.g. wharf dimensions, deck elevation (1825 days / 5 years)
    HYDROGRAPHIC_SURVEY = "HYDROGRAPHIC_SURVEY"          # e.g. certified berth pocket depth (365 days)
    MAINTAINED_CHANNEL_PRISM = "MAINTAINED_CHANNEL_PRISM"# e.g. USACE authorized/maintained depths (365 days)
    PILOTAGE_ADVISORY = "PILOTAGE_ADVISORY"              # e.g. bar pilots draft circular (90 days)
    DAILY_FORECAST = "DAILY_FORECAST"                    # e.g. river draft forecast (30 days)
    LIVE_SENSOR = "LIVE_SENSOR"                          # e.g. NOAA PORTS 6-min air gap (2 hours)


class Terminal(Base):
    """
    Maritime terminal or navigation facility entity within a port.
    Represents an industrial dock complex, marine terminal, or USACE navigation facility.
    Traces to REQ-FR-CRI-012 and Stage F1.3.2.
    """
    __tablename__ = "terminals"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    port_id = Column(Integer, ForeignKey("ports.id", ondelete="CASCADE"), nullable=False, index=True)
    port_unlocode = Column(String(5), ForeignKey("ports.unlocode", ondelete="CASCADE"), nullable=False, index=True)
    terminal_code = Column(String(64), nullable=True, index=True)  # e.g. USACE NAV_UNIT_ID ('0YZ3', '0T84')
    name = Column(String(256), nullable=False, index=True)
    facility_type = Column(String(64), nullable=True)
    operator_name = Column(String(256), nullable=True)
    owner_name = Column(String(256), nullable=True)
    waterway_name = Column(String(256), nullable=True)
    river_mile = Column(Float, nullable=True)
    bank = Column(String(16), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    primary_commodities = Column(Text, nullable=True)
    handling_equipment = Column(Text, nullable=True)
    rail_access = Column(String(128), nullable=True)
    highway_access = Column(String(128), nullable=True)
    source_system = Column(String(64), nullable=False)  # USACE_MASTER_DOCKS | PORT_AUTHORITY_GAZETTE | MANUAL_CURATED
    source_record_id = Column(String(128), nullable=True)  # e.g. OBJECTID or LOC_DOCK
    raw_attributes_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    port = relationship("Port", back_populates="terminals", foreign_keys=[port_id])
    berths = relationship("Berth", back_populates="terminal", cascade="all, delete-orphan", foreign_keys="[Berth.terminal_id]")
    restrictions = relationship("NavigationalRestriction", back_populates="terminal", foreign_keys="[NavigationalRestriction.terminal_id]")

    __table_args__ = (
        Index("idx_terminals_port_code", "port_unlocode", "terminal_code"),
        Index("idx_terminals_name", "name"),
    )


class Berth(Base):
    """
    Individual berth or discrete berthing face within a terminal/facility.
    Stores certified alongside depths, berthing length, and vessel dimensional limits.
    Traces to Stage F1.3.2.
    """
    __tablename__ = "berths"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    terminal_id = Column(Integer, ForeignKey("terminals.id", ondelete="CASCADE"), nullable=False, index=True)
    port_unlocode = Column(String(5), ForeignKey("ports.unlocode", ondelete="CASCADE"), nullable=False, index=True)
    berth_code = Column(String(64), nullable=True, index=True)
    berth_name = Column(String(256), nullable=False, index=True)
    berthing_length_meters = Column(Float, nullable=True)
    berthing_length_source_value = Column(Float, nullable=True)
    alongside_depth_min_meters = Column(Float, nullable=True)
    alongside_depth_max_meters = Column(Float, nullable=True)
    depth_source_unit = Column(String(16), nullable=True)  # feet | meters
    depth_source_value_min = Column(String(32), nullable=True)
    depth_source_value_max = Column(String(32), nullable=True)
    vertical_datum = Column(String(64), nullable=True)  # MLLW | MLG | MLT | LAT | CD
    deck_height_min_meters = Column(Float, nullable=True)
    deck_height_max_meters = Column(Float, nullable=True)
    max_vessel_loa_meters = Column(Float, nullable=True)
    max_vessel_beam_meters = Column(Float, nullable=True)
    max_vessel_draft_meters = Column(Float, nullable=True)
    source_record_id = Column(String(128), nullable=True)  # e.g. USACE Master Docks OBJECTID
    survey_date = Column(DateTime(timezone=True), nullable=True)
    is_conflicting = Column(Boolean, nullable=False, default=False)
    conflict_details = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    terminal = relationship("Terminal", back_populates="berths", foreign_keys=[terminal_id])
    restrictions = relationship("NavigationalRestriction", back_populates="berth", foreign_keys="[NavigationalRestriction.berth_id]")

    __table_args__ = (
        Index("idx_berths_terminal_name", "terminal_id", "berth_name"),
        Index("idx_berths_unlocode", "port_unlocode"),
    )


class ChannelReach(Base):
    """
    Defined navigation reach, fairway segment, or channel prism in a port approach corridor.
    Stores nominal maintained and authorized project depths with river mile markers.
    Traces to USACE National Channel Framework and Stage F1.3.2.
    """
    __tablename__ = "channel_reaches"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    port_id = Column(Integer, ForeignKey("ports.id", ondelete="CASCADE"), nullable=False, index=True)
    port_unlocode = Column(String(5), ForeignKey("ports.unlocode", ondelete="CASCADE"), nullable=False, index=True)
    reach_code = Column(String(64), nullable=True, index=True)
    reach_name = Column(String(256), nullable=False, index=True)
    channel_name = Column(String(256), nullable=True)
    depth_maintained_meters = Column(Float, nullable=True)
    depth_authorized_meters = Column(Float, nullable=True)
    depth_source_unit = Column(String(16), nullable=True)  # feet | meters
    depth_maintained_source_value = Column(Float, nullable=True)
    depth_authorized_source_value = Column(Float, nullable=True)
    width_meters = Column(Float, nullable=True)
    width_source_value = Column(Float, nullable=True)
    mile_start = Column(Float, nullable=True)
    mile_end = Column(Float, nullable=True)
    vertical_datum = Column(String(64), nullable=True)
    responsible_district = Column(String(64), nullable=True)  # CEMVN | CENWP | CESWG | CENWS
    source_system = Column(String(64), nullable=False)  # USACE_NCF | PORT_AUTHORITY_GAZETTE
    source_record_id = Column(String(128), nullable=True)  # OBJECTID in NCF
    reach_category = Column(String(64), nullable=True)
    reach_type = Column(String(64), nullable=True)
    geometry_geojson = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    port = relationship("Port", back_populates="channel_reaches", foreign_keys=[port_id])
    restrictions = relationship("NavigationalRestriction", back_populates="channel_reach", foreign_keys="[NavigationalRestriction.channel_reach_id]")

    __table_args__ = (
        Index("idx_channel_reaches_port_reach", "port_unlocode", "reach_name"),
        Index("idx_channel_reaches_district", "responsible_district"),
    )


class NavigationalRestriction(Base):
    """
    Specific operational restriction, bridge clearance, air gap, tidal window, or draft rule.
    Full provenance preservation: unit, datum, authority, validity, and freshness policy.
    Traces to Stage F1.3.2 and REQ-NFR-DIP-001.
    """
    __tablename__ = "navigational_restrictions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    port_id = Column(Integer, ForeignKey("ports.id", ondelete="CASCADE"), nullable=False, index=True)
    port_unlocode = Column(String(5), ForeignKey("ports.unlocode", ondelete="CASCADE"), nullable=False, index=True)
    terminal_id = Column(Integer, ForeignKey("terminals.id", ondelete="SET NULL"), nullable=True, index=True)
    berth_id = Column(Integer, ForeignKey("berths.id", ondelete="SET NULL"), nullable=True, index=True)
    channel_reach_id = Column(Integer, ForeignKey("channel_reaches.id", ondelete="SET NULL"), nullable=True, index=True)
    restriction_type = Column(String(64), nullable=False, index=True)  # DRAFT_MAX | LOA_MAX | BEAM_MAX | AIR_DRAFT_MAX | AIR_GAP_LIVE | UKC_MIN | TIDAL_WINDOW
    scope_type = Column(String(32), nullable=False)  # PORT | TERMINAL | BERTH | CHANNEL_REACH | BRIDGE
    scope_name = Column(String(256), nullable=False)
    numeric_limit = Column(Float, nullable=True)  # Normalized metric value (meters, knots, etc.)
    source_value = Column(String(64), nullable=True)  # Original raw string or number
    unit = Column(String(32), nullable=True)  # meters | feet | knots | percent | hours
    vertical_datum = Column(String(64), nullable=True)  # MLLW | MLG | MLT | LAT | CD | MHW
    water_density = Column(String(32), nullable=True)  # FRESH | BRACKISH | SALT | DOCK_WATER
    operating_conditions = Column(Text, nullable=True)
    is_conditional = Column(Boolean, nullable=False, default=False)
    is_temporary = Column(Boolean, nullable=False, default=False)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    freshness_policy = Column(String(64), nullable=False, default=FreshnessPolicy.STATIC_INFRASTRUCTURE)
    survey_date = Column(DateTime(timezone=True), nullable=True)
    source_publication_date = Column(DateTime(timezone=True), nullable=True)
    is_conflicting = Column(Boolean, nullable=False, default=False)
    conflict_details = Column(Text, nullable=True)
    source_authority = Column(String(128), nullable=False)
    source_url = Column(String(512), nullable=True)
    source_publication_reference = Column(String(256), nullable=True)
    provenance_id = Column(String(128), nullable=True)
    verification_status = Column(String(32), nullable=False, default=VerificationStatus.DIRECTLY_VERIFIED)
    retrieval_timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    observation_timestamp = Column(DateTime(timezone=True), nullable=True)
    qc_flags = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    port = relationship("Port", back_populates="restrictions", foreign_keys=[port_id])
    terminal = relationship("Terminal", back_populates="restrictions", foreign_keys=[terminal_id])
    berth = relationship("Berth", back_populates="restrictions", foreign_keys=[berth_id])
    channel_reach = relationship("ChannelReach", back_populates="restrictions", foreign_keys=[channel_reach_id])

    __table_args__ = (
        Index("idx_nav_rest_port_type", "port_unlocode", "restriction_type"),
        Index("idx_nav_rest_scope", "scope_type", "scope_name"),
    )


class PortCrosswalk(Base):
    """
    Deterministic crosswalk mapping between external source identifiers/names and Pelagos canonical ports.
    Tracks match method and confidence score.
    Traces to Stage F1.3.2.
    """
    __tablename__ = "port_crosswalks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    source_system = Column(String(64), nullable=False, index=True)  # USACE_DOCKS | USACE_NCF | NOAA_PORTS | NGA_WPI
    source_identifier = Column(String(128), nullable=False, index=True)  # e.g. 'KAM', 'TIW', 'GLS', 'LOG', '8762002'
    source_port_name = Column(String(256), nullable=True)
    canonical_unlocode = Column(String(5), ForeignKey("ports.unlocode", ondelete="CASCADE"), nullable=False, index=True)
    match_method = Column(String(64), nullable=False)  # EXPLICIT_VERIFIED | EXACT_NAME | SPATIAL_BUFFER
    confidence_score = Column(Float, nullable=False, default=1.0)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    port = relationship("Port", back_populates="crosswalks", foreign_keys=[canonical_unlocode])

    __table_args__ = (
        UniqueConstraint("source_system", "source_identifier", "canonical_unlocode", name="uq_port_crosswalk_src_id_unlocode"),
        Index("idx_port_crosswalk_source", "source_system", "source_identifier"),
    )


class Vessel(Base):
    """
    Canonical maritime vessel registry and static characteristics entity.
    Traces to REQ-FR-DII-002, REQ-FR-OMR-002, and Pelagos Stage F1.2.
    """
    __tablename__ = "vessels"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    vessel_id = synonym("id")  # Stable internal surrogate key alias

    # Secondary tracking identifier (temporally mutable on re-flagging)
    mmsi = Column(String(32), unique=True, index=True, nullable=False)

    # Canonical lifetime identifier (immutable under IMO Assembly Resolution A.1078(28))
    imo = Column(String(32), index=True, nullable=True)

    vessel_name = Column(String(128), index=True, nullable=False)
    vessel_type = Column(String(64), nullable=False)
    vessel_type_code = Column(Integer, nullable=True)
    vessel_class = Column(String(64), nullable=True)  # Panamax | Supramax | Handymax | Capesize
    dwt_tonnage = Column(Float, nullable=True)
    gross_tonnage = Column(Float, nullable=True)
    net_tonnage = Column(Float, nullable=True)
    length_meters = Column(Float, nullable=True)
    width_meters = Column(Float, nullable=True)
    draft_max_meters = Column(Float, nullable=True)
    air_draft_meters = Column(Float, nullable=True)
    grain_cubic_meters = Column(Float, nullable=True)
    holds_count = Column(Integer, nullable=True)
    hatches_count = Column(Integer, nullable=True)
    built_year = Column(Integer, nullable=True)
    flag_country = Column(String(64), nullable=True)
    flag_code = Column(String(8), nullable=True)
    source_name = Column(String(128), nullable=False)
    commercial_status = Column(String(32), nullable=False, default="unknown")  # active | laid_up | unknown

    # Stage F1.4.2.2a: Verified Machinery & Cargo Gear Particulars
    engine_manufacturer = Column(String(128), nullable=True)
    engine_model = Column(String(128), nullable=True)
    engine_variant = Column(String(128), nullable=True)
    engine_builder = Column(String(128), nullable=True)
    cylinder_count = Column(Integer, nullable=True)
    bore_mm = Column(Float, nullable=True)
    stroke_mm = Column(Float, nullable=True)
    rated_power_kw = Column(Float, nullable=True)
    rated_rpm = Column(Float, nullable=True)
    is_geared = Column(Boolean, nullable=True)
    crane_count = Column(Integer, nullable=True)
    crane_swl_tonnes = Column(Float, nullable=True)

    # Synonyms for backward compatibility and plural conventions
    cylinders_count = synonym("cylinder_count")
    cranes_count = synonym("crane_count")

    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    # Relationships to temporal identity, provenance, and operational models
    mmsi_history = relationship("VesselMmsiHistory", back_populates="vessel", cascade="all, delete-orphan", order_by="desc(VesselMmsiHistory.valid_from)")
    provenance_records = relationship("VesselAttributeProvenance", back_populates="vessel", cascade="all, delete-orphan", order_by="desc(VesselAttributeProvenance.retrieved_at)")
    operational_estimates = relationship("VesselOperationalEstimates", back_populates="vessel", cascade="all, delete-orphan", order_by="desc(VesselOperationalEstimates.computed_at)")
    hydrostatics = relationship("VesselHydrostatics", back_populates="vessel", cascade="all, delete-orphan", uselist=False)

    __table_args__ = (
        Index("idx_vessels_imo_unique", "imo", unique=True),
        Index("idx_vessels_name", "vessel_name"),
        Index("idx_vessels_class", "vessel_class"),
    )


class VesselMmsiHistory(Base):
    """
    Historical and temporal MMSI associations for maritime vessels.
    Traces to Pelagos Stage F1.2 (REQ-FR-DII-002, REQ-FR-OMR-002).
    """
    __tablename__ = "vessel_mmsi_history"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    vessel_id = Column(Integer, ForeignKey("vessels.id", ondelete="CASCADE"), index=True, nullable=False)
    mmsi = Column(String(32), index=True, nullable=False)
    flag_country = Column(String(64), nullable=True)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    source_name = Column(String(128), nullable=False)
    source_record_id = Column(String(128), nullable=True)
    retrieved_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    vessel = relationship("Vessel", back_populates="mmsi_history")

    __table_args__ = (
        Index("idx_vessel_mmsi_hist", "vessel_id", "mmsi"),
    )


class VesselAttributeProvenance(Base):
    """
    Field-level provenance and verification tracking for vessel particulars.
    Traces to Pelagos Stage F1.2 and REQ-NFR-DIP-001.
    """
    __tablename__ = "vessel_attribute_provenance"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    vessel_id = Column(Integer, ForeignKey("vessels.id", ondelete="CASCADE"), index=True, nullable=False)
    attribute_name = Column(String(64), index=True, nullable=False)
    raw_value = Column(Text, nullable=True)
    normalized_value = Column(Text, nullable=True)
    source_name = Column(String(128), nullable=False)
    source_record_id = Column(String(128), nullable=True)
    source_url = Column(String(512), nullable=True)
    verification_status = Column(String(32), nullable=False, default=VerificationStatus.UNVERIFIED)
    observation_date = Column(DateTime(timezone=True), nullable=True)
    retrieved_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    is_conflicting = Column(Boolean, nullable=False, default=False)
    normalization_method = Column(String(128), nullable=True)

    vessel = relationship("Vessel", back_populates="provenance_records")

    __table_args__ = (
        UniqueConstraint("vessel_id", "attribute_name", "source_name", "source_record_id", name="uq_vessel_attr_prov_source"),
        Index("idx_vessel_attr_prov", "vessel_id", "attribute_name"),
    )


class VesselOperationalEstimates(Base):
    """
    Operational engineering estimates (speed-consumption curves, voyage models).
    Strictly segregated from verified statutory vessel particulars.
    Traces to Pelagos Stage F1.2 and IMO GHG engineering models.
    """
    __tablename__ = "vessel_operational_estimates"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    vessel_id = Column(Integer, ForeignKey("vessels.id", ondelete="CASCADE"), index=True, nullable=False)
    model_name = Column(String(64), nullable=False)
    model_version = Column(String(32), nullable=True)
    eco_speed_knots = Column(Float, nullable=True)
    laden_consumption_mt_per_day = Column(Float, nullable=True)
    ballast_consumption_mt_per_day = Column(Float, nullable=True)
    port_idle_consumption_mt_per_day = Column(Float, nullable=True)
    formula_reference = Column(String(256), nullable=True)
    assumptions = Column(Text, nullable=True)
    computed_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    vessel = relationship("Vessel", back_populates="operational_estimates")

    __table_args__ = (
        Index("idx_vessel_op_est", "vessel_id", "model_name"),
    )


class VesselHydrostatics(Base):
    """
    Certified hydrostatic, hydrodynamic, and shipyard delivery benchmarks.
    Preserves displacement, lightweight tonnage, design speed/draft/power,
    and calibrated Admiralty coefficients derived from official shipyard trials
    or statutory classification society records.

    Naval Architecture Invariant:
    Displacement Delta != DWT.
    Laden Displacement = DWT + Lightweight Tonnage (or directly certified yard value).
    """
    __tablename__ = "vessel_hydrostatics"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    vessel_id = Column(Integer, ForeignKey("vessels.id", ondelete="CASCADE"), index=True, nullable=False)

    # Hydrostatic mass parameters (in Metric Tonnes)
    lightweight_tonnage_mt = Column(Float, nullable=True)
    laden_displacement_mt = Column(Float, nullable=True)
    ballast_displacement_mt = Column(Float, nullable=True)

    # Certified design speed, draft, and service power
    design_speed_knots = Column(Float, nullable=True)
    design_draft_meters = Column(Float, nullable=True)
    design_power_kw = Column(Float, nullable=True)
    design_power_definition = Column(String(32), nullable=True, default="CSR_BRAKE_POWER")  # CSR_BRAKE_POWER, MCR

    # Calibrated Admiralty coefficients: C_adm = (Delta^(2/3) * V^3) / P
    calibrated_admiralty_coefficient = Column(Float, nullable=True)
    ballast_admiralty_coefficient = Column(Float, nullable=True)

    # Source provenance and licensing governance
    source_name = Column(String(128), nullable=False)
    source_record_id = Column(String(64), nullable=True)
    source_url = Column(String(512), nullable=True)
    retrieval_date = Column(String(32), nullable=True)
    verification_status = Column(String(32), nullable=False, default="SOURCE_REPORTED")  # DIRECTLY_VERIFIED, SOURCE_REPORTED, UNAVAILABLE
    permitted_use = Column(String(32), nullable=False, default="RESEARCH_ONLY")  # OPERATIONAL_PERMITTED, RESEARCH_ONLY, RESTRICTED_PENDING_PERMISSION
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    vessel = relationship("Vessel", back_populates="hydrostatics")

    __table_args__ = (
        Index("idx_vessel_hydrostatics_vessel_id", "vessel_id"),
    )


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


# ---------------------------------------------------------------------------
# Stage F1.4.2.2b: Engine SFOC Library & Load-Curve Parameterization
# ---------------------------------------------------------------------------

class EngineSfocCurve(Base):
    """
    Traceable, variant-aware marine engine Specific Fuel Oil Consumption (SFOC) curve.
    Maintains provenance to manufacturer project guides, marine installation manuals (MIM),
    and verified statutory documentation (Stage F1.4.2.2b).
    """
    __tablename__ = "engine_sfoc_curves"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    curve_code = Column(String(64), unique=True, index=True, nullable=False)
    engine_manufacturer = Column(String(128), index=True, nullable=False)
    engine_model = Column(String(128), index=True, nullable=False)
    engine_variant = Column(String(128), index=True, nullable=True)
    engine_family = Column(String(128), index=True, nullable=False)
    applicability_scope = Column(String(64), nullable=False, default=CurveApplicabilityScope.FAMILY_REFERENCE)
    authenticity_status = Column(String(32), nullable=False, default=VerificationStatus.DIRECTLY_VERIFIED)
    permitted_use = Column(String(64), nullable=False, default=SfocPermittedUse.RESEARCH_ONLY)
    is_aggregate_research = Column(Boolean, nullable=False, default=False)
    cylinder_count = Column(Integer, nullable=True)
    rated_power_kw = Column(Float, nullable=True)
    rated_rpm = Column(Float, nullable=True)
    tuning_strategy = Column(String(128), nullable=True)
    fuel_type = Column(String(64), nullable=False, default="ISO_DMA_DIESEL")
    fuel_lcv_mj_kg = Column(Float, nullable=False, default=42.7)
    reference_conditions = Column(Text, nullable=True)
    tolerance_pct = Column(Float, nullable=True, default=5.0)
    source_name = Column(String(256), nullable=False)
    source_document = Column(String(256), nullable=False)
    source_document_url = Column(String(512), nullable=True)
    source_revision = Column(String(128), nullable=True)
    source_citation_details = Column(String(256), nullable=True)
    retrieval_date = Column(String(32), nullable=True)
    licensing_status = Column(String(64), nullable=False, default=SourceLicensingStatus.OPEN_RESEARCH_ONLY)
    min_load_percent = Column(Float, nullable=False, default=50.0)
    max_load_percent = Column(Float, nullable=False, default=100.0)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    load_points = relationship(
        "EngineSfocLoadPoint",
        back_populates="curve",
        cascade="all, delete-orphan",
        order_by="EngineSfocLoadPoint.load_percent",
    )

    __table_args__ = (
        Index("idx_sfoc_curve_maker_model", "engine_manufacturer", "engine_model"),
        Index("idx_sfoc_curve_family", "engine_family"),
    )


class EngineSfocLoadPoint(Base):
    """
    Documented or calibrated SFOC load point for a marine engine curve (Stage F1.4.2.2b).
    Preserves exact manufacturer load points and provides boundaries for piecewise linear interpolation.
    """
    __tablename__ = "engine_sfoc_load_points"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    curve_id = Column(Integer, ForeignKey("engine_sfoc_curves.id", ondelete="CASCADE"), nullable=False, index=True)
    load_percent = Column(Float, nullable=False)
    sfoc_g_kwh = Column(Float, nullable=False)
    tolerance_pct = Column(Float, nullable=True)
    is_guaranteed = Column(Boolean, nullable=False, default=False)
    is_interpolated = Column(Boolean, nullable=False, default=False)
    notes = Column(String(256), nullable=True)

    curve = relationship("EngineSfocCurve", back_populates="load_points")

    __table_args__ = (
        UniqueConstraint("curve_id", "load_percent", name="uq_sfoc_curve_load"),
        Index("idx_sfoc_curve_load", "curve_id", "load_percent"),
    )


# ---------------------------------------------------------------------------
# Stage F1.4.2.3a: Canal Tariff Registry & Currency Conversion Models
# ---------------------------------------------------------------------------

class CurrencyExchangeRate(Base):
    """
    Statutory and reference currency exchange rate (Stage F1.4.2.3a).
    Supports multi-currency settlement conversion (e.g. IMF SDR, EUR, GBP, SGD to USD)
    with strict effective-date gating and provenance audit trails.
    """
    __tablename__ = "currency_exchange_rates"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    base_currency = Column(String(8), nullable=False, default="USD")
    quote_currency = Column(String(8), nullable=False, index=True)
    rate = Column(Float, nullable=False)
    effective_date = Column(Date, nullable=False, index=True)
    rate_provider = Column(String(128), nullable=False)
    retrieval_timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    source_reference = Column(String(256), nullable=False)
    evidence_status = Column(String(32), nullable=False, default="DIRECTLY_VERIFIED")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    __table_args__ = (
        Index("idx_fx_base_quote_date", "base_currency", "quote_currency", "effective_date"),
        UniqueConstraint("base_currency", "quote_currency", "effective_date", "rate_provider", name="uq_fx_rate_entry"),
    )


class CanalTariffRecord(Base):
    """
    Structured statutory canal transit tariff record for Panama and Suez (Stage F1.4.2.3a).
    Preserves all official statutory attributes, dimensional tier criteria, progressive
    brackets, ballast treatments, effective-date schedules, and source references.
    """
    __tablename__ = "canal_tariff_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    canal_authority = Column(String(32), nullable=False, index=True)  # ACP, SCA
    jurisdiction = Column(String(64), nullable=False)
    tariff_identifier = Column(String(64), nullable=False, index=True)  # e.g. 1010.FR03, 1010.DR01, SCA-SDR-TIER1, SCA-CIRC-7-2023
    charge_category = Column(String(64), nullable=False, index=True)  # FIXED_TOLL, CAPACITY_TOLL, PROGRESSIVE_BASE_TOLL, CIRCULAR_SURCHARGE, FRESHWATER_SURCHARGE_FIXED, FRESHWATER_SURCHARGE_VARIABLE, MOORING_LIGHTING_SERVICE
    original_amount = Column(Float, nullable=True)
    currency = Column(String(8), nullable=False, default="USD")  # USD, SDR
    billing_unit = Column(String(64), nullable=False)  # USD/transit, USD/DWT, SDR/SCNT, PERCENTAGE
    calculation_basis = Column(String(128), nullable=False)  # VESSEL_DIMENSIONS, DEADWEIGHT_TONNAGE, SUEZ_CANAL_NET_TONNAGE, PROGRESSIVE_BRACKET, SIGMOIDAL_WATER_LEVEL
    vessel_applicability = Column(String(64), nullable=False, default="DRY_BULK")
    cargo_applicability = Column(String(64), nullable=False, default="DRY_BULK")
    loading_condition = Column(String(16), nullable=False, default="ANY")  # LADEN, BALLAST, ANY
    tier_or_bracket = Column(String(32), nullable=True)  # Regular, Super, Neopanamax, Tier 1, Tier 2, etc.
    min_threshold = Column(Float, nullable=True)
    max_threshold = Column(Float, nullable=True)
    rate_value = Column(Float, nullable=False)
    ballast_factor = Column(Float, nullable=True)  # e.g. 0.85
    effective_from = Column(Date, nullable=False, index=True)
    effective_to = Column(Date, nullable=False, index=True)
    publication_date = Column(Date, nullable=True)
    retrieval_date = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    source_url = Column(String(512), nullable=False)
    document_identifier = Column(String(128), nullable=False)
    precise_source_reference = Column(String(256), nullable=False)
    evidence_classification = Column(String(64), nullable=False, default="DIRECTLY_VERIFIED")  # DIRECTLY_VERIFIED, PARTIALLY_VERIFIED, SOURCE_REPORTED
    permission_status = Column(String(64), nullable=False, default="PERMITTED_FOR_STATUTORY_ESTIMATION")
    supersession_status = Column(String(32), nullable=False, default="HISTORICAL")  # CURRENT, HISTORICAL, SUPERSEDED
    original_source_text = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    __table_args__ = (
        Index("idx_canal_lookup", "canal_authority", "charge_category", "effective_from", "effective_to"),
        Index("idx_canal_tier", "canal_authority", "tier_or_bracket"),
    )


# ---------------------------------------------------------------------------
# Stage F1.4.2.3b: Global Port Disbursement Intelligence Models
# ---------------------------------------------------------------------------

class PortTariffRecord(Base):
    """
    Source-backed statutory and regulated port tariff record (Stage F1.4.2.3b).
    Encodes normalized port disbursement structures: port dues, pilotage, towage,
    dockage, wharfage, mooring, waste, security, and statutory charges.
    """
    __tablename__ = "port_tariff_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    un_locode = Column(String(8), nullable=False, index=True)  # e.g. SGSIN, NLRTM, USHOU
    port_name = Column(String(128), nullable=False)
    country_code = Column(String(8), nullable=False, index=True)
    jurisdiction = Column(String(64), nullable=False)
    port_authority = Column(String(128), nullable=False)
    tariff_identifier = Column(String(64), nullable=False, index=True)  # e.g. MPA-PORT-DUES-2023, RTM-SEAPORT-DUES-2024
    charge_category = Column(String(64), nullable=False, index=True)  # PORT_DUES, PILOTAGE, TOWAGE, BERTH_OCCUPANCY_DOCKAGE, WHARFAGE, MOORING_UNMOORING, ANCHORAGE, WASTE_RECEPTION, FRESHWATER_SERVICE, SECURITY_ISPS, AGENCY_FEE, STATUTORY_LIGHT_DUES
    applicability_type = Column(String(32), nullable=False, default="MANDATORY")  # MANDATORY, CONDITIONAL, USAGE_BASED, OPTIONAL
    vessel_class = Column(String(64), nullable=False, default="DRY_BULK")  # DRY_BULK, ALL_VESSELS, CONTAINER, TANKER
    basis_dimension = Column(String(32), nullable=False)  # GT, DWT, LOA_METERS, LOA_FEET, BEAM_METERS, DRAFT_METERS, DRAFT_FEET, DURATION_HOURS, DURATION_DAYS, CALL_EVENT, CARGO_TONS, VOLUME_M3
    min_threshold = Column(Float, nullable=True)
    max_threshold = Column(Float, nullable=True)
    rate_value = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default="USD")
    billing_unit = Column(String(64), nullable=False)  # PER_100_GT_PER_DAY, EUR_PER_GT, USD_PER_FOOT_PER_DAY, FIXED_PER_CALL, USD_PER_DRAFT_FOOT
    min_charge = Column(Float, nullable=True)
    max_charge = Column(Float, nullable=True)
    effective_from = Column(Date, nullable=False, index=True)
    effective_to = Column(Date, nullable=False, index=True)
    publication_date = Column(Date, nullable=True)
    retrieval_date = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    source_url = Column(String(512), nullable=False)
    document_identifier = Column(String(128), nullable=False)
    precise_source_reference = Column(String(256), nullable=False)
    evidence_classification = Column(String(64), nullable=False, default="DIRECTLY_VERIFIED")  # DIRECTLY_VERIFIED, SOURCE_REPORTED, PARTIALLY_VERIFIED, UNVERIFIED, UNAVAILABLE, HISTORICAL
    permission_status = Column(String(64), nullable=False, default="PERMITTED_FOR_STATUTORY_ESTIMATION")
    supersession_status = Column(String(32), nullable=False, default="HISTORICAL")  # CURRENT, HISTORICAL, SUPERSEDED
    applicability_condition = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    __table_args__ = (
        Index("idx_port_tariff_lookup", "un_locode", "charge_category", "effective_from", "effective_to"),
        Index("idx_port_tariff_id", "un_locode", "tariff_identifier"),
    )


class VoyageDisbursementRecord(Base):
    """
    Voyage-level disbursement integration record (Stage F1.4.2.4).
    Associates canal transit and port disbursement records with individual voyage assessments.
    Strictly isolated from commercial TCE, net freight, and operational chartering recommendations.
    """
    __tablename__ = "voyage_disbursement_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    voyage_id = Column(String(64), nullable=False, index=True)
    vessel_imo = Column(Integer, nullable=True, index=True)
    vessel_name = Column(String(128), nullable=True)
    vessel_class = Column(String(64), nullable=True)
    origin_port_unlocode = Column(String(8), nullable=False, index=True)
    destination_port_unlocode = Column(String(8), nullable=False, index=True)
    intermediate_ports_json = Column(Text, nullable=True)  # JSON list of UN/LOCODEs
    canal_transits_json = Column(Text, nullable=True)      # JSON list of canal IDs ("PANAMA", "SUEZ")
    transit_date = Column(Date, nullable=False, index=True)

    # Vessel Particulars at transit time
    vessel_gt = Column(Float, nullable=True)
    vessel_dwt = Column(Float, nullable=True)
    vessel_loa_m = Column(Float, nullable=True)
    vessel_beam_m = Column(Float, nullable=True)
    vessel_draft_m = Column(Float, nullable=True)
    scnt = Column(Float, nullable=True)
    pc_ums_net = Column(Float, nullable=True)

    # Synonyms for flexible access
    loa_m = synonym("vessel_loa_m")
    beam_m = synonym("vessel_beam_m")
    draft_m = synonym("vessel_draft_m")

    # Disbursement Financial Totals (USD)
    total_disbursement_usd = Column(Float, nullable=True)
    total_port_disbursement_usd = Column(Float, nullable=True)
    total_canal_transit_usd = Column(Float, nullable=True)

    # Readiness, Governance & Operational Gating
    readiness_tier = Column(String(64), nullable=False, default="LEVEL_2_RESEARCH_ONLY")
    completeness_status = Column(String(64), nullable=False)  # e.g. COMPLETE_WITHIN_REGISTERED_SCOPE, PARTIAL_COMPONENTS
    is_complete = Column(Boolean, nullable=False, default=False)
    has_partial_components = Column(Boolean, nullable=False, default=False)
    is_operational_approved = Column(Boolean, nullable=False, default=False)  # Enforced False under Level 3 Block
    blocked_reasons_json = Column(Text, nullable=True)
    evidence_summary_json = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    unallocated_overage_usd = Column(Float, nullable=True)
    disclaimer = Column(Text, nullable=True)

    # Cryptographic Content Hashing & Attestation (Stage F1.4.3 Phase 2)
    merkle_root = Column(String(64), nullable=True)
    record_payload_hash = Column(String(64), nullable=True)
    attestation_signature = Column(Text, nullable=True)
    signing_key_id = Column(String(64), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    line_items = relationship("VoyageDisbursementLineItem", back_populates="voyage_record", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_voyage_disbursement_voyage_id", "voyage_id"),
        Index("idx_voyage_disbursement_origin_dest", "origin_port_unlocode", "destination_port_unlocode"),
        Index("idx_voyage_disbursement_status", "completeness_status", "readiness_tier"),
    )


class VoyageDisbursementLineItem(Base):
    """
    Detailed line item associated with a voyage disbursement assessment (Stage F1.4.2.4).
    Preserves fine-grained charge provenance, local amount, converted amount, and partial-component flag.
    """
    __tablename__ = "voyage_disbursement_line_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    voyage_disbursement_record_id = Column(Integer, ForeignKey("voyage_disbursement_records.id", ondelete="CASCADE"), nullable=False, index=True)
    charge_type = Column(String(32), nullable=False)   # PORT_DISBURSEMENT | CANAL_TRANSIT
    location_code = Column(String(32), nullable=False)  # UN/LOCODE (e.g. SGSIN) or Canal Name (PANAMA, SUEZ)
    charge_category = Column(String(64), nullable=False)
    tariff_identifier = Column(String(64), nullable=False, index=True)
    description = Column(String(256), nullable=False)
    applicability_type = Column(String(32), nullable=False)  # MANDATORY, CONDITIONAL, USAGE_BASED, OPTIONAL, COMMERCIALLY_NEGOTIATED / CONTRACTUAL
    rate_value = Column(Float, nullable=False)
    billing_unit = Column(String(64), nullable=False)
    local_currency = Column(String(8), nullable=False)
    amount_local = Column(Float, nullable=True)
    exchange_rate_to_usd = Column(Float, nullable=True)
    amount_usd = Column(Float, nullable=True)
    fx_rate_provider = Column(String(128), nullable=True)
    fx_observation_date = Column(Date, nullable=True)
    requested_valuation_date = Column(Date, nullable=True)
    fx_observation_lag_days = Column(Integer, nullable=True)
    conversion_evidence_classification = Column(String(64), nullable=True)
    charge_tier = Column(String(64), nullable=True)
    contract_attribution = Column(String(128), nullable=True)
    is_provisional = Column(Boolean, nullable=True, default=False)
    is_partial_component = Column(Boolean, nullable=False, default=False)
    is_available = Column(Boolean, nullable=False, default=True)
    evidence_classification = Column(String(64), nullable=False, default="DIRECTLY_VERIFIED")
    permission_status = Column(String(64), nullable=False, default="PERMITTED_STATUTORY_DISCLOSURE")
    failure_reason = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    voyage_record = relationship("VoyageDisbursementRecord", back_populates="line_items")

    __table_args__ = (
        Index("idx_vydisb_line_item_loc_cat", "location_code", "charge_category"),
        Index("idx_vydisb_line_item_tariff", "tariff_identifier"),
    )


class ConsumedOperationalToken(Base):
    """
    Append-only single-use ledger for consumed operational release tokens (Stage F1.4.3 Phase 2).
    Prevents token replay attacks across execution attempts.
    """
    __tablename__ = "consumed_operational_tokens"

    jti = Column(String(64), primary_key=True)
    consumed_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    voyage_id = Column(String(64), nullable=False, index=True)
    vessel_imo = Column(Integer, nullable=False)
    disbursement_record_id = Column(Integer, nullable=False)
    caller_identity = Column(String(128), nullable=False)
    signature_hash = Column(String(64), nullable=False)

    __table_args__ = (
        Index("idx_consumed_tokens_voyage", "voyage_id"),
        Index("idx_consumed_tokens_vessel", "vessel_imo"),
    )


class VoyageDisbursementAuditLog(Base):
    """
    Append-only chronological audit ledger for voyage disbursement events (Stage F1.4.3 Phase 2).
    Enforces strictly monotonic sequence numbers and SHA-256 hash chaining back to the genesis root.
    INVARIANT: Chronological audit events are strictly append-only and never sorted lexicographically.
    """
    __tablename__ = "voyage_disbursement_audit_log"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    sequence_number = Column(BigInteger, nullable=False, unique=True, index=True)
    record_id = Column(Integer, ForeignKey("voyage_disbursement_records.id", ondelete="CASCADE"), nullable=False, index=True)
    voyage_id = Column(String(64), nullable=False, index=True)
    vessel_imo = Column(Integer, nullable=False)
    event_type = Column(String(32), nullable=False)
    merkle_root = Column(String(64), nullable=False)
    record_payload_hash = Column(String(64), nullable=False)
    attestation_signature = Column(Text, nullable=False)
    signing_key_id = Column(String(64), nullable=False)
    previous_chain_hash = Column(String(64), nullable=False)
    chain_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    __table_args__ = (
        Index("idx_vydisb_audit_seq", "sequence_number"),
        Index("idx_vydisb_audit_record", "record_id"),
        Index("idx_vydisb_audit_voyage", "voyage_id"),
    )
