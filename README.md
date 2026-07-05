# Hillclimber

Hillclimber is a framework for long-running agentic sessions aimed at measurable, eval-driven codebase improvement.

```bash
uv tool install git+https://github.com/oleh-vell/hillclimber.git
```

Its distinct feature is that it pushes you to explicitly define an eval function and spec (success criteria, budget, models) for the experiment. This is particularly useful when you want to run long sessions yet you don't have unlimited tokens to burn and you want fine control over long-running jobs.

By being open-source and harness agnostic, hillclimber allows you to swap harnesses (Claude Code, Codex, Cursor etc) and models and choose the one that most suits your needs and budget.

## Getting started

**1. Run the init command.**

```bash
cd my_projects/project_x
hillclimber init -i
```

After following the wizard, two files are produced:

- `hillclimber.toml` — defines the specs for the experiment (goal, budget, models, etc).
- `eval.py` — defines an eval/fitness function.

**2. Implement the `evaluate` function inside `eval.py`.**

Hillclimber uses `eval.py` to calculate the baseline score and delta for each cycle. You must implement `evaluate` before running an experiment. Pro tip: ask the coding agent of your choice to implement it for you 😉

**3. Commit `hillclimber.toml` and `eval.py`.**

> **Note:** Hillclimber runs each experiment in its own dedicated workspace, forked from your latest commit — which is what lets it run multiple cycles in parallel. The tradeoff: because those workspaces are checked out from committed state, any uncommitted work won't make it into them. So commit everything before a run, or Hillclimber will stop and ask you to.

**4. Start climbing.**

```bash
hillclimber run
```

## Key concepts

- **Experiment** — one full run of `hillclimber run`.
- **Cycle** — one attempt to improve the codebase. An experiment consists of 1..n cycles. Cycles can run in parallel, so you explore multiple improvements at once.
- **Strategy** — a predefined workflow hillclimber uses to improve your code. Choose between a simple strategy (cheaper and faster) or a more sophisticated one.
- **Artefact** — file or folder that hillclimber should improve.
- **Goal** — a specific eval score that hillclimber should achieve. When reached, the experiment stops.
- **Budget** — max number of cycles, tokens, or money hillclimber will spend. When exhausted, the experiment stops.
- **Agent** — the entity that does the work. An agent consists of a harness (Claude Code, Codex, Cursor) and a model.
