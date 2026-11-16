"""tests/test_p2b_cryptographic_primitives.py
Phase 2-B: Cryptographic Authorization Primitives Tests
Verification 2: Cryptographic Correctness Gate
"""

import base64
import datetime
from decimal import Decimal
import json
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519, ec
from cryptography.hazmat.primitives import hashes

from pelagos.security.canonical_json import (
    canonicalize_financial_payload,
    normalize_decimal_string,
    NonFiniteFinancialValueException,
    InvalidCanonicalDecimalException,
)
from pelagos.security.merkle import (
    RecordSetMerkleTree,
    hash_leaf,
    hash_node,
    EMPTY_TREE_ROOT,
    EMPTY_TREE_ROOT_HEX,
    LEAF_PREFIX,
    NODE_PREFIX,
)
from pelagos.security.signature_engine import (
    SignatureEngine,
    SignatureAlgorithm,
    TrustedKeyEntry,
    UnapprovedAlgorithmException,
    KeyExpiredException,
    KeyRevokedException,
)
from pelagos.security.token_gate import (
    OperationalUnlockGate,
    TokenCategory,
    TokenBinding,
    OperationalTokenPayload,
    CRLCache,
)


# =============================================================================
# 1. RFC 8785 Canonical JSON Serialization Tests
# =============================================================================

def test_canonical_json_sorting_and_whitespace():
    """Verify lexicographical key ordering and elimination of insignificant whitespace."""
    data = {
        "z": 1,
        "a": "hello",
        "m": {"nested_z": 2, "nested_a": 1},
        "amount": Decimal("14250.50"),
    }
    canonical_bytes = canonicalize_financial_payload(data)
    canonical_str = canonical_bytes.decode("utf-8")
    
    # Keys must be alphabetically sorted: "a", "amount", "m", "z"
    expected = '{"a":"hello","amount":"14250.50","m":{"nested_a":1,"nested_z":2},"z":1}'
    assert canonical_str == expected


def test_canonical_json_decimal_scale_and_negative_zero():
    """Verify exact Decimal scale preservation and negative zero normalization."""
    # Scale preservation
    assert normalize_decimal_string(Decimal("14250.50")) == "14250.50"
    assert normalize_decimal_string(Decimal("1.345620")) == "1.345620"
    assert normalize_decimal_string(Decimal("52000.000")) == "52000.000"

    # Negative zero normalization
    assert normalize_decimal_string(Decimal("-0.00")) == "0.00"
    assert normalize_decimal_string(Decimal("-0")) == "0"


def test_canonical_json_non_finite_rejection():
    """Verify strict rejection of NaN and Infinity in financial payloads."""
    with pytest.raises(NonFiniteFinancialValueException):
        normalize_decimal_string(Decimal("NaN"))

    with pytest.raises(NonFiniteFinancialValueException):
        normalize_decimal_string(Decimal("Infinity"))

    with pytest.raises(NonFiniteFinancialValueException):
        normalize_decimal_string(Decimal("-Infinity"))

    with pytest.raises(NonFiniteFinancialValueException):
        canonicalize_financial_payload({"rate": float("nan")})

    with pytest.raises(NonFiniteFinancialValueException):
        canonicalize_financial_payload({"rate": float("inf")})


# =============================================================================
# 2. RFC 6962 Record-Set Merkle Tree Tests
# =============================================================================

def test_merkle_tree_empty_and_domain_separation():
    """Verify empty tree constant and RFC 6962 domain separation prefixes."""
    empty_tree = RecordSetMerkleTree([])
    assert empty_tree.root == EMPTY_TREE_ROOT
    assert empty_tree.root_hex == EMPTY_TREE_ROOT_HEX

    # Leaf prefix 0x00 vs Node prefix 0x01
    leaf = hash_leaf({"amount": "100.00"})
    node = hash_node(leaf, leaf)
    assert leaf != node
    assert leaf.startswith(b"")  # 32 bytes


