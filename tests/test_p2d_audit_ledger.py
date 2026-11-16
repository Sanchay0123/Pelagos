"""tests/test_p2d_audit_ledger.py
Phase 2-D: Tamper-Evident Audit Ledger & Persistence Model Tests
Verification 3: Audit & Persistence Integrity Gate
"""

import base64
import datetime
from decimal import Decimal
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import DBAPIError
from cryptography.hazmat.primitives.asymmetric import ed25519

from pelagos.models import (
    Base,
    VoyageDisbursementRecord,
    VoyageDisbursementLineItem,
    ConsumedOperationalToken,
    VoyageDisbursementAuditLog,
)
from pelagos.database import ensure_schema_compatibility, install_disbursement_immutability_triggers
from pelagos.audit.audit_ledger import (
    AuditLedgerService,
    AuditEventType,
    compute_chain_hash,
    GENESIS_CHAIN_ROOT_HEX,
)
from pelagos.audit.audit_verifier import (
    DisbursementAuditVerifier,
    compute_record_payload_hash,
)
from pelagos.security.canonical_json import normalize_decimal_string
from pelagos.security.merkle import RecordSetMerkleTree
from pelagos.security.signature_engine import (
    SignatureEngine,
    SignatureAlgorithm,
    TrustedKeyEntry,
)


