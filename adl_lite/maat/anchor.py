"""Maat-style versioned workflow anchor for ADL Lite.

An *anchor* is the explicit, versioned contract a task workflow is authorized
to preserve or obey: which roles may hand off, which resources are shared,
when a rework loop must be escalated. Inspired by Maat (arXiv:2609.34017) —
the validation path is deterministic and contains no LLM — with one lesson
from that paper baked into the design: the anchor itself is part of the
tested system, so every anchor revision is hash-anchored into its own
append-only revision chain before it may govern a handoff.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def canonical_hash(data: dict[str, Any]) -> str:
    """Deterministic SHA-256 over canonically serialized anchor content."""
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class AnchorSpec(BaseModel):
    """Versioned governance contract for the task control plane."""

    anchor_id: str = "adl-task-governance"
    version: int = 1
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Gate switches — deterministic, no LLM in the validation path.
    enforce_identity: bool = True
    enforce_lineage: bool = True
    enforce_circuit_breaker: bool = True
    cascade_cancel: bool = True
    validator_not_submitter: bool = True

    # Circuit breaker: a REJECTED → re-claim loop counting to this threshold
    # is halted; further rejection is refused and the task must be escalated.
    rework_threshold: int = 20

    # Resource topology (Sui-style): resources in shared_resources are
    # exclusive objects — a task holding one blocks concurrent holders;
    # everything else is an exclusive object and flows lock-free.
    shared_resources: list[str] = Field(default_factory=list)

    # Per-event-type required payload fields (structure gate).
    required_payload_fields: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "task_submit": ["result_ref"],
            "task_validate": ["accepted"],
        }
    )

    def content_dict(self) -> dict[str, Any]:
        """Canonical content for hashing (excludes derived fields)."""
        return self.model_dump()

    def anchor_hash(self) -> str:
        return canonical_hash(self.content_dict())

    def permits(self, gate: str) -> bool:
        switch = {
            "identity": self.enforce_identity,
            "lineage": self.enforce_lineage,
            "circuit_breaker": self.enforce_circuit_breaker,
        }
        return switch.get(gate, True)


class AnchorChain:
    """Append-only, hash-linked history of anchor revisions.

    Maat's post-publication audit showed validator misconfiguration caused
    37% false-alarm halts — the contract is part of the system. Anchoring
    every revision makes anchor history tamper-evident and replayable.
    """

    def __init__(self, anchor_id: str = "adl-task-governance") -> None:
        self.anchor_id = anchor_id
        self.revisions: list[dict[str, Any]] = []

    def _hash(self, entry: dict[str, Any], prev_hash: str) -> str:
        return canonical_hash({"entry": entry, "prev": prev_hash})

    def append(self, spec: AnchorSpec, actor: str = "governance") -> dict[str, Any]:
        entry = {
            "anchor_id": spec.anchor_id,
            "version": spec.version,
            "anchor_hash": spec.anchor_hash(),
            "actor": actor,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        prev = self.revisions[-1]["chain_hash"] if self.revisions else ""
        entry["chain_hash"] = self._hash(entry, prev)
        self.revisions.append(entry)
        return entry

    def verify(self) -> bool:
        prev = ""
        for entry in self.revisions:
            expect = self._hash(
                {k: v for k, v in entry.items() if k != "chain_hash"}, prev
            )
            if entry["chain_hash"] != expect:
                return False
            prev = entry["chain_hash"]
        return True

    def current(self) -> dict[str, Any] | None:
        return self.revisions[-1] if self.revisions else None
