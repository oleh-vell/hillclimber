"""Command-line interface for hillclimber.

A thin *synchronous* shell over the async core: commands parse arguments and
drive ``hillclimber.run`` with ``asyncio.run`` (see CLAUDE.md "Concurrency").
No async logic lives here — the CLI decides how to *present* the run, never how
to execute it.

Layout:

- ``app`` — the :class:`typer.Typer` root: global options and command wiring.
- ``commands/`` — one module per subcommand (plain functions; they never import
  ``app``, so there is no import cycle — ``app`` registers them).
- ``render`` / ``console`` — all Rich presentation, kept here so the core stays
  terminal-agnostic and never imports Rich.

``main`` is the console-script entry point (see ``[project.scripts]``).
"""

from hillclimber.cli.app import app


def main() -> None:
    """Console-script entry point: run the Typer app (see ``[project.scripts]``)."""
    app()


__all__ = ["app", "main"]
