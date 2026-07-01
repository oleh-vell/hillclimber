"""``hillclimber init`` — scaffold a new experiment.

The fast, synchronous counterpart to ``run``: it writes a starter
``hillclimber.toml`` (and, later, the eval scaffold) into a directory, then
points the user at ``run``. No async core is involved, so there is no
``asyncio.run`` bridge here — it returns in milliseconds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from hillclimber.cli.console import console, err_console
from hillclimber.config import HILLCLIMBER_TOML


def init(
    path: Annotated[Path | None, typer.Argument(help="Directory to scaffold the experiment in.")] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite an existing hillclimber.toml.")] = False,
) -> None:
    """Scaffold a new experiment: write a starter hillclimber.toml into PATH."""
    target_dir = path or Path()
    toml_path = target_dir / HILLCLIMBER_TOML

    if toml_path.exists() and not force:
        err_console.print(f"[red]refusing to overwrite[/] existing {toml_path} (pass [bold]--force[/] to replace it)")
        raise typer.Exit(code=1)

    # TODO: render a real starter config + eval scaffold (test_eval_hillclimber.py).
    # Stub for the skeleton — proves the command wiring and the overwrite guard.
    console.print(f"[green]would scaffold[/] {toml_path}")
