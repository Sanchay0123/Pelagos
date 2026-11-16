"""
Pelagos Stage F1.4.3: RFC 6962-Compatible Merkle Set Aggregator (Merkle Multi-Set Hash).

Terminology & Conformance Specification (FINDING-P2-004):
This component implements an RFC 6962-compatible static binary Merkle multi-set aggregator
for voyage disbursement line items.
- Conformance with RFC 6962 §2.1:
  * Strict byte-level domain separation prefixes (0x00 for leaf hashes, 0x01 for internal node hashes).
  * Power-of-two recursive splitting: k = 2^(ceil(log2(n)) - 1) for n nodes without leaf duplication.
  * Empty set definition: SHA-256 of empty byte string (0 leaves -> SHA256(b"")).
  * Standard O(log N) inclusion proof generation and audit path verification.
- Distinction from RFC 6962 Certificate Transparency (CT) Logs:
  * RFC 6962 CT logs are append-only sequential timelines where leaf ordering preserves arrival sequence.
  * In commercial maritime disbursement auditing, line items may be presented in arbitrary order by different
    port agents or charterers. To guarantee multi-set order invariance across accounting batches,
    RecordSetMerkleTree sorts leaf hashes lexicographically prior to tree construction.
  * Duplicate line items are preserved as distinct occurrences in the sorted leaf sequence (multi-set semantics).
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, List, Optional, Tuple

from pelagos.security.canonical_json import canonicalize_financial_payload

# Domain separation prefixes (RFC 6962 §2.1)
LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"

# Empty tree root constant: SHA-256 of empty byte string
EMPTY_TREE_ROOT = hashlib.sha256(b"").digest()
EMPTY_TREE_ROOT_HEX = hashlib.sha256(b"").hexdigest()


def hash_leaf(item: Any) -> bytes:
    """Compute leaf hash using RFC 6962 byte-level domain separation prefix (0x00)."""
    canonical_bytes = canonicalize_financial_payload(item)
    return hashlib.sha256(LEAF_PREFIX + canonical_bytes).digest()


def hash_node(left: bytes, right: bytes) -> bytes:
    """Compute internal node hash using domain separation prefix (0x01)."""
    return hashlib.sha256(NODE_PREFIX + left + right).digest()


def _largest_power_of_two_less_than(n: int) -> int:
    """Return largest power of two strictly less than n (for n > 1)."""
    if n <= 1:
        return 0
    # Equivalent to 2^(ceil(log2(n)) - 1) when n is not a power of 2,
    # and n/2 when n is a power of 2.
    k = 1
    while k * 2 < n:
        k *= 2
    return k


class RecordSetMerkleTree:
    """
    RFC 6962-Compatible Merkle Set Aggregator (Merkle Multi-Set Hash) for disbursement line items.
    
    Invariants:
    1. Order Invariance: Leaves are sorted lexicographically by leaf hash before building the tree,
       satisfying multi-set equivalence across independently ordered disbursement vouchers.
    2. Zero Duplication: Odd-node counts are partitioned via RFC 6962 §2.1 binary split rather than duplicating leaves.
    3. Domain Separation: 0x00 for leaves and 0x01 for internal nodes prevents second-preimage attacks.
    4. Multi-set preservation: Identical items appear as duplicate sorted leaves rather than being deduplicated.
    """

    def __init__(self, items: Optional[List[Any]] = None):
        self.raw_items: List[Any] = items or []
        self.leaf_hashes: List[bytes] = []
        self._build_tree()

    def _build_tree(self) -> None:
        """Hash all items, sort leaves lexicographically, and compute root."""
        if not self.raw_items:
            self.leaf_hashes = []
            self.root = EMPTY_TREE_ROOT
            return

        # 1. Compute leaf hashes
        leaves = [hash_leaf(item) for item in self.raw_items]

        # 2. Sort leaves lexicographically by raw byte values
        self.leaf_hashes = sorted(leaves)

        # 3. Compute Merkle root via RFC 6962 recursive construction
        self.root = self._compute_subtree_root(self.leaf_hashes)

    def _compute_subtree_root(self, leaves: List[bytes]) -> bytes:
        """Recursively compute root of leaf slice conforming to RFC 6962 §2.1."""
        n = len(leaves)
        if n == 0:
            return EMPTY_TREE_ROOT
        if n == 1:
            return leaves[0]

        k = _largest_power_of_two_less_than(n)
        left_root = self._compute_subtree_root(leaves[:k])
        right_root = self._compute_subtree_root(leaves[k:])
        return hash_node(left_root, right_root)

    @property
    def root_hex(self) -> str:
        """Return the Merkle root hash as a lowercase hexadecimal string."""
        return self.root.hex()

    def generate_inclusion_proof(self, item: Any) -> Optional[List[Tuple[str, str]]]:
        """
        Generate inclusion proof for an item.
        Returns a list of (direction, sibling_hash_hex) tuples, or None if item not present.
        """
        if not self.leaf_hashes:
            return None

        target_leaf = hash_leaf(item)
        if target_leaf not in self.leaf_hashes:
            return None

        idx = self.leaf_hashes.index(target_leaf)
        proof: List[Tuple[str, str]] = []
        self._build_proof_recursive(self.leaf_hashes, idx, proof)
        return proof

    def _build_proof_recursive(
        self,
        leaves: List[bytes],
        target_idx: int,
        proof: List[Tuple[str, str]],
    ) -> None:
        """Recursively build inclusion proof matching RFC 6962 tree structure."""
        n = len(leaves)
        if n <= 1:
            return

        k = _largest_power_of_two_less_than(n)
        if target_idx < k:
            # Target is in left subtree; sibling is root of right subtree
            sibling = self._compute_subtree_root(leaves[k:])
            proof.append(("right", sibling.hex()))
            self._build_proof_recursive(leaves[:k], target_idx, proof)
        else:
            # Target is in right subtree; sibling is root of left subtree
            sibling = self._compute_subtree_root(leaves[:k])
            proof.append(("left", sibling.hex()))
            self._build_proof_recursive(leaves[k:], target_idx - k, proof)

    @classmethod
    def verify_inclusion_proof(
        cls,
        item: Any,
        proof: List[Tuple[str, str]],
        expected_root_hex: str,
    ) -> bool:
        """
        Verify that an item is included in the Merkle tree root using its audit proof.
        """
        current_hash = hash_leaf(item)

        # Audit path is generated from root down; evaluate from leaf up (reverse proof)
        for direction, sibling_hex in reversed(proof):
            sibling = bytes.fromhex(sibling_hex)
            if direction == "left":
                current_hash = hash_node(sibling, current_hash)
            elif direction == "right":
                current_hash = hash_node(current_hash, sibling)
            else:
                return False

        return current_hash.hex() == expected_root_hex.lower()

