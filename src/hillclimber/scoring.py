"""Scoring the artefact.

The scorer is the fitness function (see ``Scorer`` / ``CommandScorer``): it runs
a command in a checkout, which prints its ``Eval`` as JSON on stdout, and turns
that into a comparable ``Score``. Both the one-off baseline (``get_baseline_score``)
and each cycle's post-apply score (``Chain._apply_hypothesis``) route through
``score_artefact`` so the two read the same number the same way — the only
difference is the directory: the artefact dir for the baseline, a run's worktree
once a hypothesis has been applied.

Async per CLAUDE.md "Concurrency": the command is shelled out with
``asyncio.create_subprocess_shell`` so scoring never blocks the event loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

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


def _parse_eval(stdout: str) -> Eval:
    """Read the ``Eval`` a command scorer emits as JSON on its output.

    The scorer command (e.g. ``python eval.py``) prints its ``Eval`` as JSON;
    this scans stdout from the last line back so the score is still found even if
    the artefact wrote other noise to stdout first. The last JSON object that
    validates as an ``Eval`` wins.

    Args:
        stdout: The scorer command's captured standard output.

    Returns:
        The parsed ``Eval``.

    Raises:
        ValueError: If no line of ``stdout`` parses as an ``Eval``.
    """
    for line in reversed(stdout.splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            return Eval.model_validate_json(candidate)
        except ValueError:
            # Not the Eval line (plain text or unrelated JSON) — keep scanning.
            continue
    raise ValueError("scorer produced no Eval JSON on stdout")


async def score_artefact(scorer: Scorer, cwd: str | Path, *, require_success: bool = False) -> Score:
    """Score the artefact in ``cwd`` with ``scorer``.

    A command scorer runs its ``cmd`` in ``cwd``; the command emits its ``Eval``
    as JSON on stdout (see ``Eval``), and that ``Eval.score`` is the climbable
    value.

    A command that fails to run (non-zero exit) is handled one of two ways:

    - ``require_success`` false (default, per-cycle scoring): the failure scores
        ``0.0`` with ``passed`` false — a broken hypothesis is just a bad score.
    - ``require_success`` true (the baseline): the failure raises ``ScorerError``.
        A scorer that cannot run is a misconfiguration, not a score of zero, and
        there is no hill to climb without a valid baseline — so abort loudly
        rather than fabricate a ``0.0`` the whole run would then climb against.

    Args:
        scorer: The fitness function to run (v1: a command scorer).
        cwd: The directory to score in — the artefact directory for the baseline,
            a run's worktree once a hypothesis has been applied.
        require_success: Treat a non-zero exit as fatal (raise ``ScorerError``)
            rather than a ``0.0`` score. Set for the baseline.

    Returns:
        The ``Score`` — ``Eval.score`` as ``value`` when the command ran, else
        ``0.0`` with ``passed`` false.

    Raises:
        ScorerError: If the command failed (non-zero exit) and ``require_success``.
        ValueError: If the command ran but emitted no parseable ``Eval`` JSON.
    """
    logger.debug("scoring: %s (cwd=%s)", scorer.cmd, cwd)
    proc = await asyncio.create_subprocess_shell(
        scorer.cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        detail = stderr.decode().strip()
        if require_success:
            raise ScorerError(f"scorer {scorer.cmd!r} failed (exit={proc.returncode}) in {cwd}: {detail}")
        logger.warning("scorer failed (exit=%s): %s", proc.returncode, detail)
        return Score(value=0.0, passed=False, scorer_id=scorer.kind)

    evaluation = _parse_eval(stdout.decode())
    return Score(value=evaluation.score, passed=True, scorer_id=scorer.kind)
