"""Rich presentation for the CLI.

The single home for how the CLI *looks* — panels, tables, and (later) the live
run dashboard. Keeping it here means the async core (``hillclimber.run``) never
imports Rich and stays a pure library coroutine. Every function takes a plain
result model and renders it; nothing here reaches back into the core.
"""

from __future__ import annotations

from hillclimber.cli.console import console
from hillclimber.models import ExperimentStatus


def experiment_summary(status: ExperimentStatus) -> None:
    """Render the outcome of a finished run.

    Stub: one headline line today. The full version renders a Rich table of
    cycles (per-cycle score delta, which were accepted) under a panel comparing
    best-so-far against the baseline.
    """
    best = status.best
    best_value = f"{best.score_after.value:.3f}" if best and best.score_after else "n/a"
    console.print(
        f"baseline [cyan]{status.baseline_score.value:.3f}[/] "
        f"-> best [green]{best_value}[/] "
        f"([bold]{status.completed}/{status.total}[/] cycles)"
    )


# TODO: a live dashboard for the long-running climb belongs here too — a
# ``rich.live.Live`` / ``rich.progress.Progress`` view that ``run`` drives from
# strategy events (cycle N/total, current best vs. baseline, elapsed). It stays
# in this module so the core never learns about terminals.
