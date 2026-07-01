"""The ``chain`` strategy.

Chains cycles one after another: each run is attempted in sequence and folded
into the running ``ExperimentStatus``. v1 is the thinnest slice — it establishes
the baseline status that later cycles will accumulate into (see README "Core
loop" / "Architecture seam"). The per-cycle mutation loop attaches here.
"""

from __future__ import annotations

from harnesses.claude import HarnessRun
from hillclimber.git_utils import check_or_init_git, create_worktree, head_sha
from hillclimber.models import Config, Cycle, CycleStatus, CycleSummary, ExperimentStatus, Score
from hillclimber.scoring import score_artefact
from hillclimber.telemetry import get_logger
from strategies.base import CycleRecord, Strategy

logger = get_logger(__name__)


class Chain(Strategy):
    """Run cycles in sequence, recording the best so far."""

    async def one_cycle(
        self,
        config: Config,
        experiment_id: str,
        index: int,
        parent_ref: str,
        parent_score: Score,
    ) -> Cycle:
        """Run a single climb cycle: propose -> apply -> record.

        One cycle is one hypothesis iteration in its own worktree (see ``Cycle``):
        branch off the parent, ask the hillclimber agent for a hypothesis, record
        it in a ``.lock``, let the worker agent apply *and commit* it in a fresh
        context, verify the commit, then score that committed result and fold it
        back into the lock.

        The score is read from the cycle's own commit, not the artefact's ``HEAD``:
        the worktree is the cycle's checkout, the worker commits its change there,
        and the next cycle forks from *this* branch (see ``execute``) — so the climb
        chains, each cycle building on the last.

        Args:
            config: The validated experiment config.
            experiment_id: The owning experiment's id (``exp_...``); its first 4
                hex chars seed this cycle's worktree/branch names.
            index: The 1-based cycle number within the experiment.
            parent_ref: The git ref this cycle forks from — the baseline snapshot
                for cycle 1, the previous cycle's branch thereafter.
            parent_score: The score to beat — the parent's score, recorded as
                ``score_before``.

        Returns:
            The completed ``Cycle``.
        """
        # 1. Name and branch a fresh worktree off the parent. The worktree/branch
        #    are scoped per (experiment, cycle): hc_<XXXX>_cycle_<NNN>, where XXXX
        #    is the experiment's uuid prefix and NNN the zero-padded cycle number.
        slug = f"{experiment_id.removeprefix('exp_')[:4]}_cycle_{index:03d}"
        worktree_name = f"hc_{slug}"
        branch = f"hc/{slug}"
        logger.info("cycle %d: worktree %s off %s", index, worktree_name, parent_ref)
        worktree = await create_worktree(config.path_to_artefact, worktree_name, branch, parent_ref)
        # The commit the branch forks from — used to tell whether the worker
        # actually produced a new commit.
        base_sha = await head_sha(worktree)

        # 2. Ask the hillclimber agent for one hypothesis. [HARNESS SEAM]
        hypothesis = await self._propose_hypothesis(config, worktree)
        logger.debug("cycle %d: hypothesis: %s", index, hypothesis)

        # 3. Record the running cycle in its lock: hypothesis, parent, score_before.
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

        # 4. Worker applies the hypothesis and commits it in a fresh context.
        #    [HARNESS SEAM]
        await self._apply_hypothesis(config, cycle, worktree)

        # 5. Verify the worker committed (backstop a forgotten commit), and record
        #    the commit the score is read from.
        cycle.commit_sha = await self._commit_cycle(worktree, base_sha, index)

        # 6. Score this cycle's committed result.
        cycle.score_after = await score_artefact(config.scorer, worktree)
        cycle.status = CycleStatus.scored
        logger.info(
            "cycle %d: scored %.3f (was %.3f)",
            index,
            cycle.score_after.value,
            parent_score.value,
        )

        # 7. Remember this cycle so the next cycle's proposer can build on it, then
        #    fold the result back into the lock and hand the cycle back.
        self._cycle_records().append(
            CycleRecord(hypothesis=hypothesis, before=parent_score.value, after=cycle.score_after.value)
        )
        await self.write_lock(worktree, cycle)
        return cycle

    async def _propose_hypothesis(self, config: Config, worktree: str) -> str:
        """Ask the hillclimber agent for one hypothesis, via the harness.

        Drives ``config.hillclimber_agent`` (its model and system prompt) against
        the artefact checkout in ``worktree`` through ``self.harness`` and returns
        the proposed change as text. The prompt is primed with the experiment's
        past cycles (see ``_cycle_records`` / ``_render_history``) so each
        hypothesis builds on what earlier cycles already learned rather than
        restating it. v1 always routes through the Claude harness (see
        ``Strategy.__init__``); selecting the harness per ``Agent.harness`` is a
        later refinement.

        Args:
            config: The validated experiment config.
            worktree: The cycle's checkout the agent inspects and reasons over.

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
            "eval score.\n\n"
            f"{self._render_history(self._cycle_records())}"
            "Reply with only the hypothesis as one short paragraph."
        )
        return await self.harness.run(
            HarnessRun(
                system_prompt=agent.system_prompt,
                prompt=task,
                path=worktree,
                model=agent.model,
            )
        )

    async def _apply_hypothesis(self, config: Config, cycle: Cycle, worktree: str) -> None:
        """Run the worker agent to apply ``cycle.hypothesis`` and commit it.

        Drives ``config.worker_agent`` (its model and system prompt) in a fresh
        context through ``self.harness`` to apply ``cycle.hypothesis`` inside
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
            cycle: The running cycle; ``cycle.hypothesis`` is the change to apply.
            worktree: The cycle's checkout the worker edits and commits in.
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
            f"{cycle.hypothesis}"
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
        cycle's result is folded into the running ``best``/``cycles`` view.

        Args:
            config: The validated experiment config.
            baseline: The baseline ``Score`` each cycle must beat.

        Returns:
            The final ``ExperimentStatus`` — every cycle attempted and the best so far.
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

        cycles: list[CycleSummary] = []
        best: CycleSummary | None = None
        # The strongest score yet, baseline included — what the goal is checked
        # against. ``best`` (the best *cycle*) can sit below it if no cycle beat
        # baseline.
        peak_score = baseline
        completed = 0

        while not config.goal.is_met(peak_score) and not config.budget.is_exhausted(completed):
            cycle = await self.one_cycle(
                config,
                experiment_id,
                index=completed + 1,
                parent_ref=parent_ref,
                parent_score=parent_score,
            )
            completed += 1

            summary = self._summarize(cycle, baseline)
            cycles.append(summary)

            after = cycle.score_after
            if after is not None:
                if best is None or after.value > self._score_value(best):
                    best = summary
                if after.value > peak_score.value:
                    peak_score = after

            # Chain: the next cycle forks from this cycle's branch and aims to beat
            # its score (carried forward even when it dipped below the parent).
            parent_ref = cycle.branch
            if after is not None:
                parent_score = after

        return ExperimentStatus(
            baseline_score=baseline,
            cycles=cycles,
            best=best,
            completed=completed,
            total=config.budget.cycles,
        )

    async def _prepare_repo(self, config: Config) -> str:
        """Ready the artefact repo and return the ref the first cycle forks from.

        Initialises git if needed (see ``check_or_init_git``) and resolves the
        start ref: ``config.start_branch`` when set, else the artefact's current
        ``HEAD``. ``get_baseline_score`` scores the baseline at this same ref, so
        cycle 1 and the baseline measure the same code. For the ``HEAD`` default,
        ``hillclimber.run`` has already refused to start on a dirty artefact (see
        ``check_uncommitted_changes``), so ``HEAD`` is the committed state the
        baseline was scored on.

        Factored out as a seam so the loop can be tested without touching git.

        Args:
            config: The validated experiment config; ``config.path_to_artefact``
                locates the repo and ``config.start_branch`` names the start ref.

        Returns:
            The ref to use as the first cycle's ``parent_ref``.
        """
        await check_or_init_git(config.path_to_artefact)
        return config.start_branch or "HEAD"

    @staticmethod
    def _summarize(cycle: Cycle, baseline: Score) -> CycleSummary:
        """Flatten a completed ``cycle`` into a display ``CycleSummary``.

        ``delta`` is the cycle's improvement over the baseline; an unscored cycle
        (no ``score_after``) reports a zero delta.
        """
        after = cycle.score_after
        delta = after.value - baseline.value if after is not None else 0.0
        return CycleSummary(
            experiment_id=cycle.experiment_id,
            cycle_id=cycle.cycle_id,
            status=cycle.status,
            score_after=after,
            accepted=cycle.accepted,
            delta=delta,
        )

    @staticmethod
    def _score_value(summary: CycleSummary) -> float:
        """The summary's comparable score; an unscored cycle ranks lowest."""
        return summary.score_after.value if summary.score_after is not None else float("-inf")
