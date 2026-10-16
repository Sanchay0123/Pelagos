"""PyArrow schema definition and conversions for historical AIS Parquet partitions."""

from typing import List, Any, Dict
from datetime import timezone
import pyarrow as pa
from pelagos.models import VesselPosition, FreightObservation

TELEMETRY_PARQUET_SCHEMA = pa.schema([
    pa.field("id", pa.int64(), nullable=False),
    pa.field("mmsi", pa.string(), nullable=False),
    pa.field("imo", pa.string(), nullable=True),
    pa.field("vessel_name", pa.string(), nullable=False),
    pa.field("vessel_type", pa.string(), nullable=False),
    pa.field("vessel_type_code", pa.int32(), nullable=True),
    pa.field("latitude", pa.float64(), nullable=False),
    pa.field("longitude", pa.float64(), nullable=False),
    pa.field("speed_knots", pa.float64(), nullable=True),
    pa.field("course_degrees", pa.float64(), nullable=True),
    pa.field("heading_degrees", pa.float64(), nullable=True),
    pa.field("draft_meters", pa.float64(), nullable=True),
    pa.field("length_meters", pa.float64(), nullable=True),
    pa.field("width_meters", pa.float64(), nullable=True),
    pa.field("destination", pa.string(), nullable=True),
    pa.field("navigational_status", pa.string(), nullable=True),
    pa.field("observation_time", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("publication_time", pa.timestamp("us", tz="UTC"), nullable=True),
    pa.field("ingestion_time", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("processing_time", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("source_name", pa.string(), nullable=False),
    pa.field("source_partition", pa.string(), nullable=True),
    pa.field("ingestion_run_id", pa.int64(), nullable=True),
    pa.field("license_type", pa.string(), nullable=False),
    pa.field("raw_record_id", pa.string(), nullable=True),
    pa.field("idempotency_key", pa.string(), nullable=False),
])

FREIGHT_PARQUET_SCHEMA = pa.schema([
    pa.field("id", pa.int64(), nullable=False),
    pa.field("route_code", pa.string(), nullable=False),
    pa.field("observation_date", pa.date32(), nullable=False),
    pa.field("rate_value", pa.float64(), nullable=False),
    pa.field("currency", pa.string(), nullable=False),
    pa.field("unit_of_measure", pa.string(), nullable=False),
    pa.field("is_derived", pa.bool_(), nullable=False),
    pa.field("rate_high", pa.float64(), nullable=True),
    pa.field("rate_low", pa.float64(), nullable=True),
    pa.field("observation_period", pa.string(), nullable=False),
    pa.field("source_name", pa.string(), nullable=False),
    pa.field("source_dataset_id", pa.string(), nullable=True),
    pa.field("publication_time", pa.timestamp("us", tz="UTC"), nullable=True),
    pa.field("ingestion_time", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("processing_time", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("license_type", pa.string(), nullable=False),
    pa.field("idempotency_key", pa.string(), nullable=False),
    pa.field("ingestion_run_id", pa.int64(), nullable=True),
])


def normalize_datetime_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def records_to_pyarrow_table(records: List[VesselPosition]) -> pa.Table:
    """Converts a sequence of VesselPosition ORM rows into a strict PyArrow Table."""
    columns: Dict[str, List[Any]] = {field.name: [] for field in TELEMETRY_PARQUET_SCHEMA}

    for r in records:
        columns["id"].append(int(r.id))
        columns["mmsi"].append(str(r.mmsi))
        columns["imo"].append(str(r.imo) if r.imo is not None else None)
        columns["vessel_name"].append(str(r.vessel_name))
        columns["vessel_type"].append(str(r.vessel_type))
        columns["vessel_type_code"].append(int(r.vessel_type_code) if r.vessel_type_code is not None else None)
        columns["latitude"].append(float(r.latitude))
        columns["longitude"].append(float(r.longitude))
        columns["speed_knots"].append(float(r.speed_knots) if r.speed_knots is not None else None)
        columns["course_degrees"].append(float(r.course_degrees) if r.course_degrees is not None else None)
        columns["heading_degrees"].append(float(r.heading_degrees) if r.heading_degrees is not None else None)
        columns["draft_meters"].append(float(r.draft_meters) if r.draft_meters is not None else None)
        columns["length_meters"].append(float(r.length_meters) if r.length_meters is not None else None)
        columns["width_meters"].append(float(r.width_meters) if r.width_meters is not None else None)
        columns["destination"].append(str(r.destination) if r.destination is not None else None)
        columns["navigational_status"].append(str(r.navigational_status) if r.navigational_status is not None else None)
        columns["observation_time"].append(normalize_datetime_utc(r.observation_time))
        columns["publication_time"].append(normalize_datetime_utc(r.publication_time))
        columns["ingestion_time"].append(normalize_datetime_utc(r.ingestion_time))
        columns["processing_time"].append(normalize_datetime_utc(r.processing_time))
        columns["source_name"].append(str(r.source_name))
        columns["source_partition"].append(str(r.source_partition) if r.source_partition is not None else None)
        columns["ingestion_run_id"].append(int(r.ingestion_run_id) if r.ingestion_run_id is not None else None)
        columns["license_type"].append(str(r.license_type))
        columns["raw_record_id"].append(str(r.raw_record_id) if r.raw_record_id is not None else None)
        columns["idempotency_key"].append(str(r.idempotency_key))

    arrays = []
    for field in TELEMETRY_PARQUET_SCHEMA:
        arrays.append(pa.array(columns[field.name], type=field.type))

    return pa.Table.from_arrays(arrays, schema=TELEMETRY_PARQUET_SCHEMA)


def freight_records_to_pyarrow_table(records: List[FreightObservation]) -> pa.Table:
    """Converts a sequence of FreightObservation ORM rows into a strict PyArrow Table."""
    columns: Dict[str, List[Any]] = {field.name: [] for field in FREIGHT_PARQUET_SCHEMA}

    for r in records:
        columns["id"].append(int(r.id))
        columns["route_code"].append(str(r.route_code))
        columns["observation_date"].append(r.observation_date)
        columns["rate_value"].append(float(r.rate_value))
        columns["currency"].append(str(r.currency))
        columns["unit_of_measure"].append(str(r.unit_of_measure))
        columns["is_derived"].append(bool(r.is_derived))
        columns["rate_high"].append(float(r.rate_high) if r.rate_high is not None else None)
        columns["rate_low"].append(float(r.rate_low) if r.rate_low is not None else None)
        columns["observation_period"].append(str(r.observation_period))
        columns["source_name"].append(str(r.source_name))
        columns["source_dataset_id"].append(str(r.source_dataset_id) if r.source_dataset_id is not None else None)
        columns["publication_time"].append(normalize_datetime_utc(r.publication_time))
        columns["ingestion_time"].append(normalize_datetime_utc(r.ingestion_time))
        columns["processing_time"].append(normalize_datetime_utc(r.processing_time))
        columns["license_type"].append(str(r.license_type))
        columns["idempotency_key"].append(str(r.idempotency_key))
        columns["ingestion_run_id"].append(int(r.ingestion_run_id) if r.ingestion_run_id is not None else None)

    arrays = []
    for field in FREIGHT_PARQUET_SCHEMA:
        arrays.append(pa.array(columns[field.name], type=field.type))

    return pa.Table.from_arrays(arrays, schema=FREIGHT_PARQUET_SCHEMA)
