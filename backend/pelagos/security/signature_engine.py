"""
Pelagos Stage F1.4.3: Asymmetric Signature Verification Engine.

Implements candidate cryptographic algorithm profiles:
- Profile A: Ed25519 (RFC 8032) - Deterministic, constant-time, 64-byte signatures.
- Profile B: ECDSA NIST P-384 (secp384r1) + SHA-384 - Enterprise cloud KMS compatible.

Security Invariants:
1. Strict Downgrade Rejection: Rejects alg='none', symmetric algorithms (HS256), or unapproved curves.
2. Structured Key Identifiers (kid): Explicit binding to environment, profile, and validity epoch.
3. Trust Store Expiry Enforcement: Rejects signatures verified against expired or revoked keys.
4. Testing Isolation: Phase 2 permits in-memory test key generation only; zero production keys.
"""

from __future__ import annotations

import datetime
from enum import Enum
from typing import Any, Dict, Optional, Union
from cryptography.hazmat.primitives.asymmetric import ed25519, ec
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidSignature


class SignatureAlgorithm(str, Enum):
    """Approved candidate algorithm profiles."""
    ED25519 = "Ed25519"
    ECDSA_P384 = "ES384"  # ECDSA NIST P-384 with SHA-384


class UnapprovedAlgorithmException(ValueError):
    """Raised when an unapproved or downgraded algorithm is supplied."""
    pass


class KeyExpiredException(ValueError):
    """Raised when a public key is beyond its valid_until timestamp."""
    pass


class KeyRevokedException(ValueError):
    """Raised when a key is marked as revoked in the trust store."""
    pass


class TrustedKeyEntry:
    """Entry in the Governance Authority public key trust store."""

    def __init__(
        self,
        kid: str,
        public_key: Union[ed25519.Ed25519PublicKey, ec.EllipticCurvePublicKey],
        alg: SignatureAlgorithm,
        valid_from: datetime.datetime,
        valid_until: datetime.datetime,
        is_revoked: bool = False,
    ):
        self.kid = kid
        self.public_key = public_key
        self.alg = alg
        self.valid_from = valid_from
        self.valid_until = valid_until
        self.is_revoked = is_revoked

    def is_valid_at(self, dt: datetime.datetime) -> bool:
        if self.is_revoked:
            return False
        # Normalize timezones
        t = dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
        vf = self.valid_from if self.valid_from.tzinfo else self.valid_from.replace(tzinfo=datetime.timezone.utc)
        vu = self.valid_until if self.valid_until.tzinfo else self.valid_until.replace(tzinfo=datetime.timezone.utc)
        return vf <= t <= vu


class SignatureEngine:
    """
    Cryptographic signature verification engine and trust store.
    """

    DISALLOWED_ALGORITHMS = {"none", "NONE", "HS256", "HS384", "HS512", "RS256", "RS384", "RS512"}

    def __init__(self):
        self.trust_store: Dict[str, TrustedKeyEntry] = {}

    def register_trusted_key(self, entry: TrustedKeyEntry) -> None:
        """Register a trusted public key in the in-memory trust store."""
        self.trust_store[entry.kid] = entry

    def revoke_key(self, kid: str) -> bool:
        """Revoke a key in the trust store."""
        if kid in self.trust_store:
            self.trust_store[kid].is_revoked = True
            return True
        return False

    def verify_signature(
        self,
        kid: str,
        signature: bytes,
        payload_bytes: bytes,
        alg: str,
        verification_time: Optional[datetime.datetime] = None,
    ) -> bool:
        """
        Verify digital signature against trusted public key identified by kid.
        
        Strictly rejects algorithm downgrades, expired keys, and revoked keys.
        """
        # 1. Downgrade attack check
        if not alg or alg in self.DISALLOWED_ALGORITHMS:
            raise UnapprovedAlgorithmException(f"SECURITY_UNAPPROVED_ALGORITHM: Algorithm '{alg}' is disallowed.")

        if alg not in (SignatureAlgorithm.ED25519.value, SignatureAlgorithm.ECDSA_P384.value):
            raise UnapprovedAlgorithmException(f"SECURITY_UNAPPROVED_ALGORITHM: '{alg}' is not an approved candidate algorithm.")

        # 2. Key existence check
        if kid not in self.trust_store:
            return False

        key_entry = self.trust_store[kid]

        # 3. Algorithm mismatch check
        if key_entry.alg.value != alg:
            raise UnapprovedAlgorithmException(
                f"SECURITY_ALGORITHM_MISMATCH: Key '{kid}' registered for {key_entry.alg.value}, but token specified {alg}."
            )

        # 4. Expiration and revocation checks
        v_time = verification_time or datetime.datetime.now(datetime.timezone.utc)
        if key_entry.is_revoked:
            raise KeyRevokedException(f"SECURITY_KEY_REVOKED: Key '{kid}' has been revoked.")

        if not key_entry.is_valid_at(v_time):
            raise KeyExpiredException(f"SECURITY_KEY_EXPIRED: Key '{kid}' is not valid at {v_time.isoformat()}.")

        # 5. Cryptographic signature check
        try:
            if key_entry.alg == SignatureAlgorithm.ED25519:
                assert isinstance(key_entry.public_key, ed25519.Ed25519PublicKey)
                key_entry.public_key.verify(signature, payload_bytes)
                return True

            elif key_entry.alg == SignatureAlgorithm.ECDSA_P384:
                assert isinstance(key_entry.public_key, ec.EllipticCurvePublicKey)
                key_entry.public_key.verify(
                    signature,
                    payload_bytes,
                    ec.ECDSA(hashes.SHA384()),
                )
                return True

        except InvalidSignature:
            return False
        except Exception:
            return False

        return False

    @staticmethod
    def sign_test_payload(
        private_key: Union[ed25519.Ed25519PrivateKey, ec.EllipticCurvePrivateKey],
        payload_bytes: bytes,
        alg: SignatureAlgorithm = SignatureAlgorithm.ED25519,
    ) -> bytes:
        """
        Test helper: Sign payload using ephemeral mock test key.
        Strictly authorized for Phase 2 automated test suites only.
        """
        if alg == SignatureAlgorithm.ED25519:
            assert isinstance(private_key, ed25519.Ed25519PrivateKey)
            return private_key.sign(payload_bytes)
        elif alg == SignatureAlgorithm.ECDSA_P384:
            assert isinstance(private_key, ec.EllipticCurvePrivateKey)
            return private_key.sign(payload_bytes, ec.ECDSA(hashes.SHA384()))
        else:
            raise UnapprovedAlgorithmException(f"Unsupported algorithm {alg}")

