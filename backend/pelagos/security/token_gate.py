"""
Pelagos Stage F1.4.3: Operational Token Gate & Revocation Engine.

Enforces cryptographic release token verification:
- Supports Category 1 (Development/Test) and Category 2 (Non-Operational Simulation) tokens only.
- Category 3 (Production Release) tokens are strictly prohibited in Phase 2.
- 1-hour maximum lifespan enforcement (exp - iat <= 3600s).
- Maximum 30-second clock skew tolerance.
- Contextual binding verification (voyage_id, vessel_imo, transit_date, disbursement_record_id, disbursement_content_hash).
- Single-use anti-replay ledger tracking (consumed_operational_tokens).
- Offline CRL freshness governance (fails closed at > 2 hours).
"""

from __future__ import annotations

import base64
import datetime
import json
import time
from enum import Enum
from typing import Any, Dict, List, Optional
import threading
from pydantic import BaseModel, Field

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from pelagos.security.canonical_json import canonicalize_financial_payload
from pelagos.security.signature_engine import (
    SignatureEngine,
    SignatureAlgorithm,
    UnapprovedAlgorithmException,
    KeyExpiredException,
    KeyRevokedException,
)


class TokenCategory(str, Enum):
    """Token governance categorization."""
    DEV_TEST = "DEV_TEST"
    NON_OPERATIONAL_SIMULATION = "NON_OPERATIONAL_SIMULATION"
    PRODUCTION_RELEASE = "PRODUCTION_RELEASE"


class TokenBinding(BaseModel):
    """Contextual binding parameters tying a token to an exact execution context."""
    voyage_id: str
    vessel_imo: int
    transit_date: str
    disbursement_record_id: int
    disbursement_content_hash: str


class OperationalTokenPayload(BaseModel):
    """Complete decoded operational token payload."""
    iss: str = Field(default="urn:pelagos:governance:authority")
    sub: str = Field(default="operational_tce_unlock")
    aud: str = Field(default="urn:pelagos:engine:settlement")
    jti: str
    iat: int
    nbf: int
    exp: int
    alg: str
    kid: str
    token_category: TokenCategory = TokenCategory.DEV_TEST
    binding: TokenBinding


class TokenValidationResult(BaseModel):
    """Output contract for token verification and consumption."""
    is_valid: bool
    status: str
    jti: Optional[str] = None
    token_category: Optional[str] = None
    failure_reasons: List[str] = Field(default_factory=list)
    consumed_at: Optional[str] = None


class CRLCache:
    """In-memory cache for Certificate Revocation List."""

    def __init__(
        self,
        revoked_keys: Optional[List[str]] = None,
        revoked_jtis: Optional[List[str]] = None,
        last_updated: Optional[datetime.datetime] = None,
        is_online: bool = True,
    ):
        self.revoked_keys = set(revoked_keys or [])
        self.revoked_jtis = set(revoked_jtis or [])
        self.last_updated = last_updated or datetime.datetime.now(datetime.timezone.utc)
        self.is_online = is_online

    def cache_age_seconds(self, now: Optional[datetime.datetime] = None) -> float:
        t = now or datetime.datetime.now(datetime.timezone.utc)
        lu = self.last_updated if self.last_updated.tzinfo else self.last_updated.replace(tzinfo=datetime.timezone.utc)
        return (t - lu).total_seconds()


