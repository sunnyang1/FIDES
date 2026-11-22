"""ADL Lite — Core ontology registry (local minimal copy)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .exceptions import ADLOntologyError

_DEFAULT_ONTOLOGY_PATH = Path(__file__).resolve().parent / "adl_core_ontology.yaml"


class OntologyManager:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else _DEFAULT_ONTOLOGY_PATH
        self._data: dict[str, Any] = self._load(self._path)

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise ADLOntologyError(f"Core ontology not found: {path}")
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            raise ADLOntologyError(f"Invalid ontology YAML (expected mapping): {path}")
        return data

    @property
    def path(self) -> Path:
        return self._path

    @property
    def version(self) -> str:
        return str(self._data.get("version", ""))

    def list_predicates(self) -> list[str]:
        predicates = self._data.get("predicates", {})
        return sorted(predicates.keys())

    def allowed_transitions(self, status: str) -> list[str]:
        graph = self._data.get("status_transitions", {})
        targets = graph.get(status, [])
        return list(targets)

    def is_valid_transition(self, from_status: str, to_status: str) -> bool:
        return to_status in self.allowed_transitions(from_status)

    def status_transition_graph(self) -> dict[str, list[str]]:
        graph = self._data.get("status_transitions", {})
        return {status: list(targets) for status, targets in graph.items()}

    def allowed_mapping_types(self, predicate: str) -> list[str]:
        predicates = self._data.get("predicates", {})
        entry = predicates.get(predicate, {})
        return list(entry.get("allowed_mapping_types", []))

    def validate_mapping_type(self, predicate: str, mapping_type: str | None) -> bool:
        if mapping_type is None:
            return False
        allowed = self.allowed_mapping_types(predicate)
        if not allowed:
            return True
        return mapping_type in allowed

    def scope_prefixes(self) -> list[str]:
        scopes = self._data.get("scopes", {})
        prefixes = scopes.get("prefixes", [])
        return list(prefixes)

    def validate_predicate(self, name: str) -> bool:
        predicates = self._data.get("predicates", {})
        return name in predicates

    def list_classes(self) -> list[str]:
        classes = self._data.get("classes", {})
        return sorted(classes.keys())

    def list_actions(self) -> list[str]:
        actions = self._data.get("actions", {})
        return sorted(actions.keys())

    def get_action_def(self, name: str) -> dict[str, Any] | None:
        actions: dict[str, Any] = self._data.get("actions", {})
        return actions.get(name)

    def min_distinct_validators(self) -> int:
        cfg = self._data.get("collusion_resistance", {})
        value = cfg.get("min_distinct_validators", 1)
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 1

    def attestation_policy(self) -> dict[str, Any]:
        return dict(self._data.get("attestation", {}))

    def min_distinct_scopes(self) -> int:
        value = self.attestation_policy().get("min_distinct_scopes", 2)
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 2

    def evidence_factor_unbacked(self) -> float:
        value = self.attestation_policy().get("evidence_factor_unbacked", 0.5)
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return 0.5

    def refute_threshold(self) -> int:
        value = self.attestation_policy().get("refute_threshold", 2)
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 2

    def require_execution_spec_on_register(self) -> bool:
        return bool(self.attestation_policy().get("require_execution_spec_on_register", False))

    def allowed_actions_for_class(self, adl_class: str) -> list[str]:
        actions = self._data.get("actions", {})
        result = []
        for name, raw in actions.items():
            allowed = raw.get("allowed_on", [])
            if adl_class in allowed:
                result.append(name)
        return sorted(result)

    def action_preconditions(self, name: str) -> list[dict[str, Any]]:
        actions = self._data.get("actions", {})
        entry = actions.get(name, {})
        return list(entry.get("preconditions", []))

    def action_side_effects(self, name: str) -> list[str]:
        actions = self._data.get("actions", {})
        entry = actions.get(name, {})
        return list(entry.get("side_effects", []))

    def list_mapping_types(self) -> list[str]:
        return list(self._data.get("mapping_types", []))


@lru_cache(maxsize=1)
def default_ontology() -> OntologyManager:
    return OntologyManager()
