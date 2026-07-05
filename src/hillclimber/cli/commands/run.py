"""``hillclimber run`` — run an experiment to completion (can take hours).

The long-running command, and the whole reason the CLI is a sync shell over an
async core. Its job is the bridge: parse args, ``asyncio.run`` the core
coroutine, handle Ctrl-C cleanly, then hand the final ``ExperimentStatus`` to
``render`` (or dump JSON). The climb loop, worktrees and scoring all live in
``hillclimber.run`` and know nothing about terminals.

Presentation picks one of two modes:

- **Live dashboard** (a TTY, no ``--json``/``--verbose``): an opening phrase,
  then the core's trace and progress sinks feed a ``RunDashboard`` — milestone
  history, a status header, and a dim tail of recent agent activity (see
  ``hillclimber.cli.live``).
- **Plain logs** (``--json``, ``--verbose``, or piped output): no live region;
  the run narrates itself through ordinary log lines instead, which is what a
  CI log or a debugging session actually wants.

In both modes the full agent trace is teed into ``.hillclimber/trace.log``
(see ``hillclimber.cli.tracelog``) — the live tail is a glance, the file is
the copyable record — and a non-``--json`` run closes with the same next-step
CTA ``hillclimber status`` prints.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from harnesses import HarnessError, TraceEvent, TraceSink
from hillclimber.cli import render
from hillclimber.cli.banner import run_phrase
from hillclimber.cli.console import can_prompt, console, err_console
from hillclimber.cli.live import RunDashboard
from hillclimber.cli.state import CLIState
from hillclimber.cli.tracelog import TraceLog, trace_path
from hillclimber.config import load_config
from hillclimber.lockfile import lock_path, reset_history
from hillclimber.models import Config, ExperimentStatus
from hillclimber.run import run as run_experiment
from hillclimber.scoring import ScorerError
from strategies.base import log_trace


def _load_config(target: Path) -> Config | None:
    """Best-effort config load for the shell's own trimmings.

    The shell wants the config before and after the run (history detection,
    the trace-log path, the closing goal/CTA lines) but must never front-run
    the core's real, user-facing config error with a worse one — a target
    whose config cannot be loaded yields ``None`` and the trimmings are
    skipped.
    """
    try:
        return load_config(target)
    except (FileNotFoundError, ValueError):
        return None


def _detect_history(config: Config | None) -> tuple[str, Path] | None:
    """The artefact's ``(path, lock)`` when past experiment history exists, else ``None``."""
    if config is None:
        return None
    lock = lock_path(config.path_to_artefact)
    if lock.exists():
        return config.path_to_artefact, lock
    return None