class OperationalUnlockGate:
    """
    Cryptographic verification gate for operational unlock tokens.
    """

    MAX_TOKEN_LIFESPAN_SECONDS = 3600  # Exactly 1 hour
    MAX_CLOCK_SKEW_SECONDS = 30        # 30 seconds clock skew tolerance
    MAX_CRL_CACHE_AGE_SECONDS = 7200   # 2 hours offline freshness horizon

    def __init__(self, signature_engine: Optional[SignatureEngine] = None):
        self.signature_engine = signature_engine or SignatureEngine()
        self.in_memory_consumed_jtis: set[str] = set()
        self._lock = threading.Lock()

    def verify_and_consume_token(
        self,
        token_str: str,
        execution_context: Dict[str, Any],
        db: Optional[Session] = None,
        crl_cache: Optional[CRLCache] = None,
        current_time: Optional[datetime.datetime] = None,
    ) -> TokenValidationResult:
        """
        Verify cryptographic release token and record its consumption.
        
        Strictly enforces:
        - Prohibition of Category 3 Production Release tokens in Phase 2.
        - Lifespan <= 3600 seconds.
        - Single-use anti-replay transactionality.
        - 2-hour offline CRL fail-closed governance.
        """
        now = current_time or datetime.datetime.now(datetime.timezone.utc)
        now_ts = int(now.timestamp())
        failure_reasons: List[str] = []

        # 1. Parse token structure (Format: Base64(canonical_json_payload).Base64(signature))
        parts = token_str.strip().split(".")
        if len(parts) != 2:
            return TokenValidationResult(
                is_valid=False,
                status="MALFORMED_TOKEN_STRUCTURE",
                failure_reasons=["Token must consist of exactly header.signature format."],
            )

        try:
            payload_bytes = base64.urlsafe_b64decode(parts[0] + "==")
            signature_bytes = base64.urlsafe_b64decode(parts[1] + "==")
            payload_dict = json.loads(payload_bytes.decode("utf-8"))
            token = OperationalTokenPayload(**payload_dict)
        except Exception as exc:
            return TokenValidationResult(
                is_valid=False,
                status="TOKEN_DESERIALIZATION_FAILED",
                failure_reasons=[f"Could not deserialize token payload: {exc}"],
            )

        # 2. Strict Phase 2 Production Token Prohibition
        if token.token_category == TokenCategory.PRODUCTION_RELEASE:
            return TokenValidationResult(
                is_valid=False,
                status="PRODUCTION_TOKEN_PROHIBITED_IN_PHASE_2",
                jti=token.jti,
                token_category=token.token_category.value,
                failure_reasons=["Category 3 Production Release Tokens are strictly prohibited in Phase 2."],
            )

        # 3. Lifespan & Expiration Verification
        lifespan = token.exp - token.iat
        if lifespan > self.MAX_TOKEN_LIFESPAN_SECONDS:
            failure_reasons.append(
                f"INVALID_TOKEN_LIFESPAN: Token lifespan ({lifespan}s) exceeds maximum authorized window (3600s)."
            )

        if token.exp < (now_ts - self.MAX_CLOCK_SKEW_SECONDS):
            failure_reasons.append(
                f"EXPIRED_OPERATIONAL_TOKEN: Token expired at {token.exp} (current time: {now_ts})."
            )

        if token.nbf > (now_ts + self.MAX_CLOCK_SKEW_SECONDS):
            failure_reasons.append(
                f"TOKEN_NOT_YET_VALID: Token nbf {token.nbf} is in the future."
            )

        # 4. Offline CRL Freshness & Revocation Checks
        if crl_cache is not None:
            crl_age = crl_cache.cache_age_seconds(now)
            if not crl_cache.is_online and crl_age > self.MAX_CRL_CACHE_AGE_SECONDS:
                failure_reasons.append(
                    f"SECURITY_REVOCATION_STATUS_UNKNOWN: Cached CRL age ({crl_age:.0f}s) exceeds 2 hours in offline mode."
                )

            if token.kid in crl_cache.revoked_keys:
                failure_reasons.append(f"TOKEN_KEY_REVOKED: Signing key '{token.kid}' has been revoked.")

            if token.jti in crl_cache.revoked_jtis:
                failure_reasons.append(f"TOKEN_REVOKED: Token JTI '{token.jti}' has been explicitly revoked.")

        # 5. Cryptographic Signature Verification
        try:
            is_sig_valid = self.signature_engine.verify_signature(
                kid=token.kid,
                signature=signature_bytes,
                payload_bytes=payload_bytes,
                alg=token.alg,
                verification_time=now,
            )
            if not is_sig_valid:
                failure_reasons.append("SIGNATURE_VERIFICATION_FAILED: Digital signature could not be verified.")
        except UnapprovedAlgorithmException as exc:
            failure_reasons.append(str(exc))
        except KeyExpiredException as exc:
            failure_reasons.append(str(exc))
        except KeyRevokedException as exc:
            failure_reasons.append(str(exc))
        except Exception as exc:
            failure_reasons.append(f"SIGNATURE_ERROR: {exc}")

        # 6. Contextual Binding Verification
        binding = token.binding
        ctx_voyage_id = execution_context.get("voyage_id")
        ctx_vessel_imo = execution_context.get("vessel_imo")
        ctx_transit_date = str(execution_context.get("transit_date"))
        ctx_record_id = execution_context.get("disbursement_record_id")
        ctx_content_hash = execution_context.get("disbursement_content_hash")

        if binding.voyage_id != ctx_voyage_id:
            failure_reasons.append(f"SECURITY_BINDING_MISMATCH: voyage_id '{ctx_voyage_id}' != '{binding.voyage_id}'.")
        if binding.vessel_imo != ctx_vessel_imo:
            failure_reasons.append(f"SECURITY_BINDING_MISMATCH: vessel_imo '{ctx_vessel_imo}' != '{binding.vessel_imo}'.")
        if binding.transit_date != ctx_transit_date:
            failure_reasons.append(f"SECURITY_BINDING_MISMATCH: transit_date '{ctx_transit_date}' != '{binding.transit_date}'.")
        if ctx_record_id is not None and binding.disbursement_record_id != ctx_record_id:
            failure_reasons.append(f"SECURITY_BINDING_MISMATCH: disbursement_record_id mismatch.")
        if ctx_content_hash is not None and binding.disbursement_content_hash.lower() != ctx_content_hash.lower():
            failure_reasons.append(f"SECURITY_BINDING_MISMATCH: disbursement_content_hash mismatch.")

        # 7. Single-Use Anti-Replay Check & Durable Persistence
        with self._lock:
            if token.jti in self.in_memory_consumed_jtis:
                failure_reasons.append(f"REPLAYED_OPERATIONAL_TOKEN_REJECTED: JTI '{token.jti}' already consumed in memory.")

        if db is not None:
            from pelagos.models import ConsumedOperationalToken
            existing = db.query(ConsumedOperationalToken).filter(ConsumedOperationalToken.jti == token.jti).first()
            if existing:
                failure_reasons.append(f"REPLAYED_OPERATIONAL_TOKEN_REJECTED: JTI '{token.jti}' already in database ledger.")

        if failure_reasons:
            return TokenValidationResult(
                is_valid=False,
                status="TOKEN_VERIFICATION_FAILED",
                jti=token.jti,
                token_category=token.token_category.value,
                failure_reasons=failure_reasons,
            )

        # 8. Record Consumption (Inside transaction if DB present; Thread-safe & Exception-guarded)
        with self._lock:
            if token.jti in self.in_memory_consumed_jtis:
                return TokenValidationResult(
                    is_valid=False,
                    status="CONCURRENT_REPLAY_DETECTED",
                    jti=token.jti,
                    token_category=token.token_category.value,
                    failure_reasons=[f"CONCURRENT_REPLAY: Token JTI '{token.jti}' consumed concurrently in memory."],
                )
            self.in_memory_consumed_jtis.add(token.jti)

        if db is not None:
            import hashlib
            from pelagos.models import ConsumedOperationalToken
            consumed_rec = ConsumedOperationalToken(
                jti=token.jti,
                consumed_at=now,
                voyage_id=token.binding.voyage_id,
                vessel_imo=token.binding.vessel_imo,
                disbursement_record_id=token.binding.disbursement_record_id,
                caller_identity=token.iss,
                signature_hash=hashlib.sha256(signature_bytes).hexdigest(),
            )
            db.add(consumed_rec)
            try:
                db.flush()
            except IntegrityError:
                try:
                    db.rollback()
                except Exception:
                    pass
                return TokenValidationResult(
                    is_valid=False,
                    status="CONCURRENT_REPLAY_DETECTED",
                    jti=token.jti,
                    token_category=token.token_category.value,
                    failure_reasons=[f"CONCURRENT_REPLAY: Token JTI '{token.jti}' consumed concurrently in database."],
                )

        return TokenValidationResult(
            is_valid=True,
            status="TOKEN_VERIFIED_AND_CONSUMED",
            jti=token.jti,
            token_category=token.token_category.value,
            consumed_at=now.isoformat(),
        )

