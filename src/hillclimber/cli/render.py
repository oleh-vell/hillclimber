"""Rich presentation for the CLI.

The single home for how the CLI *looks* — panels, tables, the final run
summary, and the one failure renderer every command exits through (the live
dashboard lives in ``hillclimber.cli.live``). Keeping it here means the async
core (``hillclimber.run``) never imports Rich and stays a pure library
coroutine. Every function takes a plain result model and renders it; nothing
here reaches back into the core.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import NoReturn

import typer
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from hillclimber.cli.console import console, err_console
from hillclimber.cli.state import CLIState
from hillclimber.git_utils import repo_root
from hillclimber.models import CycleSummary, ExperimentStatus


def fail(state: CLIState, error: str, *, hint: str | None = None, warnings: list[str] | None = None) -> NoReturn:
    """Report a command failure and exit 1 — the one error path for every command.

    In ``--json`` mode the failure is one machine-readable ``{"ok": false, ...}``
    object on stdout, whatever command it came from, so a script driving the CLI
    parses a single shape. Otherwise the error — and the optional hint — go to
    stderr, styled. ``warnings`` gathered before the failure ride along in the
    JSON payload (in text mode they were already printed as they arose).
    """
    if state.json:
        console.print_json(json.dumps({"ok": False, "error": error, "warnings": warnings or []}))
    else:
        # escape(): the message may quote literal [brackets] like toml table names.
        err_console.print(f"[red]✗[/] {escape(error)}")
        if hint:
            err_console.print(f"[yellow]hint:[/] {escape(hint)}")
    raise typer.Exit(code=1)


def run_command(target_arg: str, *, append: bool = False) -> str:
    """The suggested ``hillclimber run`` invocation — one spelling everywhere.

    ``target_arg`` echoes the user's own path argument (empty when they ran
    against the current directory, so the suggestion stays as short as what
    they actually typed).
    """
    suffix = f" {target_arg}" if target_arg else ""
    return f"hillclimber run{suffix}{' --append' if append else ''}"


def cycles_table(cycles: list[CycleSummary], best_id: str | None, *, include_hypothesis: bool = False) -> Table:
    """The cycles/scores table — one builder for the run summary and ``status``.

    Scores and deltas always; the hypothesis column only where it belongs (the
    run summary — ``status`` stays terse). The best cycle gets a star either way.
    """
    table = Table(show_edge=False, pad_edge=False, box=None, padding=(0, 2, 0, 0))
    table.add_column("cycle", style="bold", no_wrap=True)
    table.add_column("score", justify="right", no_wrap=True)
    table.add_column("Δ base", justify="right", no_wrap=True)
    if include_hypothesis:
        table.add_column("hypothesis", overflow="ellipsis", no_wrap=True, ratio=1)
    for cycle in cycles:
        row = _cycle_row(cycle, is_best=cycle.cycle_id == best_id)
        table.add_row(*(row if include_hypothesis else row[:3]))
    return table


def experiment_summary(status: ExperimentStatus) -> None:
    """Render the outcome of a finished run: a cycles table under a headline.

    The table answers the post-run questions in one glance — what each cycle
    tried (its hypothesis), what it scored, and how that moved against the
    baseline — with the best cycle marked. A run that never cycled (zero budget,
    or a goal already met at baseline) gets just the headline.
    """
    best = status.best
    best_value = f"{best.score_after.value:.3f}" if best and best.score_after else "n/a"
    console.print(
        f"baseline [cyan]{status.baseline_score.value:.3f}[/] "
        f"-> best [green]{best_value}[/] "
        f"([bold]{status.completed}/{status.total}[/] cycles)"
    )

    if not status.cycles:
        return
    console.print(cycles_table(status.cycles, best.cycle_id if best else None, include_hypothesis=True))


def next_step(status: ExperimentStatus, artefact: str, target_arg: str) -> str:
    """The one-line next action after a climb, as Rich markup.

    Either the exact ``git merge`` command that brings the best cycle's branch
    into the user's current branch, or — when nothing beat the baseline — the
    ``--append`` rerun that keeps climbing. Shared by the end of ``hillclimber
    run`` and by ``hillclimber status`` so the two can never disagree on what
    to do next. ``target_arg`` is the path argument to echo into suggested
    hillclimber commands (empty when the user ran against the current
    directory, so the suggestion stays as short as what they actually typed).
    """
    best = status.best
    merge_ref = (best.branch or best.commit_sha) if best is not None else None
    if best is not None and best.delta > 0 and merge_ref:
        # The merge must run inside the artefact repo; spell out -C when the
        # user's shell is somewhere else so the command is copy-pasteable.
        root = repo_root(artefact)
        location = "" if root.resolve() == Path.cwd().resolve() else f"-C {shlex.quote(str(root))} "
        return f"To merge best score: [bold]git {location}merge {escape(merge_ref)}[/]"

    # There is history but nothing beat the baseline: climb again on top of it
    # (plain ``run`` would stop at the overwrite prompt).
    return f"No cycle beat the baseline yet — to keep climbing: [bold]{run_command(target_arg, append=True)}[/]"


def _cycle_row(cycle: CycleSummary, is_best: bool) -> tuple[Text, Text, Text, Text]:
    """One table row for ``cycle``; the best cycle gets a star.

    Cells are built as ``Text`` (not markup strings) so a hypothesis containing
    ``[`` is displayed verbatim rather than parsed as style tags.
    """
    name = Text(cycle.cycle_id)
    if is_best:
        name.append(" ★", style="yellow")

    if cycle.score_after is None:
        score = Text("—", style="dim")
        delta = Text("", style="dim")
    else:
        score = Text(f"{cycle.score_after.value:.3f}")
        style = "green" if cycle.delta > 0 else "red" if cycle.delta < 0 else "dim"
        delta = Text(f"{cycle.delta:+.3f}", style=style)

    return name, score, delta, Text(cycle.hypothesis, style="dim italic")
