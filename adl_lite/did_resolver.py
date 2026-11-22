"""DID Resolver for ADL Lite — did:key (local minimal copy)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

ED25519_MULTICODEC_PREFIX = bytes([0xED, 0x01])
DID_METHODS = ("key", "web", "ethr")


@dataclass
class VerificationMethod:
    id: str
    type: str
    controller: str
    public_key_bytes: bytes
    format: str
    address: str | None = None


@dataclass
class DIDDocument:
    id: str
    verification_methods: list[VerificationMethod] = field(default_factory=list)
    assertion_method: list[str] = field(default_factory=list)

    def key_for_purpose(self, purpose: str = "assertionMethod") -> VerificationMethod | None:
        ids = self.assertion_method if purpose == "assertionMethod" else []
        if not ids:
            ids = [vm.id for vm in self.verification_methods]
        by_id = {vm.id: vm for vm in self.verification_methods}
        for vm_id in ids:
            vm = by_id.get(vm_id)
            if vm:
                return vm
        return None


def _base58btc_decode(s: str) -> bytes:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    base = len(alphabet)
    num_leading_zeros = 0
    for ch in s:
        if ch == "1":
            num_leading_zeros += 1
        else:
            break
    num = 0
    for ch in s:
        num = num * base + alphabet.index(ch)
    result = num.to_bytes((num.bit_length() + 7) // 8, "big")
    return b"\x00" * num_leading_zeros + result


def _base58btc_encode(data: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    base = len(alphabet)
    num = int.from_bytes(data, "big")
    if num == 0:
        return "1" * len(data)
    result = ""
    while num > 0:
        num, rem = divmod(num, base)
        result = alphabet[rem] + result
    leading_zeros = len(data) - len(data.lstrip(b"\x00"))
    return "1" * leading_zeros + result


def resolve_did_key(did: str, **_kwargs: Any) -> DIDDocument:
    if not did.startswith("did:key:"):
        raise ValueError(f"Only did:key is supported; got {did}")
    match = re.match(r"did:key:z([a-km-zA-HJ-NP-Z1-9]+)", did)
    if not match:
        raise ValueError(f"Invalid did:key format: {did}")
    raw_bytes = _base58btc_decode(match.group(1))
    if not raw_bytes.startswith(ED25519_MULTICODEC_PREFIX):
        raise ValueError("Unexpected multicodec prefix")
    pub_key_bytes = raw_bytes[len(ED25519_MULTICODEC_PREFIX) :]
    if len(pub_key_bytes) != 32:
        raise ValueError(f"Expected 32-byte Ed25519 public key, got {len(pub_key_bytes)}")
    return DIDDocument(
        id=did,
        verification_methods=[
            VerificationMethod(
                id=f"{did}#{did.split(':')[-1]}",
                type="Ed25519VerificationKey2020",
                controller=did,
                public_key_bytes=pub_key_bytes,
                format="multibase",
            )
        ],
        assertion_method=[f"{did}#{did.split(':')[-1]}"],
    )


def _ed25519_public_key_from_doc(doc: DIDDocument) -> ed25519.Ed25519PublicKey:
    vm = doc.key_for_purpose("assertionMethod")
    if vm is None or vm.type not in ("Ed25519VerificationKey2020", "Ed25519VerificationKey2018"):
        raise ValueError("DID document has no usable Ed25519 assertion method")
    return ed25519.Ed25519PublicKey.from_public_bytes(vm.public_key_bytes)


def create_did_key(public_key: ed25519.Ed25519PublicKey) -> str:
    pub_key_bytes = public_key.public_bytes_raw()
    raw_bytes = ED25519_MULTICODEC_PREFIX + pub_key_bytes
    return f"did:key:z{_base58btc_encode(raw_bytes)}"


def resolve_did(did: str, **kwargs: Any) -> DIDDocument:
    return resolve_did_key(did, **kwargs)


def is_did(actor: str) -> bool:
    if not isinstance(actor, str) or not actor.startswith("did:"):
        return False
    parts = actor.split(":")
    return len(parts) >= 3 and parts[1] in DID_METHODS


def verify_did_signature(did: str, message: bytes, signature: bytes, **kwargs: Any) -> bool:
    try:
        doc = resolve_did_key(did)
        pk = _ed25519_public_key_from_doc(doc)
        pk.verify(signature, message)
        return True
    except (ValueError, InvalidSignature):
        return False
