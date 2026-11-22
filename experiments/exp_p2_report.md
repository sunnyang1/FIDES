# FIDES Phase 2 Experiment — Real-Trace Evolution Run

**Date:** 2026-11-22 · **Branch:** `feat/phase2-artifact-evolution` · **Script:** `experiments/exp_p2_real_traces.py`

## Question

Does the Phase-2 artifact-evolution loop, fed with **real traces harvested from live
GovernedTaskRegistry EventChains**, produce measurable governance value — and does the
commit–reveal held-out bench reproduce the proxy-gain phenomenon that makes ungoverned
self-improvement dangerous?

## Method

1. **Trace harvest (no hand-written fixtures).** Five scenarios were executed against a
   live `GovernedTaskRegistry` (`AnchorSpec(rework_threshold=5, shared_resources=["prod-db"])`,
   agents bootstrapped via the admin trust-root path), then converted with
   `trace_from_history()` into `TaskTrace`s:

   | trace | steps | rejects | role |
   |---|---|---|---|
   | green_path | 4 | 0 | public |
   | one_rework | 7 | 1 | public |
   | escalated | 7 | 2 (cancelled close) | public |
   | contended | 4 | 0 (prod-db) | public |
   | deep_dispute | 13 | 3 (then **non-cancelled** close) | **held-out** |

   `deep_dispute` cannot be produced at `rework_threshold=3` — the circuit-breaker gate
   refuses the third reject ("rework loop 3 would reach threshold 3"). This is itself a
   validation-lab finding confirmed on real code: **the governance layer caps observable
   rework loops at threshold−1**, so the planner's `rework_hard_stop` must be calibrated
   against the anchor threshold, not against raw loop counts.

2. **The evolution run.** `ArtifactEvolutionEngine` + `SeededArtifactProposer` (seeded
   neighborhood search over `rework_hard_stop` / `w_shared` / `w_rework`), max 12
   generations, held-out sealed before evaluation, revealed only at acceptance.

## Results

| metric | value |
|---|---|
| generations to acceptance | **6** |
| proxy-gain events (public-clean, held-out reject) | **5 of 6** (83%) |
| accepted change | `rework_hard_stop` 3 → 4 |
| routing distribution | board ×6 (delegation never unlocked) |
| trust trajectory | 0.500 → 0.164 (g1–g5 rejections) → **0.665** (g6 acceptance) |
| final lineage | v2, dominates on held-out, budget intact |

![Trust vs held-out truth](exp_p2_chart.png)

## Reading

**1. The proxy-gain trap is real and cheap to fall into.** Every one of the first five
candidates was *perfectly clean on the public set* — a public-only evaluator would have
accepted g1 immediately and called it a day. All five failed the held-out bench. The
83% proxy-gain rate in this micro-run is uncomfortably close to the 73.8% KernelBench
figure for cross-model self-improvement. The commit–reveal bench is what converts these
from silent regressions into *recorded, auditable rejections* (all five are in
`audit_log` with route=board).

**2. Trust is doing its job as a *brake*, not an accelerator.** Five rejections drove
lineage trust to 0.164 — far below the 0.7 `single_reviewer` floor, so every decision
stayed at the board. Delegation is earned back only by demonstrated held-out dominance
(g6: trust 0.164 → 0.665 via the acceptance blend). In a 50+ agent deployment this is
the M4 economics: human minutes are spent exactly where the evidence is weakest.

**3. The winning mutation was semantic, not cosmetic.** Four knob-tweaks on routing
weights (`w_shared`, `w_rework`) changed nothing on the held-out trace — the miss was
an escalation-detection failure, and only `rework_hard_stop` touches it. A diff-risk
router correctly classed that as `medium` risk (the only non-cosmetic field), and it
was signed at board level. Risk-tiering is not decoration: it tracked which knob
mattered.

**4. Rejection paths are recoverable.** Each rejection re-sealed the bench
(`revealed=False` + fresh commitment), so generation n+1 proceeded without operator
intervention — the loop is *self-driving but not self-accepting*.

## Threats to validity

- Single held-out trace; the 83% proxy-gain rate is illustrative, not a population estimate.
- The score function (missed-escalation counts) is a synthetic proxy for task quality —
  Phase 3's gate is whether replay scores predict *empirical* gains on real benchmarks.
- The proposer is a deterministic seeded walk, not an LLM; LLM proposers may find
  dominating candidates faster, or tune harder to the public set — which would make the
  held-out bench *more* valuable, not less.
- All decisions routed to board because trust never reached delegation floors; the
  auto-accept path remains test-covered but operationally unexercised.

## Artifacts

- `experiments/exp_p2_real_traces.py` — reproducible script
- `experiments/exp_p2_results.json` — full generation log + audit trail
- `experiments/exp_p2_chart.png` — trust vs held-out truth
