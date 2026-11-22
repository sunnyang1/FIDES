"""Reproducible SWE-bench task selection and local real-Git patch evaluation.

This module deliberately uses only the Python standard library and the Git CLI.
A temporary clone isolates repository state; it is not a security sandbox for
untrusted code. Official SWE-bench evaluation requires its container harness.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REQUIRED_TASK_FIELDS = ("instance_id", "repo", "problem_statement", "base_commit")
SELECTOR_VERSION = "realfix-selector-v1"


class RealFixError(ValueError):
    """Raised when benchmark input or a local evaluation request is invalid."""


@dataclass(frozen=True)
class RealFixTask:
    """The non-solution fields needed to identify and reproduce one task."""

    instance_id: str
    repo: str
    problem_statement: str
    base_commit: str
    split: str = "unassigned"

    @classmethod
    def from_record(cls, record: dict[str, Any], line_number: int) -> RealFixTask:
        missing = [
            field
            for field in REQUIRED_TASK_FIELDS
            if not isinstance(record.get(field), str) or not record[field].strip()
        ]
        if missing:
            raise RealFixError(
                f"line {line_number}: missing or blank required fields: {', '.join(missing)}"
            )
        return cls(**{field: record[field].strip() for field in REQUIRED_TASK_FIELDS})


def load_tasks_jsonl(path: str | Path) -> list[RealFixTask]:
    """Load a local official-dataset export without fetching or inventing tasks."""
    source = Path(path)
    tasks: list[RealFixTask] = []
    seen: set[str] = set()
    with source.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RealFixError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise RealFixError(f"line {line_number}: expected a JSON object")
            task = RealFixTask.from_record(record, line_number)
            if task.instance_id in seen:
                raise RealFixError(f"line {line_number}: duplicate instance_id {task.instance_id}")
            seen.add(task.instance_id)
            tasks.append(task)
    if not tasks:
        raise RealFixError("the task export contains no records")
    return tasks


def _stable_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def select_realfix_tasks(
    tasks: Sequence[RealFixTask], size: int = 30, max_per_repo: int | None = None
) -> list[RealFixTask]:
    """Select tasks deterministically with round-robin repository diversity.

    Input order and Python hash randomization do not affect selection. Gold
    patches and test patches are neither read nor copied into the manifest.
    """
    if size <= 0:
        raise RealFixError("selection size must be positive")
    if size > len(tasks):
        raise RealFixError(f"requested {size} tasks but only {len(tasks)} are available")

    by_repo: dict[str, list[RealFixTask]] = defaultdict(list)
    for task in tasks:
        by_repo[task.repo].append(task)
    for repo_tasks in by_repo.values():
        repo_tasks.sort(key=lambda task: (_stable_key(task.instance_id), task.instance_id))

    cap = max_per_repo if max_per_repo is not None else max(1, math.ceil(size / len(by_repo)))
    if cap <= 0:
        raise RealFixError("max_per_repo must be positive")
    if sum(min(cap, len(repo_tasks)) for repo_tasks in by_repo.values()) < size:
        raise RealFixError("repository cap makes the requested selection impossible")

    repos = sorted(
        by_repo,
        key=lambda repo: (_stable_key(by_repo[repo][0].instance_id), repo),
    )
    offsets = dict.fromkeys(repos, 0)
    selected: list[RealFixTask] = []
    while len(selected) < size:
        progressed = False
        for repo in repos:
            offset = offsets[repo]
            repo_tasks = by_repo[repo]
            if offset >= min(cap, len(repo_tasks)):
                continue
            selected.append(repo_tasks[offset])
            offsets[repo] += 1
            progressed = True
            if len(selected) == size:
                break
        if not progressed:
            raise RealFixError("repository-diverse selection exhausted all candidates")

    selected.sort(key=lambda task: (_stable_key(task.instance_id), task.instance_id))
    # Exactly every fifth selected task is public development data; the other
    # 80% are held out. This split is reproducible from the ordered selection.
    return [
        RealFixTask(**{**asdict(task), "split": "dev" if index % 5 == 0 else "held_out"})
        for index, task in enumerate(selected)
    ]


def build_selection_manifest(tasks: Sequence[RealFixTask], source: str | Path) -> dict[str, Any]:
    selected = select_realfix_tasks(tasks)
    repositories = sorted({task.repo for task in selected})
    return {
        "suite": "FIDES-Eval RealFix-30",
        "selector_version": SELECTOR_VERSION,
        "status": "selected_not_evaluated",
        "source_file": Path(source).name,
        "source_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
        "selection": {
            "size": len(selected),
            "repository_count": len(repositories),
            "repositories": repositories,
            "dev_count": sum(task.split == "dev" for task in selected),
            "held_out_count": sum(task.split == "held_out" for task in selected),
            "algorithm": "sha256 ordering, repository round robin, deterministic 1-in-5 dev split",
        },
        "tasks": [asdict(task) for task in selected],
        "boundary": (
            "Selection only. No patch was proposed and no official SWE-bench evaluation was run."
        ),
    }


def write_selection_manifest(
    tasks: Sequence[RealFixTask], source: str | Path, output: str | Path
) -> dict[str, Any]:
    manifest = build_selection_manifest(tasks, source)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _run(
    command: Sequence[str],
    cwd: Path | None = None,
    timeout: float = 60,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _output_tail(result: subprocess.CompletedProcess[str], limit: int = 4000) -> str:
    text = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return text[-limit:]


def _timeout_tail(exc: subprocess.TimeoutExpired) -> str:
    stdout = exc.stdout or ""
    stderr = exc.stderr or "test command timed out"
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    return _output_tail(subprocess.CompletedProcess(exc.cmd, 124, stdout, stderr))


def resolve_base_commit(repository: str | Path, base_commit: str = "HEAD") -> str:
    result = _run(
        ("git", "rev-parse", "--verify", "--end-of-options", f"{base_commit}^{{commit}}"),
        Path(repository),
    )
    if result.returncode != 0:
        raise RealFixError(f"cannot resolve base commit {base_commit!r}: {_output_tail(result)}")
    return result.stdout.strip()


def _patch_paths(patch: str, cwd: Path) -> list[str]:
    result = _run(("git", "apply", "--numstat", "-z", "-"), cwd, input_text=patch)
    if result.returncode != 0:
        raise RealFixError(f"invalid patch: {_output_tail(result)}")
    tokens = result.stdout.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        fields = token.split("\t", 2)
        if len(fields) != 3:
            raise RealFixError("could not parse git apply --numstat output")
        path = fields[2]
        if path:
            paths.append(path)
        else:
            if index + 1 >= len(tokens):
                raise RealFixError("truncated rename record in patch")
            paths.extend((tokens[index], tokens[index + 1]))
            index += 2
    return paths


def _is_protected(path: str, protected_paths: Sequence[str]) -> bool:
    normalized = path.replace("\\", "/").removeprefix("./")
    return any(
        normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/")
        for prefix in protected_paths
    )


def _clone_at_base(repository: Path, destination: Path, base_commit: str) -> None:
    clone = _run(("git", "clone", "--quiet", "--no-hardlinks", str(repository), str(destination)))
    if clone.returncode != 0:
        raise RealFixError(f"git clone failed: {_output_tail(clone)}")
    checkout = _run(("git", "checkout", "--quiet", "--detach", base_commit), destination)
    if checkout.returncode != 0:
        raise RealFixError(f"base checkout failed: {_output_tail(checkout)}")


def run_test_baseline(
    repository: str | Path,
    test_command: Sequence[str],
    base_commit: str = "HEAD",
    timeout_seconds: float = 60,
) -> dict[str, Any]:
    """Run the unchanged base in a temporary clone as a validity control."""
    if not test_command:
        raise RealFixError("test_command must not be empty")
    source = Path(repository).resolve()
    commit = resolve_base_commit(source, base_commit)
    with tempfile.TemporaryDirectory(prefix="realfix-base-") as temp:
        clone = Path(temp) / "repo"
        _clone_at_base(source, clone, commit)
        started = time.monotonic()
        try:
            result = _run(test_command, clone, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            return {
                "status": "timeout",
                "base_commit": commit,
                "returncode": 124,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "output_tail": _timeout_tail(exc),
            }
        except OSError as exc:
            return {
                "status": "error",
                "base_commit": commit,
                "returncode": None,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "reason": str(exc),
                "output_tail": "",
            }
        return {
            "status": "passed" if result.returncode == 0 else "failed",
            "base_commit": commit,
            "returncode": result.returncode,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "output_tail": _output_tail(result),
        }


def evaluate_patch(
    repository: str | Path,
    patch: str,
    test_command: Sequence[str],
    base_commit: str = "HEAD",
    protected_paths: Sequence[str] = ("tests",),
    timeout_seconds: float = 60,
) -> dict[str, Any]:
    """Apply one patch to a fresh clone, reject protected edits, then run tests.

    Status is one of rejected, invalid_patch, protected_path, timeout, failed,
    passed, or error. A passing result means only this local test command passed.
    """
    if not test_command:
        raise RealFixError("test_command must not be empty")
    source = Path(repository).resolve()
    commit = resolve_base_commit(source, base_commit)
    result: dict[str, Any] = {
        "status": "rejected",
        "base_commit": commit,
        "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        "patch_bytes": len(patch.encode("utf-8")),
        "patch_applied": False,
        "changed_paths": [],
        "returncode": None,
        "duration_ms": 0,
        "output_tail": "",
    }
    if not patch.strip():
        result["reason"] = "empty patch"
        return result

    with tempfile.TemporaryDirectory(prefix="realfix-patch-") as temp:
        clone = Path(temp) / "repo"
        _clone_at_base(source, clone, commit)
        try:
            paths = _patch_paths(patch, clone)
        except RealFixError as exc:
            result.update(status="invalid_patch", reason=str(exc))
            return result
        result["changed_paths"] = sorted(set(paths))
        protected = sorted(path for path in paths if _is_protected(path, protected_paths))
        if protected:
            result.update(
                status="protected_path",
                reason="patch modifies protected evaluation paths",
                protected_paths=protected,
            )
            return result

        check = _run(("git", "apply", "--check", "-"), clone, input_text=patch)
        if check.returncode != 0:
            result.update(status="invalid_patch", reason=_output_tail(check))
            return result
        apply = _run(("git", "apply", "--whitespace=nowarn", "-"), clone, input_text=patch)
        if apply.returncode != 0:
            result.update(status="invalid_patch", reason=_output_tail(apply))
            return result
        result["patch_applied"] = True

        started = time.monotonic()
        try:
            tested = _run(test_command, clone, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            result.update(
                status="timeout",
                returncode=124,
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                output_tail=_timeout_tail(exc),
            )
            return result
        except OSError as exc:
            result.update(
                status="error",
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                reason=str(exc),
            )
            return result
        result.update(
            status="passed" if tested.returncode == 0 else "failed",
            returncode=tested.returncode,
            duration_ms=round((time.monotonic() - started) * 1000, 3),
            output_tail=_output_tail(tested),
        )
        return result


def git_available() -> bool:
    """Return whether the local harness prerequisite is installed."""
    return shutil.which("git") is not None
