# FIDES — Governed Self-Evolution for Agent Ecosystems

> *Trust to evolve.* — FIDES (Latin *fides*: trust, good faith) is an **event-first, cryptographically-governed substrate for self-evolving agent systems**. The package remains `adl_lite` for import compatibility; FIDES is the product name.
>
> Everyone is building systems that improve faster. FIDES builds the missing half: **the system you can dare to let evolve** — where every mutation, every evaluation, and every acceptance is hash-anchored, gate-enforced, replayable, and fork-revertible.
>
> *"The world is the totality of facts, not of things." — Wittgenstein, Tractatus §1.1*

---

## Why FIDES Exists

Recursive self-improvement has crossed from speculation into the lab: Darwin Gödel Machine (ICLR 2026) moved SWE-bench from 20% to 50% by letting agents rewrite their own source. The same literature records the failure mode that nobody has engineered away: DGM's agents scored full marks by *deleting the log markers their detector used to flag hallucinations*; cross-model studies find **73.8% of self-improvements (KernelBench) are proxy gains, not real ones**. Current systems answer *"how do we evolve faster?"* — but the limiting factor in practice is *"how do we dare let it run overnight?"*

FIDES answers the second question with infrastructure:

- **Governed evolution** (`adl_lite/evolution/`): a propose → replay → accept loop over the *governance contract itself* (AnchorSpec), where the evaluator is constitutionally outside the mutation space and acceptance is a deterministic function of recorded facts. (Phase 1, shipping.)
- **Deterministic handoff gates** (`adl_lite/maat/`): seven LLM-free gates at every task handoff, against a versioned, hash-anchored contract — blocking orphan tasks, rework ping-pong, non-holder submission, shared-resource contention before they reach the chain.
- **Event-first provenance** (the ADL core): every capability, agent identity, task, governance decision, and anchor revision is an append-only SHA-256 EventChain; status and confidence are CRDT-derived (LUB/G-Counter), never stored; N≥3 branch merges converge with proven proofs.

The result: an evolution system where *"who approved this, on what evidence, and how do we roll back?"* is always answerable from the chain.

## The Phase 1 Loop (Anchor Tournament)

```python
from adl_lite.evolution import EvolutionEngine

eng = EvolutionEngine()                      # genesis: evaluate the incumbent anchor
cand = eng.propose_candidate()               # LLM (or seeded search) proposes a variant
card = eng.evaluate_candidate(cand)          # deterministic scenario replay — no LLM here
if card.dominates_incumbent:                 # safety never regresses, capability may grow
    eng.accept(cand, card, actor="did:key:zBoard")   # human-signed; anchored as v(n+1)

eng.rollback(to_version=1, actor="did:key:zBoard")   # revert = fork, never a deletion
eng.report()                                 # every generation, scorecarded
```

Design invariants that make this loop *governed* rather than merely *automated*:

