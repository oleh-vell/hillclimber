"""The live run dashboard.

The dashboard is a pure consumer of the core's two narration channels: these
tests feed it trace/progress events by hand and check the three layers — the
persistent milestone lines it prints, the header/tail state it folds events
into, and the logging takeover that keeps raw log lines from tearing the live
display. No Live thread is needed for most of it; the renderable is a plain
function of state.
"""

from __future__ import annotations

import logging
from io import StringIO

from rich.console import Console

from hillclimber.cli.live import RunDashboard, _format_elapsed
from hillclimber.harnesses import TraceEvent
from hillclimber.progress import RunEvent


def _console() -> tuple[Console, StringIO]:
    buffer = StringIO()
    # force_terminal so Rich renders as it would on a real TTY (styles, live).
    return Console(file=buffer, force_terminal=True, width=100), buffer


def _trace(summary: str) -> TraceEvent:
    return TraceEvent(kind="tool_use", summary=summary, raw={})


# --------------------------------------------------------------------------- #
# progress folding (header state + milestones)
# --------------------------------------------------------------------------- #


def test_run_start_prints_the_goal_as_a_persistent_milestone():
    console, buffer = _console()
    dashboard = RunDashboard(console)

    dashboard.on_progress(
        RunEvent(kind="run_start", message="goal: improve ./artefact — raise the eval score to 0.900")
    )

    assert "goal: improve ./artefact" in buffer.getvalue()
    # The header shows real activity from the next event on, not the goal text.
    assert dashboard._activity == "starting"


def test_milestones_wrap_instead_of_truncating():
    console, buffer = _console()  # width=100
    dashboard = RunDashboard(console)
    hypothesis = "swap the tokenizer for a regex " * 8  # well past one line

    dashboard.on_progress(
        RunEvent(kind="cycle_stage", message="applying", index=1, total=2, stage="applying", hypothesis=hypothesis)
    )

    # Milestones are scrollback history: every word survives, none ellipsized.
    output = buffer.getvalue()
    assert "…" not in output
    assert output.count("regex") == 8


def test_baseline_done_sets_the_score_and_prints_a_milestone():
    console, buffer = _console()
    dashboard = RunDashboard(console)

    dashboard.on_progress(RunEvent(kind="baseline_done", message="baseline scored 0.450", score=0.45))

    assert dashboard._baseline == 0.45
    assert "baseline 0.450" in buffer.getvalue()


def test_cycle_done_tracks_the_best_score_as_the_peak():
    dashboard = RunDashboard(_console()[0])

    dashboard.on_progress(RunEvent(kind="cycle_done", message="cycle 1", index=1, total=3, score=0.5, delta=0.05))
    dashboard.on_progress(RunEvent(kind="cycle_done", message="cycle 2", index=2, total=3, score=0.4, delta=-0.1))

    # Best keeps the peak even after a cycle that dipped.
    assert dashboard._best == 0.5


def test_applying_stage_prints_the_hypothesis_as_a_milestone():
    console, buffer = _console()
    dashboard = RunDashboard(console)

    dashboard.on_progress(
        RunEvent(
            kind="cycle_stage",
            message="applying the hypothesis",
            index=2,
            total=5,
            stage="applying",
            hypothesis="use a regex",
        )
    )

    assert dashboard._hypothesis == "use a regex"
    output = buffer.getvalue()
    assert "cycle 002" in output
    assert "use a regex" in output


# --------------------------------------------------------------------------- #
# the trace tail
# --------------------------------------------------------------------------- #


def test_trace_tail_keeps_only_the_last_events():
    dashboard = RunDashboard(_console()[0], trace_tail=3)

    for index in range(10):
        dashboard.on_trace(_trace(f"step {index}"))

    assert [event.summary for event in dashboard._traces] == ["step 7", "step 8", "step 9"]


