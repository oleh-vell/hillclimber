"""Run-time entry points for hillclimber.

The loop runner lives here. Each run scores the artefact with the config's
scorer; the baseline is scored once before any cycle spins up.

Everything here is ``async`` (see CLAUDE.md "Concurrency"): scoring shells out
and runs may fan out, so the runner is built on asyncio from the ground up.
Call into it with ``asyncio.run(run(path))`` from a synchronous entry point.
"""

from __future__ import annotations

from pathlib import Path

from hillclimber.config import load_config
from hillclimber.git_utils import (
    check_or_init_git,
    check_uncommitted_changes,
    create_detached_worktree,
    create_snapshot_commit,
    remove_worktree_if_present,
)
from hillclimber.harnesses import TraceSink
from hillclimber.models import Config, ExperimentStatus, Score
from hillclimber.progress import RunEvent, RunEventSink, ignore_progress
from hillclimber.sandboxes import get_sandbox
from hillclimber.scoring import score_artefact
from hillclimber.strategies.registry import get_strategy, verify_agents
from hillclimber.telemetry import get_logger

logger = get_logger(__name__)

# Name of the throwaway worktree used to score the baseline at an explicit
# ``start_branch`` (see ``get_baseline_score``).
_BASELINE_WORKTREE = "hc_baseline"


async def run(
    path: str | Path,
    trace_sink: TraceSink | None = None,
    progress_sink: RunEventSink | None = None,
) -> ExperimentStatus:
    """Run an experiment end to end from its ``hillclimber.toml``.

    Parses the config from ``path`` (a directory holding a ``hillclimber.toml``,
    or the file itself), preflights the configured models, scores the baseline
    once, then hands off to the configured strategy, which drives the cycle
    loop to completion (see ``hillclimber.strategies.chain``).

    Args:
        path: The experiment directory (or its ``hillclimber.toml``).
        trace_sink: Where labelled agent trace events land as cycles run (the
            CLI's live view). ``None`` falls back to the logging sink (see
            ``hillclimber.strategies.base.log_trace``), so the run still narrates itself
            through ordinary logs.
        progress_sink: Where run-level milestones land — baseline, preflight,
            cycle lifecycle (see ``hillclimber.progress``). ``None`` drops them;
            the same story is already told at INFO by the logs.

    Returns:
        The final ``ExperimentStatus`` produced by the strategy.
    """
    emit = progress_sink if progress_sink is not None else ignore_progress
    logger.info("loading experiment from %s", path)
    config = load_config(path)
    # The run's opening statement: what is being improved and what success
    # looks like, before any milestone lands.
    goal_clause = (
        f"raise the eval score to {config.goal.target:.3f}"
        if config.goal.target is not None
        else "maximize the eval score"
    )
    emit(
        RunEvent(
            kind="run_start",
            message=f"goal: improve {config.path_to_artefact} — {goal_clause} (budget: {config.budget.cycles} cycles)",
        )
    )
    logger.info(
        "artefact=%s strategy=%s budget=%d cycles goal=%s",
        config.path_to_artefact,
        config.strategy,
        config.budget.cycles,
        f"{config.goal.target:.3f}" if config.goal.target is not None else "maximize",
    )
    # A missing strategy role raises here; an unused agent table only warns.
    for warning in verify_agents(config):
        logger.warning(warning)

    # Cycles fork from committed state, so a dirty artefact would have the baseline
    # (scored on the working tree) and the cycles (forked from HEAD) measure
    # different code. Refuse to start rather than silently diverge; with
    # ``auto_commit`` set, snapshot the dirty tree into a commit and climb from that.
    # An explicit ``start_branch`` skips all of this: the baseline is scored in a
    # throwaway checkout of that ref and cycle 1 forks from it, so the working
    # tree's state is irrelevant — and must not be snapshotted over the user's ref.
    if not config.start_branch and await check_uncommitted_changes(config.path_to_artefact):
        if config.auto_commit:
            # Capture the dirty tree as a non-destructive commit and climb from
            # it: routing the snapshot sha through ``start_branch`` makes both the
            # baseline (scored at that ref) and cycle 1 (forked from it) measure
            # the same code, including the user's in-progress edits.
            snapshot = await create_snapshot_commit(
                config.path_to_artefact, "hillclimber: snapshot uncommitted changes"
            )
            config.start_branch = snapshot
            logger.info("auto_commit: climbing from snapshot %s", snapshot)
        else:
            raise RuntimeError(
                f"artefact has uncommitted changes at {config.path_to_artefact}; "
                "commit or stash them before running (cycles fork from committed state), "
                "or set auto_commit = true to snapshot them automatically"
            )

    # The sandbox confines every agent CLI to its worktree; the strategy
    # threads it down into the harness.
    sandbox = get_sandbox(config.sandbox)
    strategy = get_strategy(config.strategy)(
        sandbox, trace_sink=trace_sink, progress_sink=progress_sink, timeouts=config.timeout
    )
    # Preflight before scoring: a bad model alias or an unauthed CLI fails here,
    # not after the baseline eval has been paid for. Only the strategy's roles
    # are probed — an unused agent table is ignored, as verify_agents promised.
    logger.info("verifying harness can run the configured models")
    emit(RunEvent(kind="preflight_start", message="verifying the configured models"))
    await strategy.harness.verify(config.agents[role] for role in type(strategy).roles)
    emit(RunEvent(kind="preflight_done", message="models verified"))

    emit(RunEvent(kind="baseline_start", message="scoring the baseline"))
    baseline = await get_baseline_score(config)
    emit(RunEvent(kind="baseline_done", message=f"baseline scored {baseline.value:.3f}", score=baseline.value))
    status = await strategy.execute(config, baseline)
    logger.info("experiment finished: %d/%d cycles run", status.completed, status.total)
    return status


