"""tests/test_p2_adversarial.py
Stage F1.4.3 Phase 2: Adversarial Security Attack Matrix (10 Scenarios)
Verifies fail-closed behavior against tampering, downgrades, and replay attacks.
"""

import base64
import datetime
from decimal import Decimal
import json
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import DBAPIError

from pelagos.models import (
    Base,
    VoyageDisbursementRecord,
    VoyageDisbursementLineItem,
    ConsumedOperationalToken,
    VoyageDisbursementAuditLog,
)
from pelagos.database import install_disbursement_immutability_triggers
from pelagos.readiness.fixture_validation import (
    CommercialFixtureValidationEngine,
    CommercialFixturePayload,
    CommissionGovernanceTier,
    DurationStatus,
    VoyageDurationInput,
)
from pelagos.security.canonical_json import canonicalize_financial_payload
from pelagos.security.merkle import RecordSetMerkleTree
from pelagos.security.signature_engine import (
    SignatureEngine,
    SignatureAlgorithm,
    TrustedKeyEntry,
    UnapprovedAlgorithmException,
    KeyExpiredException,
)
from pelagos.security.token_gate import (
    OperationalUnlockGate,
    TokenCategory,
    CRLCache,
)
from pelagos.audit.audit_ledger import (
    AuditLedgerService,
    compute_chain_hash,
    GENESIS_CHAIN_ROOT_HEX,
)
from pelagos.audit.audit_verifier import (
    DisbursementAuditVerifier,
    compute_record_payload_hash,
)
from unittest.mock import MagicMock


# =============================================================================
# Adversarial Attack Scenario 1: Sub-Floor Fixture Undercutting
# =============================================================================
def test_scenario_01_subfloor_fixture_undercutting():
    """Attack 1: Caller attempts to submit a fixture below statutory floor dues."""
    mock_disb_service = MagicMock()
    mock_disb_service.evaluate_voyage_disbursements.return_value.total_disbursement_usd = 60000.0
    mock_disb_service.evaluate_voyage_disbursements.return_value.has_partial_components = False

    engine = CommercialFixtureValidationEngine(disbursement_service=mock_disb_service)
    payload = CommercialFixturePayload(
        fixture_id="ATTACK-01",
        vessel_imo=9593256,
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="NLRTM",
        laycan_start=datetime.date(2026, 4, 1),
        laycan_end=datetime.date(2026, 4, 5),
        transit_date=datetime.date(2026, 4, 10),
        cargo_tonnes=75000.0,
        cargo_commodity="Bulk Grain",
        total_port_allowance_usd=40000.0,  # Below $60,000 statutory floor
        duration_input=VoyageDurationInput(
            laden_sea_days=15.0,
            ballast_sea_days=10.0,
            loading_days=2.0,
            discharge_days=2.0,
            port_waiting_days=1.0,
            canal_waiting_days=0.0,
            canal_transit_days=0.0,
        ),
        source_organization="Attacker Corp",
    )

    res = engine.validate_fixture(payload)
    assert res.is_valid is False
    assert res.is_operational_permitted is False
    assert any("FIXTURE_VALUATION_BELOW_STATUTORY_FLOOR" in err for err in res.validation_errors)


# =============================================================================
# Adversarial Attack Scenario 2: Unallocated Commercial Overage
# =============================================================================
def test_scenario_02_unallocated_commercial_overage():
    """Attack 2: Caller submits lump sum above statutory floor without itemized breakdown."""
    mock_disb_service = MagicMock()
    mock_disb_service.evaluate_voyage_disbursements.return_value.total_disbursement_usd = 60000.0
    mock_disb_service.evaluate_voyage_disbursements.return_value.has_partial_components = False

    engine = CommercialFixtureValidationEngine(disbursement_service=mock_disb_service)
    payload = CommercialFixturePayload(
        fixture_id="ATTACK-02",
        vessel_imo=9593256,
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="NLRTM",
        laycan_start=datetime.date(2026, 4, 1),
        laycan_end=datetime.date(2026, 4, 5),
        transit_date=datetime.date(2026, 4, 10),
        cargo_tonnes=75000.0,
        cargo_commodity="Bulk Grain",
        total_port_allowance_usd=90000.0,  # $30,000 unallocated overage
        duration_input=VoyageDurationInput(
            laden_sea_days=15.0,
            ballast_sea_days=10.0,
            loading_days=2.0,
            discharge_days=2.0,
            port_waiting_days=1.0,
            canal_waiting_days=0.0,
            canal_transit_days=0.0,
        ),
        source_organization="Attacker Corp",
    )

    res = engine.validate_fixture(payload)
    assert res.unallocated_overage_usd == 30000.0
    assert res.status == "QUARANTINED_UNALLOCATED_OVERAGE"
    assert res.is_operational_permitted is False


