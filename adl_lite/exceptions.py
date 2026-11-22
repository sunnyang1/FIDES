"""ADL Lite — Unified exception hierarchy."""

from __future__ import annotations


class ADLError(Exception):
    """Base for all ADL Lite exceptions."""


class ADLTemplateError(ADLError):
    """Raised when an L2 template validation fails."""


class ADLParseError(ADLError):
    """Raised when a document cannot be parsed (invalid YAML, missing front matter, etc.)."""


class ADLValidationError(ADLError):
    """Raised when a document fails semantic or structural validation."""


class ADLOntologyError(ADLError):
    """Raised when an ontology constraint is violated."""


class ADLConsensusError(ADLError):
    """Raised when a consensus operation fails."""


class ADLMemoryError(ADLError):
    """Raised when a memory/storage operation fails."""


class ADLConfigError(ADLError):
    """Raised when configuration is invalid or missing."""


class ADLNeo4jError(ADLError):
    """Raised when a Neo4j graph backend operation fails."""


class ADLTrustValidationError(ADLError):
    """Raised when trust-model validation of an event chain fails hard."""


class ADLUnsupportedDIDMethodError(ADLTrustValidationError):
    """Raised when a DID method is not supported by the current validation layer."""


class GateViolation(ADLConsensusError):
    """Raised when a Maat deterministic gate blocks a handoff.

    The gate verdict (gate name + reason) is attached so callers can log or
    surface the deterministic audit record. No event is appended to the chain
    when a gate blocks — the chain only records what actually happened.
    """

    def __init__(self, gate: str, reason: str) -> None:
        super().__init__(f"[gate:{gate}] {reason}")
        self.gate = gate
        self.reason = reason
