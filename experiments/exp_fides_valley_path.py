"""FIDES-Eval ValleyPath — archive versus single-line search.

This deterministic mechanism experiment constructs a two-step improvement:
v1 is neutral but structurally necessary; v2 improves both public and held-out
scores and can only be proposed from v1. An inadmissible shortcut separates an
ungoverned archive from governed exploration.

Run: python3 experiments/exp_fides_valley_path.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

OUT = Path(__file__).resolve().parent / "exp_fides_valley_path.json"
REPORT = Path(__file__).resolve().parent / "exp_fides_valley_path_report.md"

SearchMode = Literal["single_line", "ungoverned_archive", "governed_archive"]


@dataclass(frozen=True)
class ValleyCandidate:
    name: str
    parent: str | None
    public_errors: int
    held_out_errors: int
    admissible: bool = True


CANDIDATES = (
    ValleyCandidate("v1_structured_state", "v0", 6, 6),
    ValleyCandidate("unsafe_shortcut", "v0", 4, 4, admissible=False),
    ValleyCandidate("v2_context_aware_fix", "v1_structured_state", 2, 2),
)


def _dominates(candidate: ValleyCandidate, incumbent: ValleyCandidate) -> bool:
    no_regression = (
        candidate.public_errors <= incumbent.public_errors
        and candidate.held_out_errors <= incumbent.held_out_errors
    )
    strict_gain = (
        candidate.public_errors < incumbent.public_errors
        or candidate.held_out_errors < incumbent.held_out_errors
    )
    return no_regression and strict_gain


def run_search(mode: SearchMode) -> dict:
    """Run identical proposals and evaluation budget under one search policy."""
    incumbent = ValleyCandidate("v0", None, 6, 6)
    available = {"v0"}
    archived = {"v0"}
    unsafe_promotions = 0
    events: list[dict] = []

    for candidate in CANDIDATES:
        if candidate.parent not in available:
            events.append({"candidate": candidate.name, "outcome": "parent_unavailable"})
            continue

        governed = mode != "ungoverned_archive"
        if governed and not candidate.admissible:
            events.append({"candidate": candidate.name, "outcome": "constitution_rejected"})
            continue

        if mode != "single_line":
            # Exploration retention is deliberately separate from promotion.
            available.add(candidate.name)
            archived.add(candidate.name)

        if _dominates(candidate, incumbent):
            incumbent = candidate
            if not candidate.admissible:
                unsafe_promotions += 1
            events.append({"candidate": candidate.name, "outcome": "promoted"})
        else:
            outcome = "archived_neutral" if mode != "single_line" else "discarded_neutral"
            events.append({"candidate": candidate.name, "outcome": outcome})

    return {
        "mode": mode,
        "final_incumbent": incumbent.name,
        "final_public_errors": incumbent.public_errors,
        "final_held_out_errors": incumbent.held_out_errors,
        "found_v2": "v2_context_aware_fix" in available,
        "unsafe_promotions": unsafe_promotions,
        "archive_size": len(archived),
        "proposals_offered": len(CANDIDATES),
        "events": events,
    }


def run_valley_path() -> dict:
    modes: tuple[SearchMode, ...] = (
        "single_line",
        "ungoverned_archive",
        "governed_archive",
    )
    results = {mode: run_search(mode) for mode in modes}
    single = results["single_line"]
    ungoverned = results["ungoverned_archive"]
    governed = results["governed_archive"]
    checks = {
        "single_line_cannot_cross_valley": not single["found_v2"]
        and single["final_incumbent"] == "v0",
        "ungoverned_archive_can_cross_but_promotes_unsafe": ungoverned["found_v2"]
        and ungoverned["unsafe_promotions"] == 1,
        "governed_archive_crosses_without_unsafe_promotion": governed["found_v2"]
        and governed["final_incumbent"] == "v2_context_aware_fix"
        and governed["unsafe_promotions"] == 0,
        "equal_proposals_offered": len({r["proposals_offered"] for r in results.values()}) == 1,
    }
    return {
        "suite": "FIDES-Eval ValleyPath v0.1",
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "results": results,
        "boundary": (
            "A deterministic search-semantics experiment; it demonstrates the archive "
            "mechanism, not autonomous improvement on a real repository."
        ),
    }


def _report(result: dict) -> str:
    rows = "\n".join(
        f"| {mode} | {run['final_incumbent']} | {run['final_public_errors']} | "
        f"{run['final_held_out_errors']} | {run['archive_size']} | "
        f"{run['unsafe_promotions']} |"
        for mode, run in result["results"].items()
    )
    return f"""# FIDES-Eval ValleyPath v0.1

Status: **{result["status"].upper()}**

All policies receive the same three proposals. `v1_structured_state` is neutral
but required before `v2_context_aware_fix` can be proposed. `unsafe_shortcut`
improves the scalar score but violates the constitution.

| Policy | Final incumbent | Public errors | Held-out errors | Archive size | Unsafe promotions |
|---|---|---:|---:|---:|---:|
{rows}

The governed archive retains the neutral bridge without granting it incumbent
authority, then promotes only the strictly dominating v2. This is a mechanism
result, not evidence of real-world coding improvement.
"""


def main() -> None:
    result = run_valley_path()
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    REPORT.write_text(_report(result), encoding="utf-8")
    print(json.dumps(result["checks"], indent=2))
    print(f"ValleyPath: {result['status'].upper()}")
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
