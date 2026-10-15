"""Database configuration, engine lifecycle, and session management."""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from pelagos.config import settings

connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    echo=settings.debug,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency yield for FastAPI request context."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema_compatibility(target_engine=None):
    """
    Additive, non-destructive schema reconciliation.
    Inspects existing tables and issues ALTER TABLE ADD COLUMN for missing
    columns on existing databases (both SQLite and PostgreSQL).
    Does not enforce foreign keys unless explicitly configured.
    """
    eng = target_engine or engine
    inspector = inspect(eng)
    table_names = inspector.get_table_names()

    with eng.connect() as conn:
        if "vessel_positions" in table_names:
            cols = {c["name"] for c in inspector.get_columns("vessel_positions")}
            if "source_partition" not in cols:
                conn.execute(text("ALTER TABLE vessel_positions ADD COLUMN source_partition VARCHAR(128)"))
            if "ingestion_run_id" not in cols:
                conn.execute(text("ALTER TABLE vessel_positions ADD COLUMN ingestion_run_id INTEGER"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vessel_positions_source_partition ON vessel_positions (source_partition)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vessel_positions_ingestion_run_id ON vessel_positions (ingestion_run_id)"))

        if "quarantined_records" in table_names:
            cols = {c["name"] for c in inspector.get_columns("quarantined_records")}
            if "source_partition" not in cols:
                conn.execute(text("ALTER TABLE quarantined_records ADD COLUMN source_partition VARCHAR(128)"))
            if "ingestion_run_id" not in cols:
                conn.execute(text("ALTER TABLE quarantined_records ADD COLUMN ingestion_run_id INTEGER"))
            if "mmsi" not in cols:
                conn.execute(text("ALTER TABLE quarantined_records ADD COLUMN mmsi VARCHAR(32)"))
            if "raw_record_id" not in cols:
                conn.execute(text("ALTER TABLE quarantined_records ADD COLUMN raw_record_id VARCHAR(128)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_quarantined_records_source_partition ON quarantined_records (source_partition)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_quarantined_records_ingestion_run_id ON quarantined_records (ingestion_run_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_quarantined_records_mmsi ON quarantined_records (mmsi)"))

        if "parquet_export_tasks" not in table_names:
            import pelagos.models
            pelagos.models.ParquetExportTask.__table__.create(conn)
        else:
            cols = {c["name"] for c in inspector.get_columns("parquet_export_tasks")}
            if "published_sha256" not in cols:
                conn.execute(text("ALTER TABLE parquet_export_tasks ADD COLUMN published_sha256 VARCHAR(64)"))
            if "cleanup_locked_by" not in cols:
                conn.execute(text("ALTER TABLE parquet_export_tasks ADD COLUMN cleanup_locked_by VARCHAR(64)"))
            if "cleanup_locked_expires_at" not in cols:
                conn.execute(text("ALTER TABLE parquet_export_tasks ADD COLUMN cleanup_locked_expires_at TIMESTAMP"))
        conn.commit()


def init_db(target_engine=None):
    """Create all governed tables if they do not exist and ensure additive schema compatibility."""
    import pelagos.models  # noqa: F401 - Register models with Base.metadata
    eng = target_engine or engine
    Base.metadata.create_all(bind=eng)
    ensure_schema_compatibility(eng)