# =============================================================================
# Adversarial Attack Scenario 3: Broken Chronological Audit Chain Link
# =============================================================================
def test_scenario_03_broken_chronological_audit_chain_link():
    """Attack 3: Adversary tampers with the SHA-256 hash chaining of an audit event."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    ledger = AuditLedgerService(session)
    ledger.record_disbursement_creation(
        record_id=1, voyage_id="VOY-1", vessel_imo=9593256,
        merkle_root="aa" * 32, record_payload_hash="bb" * 32,
        attestation_signature="cc" * 32, signing_key_id="k-1",
    )
    ledger.record_disbursement_creation(
        record_id=2, voyage_id="VOY-2", vessel_imo=9593256,
        merkle_root="dd" * 32, record_payload_hash="ee" * 32,
        attestation_signature="ff" * 32, signing_key_id="k-1",
    )
    session.commit()

    # Tamper with chain_hash of event 1
    session.execute(text("UPDATE voyage_disbursement_audit_log SET chain_hash = 'tampered' WHERE sequence_number = 1"))
    session.commit()

    verifier = DisbursementAuditVerifier(session)
    res = verifier.verify_audit_chain()
    assert res.is_chain_valid is False
    assert any("TAMPER_DETECTED" in a for a in res.anomalies_detected)


# =============================================================================
# Adversarial Attack Scenario 4: Direct Database Mutation of Sealed Record
# =============================================================================
def test_scenario_04_direct_database_record_tampering():
    """Attack 4: Direct SQL UPDATE on sealed VoyageDisbursementRecord."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    install_disbursement_immutability_triggers(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    rec = VoyageDisbursementRecord(
        voyage_id="VOY-TAMPER-04",
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
        signing_key_id="k-1",
    )
    session.add(rec)
    session.commit()

    # Trigger immediately aborts direct SQL mutation
    with pytest.raises(DBAPIError) as exc_info:
        session.execute(text("UPDATE voyage_disbursement_records SET total_disbursement_usd = 99999.00 WHERE id = :id"), {"id": rec.id})
        session.commit()
    assert "IMMUTABILITY VIOLATION: voyage_disbursement_records is append-only" in str(exc_info.value)


