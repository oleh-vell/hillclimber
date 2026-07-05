"""Scoring the artefact.

The scorer is the fitness function (see ``Scorer`` / ``CommandScorer``): it runs
a command in a checkout, which prints its ``Eval`` envelope as JSON on stdout,
and turns that into a comparable ``Score``. Both the one-off baseline
(``get_baseline_score``) and each cycle's post-apply score
(``Chain._apply_hypothesis``) route through ``score_artefact`` so the two read
the same number the same way — the only difference is the directory: the
artefact dir for the baseline, a run's worktree once a hypothesis has been
applied. ``hillclimber check`` reuses the same pieces (``run_scorer_command`` +
``parse_eval``) so a green check means the climb will read the eval identically.

Async per CLAUDE.md "Concurrency": the command is shelled out with
``asyncio.create_subprocess_shell`` so scoring never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from pathlib import Path

from pydantic import ValidationError

from hillclimber.models import Eval, Score, Scorer
from hillclimber.telemetry import get_logger

logger = get_logger(__name__)


class ScorerError(RuntimeError):
    """The scorer command itself failed to run — no score could be measured.

    Distinct from a low score: the fitness function never produced a number, so
    there is nothing to climb. Raised when a scorer is *required* to succeed (the
    baseline; see ``get_baseline_score``), as opposed to a per-cycle score where a
    failing hypothesis legitimately scores ``0.0``.
    """


def parse_eval(stdout: str) -> Eval:
    """Read the ``Eval`` envelope a command scorer emits on its stdout.

    Recognition is by *marker*, not by shape: the eval ends by printing one JSON
    line carrying ``"hillclimber_eval": 1`` (see ``Eval``). Scanning from the
    last line back, the last marked line wins; unmarked JSON — a metrics dump,
    an API response the artefact happened to print — is never mistaken for the
    score. A marked line that fails validation is an *error*, not noise: the
    eval clearly tried to emit a result, and scanning past it would hide the bug.

    Args:
        stdout: The scorer command's captured standard output.

    Returns:
        The parsed ``Eval``.

    Raises:
        ValueError: If no line of ``stdout`` carries the envelope marker, or the
            marked line does not validate against the ``Eval`` schema.
    """
    for line in reversed(stdout.splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or "hillclimber_eval" not in payload:
            continue
        try:
            return Eval.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(
                f"scorer emitted a hillclimber_eval envelope that does not match the schema: {exc}"
            ) from exc
    raise ValueError(
        "scorer printed no hillclimber_eval envelope on stdout; the eval must end by printing one JSON line "
        'like {"hillclimber_eval": 1, "score": 0.5, "details": {}}'
    )


async def run_scorer_command(scorer: Scorer, cwd: str | Path, timeout: float | None = None) -> tuple[int, str, str]:
    """Run the scorer's command in ``cwd`` and capture what happened.

    The one place the scorer subprocess is spawned: ``score_artefact`` builds a
    ``Score`` from it, and ``hillclimber check`` reports on it — both see the
    exact same execution.

    Args:
        scorer: The fitness function to run (v1: a command scorer).
        cwd: The directory to run the command in.
        timeout: Wall-clock ceiling in seconds; ``None`` waits indefinitely. On
            overrun the command (and its children) are killed and ``ScorerError``
            is raised, so a hung eval never stalls the climb.

    Returns:
        ``(returncode, stdout, stderr)`` with the streams decoded.

    Raises:
        ScorerError: If the command does not finish within ``timeout`` seconds.
    """
    logger.debug("scoring: %s (cwd=%s)", scorer.cmd, cwd)
    proc = await asyncio.create_subprocess_shell(
        scorer.cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,  # session leader, so the kill below reaches the whole group
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError as exc:
        raise ScorerError(f"scorer {scorer.cmd!r} exceeded the {timeout}s timeout in {cwd}") from exc
    finally:
        # Covers the timeout and a cancelled climb: kill and reap the scorer so
        # a hung or aborted eval never leaks processes. The shell is a session
        # leader, so killing its group also takes down the command it spawned
        # (a compound ``cd x && pytest`` would otherwise leave orphans).
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(Exception):
                await proc.wait()
    # ``communicate`` only returns once the process exited, so returncode is set.
    assert proc.returncode is not None
    return proc.returncode, stdout.decode(), stderr.decode()


async def score_artefact(
    scorer: Scorer, cwd: str | Path, *, require_success: bool = False, timeout: float | None = None
) -> Score:
    """Score the artefact in ``cwd`` with ``scorer``.

    A command scorer runs its ``cmd`` in ``cwd``; the command emits its ``Eval``
    as JSON on stdout (see ``Eval``), and that ``Eval.score`` is the climbable
    value.

    A scorer that does not yield a usable number — a non-zero exit, a timeout, or
    an exit-0 run that printed no valid ``Eval`` envelope — is handled one of two
    ways:

    - ``require_success`` false (default, per-cycle scoring): it scores ``0.0``
        with ``passed`` false. A broken hypothesis is just a bad score, and the
        worker can even break the eval script in its own worktree — that must not
        abort the whole experiment, only sink that one cycle.
    - ``require_success`` true (the baseline): it raises. A scorer that cannot
        produce a baseline is a misconfiguration, not a score of zero, and there
        is no hill to climb without a valid baseline — so abort loudly rather
        than fabricate a ``0.0`` the whole run would then climb against.

    Args:
        scorer: The fitness function to run (v1: a command scorer).
        cwd: The directory to score in — the artefact directory for the baseline,
            a run's worktree once a hypothesis has been applied.
        require_success: Treat any unscorable outcome as fatal (raise) rather than
            a ``0.0`` score. Set for the baseline.
        timeout: Wall-clock ceiling in seconds for the scorer command; ``None``
            waits indefinitely (see ``run_scorer_command``).

    Returns:
        The ``Score`` — ``Eval.score`` as ``value`` when the command produced a
        valid envelope, else ``0.0`` with ``passed`` false.

    Raises:
        ScorerError: If the command failed/timed out and ``require_success``.
        ValueError: If the command exited 0 but emitted no valid ``Eval``
            envelope and ``require_success``.
    """
    try:
        returncode, stdout, stderr = await run_scorer_command(scorer, cwd, timeout)
    except ScorerError:
        # A timeout (or other run failure) surfaced from the subprocess layer.
        if require_success:
            raise
        logger.warning("scorer did not complete; scoring 0.0")
        return Score(value=0.0, passed=False, scorer_id=scorer.kind)

    if returncode != 0:
        detail = stderr.strip()
        if require_success:
            raise ScorerError(f"scorer {scorer.cmd!r} failed (exit={returncode}) in {cwd}: {detail}")
        logger.warning("scorer failed (exit=%s): %s", returncode, detail)
        return Score(value=0.0, passed=False, scorer_id=scorer.kind)

    try:
        evaluation = parse_eval(stdout)
    except ValueError:
        # Exit 0 but no parseable envelope: for a required baseline this is a
        # real misconfiguration, but for a per-cycle score it is just a hypothesis
        # that left the eval unable to report — a bad score, not a run-ender.
        if require_success:
            raise
        logger.warning("scorer exited 0 but emitted no valid eval envelope; scoring 0.0")
        return Score(value=0.0, passed=False, scorer_id=scorer.kind)
    return Score(value=evaluation.score, passed=True, scorer_id=scorer.kind)
