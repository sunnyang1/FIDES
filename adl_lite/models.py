"""ADL Lite core data models (local minimal copy for Maat governance dev)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, PrivateAttr, field_validator

from .crdt import StatusOrder

CANON_VERSION = "1.0"


def _round_floats(obj: Any, ndigits: int = 6) -> Any:
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: _round_floats(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(item, ndigits) for item in obj]
    return obj


class DiscoveryStatus(str, Enum):
    PROVISIONAL = "provisional"
    VALIDATED = "validated"
    DEPRECATED = "deprecated"
    FORKED = "forked"
    ARCHIVED = "archived"


class ADLType(str, Enum):
    DISCOVERY = "discovery"
    CONCEPT = "concept"
    RELATION = "relation"
    EVIDENCE = "evidence"
    FORMAL_SEAL = "formal_seal"


class MechanismType(str, Enum):
    ISOMORPHIC_MAPPING = "isomorphic_mapping"
    ANALOGICAL_TRANSFER = "analogical_transfer"
    COMPOSITIONAL_BLEND = "compositional_blend"
    ABSTRACT_GENERALISATION = "abstract_generalisation"
    EMERGENT_PATTERN = "emergent_pattern"


class EvidenceType(str, Enum):
    VECTOR_CLUSTER = "vector_cluster"
    SIMULATOR_RUN = "simulator_run"
    HUMAN_EXPERT = "human_expert"
    CROSS_REFERENCE = "cross_reference"
    EMPIRICAL_OBSERVATION = "empirical_observation"


class EventType(str, Enum):
    REGISTER = "register"
    VALIDATE = "validate"
    DEPRECATE = "deprecate"
    FORK = "fork"
    ARCHIVE = "archive"
    CALIBRATE = "calibrate"
    RELATE = "relate"
    EVIDENCE = "evidence"
    SEAL = "seal"
    REVOKE = "revoke"
    ANNOUNCE = "announce"
    PUBLISH = "publish"
    SYNC_DASHBOARD = "sync_dashboard"
    LISTEN = "listen"
    SNAPSHOT = "snapshot"
    EXECUTE = "execute"
    ATTEST = "attest"
    EXEC_ANCHOR = "exec_anchor"
    CHALLENGE = "challenge"
    AGENT_REGISTER = "agent_register"
    AGENT_VALIDATE = "agent_validate"
    AGENT_UPDATE = "agent_update"
    AGENT_DEPRECATE = "agent_deprecate"
    TASK_CREATE = "task_create"
    TASK_ASSIGN = "task_assign"
    TASK_CLAIM = "task_claim"
    TASK_SUBMIT = "task_submit"
    TASK_VALIDATE = "task_validate"
    TASK_CLOSE = "task_close"
    MESSAGE = "message"


_EAL_REQUIRED_PAYLOAD_FIELDS: dict[EventType, tuple[str, ...]] = {
    EventType.EXECUTE: ("execution_id", "input_commitment", "output_commitment"),
    EventType.ATTEST: ("subject_execution", "method", "verdict"),
    EventType.EXEC_ANCHOR: ("log_merkle_root",),
    EventType.CHALLENGE: ("challenge_id", "phase"),
}

_EAL_PROOF_REQUIRED_TYPES: frozenset[EventType] = frozenset(
    {EventType.EXECUTE, EventType.ATTEST, EventType.CHALLENGE}
)

_CHALLENGE_PHASES: frozenset[str] = frozenset({"open", "reveal", "answer"})
_CHALLENGE_PHASE_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "open": ("seed_commitment", "reveal_deadline", "response_window_s"),
    "reveal": ("seed",),
    "answer": ("output_commitment",),
}


def _challenge_payload_well_formed(payload: dict) -> bool:
    phase = payload.get("phase")
    if phase not in _CHALLENGE_PHASES:
        return False
    for field_name in _CHALLENGE_PHASE_REQUIRED_FIELDS[phase]:
        value = payload.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            return False
    return True


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    concept_id: str = Field(...)
    event_type: EventType = Field(...)
    actor: str = Field(default="system")
    reasoning: str = Field(default="")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    payload: dict[str, Any] = Field(default_factory=dict)
    previous_event_id: str | None = Field(default=None)
    hash: str = Field(default="")
    signature: str = Field(default="")
    proof: dict[str, Any] | None = Field(default=None)
    _prev_hash: str = PrivateAttr(default="")

    def model_post_init(self, __context, /) -> None:
        if not self.hash:
            self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        event_type_value = (
            self.event_type.value
            if isinstance(self.event_type, EventType)
            else str(self.event_type)
        )
        content = {
            "event_id": self.event_id,
            "concept_id": self.concept_id,
            "event_type": event_type_value,
            "actor": self.actor,
            "timestamp": self.timestamp,
            "payload": _round_floats(self.payload),
            "previous_event_id": self.previous_event_id,
            "prev_hash": self._prev_hash,
            "canon_version": CANON_VERSION,
        }
        return hashlib.sha256(
            json.dumps(content, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()


class EventChain:
    def __init__(
        self,
        concept_id: str,
        events: list[Event] | None = None,
        markdown_body: str = "",
        source_path: str | None = None,
    ) -> None:
        self.concept_id = concept_id
        self.markdown_body = markdown_body
        self.source_path = source_path
        self._cached_status: DiscoveryStatus = DiscoveryStatus.PROVISIONAL
        self._cached_status_order: int = 0
        self._cached_confidence: float = 0.0
        self._events: list[Event] = []
        if events:
            for e in events:
                self.append(e)

    def append(self, event: Event) -> None:
        if event.concept_id != self.concept_id:
            raise ValueError(
                f"Event concept_id {event.concept_id} != chain concept_id {self.concept_id}"
            )
        if self._events:
            prev_event = self._events[-1]
            event.previous_event_id = prev_event.event_id
            event._prev_hash = prev_event.hash
            event.timestamp = max(event.timestamp, prev_event.timestamp)
            event.hash = ""
            event.model_post_init(None)
        else:
            event.previous_event_id = None
            event._prev_hash = ""
            event.hash = ""
            event.model_post_init(None)
        self._events.append(event)
        self._update_crdt_caches(event)

    def _update_crdt_caches(self, event: Event) -> None:
        type_to_status = {
            EventType.REGISTER: DiscoveryStatus.PROVISIONAL,
            EventType.VALIDATE: DiscoveryStatus.VALIDATED,
            EventType.DEPRECATE: DiscoveryStatus.DEPRECATED,
            EventType.FORK: DiscoveryStatus.FORKED,
            EventType.ARCHIVE: DiscoveryStatus.ARCHIVED,
        }
        if event.event_type in type_to_status:
            status = type_to_status[event.event_type]
            order = StatusOrder[status.name.upper()].value
            if order > self._cached_status_order:
                self._cached_status_order = order
                self._cached_status = DiscoveryStatus[StatusOrder(order).name]
        if event.event_type in (EventType.VALIDATE, EventType.SNAPSHOT):
            val = min(1.0, max(0.0, float(event.payload.get("confidence", 0.0))))
            self._cached_confidence = max(self._cached_confidence, val)

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    @property
    def length(self) -> int:
        return len(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(list(self._events))

    def __getitem__(self, index: int) -> Event:
        return self._events[index]

    @property
    def status(self) -> DiscoveryStatus:
        if self._events and self._cached_status_order == 0:
            return self._compute_status_from_events()
        return self._cached_status

    def _compute_status_from_events(self) -> DiscoveryStatus:
        max_order = 0
        type_to_status = {
            EventType.REGISTER: DiscoveryStatus.PROVISIONAL,
            EventType.VALIDATE: DiscoveryStatus.VALIDATED,
            EventType.DEPRECATE: DiscoveryStatus.DEPRECATED,
            EventType.FORK: DiscoveryStatus.FORKED,
            EventType.ARCHIVE: DiscoveryStatus.ARCHIVED,
        }
        for event in self._events:
            if event.event_type in type_to_status:
                status = type_to_status[event.event_type]
                order = StatusOrder[status.name.upper()].value
                max_order = max(max_order, order)
        if max_order == 0:
            return DiscoveryStatus.PROVISIONAL
        return DiscoveryStatus[StatusOrder(max_order).name]

    @property
    def confidence(self) -> float:
        if self._events and self._cached_confidence == 0.0:
            return self._compute_confidence_from_events()
        return self._cached_confidence

    def _compute_confidence_from_events(self) -> float:
        max_conf = 0.0
        for event in self._events:
            if event.event_type in (EventType.VALIDATE, EventType.SNAPSHOT):
                val = min(1.0, max(0.0, float(event.payload.get("confidence", 0.0))))
                max_conf = max(max_conf, val)
        return max_conf

    @property
    def validators(self) -> list[str]:
        return list(
            dict.fromkeys(e.actor for e in self._events if e.event_type == EventType.VALIDATE)
        )

    @property
    def created_at(self) -> str:
        if self._events:
            return self._events[0].timestamp
        return datetime.now(UTC).isoformat()

    @property
    def updated_at(self) -> str:
        if self._events:
            return self._events[-1].timestamp
        return self.created_at

    def _parse_event_dt(self, ts: str) -> datetime:
        try:
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return datetime.min.replace(tzinfo=UTC)

    def verify_integrity(self, full: bool = False, registry=None) -> bool:
        events = self._events
        n = len(events)
        if n == 0:
            return True
        seen: set[str] = set()
        last_dt: datetime | None = None
        action_types = {
            EventType.ANNOUNCE,
            EventType.PUBLISH,
            EventType.SYNC_DASHBOARD,
            EventType.LISTEN,
        }
        for i in range(n):
            event = events[i]
            if i == 0:
                if event.previous_event_id is not None:
                    return False
            else:
                prev_event = events[i - 1]
                if event.previous_event_id != prev_event.event_id:
                    return False
                if event._prev_hash != prev_event.hash:
                    return False
            if event.concept_id != self.concept_id:
                return False
            if event.hash != event._compute_hash():
                return False
            if event.event_id in seen:
                return False
            seen.add(event.event_id)
            if not event.actor or not str(event.actor).strip():
                return False
            ts = self._parse_event_dt(event.timestamp)
            if last_dt is not None and ts < last_dt:
                return False
            last_dt = ts
            if not isinstance(event.payload, dict):
                return False
            if event.event_type in action_types and not event.payload.get("action"):
                return False
            if event.hash and len(event.hash) != 64:
                return False
            if i > 0 and not event.previous_event_id:
                return False
            if not isinstance(event.event_type, EventType):
                return False
            required = _EAL_REQUIRED_PAYLOAD_FIELDS.get(event.event_type)
            if required is not None:
                for field_name in required:
                    value = event.payload.get(field_name)
                    if value is None or (isinstance(value, str) and not value.strip()):
                        return False
            if event.event_type in _EAL_PROOF_REQUIRED_TYPES:
                proof = event.proof
                if (
                    not isinstance(proof, dict)
                    or not proof.get("type")
                    or not proof.get("proofValue")
                ):
                    return False
            if event.event_type is EventType.ATTEST:
                verdict = event.payload.get("verdict")
                if verdict not in ("confirm", "refute", "inconclusive"):
                    return False
            if (event.event_type is EventType.CHALLENGE
                    and not _challenge_payload_well_formed(event.payload)):
                return False
        return True

    def history(self) -> list[dict[str, Any]]:
        return [
            {
                "event_id": e.event_id,
                "event_type": e.event_type.value,
                "actor": e.actor,
                "reasoning": e.reasoning,
                "timestamp": e.timestamp,
                "hash": e.hash,
                "payload": e.payload,
            }
            for e in self._events
        ]


class ProvisionalNames(BaseModel):
    zh: str | None = None
    en: str | None = None


class ADLFrontMatter(BaseModel):
    adl_type: ADLType = Field(...)
    adl_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")
    status: DiscoveryStatus = Field(default=DiscoveryStatus.PROVISIONAL)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    novelty: float = Field(default=0.0, ge=0.0, le=1.0)
    domain: str = Field(default="")
    mechanism: MechanismType | None = None
    scope: str = Field(default="public")
    validators: list[str] = Field(default_factory=list)
    provisional_names: ProvisionalNames = Field(default_factory=ProvisionalNames)
    evidence_refs: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    l2_template: str | bool | None = None

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: str) -> str:
        valid_prefixes = ("public", "private/", "user/", "shared/")
        if not any(v.startswith(p) for p in valid_prefixes):
            raise ValueError(f"Scope must start with one of {valid_prefixes}, got: {v}")
        return v


class ConceptSkeleton(BaseModel):
    adl_id: str
    semantic_type: ADLType
    domain_tag: str
    status: DiscoveryStatus
    scope: str
    relation_summary: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    confidence: float = 0.0
    novelty: float = 0.0
    tenant_id: str | None = None


@dataclass
class ValidationResult:
    path: str
    severity: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.path}: {self.message}"


class ADLDocument(BaseModel):
    """Minimal document model for local governance development."""

    front_matter: ADLFrontMatter
    markdown_body: str = ""

    @property
    def adl_id(self) -> str:
        return self.front_matter.adl_id

    @property
    def scope(self) -> str:
        return self.front_matter.scope

    @property
    def execution_spec(self) -> None:
        return None

    @property
    def event_chain(self) -> EventChain:
        chain = EventChain(concept_id=self.adl_id)
        chain.append(
            Event(
                concept_id=self.adl_id,
                event_type=EventType.REGISTER,
                actor="system",
                reasoning="Document registered (local minimal model)",
            )
        )
        return chain
