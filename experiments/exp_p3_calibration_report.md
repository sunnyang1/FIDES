# FIDES Phase 3 Calibration — Replay Score vs Ground Truth

**Date:** 2026-11-22 · **Script:** `experiments/exp_p3_calibration.py` · **Gate decision:** **PASS** (proposed gate: combined Spearman ρ ≥ 0.6)

## Question

Does the Phase-2 replay score — the only signal the governance loop uses to accept or
reject an artifact — predict an artifact's **true** quality? Without this, Phase 3
(code-level evolution) would be grading code mutations with an uncalibrated ruler, and
the KernelBench 73.8% proxy-gain problem would walk straight through the front door.

## Method (why ground truth is legitimate here)

External benchmarks are not needed for calibration at this layer: planner artifacts are
**deterministic programs**, so their true quality can be computed by exhaustive
evaluation. The full behavioural trace space — loop count 0–5 × close outcome
(accepted/cancelled) × shared/non-shared = 24 distinct traces — is enumerated and each
candidate's true missed-escalation rate is measured over it. The replay score is what
the loop actually sees: misses on a 4-trace public set, or 4 public + 3 held-out.

Candidate pool (14): an explicit `rework_hard_stop` 1–8 sweep (the true quality axis,
monotone), three routing-weight variants (documented score-neutral knobs — the score
is deliberately indifferent to them), and three **hand-written proxy-gain cheaters**
(real `hard_stop=1`, worst truth 0.417, but name-specialised to score clean on the
public traces — the DGM move).

Rankings are compared with tie-aware Spearman.

## Results

| evaluator | Spearman ρ vs ground truth |
|---|---|
| public only | **0.353** |
| held-out only | **0.876** |
| public + held-out (what the loop uses) | **0.889** — gate PASS |

![calibration chart](exp_p3_chart.png)

Best-candidate selection:

| evaluator | picks | true miss rate |
|---|---|---|
| public only | `sweep_hs2` | **0.333** (2× worse than optimal) |
| public + held-out | `sweep_hs6` | **0.000** = ground-truth optimum |
| ground truth | `sweep_hs6` | 0.000 |

Cheaters: public-only ranks them 11–13 of 14 (illusion of excellence). But the
dominance gate — which requires **strict** improvement, not ties — rejects them under
both evaluators. The held-out bench is therefore not what catches cheaters; the
strict-dominance gate is. What the bench contributes is **selection quality among
honest candidates**: it lifts ρ from 0.353 to 0.889 and moves the selected artifact
from 2×-worse-than-optimal to optimal.

## Interpretation

1. **The Phase 3 quantitative gate passes on the planner domain.** With the
   commit–reveal held-out bench in the loop, replay rankings predict exhaustive
   ground truth at ρ = 0.889.
2. **The score function is the calibrated unit, not the bench.** The score
   (missed-escalation counts) is what ρ measures. Any new score function — e.g. for
   code-level candidates — must repeat this calibration before its acceptance
   decisions are trusted. This script is the reusable harness: swap the artifact
   class, keep the protocol.
3. **Public-only evaluation is not merely weak, it is actively misleading** (ρ = 0.353,
   selects a 2×-suboptimal artifact). Every ungoverned "fast iteration" loop is
   running the left column of that table.
4. **Strict dominance + held-out is defence in depth**: dominance rejects overfit
   cheaters; the bench ranks honest candidates correctly.

## Threats to validity

- The ground-truth space is synthetic and small (24 traces); the absolute ρ will not
  transfer to richer artifact classes. What transfers is the **protocol**: exhaustive
  or reference evaluation defines truth; scenario-set scores must be shown to rank
  against it before decisions are trusted.
- Routing-weight candidates tie at truth 0.25 / score 0 — the current score is blind
  to routing quality by design. If weights start mattering to real outcomes, the score
  function needs a second calibrated metric.
- One score function, one artifact class, one scenario split. The gate passes for
  PlannerSpec evolution specifically; code-level evolution (Phase 3) must re-run this
  calibration with a code-appropriate score and ground truth (e.g. held-out replay
  traces vs sandbox behavioural probes) before its first acceptance.

## Artifacts

- `experiments/exp_p3_calibration.py` — reusable calibration harness
- `experiments/exp_p3_calibration.json` — full per-candidate data
- `experiments/exp_p3_chart.png` — score-vs-truth curves
