"""Run-time entry points for hillclimber.

The loop runner lives here (see README "Architecture seam"). Each run scores
the artefact with the config's scorer; the baseline is scored once before any
cycle spins up.

Everything here is ``async`` (see CLAUDE.md "Concurrency"): scoring shells out
and runs may fan out, so the runner is built on asyncio from the ground up.
Call into it with ``asyncio.run(run(path))`` from a synchronous entry point.
"""

from __future__ import annotations

from pathlib import Path

from hillclimber.config import load_config
from hillclimber.git_utils import check_uncommitted_changes
from hillclimber.models import Config, ExperimentStatus, Score
from hillclimber.scoring import score_artefact
from hillclimber.telemetry import get_logger
from sandboxes import get_sandbox
from strategies.chain import Chain

logger = get_logger(__name__)


async def run(path: str | Path) -> ExperimentStatus:
    """Run an experiment end to end from its ``hillclimber.toml``.

    v1 is the thinnest possible slice of the loop runner (see README
    "Architecture seam"): parse the config from ``path`` (a directory holding a
    ``hillclimber.toml``, or the file itself) and score the baseline once. The
    per-cycle mutation loop attaches here later.

    Args:
        path: The experiment directory (or its ``hillclimber.toml``).

    Returns:
        The final ``ExperimentStatus`` produced by the strategy.
    """
    logger.info("loading experiment from %s", path)
    config = load_config(path)
    logger.info(
        "artefact=%s strategy=%s budget=%d cycles", config.path_to_artefact, config.strategy, config.budget.cycles
    )

    # Cycles fork from committed state, so a dirty artefact would have the baseline
    # (scored on the working tree) and the cycles (forked from HEAD) measure
    # different code. Refuse to start rather than silently diverge — commit or
    # stash first.
    if await check_uncommitted_changes(config.path_to_artefact):
        raise RuntimeError(
            f"artefact has uncommitted changes at {config.path_to_artefact}; "
            "commit or stash them before running (cycles fork from committed state)"
        )

    baseline = await get_baseline_score(config)
    # Build the OS sandbox that confines every agent CLI to its worktree and
    # hand it to the strategy, which threads it down into the harness.
    sandbox = get_sandbox(config.sandbox)
    strategy = Chain(sandbox)
    status = await strategy.execute(config, baseline)
    logger.info("experiment finished: %d/%d cycles run", status.completed, status.total)
    return status


async def get_baseline_score(config: Config) -> Score:
    """Score the artefact once with the config's scorer.

    The scorer is the fitness function (see ``Scorer`` / ``CommandScorer``). A
    command scorer runs its ``cmd`` in the artefact directory; the command emits
    its ``Eval`` as JSON on stdout (see ``Eval``), and that ``Eval.score`` is the
    climbable value. A command that fails to run (non-zero exit) scores ``0.0``.
    This baseline is the number later cycles must beat.

    Args:
        config: The validated experiment config. ``config.scorer`` drives the
            scoring; ``config.path_to_artefact`` is the working directory.

    Returns:
        The baseline ``Score`` — ``Eval.score`` as ``value`` when the command
        ran, else ``0.0``.

    Raises:
        ValueError: If the command ran but emitted no parseable ``Eval`` JSON.
    """
    score = await score_artefact(config.scorer, config.path_to_artefact)
    if score.passed:
        logger.info("baseline scored: %.3f", score.value)
    return score
