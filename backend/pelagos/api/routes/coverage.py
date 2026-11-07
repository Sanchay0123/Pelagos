"""Pelagos Multi-Source Maritime Data Fusion & Coverage Reporting API.

Provides auditable multi-source coverage reporting, separating directory registration,
navigational network connectivity, freight rate observations, forecasting validity,
vessel telemetry provenance, and commercial charter availability.

Traces to DATA-001 Section 5.2, REQ-FR-CRI-012, OD-ROUT-06-03, and REQ-NFR-DIP-001.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from pelagos.config import settings
from pelagos.database import get_db
from pelagos.models import (
    EnvironmentalObservation,
    FreightObservation,
    FreightRoute,
    IngestionRun,
    Port,
    PortAlias,
    TradeObservation,
    Vessel,
    VesselPosition,
)

router = APIRouter(tags=["Coverage & Data Fusion"])


@router.get("/coverage/summary")
def get_coverage_summary(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Return comprehensive multi-source coverage statistics from genuine database records.

    Strictly separates:
    - Port existence in directory
    - Navigable routing support
    - Freight rate observations & monitored corridors
    - Forecast model readiness
    - Vessel position freshness
    - Commercial charter availability
    """
    # 1. Ports and Geographic Identifiers
    total_ports = db.query(Port).count()
    indian_ports = db.query(Port).filter(Port.country_code == "IN").count()
    us_ports = db.query(Port).filter(Port.country_code == "US").count()
    japan_ports = db.query(Port).filter(Port.country_code == "JP").count()

    ports_by_country_raw = (
        db.query(Port.country_code, Port.country, func.count(Port.id))
        .group_by(Port.country_code, Port.country)
        .order_by(func.count(Port.id).desc())
        .all()
    )
    ports_by_country = [
        {"country_code": r[0], "country": r[1], "count": r[2]}
        for r in ports_by_country_raw
    ]

    ports_by_region_raw = (
        db.query(Port.port_region, func.count(Port.id))
        .group_by(Port.port_region)
        .order_by(func.count(Port.id).desc())
        .all()
    )
    ports_by_region = [
        {"region": r[0] or "Unassigned", "count": r[1]}
        for r in ports_by_region_raw
    ]
    total_aliases = db.query(PortAlias).count()

    # 2. Navigational Routing
    routing_info = {
        "supported_modes": ["prototype", "marnet"],
        "default_mode": settings.routing_mode,
        "marnet_network": "Eurostat SeaRoute 50km generalized sea-lane graph (~4,000 nodes, ~10,000 edges)",
        "antimeridian_handling": "Split into disjoint polylines across 180° meridian to prevent visual Cesium wrap artifacts",
        "chokepoints_modeled": ["Panama Canal", "Suez Canal", "Strait of Malacca", "Strait of Juan de Fuca", "Tokyo Wan / Uraga Channel"],
        "unnavigable_routes": "Inland or landlocked water bodies (e.g. Caspian Sea / Baku) explicitly return unsupported_route.",
    }

    # 3. Freight Corridors & Rate Observations
    freight_routes = db.query(FreightRoute).all()
    freight_obs_count = db.query(FreightObservation).count()
    min_freight_date = db.query(func.min(FreightObservation.observation_date)).scalar()
    max_freight_date = db.query(func.max(FreightObservation.observation_date)).scalar()

    corridors_monitored = [
        {
            "route_code": r.route_code,
            "route_name": r.route_name,
            "origin_region": r.origin_region,
            "destination_region": r.destination_region,
            "cargo_type": r.cargo_type,
            "vessel_class": r.vessel_class,
            "unit": r.unit_of_measure,
            "observation_count": db.query(FreightObservation).filter(FreightObservation.route_code == r.route_code).count(),
        }
        for r in freight_routes
    ]

    # 4. Forecasting Models
    forecasting_info = {
        "active_runtime_default": "persistence",
        "certified_baselines": ["persistence", "ma3", "ma6", "linear_trend_6"],
        "experimental_candidates": ["ridge_ar"],
        "horizon": "T+1 (monthly)",
        "status_label": "Deterministic Persistence Baseline (certified out-of-sample)",
        "limitation": "Experimental Ridge AR candidate requires further held-out walk-forward empirical validation before production promotion.",
    }

    # 5. Vessels and AIS Telemetry
    total_vessels = db.query(Vessel).count()
    vessel_positions_count = db.query(VesselPosition).count()
    min_vessel_time = db.query(func.min(VesselPosition.observation_time)).scalar()
    max_vessel_time = db.query(func.max(VesselPosition.observation_time)).scalar()

    vessels_info = {
        "canonical_registered_vessels": total_vessels,
        "position_observations_count": vessel_positions_count,
        "telemetry_mode": "historical",
        "freshness": "historical",
        "is_live": False,
        "live_ais_connected": False,
        "earliest_observation": min_vessel_time.isoformat() if min_vessel_time else None,
        "latest_observation": max_vessel_time.isoformat() if max_vessel_time else None,
        "commercial_charter_availability": "unavailable",
        "commercial_charter_note": "Candidate vessel suitability screening (OPT-002) is operational based on physical dimensions, but commercial charter availability requires verified commercial fixture feeds.",
        "source": "NOAA MarineCadastre / USCG NAIS",
    }

    # 6. Trade and Commodity Flows
    trade_count = db.query(TradeObservation).count()
    trade_partners = (
        db.query(TradeObservation.reporter_iso2, TradeObservation.partner_iso2, func.count(TradeObservation.id))
        .group_by(TradeObservation.reporter_iso2, TradeObservation.partner_iso2)
        .all()
    )
    trade_flows_summary = [
        {"reporter": r[0], "partner": r[1], "observation_count": r[2]}
        for r in trade_partners
    ]

    # 7. Environmental & Oceanographic Buoys
    # 7. Environmental & Oceanographic Buoys
    env_count = db.query(EnvironmentalObservation).count()
    env_stations = (
        db.query(EnvironmentalObservation.station_id, EnvironmentalObservation.station_name, EnvironmentalObservation.sea_area, func.count(EnvironmentalObservation.id))
        .group_by(EnvironmentalObservation.station_id, EnvironmentalObservation.station_name, EnvironmentalObservation.sea_area)
        .all()
    )
    env_stations_summary = [
        {"station_id": r[0], "station_name": r[1], "sea_area": r[2], "observations": r[3]}
        for r in env_stations
    ]
    min_env_time = db.query(func.min(EnvironmentalObservation.observation_time)).scalar()
    max_env_time = db.query(func.max(EnvironmentalObservation.observation_time)).scalar()

    # Acquired vs Fixture breakdown for environmental
    env_acquired_count = (
        db.query(EnvironmentalObservation)
        .filter(EnvironmentalObservation.idempotency_key.like("NOAA:NDBC:%"))
        .count()
    )
    env_fixture_count = env_count - env_acquired_count

    # Ingestion runs audit breakdown
    failed_runs_count = db.query(IngestionRun).filter(IngestionRun.status == "failed").count()
    last_env_sync = (
        db.query(IngestionRun.completed_at)
        .filter(
            IngestionRun.source_name.like("%NDBC%"),
            IngestionRun.status.in_(["completed", "partial"]),
        )
        .order_by(IngestionRun.id.desc())
        .first()
    )
    last_trade_sync = (
        db.query(IngestionRun.completed_at)
        .filter(
            or_(
                IngestionRun.source_partition == "trade_observations",
                IngestionRun.source_name == "USDA FAS GATS / Indian DGCI&S",
                IngestionRun.source_name.like("%USDA%"),
                IngestionRun.source_name.like("%DGCI%"),
                IngestionRun.source_name.like("%Trade%"),
            ),
            IngestionRun.status.in_(["completed", "partial"]),
        )
        .order_by(IngestionRun.id.desc())
        .first()
    )
    last_port_sync = (
        db.query(IngestionRun.completed_at)
        .filter(
            IngestionRun.source_name.like("%UNECE%"),
            IngestionRun.status.in_(["completed", "partial"]),
        )
        .order_by(IngestionRun.id.desc())
        .first()
    )
    last_vessel_sync = (
        db.query(IngestionRun.completed_at)
        .filter(
            IngestionRun.source_name.like("%MarineCadastre%"),
            IngestionRun.status.in_(["completed", "partial"]),
        )
        .order_by(IngestionRun.id.desc())
        .first()
    )

    # Determine environmental freshness
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    env_freshness = "historical"
    if max_env_time:
        max_env_dt = max_env_time.replace(tzinfo=datetime.timezone.utc) if max_env_time.tzinfo is None else max_env_time
        diff_sec = (now_utc - max_env_dt).total_seconds()
        if diff_sec < 172800:
            env_freshness = "current"
        elif diff_sec < 604800:
            env_freshness = "recent"

    # 8. Source Acquisition & Freshness Governance Matrix (Stage B)
    source_acquisition_matrix = {
        "environmental_buoys": {
            "source_name": "NOAA National Data Buoy Center (NDBC)",
            "integration_status": "automated_working",
            "endpoint": "https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt",
            "curated_fixture_records": env_fixture_count,
            "acquired_external_records": env_acquired_count,
            "total_records": env_count,
            "freshness": env_freshness,
            "latest_observation": max_env_time.isoformat() if max_env_time else None,
            "last_successful_sync": last_env_sync[0].isoformat() if last_env_sync and last_env_sync[0] else None,
            "failed_refresh_attempts": db.query(IngestionRun).filter(IngestionRun.source_name.like("%NDBC%"), IngestionRun.status == "failed").count(),
            "notes": "Automated standard meteorological realtime retrieval active for monitored stations.",
        },
        "bilateral_trade": {
            "source_name": "USDA FAS GATS / Indian DGCI&S",
            "integration_status": "requires_credentials",
            "endpoint": "https://apps.fas.usda.gov/OpenData/api/gats/v1/trade",
            "curated_fixture_records": trade_count,
            "acquired_external_records": 0,
            "total_records": trade_count,
            "freshness": "historical",
            "latest_observation": (
                db.query(func.max(TradeObservation.period_date)).scalar().isoformat()
                if trade_count > 0 and db.query(func.max(TradeObservation.period_date)).scalar() else None
            ),
            "last_successful_sync": last_trade_sync[0].isoformat() if last_trade_sync and last_trade_sync[0] else None,
            "failed_refresh_attempts": db.query(IngestionRun).filter(
                or_(
                    IngestionRun.source_partition == "trade_observations",
                    IngestionRun.source_name == "USDA FAS GATS / Indian DGCI&S",
                    IngestionRun.source_name.like("%USDA%"),
                    IngestionRun.source_name.like("%DGCI%"),
                    IngestionRun.source_name.like("%Trade%"),
                ),
                IngestionRun.status == "failed",
            ).count(),
            "notes": "Automated retrieval requires USDA FAS OpenData API key (HTTP 403 without key); DGCI&S is published as periodic bulk portal downloads. Trade unit values reflect commodity pricing and are NOT freight rates.",
        },
        "ports_directory": {
            "source_name": "UNECE UN/LOCODE / NGA World Port Index",
            "integration_status": "manual_download_only",
            "endpoint": "https://service.unece.org/trade/locode/",
            "curated_fixture_records": total_ports,
            "acquired_external_records": 0,
            "total_records": total_ports,
            "total_aliases": total_aliases,
            "freshness": "historical",
            "last_successful_sync": last_port_sync[0].isoformat() if last_port_sync and last_port_sync[0] else None,
            "failed_refresh_attempts": db.query(IngestionRun).filter(IngestionRun.source_name.like("%UNECE%"), IngestionRun.status == "failed").count(),
            "notes": "Official UNECE releases distributed bi-annually as downloadable zip archives; automated scraping blocked by bot protection. Non-destructive reconciliation preserves existing canonical ports.",
        },
        "vessels_and_ais": {
            "source_name": "NOAA MarineCadastre / USCG NAIS",
            "integration_status": "manual_download_only",
            "endpoint": "https://coast.noaa.gov/htdata/CMSP/AISDataHandler/",
            "curated_fixture_records": total_vessels,
            "acquired_external_records": 0,
            "total_canonical_vessels": total_vessels,
            "total_positions_count": vessel_positions_count,
            "telemetry_mode": "historical",
            "freshness": "historical",
            "is_live": False,
            "live_ais_connected": False,
            "commercial_charter_availability": "unavailable",
            "last_successful_sync": last_vessel_sync[0].isoformat() if last_vessel_sync and last_vessel_sync[0] else None,
            "failed_refresh_attempts": db.query(IngestionRun).filter(IngestionRun.source_name.like("%MarineCadastre%"), IngestionRun.status == "failed").count(),
            "notes": "Bulk historical AIS data (~500MB/day) hosted as daily archives for offline ETL; no public live unauthenticated AIS stream. Vessel physical suitability screening does NOT imply spot charter party availability.",
        },
    }

    # 9. Separation of Coverage Types Mandate
    separation_statement = (
        "Pelagos enforces strict architectural separation of coverage types: "
        "(1) Directory presence does NOT imply freight monitoring; "
        "(2) Navigable routing does NOT imply an active freight forecast; "
        "(3) Freight rate benchmarks are strictly bounded to verified dry bulk grain export corridors; "
        "(4) Historical AIS positions remain explicitly labeled historical; "
        "(5) Vessel physical suitability does NOT imply spot commercial charter availability; "
        "(6) External data acquisition adapters do not imply live data unless connected to verified live feeds."
    )

    # 10. Ingestion Audit Runs
    ingestion_runs_count = db.query(IngestionRun).count()

    return {
        "status": "ok",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "separation_of_coverage_types": separation_statement,
        "port_directory": {
            "total_ports": total_ports,
            "indian_ports_count": indian_ports,
            "us_ports_count": us_ports,
            "japan_ports_count": japan_ports,
            "total_aliases": total_aliases,
            "ports_by_country": ports_by_country,
            "ports_by_region": ports_by_region,
            "sources": ["UNECE UN/LOCODE 2024-1", "NGA World Port Index (Pub. 150)", "Official Indian Major Ports Authority"],
            "decision_reconciliation": "OD-ROUT-06-03 and REQ-FR-CRI-012 implemented via canonical database ports directory and dynamic PORT_REGION_MAP expansion.",
        },
        "navigational_routing": routing_info,
        "freight_intelligence": {
            "monitored_corridors_count": len(corridors_monitored),
            "corridors": corridors_monitored,
            "total_observations": freight_obs_count,
            "cadence": "monthly",
            "earliest_observation": min_freight_date.isoformat() if min_freight_date else None,
            "latest_observation": max_freight_date.isoformat() if max_freight_date else None,
            "sources": ["USDA AMS AgTransport Ocean Bulk Vessel Rates (ehs5-yac3)"],
            "unmonitored_trade_lanes_policy": "Explicit status 'unmonitored' with zero rate fabrication.",
        },
        "forecasting": forecasting_info,
        "vessels_and_ais": vessels_info,
        "trade_and_commodities": {
            "total_observations": trade_count,
            "bilateral_flows": trade_flows_summary,
            "sources": [
                "USDA Foreign Agricultural Service (FAS) Global Agricultural Trade System (GATS)",
                "Directorate General of Commercial Intelligence and Statistics (DGCI&S), Ministry of Commerce and Industry, India",
            ],
        },
        "environmental_oceanographic": {
            "total_observations": env_count,
            "stations": env_stations_summary,
            "sources": [
                "NOAA National Data Buoy Center (NDBC)",
                "Indian National Centre for Ocean Information Services (INCOIS) / NIOT Deep Ocean Moored Buoy Network",
            ],
        },
        "source_acquisition": source_acquisition_matrix,
        "ingestion_audit": {
            "total_runs": ingestion_runs_count,
            "idempotency_enforced": True,
        },
        "known_gaps": [
            "Commercial freight rates are monitored strictly for U.S.-Japan dry bulk grain export corridors; Indian, European, and Asian coastal routes are unmonitored.",
            "AIS positions are genuine historical observations (Oct 2024 - 2026); real-time commercial vessel positions and spot availability require paid broker/satellite subscriptions.",
            "Navigational sea route distance is hydrographic approximation; certified voyage passage planning requires official ECDIS nautical charts and licensed pilotage.",
        ],
    }


