"""Pydantic schemas for telemetry ingestion, validation, and snapshot serialization."""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class TelemetryProvenance(BaseModel):
    source_name: str = Field(..., description="Canonical source provider identifier")
    license: str = Field(..., description="Data license identifier or terms")
    raw_record_id: Optional[str] = Field(None, description="Unique upstream message or frame identifier")


class TelemetryRecord(BaseModel):
    """
    Ingestion telemetry record matching DATA-001 Section 5.1 contract.
    Enforces multi-dimensional UTC timestamps and verifiable provenance.
    """
    mmsi: str = Field(..., description="Maritime Mobile Service Identity (5-9 digits)")
    imo: Optional[str] = Field(None, description="International Maritime Organization number")
    vessel_name: str = Field(..., description="Reported vessel name")
    vessel_type: Optional[str] = Field("Commercial", description="Vessel categorical classification")
    vessel_type_code: Optional[int] = Field(None, description="Raw AIS vessel type code")
    
    latitude: float = Field(..., description="Latitude in decimal degrees [-90, +90]")
    longitude: float = Field(..., description="Longitude in decimal degrees [-180, +180]")
    
    speed_knots: Optional[float] = Field(None, description="Speed Over Ground in knots")
    course_degrees: Optional[float] = Field(None, description="Course Over Ground in degrees [0, 360]")
    heading_degrees: Optional[float] = Field(None, description="True heading in degrees [0, 359]")
    draft_meters: Optional[float] = Field(None, description="Maximum present static draft in meters")
    length_meters: Optional[float] = Field(None, description="Overall vessel length in meters")
    width_meters: Optional[float] = Field(None, description="Vessel beam in meters")
    destination: Optional[str] = Field(None, description="Reported voyage destination")
    navigational_status: Optional[str] = Field(None, description="AIS navigational status")
    
    observation_time: datetime = Field(..., description="UTC timestamp when position was broadcast")
    publication_time: Optional[datetime] = Field(None, description="UTC timestamp when provider published record")
    source_timezone_verified: bool = Field(True, description="Whether UTC interpretation is contractually established")
    
    source_name: str = Field("NOAA MarineCadastre / USCG NAIS", description="Source provider name")
    license: str = Field("CC0 1.0 Universal", description="License identifier")
    raw_record_id: Optional[str] = Field(None, description="Upstream raw ID")


class QuarantinedSummary(BaseModel):
    error_code: str
    error_details: str
    mmsi: Optional[str] = None
    raw_record_id: Optional[str] = None


class IngestionResult(BaseModel):
    total_received: int
    accepted_count: int
    quarantined_count: int
    duplicate_count: int
    quarantined_records: List[QuarantinedSummary] = Field(default_factory=list)


class VesselSnapshotRow(BaseModel):
    """
    Vessel row serialized for the God's Eye View frontend (vesselSnapshot parser).
    Compatible with src/sources/live/vessels.js.
    """
    mmsi: str
    imo: Optional[str] = ""
    name: str
    type_specific: str
    lat: float
    lon: float
    speed: Optional[float] = None  # in knots
    course: Optional[float] = None
    heading: Optional[float] = None
    destination: Optional[str] = ""
    last_position_UTC: str
    last_position_epoch: Optional[int] = None
    historical: bool = False


class VesselSnapshotResponse(BaseModel):
    """
    Complete vessel snapshot response matching God's Eye View contract.
    Explicitly distinguishes historical observations from live streaming AIS.
    """
    rows: List[VesselSnapshotRow]
    source: str
    license: str
    coverage: str
    status: str = "historical"
    data_mode: str = "historical"
    is_live: bool = False
    live_ais_connected: bool = False
    backend_connected: bool = True
    freshness: str = "historical"
    refreshing: bool = False
    newestPositionAt: Optional[str] = None
    observation_date: Optional[str] = None
    rawRowCount: int
    total_records: int
    disclaimer: str
