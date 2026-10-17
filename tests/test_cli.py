"""Focused tests for Pelagos CLI subcommands, dry-run mode, and parameter validation."""

import pytest
import json
from unittest.mock import patch
from sqlalchemy import create_engine, text, event, inspect
from sqlalchemy.orm import sessionmaker

from pelagos.cli import (
    build_parser,
    handle_ingest_sample,
    handle_ingest_remote,
    handle_status,
    handle_coverage,
    handle_freight_forecast_baseline,
)
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


@pytest.fixture
def freight_cli_fixture(tmp_path):
    """Provides isolated SQLite database and published Parquet storage for freight CLI tests."""
    from datetime import date
    from pelagos.models import Base, FreightObservation
    from pelagos.ingestion.freight_usda import load_usda_vessel_rates_sample, normalize_usda_record
    from pelagos.storage.freight_writer import write_freight_partition_parquet, commit_freight_publication

    db_path = tmp_path / "test_freight_cli.db"
    storage_dir = tmp_path / "parquet_storage"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Ingest 24 fixture records
    raw_fixture = load_usda_vessel_rates_sample()
    for raw in raw_fixture:
        records, _ = normalize_usda_record(raw)
        for r in records:
            obs = FreightObservation(
                route_code=r.route_code,
                observation_date=r.observation_date,
                rate_value=r.rate_value,
                currency=r.currency,
                unit_of_measure=r.unit_of_measure,
                is_derived=r.is_derived,
                observation_period=r.observation_period,
                source_name=r.source_name,
                source_dataset_id=r.source_dataset_id,
                license_type=r.license_type,
                raw_payload=r.raw_payload,
                idempotency_key=r.idempotency_key,
            )
            session.add(obs)
    session.commit()

    # Publish Parquet partitions for 2024, 2025, 2026
    for yr in [2024, 2025, 2026]:
        obs = session.query(FreightObservation).filter(
            FreightObservation.observation_date >= date(yr, 1, 1),
            FreightObservation.observation_date <= date(yr, 12, 31),
        ).all()
        if obs:
            p, count, size, sha = write_freight_partition_parquet(
                records=obs, base_dir=storage_dir, year=yr, generation=1
            )
            commit_freight_publication(
                db=session,
                year=yr,
                expected_generation=None,
                published_path=p,
                record_count=count,
                file_size_bytes=size,
                content_sha256=sha,
                snapshot_watermark_id=max(o.id for o in obs),
            )

    db_url = f"sqlite:///{db_path.as_posix()}"
    yield session, engine, db_url, storage_dir
    session.close()


def test_cli_forecast_baseline_both_routes_and_default_model(freight_cli_fixture, capsys):
    """Verifies that both direct routes execute successfully with default persistence model in table format."""
    _, _, db_url, storage_dir = freight_cli_fixture
    parser = build_parser()

    # 1. USG-JPN-BULK default
    args_usg = parser.parse_args([
        "freight-forecast-baseline",
        "--route-code", "USG-JPN-BULK",
        "--db-url", db_url,
        "--storage-dir", str(storage_dir),
    ])
    code_usg = handle_freight_forecast_baseline(args_usg)
    assert code_usg == 0
    out_usg = capsys.readouterr().out
    assert "USG-JPN-BULK" in out_usg
    assert "Model: persistence (default)" in out_usg
    assert "Forecast Status : forecast_generated" in out_usg
    assert "Predicted Rate  : 76.3800 USD/MT" in out_usg
    assert "Origin Date     : 2026-09-01" in out_usg
    assert "Target Date     : 2026-10-01 (Horizon: 1 month(s))" in out_usg
    assert "24 observation(s) at origin" in out_usg

    # 2. PNW-JPN-BULK default
    args_pnw = parser.parse_args([
        "freight-forecast-baseline",
        "--route-code", "PNW-JPN-BULK",
        "--db-url", db_url,
        "--storage-dir", str(storage_dir),
    ])
    code_pnw = handle_freight_forecast_baseline(args_pnw)
    assert code_pnw == 0
    out_pnw = capsys.readouterr().out
    assert "PNW-JPN-BULK" in out_pnw
    assert "Predicted Rate  : 37.3100 USD/MT" in out_pnw


def test_cli_forecast_baseline_all_four_models(freight_cli_fixture, capsys):
    """Verifies each supported model produces exact predictions via CLI."""
    _, _, db_url, storage_dir = freight_cli_fixture
    parser = build_parser()

    expected_predictions = {
        "persistence": 76.38,
        "rolling_ma_3": 72.7567,
        "rolling_ma_6": 71.0117,
        "linear_trend_6": 76.7167,
    }

    for model_name, exp_pred in expected_predictions.items():
        args = parser.parse_args([
            "freight-forecast-baseline",
            "--route-code", "USG-JPN-BULK",
            "--model", model_name,
            "--format", "json",
            "--db-url", db_url,
            "--storage-dir", str(storage_dir),
        ])
        code = handle_freight_forecast_baseline(args)
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model_type"] == model_name
        assert payload["default_model_applied"] is False
        assert pytest.approx(payload["predicted_value"], abs=0.001) == exp_pred