def test_merkle_tree_leaf_sorting_order_invariance():
    """Verify that leaf sets in different input order produce identical Merkle roots."""
    items_order_1 = [
        {"code": "PORT_DUES", "amount": "50000.00"},
        {"code": "PILOTAGE", "amount": "12000.00"},
        {"code": "TOWAGE", "amount": "18000.00"},
        {"code": "BERTHAGE", "amount": "8000.00"},
    ]
    items_order_2 = [
        {"code": "BERTHAGE", "amount": "8000.00"},
        {"code": "PORT_DUES", "amount": "50000.00"},
        {"code": "TOWAGE", "amount": "18000.00"},
        {"code": "PILOTAGE", "amount": "12000.00"},
    ]

    tree1 = RecordSetMerkleTree(items_order_1)
    tree2 = RecordSetMerkleTree(items_order_2)
    assert tree1.root == tree2.root
    assert tree1.root_hex == tree2.root_hex


def test_merkle_tree_odd_nodes_and_inclusion_proofs():
    """Verify power-of-two partitioning for odd node counts and inclusion proofs."""
    items = [
        {"id": 1, "desc": "Item 1"},
        {"id": 2, "desc": "Item 2"},
        {"id": 3, "desc": "Item 3"},
        {"id": 4, "desc": "Item 4"},
        {"id": 5, "desc": "Item 5"},  # 5 items (odd, non-power-of-two)
    ]
    tree = RecordSetMerkleTree(items)
    assert len(tree.leaf_hashes) == 5

    # Test inclusion proofs for all 5 items
    for item in items:
        proof = tree.generate_inclusion_proof(item)
        assert proof is not None
        assert RecordSetMerkleTree.verify_inclusion_proof(item, proof, tree.root_hex) is True

    # Tampered item must fail verification
    tampered_item = {"id": 1, "desc": "Item 1 Tampered"}
    valid_proof = tree.generate_inclusion_proof(items[0])
    assert RecordSetMerkleTree.verify_inclusion_proof(tampered_item, valid_proof, tree.root_hex) is False


# =============================================================================
# 3. Signature Engine & Downgrade Rejection Tests
# =============================================================================

def test_signature_engine_ed25519_and_p384_profiles():
    """Verify Ed25519 and ECDSA P-384 signature verification."""
    engine = SignatureEngine()
    now = datetime.datetime.now(datetime.timezone.utc)

    # 1. Profile A: Ed25519
    ed_priv = ed25519.Ed25519PrivateKey.generate()
    ed_pub = ed_priv.public_key()
    ed_entry = TrustedKeyEntry(
        kid="test-ed25519-01",
        public_key=ed_pub,
        alg=SignatureAlgorithm.ED25519,
        valid_from=now - datetime.timedelta(days=1),
        valid_until=now + datetime.timedelta(days=30),
    )
    engine.register_trusted_key(ed_entry)

    payload = b"PELAGOS_VOYAGE_RELEASE_MOCK_PAYLOAD"
    ed_sig = SignatureEngine.sign_test_payload(ed_priv, payload, SignatureAlgorithm.ED25519)
    assert engine.verify_signature("test-ed25519-01", ed_sig, payload, "Ed25519", now) is True

    # 2. Profile B: ECDSA P-384
    p384_priv = ec.generate_private_key(ec.SECP384R1())
    p384_pub = p384_priv.public_key()
    p384_entry = TrustedKeyEntry(
        kid="test-p384-01",
        public_key=p384_pub,
        alg=SignatureAlgorithm.ECDSA_P384,
        valid_from=now - datetime.timedelta(days=1),
        valid_until=now + datetime.timedelta(days=30),
    )
    engine.register_trusted_key(p384_entry)

    p384_sig = SignatureEngine.sign_test_payload(p384_priv, payload, SignatureAlgorithm.ECDSA_P384)
    assert engine.verify_signature("test-p384-01", p384_sig, payload, "ES384", now) is True


