**Hillclimber is an opinionated CLI tool that auto-improves code artefacts with LLMs.** Each improvement cycle is an isolated, git-managed, fully-traced experiment scored by a configurable fitness function, so every change is objective, inspectable, and reversible.

## Core loop

Take an artefact, run an LLM-driven mutation against a hypothesis, score the result, record it. Repeat for N cycles. "Hillclimbing" because each run searches for a better artefact according to a defined objective. v1 targets **code with test suites**, where "better" can be measured.

## Key features

**1. Spec-driven** — everything is declared up front in `hillclimber.toml`; no hidden defaults.

- Define each step: which model, which harness, number of cycles, scorer, and **strategy**.
- `strategy` governs how cycles relate to each other. v1 ships `iterative` (each cycle is an independent attempt from the same baseline, best score recorded). The field reserves the slot for later strategies (`hillclimb` = cumulative ascent from the previous winner; `beam` = parallel fan-out) without a schema change.

**2. Configurable fitness function** — how a cycle decides it improved, declared per step.

- **Deterministic** — a command Hillclimber runs (e.g. `pytest test_x.py`) producing a pass/fail or numeric score. The canonical, reproducible signal.
- **LLM-as-judge** — a model scores against a rubric for qualities tests can't capture.
- Both reduce to one comparable score, and can be composed (tests must pass *and* judge score must rise).

**3. Traceability by design**

- Each cycle runs in its own **git worktree** on its own branch — isolated hypotheses, clean per-cycle diffs, trivially discardable bad cycles.
- **OTEL by design** — every cycle, mutation, and scoring call emits spans (model, score, accepted?). Not bolted on.
- **Simple built-in trace viewer** — inspect a run and its cycle lineage locally, no external infra.

**4. Model & harness agnostic** — choose the model and harness per step. Hillclimber orchestrates; it doesn't lock you in.

## File model

Three layers — intent, global state, per-cycle state:

- **`hillclimber.toml`** — *intent*, human-authored, committed. How the experiment should run (cycles, models, harness, scorer, strategy). Read-only at runtime.
- **`hillclimber.lock`** — *global state, source of truth*. Baseline score, per-cycle outcomes, branch/worktree refs, best-so-far. The resume point and audit spine. Each cycle records its `parent` ref (always baseline in v1; the seam where cumulative strategies attach later).
- **Per-cycle workspace `hc_exp1_<id>/`** — a git worktree where the agent works the hypothesis. Owns its `hillclimber_exp.lock` (authoritative *for that cycle* while it runs) and log. On completion, the settled result is promoted into the root lock. Cycle metadata is kept out of the artefact diff so the commit shows only what the agent changed.

`<id>` is a ULID or short hash — globally unique and sortable for tracing; `exp1` stays human-readable.

## User journeys

**Primary — Hillclimber drives the harness (v1):**

```
hillclimber init      # scaffold hillclimber.toml + example
                      # (edit the toml: cycles, models, harness, scorer, strategy)
hillclimber run       # score baseline → per cycle: spin up worktree, harness mutates,
                      #   score, record best in the lock. Nothing auto-merged.
hillclimber view      # inspect cycle lineage, diffs, and traces
hillclimber resume    # continue from last settled cycle (falls out of the lock being truth)
```

Accept semantics: **record, don't merge.** The loop never touches your working branch — the "winner" is a pointer in the lock, and the user decides what to do with it. Non-destructive by design.

**Secondary — the harness drives Hillclimber (post-v1):** your harness of choice calls Hillclimber as a primitive (e.g. via a skill) to perform/score a single improvement. Enabled later by exposing the same core engine as a single-shot command — a thin wrapper over the v1 internals, not a rewrite.

## Architecture seam

Separate the **engine** (pure: load spec, run one mutation via a harness, score, produce a scored diff — no loop) from the **loop runner** (wraps the engine in the cycle: iterate, compare, record). The embeddable secondary mode is then a second thin wrapper over the same engine. Build the engine clean and the harness-driven mode is nearly free later.

## Why Hillclimber

1. **Tight, per-cycle control** — you define exactly what happens each cycle; model- and harness-agnostic.
2. **Trust through traceability** — git-isolated experiments + OTEL + a viewer mean you can always see and verify what changed and why it was kept.
3. **Non-destructive** — auto-improvement that never silently touches your work.

##