async def get_baseline_score(config: Config) -> Score:
    """Score the artefact once with the config's scorer.

    The scorer is the fitness function (see ``Scorer`` / ``CommandScorer``). A
    command scorer runs its ``cmd`` in the artefact directory; the command emits
    its ``Eval`` as JSON on stdout (see ``Eval``), and that ``Eval.score`` is the
    climbable value. This baseline is the number later cycles must beat, so a
    scorer that cannot run is fatal: unlike a per-cycle score, a failing baseline
    command raises ``ScorerError`` rather than fabricating a ``0.0`` the whole run
    would then climb against (``require_success=True``).

    Where the baseline is scored tracks where cycle 1 forks from (see
    ``Chain._prepare_repo``), so the two measure the same code:

    - No ``start_branch`` (the default): score the working tree in place. The
        runner has already refused to start on a dirty tree, so this is the same
        committed state cycle 1 forks from (``HEAD``).
    - An explicit ``start_branch``: score a throwaway checkout of that ref, since
        it may differ from the working tree. The checkout is torn down after.

    Args:
        config: The validated experiment config. ``config.scorer`` drives the
            scoring; ``config.path_to_artefact`` is the artefact repo and
            ``config.start_branch`` the ref to score at (if any).

    Returns:
        The baseline ``Score`` — ``Eval.score`` as ``value`` (``passed`` true,
        since a failing command aborts rather than returns).

    Raises:
        ScorerError: If the scorer command failed to run (non-zero exit).
        ValueError: If the command ran but emitted no parseable ``Eval`` JSON.
    """
    if config.start_branch:
        # Score committed state at the start ref, isolated in a throwaway
        # checkout so it can differ from the working tree without touching it.
        await check_or_init_git(config.path_to_artefact)
        # A killed prior run can leave a stale ``hc_baseline`` (dir + git
        # registration) that would make ``worktree add`` fail with an opaque
        # error; clear it best-effort before creating a fresh one.
        await remove_worktree_if_present(config.path_to_artefact, _BASELINE_WORKTREE)
        worktree = await create_detached_worktree(config.path_to_artefact, _BASELINE_WORKTREE, config.start_branch)
        try:
            score = await score_artefact(
                config.scorer, worktree, require_success=True, timeout=config.timeout.scorer_seconds
            )
        finally:
            # Best-effort so a teardown error can never mask a ScorerError from
            # the scoring above (the branch keeps nothing here — it is detached).
            await remove_worktree_if_present(config.path_to_artefact, _BASELINE_WORKTREE)
    else:
        score = await score_artefact(
            config.scorer, config.path_to_artefact, require_success=True, timeout=config.timeout.scorer_seconds
        )
    logger.info("baseline scored: %.3f", score.value)
    return score
