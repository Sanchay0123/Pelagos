"""Health check endpoints for service liveness and database status."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text, func

from pelagos.database import get_db
from pelagos.models import VesselPosition
from pelagos.config import settings

router = APIRouter(tags=["Health"])


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """Liveness probe reporting database connectivity and vessel count."""
    db_status = "connected"
    vessel_count = 0
    try:
        db.execute(text("SELECT 1"))
        vessel_count = db.query(func.count(VesselPosition.id)).scalar() or 0
    except Exception as e:
        db_status = f"unhealthy: {e}"

    return {
        "status": "ok" if "unhealthy" not in db_status else "degraded",
        "service": "pelagos-backend",
        "version": settings.app_version,
        "database": db_status,
        "active_vessels_in_store": vessel_count,
        "safety_disclaimer": settings.safety_disclaimer,
    }
