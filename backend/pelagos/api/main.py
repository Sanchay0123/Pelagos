"""Main FastAPI application for Pelagos Maritime Intelligence Platform."""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from pelagos.config import settings
from pelagos.database import init_db, get_db
from pelagos.api.routes import health, vessels, ingestion, analytics, freight, routing


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables and additive schema compatibility; zero network or auto-seeding on startup
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        f"{settings.app_name} backend.\n\n"
        f"**Safety Disclaimer:** {settings.safety_disclaimer}"
    ),
    lifespan=lifespan,
)

# CORS middleware for local frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Canonical API v1 routes
app.include_router(health.router, prefix="/api/v1")
app.include_router(vessels.router, prefix="/api/v1")
app.include_router(ingestion.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(freight.router, prefix="/api/v1")
app.include_router(routing.router, prefix="/api/v1")

# Convenience root aliases
app.include_router(health.router)

# Direct compatibility alias for existing God's Eye View client
@app.get("/api/ais-live", tags=["Compatibility"])
def ais_live_compatibility_endpoint(
    limit: int = 500,
    vessel_type: str = None,
    min_lat: float = None,
    max_lat: float = None,
    min_lon: float = None,
    max_lon: float = None,
    db: Session = Depends(get_db)
):
    """Direct alias for /api/v1/vessels/snapshot for seamless God's Eye View integration."""
    return vessels.get_vessels_snapshot(
        limit=limit,
        vessel_type=vessel_type,
        min_lat=min_lat,
        max_lat=max_lat,
        min_lon=min_lon,
        max_lon=max_lon,
        db=db,
    )


@app.get("/", tags=["Root"])
def root_info():
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "docs_url": "/docs",
        "api_v1_snapshot": "/api/v1/vessels/snapshot",
        "provenance": {
            "source": settings.default_source_name,
            "license": settings.default_license,
            "coverage": settings.default_coverage,
        },
        "safety_disclaimer": settings.safety_disclaimer,
    }


def run():
    """CLI entrypoint to launch uvicorn."""
    import uvicorn
    uvicorn.run(
        "pelagos.api.main:app", 
        host=settings.host, 
        port=settings.port, 
        reload=settings.debug
    )


if __name__ == "__main__":
    run()
