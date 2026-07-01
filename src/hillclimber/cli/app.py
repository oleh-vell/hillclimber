"""Typer application root: global options and command wiring.

This module owns the :class:`typer.Typer` app, the options every command shares
(stashed on the Typer context as :class:`CLIState`), and the registration of
each subcommand. The commands themselves are plain functions in ``commands/`` —
they read ``CLIState`` off ``ctx.obj`` but never import this module, so the wiring
stays one-directional and cycle-free.
"""

from __future__ import annotations

from typing import Annotated

import typer

from hillclimber.cli.commands import init as init_cmd
from hillclimber.cli.commands import run as run_cmd
from hillclimber.cli.state import CLIState
from hillclimber.telemetry import configure_logging

app = typer.Typer(
    name="hillclimber",
    help="Auto-improve code artefacts with LLMs.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


@app.callback()
def _global_options(
    ctx: typer.Context,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON instead of a rendered view.")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Log at DEBUG instead of INFO.")] = False,
) -> None:
    """Runs before every command: wire logging and stash the shared flags."""
    # Logs go to stderr (see telemetry), leaving stdout clean for results/--json.
    configure_logging("DEBUG" if verbose else "INFO")
    ctx.obj = CLIState(json=json_output, verbose=verbose)


# Commands are defined as plain functions and registered here, so the command
# modules never import ``app`` (no cycle) and this file is the one place the full
# command surface is visible. New subcommands slot in with one line each.
app.command("init")(init_cmd.init)
app.command("run")(run_cmd.run)
