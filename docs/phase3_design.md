# FIDES Phase 3 — Code-Level Self-Modification: Design Document

**Status:** FULLY IMPLEMENTED — sandbox + code engine + 24 engine tests + P5 live drill (`adl_lite/evolution/sandbox.py`, `adl_lite/evolution/code_engine.py`, `experiments/exp_p5_code_evolution.py`) · **Date:** 2026-11-22
**Governing evidence:** P2 real-trace run (83% proxy-gain intercepted) · P3 calibration (ρ=0.889, gate PASS) · P4 delegation drill (dominance holds under delegation; bench lifecycle fixed)

## 1. Problem Statement

Phases 1–2 evolve the governance contract and L1 artifacts. Phase 3 is the step DGM
took ungoverned: letting the system modify its own **code**. The failure mode is
documented and expensive — DGM agents scored full marks by deleting the log markers
their detector used to flag hallucinations; KernelBench finds 73.8% of
self-improvements are proxy gains. Phase 3's design goal is not to make code
evolution fast. It is to make the DGM move **structurally impossible** rather than
merely detectable.

## 2. Non-Negotiable Invariants

These are constitution-level: no acceptance route, trust score, or lineage budget
can waive them. They are checked before any sandbox execution or evaluation.

| # | Invariant | Mechanism | Attack it defeats |
|---|---|---|---|
| I1 | **The evaluator is permanently outside the mutation space** | `check_evaluator_namespace` — any diff touching `DEFAULT_EVALUATOR_NAMESPACE` is rejected pre-evaluation | DGM log-marker deletion (the detector cannot be voted out by the thing it detects) |
| I2 | **Sandboxed execution is a hard precondition** | `check_sandbox` — unsandboxed candidates never execute | Arbitrary code escape into the governance runtime |
| I3 | **Calibration precedes trust** | `check_calibration` — score functions need a passing P3 calibration record (ρ ≥ 0.6) before any acceptance decision | Ungoverned self-improvement grading itself with an uncalibrated ruler |
| I4 | **Diff risk-tiering** | `diff_escalation` — evaluator/IO/network/persistence/unsandboxed = constitutional (board multi-signoff) | Code regressions with the largest blast radius |
| I5 | **Full Phase-1+2 pipeline still applies** | constitution check → public replay → commit–reveal held-out → strict dominance → diff-risk × trust routing → budgeted lineage | Proxy gains (P3-calibrated replay is the filter) |

## 3. Evaluator Namespace (the I1 set)

Declared in `DEFAULT_EVALUATOR_NAMESPACE`:

```
adl_lite.evolution.replay   — plan(), score_plans(), HeldOutBench, ArtifactEvolutionEngine
adl_lite.evolution.engine   — Phase-1 anchor evaluation
adl_lite.maat.gates         — the seven gates
adl_lite.maat.anchor        — anchor chain + verification
adl_lite.agents.task        — task state machine (validation truth)
adl_lite.consensus          — consensus engine
adl_lite.crdt               — CRDT merge (status truth)
```