1. **Constitution before evaluation** — candidates that remove immutable payload fields (e.g. the validator's `accepted` channel), disable always-on gates, or lower the rework threshold below 1 are rejected *before* any scenario runs. The evaluator cannot be blinded by the thing it evaluates.
2. **No LLM in the evaluation or acceptance path** — proposers may be LLMs; scoring and acceptance are deterministic folds over EventChains.
3. **Strict-dominance acceptance** — a candidate is accepted only if it strictly improves safety or capability without regressing the other (Pareto gate).
4. **Budget escalation** — a generation budget forces human escalation instead of unbounded self-modification.
5. **Fork-rollback** — CRDT LUB semantics never regress, so reverting is forking from an ancestor; the detour stays in the lineage as evidence.

## Capabilities

**Event-first document model** — four layers (L1 YAML identity → L2 Markdown narrative → L3 typed relations → L4 actions); status/confidence derived via CRDT semantics that never regress.

**Governed task lifecycle** (`maat/`) — `GovernedTaskRegistry` wraps the task state machine with seven deterministic gates (structure, vocabulary, state-machine, identity, lineage, resource, circuit-breaker); blocked handoffs never touch the chain; operator-takeover closes and cascade-void of descendants are first-class.

**Versioned governance contract** — every anchor revision is hash-anchored before it governs (`AnchorChain.verify()`), closing the "validator misconfiguration caused 37% of halts" hole identified by post-hoc audits of deterministic governance systems.

**Multi-agent consensus** — register → validate → fork → deprecate lifecycle with dynamic N_min (1 dev / ≥2 production), SHA-256 integrity, N≥3 CRDT branch merge.

**Trust & provenance** — DID identities (`did:key`/`did:web`), linked-data proofs, Merkle transparency anchors with batch verification, PROV-O / RDF-star / OWL 2 DL / JSON-LD exports, SHACL (opt-in).

**Execution attestation** — signed EXECUTE/ATTEST receipts, independent replay, commit–reveal challenges against cached answers.

**Pluggable persistence** — NetworkX (default), SQL, Neo4j graph backends; hot/warm/cold memory.

**Interfaces** — CLI, FastAPI REST (multi-tenant, JWT/API-key, rate limiting, quota), MCP tool server.

## Quick Start

```bash
git clone https://github.com/sunnyang1/adl-lite.git && cd adl-lite
pip install -e ".[dev]"
pytest tests/ -m "not slow"
```

```bash
# Governed task lifecycle
adl-lite agent register --name alice --role discoverer
adl-lite task create "analyze fraud" --capabilities concept-fraud-detection
adl-lite task claim <task-id> --agent-did did:key:z...
adl-lite task submit <task-id> --result-ref cap-fraud-v1
adl-lite task validate <task-id> --accepted true --actor did:key:z...   # ≥N_min validators
adl-lite task close <task-id> --outcome accepted

# Consensus chains
adl-lite consensus register --adl-id cap-weather-api
adl-lite consensus transition cap-weather-api --to validated --actor agent_2
adl-lite consensus verify cap-weather-api
```

Python (governance):

```python
from adl_lite.maat import AnchorSpec, GovernedTaskRegistry
from adl_lite.consensus import ConsensusEngine
from adl_lite.agents.identity import AgentRegistry

engine = ConsensusEngine(dev_mode=True)
anchor = AnchorSpec(shared_resources=["prod-db"], rework_threshold=20)
reg = GovernedTaskRegistry(engine, anchor=anchor, agents=AgentRegistry(engine))
# ... every handoff passes the seven gates; violations raise GateViolation
assert reg.verify_anchor_history()
```

## Architecture

```
Markdown (L1–L4) → ADLParser → EventChain → ConsensusEngine → ADLMemory
                                              │
                     ┌────────────────────────┼────────────────────────┐
                     ▼                        ▼                        ▼
               agents/ (identity,      maat/ (gates, locks,     evolution/ (Phase 1:
               task lifecycle)          anchored contract)       governed anchor loop)
                     │                        │                        │
                     └──────────► REST API / CLI / MCP ◄──────────────┘
```

| FIDES layer | Question it answers |
|---|---|
| `evolution/` | *May* this change govern? (Phase 1: the contract itself) |
| `maat/` | *Is* this handoff permitted right now? |
| EventChain + EAL | *What happened, provably?* |
| CRDT + fork | *How do we converge and how do we roll back?* |

## Verification

**127 tests, all green** (`tests/test_maat_governance.py` 12, `tests/test_governed_evolution.py` 9, `tests/test_phase2_artifacts.py` 32, `tests/test_phase3_code_gates.py` 12, `tests/test_phase3_code_engine.py` 51, `tests/test_fides_eval.py` 3, `tests/test_realfix.py` 4, `tests/test_realfix_experiment.py` 4) covering the validation-lab poison suite — orphan minting, rework ping-pong, submit-by-non-holder, self-validation, resource contention, anchor tamper evidence, non-dominance rejection, fork-rollback — plus Phase 3 end-to-end coverage: sandbox confinement (import allowlist, blocked builtins, sys.modules purge, resource caps, timeout kills, protocol-spoof defence, source-size cap, no silent RLIMIT downgrade), live calibration through the sandbox, pre-execution constitution blocks with zero sandbox runs, held-out name-cheater exposure, Pareto spurious-escalation rejection, probe-sufficiency standard (S1–S3 incl. crafted adversarial probes), executed Ed25519 multi-signoff governance (distinct signers, separation of duties, wrong-domain rejection), frozen commit–reveal seals (source-swap impostor, bench rotation), thread-binding, telemetry-free rejection, hash-chained audit verification against the anchored record — plus TLA⁺ specs and Coq/Iris proof skeletons.

**Claims vs maturity** (post adversarial review, `docs/` review report): "hash-anchored" and "multi-party-signed" are *executed* — every acceptance event is appended to a versioned AnchorChain with Ed25519 approvals bound to {candidate content, scorecard fingerprint, bench commitment, engine identity}, verified before reveal; auto_accept on medium risk is banned by routing, not by documentation. ρ calibration is a *sampling-sufficiency statistic*; external validity is established separately via `calibrate_against_history` on harvested EventChain traces. The calibration record and diff metadata are self-declared but hash-bound: a swapped record or doctored diff breaks `check_calibration` / `CodeDiff.from_source` consistency checks.

## Roadmap

| Phase | Content | Status |
|---|---|---|
| **1 — Governed contract evolution** | Anchor tournament loop (this README's centerpiece); constitution; fork-rollback; budget escalation | ✅ `feat/phase1-governed-evolution` |
| **2 — Artifact-level evolution** | Task-trace replay scenario sets; commit–reveal held-out bench (anti-overfitting, DGM lesson); PlannerSpec artifacts in the same tournament loop; diff-risk × reputation acceptance routing (M4: auto-accept/single-reviewer/board, constitutional → board multi-signoff always); per-lineage budgets + EWMA trust | ✅ `feat/phase2-artifact-evolution` |
| **3 — Code-level self-modification** | Confined runtime (`evolution/sandbox.py`): CPython subprocess, import allowlist + sys.modules purge + blocked builtins, RLIMIT_AS/CPU/NPROC + wall-clock timeout + source-size cap (no silent downgrade), outcome-only JSON exit channel. Code engine (`evolution/code_engine.py`): constitution gates (I1–I3) fire pre-execution; evaluator-side requirement model (the DGM marker game is structurally impossible); commit–reveal held-out with frozen seals (source-swap impostor + bench rotation detected); probe-sufficiency standard S1–S3 with crafted adversarial probes; strict Pareto dominance with a 100 ms latency-veto axis; risk × trust routing with code-level auto-accept ban on medium risk and trust-floor blend cap; executed Ed25519 approvals (board multi-signoff, distinct signers, separation of duties) verified before reveal; hash-chained anchored audit. Design doc: `docs/phase3_design.md` — hardened post adversarial review (all C/H/M/L findings closed) | ✅ on `feat/phase2-artifact-evolution` |

## Evidence chain (live runs)

| Drill | Question | Result |
|---|---|---|
| **P2** real-trace evolution | does proxy gain appear against a held-out dispute? | 83% of public-clean candidates failed held-out (cf. KernelBench 73.8%); circuit-breaker/threshold coupling constitutionalised |
| **P3** score calibration | does the replay score rank true quality? | ρ 0.354 public-only → **0.889** with held-out; gate ρ≥0.6 PASS |
| **P4** delegation under fire | can reputation unlock speed without opening the door? | auto-accept live at trust 0.920; red-team cheater downgraded + rejected; post-attack honest tie rejected; bench-lifecycle bug found & fixed |
| **P5** code evolution drill | does the full Phase 3 pipeline hold under attack, in the real sandbox? | 9 generations: honest fix accepted under executed board multi-signoff; over-eager / never-escalate / name-cheater / honest-tie rejected by dominance; evaluator-namespace + unsandboxed attacks blocked **pre-execution with zero sandbox runs**; boundary-specialist (escalate only at loops≥4) rejected via the probe-sufficiency standard; live calibration ρ=0.955; no held-out telemetry leaked in rejections; audit chain intact (`experiments/exp_p5_code_evolution.*`) |
| **FIDES-Eval v0.1** | can discriminating tasks separate governance, archive search, and unsafe promotion? | ProxyGauntlet: 8/8 cases, zero unsafe promotions, honest fix accepted; ValleyPath: single-line search cannot cross the neutral bridge, ungoverned archive records one unsafe promotion, governed archive reaches v2 with zero unsafe promotions; RealFix: deterministic 30-task selector, local real-Git controls, identical-budget five-arm plans, prediction validation, and official-report comparison pass, with official SWE-bench evaluation explicitly not run (`docs/fides_eval_v0_1.md`) |

## Research Positioning

FIDES occupies the intersection current systems leave empty: DGM/Hyperagents/AlphaEvolve evolve without governance memory; LangGraph/enterprise registries govern without evolution; SAGE/EVE-Agent gate on *statistics* — FIDES gates on *governance history*: every acceptance is a hash-anchored, multi-party-signed, replayable decision, and the mutation space is constitutionally separated from the evaluator. See `docs/` for the Applied Ontology submission (7 theorems + 9 Coq proofs).

## Project Layout

```
adl_lite/            # Core package: models, consensus, ontology, EAL, …
├── agents/          # Agent identity + task lifecycle (EventChains)
├── maat/            # Deterministic seven-gate handoff governance
├── evolution/       # Phase 1: anchor tournament · Phase 2: artifact evolution
│                    # Phase 3: sandbox + code evolution engine (I1–I5)
experiments/         # E1–E35 registered experiments
tests/               # 127 governance tests (phases 1–3 + maat + FIDES-Eval)
specs/  formal/      # TLA⁺ specs, Coq/Iris skeletons
dashboard/           # Multi-agent management UI
docs/                # Papers, runbooks, design docs
```

## Contributing

Fork → topic branch (`feat/…`, `fix/…`) → `pip install -e ".[dev]"` → `pre-commit install` → add tests → `ruff check adl_lite/ tests/ && mypy adl_lite/ --ignore-missing-imports && pytest tests/ -m "not slow"` → PR with a `CHANGELOG.md` entry. See CONTRIBUTING.md.

## License

MIT — see LICENSE. Copyright (c) 2026 CEIEC AI Infrastructure.