def test_trace_tail_defaults_to_four_lines():
    dashboard = RunDashboard(_console()[0])

    assert dashboard._traces.maxlen == 4


def test_empty_tail_shows_a_placeholder_once_a_cycle_is_active():
    console, buffer = _console()
    dashboard = RunDashboard(console)
    dashboard.on_progress(
        RunEvent(kind="cycle_stage", message="proposing a hypothesis", index=1, total=2, stage="proposing")
    )

    console.print(dashboard._render())

    # No trace yet, but the region still reads as "working".
    assert "│ …" in buffer.getvalue()


def test_no_placeholder_before_any_cycle():
    console, buffer = _console()
    dashboard = RunDashboard(console)
    dashboard.on_progress(RunEvent(kind="baseline_start", message="scoring the baseline"))

    console.print(dashboard._render())

    assert "│" not in buffer.getvalue()


def test_stage_changes_clear_the_stale_tail():
    dashboard = RunDashboard(_console()[0])
    dashboard.on_trace(_trace("Read(pipeline.py)"))

    # A new stage means a new agent; the previous agent's lines would mislead.
    dashboard.on_progress(RunEvent(kind="cycle_stage", message="scoring the change", index=1, total=2, stage="scoring"))

    assert not dashboard._traces


def test_render_shows_position_scores_and_the_tail():
    console, buffer = _console()
    dashboard = RunDashboard(console)
    dashboard.on_progress(RunEvent(kind="baseline_done", message="baseline scored 0.450", score=0.45))
    dashboard.on_progress(RunEvent(kind="cycle_start", message="cycle 2/5 starting", index=2, total=5))
    dashboard.on_progress(
        RunEvent(kind="cycle_stage", message="proposing a hypothesis", index=2, total=5, stage="proposing")
    )
    dashboard.on_trace(_trace("Read(pipeline.py)"))

    console.print(dashboard._render())

    output = buffer.getvalue()
    assert "cycle 2/5" in output
    assert "proposing a hypothesis" in output
    assert "baseline 0.450" in output
    assert "Read(pipeline.py)" in output


# --------------------------------------------------------------------------- #
# logging takeover
# --------------------------------------------------------------------------- #


def test_dashboard_reroutes_warnings_and_restores_handlers():
    console, buffer = _console()
    package_logger = logging.getLogger("hillclimber")
    handlers_before = package_logger.handlers[:]

    with RunDashboard(console):
        # While live, the project's handlers are swapped out and WARNING+
        # surfaces as a persistent milestone line instead of raw stderr.
        assert package_logger.handlers != handlers_before
        logging.getLogger("hillclimber.run").warning("scorer flaked once")

    assert package_logger.handlers == handlers_before
    assert "scorer flaked once" in buffer.getvalue()


def test_dashboard_keeps_non_console_handlers_so_otel_export_survives():
    # The takeover only quiets the console handlers; a non-StreamHandler (stand-in
    # for the OTEL export handler configure_logging may have installed) must keep
    # receiving records for the one command where export matters.
    console, _ = _console()
    package_logger = logging.getLogger("hillclimber")

    exported: list[str] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            exported.append(record.getMessage())

    export_handler = _CaptureHandler()
    package_logger.addHandler(export_handler)
    try:
        with RunDashboard(console):
            # The non-console handler is still attached while the dashboard owns
            # the terminal...
            assert export_handler in package_logger.handlers
            logging.getLogger("hillclimber.run").warning("ship me to otel")
        # ...and is restored verbatim afterwards.
        assert export_handler in package_logger.handlers
    finally:
        package_logger.removeHandler(export_handler)

    assert "ship me to otel" in exported


# --------------------------------------------------------------------------- #
# elapsed formatting
# --------------------------------------------------------------------------- #


def test_format_elapsed():
    assert _format_elapsed(0) == "0:00"
    assert _format_elapsed(65) == "1:05"
    assert _format_elapsed(3725) == "1:02:05"
