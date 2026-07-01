"""``hillclimber run`` — run an experiment to completion (can take hours).

The long-running command, and the whole reason the CLI is a sync shell over an
async core. Its job is the bridge: parse args, ``asyncio.run`` the core
coroutine, handle Ctrl-C cleanly, then hand the final ``ExperimentStatus`` to
``render`` (or dump JSON). The climb loop, worktrees and scoring all live in
``hillclimber.run`` and know nothing about terminals.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from hillclimber.cli import render
from hillclimber.cli.console import console, err_console
from hillclimber.cli.state import CLIState
from hillclimber.run import run as run_experiment


def run(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Argument(help="Experiment directory (or its hillclimber.toml).")] = None,
) -> None:
    """Run an experiment end to end, climbing until the budget is spent."""
    state: CLIState = ctx.obj
    target = path or Path()

    try:
        # The one bridge from the sync CLI into the async core. On Ctrl-C,
        # asyncio.run cancels the task first, so the core's teardown (worktree
        # removal, etc.) runs before KeyboardInterrupt propagates out here.
        status = asyncio.run(run_experiment(target))
    except KeyboardInterrupt:
        err_console.print("[yellow]interrupted[/] — stopped before the budget was spent")
        raise typer.Exit(code=130) from None  # 128 + SIGINT, the shell convention

    if state.json:
        console.print_json(status.model_dump_json())
    else:
        render.experiment_summary(status)
