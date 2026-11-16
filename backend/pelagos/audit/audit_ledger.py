"""
Pelagos Stage F1.4.3: Append-Only Chronological Audit Ledger Service (P2-D Core).

Implements strictly sequential, tamper-evident hash chaining for platform events:
- Atomic monotonic sequence numbers (seq = 1, 2, 3, ...).
- SHA-256 hash chaining back to the genesis root using domain separation prefix (0x02).
- INVARIANT: Chronological audit events are strictly append-only and NEVER sorted lexicographically.
- Records fine-grained attestation signatures, Merkle roots, and actor identities.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

from pelagos.models import VoyageDisbursementAuditLog
from pelagos.security.canonical_json import canonicalize_financial_payload

# Domain separation tag for chronological audit ledger links
CHAIN_LINK_PREFIX = b"\x02"

# Genesis chain root constant: SHA-256 of genesis string
GENESIS_CHAIN_ROOT_HEX = hashlib.sha256(b"PELAGOS_GENESIS_AUDIT_LEDGER_V1").hexdigest()
GENESIS_CHAIN_ROOT = hashlib.sha256(b"PELAGOS_GENESIS_AUDIT_LEDGER_V1").digest()


class AuditEventType(str, Enum):
    """Categorized audit ledger event types."""
    RECORD_CREATED = "RECORD_CREATED"
    TOKEN_CONSUMED = "TOKEN_CONSUMED"
    AUDIT_VERIFIED = "AUDIT_VERIFIED"
    TAMPER_DETECTED = "TAMPER_DETECTED"
    COMMERCIAL_WAIVER_GRANTED = "COMMERCIAL_WAIVER_GRANTED"


class AuditEventPayload(BaseModel):
    """Canonical event payload to be hashed into the chronological chain."""
    sequence_number: int
    event_id: str
    timestamp_utc: str
    actor_identity: str
    event_type: str
    record_id: int
    voyage_id: str
    vessel_imo: int
    merkle_root: str
    record_payload_hash: str
    attestation_signature: str
    signing_key_id: str


def compute_chain_hash(previous_chain_hash_hex: str, event_payload: Dict[str, Any]) -> str:
    """
    Compute next chronological chain hash using domain separation prefix 0x02:
    H_chain^(k) = SHA256( 0x02 || H_chain^(k-1) || CanonicalJSON(E_k) )
    """
    prev_bytes = bytes.fromhex(previous_chain_hash_hex)
    payload_bytes = canonicalize_financial_payload(event_payload)
    return hashlib.sha256(CHAIN_LINK_PREFIX + prev_bytes + payload_bytes).hexdigest()


class AuditLedgerService:
    """
    Appends events to the chronological audit ledger and maintains the SHA-256 hash chain.
    """

    def __init__(self, db: Session):
        self.db = db

    def get_latest_sequence_and_hash(self) -> tuple[int, str]:
        """Query highest sequence number and chain hash currently in ledger."""
        latest = (
            self.db.query(VoyageDisbursementAuditLog)
            .order_by(VoyageDisbursementAuditLog.sequence_number.desc())
            .first()
        )
        if latest is None:
            return 0, GENESIS_CHAIN_ROOT_HEX
        return latest.sequence_number, latest.chain_hash

    def record_disbursement_creation(
        self,
        record_id: int,
        voyage_id: str,
        vessel_imo: int,
        merkle_root: str,
        record_payload_hash: str,
        attestation_signature: str,
        signing_key_id: str,
        actor_identity: str = "pelagos:engine:settlement",
        timestamp: Optional[datetime.datetime] = None,
    ) -> VoyageDisbursementAuditLog:
        """
        Record the creation and cryptographic sealing of a VoyageDisbursementRecord.
        """
        t = timestamp or datetime.datetime.now(datetime.timezone.utc)
        latest_seq, prev_hash = self.get_latest_sequence_and_hash()
        next_seq = latest_seq + 1
        event_id = str(uuid.uuid4())

        payload_dict = {
            "sequence_number": next_seq,
            "record_id": record_id,
            "voyage_id": voyage_id,
            "vessel_imo": vessel_imo,
            "event_type": AuditEventType.RECORD_CREATED.value,
            "merkle_root": merkle_root,
            "record_payload_hash": record_payload_hash,
            "attestation_signature": attestation_signature,
            "signing_key_id": signing_key_id,
        }

        chain_hash = compute_chain_hash(prev_hash, payload_dict)

        log_entry = VoyageDisbursementAuditLog(
            sequence_number=next_seq,
            record_id=record_id,
            voyage_id=voyage_id,
            vessel_imo=vessel_imo,
            event_type=AuditEventType.RECORD_CREATED.value,
            merkle_root=merkle_root,
            record_payload_hash=record_payload_hash,
            attestation_signature=attestation_signature,
            signing_key_id=signing_key_id,
            previous_chain_hash=prev_hash,
            chain_hash=chain_hash,
            created_at=t,
        )

        self.db.add(log_entry)
        self.db.flush()
        return log_entry
