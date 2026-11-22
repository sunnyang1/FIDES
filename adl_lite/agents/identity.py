"""Agent identity registry: agents as cryptographically-signed EventChains.

Local minimal copy for Maat governance development (admin attestation path
simplified to key-whitelist verification without signature checks).
"""

from __future__ import annotations

import base64
from enum import Enum
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ed25519
from pydantic import BaseModel, Field

from ..consensus import ConsensusEngine
from ..did_resolver import create_did_key
from ..exceptions import ADLConsensusError
from ..models import Event, EventChain, EventType


class AgentRole(str, Enum):
    DISCOVERER = "discoverer"
    REVIEWER = "reviewer"
    SKEPTIC = "skeptic"
    MERGER = "merger"
    LIBRARIAN = "librarian"
    PLANNER = "planner"


class AgentStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class AgentProfile(BaseModel):
    did: str
    role: AgentRole
    name: str
    model: str = ""
    capabilities: list[str] = Field(default_factory=list)
    mcp_endpoint: str | None = None
    scope: str = "public"
    tenant: str | None = None
    org_id: str | None = None


def chain_kind(chain: EventChain) -> str:
    if not chain.events:
        return "unknown"
    et = chain.events[0].event_type
    if et == EventType.AGENT_REGISTER:
        return "agent"
    if et == EventType.TASK_CREATE:
        return "task"
    return "discovery"


class AgentRegistry:
    def __init__(
        self,
        engine: ConsensusEngine | None = None,
        admin_public_keys: dict[str, str] | None = None,
        admin_calls_allowed: bool = False,
    ) -> None:
        self.engine = engine or ConsensusEngine(dev_mode=True)
        self.admin_public_keys: dict[str, str] = dict(admin_public_keys or {})
        self._admin_calls_allowed = admin_calls_allowed

    def _effective_n_min(self) -> int:
        if self.engine.dev_mode:
            return 1
        return max(int(self.engine._ontology.min_distinct_validators()), 2)

    def _agent_validators(self, chain: EventChain) -> list[str]:
        seen: list[str] = []
        for e in chain.events:
            if e.event_type == EventType.AGENT_VALIDATE and e.actor not in seen:
                seen.append(e.actor)
        return seen

    def register_agent(
        self,
        profile: AgentProfile,
        public_key: Any | None = None,
        genesis_signature: str = "",
        genesis_proof: dict | None = None,
    ) -> EventChain:
        if isinstance(public_key, str):
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
        did = profile.did
        if not did:
            if public_key is None:
                raise ValueError("register_agent requires did or public_key")
            did = create_did_key(public_key)
            profile.did = did
        if did in self.engine.chains:
            raise ADLConsensusError(f"agent already registered: {did}")
        genesis = Event(
            concept_id=did,
            event_type=EventType.AGENT_REGISTER,
            actor=did,
            payload={"profile": profile.model_dump(), "scope": profile.scope},
        )
        if genesis_signature:
            genesis.signature = genesis_signature
        if genesis_proof:
            genesis.proof = genesis_proof
        chain = EventChain(concept_id=did)
        chain.append(genesis)
        self.engine.chains[did] = chain
        return chain

    def validate_agent(
        self,
        agent_did: str,
        validator_actor: str,
        reason: str = "",
        confidence: float = 0.9,
        signature: str = "",
        admin: bool = False,
    ) -> Event:
        if agent_did not in self.engine.chains:
            raise ADLConsensusError(f"unknown agent: {agent_did}")
        chain = self.engine.chains[agent_did]

        if admin:
            if not self._admin_calls_allowed:
                raise ADLConsensusError("admin attestation only via admin-gated API")
            if validator_actor not in self.admin_public_keys:
                raise ADLConsensusError("admin DID has no registered key")
            actor = validator_actor
        else:
            if validator_actor == agent_did:
                raise ADLConsensusError("self-validation is forbidden (B3 analog)")
            if (
                validator_actor not in self.engine.chains
                or self.agent_status(validator_actor) != AgentStatus.ACTIVE
            ):
                raise ADLConsensusError("validator must be a registered ACTIVE agent")
            n_min = self._effective_n_min()
            existing = self._agent_validators(chain)
            prospective = len(existing) + (0 if validator_actor in existing else 1)
            if prospective < n_min:
                raise ADLConsensusError(
                    f"AGENT_VALIDATE requires at least {n_min} distinct validators"
                )
            actor = validator_actor

        event = Event(
            concept_id=agent_did,
            event_type=EventType.AGENT_VALIDATE,
            actor=actor,
            reasoning=reason,
            payload={"confidence": confidence, "admin": admin},
        )
        chain.append(event)
        return event

    def get_agent(self, agent_did: str) -> AgentProfile | None:
        chain = self.engine.chains.get(agent_did)
        if chain is None or chain_kind(chain) != "agent":
            return None
        genesis = chain.events[0]
        return AgentProfile(**genesis.payload["profile"])

    def list_agents(self, scope: str | None = None) -> list[AgentProfile]:
        out: list[AgentProfile] = []
        for chain in self.engine.chains.values():
            if chain_kind(chain) != "agent":
                continue
            p = AgentProfile(**chain.events[0].payload["profile"])
            if scope and p.scope != scope:
                continue
            out.append(p)
        return sorted(out, key=lambda p: p.did)

    def agent_status(self, agent_did: str) -> AgentStatus:
        chain = self.engine.chains.get(agent_did)
        if chain is None:
            return AgentStatus.PENDING
        if any(e.event_type == EventType.AGENT_DEPRECATE for e in chain.events):
            return AgentStatus.DEPRECATED
        if any(
            e.event_type == EventType.AGENT_VALIDATE and e.payload.get("admin")
            for e in chain.events
        ):
            return AgentStatus.ACTIVE
        validators = self._agent_validators(chain)
        return (
            AgentStatus.ACTIVE
            if len(validators) >= self._effective_n_min()
            else AgentStatus.PENDING
        )