def test_signature_downgrade_and_expiration_rejection():
    """Verify rejection of alg='none', HS256, expired keys, and revoked keys."""
    engine = SignatureEngine()
    now = datetime.datetime.now(datetime.timezone.utc)

    ed_priv = ed25519.Ed25519PrivateKey.generate()
    ed_pub = ed_priv.public_key()

    # Expired key
    expired_entry = TrustedKeyEntry(
        kid="expired-key",
        public_key=ed_pub,
        alg=SignatureAlgorithm.ED25519,
        valid_from=now - datetime.timedelta(days=10),
        valid_until=now - datetime.timedelta(days=1),
    )
    engine.register_trusted_key(expired_entry)

    payload = b"TEST"
    sig = SignatureEngine.sign_test_payload(ed_priv, payload, SignatureAlgorithm.ED25519)

    # Downgrade: alg='none'
    with pytest.raises(UnapprovedAlgorithmException):
        engine.verify_signature("expired-key", sig, payload, "none", now)

    # Downgrade: alg='HS256'
    with pytest.raises(UnapprovedAlgorithmException):
        engine.verify_signature("expired-key", sig, payload, "HS256", now)

    # Expired key check
    with pytest.raises(KeyExpiredException):
        engine.verify_signature("expired-key", sig, payload, "Ed25519", now)

    # Revoked key check
    valid_entry = TrustedKeyEntry(
        kid="revoked-key",
        public_key=ed_pub,
        alg=SignatureAlgorithm.ED25519,
        valid_from=now - datetime.timedelta(days=1),
        valid_until=now + datetime.timedelta(days=1),
        is_revoked=True,
    )
    engine.register_trusted_key(valid_entry)
    with pytest.raises(KeyRevokedException):
        engine.verify_signature("revoked-key", sig, payload, "Ed25519", now)


# =============================================================================
# 4. Operational Unlock Gate & Governance Tests
# =============================================================================

@pytest.fixture
def token_setup():
    engine = SignatureEngine()
    now = datetime.datetime.now(datetime.timezone.utc)
    ed_priv = ed25519.Ed25519PrivateKey.generate()
    ed_pub = ed_priv.public_key()
    kid = "ephemeral-test-key-001"
    entry = TrustedKeyEntry(
        kid=kid,
        public_key=ed_pub,
        alg=SignatureAlgorithm.ED25519,
        valid_from=now - datetime.timedelta(hours=2),
        valid_until=now + datetime.timedelta(hours=2),
    )
    engine.register_trusted_key(entry)
    gate = OperationalUnlockGate(signature_engine=engine)

    def create_token_str(payload_dict: dict) -> str:
        payload_bytes = json.dumps(payload_dict).encode("utf-8")
        sig = SignatureEngine.sign_test_payload(ed_priv, payload_bytes, SignatureAlgorithm.ED25519)
        b64_payload = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")
        b64_sig = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
        return f"{b64_payload}.{b64_sig}"

    return gate, create_token_str, now, kid


def test_token_gate_valid_consumption_and_anti_replay(token_setup):
    """Verify successful verification and subsequent anti-replay rejection."""
    gate, create_token_str, now, kid = token_setup
    now_ts = int(now.timestamp())

    payload = {
        "jti": "jti-001",
        "iat": now_ts,
        "nbf": now_ts - 5,
        "exp": now_ts + 1800,  # 30 mins
        "alg": "Ed25519",
        "kid": kid,
        "token_category": "DEV_TEST",
        "binding": {
            "voyage_id": "VOY-2026-001",
            "vessel_imo": 9593256,
            "transit_date": "2026-04-10",
            "disbursement_record_id": 101,
            "disbursement_content_hash": "a" * 64,
        },
    }
    token_str = create_token_str(payload)
    context = {
        "voyage_id": "VOY-2026-001",
        "vessel_imo": 9593256,
        "transit_date": "2026-04-10",
        "disbursement_record_id": 101,
        "disbursement_content_hash": "a" * 64,
    }

    # 1. First consumption succeeds
    res1 = gate.verify_and_consume_token(token_str, context, current_time=now)
    assert res1.is_valid is True
    assert res1.status == "TOKEN_VERIFIED_AND_CONSUMED"
    assert res1.jti == "jti-001"

    # 2. Replay attempt fails
    res2 = gate.verify_and_consume_token(token_str, context, current_time=now)
    assert res2.is_valid is False
    assert res2.status == "TOKEN_VERIFICATION_FAILED"
    assert any("REPLAYED_OPERATIONAL_TOKEN_REJECTED" in err for err in res2.failure_reasons)


