"""FIDES Phase 3 calibration experiment — does the Phase-2 replay score
predict ground-truth artifact quality?

Ground truth without external benchmarks: planner artifacts are
deterministic, so their TRUE quality (missed-escalation rate) can be
computed by exhaustive evaluation over the whole trace space
(loops 0..5 × close outcome × shared/non-shared). The replay score is
what the governance loop actually sees: a candidate's misses on a small
scenario set (public only, or public + commit–reveal held-out).

Protocol:
  1. Generate N candidates: quality-axis sweep + score-neutral weight
     variants + hand-written PROXY-GAIN candidates (specialised to the
     public trace names — clean on the scenario set, worse on the full
     space; the DGM move).
  2. Ground-truth ranking: exhaustive miss-rate over the full space.
  3. Replay rankings: public-only score, then public+held-out score.
  4. Tie-aware Spearman rho between each replay ranking and ground truth.
     The headline: how much does the held-out bench raise rho?

Gate proposal: combined rho >= 0.6 unlocks Phase 3 (code-level evolution).

Run:  python3 experiments/exp_p3_calibration.py
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adl_lite.agents.task import TaskStatus
from adl_lite.evolution.replay import (
    PlannerSpec,
    Step,
    TaskTrace,
    score_plans,
)

OUT = Path(__file__).resolve().parent.parent / "experiments" / "exp_p3_calibration.json"


# ---------------------------------------------------------------------------
# Ground truth: exhaustive trace space
# ---------------------------------------------------------------------------


def enumerate_space(max_loops: int = 5) -> list[TaskTrace]:
    """All behavioural distinct traces: loops 0..max_loops, close outcome
    accepted/cancelled, shared db or not. Loop count = number of reject
    cycles before the final validate."""
    traces: list[TaskTrace] = []
    for loops, outcome, shared in itertools.product(
        range(max_loops + 1), ("accepted", "cancelled"), (False, True)
    ):
        steps: list[Step] = []
        for i in range(loops + 1):
            steps.append(Step("claim", "worker", {"agent_did": "did:key:zW"}))
            steps.append(Step("submit", "worker", {"result_ref": f"r{i}"}))
            if i < loops:
                steps.append(Step("validate", "reviewer", {"accepted": False}))
        steps.append(Step("validate", "reviewer", {"accepted": True}))
        steps.append(Step("close", "planner", {"outcome": outcome}))
        traces.append(TaskTrace(
            f"L{loops}_{outcome[:4]}_{'sh' if shared else 'ns'}",
            "calibration", "prod-db" if shared else None,
            steps, TaskStatus.CLOSED,
        ))
    return traces


def true_miss_rate(spec: PlannerSpec, space: list[TaskTrace]) -> float:
    misses = sum(score_plans(t, spec)["missed_escalations"] for t in space)
    return misses / len(space)


def _plain(spec: PlannerSpec) -> PlannerSpec:
    """Copy without subclass fields (NameSpecialisedSpec carries extras)."""
    return PlannerSpec(
        name=spec.name, version=spec.version, w_claim=spec.w_claim,
        w_rework=spec.w_rework, w_shared=spec.w_shared,
        rework_hard_stop=spec.rework_hard_stop,
    )


# ---------------------------------------------------------------------------
# Proxy-gain candidates: clean on the scenario set, worse in truth.
# The DGM move — specialise to what the evaluator shows you.
# ---------------------------------------------------------------------------


class NameSpecialisedSpec(PlannerSpec):
    """A 'candidate proposer' that cheats: it knows the public trace names
    and tunes its effective hard_stop per name, so it looks clean on the
    scenario set while its real knob rots. Modelled as a scorer wrapper."""

    def __init__(self, base: PlannerSpec, known_names: set[str], cheat_stop: int):
        super().__init__(**{**base.__dict__})
        self._known = known_names
        self._cheat_stop = cheat_stop


def score_with_cheat(trace: TaskTrace, spec: NameSpecialisedSpec) -> dict:
    """The cheater scores clean on known trace names by pretending its
    hard_stop is higher there. Replay the SAME deterministic plan(), but
    with an effective spec swapped in for known names."""
    effective = _plain(spec)
    if trace.name in spec._known:
        effective.rework_hard_stop = spec._cheat_stop
    return score_plans(trace, effective)


def main() -> None:
    space = enumerate_space()
    print(f"trace space: {len(space)} traces "
          f"({len(set(t.name for t in space))} distinct)")

    # ---- scenario sets (what the loop actually sees) --------------------
    public = [t for t in space if t.name in {
        "L0_acce_ns", "L1_acce_sh", "L2_canc_ns", "L0_acce_sh",
    }]
    held_out = [t for t in space if t.name in {
        "L3_acce_ns", "L4_acce_sh", "L5_acce_ns",
    }]
    print(f"scenario set: public={len(public)} held_out={len(held_out)}")

    # ---- candidate pool --------------------------------------------------
    # Explicit quality-axis sweep: hard_stop 1..8 (truth is monotone in it),
    # plus weight-tweaked variants (the score is, by design, indifferent to
    # routing weights — they are documented as score-neutral knobs), plus
    # hand-written proxy-gain cheaters.
    pool: list[PlannerSpec] = [
        PlannerSpec(version=v, rework_hard_stop=hs)
        for v, hs in enumerate(range(1, 9), start=1)
    ]
    pool += [
        PlannerSpec(version=20 + i, w_shared=0.5 * i, w_rework=3.0 + i)
        for i in range(1, 4)
    ]
    # hand-written proxy-gain candidates: real hard_stop 1 (terrible in
    # truth) but they cheat on the known public names
    cheaters: list[NameSpecialisedSpec] = [
        NameSpecialisedSpec(PlannerSpec(version=100 + i, rework_hard_stop=1),
                            {t.name for t in public}, cheat_stop=99)
        for i in range(3)
    ]
    all_names = [
        f"sweep_hs{c.rework_hard_stop}" if c.version < 20 else f"weights_v{c.version}"
        for c in pool
    ]
    cheater_names = [f"cheater_hs1_cheat99_{i}" for i in range(len(cheaters))]
    n_total = len(pool) + len(cheaters)

    # ---- ground truth ----------------------------------------------------
    truth: dict[str, float] = {}
    for name, spec in zip(all_names, pool):
        truth[name] = true_miss_rate(spec, space)
    for name, spec in zip(cheater_names, cheaters):
        # truth uses the REAL knob, no name-based cheat — exhaustive space
        truth[name] = true_miss_rate(_plain(spec), space)

    # ---- replay scores ---------------------------------------------------
    def public_score(spec) -> int:
        if isinstance(spec, NameSpecialisedSpec):
            return sum(score_with_cheat(t, spec)["missed_escalations"] for t in public)
        return sum(score_plans(t, spec)["missed_escalations"] for t in public)

    def heldout_score(spec) -> int:
        # held-out traces are NOT in the cheater's known set: cheat fails
        if isinstance(spec, NameSpecialisedSpec):
            real = _plain(spec)
            return sum(score_plans(t, real)["missed_escalations"] for t in held_out)
        return sum(score_plans(t, spec)["missed_escalations"] for t in held_out)

    names = all_names + cheater_names
    specs = pool + cheaters
    pub = {n: public_score(s) for n, s in zip(names, specs)}
    ho = {n: heldout_score(s) for n, s in zip(names, specs)}
    combined = {n: pub[n] + ho[n] for n in names}

    # ---- Spearman rankings (tie-aware average ranks) ---------------------
    def _ranks(scores: dict[str, float]) -> dict[str, float]:
        order = sorted(scores, key=lambda k: scores[k])
        ranks: dict[str, float] = {}
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in order[i:j + 1]:
                ranks[k] = avg
            i = j + 1
        return ranks

    def spearman(a: dict[str, float], b: dict[str, float]) -> float:
        ra, rb = _ranks(a), _ranks(b)
        keys = list(a)
        n = len(keys)
        ma = sum(ra.values()) / n
        mb = sum(rb.values()) / n
        num = sum((ra[k] - ma) * (rb[k] - mb) for k in keys)
        den = (sum((ra[k] - ma) ** 2 for k in keys) * sum((rb[k] - mb) ** 2 for k in keys)) ** 0.5
        return num / den if den else 0.0

    rho_public = spearman(pub, truth)
    rho_heldout = spearman(combined, truth)
    rho_ho_only = spearman(ho, truth)

    # cheater rank positions
    def rank_of(scores: dict[str, float], name: str) -> int:
        order = sorted(scores, key=lambda k: scores[k])
        return order.index(name) + 1

    cheater_pub_ranks = [rank_of(pub, n) for n in cheater_names]
    cheater_comb_ranks = [rank_of(combined, n) for n in cheater_names]

    # dominance-gate outcomes: would the real loop ACCEPT a cheater?
    # Phase-2 acceptance requires STRICT dominance on the evaluation set.
    incumbent_pub = pub["sweep_hs3"]      # the real default incumbent
    incumbent_comb = combined["sweep_hs3"]
    cheater_gate = {
        "public_only": [
            "accepted" if pub[n] < incumbent_pub else "rejected(tie/no-strict-dominance)"
            for n in cheater_names
        ],
        "combined": [
            "accepted" if combined[n] < incumbent_comb else "rejected(tie/no-strict-dominance)"
            for n in cheater_names
        ],
    }

    results = {
        "n_candidates": n_total,
        "n_cheaters": len(cheaters),
        "trace_space_size": len(space),
        "spearman": {
            "public_only_vs_truth": round(rho_public, 4),
            "heldout_only_vs_truth": round(rho_ho_only, 4),
            "public_plus_heldout_vs_truth": round(rho_heldout, 4),
        },
        "gate_proposal": "phase3_unlock_requires_combined_rho >= 0.6",
        "gate_pass": bool(rho_heldout >= 0.6),
        "cheater_analysis": {
            "cheater_true_miss_rate": [round(truth[n], 4) for n in cheater_names],
            "cheater_public_rank_of": [f"{r}/{n_total}" for r in cheater_pub_ranks],
            "cheater_combined_rank_of": [f"{r}/{n_total}" for r in cheater_comb_ranks],
            "dominance_gate_if_public_only": cheater_gate["public_only"],
            "dominance_gate_if_combined": cheater_gate["combined"],
        },
        "best_candidate_by": {
            "public_only": {
                "name": min(pub, key=pub.get),
                "true_miss_rate": round(truth[min(pub, key=pub.get)], 4),
            },
            "combined": {
                "name": min(combined, key=combined.get),
                "true_miss_rate": round(truth[min(combined, key=combined.get)], 4),
            },
            "ground_truth": {
                "name": min(truth, key=truth.get),
                "true_miss_rate": round(truth[min(truth, key=truth.get)], 4),
            },
        },
        "per_candidate": [
            {
                "name": n,
                "true_miss_rate": round(truth[n], 4),
                "public_misses": pub[n],
                "held_out_misses": ho[n],
            }
            for n in names
        ],
    }
    OUT.write_text(json.dumps(results, indent=2))

    print("\n=== SPEARMAN (replay ranking vs ground truth) ===")
    for k, v in results["spearman"].items():
        print(f"  {k:34s} rho = {v:.3f}")
    print(f"\nGATE (proposed: combined rho >= 0.6): "
          f"{'PASS' if results['gate_pass'] else 'FAIL'}")
    print(f"\ncheaters (true miss rate "
          f"{[round(truth[n],3) for n in cheater_names]}):")
    print(f"  public-only ranks: {cheater_pub_ranks} of {n_total} — illusion of excellence")
    print(f"  combined ranks:    {cheater_comb_ranks} of {n_total}")
    print(f"  dominance gate, public-only: {cheater_gate['public_only']}")
    print(f"  dominance gate, combined:    {cheater_gate['combined']}")
    for src in ("public_only", "combined", "ground_truth"):
        b = results["best_candidate_by"][src]
        print(f"  best by {src:14s} {b['name']:12s} (true miss rate {b['true_miss_rate']:.3f})")
    print(f"\nwritten -> {OUT}")


if __name__ == "__main__":
    main()
