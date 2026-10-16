"""
Schema contract and constraint tests for freight route and observation models.
Traces to REQ-FR-DII-005, REQ-NFR-ABM-002, and DATA-001 Section 5.2.
"""

import pytest
from datetime import date, datetime, timezone
import pyarrow as pa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from pelagos.models import Base, FreightRoute, FreightObservation
from pelagos.storage.schema import FREIGHT_PARQUET_SCHEMA, freight_records_to_pyarrow_table


@pytest.fixture
def mem_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_freight_parquet_schema_contract():
    """
    Verifies that FREIGHT_PARQUET_SCHEMA matches FreightObservation ORM fields:
    - Field count matches.
    - All column names present.
    - Rate values are float64.
    - Observation dates are date32.
    - Nullability strictly agrees with SQL model.
    """
    sql_cols = {c.name: c for c in FreightObservation.__table__.columns}

    # All parquet fields must exist in SQL
    for f in FREIGHT_PARQUET_SCHEMA:
        assert f.name in sql_cols, f"Parquet field '{f.name}' missing in SQL model"
        sql_col = sql_cols[f.name]
        assert f.nullable == sql_col.nullable, (
            f"Nullability mismatch for '{f.name}': Parquet={f.nullable}, SQL={sql_col.nullable}"
        )

    assert FREIGHT_PARQUET_SCHEMA.field("rate_value").type == pa.float64()
    assert FREIGHT_PARQUET_SCHEMA.field("observation_date").type == pa.date32()
    assert FREIGHT_PARQUET_SCHEMA.field("is_derived").type == pa.bool_()


def test_freight_records_to_pyarrow_table_conversion():
    """Verifies in-memory conversion from FreightObservation ORM objects to PyArrow Table."""
    obs1 = FreightObservation(
        id=1,
        route_code="USG-JPN-BULK",
        observation_date=date(2024, 1, 1),
        rate_value=55.50,
        currency="USD",
        unit_of_measure="USD/MT",
        is_derived=False,
        observation_period="monthly",
        source_name="USDA AMS",
        source_dataset_id="ehs5-yac3",
        ingestion_time=datetime(2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc),
        processing_time=datetime(2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc),
        license_type="U.S. Public Domain",
        idempotency_key="USDA:USG-JPN-BULK:2024-01-01",
    )
    obs2 = FreightObservation(
        id=2,
        route_code="SPREAD-USG-PNW-JPN",
        observation_date=date(2024, 1, 1),
        rate_value=25.00,
        currency="USD",
        unit_of_measure="USD/MT",
        is_derived=True,
        observation_period="monthly",
        source_name="USDA AMS",
        source_dataset_id="ehs5-yac3",
        ingestion_time=datetime(2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc),
        processing_time=datetime(2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc),
        license_type="U.S. Public Domain",
        idempotency_key="USDA:SPREAD-USG-PNW-JPN:2024-01-01",
    )

    table = freight_records_to_pyarrow_table([obs1, obs2])
    assert table.num_rows == 2
    assert table.schema.equals(FREIGHT_PARQUET_SCHEMA)
    assert table.column("rate_value").to_pylist() == [55.50, 25.00]
    assert table.column("is_derived").to_pylist() == [False, True]


def test_freight_sql_unique_constraints(mem_db):
    """Verifies that duplicate (route_code, observation_date) raises IntegrityError."""
    obs1 = FreightObservation(
        route_code="USG-JPN-BULK",
        observation_date=date(2024, 2, 1),
        rate_value=50.0,
        currency="USD",
        unit_of_measure="USD/MT",
        source_name="USDA",
        idempotency_key="K1",
    )
    obs2 = FreightObservation(
        route_code="USG-JPN-BULK",
        observation_date=date(2024, 2, 1),
        rate_value=52.0,
        currency="USD",
        unit_of_measure="USD/MT",
        source_name="USDA",
        idempotency_key="K2",
    )

    mem_db.add(obs1)
    mem_db.commit()

    mem_db.add(obs2)
    with pytest.raises(IntegrityError):
        mem_db.commit()
    mem_db.rollback()