@pytest.fixture
def isolated_db():
    """Create an isolated in-memory SQLite database with immutability triggers."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    install_disbursement_immutability_triggers(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session, engine
    session.close()


def test_audit_ledger_sequential_hash_chaining(isolated_db):
    """Verify monotonic sequence numbers and SHA-256 hash chaining back to genesis."""
    session, _ = isolated_db
    ledger = AuditLedgerService(session)

    # 1. Initial state: sequence 0 and genesis hash
    seq0, hash0 = ledger.get_latest_sequence_and_hash()
    assert seq0 == 0
    assert hash0 == GENESIS_CHAIN_ROOT_HEX

    # 2. Append first entry (Seq 1)
    entry1 = ledger.record_disbursement_creation(
        record_id=1,
        voyage_id="VOY-2026-001",
        vessel_imo=9593256,
        merkle_root="a" * 64,
        record_payload_hash="b" * 64,
        attestation_signature="c" * 88,
        signing_key_id="test-key-01",
    )
    assert entry1.sequence_number == 1
    assert entry1.previous_chain_hash == GENESIS_CHAIN_ROOT_HEX

    # Verify chain hash computation matches
    expected_hash1 = compute_chain_hash(
        GENESIS_CHAIN_ROOT_HEX,
        {
            "sequence_number": 1,
            "record_id": 1,
            "voyage_id": "VOY-2026-001",
            "vessel_imo": 9593256,
            "event_type": AuditEventType.RECORD_CREATED.value,
            "merkle_root": "a" * 64,
            "record_payload_hash": "b" * 64,
            "attestation_signature": "c" * 88,
            "signing_key_id": "test-key-01",
        },
    )
    assert entry1.chain_hash == expected_hash1

    # 3. Append second entry (Seq 2)
    entry2 = ledger.record_disbursement_creation(
        record_id=2,
        voyage_id="VOY-2026-002",
        vessel_imo=9593256,
        merkle_root="d" * 64,
        record_payload_hash="e" * 64,
        attestation_signature="f" * 88,
        signing_key_id="test-key-01",
    )
    assert entry2.sequence_number == 2
    assert entry2.previous_chain_hash == expected_hash1
    assert entry2.chain_hash != entry1.chain_hash


def test_sqlite_immutability_triggers(isolated_db):
    """Verify that storage triggers prevent UPDATE and DELETE operations."""
    session, engine = isolated_db

    # Create a record directly
    rec = VoyageDisbursementRecord(
        voyage_id="VOY-TRIGGER-001",
        vessel_imo=9593256,
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="NLRTM",
        transit_date=datetime.date(2026, 4, 10),
        readiness_tier="TIER_1_STATUTORY",
        completeness_status="COMPLETE",
        total_disbursement_usd=Decimal("50000.00"),
        merkle_root="11" * 32,
        record_payload_hash="22" * 32,
        attestation_signature="33" * 32,
        signing_key_id="key-01",
    )
    session.add(rec)
    session.commit()

    # Attempt UPDATE on VoyageDisbursementRecord
    with pytest.raises(DBAPIError) as exc_update:
        session.execute(
            text("UPDATE voyage_disbursement_records SET total_disbursement_usd = 60000.00 WHERE id = :id"),
            {"id": rec.id},
        )
        session.commit()
    assert "IMMUTABILITY VIOLATION: voyage_disbursement_records is append-only" in str(exc_update.value)
    session.rollback()

    # Attempt DELETE on VoyageDisbursementRecord
    with pytest.raises(DBAPIError) as exc_delete:
        session.execute(
            text("DELETE FROM voyage_disbursement_records WHERE id = :id"),
            {"id": rec.id},
        )
        session.commit()
    assert "IMMUTABILITY VIOLATION: voyage_disbursement_records cannot be deleted" in str(exc_delete.value)
    session.rollback()

    # Test ConsumedOperationalToken immutability
    token_rec = ConsumedOperationalToken(
        jti="jti-trg-001",
        consumed_at=datetime.datetime.now(datetime.timezone.utc),
        voyage_id="VOY-TRIGGER-001",
        vessel_imo=9593256,
        disbursement_record_id=rec.id,
        caller_identity="test",
        signature_hash="hash",
    )
    session.add(token_rec)
    session.commit()

    with pytest.raises(DBAPIError) as exc_token_upd:
        session.execute(
            text("UPDATE consumed_operational_tokens SET caller_identity = 'attacker' WHERE jti = 'jti-trg-001'")
        )
        session.commit()
    assert "IMMUTABILITY VIOLATION: consumed_operational_tokens is append-only" in str(exc_token_upd.value)
    session.rollback()


def test_disbursement_audit_verifier_intact_and_tamper_detection(isolated_db):
    """Verify DisbursementAuditVerifier detecting line-item, header, and chain tampering."""
    session, _ = isolated_db

    # 1. Set up Signature Engine and mock test key
    now = datetime.datetime.now(datetime.timezone.utc)
    sig_engine = SignatureEngine()
    ed_priv = ed25519.Ed25519PrivateKey.generate()
    ed_pub = ed_priv.public_key()
    kid = "verifier-test-key-01"
    entry = TrustedKeyEntry(
        kid=kid,
        public_key=ed_pub,
        alg=SignatureAlgorithm.ED25519,
        valid_from=now - datetime.timedelta(days=1),
        valid_until=now + datetime.timedelta(days=30),
    )
    sig_engine.register_trusted_key(entry)

    # 2. Build line items and compute Merkle root
    line_item_data = [
        {
            "tariff_identifier": "TAR-01",
            "description": "Port Dues",
            "charge_category": "PORT_DUES",
            "amount_usd": "35000.00",
            "local_currency": "USD",
            "applicability_type": "MANDATORY",
        },
        {
            "tariff_identifier": "TAR-02",
            "description": "Pilotage",
            "charge_category": "PILOTAGE",
            "amount_usd": "15000.00",
            "local_currency": "USD",
            "applicability_type": "MANDATORY",
        },
    ]
    tree = RecordSetMerkleTree(line_item_data)
    merkle_root_hex = tree.root_hex

    # 3. Create Record and calculate payload hash & signature
    rec = VoyageDisbursementRecord(
        voyage_id="VOY-AUDIT-001",
        vessel_imo=9593256,
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="NLRTM",
        transit_date=datetime.date(2026, 4, 10),
        readiness_tier="TIER_1_STATUTORY",
        completeness_status="COMPLETE",
        total_disbursement_usd=Decimal("50000.00"),
        merkle_root=merkle_root_hex,
        signing_key_id=kid,
    )
    payload_hash = compute_record_payload_hash(rec, merkle_root_hex)
    rec.record_payload_hash = payload_hash

    # Sign payload hash
    sig_bytes = SignatureEngine.sign_test_payload(ed_priv, bytes.fromhex(payload_hash), SignatureAlgorithm.ED25519)
    rec.attestation_signature = base64.b64encode(sig_bytes).decode("utf-8")

    session.add(rec)
    session.flush()

    # Add line items
    for item in line_item_data:
        li = VoyageDisbursementLineItem(
            voyage_disbursement_record_id=rec.id,
            charge_type=item["charge_category"],
            location_code="SGSIN",
            rate_value=Decimal(item["amount_usd"]),
            billing_unit="LUMP_SUM",
            tariff_identifier=item["tariff_identifier"],
            description=item["description"],
            charge_category=item["charge_category"],
            amount_usd=Decimal(item["amount_usd"]),
            local_currency=item["local_currency"],
            applicability_type=item["applicability_type"],
        )
        session.add(li)
    session.flush()

    # Add audit log entry
    ledger = AuditLedgerService(session)
    ledger.record_disbursement_creation(
        record_id=rec.id,
        voyage_id=rec.voyage_id,
        vessel_imo=rec.vessel_imo,
        merkle_root=rec.merkle_root,
        record_payload_hash=rec.record_payload_hash,
        attestation_signature=rec.attestation_signature,
        signing_key_id=rec.signing_key_id,
    )
    session.commit()

    verifier = DisbursementAuditVerifier(session, signature_engine=sig_engine)

    # A. Verify intact record and chain
    chain_res = verifier.verify_audit_chain()
    assert chain_res.is_chain_valid is True
    assert chain_res.total_events_checked == 1
    assert len(chain_res.anomalies_detected) == 0

    rec_res = verifier.verify_record_integrity(rec.id)
    assert rec_res.is_intact is True
    assert rec_res.merkle_root_matches is True
    assert rec_res.payload_hash_matches is True
    assert rec_res.signature_valid is True

    # B. Simulate line item tampering (bypassing triggers via session-level or direct raw mock)
    # Tamper one line item amount
    li_to_tamper = session.query(VoyageDisbursementLineItem).filter(VoyageDisbursementLineItem.voyage_disbursement_record_id == rec.id).first()
    # Temporarily drop trigger to simulate direct raw attacker access
    session.execute(text("DROP TRIGGER trg_vydisb_lines_no_update"))
    session.execute(
        text("UPDATE voyage_disbursement_line_items SET amount_usd = 99999.00 WHERE id = :id"),
        {"id": li_to_tamper.id},
    )
    session.commit()

    # Re-verify record integrity -> Merkle mismatch must be detected!
    rec_tampered = verifier.verify_record_integrity(rec.id)
    assert rec_tampered.is_intact is False
    assert rec_tampered.merkle_root_matches is False
    assert any("TAMPER_DETECTED_MERKLE_MISMATCH" in err for err in rec_tampered.failure_reasons)

    # C. Simulate audit chain tampering (alter previous hash or chain hash)
    session.execute(text("DROP TRIGGER trg_vydisb_audit_no_update"))
    session.execute(
        text("UPDATE voyage_disbursement_audit_log SET chain_hash = 'tampered_hash_00000000000000000000000000000000000000000000000000' WHERE sequence_number = 1")
    )
    session.commit()

    chain_tampered = verifier.verify_audit_chain()
    assert chain_tampered.is_chain_valid is False
    assert any("TAMPER_DETECTED_HASH_MISMATCH" in a for a in chain_tampered.anomalies_detected)


def test_isolated_database_additive_migration():
    """Verify that ensure_schema_compatibility cleanly adds columns/tables on an isolated DB."""
    engine = create_engine("sqlite:///:memory:")
    # Initialize legacy schema missing Phase 2 tables
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE voyage_disbursement_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                voyage_id VARCHAR(64) NOT NULL,
                vessel_imo INTEGER NOT NULL,
                origin_port_unlocode VARCHAR(5) NOT NULL,
                destination_port_unlocode VARCHAR(5) NOT NULL,
                transit_date DATE NOT NULL,
                readiness_tier VARCHAR(64) NOT NULL,
                total_disbursement_usd NUMERIC(14, 2) NOT NULL
            );
        """))
        conn.commit()

    # Run additive migration
    ensure_schema_compatibility(engine)

    # Inspect columns
    from sqlalchemy import inspect
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("voyage_disbursement_records")}
    assert "merkle_root" in cols
    assert "record_payload_hash" in cols
    assert "attestation_signature" in cols
    assert "signing_key_id" in cols

    tables = set(insp.get_table_names())
    assert "consumed_operational_tokens" in tables
    assert "voyage_disbursement_audit_log" in tables
