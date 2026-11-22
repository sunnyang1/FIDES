"""CRDT Lattice for ADL Lite derived states."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from functools import reduce
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import EventChain


class StatusOrder(IntEnum):
    """Lattice-ordered status — higher = more progressed."""

    PROVISIONAL = 1
    FORKED = 2
    VALIDATED = 3
    DEPRECATED = 4
    ARCHIVED = 5


@dataclass(frozen=True)
class CRDTState:
    """CRDT-correct derived state for an ADL capability."""

    status: StatusOrder = StatusOrder.PROVISIONAL
    confidence: float = 0.0
    validator_count: int = 0
    evidence_count: int = 0

    def merge(self, other: CRDTState) -> CRDTState:
        return CRDTState(
            status=max(self.status, other.status),
            confidence=max(self.confidence, other.confidence),
            validator_count=max(self.validator_count, other.validator_count),
            evidence_count=max(self.evidence_count, other.evidence_count),
        )

    def apply_event(self, event_type: str, payload: dict[str, Any]) -> CRDTState:
        from .models import EventType

        new_status = self.status
        if event_type == EventType.VALIDATE.value and self.status in (
            StatusOrder.PROVISIONAL,
            StatusOrder.FORKED,
        ):
            new_status = StatusOrder.VALIDATED
        elif event_type == EventType.DEPRECATE.value:
            new_status = StatusOrder.DEPRECATED
        elif event_type == EventType.FORK.value and self.status == StatusOrder.PROVISIONAL:
            new_status = StatusOrder.FORKED
        elif event_type == EventType.ARCHIVE.value:
            new_status = StatusOrder.ARCHIVED

        new_confidence = max(self.confidence, float(payload.get("confidence", 0.0)))
        new_validators = self.validator_count + (1 if event_type == EventType.VALIDATE.value else 0)
        new_evidence = self.evidence_count + (1 if event_type == EventType.EVIDENCE.value else 0)

        result = CRDTState(
            status=new_status,
            confidence=new_confidence,
            validator_count=new_validators,
            evidence_count=new_evidence,
        )
        assert result.status >= self.status
        assert result.confidence >= self.confidence
        assert result.validator_count >= self.validator_count
        assert result.evidence_count >= self.evidence_count
        return result


def _merge_two_chains(chain_a, chain_b) -> EventChain:
    from .models import Event, EventChain

    if chain_a.concept_id != chain_b.concept_id:
        raise ValueError(
            f"Cannot merge chains with different concept_ids: "
            f"{chain_a.concept_id} vs {chain_b.concept_id}"
        )
    event_map: dict[str, Event] = {}
    for event in chain_a.events:
        event_map[event.event_id] = event
    for event in chain_b.events:
        if event.event_id in event_map:
            existing = event_map[event.event_id]
            try:
                existing_ts = datetime.fromisoformat(existing.timestamp)
            except (ValueError, TypeError):
                existing_ts = datetime.min.replace(tzinfo=UTC)
            try:
                new_ts = datetime.fromisoformat(event.timestamp)
            except (ValueError, TypeError):
                new_ts = datetime.min.replace(tzinfo=UTC)
            if new_ts >= existing_ts:
                event_map[event.event_id] = event
        else:
            event_map[event.event_id] = event

    sorted_events = sorted(
        event_map.values(),
        key=lambda e: (
            datetime.fromisoformat(e.timestamp)
            if e.timestamp
            else datetime.min.replace(tzinfo=UTC)
        ),
    )
    merged = EventChain(concept_id=chain_a.concept_id)
    for event in sorted_events:
        merged.append(event)
    return merged


def merge_event_chains(*chains: EventChain) -> EventChain:
    from .models import EventChain

    if len(chains) == 0:
        raise ValueError("At least one EventChain required")
    if len(chains) >= 2:
        first_id = chains[0].concept_id
        for i, chain in enumerate(chains[1:], start=2):
            if chain.concept_id != first_id:
                raise ValueError(
                    f"Cannot merge chains with different concept_ids: "
                    f"{first_id} (chain 1) vs {chain.concept_id} (chain {i})"
                )
    if len(chains) == 1:
        result = EventChain(concept_id=chains[0].concept_id)
        for event in chains[0].events:
            result.append(event)
        return result
    if len(chains) == 2:
        return _merge_two_chains(chains[0], chains[1])
    return reduce(_merge_two_chains, chains)
