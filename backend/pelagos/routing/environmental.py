"""Pelagos Maritime Routing - Environmental Observation and Buoy Corridor Association.

Associates genuine NOAA National Data Buoy Center (NDBC) and oceanic buoy observations
with monitored commercial maritime freight corridors (e.g. PNW-JPN-BULK, USG-JPN-BULK).

Enforces strict oceanographic and commercial integrity:
- Associates buoys with corridor departure fairways via genuine geodesic distances (WGS 84).
- Reports individual station measurements; does NOT represent a single coastal buoy
  as an observation of an entire trans-oceanic passage.
- Does NOT perform spatial interpolation across the open Pacific or Atlantic.
- Evaluates operational data freshness (fresh <= 24h, recent <= 7d, stale <= 30d, historical > 30d).
- Preserves genuine station coordinates, timestamps, variables, and units.
- Never creates or fabricates synthetic environmental observations.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pelagos.routing.geometry import geodesic_distance_nm
from pelagos.ingestion.acquisition.noaa_ndbc import KNOWN_NDBC_STATIONS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Corridor Departure Fairway & Mooring Registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CorridorFairwayReference:
    """Geographic departure fairway or reference waypoint for a commercial corridor."""

    corridor_code: str
    fairway_name: str
    latitude: float
    longitude: float
    candidate_station_ids: Tuple[str, ...]
    sea_area_keywords: Tuple[str, ...]
    max_fairway_distance_nm: float = 350.0


# Authoritative reference fairways and associated NOAA NDBC buoys
CORRIDOR_FAIRWAYS: Dict[str, CorridorFairwayReference] = {
    "PNW-JPN-BULK": CorridorFairwayReference(
        corridor_code="PNW-JPN-BULK",
        fairway_name="Columbia River & Pacific Northwest Coastal Fairway",
        latitude=46.25,
        longitude=-124.25,
        candidate_station_ids=("46041", "46029", "46050"),
        sea_area_keywords=("North Pacific", "Columbia River", "Washington", "Oregon"),
        max_fairway_distance_nm=350.0,
    ),
    "USG-JPN-BULK": CorridorFairwayReference(
        corridor_code="USG-JPN-BULK",
        fairway_name="Mississippi River Approach & Central Gulf of Mexico Fairway",
        latitude=28.93,
        longitude=-89.43,
        candidate_station_ids=("42001", "42002", "42003"),
        sea_area_keywords=("Gulf of Mexico", "Mississippi River"),
        max_fairway_distance_nm=350.0,
    ),
}


# ---------------------------------------------------------------------------
# Freshness Classification
# ---------------------------------------------------------------------------

def classify_freshness(
    obs_time: Union[datetime.datetime, str],
    as_of: Optional[Union[datetime.datetime, str]] = None,
) -> Tuple[str, float]:
    """Classify observation age into operational freshness categories.

    Categories:
    - 'fresh': age <= 24.0 hours (real-time operational window)
    - 'recent': 24.0 < age <= 168.0 hours (1 to 7 days)
    - 'stale': 168.0 < age <= 720.0 hours (7 to 30 days)
    - 'historical': age > 720.0 hours (> 30 days)

    Args:
        obs_time: Observation timestamp (datetime or ISO string).
        as_of: Evaluation reference timestamp (defaults to current UTC time).

    Returns:
        Tuple of (freshness_status, age_hours).
    """
    if isinstance(obs_time, str):
        # Normalize ISO strings
        clean_obs = obs_time.replace("Z", "+00:00")
        obs_dt = datetime.datetime.fromisoformat(clean_obs)
    else:
        obs_dt = obs_time

    if obs_dt.tzinfo is None:
        obs_dt = obs_dt.replace(tzinfo=datetime.timezone.utc)

    if as_of is None:
        as_of_dt = datetime.datetime.now(datetime.timezone.utc)
    elif isinstance(as_of, str):
        clean_as_of = as_of.replace("Z", "+00:00")
        as_of_dt = datetime.datetime.fromisoformat(clean_as_of)
    else:
        as_of_dt = as_of

    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=datetime.timezone.utc)

    delta = as_of_dt - obs_dt
    age_hours = round(max(0.0, delta.total_seconds() / 3600.0), 2)

    if age_hours <= 24.0:
        status = "fresh"
    elif age_hours <= 168.0:
        status = "recent"
    elif age_hours <= 720.0:
        status = "stale"
    else:
        status = "historical"

    return status, age_hours


# ---------------------------------------------------------------------------
# Pydantic Schemas for Environmental Intelligence
# ---------------------------------------------------------------------------

class EnvironmentalVariableInfo(BaseModel):
    """Specific oceanographic or meteorological parameter with unit and standard name."""

    value: Optional[float] = None
    unit: str
    standard_name: str


class StationCoordinates(BaseModel):
    """Geographic position of observation station."""

    latitude: float
    longitude: float


class StationEnvironmentalObservation(BaseModel):
    """Latest point-in-time environmental observation for an associated buoy station."""

    station_id: str
    station_name: str
    sea_area: str
    coordinates: StationCoordinates
    distance_to_corridor_fairway_nm: float
    distance_to_origin_port_nm: Optional[float] = None
    observation_time: str
    data_age_hours: float
    freshness: str  # "fresh" | "recent" | "stale" | "historical"
    quality_status: str
    source_name: str
    variables: Dict[str, EnvironmentalVariableInfo]


class EnvironmentalAggregationInfo(BaseModel):
    """Metadata regarding spatial reporting method and absence of unvalidated interpolation."""

    method: str
    summary: str


class EnvironmentalConditionsSummary(BaseModel):
    """Corridor-level environmental summary combining associated moored buoy stations."""

    status: str  # "available" | "partial" | "unavailable"
    coverage_status: str  # "departure_waters_monitored" | "no_active_stations" | "unmonitored"
    coverage_extent: str  # "coastal_departure_fairway" | "none"
    corridor_reference_point: Optional[str] = None
    stations_count: int
    stations: List[StationEnvironmentalObservation] = Field(default_factory=list)
    aggregation: Optional[EnvironmentalAggregationInfo] = None
    spatial_coverage_notes: str
    disclaimer: str


# ---------------------------------------------------------------------------
# Variable Extraction Helper
# ---------------------------------------------------------------------------

def extract_environmental_variables(obs: Any) -> Dict[str, EnvironmentalVariableInfo]:
    """Extract standard meteorological and oceanographic variables with explicit units."""
    return {
        "wind_speed_knots": EnvironmentalVariableInfo(
            value=round(float(obs.wind_speed_knots), 2) if getattr(obs, "wind_speed_knots", None) is not None else None,
            unit="knots",
            standard_name="wind_speed",
        ),
        "wind_direction_degrees": EnvironmentalVariableInfo(
            value=round(float(obs.wind_direction_degrees), 1) if getattr(obs, "wind_direction_degrees", None) is not None else None,
            unit="degrees",
            standard_name="wind_from_direction",
        ),
        "wave_height_meters": EnvironmentalVariableInfo(
            value=round(float(obs.wave_height_meters), 2) if getattr(obs, "wave_height_meters", None) is not None else None,
            unit="meters",
            standard_name="significant_wave_height",
        ),
        "wave_period_seconds": EnvironmentalVariableInfo(
            value=round(float(obs.wave_period_seconds), 1) if getattr(obs, "wave_period_seconds", None) is not None else None,
            unit="seconds",
            standard_name="sea_surface_wave_period",
        ),
        "sea_surface_temp_c": EnvironmentalVariableInfo(
            value=round(float(obs.sea_surface_temp_c), 2) if getattr(obs, "sea_surface_temp_c", None) is not None else None,
            unit="degC",
            standard_name="sea_surface_temperature",
        ),
        "air_pressure_hpa": EnvironmentalVariableInfo(
            value=round(float(obs.air_pressure_hpa), 1) if getattr(obs, "air_pressure_hpa", None) is not None else None,
            unit="hPa",
            standard_name="air_pressure_at_sea_level",
        ),
    }


# ---------------------------------------------------------------------------
# Corridor Environmental Resolver
# ---------------------------------------------------------------------------

def resolve_corridor_environmental_conditions(
    db: Session,
    corridor_code: str,
    origin_coords: Optional[Tuple[float, float]] = None,
    as_of: Optional[Union[datetime.datetime, str]] = None,
) -> EnvironmentalConditionsSummary:
    """Resolve and summarize genuine moored buoy environmental conditions for a corridor.

    Args:
        db: Active SQLAlchemy database session.
        corridor_code: Monitored corridor code (e.g. PNW-JPN-BULK, USG-JPN-BULK).
        origin_coords: Optional (longitude, latitude) of the departure port for local distance.
        as_of: Optional evaluation timestamp for deterministic freshness evaluation.

    Returns:
        Structured corridor-level environmental conditions summary.
    """
    from pelagos.models import EnvironmentalObservation

    ref = CORRIDOR_FAIRWAYS.get(corridor_code)
    if not ref:
        return EnvironmentalConditionsSummary(
            status="unavailable",
            coverage_status="unmonitored",
            coverage_extent="none",
            corridor_reference_point=None,
            stations_count=0,
            stations=[],
            aggregation=None,
            spatial_coverage_notes=f"Corridor '{corridor_code}' is not configured for environmental buoy association.",
            disclaimer="Environmental buoy monitoring is restricted to monitored commercial trade corridors.",
        )

    try:
        # Find distinct stations present in the database
        db_stations = (
            db.query(EnvironmentalObservation.station_id)
            .distinct()
            .all()
        )
        station_ids = [s[0] for s in db_stations if s[0]]

        associated_station_observations: List[StationEnvironmentalObservation] = []

        for st_id in station_ids:
            # Check candidate station IDs first or check geographic proximity
            is_candidate = st_id in ref.candidate_station_ids

            # Query the latest observation for this station
            latest_obs = (
                db.query(EnvironmentalObservation)
                .filter(EnvironmentalObservation.station_id == st_id)
                .order_by(EnvironmentalObservation.observation_time.desc())
                .first()
            )

            if not latest_obs:
                continue

            # Resolve authoritative station identity and mooring coordinates
            station_meta = KNOWN_NDBC_STATIONS.get(st_id)
            if station_meta:
                st_name = station_meta["station_name"]
                st_lat = station_meta["latitude"]
                st_lon = station_meta["longitude"]
                st_sea_area = station_meta["sea_area"]
            else:
                st_name = latest_obs.station_name
                st_lat = latest_obs.latitude
                st_lon = latest_obs.longitude
                st_sea_area = latest_obs.sea_area

            if st_lat is None or st_lon is None:
                continue

            # Compute geodesic distance from station to corridor fairway
            dist_to_fairway = geodesic_distance_nm(
                lon1=st_lon,
                lat1=st_lat,
                lon2=ref.longitude,
                lat2=ref.latitude,
            )

            # Match criteria: explicit candidate station ID, OR within max distance and matching sea area
            matches_sea_area = any(kw.lower() in (st_sea_area or "").lower() for kw in ref.sea_area_keywords)
            is_associated = is_candidate or (dist_to_fairway <= ref.max_fairway_distance_nm and matches_sea_area)

            if not is_associated:
                continue

            # Compute distance to origin port if provided
            dist_to_origin = None
            if origin_coords is not None and len(origin_coords) == 2:
                try:
                    dist_to_origin = round(
                        geodesic_distance_nm(
                            lon1=st_lon,
                            lat1=st_lat,
                            lon2=origin_coords[0],
                            lat2=origin_coords[1],
                        ),
                        2,
                    )
                except Exception as exc:
                    logger.debug("Failed to calculate distance to origin port: %s", exc)

            # Freshness classification
            freshness_status, age_hours = classify_freshness(latest_obs.observation_time, as_of=as_of)

            # Observation time string
            if hasattr(latest_obs.observation_time, "isoformat"):
                obs_time_str = latest_obs.observation_time.isoformat()
            else:
                obs_time_str = str(latest_obs.observation_time)

            station_entry = StationEnvironmentalObservation(
                station_id=latest_obs.station_id,
                station_name=st_name,
                sea_area=st_sea_area,
                coordinates=StationCoordinates(
                    latitude=round(float(st_lat), 4),
                    longitude=round(float(st_lon), 4),
                ),
                distance_to_corridor_fairway_nm=round(dist_to_fairway, 2),
                distance_to_origin_port_nm=dist_to_origin,
                observation_time=obs_time_str,
                data_age_hours=age_hours,
                freshness=freshness_status,
                quality_status=latest_obs.quality_status or "verified",
                source_name=latest_obs.source_name,
                variables=extract_environmental_variables(latest_obs),
            )
            associated_station_observations.append(station_entry)

        # Sort stations by distance to corridor fairway
        associated_station_observations.sort(key=lambda s: s.distance_to_corridor_fairway_nm)

        if associated_station_observations:
            count = len(associated_station_observations)
            return EnvironmentalConditionsSummary(
                status="available",
                coverage_status="departure_waters_monitored",
                coverage_extent="coastal_departure_fairway",
                corridor_reference_point=ref.fairway_name,
                stations_count=count,
                stations=associated_station_observations,
                aggregation=EnvironmentalAggregationInfo(
                    method="individual_station_reporting",
                    summary=(
                        f"{count} associated station(s) reporting individually; "
                        "spatial interpolation across the open ocean corridor is omitted to prevent unvalidated extrapolation."
                    ),
                ),
                spatial_coverage_notes=(
                    "Buoy observations reflect localized meteorological and oceanographic conditions "
                    "at departure fairways and approach waters. Deep-sea open ocean transit segments "
                    "lack moored buoy coverage."
                ),
                disclaimer=(
                    "Environmental observations are recorded by moored oceanographic buoys at fixed "
                    "geographic positions and must not be interpreted as uniform corridor-wide ocean conditions."
                ),
            )
        else:
            return EnvironmentalConditionsSummary(
                status="unavailable",
                coverage_status="no_active_stations",
                coverage_extent="none",
                corridor_reference_point=ref.fairway_name,
                stations_count=0,
                stations=[],
                aggregation=None,
                spatial_coverage_notes=(
                    f"No active NOAA NDBC observation stations found for corridor {corridor_code} "
                    "in the local database."
                ),
                disclaimer="No moored environmental observations are currently recorded for this corridor.",
            )

    except Exception as exc:
        logger.error("Error resolving environmental conditions for corridor '%s': %s", corridor_code, exc)
        return EnvironmentalConditionsSummary(
            status="unavailable",
            coverage_status="query_failed",
            coverage_extent="none",
            corridor_reference_point=ref.fairway_name,
            stations_count=0,
            stations=[],
            aggregation=None,
            spatial_coverage_notes=f"Database query encountered an unexpected error: {exc}",
            disclaimer="Environmental data retrieval degraded gracefully.",
        )
