"""Focused API tests for endpoints, GEV snapshot compatibility, and error responses."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pelagos.database import Base, get_db
from pelagos.api.main import app
from pelagos.ingestion.service import IngestionService
from pelagos.ingestion.marinecadastre import load_genuine_sample_records


@pytest.fixture
def client_and_db():
    engine = create_engine(
        "sqlite:///:memory:", 
        connect_args={"check_same_thread": False}, 
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    
    with TestClient(app) as client:
        session = TestingSession()
        yield client, session
        session.close()

    app.dependency_overrides.clear()


def test_health_endpoint(client_and_db):
    client, _ = client_and_db
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "Pelagos is NOT a navigation-grade safety system" in data["safety_disclaimer"]


def test_empty_snapshot_response(client_and_db):
    """Empty database must return a clean empty state without throwing."""
    client, _ = client_and_db
    response = client.get("/api/v1/vessels/snapshot")
    assert response.status_code == 200
    data = response.json()
    assert data["rows"] == []
    assert data["total_records"] == 0
    assert data["status"] == "historical"
    assert data["data_mode"] == "historical"
    assert data["is_live"] is False
    assert data["live_ais_connected"] is False
    assert data["backend_connected"] is True
    assert data["freshness"] == "unknown"
    assert "NOAA MarineCadastre" in data["source"]
    assert "CC0 1.0 Universal" in data["license"]


def test_snapshot_with_populated_vessels(client_and_db):
    """Verify integration contract expected by God's Eye View vesselSnapshot parser."""
    client, session = client_and_db
    service = IngestionService(session)
    sample_records = load_genuine_sample_records()[:20]
    service.ingest_batch(sample_records)

    response = client.get("/api/v1/vessels/snapshot")
    assert response.status_code == 200
    data = response.json()
    assert len(data["rows"]) == 20
    assert data["status"] == "historical"
    assert data["data_mode"] == "historical"
    assert data["is_live"] is False
    assert data["live_ais_connected"] is False
    assert data["backend_connected"] is True
    assert data["freshness"] == "historical"
    assert data["observation_date"] == "2024-01-01"
    assert data["newestPositionAt"] is not None
    assert data["newestPositionAt"].startswith("2024-01-01")

    # Check row fields needed by GEV
    row0 = data["rows"][0]
    assert "mmsi" in row0
    assert "lat" in row0
    assert "lon" in row0
    assert "name" in row0
    assert "type_specific" in row0
    assert "last_position_UTC" in row0
    assert "last_position_epoch" in row0
    assert row0["historical"] is True


def test_historical_status_and_live_ais_distinction(client_and_db):
    """
    A successful backend connection must not imply current AIS connectivity.
    Historical 2024-01-01 data must explicitly report historical status,
    preserve provenance, and keep live_ais_connected=False.
    """
    client, session = client_and_db
    service = IngestionService(session)
    sample_records = load_genuine_sample_records()[:10]
    service.ingest_batch(sample_records)

    response = client.get("/api/v1/vessels/snapshot")
    assert response.status_code == 200
    data = response.json()

    # Backend connection succeeded, but live AIS is NOT connected
    assert data["backend_connected"] is True
    assert data["live_ais_connected"] is False
    assert data["is_live"] is False
    assert data["status"] == "historical"
    assert data["data_mode"] == "historical"
    assert data["freshness"] == "historical"
    assert data["observation_date"] == "2024-01-01"

    # Genuine provenance and timestamps preserved
    assert data["source"] == "NOAA MarineCadastre / USCG NAIS"
    assert data["license"] == "CC0 1.0 Universal"
    for r in data["rows"]:
        assert r["last_position_UTC"].startswith("2024-01-01")
        assert r["historical"] is True


def test_vessel_filter_and_detail(client_and_db):
    client, session = client_and_db
    service = IngestionService(session)
    sample_records = load_genuine_sample_records()[:30]
    service.ingest_batch(sample_records)

    # Filter by vessel type
    towing_resp = client.get("/api/v1/vessels/snapshot?vessel_type=Towing")
    assert towing_resp.status_code == 200
    towing_data = towing_resp.json()
    for r in towing_data["rows"]:
        assert "Towing" in r["type_specific"]

    # Detail endpoint
    mmsi = sample_records[0]["mmsi"]
    detail_resp = client.get(f"/api/v1/vessels/{mmsi}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["mmsi"] == mmsi
    assert detail["vessel_name"] == sample_records[0]["vessel_name"]
    assert detail["data_mode"] == "historical"
    assert detail["is_live"] is False
    assert detail["freshness"] == "historical"

    # Unknown MMSI 404
    not_found = client.get("/api/v1/vessels/999999999")
    assert not_found.status_code == 404


def test_backward_compatibility_endpoint(client_and_db):
    """The /api/ais-live endpoint must respond identically for God's Eye View client."""
    client, session = client_and_db
    service = IngestionService(session)
    service.ingest_batch(load_genuine_sample_records()[:5])

    response = client.get("/api/ais-live")
    assert response.status_code == 200
    data = response.json()
    assert len(data["rows"]) == 5
    assert data["status"] == "historical"
    assert data["data_mode"] == "historical"
    assert data["is_live"] is False
    assert data["backend_connected"] is True
    assert data["live_ais_connected"] is False


def test_ingestion_api_endpoints(client_and_db):
    client, _ = client_and_db

    # Ingest a valid and invalid record via API
    batch = [
        {
            "mmsi": "367888999",
            "vessel_name": "CARGO SHIP 1",
            "latitude": 32.0,
            "longitude": -80.0,
            "observation_time": "2024-01-01T12:00:00Z",
        },
        {
            "mmsi": "367000111",
            "vessel_name": "ANOMALOUS",
            "latitude": 99.0,  # Invalid latitude
            "longitude": -80.0,
            "observation_time": "2024-01-01T12:00:00Z",
        }
    ]
    resp = client.post("/api/v1/ingestion/ingest", json=batch)
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["accepted_count"] == 1
    assert res_data["quarantined_count"] == 1

    # Check quarantine endpoint
    q_resp = client.get("/api/v1/ingestion/quarantine")
    assert q_resp.status_code == 200
    q_list = q_resp.json()
    assert len(q_list) >= 1
    assert any(q["error_code"] == "ANOMALY_INVALID_COORDINATES" for q in q_list)
