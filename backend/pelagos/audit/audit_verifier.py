"""
Pelagos Stage F1.4.3: Out-of-Band Audit Verifier Service (P2-D Core).

Traverses and independently verifies:
1. Chronological Audit Chain: Strictly monotonic sequence numbers, unbroken SHA-256 hash chaining back to genesis.
2. Record Merkle Integrity: Recomputes RFC 6962 Merkle root from normalized line items.
3. Record Header Attestation: Recomputes payload hash and verifies asymmetric digital signature.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pelagos.models import VoyageDisbursementRecord, VoyageDisbursementLineItem, VoyageDisbursementAuditLog
from pelagos.security.canonical_json import canonicalize_financial_payload, normalize_decimal_string
from pelagos.security.merkle import RecordSetMerkleTree
from pelagos.security.signature_engine import SignatureEngine
from pelagos.audit.audit_ledger import (
    GENESIS_CHAIN_ROOT_HEX,
    CHAIN_LINK_PREFIX,
    compute_chain_hash,
)


class AuditChainVerificationResult(BaseModel):
    """Result of chronological audit chain traversal and verification."""
    is_chain_valid: bool
    total_events_checked: int
    latest_sequence_number: int
    latest_chain_hash: Optional[str] = None
    anomalies_detected: List[str] = Field(default_factory=list)


class RecordIntegrityResult(BaseModel):
    """Result of line-item Merkle tree and signature recomputation for a single record."""
    record_id: int
    is_intact: bool
    merkle_root_matches: bool
    payload_hash_matches: bool
    signature_valid: bool
    stored_merkle_root: Optional[str] = None
    recomputed_merkle_root: Optional[str] = None
    stored_payload_hash: Optional[str] = None
    recomputed_payload_hash: Optional[str] = None
    failure_reasons: List[str] = Field(default_factory=list)


def build_canonical_record_header(record: VoyageDisbursementRecord, merkle_root_hex: str) -> Dict[str, Any]:
    """Construct canonical record header dictionary for payload hashing."""
    return {
        "voyage_id": record.voyage_id,
        "vessel_imo": record.vessel_imo,
        "origin_port": record.origin_port_unlocode,
        "destination_port": record.destination_port_unlocode,
        "transit_date": record.transit_date.isoformat(),
        "total_disbursement_usd": normalize_decimal_string(record.total_disbursement_usd or 0.0, precision=2),
        "readiness_tier": record.readiness_tier,
        "merkle_root": merkle_root_hex.lower(),
    }


def compute_record_payload_hash(record: VoyageDisbursementRecord, merkle_root_hex: str) -> str:
    """Compute SHA-256 payload hash over canonical record header."""
    header = build_canonical_record_header(record, merkle_root_hex)
    canonical_bytes = canonicalize_financial_payload(header)
    return hashlib.sha256(b"\x00" + canonical_bytes).hexdigest()


class DisbursementAuditVerifier:
    """
    Automated verification gate for detecting database tampering and ledger disruption.
    """

    def __init__(self, db: Session, signature_engine: Optional[SignatureEngine] = None):
        self.db = db
        self.signature_engine = signature_engine or SignatureEngine()

    def verify_audit_chain(self) -> AuditChainVerificationResult:
        """
        Traverse the entire chronological audit chain from sequence 1 to M.
        
        Verifies:
        - Strictly monotonic sequence numbering with zero gaps.
        - First entry links to genesis root.
        - Subsequent entries link to preceding entry's chain_hash.
        - Recomputed hash matches stored chain_hash for every entry.
        """
        entries = (
            self.db.query(VoyageDisbursementAuditLog)
            .order_by(VoyageDisbursementAuditLog.sequence_number.asc())
            .all()
        )

        if not entries:
            return AuditChainVerificationResult(
                is_chain_valid=True,
                total_events_checked=0,
                latest_sequence_number=0,
                latest_chain_hash=GENESIS_CHAIN_ROOT_HEX,
            )

        anomalies: List[str] = []
        expected_seq = 1
        expected_prev_hash = GENESIS_CHAIN_ROOT_HEX

        for entry in entries:
            # 1. Monotonic sequence check
            if entry.sequence_number != expected_seq:
                anomalies.append(
                    f"TAMPER_DETECTED_SEQUENCE_GAP: Expected sequence {expected_seq}, found {entry.sequence_number}."
                )

            # 2. Previous hash link check
            if entry.previous_chain_hash.lower() != expected_prev_hash.lower():
                anomalies.append(
                    f"TAMPER_DETECTED_CHAIN_BROKEN: Entry seq {entry.sequence_number} previous_chain_hash "
                    f"'{entry.previous_chain_hash}' != expected '{expected_prev_hash}'."
                )

            # 3. Recompute chain hash
            payload_dict = {
                "sequence_number": entry.sequence_number,
                "record_id": entry.record_id,
                "voyage_id": entry.voyage_id,
                "vessel_imo": entry.vessel_imo,
                "event_type": entry.event_type,
                "merkle_root": entry.merkle_root,
                "record_payload_hash": entry.record_payload_hash,
                "attestation_signature": entry.attestation_signature,
                "signing_key_id": entry.signing_key_id,
            }
            try:
                recomputed_chain_hash = compute_chain_hash(expected_prev_hash, payload_dict)
                if entry.chain_hash.lower() != recomputed_chain_hash.lower():
                    anomalies.append(
                        f"TAMPER_DETECTED_HASH_MISMATCH: Entry seq {entry.sequence_number} chain_hash "
                        f"'{entry.chain_hash}' != recomputed '{recomputed_chain_hash}'."
                    )
            except Exception as exc:
                anomalies.append(
                    f"TAMPER_DETECTED_CORRUPTED_CHAIN_HASH: Entry seq {entry.sequence_number} hash computation failed: {exc}."
                )

            # Advance chain tracking
            expected_prev_hash = entry.chain_hash
            expected_seq = entry.sequence_number + 1

        is_valid = len(anomalies) == 0
        return AuditChainVerificationResult(
            is_chain_valid=is_valid,
            total_events_checked=len(entries),
            latest_sequence_number=entries[-1].sequence_number if entries else 0,
            latest_chain_hash=entries[-1].chain_hash if entries else GENESIS_CHAIN_ROOT_HEX,
            anomalies_detected=anomalies,
        )

    def verify_record_integrity(self, record_id: int) -> RecordIntegrityResult:
        """
        Recompute Merkle tree root and payload hash for a VoyageDisbursementRecord.
        Detects unauthorized database edits on line items, amounts, or record headers.
        """
        record = self.db.query(VoyageDisbursementRecord).filter(VoyageDisbursementRecord.id == record_id).first()
        if not record:
            return RecordIntegrityResult(
                record_id=record_id,
                is_intact=False,
                merkle_root_matches=False,
                payload_hash_matches=False,
                signature_valid=False,
                failure_reasons=[f"Record ID {record_id} not found."],
            )

        failure_reasons: List[str] = []

        # 1. Recompute Merkle root from normalized line items
        line_items_data = []
        for li in record.line_items:
            line_items_data.append({
                "tariff_identifier": li.tariff_identifier,
                "description": li.description,
                "charge_category": li.charge_category,
                "amount_usd": normalize_decimal_string(li.amount_usd or 0.0, precision=2),
                "local_currency": li.local_currency,
                "applicability_type": li.applicability_type,
            })

        tree = RecordSetMerkleTree(line_items_data)
        recomputed_root = tree.root_hex
        stored_root = record.merkle_root or ""
        merkle_matches = (recomputed_root.lower() == stored_root.lower())

        if not merkle_matches:
            failure_reasons.append(
                f"TAMPER_DETECTED_MERKLE_MISMATCH: Stored root '{stored_root}' != Recomputed '{recomputed_root}'."
            )

        # 2. Recompute Record Payload Hash
        recomputed_payload_hash = compute_record_payload_hash(record, recomputed_root)
        stored_payload_hash = record.record_payload_hash or ""
        payload_matches = (recomputed_payload_hash.lower() == stored_payload_hash.lower())

        if not payload_matches:
            failure_reasons.append(
                f"TAMPER_DETECTED_PAYLOAD_MISMATCH: Stored hash '{stored_payload_hash}' != Recomputed '{recomputed_payload_hash}'."
            )

        # 3. Verify Asymmetric Digital Signature
        sig_valid = False
        if record.attestation_signature and record.signing_key_id:
            try:
                import base64
                sig_bytes = base64.b64decode(record.attestation_signature)
                payload_bytes = bytes.fromhex(recomputed_payload_hash)
                sig_valid = self.signature_engine.verify_signature(
                    kid=record.signing_key_id,
                    signature=sig_bytes,
                    payload_bytes=payload_bytes,
                    alg="Ed25519",
                )
                if not sig_valid:
                    failure_reasons.append("TAMPER_DETECTED_SIGNATURE_INVALID: Asymmetric signature verification failed.")
            except Exception as exc:
                failure_reasons.append(f"SIGNATURE_VERIFICATION_ERROR: {exc}")
        else:
            failure_reasons.append("MISSING_ATTESTATION_SIGNATURE: Record has no attestation signature on file.")

        is_intact = merkle_matches and payload_matches and sig_valid
        return RecordIntegrityResult(
            record_id=record_id,
            is_intact=is_intact,
            merkle_root_matches=merkle_matches,
            payload_hash_matches=payload_matches,
            signature_valid=sig_valid,
            stored_merkle_root=stored_root,
            recomputed_merkle_root=recomputed_root,
            stored_payload_hash=stored_payload_hash,
            recomputed_payload_hash=recomputed_payload_hash,
            failure_reasons=failure_reasons,
        )
