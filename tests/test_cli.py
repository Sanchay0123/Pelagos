"""Focused tests for Pelagos CLI subcommands, dry-run mode, and parameter validation."""

import pytest
import json
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.cli import build_parser, handle_ingest_sample, handle_ingest_remote, handle_status, handle_coverage
from pelagos.models import VesselPosition, IngestionRun
from pelagos.ingestion.marinecadastre import PartitionNotFoundError, NetworkIngestionError


@pytest.fixture
def cli_db_url(tmp_path):
    db_file = tmp_path / "cli_test.db"
    return f"sqlite:///{db_file}"


def test_cli_ingest_sample_and_idempotency(cli_db_url, capsys):
    """Running ingest-sample populates 120 authentic records; re-running detects 120 duplicates."""
    parser = build_parser()

    # First run: fresh ingestion
    args1 = parser.parse_args(["ingest-sample", "--db-url", cli_db_url])
    code1 = handle_ingest_sample(args1)
    assert code1 == 0

    out1 = json.loads(capsys.readouterr().out)
    assert out1["accepted_count"] == 120
    assert out1["duplicate_count"] == 0
    assert out1["quarantined_count"] == 0
    assert out1["status"] == "completed"
    assert out1["is_dry_run"] is False
    assert out1["run_id"] is not None

    # Second run: identical sample should be 100% deduplicated
    args2 = parser.parse_args(["ingest-sample", "--db-url", cli_db_url])
    code2 = handle_ingest_sample(args2)
    assert code2 == 0

    out2 = json.loads(capsys.readouterr().out)
    assert out2["accepted_count"] == 0
    assert out2["duplicate_count"] == 120
    assert out2["quarantined_count"] == 0
    assert out2["status"] == "completed"

    # Verify database has exactly 120 records
    engine = create_engine(cli_db_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    count = session.query(VesselPosition).count()
    assert count == 120

    # IngestionRun audit table records both runs
    runs = session.query(IngestionRun).all()
    assert len(runs) == 2
    session.close()


def test_cli_dry_run_mode(cli_db_url, capsys):
    """
    Guardrail 1: Dry-run validates without database mutation and does not log an IngestionRun.
    """
    parser = build_parser()
    args = parser.parse_args(["ingest-sample", "--dry-run", "--db-url", cli_db_url])
    code = handle_ingest_sample(args)
    assert code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["accepted_count"] == 120
    assert out["is_dry_run"] is True
    assert out["status"] == "dry_run"
    assert out["run_id"] is None

    # Zero rows in vessel_positions and zero rows in ingestion_runs
    engine = create_engine(cli_db_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    assert session.query(VesselPosition).count() == 0
    assert session.query(IngestionRun).count() == 0
    session.close()


def test_cli_remote_date_validation(cli_db_url, capsys):
    """Dates outside the verified 2024 catalog are rejected client-side with clean JSON error."""
    parser = build_parser()
    args = parser.parse_args(["ingest-remote", "--date", "2025-01-01", "--db-url", cli_db_url])
    code = handle_ingest_remote(args)
    assert code == 1

    err = json.loads(capsys.readouterr().err)
    assert err["status"] == "failed"
    assert "outside verified 2024 catalog" in err["error"]


def test_cli_remote_network_failure_handling(cli_db_url, capsys):
    """Network connection failure outputs clean JSON error, exit code 1, and records failed IngestionRun."""
    parser = build_parser()
    args = parser.parse_args(["ingest-remote", "--date", "2024-01-01", "--db-url", cli_db_url])

    with patch("pelagos.cli.fetch_remote_noaa_slice", side_effect=NetworkIngestionError("Connection timed out after 30s")):
        code = handle_ingest_remote(args)
        assert code == 1

    err = json.loads(capsys.readouterr().err)
    assert err["status"] == "failed"
    assert "Connection timed out" in err["error"]

    # IngestionRun table records failed run
    engine = create_engine(cli_db_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    failed_run = session.query(IngestionRun).filter(IngestionRun.status == "failed").first()
    assert failed_run is not None
    assert failed_run.source_partition == "ais-2024-01-01.parquet"
    session.close()


def test_cli_status_and_coverage(cli_db_url, capsys):
    """CLI status and coverage commands execute cleanly and output JSON."""
    parser = build_parser()

    # Seed sample first
    args_seed = parser.parse_args(["ingest-sample", "--db-url", cli_db_url])
    assert handle_ingest_sample(args_seed) == 0
    capsys.readouterr()  # flush output

    # Status command
    args_status = parser.parse_args(["status", "--db-url", cli_db_url])
    assert handle_status(args_status) == 0
    status_out = json.loads(capsys.readouterr().out)
    assert status_out["total_observations"] == 120
    assert len(status_out["recent_runs"]) >= 1

    # Coverage command
    args_cov = parser.parse_args(["coverage", "--db-url", cli_db_url])
    assert handle_coverage(args_cov) == 0
    cov_out = json.loads(capsys.readouterr().out)
    assert cov_out["status"] == "available"
    assert cov_out["spatial_extent"] is not None
    assert cov_out["temporal_extent"]["temporal_mode"] == "historical"


def test_cli_ingest_sample_failure_exit_code(cli_db_url, capsys):
    """
    Review Fix 2: When ingest-sample experiences a failure, it returns a non-zero exit code.
    """
    from pelagos.ingestion.schemas import IngestionResult

    parser = build_parser()
    args = parser.parse_args(["ingest-sample", "--db-url", cli_db_url])

    failed_result = IngestionResult(
        source_partition="bundled-sample-2024-01-01",
        total_received=120,
        records_read=120,
        accepted_count=0,
        quarantined_count=0,
        duplicate_count=0,
        unparseable_count=0,
        failed_count=120,
        status="failed",
        is_dry_run=False,
        run_id=None,
        error_details="Simulated database lock failure",
    )

    with patch("pelagos.cli.IngestionService.ingest_batch", return_value=failed_result):
        code = handle_ingest_sample(args)
        assert code == 1

    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "failed"
    assert out["failed_count"] == 120


def test_cli_remote_success_mocked(cli_db_url, capsys):
    """
    Review Fix 5: Deterministic offline test asserting exit code 0 and valid JSON
    output for successful ingest-remote execution.
    """
    parser = build_parser()
    args = parser.parse_args([
        "ingest-remote",
        "--date", "2024-01-01",
        "--limit", "10",
        "--row-group", "0",
        "--db-url", cli_db_url,
    ])

    mock_record = {
        "mmsi": "367999888",
        "vessel_name": "REMOTE CLI TEST SHIP",
        "vessel_type": "Cargo",
        "latitude": 29.12345,
        "longitude": -89.54321,
        "speed_knots": 14.5,
        "observation_time": "2024-01-01T08:00:00Z",
        "source_name": "NOAA MarineCadastre / USCG NAIS",
        "source_partition": "ais-2024-01-01.parquet",
    }

    with patch("pelagos.cli.fetch_remote_noaa_slice", return_value=([mock_record], 0)):
        code = handle_ingest_remote(args)
        assert code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "completed"
    assert out["accepted_count"] == 1
    assert out["records_read"] == 1
    assert out["source_partition"] == "ais-2024-01-01.parquet"
    assert out["is_dry_run"] is False