# =============================================================================
# Adversarial Attack Scenario 5: Unauthorized Line Item Modification
# =============================================================================
def test_scenario_05_unauthorized_line_item_modification():
    """Attack 5: Adversary bypasses triggers and tampers with a line item amount."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    now = datetime.datetime.now(datetime.timezone.utc)
    sig_engine = SignatureEngine()
    ed_priv = ed25519.Ed25519PrivateKey.generate()
    ed_pub = ed_priv.public_key()
    kid = "k-scen-05"
    sig_engine.register_trusted_key(TrustedKeyEntry(kid=kid, public_key=ed_pub, alg=SignatureAlgorithm.ED25519, valid_from=now - datetime.timedelta(days=1), valid_until=now + datetime.timedelta(days=1)))

    items = [{"tariff_identifier": "T-1", "description": "Dues", "charge_category": "PORT", "amount_usd": "25000.00", "local_currency": "USD", "applicability_type": "MANDATORY"}]
    tree = RecordSetMerkleTree(items)

    rec = VoyageDisbursementRecord(
        voyage_id="VOY-SCEN-05", vessel_imo=9593256, origin_port_unlocode="SGSIN", destination_port_unlocode="NLRTM",
        transit_date=datetime.date(2026, 4, 10), readiness_tier="TIER_1_STATUTORY", completeness_status="COMPLETE",
        total_disbursement_usd=Decimal("25000.00"), merkle_root=tree.root_hex, signing_key_id=kid,
    )
    p_hash = compute_record_payload_hash(rec, tree.root_hex)
    rec.record_payload_hash = p_hash
    sig_bytes = SignatureEngine.sign_test_payload(ed_priv, bytes.fromhex(p_hash), SignatureAlgorithm.ED25519)
    rec.attestation_signature = base64.b64encode(sig_bytes).decode("utf-8")
    session.add(rec)
    session.flush()

    li = VoyageDisbursementLineItem(
        voyage_disbursement_record_id=rec.id, charge_type="PORT", location_code="SGSIN", rate_value=Decimal("25000.00"),
        billing_unit="LUMP_SUM", tariff_identifier="T-1", description="Dues", charge_category="PORT",
        amount_usd=Decimal("25000.00"), local_currency="USD", applicability_type="MANDATORY",
    )
    session.add(li)
    session.commit()

    # Tamper line item amount
    session.execute(text("UPDATE voyage_disbursement_line_items SET amount_usd = 30000.00 WHERE id = :id"), {"id": li.id})
    session.commit()

    verifier = DisbursementAuditVerifier(session, signature_engine=sig_engine)
    res = verifier.verify_record_integrity(rec.id)
    assert res.is_intact is False
    assert res.merkle_root_matches is False
    assert any("TAMPER_DETECTED_MERKLE_MISMATCH" in err for err in res.failure_reasons)


# =============================================================================
# Adversarial Attack Scenario 6: Operational Token Replay Attack
# =============================================================================
def test_scenario_06_operational_token_replay_attack():
    """Attack 6: Submitting an operational unlock token a second time."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    sig_engine = SignatureEngine()
    now = datetime.datetime.now(datetime.timezone.utc)
    ed_priv = ed25519.Ed25519PrivateKey.generate()
    kid = "k-scen-06"
    sig_engine.register_trusted_key(TrustedKeyEntry(kid=kid, public_key=ed_priv.public_key(), alg=SignatureAlgorithm.ED25519, valid_from=now - datetime.timedelta(hours=1), valid_until=now + datetime.timedelta(hours=1)))
    gate = OperationalUnlockGate(signature_engine=sig_engine)

    payload = {
        "jti": "jti-attack-06",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()) - 5,
        "exp": int(now.timestamp()) + 1800,
        "alg": "Ed25519",
        "kid": kid,
        "token_category": "DEV_TEST",
        "binding": {"voyage_id": "V-1", "vessel_imo": 9593256, "transit_date": "2026-04-10", "disbursement_record_id": 1, "disbursement_content_hash": "a" * 64},
    }
    p_bytes = json.dumps(payload).encode("utf-8")
    sig = SignatureEngine.sign_test_payload(ed_priv, p_bytes, SignatureAlgorithm.ED25519)
    token_str = f"{base64.urlsafe_b64encode(p_bytes).decode().rstrip('=')}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"
    ctx = {"voyage_id": "V-1", "vessel_imo": 9593256, "transit_date": "2026-04-10", "disbursement_record_id": 1, "disbursement_content_hash": "a" * 64}

    # First attempt: consumed
    res1 = gate.verify_and_consume_token(token_str, ctx, db=session, current_time=now)
    assert res1.is_valid is True

    # Replay attempt: rejected
    res2 = gate.verify_and_consume_token(token_str, ctx, db=session, current_time=now)
    assert res2.is_valid is False
    assert any("REPLAYED_OPERATIONAL_TOKEN_REJECTED" in err for err in res2.failure_reasons)


# =============================================================================
# Adversarial Attack Scenario 7: Stale Offline CRL Cache
# =============================================================================
def test_scenario_07_stale_offline_crl_cache():
    """Attack 7: Presenting token to an offline node whose cached CRL is > 2 hours old."""
    sig_engine = SignatureEngine()
    now = datetime.datetime.now(datetime.timezone.utc)
    ed_priv = ed25519.Ed25519PrivateKey.generate()
    kid = "k-scen-07"
    sig_engine.register_trusted_key(TrustedKeyEntry(kid=kid, public_key=ed_priv.public_key(), alg=SignatureAlgorithm.ED25519, valid_from=now - datetime.timedelta(hours=1), valid_until=now + datetime.timedelta(hours=1)))
    gate = OperationalUnlockGate(signature_engine=sig_engine)

    payload = {
        "jti": "jti-attack-07",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(now.timestamp()) + 1800,
        "alg": "Ed25519",
        "kid": kid,
        "token_category": "DEV_TEST",
        "binding": {"voyage_id": "V-1", "vessel_imo": 9593256, "transit_date": "2026-04-10", "disbursement_record_id": 1, "disbursement_content_hash": "a" * 64},
    }
    p_bytes = json.dumps(payload).encode("utf-8")
    sig = SignatureEngine.sign_test_payload(ed_priv, p_bytes, SignatureAlgorithm.ED25519)
    token_str = f"{base64.urlsafe_b64encode(p_bytes).decode().rstrip('=')}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"
    ctx = {"voyage_id": "V-1", "vessel_imo": 9593256, "transit_date": "2026-04-10"}

    # Offline CRL cached 150 minutes ago (> 120 minutes)
    stale_crl = CRLCache(revoked_keys=[], revoked_jtis=[], last_updated=now - datetime.timedelta(minutes=150), is_online=False)
    res = gate.verify_and_consume_token(token_str, ctx, crl_cache=stale_crl, current_time=now)
    assert res.is_valid is False
    assert any("SECURITY_REVOCATION_STATUS_UNKNOWN" in err for err in res.failure_reasons)


