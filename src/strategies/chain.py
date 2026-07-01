"""The ``chain`` strategy.

Chains cycles one after another: each run is attempted in sequence and folded
into the running ``ExperimentStatus``. v1 is the thinnest slice — it establishes
the baseline status that later cycles will accumulate into (see README "Core
loop" / "Architecture seam"). The per-cycle mutation loop attaches here.
"""

from __future__ import annotations

from harnesses.claude import HarnessRun
from hillclimber.git_utils import (
    check_or_init_git,
    commit_all,
    create_worktree,
    head_sha,
    is_dirty,
)
from hillclimber.models import Config, ExperimentStatus, Run, RunStatus, RunSummary, Score
from hillclimber.scoring import score_artefact
from hillclimber.telemetry import get_logger
from strategies.base import Strategy

logger = get_logger(__name__)


class Chain(Strategy):
    """Run cycles in sequence, recording the best so far."""

    async def one_cycle(
        self,
        config: Config,
        experiment_id: str,
        cycle: int,
        parent_ref: str,
        parent_score: Score,
    ) -> Run:
        """Run a single climb cycle: propose -> apply -> record.

        One cycle is one hypothesis attempt in its own worktree (see ``Run``):
        branch off the parent, ask the hillclimber agent for a hypothesis, record
        it in a ``.lock``, let the worker agent apply *and commit* it in a fresh
        context, verify the commit, then score that committed result and fold it
        back into the lock.

        The score is read from the cycle's own commit, not the artefact's ``HEAD``:
        the worktree is the run's checkout, the worker commits its change there, and
        the next cycle forks from *this* branch (see ``execute``) — so the climb
        chains, each cycle building on the last.

        Args:
            config: The validated experiment config.
            experiment_id: The owning experiment's id (``exp_...``); its first 4
                hex chars seed this cycle's worktree/branch names.
            cycle: The 1-based attempt number within the experiment.
            parent_ref: The git ref this run forks from — the baseline snapshot for
                cycle 1, the previous cycle's branch thereafter.
            parent_score: The score to beat — the parent's score, recorded as
                ``score_before``.

        Returns:
            The completed ``Run`` for this cycle.
        """
        # 1. Name and branch a fresh worktree off the parent. The worktree/branch
        #    are scoped per (experiment, cycle): hc_<XXXX>_cycle_<NNN>, where XXXX
        #    is the experiment's uuid prefix and NNN the zero-padded cycle number.
        slug = f"{experiment_id.removeprefix('exp_')[:4]}_cycle_{cycle:03d}"
        worktree_name = f"hc_{slug}"
        branch = f"hc/{slug}"
        logger.info("cycle %d: worktree %s off %s", cycle, worktree_name, parent_ref)
        worktree = await create_worktree(config.path_to_artefact, worktree_name, branch, parent_ref)
        # The commit the branch forks from — used to tell whether the worker
        # actually produced a new commit.
        base_sha = await head_sha(worktree)

        # 2. Ask the hillclimber agent for one hypothesis. [HARNESS SEAM]
        hypothesis = await self._propose_hypothesis(config, worktree)
        logger.debug("cycle %d: hypothesis: %s", cycle, hypothesis)

        # 3. Record the running run in its lock: hypothesis, parent, score_before.
        run = Run(
            experiment_id=experiment_id,
            cycle=cycle,
            parent_ref=parent_ref,
            branch=branch,
            worktree=worktree_name,
            hypothesis=hypothesis,
            score_before=parent_score,
            status=RunStatus.running,
        )
        await self.write_lock(worktree, run)

        # 4. Worker applies the hypothesis and commits it in a fresh context.
        #    [HARNESS SEAM]
        await self._apply_hypothesis(config, run, worktree)

        # 5. Verify the worker committed (backstop a forgotten commit), and record
        #    the commit the score is read from.
        run.commit_sha = await self._commit_cycle(worktree, base_sha, cycle)

        # 6. Score this cycle's committed result.
        run.score_after = await score_artefact(config.scorer, worktree)
        run.status = RunStatus.scored
        logger.info(
            "cycle %d: scored %.3f (was %.3f)",
            cycle,
            run.score_after.value,
            parent_score.value,
        )

        # 7. Fold the result back into the lock and hand the run back.
        await self.write_lock(worktree, run)
        return run

    async def _commit_cycle(self, worktree: str, base_sha: str, cycle: int) -> str:
        """Ensure the worker's change is committed; return the cycle's commit sha.

        The worker is asked to commit its own change (see ``_apply_hypothesis``).
        This verifies it did: any uncommitted leftovers are committed on its behalf
        so the cycle's result is a clean commit — the score is read from it and the
        next cycle forks from it. A cycle that produced no new commit at all (no
        change applied) is logged but not an error.

        Args:
            worktree: The run's checkout the worker edited.
            base_sha: The commit the branch forked from, to detect a no-op cycle.
            cycle: The 1-based cycle number, for log context.

        Returns:
            The sha of this cycle's resulting commit.
        """
        if await is_dirty(worktree):
            logger.warning("cycle %d: worker left uncommitted changes; committing them", cycle)
            await commit_all(worktree, f"hillclimber: cycle {cycle:03d}")
        sha = await head_sha(worktree)
        if sha == base_sha:
            logger.warning("cycle %d: worker produced no new commit (no change applied)", cycle)
        return sha

    async def _propose_hypothesis(self, config: Config, worktree: str) -> str:
        """Ask the hillclimber agent for one hypothesis, via the harness.

        Drives ``config.hillclimber_agent`` (its model and system prompt) against
        the artefact checkout in ``worktree`` through ``self.harness`` and returns
        the proposed change as text. v1 always routes through the Claude harness
        (see ``Strategy.__init__``); selecting the harness per ``Agent.harness``
        is a later refinement.

        Args:
            config: The validated experiment config.
            worktree: The run's checkout the agent inspects and reasons over.

        Returns:
            The proposed hypothesis as plain text.
        """
        agent = config.hillclimber_agent
        # Config fills each role's prompt default, so this is always set; guard
        # the invariant rather than smuggle an empty prompt to the agent.
        if agent.system_prompt is None:
            raise RuntimeError("hillclimber agent is missing a system prompt")

        task = (
            "Inspect the artefact in this directory and how it is scored, then "
            "propose exactly one concrete, testable change that should raise the "
            "eval score. Reply with only the hypothesis as one short paragraph."
        )
        return await self.harness.run(
            HarnessRun(
                system_prompt=agent.system_prompt,
                prompt=task,
                path=worktree,
                model=agent.model,
            )
        )

    async def _apply_hypothesis(self, config: Config, run: Run, worktree: str) -> None:
        """Run the worker agent to apply ``run.hypothesis`` and commit it.

        Drives ``config.worker_agent`` (its model and system prompt) in a fresh
        context through ``self.harness`` to apply ``run.hypothesis`` inside
        ``worktree`` and commit it with git. The worker is handed only the
        hypothesis as its task — a fresh context with no memory of how it was
        proposed (the proposer/worker split). v1 always routes through the Claude
        harness (see ``Strategy.__init__``).

        Scoring is *not* done here: it is read from the resulting commit by
        ``one_cycle`` (see ``_commit_cycle`` / ``score_artefact``), so the worker
        is never asked to run or reason about the eval — that avoids it chasing a
        failing score in an open-ended remediation loop.

        Args:
            config: The validated experiment config.
            run: The running run; ``run.hypothesis`` is the change to apply.
            worktree: The run's checkout the worker edits and commits in.
        """
        agent = config.worker_agent
        # Config fills each role's prompt default, so this is always set; guard
        # the invariant rather than smuggle an empty prompt to the agent.
        if agent.system_prompt is None:
            raise RuntimeError("worker agent is missing a system prompt")

        task = (
            "Apply exactly this one change to the artefact in this directory, and "
            "make no other changes. When done, commit it with git: stage your edits "
            "and run `git commit`. Do not run the tests or eval.\n\n"
            f"{run.hypothesis}"
        )
        await self.harness.run(
            HarnessRun(
                system_prompt=agent.system_prompt,
                prompt=task,
                path=worktree,
                model=agent.model,
            )
        )

    async def execute(self, config: Config, baseline: Score) -> ExperimentStatus:
        """Drive the chained climb to completion.

        Ensures the artefact directory is a git repository first (initialising
        it if need be) — every cycle forks its worktree from there. Then mints
        one experiment id and runs cycles in sequence until either the goal is
        met or the budget is exhausted — both checked up front, so a goal already
        satisfied by the baseline (or a zero-cycle budget) runs nothing. Each
        cycle's result is folded into the running ``best``/``runs`` view.

        Args:
            config: The validated experiment config.
            baseline: The baseline ``Score`` each run must beat.

        Returns:
            The final ``ExperimentStatus`` — every run attempted and the best so far.
        """
        # Ensure the artefact is a git repo and capture the working tree (which
        # the baseline was scored on) as the commit the first cycle forks from.
        base_ref = await self._prepare_repo(config)

        experiment_id = self.new_experiment_id()
        logger.info(
            "%s: starting (baseline=%.3f, budget=%d cycles)", experiment_id, baseline.value, config.budget.cycles
        )
        # The chain: cycle 1 forks from the baseline snapshot; each later cycle
        # forks from the previous cycle's branch and must beat its score. So the
        # climb accumulates — every cycle builds on its predecessor's commit.
        parent_ref = base_ref
        parent_score = baseline

        runs: list[RunSummary] = []
        best: RunSummary | None = None
        # The strongest score yet, baseline included — what the goal is checked
        # against. ``best`` (the best *run*) can sit below it if no run beat baseline.
        peak_score = baseline
        completed = 0

        while not config.goal.is_met(peak_score) and not config.budget.is_exhausted(completed):
            run = await self.one_cycle(
                config,
                experiment_id,
                cycle=completed + 1,
                parent_ref=parent_ref,
                parent_score=parent_score,
            )
            completed += 1

            summary = self._summarize(run, baseline)
            runs.append(summary)

            after = run.score_after
            if after is not None:
                if best is None or after.value > self._score_value(best):
                    best = summary
                if after.value > peak_score.value:
                    peak_score = after

            # Chain: the next cycle forks from this cycle's branch and aims to beat
            # its score (carried forward even when it dipped below the parent).
            parent_ref = run.branch
            if after is not None:
                parent_score = after

        return ExperimentStatus(
            baseline_score=baseline,
            runs=runs,
            best=best,
            completed=completed,
            total=config.budget.cycles,
        )

    async def _prepare_repo(self, config: Config) -> str:
        """Ready the artefact repo and return the ref the first cycle forks from.

        Initialises git if needed (see ``check_or_init_git``) and forks the first
        cycle from ``HEAD``. ``hillclimber.run`` has already refused to start on a
        dirty artefact (see ``check_uncommitted_changes``), so ``HEAD`` is the same
        committed state the baseline was scored on.

        Factored out as a seam so the loop can be tested without touching git.

        Args:
            config: The validated experiment config; ``config.path_to_artefact``
                locates the repo.

        Returns:
            The ref to use as the first cycle's ``parent_ref`` (``HEAD``).
        """
        await check_or_init_git(config.path_to_artefact)
        return "HEAD"

    @staticmethod
    def _summarize(run: Run, baseline: Score) -> RunSummary:
        """Flatten a completed ``run`` into a display ``RunSummary``.

        ``delta`` is the run's improvement over the baseline; an unscored run
        (no ``score_after``) reports a zero delta.
        """
        after = run.score_after
        delta = after.value - baseline.value if after is not None else 0.0
        return RunSummary(
            experiment_id=run.experiment_id,
            cycle_id=run.cycle_id,
            status=run.status,
            score_after=after,
            accepted=run.accepted,
            delta=delta,
        )

    @staticmethod
    def _score_value(summary: RunSummary) -> float:
        """The summary's comparable score; an unscored run ranks lowest."""
        return summary.score_after.value if summary.score_after is not None else float("-inf")