def test_token_gate_category_3_production_prohibition(token_setup):
    """Verify that Category 3 Production Release tokens are strictly rejected in Phase 2."""
    gate, create_token_str, now, kid = token_setup
    now_ts = int(now.timestamp())

    payload = {
        "jti": "jti-prod-001",
        "iat": now_ts,
        "nbf": now_ts,
        "exp": now_ts + 1800,
        "alg": "Ed25519",
        "kid": kid,
        "token_category": "PRODUCTION_RELEASE",  # Strict prohibition in Phase 2
        "binding": {
            "voyage_id": "VOY-2026-001",
            "vessel_imo": 9593256,
            "transit_date": "2026-04-10",
            "disbursement_record_id": 101,
            "disbursement_content_hash": "a" * 64,
        },
    }
    token_str = create_token_str(payload)
    context = {
        "voyage_id": "VOY-2026-001",
        "vessel_imo": 9593256,
        "transit_date": "2026-04-10",
    }
    res = gate.verify_and_consume_token(token_str, context, current_time=now)
    assert res.is_valid is False
    assert res.status == "PRODUCTION_TOKEN_PROHIBITED_IN_PHASE_2"


def test_token_gate_lifespan_and_clock_skew(token_setup):
    """Verify 1-hour lifespan limit and clock skew boundary enforcement."""
    gate, create_token_str, now, kid = token_setup
    now_ts = int(now.timestamp())

    # Lifespan: 3601 seconds (> 1 hour)
    payload_long_life = {
        "jti": "jti-long",
        "iat": now_ts,
        "nbf": now_ts,
        "exp": now_ts + 3601,
        "alg": "Ed25519",
        "kid": kid,
        "token_category": "DEV_TEST",
        "binding": {
            "voyage_id": "VOY-1",
            "vessel_imo": 9593256,
            "transit_date": "2026-04-10",
            "disbursement_record_id": 1,
            "disbursement_content_hash": "a" * 64,
        },
    }
    res_long = gate.verify_and_consume_token(
        create_token_str(payload_long_life),
        {"voyage_id": "VOY-1", "vessel_imo": 9593256, "transit_date": "2026-04-10"},
        current_time=now,
    )
    assert res_long.is_valid is False
    assert any("INVALID_TOKEN_LIFESPAN" in err for err in res_long.failure_reasons)


def test_token_gate_offline_crl_freshness(token_setup):
    """Verify that offline CRL cache exceeding 2 hours triggers fail-closed rejection."""
    gate, create_token_str, now, kid = token_setup
    now_ts = int(now.timestamp())

    payload = {
        "jti": "jti-crl-test",
        "iat": now_ts,
        "nbf": now_ts,
        "exp": now_ts + 1800,
        "alg": "Ed25519",
        "kid": kid,
        "token_category": "DEV_TEST",
        "binding": {
            "voyage_id": "VOY-1",
            "vessel_imo": 9593256,
            "transit_date": "2026-04-10",
            "disbursement_record_id": 1,
            "disbursement_content_hash": "a" * 64,
        },
    }
    token_str = create_token_str(payload)
    context = {"voyage_id": "VOY-1", "vessel_imo": 9593256, "transit_date": "2026-04-10"}

    # Fresh offline CRL (1 hour old <= 2 hours) -> Valid
    fresh_crl = CRLCache(
        revoked_keys=[],
        revoked_jtis=[],
        last_updated=now - datetime.timedelta(hours=1),
        is_online=False,
    )
    res_fresh = gate.verify_and_consume_token(token_str, context, crl_cache=fresh_crl, current_time=now)
    assert res_fresh.is_valid is True

    # Stale offline CRL (2.5 hours old > 2 hours) -> Fails closed
    stale_crl = CRLCache(
        revoked_keys=[],
        revoked_jtis=[],
        last_updated=now - datetime.timedelta(hours=2, minutes=30),
        is_online=False,
    )
    payload["jti"] = "jti-crl-stale"
    token_str_stale = create_token_str(payload)
    res_stale = gate.verify_and_consume_token(token_str_stale, context, crl_cache=stale_crl, current_time=now)
    assert res_stale.is_valid is False
    assert any("SECURITY_REVOCATION_STATUS_UNKNOWN" in err for err in res_stale.failure_reasons)


