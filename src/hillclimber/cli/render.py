"""Rich presentation for the CLI.

The single home for how the CLI *looks* — panels, tables, and the final run
summary (the live dashboard lives in ``hillclimber.cli.live``). Keeping it here
means the async core (``hillclimber.run``) never imports Rich and stays a pure
library coroutine. Every function takes a plain result model and renders it;
nothing here reaches back into the core.
"""

from __future__ import annotations

from rich.table import Table
from rich.text import Text

from hillclimber.cli.console import console
from hillclimber.models import CycleSummary, ExperimentStatus


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

    table = Table(show_edge=False, pad_edge=False, box=None, padding=(0, 2, 0, 0))
    table.add_column("cycle", style="bold", no_wrap=True)
    table.add_column("score", justify="right", no_wrap=True)
    table.add_column("Δ base", justify="right", no_wrap=True)
    table.add_column("hypothesis", overflow="ellipsis", no_wrap=True, ratio=1)

    best_id = best.cycle_id if best else None
    for cycle in status.cycles:
        table.add_row(*_cycle_row(cycle, is_best=cycle.cycle_id == best_id))
    console.print(table)


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
