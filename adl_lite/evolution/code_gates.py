"""FIDES Phase 3 — code-level evolution gates.

The P4 drill's caveat named the boundary: a proposer that attacks the
score function itself (making `plan()` stop emitting MISSED-ESCALATION
markers) is out of scope for the Phase-2 artifact loop. Phase 3 closes
that hole structurally, not behaviorally:

1. **Evaluator namespace is permanently outside the mutation space.**
   The set of modules that constitute the evaluator (score functions,
   gates, the dominance checker, the bench) is declared in the
   constitution. Any code-level candidate whose diff touches the
   evaluator namespace is rejected BEFORE evaluation — no acceptance
   mechanism, however trusted the lineage, can waive this. This is the
   DGM "agents deleted the detector's log markers" defence: the detector
   cannot be voted out by the thing it detects.

2. **Code diffs are risk-tiered like artifact diffs.** Touches to IO,
   network, persistence, or the evaluation path escalate to
   constitutional (board multi-signoff). Sandboxed runtime reachability
   is a hard precondition for execution.

3. **Calibration is a precondition.** A code-level score function must
   pass the P3 calibration protocol (replay ranking vs ground truth,
   rho >= 0.6) before its first acceptance decision. Uncalibrated
   scores cannot gate anything.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Namespace declaration
# ---------------------------------------------------------------------------

# The evaluator namespace: modules that compute scores, enforce gates,
# check dominance, or hold the bench. This set is itself constitution-level
# — changing it requires board multi-signoff AND is recorded as a
# constitutional amendment (it never enters any candidate's diff silently).
DEFAULT_EVALUATOR_NAMESPACE: frozenset[str] = frozenset({
    "adl_lite.evolution.replay",      # plan(), score_plans(), bench, engine
    "adl_lite.evolution.engine",      # Phase-1 anchor evaluation
    "adl_lite.maat.gates",            # the seven gates
    "adl_lite.maat.anchor",           # anchor chain + verification
    "adl_lite.agents.task",           # task state machine (validation truth)
    "adl_lite.consensus",             # consensus engine
    "adl_lite.crdt",                  # CRDT merge (status truth)
})


@dataclass
class CodeDiff:
    """A code-level candidate: the set of module paths it modifies."""

    modules: frozenset[str]
    touches_io: bool = False           # filesystem / process / env access
    touches_network: bool = False      # sockets, HTTP, RPC
    touches_persistence: bool = False  # direct DB / chain writes (bypassing registries)
    sandboxed: bool = False            # runtime is confined (no imports beyond allowlist)
    description: str = ""

    @classmethod
    def from_source(cls, source: str, *, sandboxed: bool = True,
                    description: str = "") -> CodeDiff:
        """Build a diff from STATIC ANALYSIS of candidate source — the modules
        field is derived, not declared (review finding C3). Imports inside
        function bodies are found too; dynamic imports (__import__ /
        importlib with non-literal names) are flagged conservatively by
        denying them: if we cannot resolve every import statically, the
        diff claims IO/network/persistence so the tier escalates.

        The proposer may not hand-construct a low-risk diff for arbitrary
        source; hand-construction remains available only for deployment
        tooling that runs its own analysis."""
        io_markers = {"os", "sys", "io", "pathlib", "shutil", "subprocess",
                      "socket", "urllib", "requests", "http", "sqlite3",
                      "pickle", "marshal", "ctypes", "importlib", "signal",
                      "threading", "multiprocessing", "builtins"}
        tree = ast.parse(source)
        modules: set[str] = set()
        dynamic = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    modules.add(a.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    modules.add(node.module)
            elif isinstance(node, ast.Call):
                f = node.func
                name = None
                if isinstance(f, ast.Name):
                    name = f.id
                elif isinstance(f, ast.Attribute):
                    name = f.attr
                if name in ("__import__", "exec", "eval", "compile",
                            "open", "breakpoint", "input"):
                    dynamic = True
        touches = bool(modules & io_markers) or dynamic
        return cls(modules=frozenset(modules),
                   touches_io=touches, touches_network=touches,
                   touches_persistence=touches, sandboxed=sandboxed,
                   description=description)


def diff_escalation(diff: CodeDiff,
                    evaluator_ns: frozenset[str] = DEFAULT_EVALUATOR_NAMESPACE
                    ) -> str:
    """Risk-tier a code diff; deterministic.

    constitutional  — touches the evaluator namespace, IO/network/persistence,
                      or is not sandboxed
    medium          — sandboxed, outside evaluator, but within core package
    low             — sandboxed, outside core package (pure plugin/scenario)
    """
    if set(diff.modules) & set(evaluator_ns):
        return "constitutional"
    if diff.touches_io or diff.touches_network or diff.touches_persistence:
        return "constitutional"
    if not diff.sandboxed:
        return "constitutional"
    if any(m.startswith("adl_lite.") for m in diff.modules):
        return "medium"
    return "low"


def check_evaluator_namespace(diff: CodeDiff,
                              evaluator_ns: frozenset[str] = DEFAULT_EVALUATOR_NAMESPACE
                              ) -> None:
    """Constitution check: the evaluator cannot be mutated by the thing it
    evaluates. Raises before any evaluation runs. This check is permanent —
    it is NOT gated on trust, budget, or any acceptance route."""
    hit = set(diff.modules) & set(evaluator_ns)
    if hit:
        raise ValueError(
            "constitution: candidate touches the evaluator namespace "
            f"{sorted(hit)}; the evaluator is permanently outside the "
            "mutation space (DGM log-marker defence)"
        )


def check_sandbox(diff: CodeDiff) -> None:
    """Constitution check: unsandboxed candidates never execute."""
    if not diff.sandboxed:
        raise ValueError(
            "constitution: candidate is not sandboxed; code-level candidates "
            "must run in a confined runtime before any evaluation"
        )


# ---------------------------------------------------------------------------
# Calibration precondition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CalibrationRecord:
    """Evidence that a score function was calibrated before use.

    frozen: the record is decided ONCE at calibration time; mutating
    rho/passed after the fact cannot re-open the gate (review finding M3).
    `artifact_hash` binds the record to the exact scoring artifact it
    calibrated — a record calibrated for one score function cannot be
    replayed against another."""

    score_function: str
    protocol: str               # e.g. "exp_p3_calibration"
    rho: float
    gate: float = 0.6
    passed: bool = False
    artifact_hash: str = ""     # sha256 of the canonical scoring artifact

    def record_fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps({"score_function": self.score_function,
                        "protocol": self.protocol, "rho": self.rho,
                        "gate": self.gate, "passed": self.passed,
                        "artifact_hash": self.artifact_hash},
                       sort_keys=True).encode("utf-8")).hexdigest()


def check_calibration(record: CalibrationRecord | None,
                      expected_fingerprint: str | None = None) -> None:
    """A code-level score function must be calibrated (rho >= gate) before
    its decisions gate anything. Absence of a record is a rejection. If the
    engine captured a fingerprint at init time, the record must still match
    it — a swapped record (even a valid one) is a rejection."""
    if record is None:
        raise ValueError(
            "constitution: score function has no calibration record; run the "
            "P3 calibration protocol before using it in an acceptance path"
        )
    if expected_fingerprint is not None and \
            record.record_fingerprint() != expected_fingerprint:
        raise ValueError(
            "constitution: calibration record does not match the fingerprint "
            "captured at engine init — record swapped or mutated"
        )
    if not record.passed or record.rho < record.gate:
        raise ValueError(
            f"constitution: score function '{record.score_function}' failed "
            f"calibration (rho={record.rho} < gate={record.gate})"
        )


def evaluator_namespace_self_check(
    evaluator_ns: frozenset[str] = DEFAULT_EVALUATOR_NAMESPACE,
) -> dict[str, list[str]]:
    """Deployment-side assertion (review finding C3): at governance-process
    startup, verify the evaluator modules actually loaded in this process
    are exactly the declared namespace — a dependency that silently pulls an
    evaluator module under a different name, or an unexpected second copy,
    is reported. Returns {'unexpected': [...], 'missing': [...]}; an empty
    report means the runtime matches the constitution."""
    loaded = set()
    for name, mod in sys.modules.items():
        if mod is None:
            continue
        for ns in evaluator_ns:
            if name == ns or name.startswith(ns + "."):
                loaded.add(name)
                break
    declared = set(evaluator_ns)
    unexpected = sorted(m for m in loaded if not any(
        m == ns or m.startswith(ns + ".") for ns in declared))
    # 'missing' = declared modules never imported (acceptable pre-use, but
    # surfaced so a gutted evaluator cannot pass silently)
    missing = sorted(ns for ns in declared if ns not in sys.modules)
    return {"unexpected": unexpected, "missing": missing}


# ---------------------------------------------------------------------------
# Phase-3 acceptance gate — the composition the engine calls
# ---------------------------------------------------------------------------


@dataclass
class Phase3Gate:
    """Constitution checks every code-level candidate must clear BEFORE any
    sandbox execution or evaluation. Order matters: namespace first (so the
    evaluator is protected even if later checks are buggy), sandbox second,
    calibration last (only relevant if we got this far)."""

    evaluator_ns: frozenset[str] = DEFAULT_EVALUATOR_NAMESPACE

    def check(self, diff: CodeDiff,
              calibration: CalibrationRecord | None = None,
              expected_fingerprint: str | None = None) -> str:
        check_evaluator_namespace(diff, self.evaluator_ns)
        check_sandbox(diff)
        check_calibration(calibration, expected_fingerprint)
        return diff_escalation(diff, self.evaluator_ns)
