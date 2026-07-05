"""``hillclimber status`` — where the climb stands, and what to do next.

The read-only counterpart to ``run``: it folds the artefact's experiment lock
(``.hillclimber/hillclimber.lock``, see ``hillclimber.lockfile``) into the same
``ExperimentStatus`` view the run summary uses, and renders the latest
experiment — cycles run, deltas against the baseline, the best cycle starred.

Whatever the history looks like, the last line is always the next step:

- nothing scaffolded here -> ``hillclimber init``
- scaffolded but never run -> ``hillclimber run``
- climbed with an improvement -> the exact ``git merge`` command that brings
  the best cycle's branch into the user's current branch
- climbed without beating the baseline -> ``hillclimber run --append``

Deliberately terse — a headline, a bare cycles/scores table (no hypotheses;
those belong to the run view), and the CTA.

Costs nothing (no agents, no scorer) and never mutates state, so it is always
safe to run.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from hillclimber.cli import render
from hillclimber.cli.console import console
from hillclimber.cli.state import CLIState
from hillclimber.config import load_config
from hillclimber.lockfile import load_statuses, lock_path
from hillclimber.models import ExperimentStatus

# How each folded state reads in the summary line. "running" is rendered with
# the caveat because the lock cannot tell a live climb from an interrupted one.
_STATE_LABELS = {
    "running": "running or interrupted",
    "completed": "completed",
    "failed": "failed",
}


def _next_step(statuses: list[ExperimentStatus], artefact: str, target_arg: str) -> str:
    """The one-line CTA for the current history — always the last thing printed.

    ``target_arg`` is the path argument to echo into suggested hillclimber
    commands (empty when the user ran against the current directory, so the
    suggestions stay as short as what they actually typed). With history, the
    line is the shared merge/append CTA (see ``render.next_step``) — the same
    one the end of ``hillclimber run`` prints, so the two views never disagree.
    """
    if not statuses:
        return f"To start climbing: [bold]{render.run_command(target_arg)}[/]"
    return render.next_step(statuses[-1], artefact, target_arg)


def _print_summary(statuses: list[ExperimentStatus]) -> None:
    """The state of play in one headline plus a bare cycles table."""
    if not statuses:
        console.print("no experiments have been run against this artefact yet")
        return

    latest = statuses[-1]
    if len(statuses) > 1:
        console.print(f"[dim]{len(statuses) - 1} earlier experiment(s) in history — showing the latest[/]")

    best = latest.best
    delta = best.delta if best is not None else 0.0
    delta_style = "green" if delta > 0 else "red" if delta < 0 else "dim"
    best_value = f"{best.score_after.value:.3f}" if best and best.score_after else "n/a"
    # No experiment id: it means nothing to a reader (--json carries it for
    # machines). State, progress, and scores are the whole story.
    console.print(
        f"Experiment {_STATE_LABELS[latest.state]} — "
        f"[bold]{latest.completed}/{latest.total}[/] cycles, "
        f"baseline [cyan]{latest.baseline_score.value:.3f}[/] -> best [green]{best_value}[/] "
        f"([{delta_style}]{delta:+.3f}[/])"
    )

    if not latest.cycles:
        return
    # Scores only — no hypotheses. Deeper cycle detail is the run view's job.
    console.print(render.cycles_table(latest.cycles, best.cycle_id if best else None))


def status(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Argument(help="Experiment directory (or its hillclimber.toml).")] = None,
) -> None:
    """Show the experiment status"""
    state: CLIState = ctx.obj
    target = path or Path()
    target_arg = str(path) if path is not None else ""

    # No hillclimber.toml -> nothing scaffolded here; the next step is init,
    # not an error (status must be safe to run anywhere, even a fresh dir).
    try:
        config = load_config(target)
    except FileNotFoundError:
        if state.json:
            console.print_json(json.dumps({"initialized": False, "experiments": []}))
        else:
            console.print("no experiment here yet — this directory has no hillclimber.toml")
            suffix = f" {target_arg}" if target_arg else ""
            console.print(f"\nTo scaffold one: [bold]hillclimber init{suffix}[/]")
        return
    except ValueError as exc:
        render.fail(state, f"config: {exc}")

    # The reader is resilient to a torn/corrupt lock line: it skips the bad line
    # (with a warning) and folds the records around it, so a single interrupted
    # write can never wedge ``status`` for the artefact (see ``read_events``).
    folded = asyncio.run(load_statuses(lock_path(config.path_to_artefact)))
    statuses = list(folded.values())

    if state.json:
        console.print_json(
            json.dumps(
                {
                    "initialized": True,
                    "experiments": [experiment.model_dump(mode="json") for experiment in statuses],
                }
            )
        )
        return

    _print_summary(statuses)
    console.print(f"\n{_next_step(statuses, config.path_to_artefact, target_arg)}")