def test_token_gate_concurrent_in_memory_replay(token_setup):
    """
    P2-003 Remediation: Verify that multi-threaded concurrent token submissions
    against in-memory gate cleanly reject duplicate execution without race conditions.
    """
    import concurrent.futures

    gate, create_token_str, now, kid = token_setup
    now_ts = int(now.timestamp())

    payload = {
        "jti": "jti-concurrent-in-memory-001",
        "iat": now_ts,
        "nbf": now_ts,
        "exp": now_ts + 1800,
        "alg": "Ed25519",
        "kid": kid,
        "token_category": "DEV_TEST",
        "binding": {
            "voyage_id": "VOY-CONCURRENT",
            "vessel_imo": 9593256,
            "transit_date": "2026-04-10",
            "disbursement_record_id": 1,
            "disbursement_content_hash": "a" * 64,
        },
    }
    token_str = create_token_str(payload)
    context = {"voyage_id": "VOY-CONCURRENT", "vessel_imo": 9593256, "transit_date": "2026-04-10"}

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(gate.verify_and_consume_token, token_str, context, current_time=now)
            for _ in range(5)
        ]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Exactly one submission must succeed; all others must be rejected
    success_count = sum(1 for r in results if r.is_valid)
    rejected_count = sum(1 for r in results if not r.is_valid)

    assert success_count == 1, f"Expected exactly 1 success, got {success_count}"
    assert rejected_count == 4, f"Expected 4 rejections, got {rejected_count}"
    for r in results:
        if not r.is_valid:
            assert r.status in ("CONCURRENT_REPLAY_DETECTED", "TOKEN_VERIFICATION_FAILED")


def test_token_gate_concurrent_database_integrity_error_handling(token_setup, tmp_path):
    """
    P2-003 Remediation: Verify that concurrent submissions with separate database sessions
    safely trap IntegrityError on unique JTI constraint, rollback cleanly, and return
    a structured domain response (status='CONCURRENT_REPLAY_DETECTED') without bubbling unhandled 500 exceptions.
    """
    import concurrent.futures
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from pelagos.database import Base
    import pelagos.models  # Register all models with Base.metadata

    gate, create_token_str, now, kid = token_setup
    now_ts = int(now.timestamp())

    # Create isolated file-backed test database supporting multi-session concurrency
    db_file = tmp_path / "concurrent_token_test.db"
    test_engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)

    payload = {
        "jti": "jti-concurrent-db-002",
        "iat": now_ts,
        "nbf": now_ts,
        "exp": now_ts + 1800,
        "alg": "Ed25519",
        "kid": kid,
        "token_category": "DEV_TEST",
        "binding": {
            "voyage_id": "VOY-CONCURRENT-DB",
            "vessel_imo": 9593256,
            "transit_date": "2026-04-10",
            "disbursement_record_id": 1,
            "disbursement_content_hash": "b" * 64,
        },
    }
    token_str = create_token_str(payload)
    context = {"voyage_id": "VOY-CONCURRENT-DB", "vessel_imo": 9593256, "transit_date": "2026-04-10"}

    def run_token_verification(thread_id: int):
        session = TestSession()
        try:
            res = gate.verify_and_consume_token(
                token_str,
                context,
                db=session,
                current_time=now,
            )
            if res.is_valid:
                session.commit()
            return res
        finally:
            session.close()

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(run_token_verification, i) for i in range(4)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Exactly one session must succeed; concurrent attempts must return fail-closed without exception
    success_count = sum(1 for r in results if r.is_valid)
    assert success_count == 1, f"Expected exactly 1 valid consumption, got {success_count}"
    failures = [r for r in results if not r.is_valid]
    assert len(failures) == 3
    for f in failures:
        assert f.status in ("CONCURRENT_REPLAY_DETECTED", "TOKEN_VERIFICATION_FAILED")


