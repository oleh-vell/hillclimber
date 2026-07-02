"""``hillclimber run`` — run an experiment to completion (can take hours).

The long-running command, and the whole reason the CLI is a sync shell over an
async core. Its job is the bridge: parse args, ``asyncio.run`` the core
coroutine, handle Ctrl-C cleanly, then hand the final ``ExperimentStatus`` to
``render`` (or dump JSON). The climb loop, worktrees and scoring all live in
``hillclimber.run`` and know nothing about terminals.

Presentation picks one of two modes:

- **Live dashboard** (a TTY, no ``--json``/``--verbose``): the core's trace and
  progress sinks feed a ``RunDashboard`` — milestone history, a status header,
  and a dim tail of recent agent activity (see ``hillclimber.cli.live``).
- **Plain logs** (``--json``, ``--verbose``, or piped output): no live region;
  the run narrates itself through ordinary log lines instead, which is what a
  CI log or a debugging session actually wants.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from harnesses import HarnessError
from hillclimber.cli import render
from hillclimber.cli.console import console, err_console
from hillclimber.cli.live import RunDashboard
from hillclimber.cli.state import CLIState
from hillclimber.run import run as run_experiment
from hillclimber.scoring import ScorerError


def run(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Argument(help="Experiment directory (or its hillclimber.toml).")] = None,
) -> None:
    """Run an experiment end to end, climbing until the budget is spent."""
    state: CLIState = ctx.obj
    target = path or Path()

    # The dashboard needs a terminal to own; --json keeps stdout machine-clean
    # and --verbose means "show me the raw logs", so both fall back to plain
    # log streaming (as does piped output).
    live_view = not state.json and not state.verbose and console.is_terminal

    try:
        if live_view:
            with RunDashboard(console) as dashboard:
                # The one bridge from the sync CLI into the async core. On Ctrl-C,
                # asyncio.run cancels the task first, so the core's teardown
                # (worktree removal, etc.) runs before KeyboardInterrupt
                # propagates out here.
                status = asyncio.run(
                    run_experiment(target, trace_sink=dashboard.on_trace, progress_sink=dashboard.on_progress)
                )
        else:
            status = asyncio.run(run_experiment(target))
    except KeyboardInterrupt:
        err_console.print("[yellow]interrupted[/] — stopped before the budget was spent")
        raise typer.Exit(code=130) from None  # 128 + SIGINT, the shell convention
    except (FileNotFoundError, ScorerError, HarnessError, RuntimeError, ValueError) as exc:
        # The core's known failure modes (missing/invalid toml, dirty artefact,
        # failing baseline scorer, unrunnable model) each raise with a message
        # written for the user — show that, not a traceback. --verbose keeps the
        # traceback for debugging.
        if state.verbose:
            raise
        err_console.print(f"[red]error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    if state.json:
        console.print_json(status.model_dump_json())
    else:
        render.experiment_summary(status)
