"""FIDES — multi-party approvals with real signatures (review finding C2).

`board_multi_signoff` was previously a string label: the acceptance route
was *computed* but never *executed* — no signatures were collected,
verified, or stored. This module makes approval a first-class, verifiable
object:

- `KeyRing` — holds Ed25519 keypairs for governance DIDs and produces
  `SignedApproval`s. A keypair is a stand-in for an agent's DID
  document; in production the signing key lives in the agent's own
  custody and only the public key is registered here.
- `SignedApproval` — an Ed25519 signature over the acceptance domain:
  {candidate_hash, scorecard_hash, bench_commitment, engine_id}. The
  domain binding means an approval for one candidate/scorecard/bench
  state cannot be replayed for another, and an approval captured for a
  different engine cannot be transplanted.

Verification requires the approver's public key (registered in the
KeyRing under the DID) and re-derives the exact signed payload. A
signature over any other payload — even for the same candidate — fails.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def _canon(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, default=str).encode("utf-8")


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@dataclass(frozen=True)
class SignedApproval:
    """A verifiable approval decision bound to its acceptance domain."""

    did: str
    signature: str            # hex Ed25519 signature over the domain payload
    payload_hash: str         # sha256 of the canonical domain payload
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def verify(self, public_key: Ed25519PublicKey, domain: dict) -> bool:
        """Re-derive the exact payload that was signed and check both the
        signature and the domain binding."""
        if sha256_hex(_canon(domain)) != self.payload_hash:
            return False
        try:
            public_key.verify(bytes.fromhex(self.signature), _canon(domain))
            return True
        except (InvalidSignature, ValueError):
            return False


class KeyRing:
    """DID → Ed25519 keypair registry for governance actors.

    `generate(did)` creates a keypair; `register(did, private_key_hex)`
    adopts an externally-held key. Only the engine (verifier) needs the
    public halves; signing happens through `approve()` here for the
    reference implementation.
    """

    def __init__(self) -> None:
        self._private: dict[str, Ed25519PrivateKey] = {}
        self._public: dict[str, Ed25519PublicKey] = {}

    def generate(self, did: str) -> None:
        sk = Ed25519PrivateKey.generate()
        self._private[did] = sk
        self._public[did] = sk.public_key()

    def register(self, did: str, private_key_hex: str) -> None:
        sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
        self._private[did] = sk
        self._public[did] = sk.public_key()

    def public_key(self, did: str) -> Ed25519PublicKey:
        if did not in self._public:
            raise KeyError(f"no registered key for did {did!r}")
        return self._public[did]

    def approve(self, did: str, domain: dict) -> SignedApproval:
        """Produce a SignedApproval over the acceptance domain."""
        if did not in self._private:
            raise KeyError(f"no signing key for did {did!r}")
        payload_hash = sha256_hex(_canon(domain))
        sig = self._private[did].sign(_canon(domain)).hex()
        return SignedApproval(did=did, signature=sig, payload_hash=payload_hash)

    def verify(self, approval: SignedApproval, domain: dict) -> bool:
        """Verify an approval against the registered public key."""
        if approval.did not in self._public:
            return False
        return approval.verify(self._public[approval.did], domain)


def acceptance_domain(
    *,
    engine_id: str,
    candidate_hash: str,
    scorecard_hash: str,
    bench_commitment: str,
) -> dict:
    """The canonical domain every approval signature covers. Any change to
    candidate content, scorecard, bench state, or engine identity invalidates
    all outstanding approvals."""
    return {
        "engine_id": engine_id,
        "candidate_hash": candidate_hash,
        "scorecard_hash": scorecard_hash,
        "bench_commitment": bench_commitment,
    }


# Route → required approval policy, shared by Phase 2 and Phase 3 engines.
# auto_accept needs the actor's own attestation only; everything else needs
# independent signatures, and board routes need multiple distinct DIDs
# (review finding C2: the route is EXECUTED, not merely computed).
APPROVAL_POLICY: dict[str, dict[str, int]] = {
    "auto_accept": {"min_signers": 1, "min_distinct_dids": 1,
                    "reviewer_must_differ": False},
    "single_reviewer": {"min_signers": 1, "min_distinct_dids": 1,
                        "reviewer_must_differ": True},
    "board": {"min_signers": 1, "min_distinct_dids": 1,
              "reviewer_must_differ": True},
    "board_multi_signoff": {"min_signers": 2, "min_distinct_dids": 2,
                            "reviewer_must_differ": True},
}