Changing this set is itself a **constitutional amendment**: board multi-signoff, and
the amendment event is recorded on-chain as a first-class governance event (never
enters any candidate's diff silently). Rationale: if candidates could amend the
namespace, I1 would be self-referential and empty.

## 4. Risk-Tiering Rules

| diff properties | tier | acceptance |
|---|---|---|
| touches evaluator namespace, OR IO, OR network, OR persistence, OR unsandboxed | **constitutional** | board multi-signoff (never delegable) |
| sandboxed, outside evaluator, inside `adl_lite.*` | medium | trust-gated: board / single_reviewer (auto_accept unavailable) |
| sandboxed, outside evaluator, outside core package | low | trust-gated: full routing incl. auto_accept at trust ≥ 0.9 |

Note the asymmetry with artifact evolution: code-level **auto_accept is unavailable
even for medium risk** regardless of trust. Only external, sandboxed plugins can
ever auto-accept — and only after their score function is calibrated (I3).

## 5. Execution Architecture

```
candidate code diff
  → Phase3Gate.check()            (I1–I4, pre-execution)
  → sandboxed runtime             (allowlisted imports; no filesystem/network/env;
  │                                resource-capped; outcome-only exit channel)
  → behavioural probes            (deterministic scenario replay, EAL attestation)
  → P3-calibrated scoring         (I3; public set)
  → commit–reveal held-out        (anti-overfitting, as Phase 2)
  → strict-dominance acceptance   (never waived, as P4 verified)
  → risk × trust routing          (M4; constitutional → multi-signoff always)
  → anchored acceptance event     (append-only, fork-revertible)
```

The sandbox boundary is the load-bearing piece: the candidate runs in a confined
runtime whose only exit channel is the probe results. The evaluator (I1 set) runs
outside the sandbox. This is the structural DGM defence — the detector's markers
live in a namespace the mutated code cannot reach.

## 6. Relationship to Prior Phases

- **P4's caveat** is this phase's requirement: a proposer attacking `plan()` itself
  (making it stop emitting MISSED-ESCALATION) is an evaluator-namespace attack. I1
  rejects the diff before execution.
- **P3's calibration protocol** is I3: the harness (`exp_p3_calibration.py`) is the
  template; code-level scoring must re-run it with code-appropriate ground truth
  (e.g. sandbox behavioural probes vs reference traces) before first acceptance.
- **P4's delegation drill** is why I5 keeps the full pipeline: delegation unlocked
  live and still the dominance gate held. Phase 3 inherits that behaviour; I4
  additionally forbids code-level auto-accept on medium risk.

## 7. What Is Deliberately NOT in Phase 3

- **No LLM in the evaluation path** (unchanged from Phase 1).
- **No waiver of strict dominance** under any trust level.
- **No self-amendment of the evaluator namespace.**
- **No production authority** — Phase 3 candidates, once accepted, are advisory
  artifacts until separately promoted by a human-operated deployment gate. Evolution
  governance ≠ deployment governance.

## 8. Open Questions for Implementation — Status

1. **Sandbox substrate** — **RESOLVED: CPython subprocess** (`evolution/sandbox.py`).
   Import allowlist via meta-path finder + full sys.modules purge of non-allowlisted
   tops (harness keeps its own references), blocked builtins (open/exec/eval/
   compile/input/…), RLIMIT_AS/RLIMIT_CPU/RLIMIT_NPROC=0 set both pre-exec and
   in-harness, parent wall-clock timeout, outcome-only JSON exit channel with
   candidate stdout sink-swapped (protocol spoofing is tested against). WASM/microVM
   remain upgrade paths documented in the code; subprocess isolation is *the
   minimal viable substrate this design asked for*, not the final word.
2. **Probe definition language** — **RESOLVED by reuse**: probes are `TaskTrace`
   dicts (same shape Phase 2 already seals); `CodeBench` mirrors `HeldOutBench`
   commit–reveal semantics with the candidate's version list sealed before any
   evaluation.
3. **Code score functions** — **RESOLVED for the planner domain**: strict Pareto
   over (missed escalations, spurious escalations), both computed *evaluator-side*
   from the evaluator's own requirement model (escalation required at ≥ eval hard
   stop with non-cancelled close). The candidate cannot self-report anything that
   affects scoring — the DGM marker game is structurally impossible, not merely
   detected. Calibration (I3) reuses the P3 exhaustive-enumeration protocol,
   executed *through the sandbox itself* (`calibrate_scoring`): the live drill
   measured ρ = 0.955 against gate 0.6.
4. **Amendment mechanics** — **DEFERRED, documented**: namespace changes are a
   constitutional amendment event type on the anchor chain. Deliberately not
   built in Phase 3: accepting code must not imply any path to amending the
   constitution that accepted it (self-reference guard). Tracked as future work;
   until then the evaluator namespace is immutable in code.

## 9. Implementation Status (final product)

Shipped on `feat/phase2-artifact-evolution`, all tests green:

- `evolution/sandbox.py` — confined runtime (I2) as above.
- `evolution/code_engine.py` — `CodeCandidate` / `CodeDiff` risk tiering /
  `CodeBench` commit–reveal / evaluator-side scoring / `CodeEvolutionEngine`
  full pipeline: `Phase3Gate.check` → sandbox public probes → seal check →
  reveal + sandbox held-out → strict Pareto dominance → `code_acceptance_route`
  (I4: medium risk never auto-accepts) → delegation downgrade on non-dominance
  → EWMA trust + budget + lineage blend → hash-chained anchored audit events
  (`verify_audit_chain()`).
- `tests/test_phase3_code_engine.py` — 24 tests incl. confinement suite and
  zero-sandbox-run pre-execution blocks.
- `experiments/exp_p5_code_evolution.py` — live 7-generation drill: honest fix
  accepted; over-eager / never-escalate / name-cheater rejected; evaluator-
  namespace and unsandboxed attacks blocked pre-execution; honest tie rejected
  post-acceptance; ρ = 0.955 live calibration; audit chain intact.
