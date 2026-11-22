"""FIDES Phase 3 — confined runtime for evolved code (invariant I2).

A candidate never executes inside the governance process. `SandboxRunner`
runs candidate source in an isolated CPython subprocess:

- **import allowlist** — a meta-path finder rejects any module outside the
  allowlist (no os, sys, socket, subprocess, pathlib, …). The governance
  runtime's modules are unreachable.
- **restricted builtins** — open/exec/eval/compile/input/breakpoint are
  removed from builtins before candidate code runs.
- **resource caps** — RLIMIT_AS (memory), RLIMIT_CPU (CPU seconds),
  RLIMIT_NPROC=0 (fork bombs die at the OS level), plus a parent-side
  wall-clock timeout.
- **outcome-only exit channel** — the candidate's only observable effect is
  the JSON result line on stdout; candidate prints are captured and
  discarded (stdout is swapped to a sink during candidate execution).

Protocol: the parent writes one JSON job on stdin
    {"source": <candidate python source>, "traces": [<trace dict>, …]}
and the child replies with one JSON line on stdout
    {"status": "ok", "results": {trace_name: [actions…] | {"error": …}},
     "stdout": <captured candidate output>}

The candidate source must define ``def plan(trace) -> list[str]`` where
``trace`` is a plain dict (see ``trace_to_dict``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from .replay import TaskTrace


class SandboxError(RuntimeError):
    """The sandbox itself failed (harness error, non-zero exit, bad protocol)."""


class SandboxTimeout(SandboxError):
    """The candidate exceeded the wall-clock timeout."""


MAX_SOURCE_BYTES = 1 << 20  # 1 MiB cap on candidate source (review finding M4)


HARNESS = r'''
import sys, json, io, os, resource
import importlib.abc

ALLOWLIST = frozenset({
    "math", "json", "re", "collections", "itertools", "functools",
    "dataclasses", "typing", "heapq", "bisect", "string", "enum",
})

_BLOCKED_BUILTINS = ("open", "exec", "eval", "compile", "input",
                     "breakpoint", "exit", "quit", "help")


class _BlockFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] not in ALLOWLIST:
            raise ImportError(
                "sandbox: import of %r is not allowlisted" % name)
        return None


def _blocked(*_a, **_k):
    raise PermissionError("sandbox: operation blocked")


# Captured BEFORE restrictions: the harness itself needs compile/exec.
_SAFE_COMPILE = compile
_SAFE_EXEC = exec


def _install_restrictions():
    import builtins
    for fn in _BLOCKED_BUILTINS:
        setattr(builtins, fn, _blocked)
    # purge cached modules the harness itself imported: `import os` would
    # otherwise hit sys.modules and never reach the finder. The harness
    # keeps its own references, so this only affects candidate code.
    for name in list(sys.modules):
        top = name.split(".")[0]
        if top not in ALLOWLIST and top != "builtins":
            del sys.modules[name]
    sys.meta_path.insert(0, _BlockFinder())


def main():
    # --- read the job BEFORE restricting ourselves -----------------------
    job = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    source = job["source"]
    traces = job["traces"]
    _install_restrictions()
    mem = int(job.get("mem_bytes", 536870912))
    cpu = int(job.get("cpu_seconds", 8))
    limits_applied = True
    try:
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    except (ValueError, OSError):
        limits_applied = False  # surfaced to the parent; never silent

    captured = io.StringIO()
    real_stdout = sys.stdout
    namespace: dict = {"__name__": "__candidate__"}
    try:
        sys.stdout = captured
        _SAFE_EXEC(_SAFE_COMPILE(source, "<candidate>", "exec"), namespace)
    except Exception as e:  # noqa: BLE001 - any candidate failure is reported
        sys.stdout = real_stdout
        _emit({"status": "error",
               "error": "exec: %s: %s" % (type(e).__name__, e),
               "stdout": captured.getvalue()})
        return
    plan_fn = namespace.get("plan")
    if not callable(plan_fn):
        sys.stdout = real_stdout
        _emit({"status": "error", "error": "candidate defines no plan()",
               "stdout": captured.getvalue()})
        return

    results: dict = {}
    for t in traces:
        buf = io.StringIO()
        try:
            sys.stdout = buf
            actions = plan_fn(t)
            if not isinstance(actions, list) or not all(
                    isinstance(a, str) for a in actions):
                raise TypeError("plan() must return list[str]")
            results[t["name"]] = actions
        except Exception as e:  # noqa: BLE001
            results[t["name"]] = {"error": "%s: %s" % (type(e).__name__, e)}
        finally:
            captured.write(buf.getvalue())
    sys.stdout = real_stdout
    _emit({"status": "ok", "results": results, "stdout": captured.getvalue(),
           "limits_applied": limits_applied})


def _emit(obj):
    os.write(1, (json.dumps(obj) + "\n").encode("utf-8"))


main()
'''


def trace_to_dict(t: TaskTrace) -> dict[str, Any]:
    return {
        "name": t.name,
        "objective": t.objective,
        "db_ref": t.db_ref,
        "steps": [
            {"action": s.action, "actor": s.actor, "payload": s.payload}
            for s in t.steps
        ],
    }


@dataclass
class SandboxRunner:
    """Runs candidate planner sources in a confined subprocess."""

    timeout: float = 20.0
    mem_bytes: int = 512 << 20
    cpu_seconds: int = 8
    runs: int = 0            # instrumentation: sandbox executions
    degraded: bool = False   # True if any OS-level limit failed to apply
    last_run_ms: float = 0.0  # wall time of the most recent run (scoring axis)

    def run_planner(
        self, source: str, traces: list[TaskTrace]
    ) -> dict[str, list[str] | dict[str, str]]:
        """Execute candidate `source` against `traces`.

        Returns {trace_name: actions} with per-trace {"error": ...} entries
        for traces whose plan() call raised. Raises SandboxError if the
        sandbox itself failed (exec error, protocol violation, resource
        exhaustion of the whole run, timeout).
        """
        self.runs += 1
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise SandboxError(
                f"sandbox: candidate source exceeds {MAX_SOURCE_BYTES} bytes")
        job = json.dumps({
            "source": source,
            "traces": [trace_to_dict(t) for t in traces],
            "mem_bytes": self.mem_bytes,
            "cpu_seconds": self.cpu_seconds,
        })
        import time as _time
        t0 = _time.perf_counter()
        try:
            # start_new_session isolates the child into its own process
            # group (no preexec_fn: fork+preexec in a multithreaded parent
            # can deadlock on inherited locks — review finding L1). The
            # harness applies its own resource limits before running the
            # candidate; the wall-clock timeout is the parent-side backstop.
            proc = subprocess.run(
                [sys.executable, "-I", "-c", HARNESS],
                input=job,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env={"PYTHONHASHSEED": "0"},
                start_new_session=True,
            )
        except subprocess.TimeoutExpired as e:
            raise SandboxTimeout(
                f"sandbox: candidate exceeded {self.timeout}s wall clock"
            ) from e
        finally:
            self.last_run_ms = (_time.perf_counter() - t0) * 1000.0
        if proc.returncode != 0:
            raise SandboxError(
                f"sandbox: harness exited {proc.returncode}: "
                f"{proc.stderr.strip()[:400]}"
            )
        last_line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        try:
            msg = json.loads(last_line)
        except json.JSONDecodeError as e:
            raise SandboxError(
                f"sandbox: malformed result protocol: {proc.stdout[:400]!r}"
            ) from e
        if msg.get("status") != "ok":
            raise SandboxError(f"sandbox: {msg.get('error', 'unknown failure')}")
        if msg.get("limits_applied") is False:
            # harness could not apply OS limits (host restriction): surface
            # it instead of silently downgrading (review finding M4)
            self.degraded = True
        return msg["results"]