# =============================================================================
# Adversarial Attack Scenario 8: Signature Algorithm Downgrade Attack
# =============================================================================
def test_scenario_08_signature_algorithm_downgrade_attack():
    """Attack 8: Forcing alg='none' or symmetric HMAC (HS256)."""
    engine = SignatureEngine()
    now = datetime.datetime.now(datetime.timezone.utc)
    ed_priv = ed25519.Ed25519PrivateKey.generate()
    kid = "k-scen-08"
    engine.register_trusted_key(TrustedKeyEntry(kid=kid, public_key=ed_priv.public_key(), alg=SignatureAlgorithm.ED25519, valid_from=now - datetime.timedelta(days=1), valid_until=now + datetime.timedelta(days=1)))

    with pytest.raises(UnapprovedAlgorithmException):
        engine.verify_signature(kid, b"sig", b"payload", "none", now)

    with pytest.raises(UnapprovedAlgorithmException):
        engine.verify_signature(kid, b"sig", b"payload", "HS256", now)


# =============================================================================
# Adversarial Attack Scenario 9: Expired Cryptographic Key
# =============================================================================
def test_scenario_09_expired_cryptographic_key():
    """Attack 9: Signature created or presented against an expired key in trust store."""
    engine = SignatureEngine()
    now = datetime.datetime.now(datetime.timezone.utc)
    ed_priv = ed25519.Ed25519PrivateKey.generate()
    kid = "k-scen-09"
    # Key expired 5 minutes ago
    engine.register_trusted_key(TrustedKeyEntry(kid=kid, public_key=ed_priv.public_key(), alg=SignatureAlgorithm.ED25519, valid_from=now - datetime.timedelta(days=2), valid_until=now - datetime.timedelta(minutes=5)))

    sig = SignatureEngine.sign_test_payload(ed_priv, b"payload", SignatureAlgorithm.ED25519)
    with pytest.raises(KeyExpiredException):
        engine.verify_signature(kid, sig, b"payload", "Ed25519", now)


# =============================================================================
# Adversarial Attack Scenario 10: Category 3 Production Release Token
# =============================================================================
def test_scenario_10_production_token_prohibition():
    """Attack 10: Presenting a Category 3 Production Release Token in Phase 2."""
    sig_engine = SignatureEngine()
    now = datetime.datetime.now(datetime.timezone.utc)
    ed_priv = ed25519.Ed25519PrivateKey.generate()
    kid = "k-scen-10"
    sig_engine.register_trusted_key(TrustedKeyEntry(kid=kid, public_key=ed_priv.public_key(), alg=SignatureAlgorithm.ED25519, valid_from=now - datetime.timedelta(hours=1), valid_until=now + datetime.timedelta(hours=1)))
    gate = OperationalUnlockGate(signature_engine=sig_engine)

    payload = {
        "jti": "jti-attack-10",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(now.timestamp()) + 1800,
        "alg": "Ed25519",
        "kid": kid,
        "token_category": "PRODUCTION_RELEASE",  # Category 3
        "binding": {"voyage_id": "V-1", "vessel_imo": 9593256, "transit_date": "2026-04-10", "disbursement_record_id": 1, "disbursement_content_hash": "a" * 64},
    }
    p_bytes = json.dumps(payload).encode("utf-8")
    sig = SignatureEngine.sign_test_payload(ed_priv, p_bytes, SignatureAlgorithm.ED25519)
    token_str = f"{base64.urlsafe_b64encode(p_bytes).decode().rstrip('=')}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"
    ctx = {"voyage_id": "V-1", "vessel_imo": 9593256, "transit_date": "2026-04-10"}

    res = gate.verify_and_consume_token(token_str, ctx, current_time=now)
    assert res.is_valid is False
    assert res.status == "PRODUCTION_TOKEN_PROHIBITED_IN_PHASE_2"
    assert any("strictly prohibited in Phase 2" in err for err in res.failure_reasons)