def test_cli_forecast_baseline_origin_date_and_horizons_1_and_6(freight_cli_fixture, capsys):
    """Verifies explicit origin date filtering and multi-step horizons 1 and 6."""
    _, _, db_url, storage_dir = freight_cli_fixture
    parser = build_parser()

    # 1. Historical origin date 2025-06-01
    args_origin = parser.parse_args([
        "freight-forecast-baseline",
        "--route-code", "USG-JPN-BULK",
        "--origin-date", "2025-06-01",
        "--format", "json",
        "--db-url", db_url,
        "--storage-dir", str(storage_dir),
    ])
    assert handle_freight_forecast_baseline(args_origin) == 0
    payload_origin = json.loads(capsys.readouterr().out)
    assert payload_origin["origin_date"] == "2025-06-01"
    assert payload_origin["target_date"] == "2025-07-01"
    assert payload_origin["predicted_value"] == 46.88
    assert payload_origin["history_count_observed"] == 9

    # 2. Horizon 1
    args_h1 = parser.parse_args([
        "freight-forecast-baseline",
        "--route-code", "USG-JPN-BULK",
        "--horizon-months", "1",
        "--format", "json",
        "--db-url", db_url,
        "--storage-dir", str(storage_dir),
    ])
    assert handle_freight_forecast_baseline(args_h1) == 0
    payload_h1 = json.loads(capsys.readouterr().out)
    assert payload_h1["horizon_months"] == 1
    assert payload_h1["target_date"] == "2026-10-01"

    # 3. Horizon 6 (advancing to 2027-03-01 across year boundary)
    args_h6 = parser.parse_args([
        "freight-forecast-baseline",
        "--route-code", "USG-JPN-BULK",
        "--horizon-months", "6",
        "--format", "json",
        "--db-url", db_url,
        "--storage-dir", str(storage_dir),
    ])
    assert handle_freight_forecast_baseline(args_h6) == 0
    payload_h6 = json.loads(capsys.readouterr().out)
    assert payload_h6["horizon_months"] == 6
    assert payload_h6["target_date"] == "2027-03-01"


def test_cli_forecast_baseline_insufficient_history(freight_cli_fixture, capsys):
    """Verifies clear representation of insufficient history limitations without misleading rates."""
    _, _, db_url, storage_dir = freight_cli_fixture
    parser = build_parser()

    # Origin 2024-11-01 has 2 observations; MA-6 requires 6
    # 1. Table format
    args_table = parser.parse_args([
        "freight-forecast-baseline",
        "--route-code", "USG-JPN-BULK",
        "--model", "rolling_ma_6",
        "--origin-date", "2024-11-01",
        "--db-url", db_url,
        "--storage-dir", str(storage_dir),
    ])
    code_table = handle_freight_forecast_baseline(args_table)
    assert code_table == 0
    out_table = capsys.readouterr().out
    assert "Forecast Status : insufficient_history" in out_table
    assert "Predicted Rate  : N/A" in out_table
    assert "Limitations     : Rolling MA-6 requires at least 6 historical observations: got 2" in out_table

    # 2. JSON format
    args_json = parser.parse_args([
        "freight-forecast-baseline",
        "--route-code", "USG-JPN-BULK",
        "--model", "rolling_ma_6",
        "--origin-date", "2024-11-01",
        "--format", "json",
        "--db-url", db_url,
        "--storage-dir", str(storage_dir),
    ])
    code_json = handle_freight_forecast_baseline(args_json)
    assert code_json == 0
    payload_json = json.loads(capsys.readouterr().out)
    assert payload_json["status"] == "insufficient_history"
    assert payload_json["predicted_value"] is None
    assert payload_json["history_count_observed"] == 2
    assert "requires at least 6" in payload_json["notes"]


