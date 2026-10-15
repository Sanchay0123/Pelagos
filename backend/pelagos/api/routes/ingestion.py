"""Ingestion endpoints for data loading, batch submission, and quarantine audit."""

from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from pelagos.database import get_db
from pelagos.ingestion.service import IngestionService
from pelagos.ingestion.marinecadastre import load_genuine_sample_records
from pelagos.ingestion.schemas import IngestionResult

router = APIRouter(tags=["Ingestion"])


@router.post("/ingestion/load-sample", response_model=IngestionResult)
def load_sample_dataset(db: Session = Depends(get_db)):
    """
    Ingest curated authentic vessel telemetry from NOAA MarineCadastre / USCG NAIS.
    Guarantees zero synthetic observations and enforces multi-dimensional UTC temporal tracking.
    """
    try:
        sample_records = load_genuine_sample_records()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load sample dataset: {e}")

    service = IngestionService(db)
    return service.ingest_batch(sample_records)


@router.post("/ingestion/ingest", response_model=IngestionResult)
def ingest_telemetry_batch(
    payload: List[Dict[str, Any]], 
    db: Session = Depends(get_db)
):
    """
    Ingest an arbitrary batch of maritime telemetry records.
    Validates coordinates, kinematics, timestamps, and quarantines anomalies.
    """
    if not payload:
        raise HTTPException(status_code=400, detail="Empty payload received")

    service = IngestionService(db)
    return service.ingest_batch(payload)


@router.get("/ingestion/quarantine")
def list_quarantined_records(
    limit: int = Query(50, ge=1, le=500), 
    db: Session = Depends(get_db)
):
    """List recently quarantined anomalous records and their failure reasons."""
    service = IngestionService(db)
    return service.get_quarantine_records(limit=limit)


@router.get("/ingestion/status")
def get_ingestion_statistics(db: Session = Depends(get_db)):
    """Summary of ingested observations, distinct vessels, and quarantine counts."""
    service = IngestionService(db)
    return service.get_ingestion_status()
