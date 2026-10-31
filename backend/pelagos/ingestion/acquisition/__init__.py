"""Pelagos Automated External Source Acquisition Package."""

from pelagos.ingestion.acquisition.client import (
    AcquisitionError,
    AcquisitionHTTPError,
    AcquisitionNetworkError,
    AcquisitionResponse,
    AcquisitionTimeoutError,
    ResilientHttpClient,
)
from pelagos.ingestion.acquisition.noaa_ndbc import (
    fetch_noaa_ndbc_station_data,
    parse_ndbc_realtime_text,
    refresh_environmental_observations,
)
from pelagos.ingestion.acquisition.ports_adapter import refresh_ports_directory
from pelagos.ingestion.acquisition.trade_adapter import refresh_trade_observations
from pelagos.ingestion.acquisition.vessels_adapter import refresh_vessels_registry
from pelagos.ingestion.acquisition.coordinator import run_sources_refresh

__all__ = [
    "AcquisitionError",
    "AcquisitionHTTPError",
    "AcquisitionNetworkError",
    "AcquisitionResponse",
    "AcquisitionTimeoutError",
    "ResilientHttpClient",
    "fetch_noaa_ndbc_station_data",
    "parse_ndbc_realtime_text",
    "refresh_environmental_observations",
    "refresh_ports_directory",
    "refresh_trade_observations",
    "refresh_vessels_registry",
    "run_sources_refresh",
]
