"""ADL Lite — ConsensusEngine (local minimal copy for Maat governance dev)."""

from __future__ import annotations

import threading
from typing import Literal, cast

from .exceptions import ADLConsensusError
from .logging_config import get_logger
from .models import ADLDocument, DiscoveryStatus, Event, EventChain, EventType
from .ontology import OntologyManager, default_ontology

logger = get_logger(__name__)


def _status_to_event_type(status: DiscoveryStatus) -> EventType:
    _map = {
        DiscoveryStatus.PROVISIONAL: EventType.REGISTER,
        DiscoveryStatus.VALIDATED: EventType.VALIDATE,
        DiscoveryStatus.DEPRECATED: EventType.DEPRECATE,
        DiscoveryStatus.FORKED: EventType.FORK,
        DiscoveryStatus.ARCHIVED: EventType.ARCHIVE,
    }
    return _map[status]


class ForkManager:
    ISOMORPHISM_THRESHOLD = 0.90
    PRUNE_AGE_DAYS = 180
    PRUNE_MIN_ENTRIES = 3

    def __init__(self) -> None:
        self.forks: dict[str, list[str]] = {}
        self.creation_times: dict[str, str] = {}

    def register_fork(self, original_id: str, fork_id: str, reason: str = "") -> None:
        if original_id not in self.forks:
            self.forks[original_id] = []
        self.forks[original_id].append(fork_id)


class ConsensusEngine:
    def __init__(self, ontology: OntologyManager | None = None, dev_mode: bool = True) -> None:
        self.chains: dict[str, EventChain] = {}
        self._lock = threading.RLock()
        self.fork_manager = ForkManager()
        self._ontology = ontology or default_ontology()
        self._dev_mode = dev_mode

    def register(self, doc: ADLDocument) -> EventChain:
        cid = doc.adl_id
        with self._lock:
            if cid not in self.chains:
                self._enforce_execution_spec(doc)
                chain = doc.event_chain
                if chain.length == 0:
                    chain.append(
                        Event(
                            concept_id=cid,
                            event_type=EventType.REGISTER,
                            actor="system",
                            reasoning="Document registered",
                        )
                    )
                self.chains[cid] = chain
            return self.chains[cid]

    def _enforce_execution_spec(self, doc: ADLDocument) -> None:
        if self._dev_mode:
            return
        if not self._ontology.require_execution_spec_on_register():
            return
        if doc.execution_spec is None:
            raise ADLConsensusError(
                f"Capability '{doc.adl_id}' lacks an adl:execution spec block"
            )

    def transition(
        self,
        adl_id: str,
        to_status: DiscoveryStatus,
        actor: str,
        reason: str = "",
        payload: dict | None = None,
    ) -> Event | None:
        with self._lock:
            if adl_id not in self.chains:
                raise ADLConsensusError(f"Capability '{adl_id}' not registered")
            chain = self.chains[adl_id]
            current = chain.status
            if not self._is_valid_transition(current, to_status, self._ontology):
                raise ADLConsensusError(
                    f"Invalid transition: {current.value} → {to_status.value}"
                )
            if to_status == DiscoveryStatus.VALIDATED:
                n_min = self._effective_n_min()
                existing = chain.validators
                prospective = len(existing) + (0 if actor in existing else 1)
                if prospective < n_min:
                    raise ADLConsensusError(
                        f"VALIDATE requires at least {n_min} distinct validators"
                    )
            event_type = _status_to_event_type(to_status)
            event = Event(
                concept_id=adl_id,
                event_type=event_type,
                actor=actor,
                reasoning=reason,
                payload=payload or {},
            )
            chain.append(event)
            return event

    def fork(self, original_id: str, fork_id: str, actor: str, reason: str = "") -> EventChain:
        if original_id not in self.chains:
            raise KeyError(f"Original capability '{original_id}' not found")
        with self._lock:
            current_status = self.chains[original_id].status
            if current_status != DiscoveryStatus.FORKED:
                self.transition(original_id, DiscoveryStatus.FORKED, actor, reason)
            new_chain = EventChain(concept_id=fork_id)
            new_chain.append(
                Event(
                    concept_id=fork_id,
                    event_type=EventType.REGISTER,
                    actor=actor,
                    reasoning=f"Forked from {original_id}: {reason}",
                )
            )
            self.chains[fork_id] = new_chain
            self.fork_manager.register_fork(original_id, fork_id, reason)
            return new_chain

    def get_history(self, adl_id: str) -> list[dict]:
        with self._lock:
            if adl_id not in self.chains:
                return []
            return self.chains[adl_id].history()

    def get_status(self, adl_id: str) -> DiscoveryStatus:
        with self._lock:
            if adl_id not in self.chains:
                return DiscoveryStatus.PROVISIONAL
            return self.chains[adl_id].status

    def verify_all(self) -> dict[str, bool]:
        with self._lock:
            return {cid: chain.verify_integrity() for cid, chain in self.chains.items()}

    def _effective_n_min(self) -> int:
        ontology_min = self._ontology.min_distinct_validators()
        if self._dev_mode:
            return 1
        return max(ontology_min, 2)

    def set_production_mode(self) -> None:
        self._dev_mode = False

    def set_dev_mode(self) -> None:
        self._dev_mode = True

    @property
    def dev_mode(self) -> bool:
        return self._dev_mode

    @staticmethod
    def _is_valid_transition(
        current: DiscoveryStatus,
        target: DiscoveryStatus,
        ontology: OntologyManager | None = None,
    ) -> bool:
        mgr = ontology or default_ontology()
        return mgr.is_valid_transition(current.value, target.value)


def validate_event_chain(chain: EventChain, config=None):
    from .trust_model import ConsensusConfig, TrustValidator

    if config is None:
        config = ConsensusConfig(mode=cast(Literal["dev", "prod"], "dev"))
    return TrustValidator().validate_event_chain(chain, config)