def _reset(artefact: str) -> None:
    """Drive ``reset_history`` from the sync shell, rendering its failures cleanly."""
    try:
        asyncio.run(reset_history(artefact))
    except RuntimeError as exc:
        err_console.print(f"[red]error:[/] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc


def _settle_history(state: CLIState, config: Config | None, overwrite: bool, append: bool) -> None:
    """Decide what happens to past experiment history before the run starts.

    Past history is never overwritten silently: the user resets it explicitly
    (the interactive prompt or ``--overwrite``) or keeps climbing on top of it
    (``--append``). Declining the prompt — or having history in a session that
    cannot prompt — stops the run before anything is touched.

    Raises:
        typer.Exit: When the run must not proceed (declined, or no way to ask).
    """
    if append:
        return
    detected = _detect_history(config)
    if detected is None:
        return
    artefact, lock = detected
    if overwrite:
        _reset(artefact)
    elif can_prompt(state):
        if typer.confirm("past experiment detected — overwrite its history?", default=True):
            _reset(artefact)
        else:
            err_console.print("aborted — previous experiment history kept")
            raise typer.Exit(code=1)
    else:
        err_console.print(
            f"[red]error:[/] past experiment history exists at {lock}; "
            "rerun with --overwrite to reset it or --append to climb on top of it"
        )
        raise typer.Exit(code=1)


def _tee(*sinks: TraceSink) -> TraceSink:
    """One trace sink fanning each event out to every one of ``sinks``, in order."""

    def fan_out(event: TraceEvent) -> None:
        for sink in sinks:
            sink(event)

    return fan_out


def _open_trace_log(config: Config | None) -> TraceLog | None:
    """This run's ``TraceLog``, or ``None`` when the artefact can't host one.

    Best-effort like ``_load_config``: a missing artefact directory means the
    core is about to raise the real error, so the trace file is simply skipped
    rather than crashing first.
    """
    if config is None:
        return None
    try:
        return TraceLog(trace_path(config.path_to_artefact))
    except FileNotFoundError:
        return None


def _print_next_step(status: ExperimentStatus, config: Config, target_arg: str) -> None:
    """The closing lines: how the goal fared, then the one next action.

    The merge/append CTA is the same line ``hillclimber status`` prints (see
    ``render.next_step``); a finished run adds the goal verdict only it can
    know — whether the configured target was reached.
    """
    console.print()
    target = config.goal.target
    peak = status.baseline_score
    best = status.best
    if best is not None and best.score_after is not None and best.score_after.value > peak.value:
        peak = best.score_after
    if target is not None:
        if config.goal.is_met(peak):
            console.print(f"[bold green]🎯 goal met[/] — best score {peak.value:.3f} reached the target {target:.3f}")
        else:
            hint = ""
            if best is not None and best.delta > 0:
                suffix = f" {target_arg}" if target_arg else ""
                hint = f"; to keep climbing: [bold]hillclimber run{suffix} --append[/]"
            console.print(f"[yellow]goal not met[/] — best score {peak.value:.3f} vs target {target:.3f}{hint}")
    console.print(render.next_step(status, config.path_to_artefact, target_arg))


def run(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Argument(help="Experiment directory (or its hillclimber.toml).")] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Reset past experiment history (lock and leftover worktrees) first.")
    ] = False,
    append: Annotated[
        bool, typer.Option("--append", help="Keep past experiment history and append this run to it.")
    ] = False,
) -> None:
    """Run an experiment end to end"""
    if overwrite and append:
        raise typer.BadParameter("--overwrite and --append are mutually exclusive")
    state: CLIState = ctx.obj
    target = path or Path()
    target_arg = str(path) if path is not None else ""

    # The dashboard needs a terminal to own; --json keeps stdout machine-clean
    # and --verbose means "show me the raw logs", so both fall back to plain
    # log streaming (as does piped output).
    live_view = not state.json and not state.verbose and console.is_terminal

    # Loaded best-effort once, up front — history detection, the trace log and
    # the closing CTA all want it. None -> the core raises the real config
    # error inside the try below.
    config = _load_config(target)

    # Settled outside the try below: it raises typer.Exit (a RuntimeError
    # subclass via click) for "don't run", which the error rendering there
    # would otherwise swallow.
    _settle_history(state, config, overwrite, append)

    # The opening wink comes after the overwrite question — the run is now
    # actually starting, and a quip right above a serious prompt reads odd.
    if live_view:
        console.print(f"[dim italic]{run_phrase()}[/]")

    trace_log = _open_trace_log(config)
    try:
        with contextlib.ExitStack() as stack:
            file_sinks: tuple[TraceSink, ...] = ()
            if trace_log is not None:
                stack.enter_context(trace_log)
                file_sinks = (trace_log.on_trace,)
                if not state.json:
                    console.print(f"[dim]agent traces → {trace_log.path}[/]")
            if live_view:
                dashboard = stack.enter_context(RunDashboard(console))
                # The one bridge from the sync CLI into the async core. On Ctrl-C,
                # asyncio.run cancels the task first, so the core's teardown
                # (worktree removal, etc.) runs before KeyboardInterrupt
                # propagates out here.
                status = asyncio.run(
                    run_experiment(
                        target,
                        trace_sink=_tee(dashboard.on_trace, *file_sinks),
                        progress_sink=dashboard.on_progress,
                    )
                )
            else:
                # Plain mode keeps the default log narration; the tee only adds
                # the file. No file -> None, so the core's own default applies.
                trace_sink = _tee(log_trace, *file_sinks) if file_sinks else None
                status = asyncio.run(run_experiment(target, trace_sink=trace_sink))
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
        # escape(): the message may quote literal [brackets] like toml table names.
        err_console.print(f"[red]error:[/] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    if state.json:
        console.print_json(status.model_dump_json())
        return
    render.experiment_summary(status)
    if trace_log is not None:
        console.print(f"[dim]full agent traces: {trace_log.path}[/]")
    if config is not None:
        _print_next_step(status, config, target_arg)
