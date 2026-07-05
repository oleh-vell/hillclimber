"""``hillclimber check`` — verify the eval conforms before spending a climb.

The fast feedback loop for authoring ``eval.py``: it runs the scorer command
once, exactly as the climb would (same ``run_scorer_command`` + ``parse_eval``
pieces), and reports what happened at each step — config loaded, command ran,
envelope found. It costs nothing (no agents, no git requirements), so users
iterate against it until it is green and only then pay for a run.

Failures are diagnosed, not just reported: a scorer that can't import its deps
gets an environment hint, and output that contains a *score-shaped* JSON line
without the envelope marker gets pointed at the marker specifically — the two
mistakes a first run actually makes.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.markup import escape

from hillclimber.cli.console import console, err_console
from hillclimber.cli.state import CLIState
from hillclimber.config import load_config
from hillclimber.scoring import ScorerError, parse_eval, run_scorer_command
from hillclimber.strategies.registry import get_strategy, verify_agents

# How much of the scorer's output to show when diagnosing a failure — enough to
# see the traceback or the stray print, without flooding the terminal.
_TAIL_LINES = 10


def _tail(state: CLIState, stream: str, label: str) -> None:
    """Print the last few lines of a captured stream, dimmed, for context.

    Silent in ``--json`` mode: the error field carries the verdict, and a
    machine consumer must not have to skip past diagnostics.
    """
    lines = stream.strip().splitlines()
    if state.json or not lines:
        return
    err_console.print(f"[dim]last {min(len(lines), _TAIL_LINES)} lines of {label}:[/]")
    for line in lines[-_TAIL_LINES:]:
        # style= (not markup tags): the stream is arbitrary text, never markup.
        err_console.print(f"  {line}", style="dim", markup=False, highlight=False)


def _has_unmarked_score_line(stdout: str) -> bool:
    """Whether stdout holds a JSON object with a ``score`` but no envelope marker.

    The near-miss worth a targeted hint: the eval computed a score and printed
    it, but without ``"hillclimber_eval": 1`` the runner will never read it.
    """
    for line in stdout.splitlines():
        try:
            payload = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "score" in payload and "hillclimber_eval" not in payload:
            return True
    return False


def _fail(state: CLIState, error: str, hint: str | None = None, warnings: list[str] | None = None) -> NoReturn:
    """Report a failed check (plus any warnings gathered before it) and exit 1."""
    if state.json:
        console.print_json(json.dumps({"ok": False, "error": error, "warnings": warnings or []}))
    else:
        # escape(): the message may quote literal [brackets] like toml table names.
        err_console.print(f"[red]✗[/] {escape(error)}")
        if hint:
            err_console.print(f"[yellow]hint:[/] {escape(hint)}")
    raise typer.Exit(code=1)


def check(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Argument(help="Experiment directory (or its hillclimber.toml).")] = None,
) -> None:
    """Run the scorer once and verify the eval emits a valid envelope."""
    state: CLIState = ctx.obj
    target = path or Path()

    # 1. The config parses and the scorer command is known.
    try:
        config = load_config(target)
    except (FileNotFoundError, ValueError) as exc:
        _fail(state, f"config: {exc}")
    if not state.json:
        console.print(f"[green]✓[/] config loaded (scorer: [bold]{config.scorer.cmd}[/])")

    # 2. The agents cover the strategy's declared roles. A missing role is fatal
    #    (the run couldn't start either); an unused one only warns.
    try:
        agent_warnings = verify_agents(config)
    except ValueError as exc:
        _fail(state, str(exc))
    if not state.json:
        for warning in agent_warnings:
            err_console.print(f"[yellow]warning:[/] {escape(warning)}")
        roles = ", ".join(get_strategy(config.strategy).roles)
        console.print(f'[green]✓[/] agents cover strategy "{config.strategy}" ({roles})')

    # 3. The command runs — in the artefact dir, exactly as the baseline would:
    #    same subprocess chokepoint, same wall-clock ceiling.
    started = time.perf_counter()
    try:
        returncode, stdout, stderr = asyncio.run(
            run_scorer_command(config.scorer, config.path_to_artefact, timeout=config.timeout.scorer_seconds)
        )
    except ScorerError as exc:
        _fail(state, str(exc), warnings=agent_warnings)
    elapsed = time.perf_counter() - started
    if returncode != 0:
        _tail(state, stderr, "stderr")
        hint = None
        if "ModuleNotFoundError" in stderr:
            hint = (
                "the scorer runs in your shell's environment; whatever `python` resolves to there "
                "must be able to import everything eval.py uses"
            )
        _fail(state, f"scorer exited {returncode} after {elapsed:.1f}s", hint, warnings=agent_warnings)
    if not state.json:
        console.print(f"[green]✓[/] scorer ran (exit 0, {elapsed:.1f}s)")

    # 4. The output carries a valid envelope.
    try:
        evaluation = parse_eval(stdout)
    except ValueError as exc:
        _tail(state, stdout, "stdout")
        hint = None
        if _has_unmarked_score_line(stdout):
            hint = (
                'found a JSON line with a "score" but no marker — add "hillclimber_eval": 1 to the '
                "object your eval prints"
            )
        _fail(state, str(exc), hint, warnings=agent_warnings)

    if state.json:
        console.print_json(
            json.dumps(
                {
                    "ok": True,
                    "score": evaluation.score,
                    "details": evaluation.details,
                    "elapsed_s": round(elapsed, 3),
                    "warnings": agent_warnings,
                }
            )
        )
        return
    console.print(f"[green]✓[/] envelope valid (score [bold]{evaluation.score:.3f}[/])")
    if not 0.0 <= evaluation.score <= 1.0:
        console.print("[dim]note: score is outside the usual 0..1 range (allowed, just unconventional)[/]")
    console.print("\n[green]eval conforms — ready to climb[/]")
