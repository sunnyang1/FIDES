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

## The Phase 2 Loop (Artifact Evolution)

Phase 2 brings **L1 artifacts** (planner configs — `PlannerSpec`) into the same tournament, with replay-based evaluation and an anti-overfitting protocol:

```python
from adl_lite.evolution import ArtifactEvolutionEngine, HeldOutBench, PlannerSpec

eng = ArtifactEvolutionEngine(bench=HeldOutBench(held_out=[...]))  # seal committed at init
cand = eng.propose()                          # seeded neighborhood search over knobs
card = eng.evaluate(cand)                     # public trace replay + commitment check
route = eng.accept(cand, card)                # held-out revealed HERE, post-commitment
```

- **Commit–reveal held-out bench** — candidates are hash-sealed against the hidden scenario set *before* evaluation; the held-out is revealed only at acceptance time, so a proposer cannot tune to it (the DGM "read the test file" lesson).
- **Diff-risk × reputation routing (M4)** — low-risk diffs from high-trust lineages route to `auto_accept`; medium risk or lower trust to `single_reviewer`; anything else to `board`. Constitutional changes (`name`) always require board multi-signoff. Delegation never waives the dominance gate — a delegated route that fails strict dominance is downgraded to `board`.
- **Lineage budgets + EWMA trust** — every accepted generation spends quota; trust updates deterministically from outcomes; exhausted lineages cannot propose.

## Experiments (governance evidence)

| Experiment | Finding |
|---|---|
| P2 real-trace run (`exp_p2_real_traces.py`) | 83% proxy-gain rate reproduced in miniature; trust braked every decision to board; circuit breaker caps observable rework loops at threshold−1 (now a constitution coupling) |
| P3 calibration (`exp_p3_calibration.py`) | Replay rankings vs exhaustive ground truth: public-only ρ=0.353 (picks 2×-suboptimal), with held-out ρ=0.889 — gate (ρ≥0.6) PASS; dominance gate (not the bench) is what rejects cheaters |
| P4 delegation drill (`exp_p4_delegation.py`) | First live `single_reviewer` acceptance (trust 0.920); red-team cheater downgraded auto_accept→board and rejected; attack cost trust 0.92→0.736 (auto-revocation); bench-lifecycle bug found & fixed |

## Phase 3 — Code-Level Gates (constitution shipped)

`adl_lite/evolution/code_gates.py` makes the DGM move **structurally impossible**:

- **Evaluator namespace is permanently outside the mutation space** (`DEFAULT_EVALUATOR_NAMESPACE` — replay, engine, maat gates/anchor, task, consensus, crdt). Any candidate diff touching it is rejected pre-evaluation; no trust, budget, or route can waive this. Namespace amendment is itself constitutional.
- **Sandboxed execution is a hard precondition**; unsandboxed candidates never execute.
- **Calibration precedes trust**: a score function needs a passing P3 calibration record (ρ ≥ 0.6) before it gates anything.
- **Code-level auto_accept is banned on medium risk** regardless of trust — the asymmetry vs artifact evolution.

Full design: `docs/phase3_design.md`.

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
               agents/ (identity,      maat/ (gates, locks,     evolution/ (Phases 1–3:
               task lifecycle)          anchored contract)       governed self-evolution)
                     │                        │                        │
                     └──────────► REST API / CLI / MCP ◄──────────────┘
```

| FIDES layer | Question it answers |
|---|---|
| `evolution/` | *May* this change govern? (Phase 1: the contract · Phase 2: L1 artifacts · Phase 3: code, constitution-gated) |
| `maat/` | *Is* this handoff permitted right now? |
| EventChain + EAL | *What happened, provably?* |
| CRDT + fork | *How do we converge and how do we roll back?* |

## Verification

1881 tests (86% coverage) on the registry core; **57 governance tests** (`tests/test_maat_governance.py`, `tests/test_governed_evolution.py`, `tests/test_phase2_artifacts.py`, `tests/test_phase3_code_gates.py`) covering the validation-lab poison suite — orphan minting, rework ping-pong, submit-by-non-holder, self-validation, resource contention, anchor tamper evidence, non-dominance rejection, fork-rollback — plus TLA⁺ specs and Coq/Iris proof skeletons.

## Roadmap

| Phase | Content | Status |
|---|---|---|
| **1 — Governed contract evolution** | Anchor tournament loop (this README's centerpiece); constitution; fork-rollback; budget escalation | ✅ `feat/phase1-governed-evolution` |
| **2 — Artifact-level evolution** | Task-trace replay scenario sets; commit–reveal held-out bench (anti-overfitting, DGM lesson); PlannerSpec artifacts in the same tournament loop; diff-risk × reputation acceptance routing (M4: auto-accept/single-reviewer/board, constitutional → board multi-signoff always); per-lineage budgets + EWMA trust | ✅ `feat/phase2-artifact-evolution` |
| **3 — Code-level self-modification** | Constitution gates shipped (`evolution/code_gates.py`): evaluator namespace permanently outside the mutation space (DGM log-marker defence), sandboxed execution precondition, P3 calibration precondition (ρ≥0.6), diff risk-tiering with code-level auto-accept ban on medium risk. Design doc: `docs/phase3_design.md` | 🔶 constitution gates on `feat/phase2-artifact-evolution`; sandbox substrate pending |

## Research Positioning

FIDES occupies the intersection current systems leave empty: DGM/Hyperagents/AlphaEvolve evolve without governance memory; LangGraph/enterprise registries govern without evolution; SAGE/EVE-Agent gate on *statistics* — FIDES gates on *governance history*: every acceptance is a hash-anchored, multi-party-signed, replayable decision, and the mutation space is constitutionally separated from the evaluator. See `docs/` for the Applied Ontology submission (7 theorems + 9 Coq proofs).

## Project Layout

```
adl_lite/            # Core package: models, consensus, ontology, EAL, …
├── agents/          # Agent identity + task lifecycle (EventChains)
├── maat/            # Deterministic seven-gate handoff governance
├── evolution/       # Phase 1: anchor tournament · Phase 2: artifact evolution · Phase 3: code gates
experiments/         # E1–E35 registered experiments + P2/P3/P4 governance evidence
tests/               # 1881 core tests + governance suites
specs/  formal/      # TLA⁺ specs, Coq/Iris proof skeletons
dashboard/           # Multi-agent management UI
docs/                # Papers, runbooks, design docs (incl. phase3_design.md)
```

## Contributing

Fork → topic branch (`feat/…`, `fix/…`) → `pip install -e ".[dev]"` → `pre-commit install` → add tests → `ruff check adl_lite/ tests/ && mypy adl_lite/ --ignore-missing-imports && pytest tests/ -m "not slow"` → PR with a `CHANGELOG.md` entry. See CONTRIBUTING.md.

## License

MIT — see LICENSE. Copyright (c) 2026 CEIEC AI Infrastructure.
