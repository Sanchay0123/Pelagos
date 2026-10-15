"""Configuration settings for Pelagos platform."""

import os
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Pelagos Maritime Intelligence Platform"
    app_version: str = "0.1.0"
    debug: bool = False
    
    # Database URL: defaults to SQLite for reproducible zero-config local run,
    # or connects to PostgreSQL/PostGIS when configured via PELAGOS_DATABASE_URL.
    database_url: str = os.getenv(
        "PELAGOS_DATABASE_URL", 
        "sqlite:///./pelagos.db"
    )
    
    # Server network bindings
    host: str = os.getenv("PELAGOS_HOST", "127.0.0.1")
    port: int = int(os.getenv("PELAGOS_PORT", "8000"))
    
    # CORS policy allowing Vite / Cesium client origins
    cors_origins: List[str] = [
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    
    # Data governance & provenance metadata
    default_source_name: str = "NOAA MarineCadastre / USCG NAIS"
    default_source_attribution: str = "Data provided by MarineCadastre.gov, NOAA, BOEM, and the U.S. Coast Guard."
    default_license: str = "U.S. Government Work / Public Domain (17 U.S.C. § 105)"
    default_coverage: str = "US Coastal and Territorial Waters (Verified Genuine Observations)"
    safety_disclaimer: str = (
        "Pelagos is NOT a navigation-grade safety system. "
        "Not for live maritime collision avoidance or SOLAS regulatory compliance."
    )
    
    model_config = SettingsConfigDict(
        env_prefix="PELAGOS_",
        extra="ignore"
    )


settings = Settings()