@router.get("/trade/observations")
def list_trade_observations(
    reporter_iso2: Optional[str] = Query(None, description="2-letter ISO country code of reporter"),
    partner_iso2: Optional[str] = Query(None, description="2-letter ISO country code of partner"),
    commodity_code: Optional[str] = Query(None, description="HS commodity classification code"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Return genuine point-in-time bilateral maritime trade observations."""
    query = db.query(TradeObservation)
    if reporter_iso2:
        query = query.filter(TradeObservation.reporter_iso2 == reporter_iso2.strip().upper())
    if partner_iso2:
        query = query.filter(TradeObservation.partner_iso2 == partner_iso2.strip().upper())
    if commodity_code:
        query = query.filter(TradeObservation.commodity_code == commodity_code.strip())

    records = query.order_by(TradeObservation.period_date.desc()).limit(limit).all()
    return [
        {
            "reporter_country": r.reporter_country,
            "reporter_iso2": r.reporter_iso2,
            "partner_country": r.partner_country,
            "partner_iso2": r.partner_iso2,
            "commodity_code": r.commodity_code,
            "commodity_name": r.commodity_name,
            "trade_flow": r.trade_flow,
            "period_date": r.period_date.isoformat(),
            "period_type": r.period_type,
            "volume_metric_tons": r.volume_metric_tons,
            "trade_value_usd": r.trade_value_usd,
            "unit_price_usd_per_mt": r.unit_price_usd_per_mt,
            "source_name": r.source_name,
            "license_type": r.license_type,
        }
        for r in records
    ]


@router.get("/environmental/observations")
def list_environmental_observations(
    station_id: Optional[str] = Query(None, description="Buoy station identifier (e.g. 46041, 42001, AD01)"),
    sea_area: Optional[str] = Query(None, description="Filter by maritime sea area"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Return authentic oceanographic buoy and marine meteorological observations."""
    query = db.query(EnvironmentalObservation)
    if station_id:
        query = query.filter(EnvironmentalObservation.station_id == station_id.strip())
    if sea_area:
        query = query.filter(EnvironmentalObservation.sea_area.ilike(f"%{sea_area.strip()}%"))

    records = query.order_by(EnvironmentalObservation.observation_time.desc()).limit(limit).all()
    return [
        {
            "station_id": r.station_id,
            "station_name": r.station_name,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "sea_area": r.sea_area,
            "observation_time": r.observation_time.isoformat(),
            "wave_height_meters": r.wave_height_meters,
            "wave_period_seconds": r.wave_period_seconds,
            "wind_speed_knots": r.wind_speed_knots,
            "wind_direction_degrees": r.wind_direction_degrees,
            "sea_surface_temp_c": r.sea_surface_temp_c,
            "air_pressure_hpa": r.air_pressure_hpa,
            "source_name": r.source_name,
            "license_type": r.license_type,
            "quality_status": r.quality_status,
        }
        for r in records
    ]
