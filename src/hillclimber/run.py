"""Run-time entry points for hillclimber.

The loop runner lives here (see README "Architecture seam"). Each run scores
the artefact with the config's scorer; the baseline is scored once before any
cycle spins up.

Everything here is ``async`` (see CLAUDE.md "Concurrency"): scoring shells out
and runs may fan out, so the runner is built on asyncio from the ground up.
Call into it with ``asyncio.run(run(path))`` from a synchronous entry point.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from hillclimber.config import load_config
from hillclimber.models import Config, ExperimentStatus, Score
from strategies.chain import Chain


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
    config = load_config(path)
    baseline = await get_baseline_score(config)
    strategy = Chain()
    return await strategy.execute(config, baseline)


async def get_baseline_score(config: Config) -> Score:
    """Score the artefact once with the config's scorer.

    The scorer is the fitness function (see ``Scorer`` / ``CommandScorer``). A
    command scorer runs its ``cmd`` in the artefact directory; the run passes
    when the command exits ``0``, scoring ``1.0`` on pass and ``0.0`` on fail.
    This baseline is the number later cycles must beat.

    Args:
        config: The validated experiment config. ``config.scorer`` drives the
            scoring; ``config.path_to_artefact`` is the working directory.

    Returns:
        The baseline ``Score`` produced by the scorer.
    """
    scorer = config.scorer
    proc = await asyncio.create_subprocess_shell(
        scorer.cmd,
        cwd=config.path_to_artefact,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    passed = proc.returncode == 0
    return Score(value=1.0 if passed else 0.0, passed=passed, scorer_id=scorer.kind)
