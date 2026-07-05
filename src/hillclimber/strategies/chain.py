"""The ``chain`` strategy.

Chains cycles one after another: each cycle forks a worktree from its parent,
asks the orchestrator for a hypothesis, has the worker apply it, scores the
committed result, and folds it into the running ``ExperimentStatus`` (see
README "Core loop"). The next cycle builds on the last good branch, so the
climb accumulates.
"""

from __future__ import annotations

import contextlib

from hillclimber.git_utils import check_or_init_git, create_worktree, head_sha, remove_worktree_if_present
from hillclimber.harnesses.base import HarnessRun
from hillclimber.lockfile import ExperimentLog, lock_path, score_value
from hillclimber.models import Config, Cycle, CycleStatus, CycleSummary, ExperimentStatus, Score
from hillclimber.progress import RunEvent
from hillclimber.scoring import score_artefact
from hillclimber.strategies import prompt
from hillclimber.strategies.base import CycleRecord, RoleSpec, Strategy
from hillclimber.telemetry import get_logger

logger = get_logger(__name__)


class Chain(Strategy):
    """Run cycles in sequence, recording the best so far."""

    # The chain drives two roles per cycle: the orchestrator proposes, the
    # worker applies. No reflector yet — the reflect step is scaffolded but not
    # wired into ``execute``, so declaring it would force users to configure an
    # agent that never runs (re-add it here when the step lands).
    roles = {
        "orchestrator": RoleSpec(
            default_prompt=prompt.ORCHESTRATOR_AGENT,
            description="Proposes the next hypothesis for improving the artefact.",
        ),
        "worker": RoleSpec(
            default_prompt=prompt.WORKER_AGENT,
            description="Applies the proposed change to the artefact.",
        ),
    }

    async def one_cycle(
        self,
        config: Config,
        experiment_id: str,
        index: int,
        parent_ref: str,
        parent_score: Score,
    ) -> Cycle:
        """Run a single climb cycle in its own worktree: propose -> apply -> score.

        The score is read from the cycle's own commit, not the artefact's ``HEAD``:
        the worker commits its change in the cycle's checkout, and the next cycle
        forks from *this* branch (see ``execute``) — so the climb chains, each
        cycle building on the last.
        """
        # Worktree/branch names are scoped per (experiment, cycle):
        # hc_<XXXXXXXX>_cycle_<NNN>, using the experiment's full 8-hex id (not a
        # short prefix) so branch names can't collide with a prior experiment's
        # leftover hc/* branches.
        slug = f"{experiment_id.removeprefix('exp_')}_cycle_{index:03d}"
        worktree_name = f"hc_{slug}"
        branch = f"hc/{slug}"
        logger.info("cycle %d: worktree %s off %s", index, worktree_name, parent_ref)
        worktree = await create_worktree(config.path_to_artefact, worktree_name, branch, parent_ref)
        # The cycle's commit lives on ``branch`` (which the next cycle forks from);
        # the worktree checkout is disposable, so it is torn down on every exit —
        # success or failure — rather than accumulating a full checkout per cycle.
        try:
            # The commit the branch forks from — used to tell whether the worker
            # actually produced a new commit.
            base_sha = await head_sha(worktree)

            hypothesis = await self._propose_hypothesis(config, worktree, index)
            logger.debug("cycle %d: hypothesis: %s", index, hypothesis)

            cycle = Cycle(
                experiment_id=experiment_id,
                index=index,
                parent_ref=parent_ref,
                branch=branch,
                worktree=worktree_name,
                hypothesis=hypothesis,
                score_before=parent_score,
                status=CycleStatus.running,
            )
            await self.write_lock(worktree, cycle)

            await self._apply_hypothesis(config, cycle, worktree)

            # The sandbox denies the worker git, so committing is the runner's job.
            cycle.commit_sha = await self._commit_cycle(worktree, base_sha, index)

            # A hypothesis that leaves the eval unable to report (broken script,
            # timeout) scores 0.0/passed false rather than aborting the experiment
            # — record it as failed.
            self.progress_sink(
                RunEvent(
                    kind="cycle_stage",
                    message="scoring the change",
                    index=index,
                    total=config.budget.cycles,
                    stage="scoring",
                )
            )
            cycle.score_after = await score_artefact(config.scorer, worktree, timeout=config.timeout.scorer_seconds)
            cycle.status = CycleStatus.scored if cycle.score_after.passed else CycleStatus.failed
            logger.info(
                "cycle %d: scored %.3f (was %.3f)",
                index,
                cycle.score_after.value,
                parent_score.value,
            )

            self._cycle_records.append(
                CycleRecord(hypothesis=hypothesis, before=parent_score.value, after=cycle.score_after.value)
            )
            await self.write_lock(worktree, cycle)
            return cycle
        finally:
            # Drop the checkout (the branch, and thus the commit, survives). Best
            # effort so a teardown hiccup never masks a real cycle error.
            await remove_worktree_if_present(config.path_to_artefact, worktree_name)

    async def _propose_hypothesis(self, config: Config, worktree: str, index: int) -> str:
        """Ask the orchestrator agent for one hypothesis, returned as plain text.

        The prompt is primed with the experiment's past cycles (see
        ``_render_history``) so each hypothesis builds on what earlier cycles
        already learned rather than restating it.
        """
        agent = self._role_agent(config, "orchestrator")
        assert agent.system_prompt is not None  # resolved by _role_agent
        self.progress_sink(
            RunEvent(
                kind="cycle_stage",
                message="proposing a hypothesis",
                index=index,
                total=config.budget.cycles,
                stage="proposing",
            )
        )
        task = (
            "Inspect the artefact in this directory and how it is scored, then "
            "propose exactly one concrete, testable change that should raise the "
            "eval score.\n\n"
            f"{self._render_history(self._cycle_records)}"
            "Reply with only the hypothesis as one short paragraph."
        )
        return await self.harness.run(
            HarnessRun(
                system_prompt=agent.system_prompt,
                prompt=task,
                path=worktree,
                model=agent.model,
            ),
            on_trace=self._make_trace_sink(f"cycle {index:03d}/orchestrator"),
        )

    async def _apply_hypothesis(self, config: Config, cycle: Cycle, worktree: str) -> None:
        """Run the worker agent to apply ``cycle.hypothesis`` inside ``worktree``.

        The worker is handed only the hypothesis — a fresh context with no memory
        of how it was proposed. It only edits: the sandbox denies it git (the
        worktree's metadata lives in the parent repo, outside the boundary), so
        ``one_cycle`` commits afterwards. And it is never asked to run or reason
        about the eval — that avoids it chasing a failing score in an open-ended
        remediation loop.
        """
        agent = self._role_agent(config, "worker")
        assert agent.system_prompt is not None  # resolved by _role_agent
        self.progress_sink(
            RunEvent(
                kind="cycle_stage",
                message="applying the hypothesis",
                index=cycle.index,
                total=config.budget.cycles,
                stage="applying",
                hypothesis=cycle.hypothesis,
            )
        )
        task = (
            "Apply exactly this one change to the artefact in this directory, and "
            "make no other changes. Edit the files directly and stop when the "
            "change is complete — do not commit (git is unavailable here; the "
            "runner commits your edits). Do not run the tests or eval.\n\n"
            f"{cycle.hypothesis}"
        )
        await self.harness.run(
            HarnessRun(
                system_prompt=agent.system_prompt,
                prompt=task,
                path=worktree,
                model=agent.model,
            ),
            on_trace=self._make_trace_sink(f"cycle {cycle.index:03d}/worker"),
        )

    async def execute(self, config: Config, baseline: Score) -> ExperimentStatus:
        """Drive the chained climb to completion and return the final status.

        Runs cycles in sequence until either the goal is met or the budget is
        exhausted — both checked up front, so a goal already satisfied by the
        baseline (or a zero-cycle budget) runs nothing.
        """
        # Ensure the artefact is a git repo and capture the working tree (which
        # the baseline was scored on) as the commit the first cycle forks from.
        base_ref = await self._prepare_repo(config)

        experiment_id = self.new_experiment_id()
        logger.info(
            "%s: starting (baseline=%.3f, budget=%d cycles)", experiment_id, baseline.value, config.budget.cycles
        )
        # The experiment log is the durable side of this run: started now, one
        # promotion per settled cycle, a terminal line on the way out (see
        # ``hillclimber.lockfile``). Opened after ``_prepare_repo`` so a fresh
        # artefact is a git repo before ``.hillclimber`` first appears.
        log = ExperimentLog(lock_path(config.path_to_artefact), experiment_id)
        await log.record_started(strategy=config.strategy, baseline=baseline, budget=config.budget)
        # The chain: cycle 1 forks from the baseline snapshot; each later cycle
        # forks from the previous cycle's branch and must beat its score. So the
        # climb accumulates — every cycle builds on its predecessor's commit.
        parent_ref = base_ref
        parent_score = baseline

        cycles: list[CycleSummary] = []
        best: CycleSummary | None = None
        # The strongest score yet, baseline included — what the goal is checked
        # against. ``best`` (the best *cycle*) can sit below it if no cycle beat
        # baseline.
        peak_score = baseline
        completed = 0

        try:
            while not config.goal.is_met(peak_score) and not config.budget.is_exhausted(completed):
                index = completed + 1
                self.progress_sink(
                    RunEvent(
                        kind="cycle_start",
                        message=f"cycle {index}/{config.budget.cycles} starting",
                        index=index,
                        total=config.budget.cycles,
                    )
                )
                cycle = await self.one_cycle(
                    config,
                    experiment_id,
                    index=index,
                    parent_ref=parent_ref,
                    parent_score=parent_score,
                )
                completed += 1
                # The promotion: the cycle's settled ``cyc_<NNN>.lock`` state
                # becomes a permanent line in the experiment log.
                await log.record_cycle(cycle)
                self.progress_sink(self._cycle_done_event(cycle, config.budget.cycles))

                summary = CycleSummary.from_cycle(cycle, baseline)
                cycles.append(summary)

                after = cycle.score_after
                if after is not None:
                    if best is None or after.value > score_value(best):
                        best = summary
                    if after.value > peak_score.value:
                        peak_score = after

                # Chain: the next cycle forks from this cycle's branch and aims to
                # beat its score (carried forward even when it dipped below the
                # parent). A *failed* cycle is different: its 0.0 means "the eval
                # could not report", not a measurement — chaining onto that branch
                # would let any change that merely un-breaks the eval read as a
                # huge win, so the chain keeps the last good parent instead.
                if after is not None and after.passed:
                    parent_ref = cycle.branch
                    parent_score = after
        except BaseException:
            # Best-effort terminal record — a secondary write failure must not
            # mask the real error. A hard kill still leaves no finished line;
            # the reader then reports the experiment as running/interrupted,
            # which is the truth.
            with contextlib.suppress(Exception):
                await log.record_finished(
                    outcome="failed",
                    completed=completed,
                    best_cycle_id=best.cycle_id if best is not None else None,
                )
            raise

        await log.record_finished(
            outcome="completed",
            completed=completed,
            best_cycle_id=best.cycle_id if best is not None else None,
        )
        return ExperimentStatus(
            experiment_id=experiment_id,
            baseline_score=baseline,
            cycles=cycles,
            best=best,
            completed=completed,
            total=config.budget.cycles,
        )

    async def _prepare_repo(self, config: Config) -> str:
        """Ready the artefact repo and return the ref the first cycle forks from.

        Initialises git if needed and resolves the start ref:
        ``config.start_branch`` when set, else the artefact's current ``HEAD``.
        ``get_baseline_score`` scores the baseline at this same ref, so cycle 1
        and the baseline measure the same code (for the ``HEAD`` default,
        ``hillclimber.run`` has already refused to start on a dirty artefact).
        """
        await check_or_init_git(config.path_to_artefact)
        return config.start_branch or "HEAD"

    @staticmethod
    def _cycle_done_event(cycle: Cycle, total: int) -> RunEvent:
        """Build the ``cycle_done`` progress event for a completed ``cycle``.

        ``parent_delta`` is the movement against the cycle's *parent* (``score_before``)
        — "did this step of the chain climb" — not against the baseline, which a
        consumer can derive itself from the scores it has seen.
        """
        after = cycle.score_after
        if after is None:
            return RunEvent(
                kind="cycle_done",
                message=f"cycle {cycle.index} produced no score",
                index=cycle.index,
                total=total,
                hypothesis=cycle.hypothesis,
            )
        return RunEvent(
            kind="cycle_done",
            message=f"cycle {cycle.index} scored {after.value:.3f}",
            index=cycle.index,
            total=total,
            score=after.value,
            parent_delta=after.value - cycle.score_before.value,
            hypothesis=cycle.hypothesis,
        )