def test_cli_forecast_baseline_invalid_arguments(freight_cli_fixture, capsys):
    """Verifies that invalid arguments return exit code 1 and error details to stderr."""
    _, _, db_url, storage_dir = freight_cli_fixture
    parser = build_parser()

    # 1. Disallowed derived route
    args_spread = parser.parse_args([
        "freight-forecast-baseline",
        "--route-code", "SPREAD-USG-PNW-JPN",
        "--db-url", db_url,
        "--storage-dir", str(storage_dir),
    ])
    assert handle_freight_forecast_baseline(args_spread) == 1
    err_spread = capsys.readouterr().err
    assert "derived rate difference indicator" in err_spread

    # 2. Unsupported route
    args_unsupp_route = parser.parse_args([
        "freight-forecast-baseline",
        "--route-code", "ATLANTIC-CAPESIZE",
        "--db-url", db_url,
        "--storage-dir", str(storage_dir),
    ])
    assert handle_freight_forecast_baseline(args_unsupp_route) == 1
    err_route = capsys.readouterr().err
    assert "Unsupported route code" in err_route

    # 3. Unsupported model
    args_unsupp_model = parser.parse_args([
        "freight-forecast-baseline",
        "--route-code", "USG-JPN-BULK",
        "--model", "deep_lstm_network",
        "--db-url", db_url,
        "--storage-dir", str(storage_dir),
    ])
    assert handle_freight_forecast_baseline(args_unsupp_model) == 1
    err_model = capsys.readouterr().err
    assert "Unsupported baseline model" in err_model

    # 4. Out-of-bounds horizon (0 and 7)
    args_h0 = parser.parse_args([
        "freight-forecast-baseline",
        "--route-code", "USG-JPN-BULK",
        "--horizon-months", "0",
        "--db-url", db_url,
        "--storage-dir", str(storage_dir),
    ])
    assert handle_freight_forecast_baseline(args_h0) == 1
    err_h0 = capsys.readouterr().err
    assert "horizon must be between 1 and 6" in err_h0

    args_h7 = parser.parse_args([
        "freight-forecast-baseline",
        "--route-code", "USG-JPN-BULK",
        "--horizon-months", "7",
        "--db-url", db_url,
        "--storage-dir", str(storage_dir),
    ])
    assert handle_freight_forecast_baseline(args_h7) == 1
    err_h7 = capsys.readouterr().err
    assert "horizon must be between 1 and 6" in err_h7

    # 5. Invalid origin date
    args_bad_date = parser.parse_args([
        "freight-forecast-baseline",
        "--route-code", "USG-JPN-BULK",
        "--origin-date", "2025-99-99",
        "--db-url", db_url,
        "--storage-dir", str(storage_dir),
    ])
    assert handle_freight_forecast_baseline(args_bad_date) == 1
    err_date = capsys.readouterr().err
    assert "Invalid origin date format" in err_date

    # 6. JSON error output to stderr
    args_bad_json = parser.parse_args([
        "freight-forecast-baseline",
        "--route-code", "INVALID-ROUTE",
        "--format", "json",
        "--db-url", db_url,
        "--storage-dir", str(storage_dir),
    ])
    assert handle_freight_forecast_baseline(args_bad_json) == 1
    err_json = json.loads(capsys.readouterr().err)
    assert err_json["status"] == "failed"
    assert "Unsupported route code" in err_json["error"]


def test_cli_forecast_baseline_strictly_read_only(freight_cli_fixture):
    """Verifies that freight-forecast-baseline CLI causes zero database mutations and zero Parquet writes."""
    _, engine, db_url, storage_dir = freight_cli_fixture
    parser = build_parser()

    # 1. Capture complete database state across all tables
    inspector = inspect(engine)
    all_tables = sorted(inspector.get_table_names())
    assert len(all_tables) > 0

    def capture_full_db_state():
        state = {}
        with engine.connect() as conn:
            for tbl in all_tables:
                rows = conn.execute(text(f"SELECT * FROM `{tbl}` ORDER BY rowid")).fetchall()
                state[tbl] = rows
        return state

    db_state_before = capture_full_db_state()

    # 2. Baseline storage files state
    parquet_files_before = {
        p.as_posix(): (p.stat().st_size, p.stat().st_mtime)
        for p in storage_dir.rglob("*.parquet")
    }

    # 3. Intercept write operations on Engine
    write_statements = []

    def intercept_write_operations(conn, cursor, statement, parameters, context, executemany):
        stmt_upper = statement.strip().upper()
        mutating_verbs = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "TRUNCATE")
        if any(stmt_upper.startswith(v) for v in mutating_verbs):
            write_statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", intercept_write_operations)

    try:
        # Run various CLI requests
        for route in ["USG-JPN-BULK", "PNW-JPN-BULK"]:
            for model in [None, "persistence", "rolling_ma_3", "rolling_ma_6", "linear_trend_6"]:
                cmd = ["freight-forecast-baseline", "--route-code", route, "--db-url", db_url, "--storage-dir", str(storage_dir)]
                if model:
                    cmd.extend(["--model", model])
                args = parser.parse_args(cmd)
                assert handle_freight_forecast_baseline(args) == 0
    finally:
        event.remove(engine, "before_cursor_execute", intercept_write_operations)

    # Assert zero mutating SQL statements
    assert write_statements == [], f"Mutating SQL statements detected: {write_statements}"

    # Assert zero row changes in any table
    db_state_after = capture_full_db_state()
    assert db_state_after.keys() == db_state_before.keys()
    for tbl in all_tables:
        assert db_state_after[tbl] == db_state_before[tbl], f"Table '{tbl}' was mutated"

    # Assert zero Parquet files created or modified
    parquet_files_after = {
        p.as_posix(): (p.stat().st_size, p.stat().st_mtime)
        for p in storage_dir.rglob("*.parquet")
    }
    assert parquet_files_after == parquet_files_before
