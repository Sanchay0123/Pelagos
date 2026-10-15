from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from pelagos.database import get_db
from pelagos.ingestion.service import IngestionService
from pelagos.ingestion.marinecadastre import load_genuine_sample_records
from pelagos.ingestion.schemas import IngestionResult, CoverageSummaryResponse, IngestionRunSummary
from pelagos.ingestion.coverage import compute_coverage_summary

router = APIRouter(tags=["Ingestion"])


@router.post("/ingestion/load-sample", response_model=IngestionResult)
def load_sample_dataset(
    dry_run: bool = Query(False, description="Validate without writing records or logging an IngestionRun"),
    db: Session = Depends(get_db)
):
    """
    Ingest curated authentic vessel telemetry from NOAA MarineCadastre / USCG NAIS.
    Guarantees zero synthetic observations and enforces multi-dimensional UTC temporal tracking.
    """
    try:
        sample_records = load_genuine_sample_records()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load sample dataset: {e}")

    service = IngestionService(db)
    return service.ingest_batch(
        sample_records,
        source_partition="bundled-sample-2024-01-01",
        run_type="bundled",
        dry_run=dry_run
    )


@router.post("/ingestion/ingest", response_model=IngestionResult)
def ingest_telemetry_batch(
    payload: List[Dict[str, Any]],
    source_partition: str = Query("api-batch", description="Partition or batch identifier"),
    dry_run: bool = Query(False, description="Validate without writing records"),
    db: Session = Depends(get_db)
):
    """
    Ingest an arbitrary batch of maritime telemetry records.
    Validates coordinates, kinematics, timestamps, and quarantines anomalies.
    """
    if not payload:
        raise HTTPException(status_code=400, detail="Empty payload received")

    service = IngestionService(db)
    return service.ingest_batch(
        payload,
        source_partition=source_partition,
        run_type="api",
        dry_run=dry_run
    )


@router.get("/ingestion/coverage", response_model=CoverageSummaryResponse)
def get_coverage_summary(db: Session = Depends(get_db)):
    """
    Computes spatial bounding box, temporal observation bounds, partition list,
    and honest coverage disclaimers from actual database state.
    """
    return compute_coverage_summary(db)


@router.get("/ingestion/runs", response_model=List[IngestionRunSummary])
def list_ingestion_runs(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """List historical ingestion execution runs, counters, and status."""
    service = IngestionService(db)
    return service.get_ingestion_runs(limit=limit)


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
