"""Tests for the chain cycle orchestration.

``one_cycle`` wires real mechanics (worktree, lock) around two harness calls
(``_propose_hypothesis`` / ``_apply_hypothesis``). These tests drive each seam
through a recording harness so the agent contract — what the worker is asked and
how its result is scored — is pinned without shelling out to a real CLI.
"""

import asyncio

import pytest

import hillclimber  # noqa: F401  (initialise package before importing strategies)
from harnesses import ClaudeHarness, Harness
from harnesses.claude import HarnessRun
from hillclimber.models import Budget, Config, Goal, Run, RunStatus, Score
from sandboxes import PassthroughSandbox
from strategies.chain import Chain


class _RecordingHarness(Harness):
    """A fake harness that records its calls and returns a canned hypothesis."""

    def __init__(self) -> None:
        self.calls: list[HarnessRun] = []

    async def run(self, harness_run: HarnessRun) -> str:
        self.calls.append(harness_run)
        return "use a regex instead of str.split()"


def _config() -> Config:
    agent = {"harness": "claude", "model": "claude-opus-4-8"}
    # model_validate (not the constructor) so the nested dicts validate into their
    # models without tripping the static type checker on dict-vs-model arguments.
    return Config.model_validate(
        {
            "path_to_artefact": ".",
            "scorer": {"kind": "command", "cmd": "true"},
            "budget": {"cycles": 1},
            "hillclimber_agent": agent,
            "worker_agent": agent,
            "reflector_agent": agent,
        }
    )


def _run() -> Run:
    return Run(
        experiment_id="exp_a1b2c3d4",
        cycle=1,
        parent_ref="baseline",
        branch="hc/a1b2_cycle_001",
        worktree="hc_a1b2_cycle_001",
        hypothesis="try X",
        score_before=Score(value=0.5, passed=True, scorer_id="command"),
        status=RunStatus.running,
    )


def test_chain_uses_the_claude_harness_by_default():
    assert isinstance(Chain(PassthroughSandbox()).harness, ClaudeHarness)


def test_propose_hypothesis_runs_the_agent_through_the_harness():
    chain = Chain(PassthroughSandbox())
    fake = _RecordingHarness()
    chain.harness = fake

    hypothesis = asyncio.run(chain._propose_hypothesis(_config(), "hc_a1b2_cycle_001"))

    assert hypothesis == "use a regex instead of str.split()"
    # The agent's config drives the call: its worktree, model, and (filled) prompt.
    call = fake.calls[0]
    assert call.path == "hc_a1b2_cycle_001"
    assert call.model == "claude-opus-4-8"
    assert call.system_prompt  # the role default, filled in by Config


def test_apply_hypothesis_runs_the_worker_and_asks_for_a_commit():
    chain = Chain(PassthroughSandbox())
    fake = _RecordingHarness()
    chain.harness = fake
    run = _run()

    result = asyncio.run(chain._apply_hypothesis(_config(), run, "hc_a1b2_cycle_001"))

    # _apply_hypothesis only drives the worker — scoring is read from the commit
    # by one_cycle, so it returns nothing.
    assert result is None
    # The worker's config drives the call: worktree, model, (filled) prompt with
    # the hypothesis verbatim and an explicit instruction to commit.
    call = fake.calls[0]
    assert call.path == "hc_a1b2_cycle_001"
    assert call.model == "claude-opus-4-8"
    assert call.system_prompt  # the worker role default, filled in by Config
    assert run.hypothesis in call.prompt
    assert "commit" in call.prompt.lower()


# --------------------------------------------------------------------------- #
# execute (the cycle loop)
# --------------------------------------------------------------------------- #


class _StubChain(Chain):
    """A ``Chain`` whose ``one_cycle`` returns canned scores instead of touching
    git or the harness, so the loop itself can be tested in isolation."""

    def __init__(self, scores: list[float]) -> None:
        super().__init__(PassthroughSandbox())
        self._scores = scores
        self.cycles_run: list[tuple[str, int, str]] = []

    async def _prepare_repo(self, config: Config) -> str:
        # Skip git entirely; the first cycle "forks" from this sentinel ref.
        return "baseline"

    async def one_cycle(
        self,
        config: Config,
        experiment_id: str,
        cycle: int,
        parent_ref: str,
        parent_score: Score,
    ) -> Run:
        self.cycles_run.append((experiment_id, cycle, parent_ref))
        value = self._scores[cycle - 1]
        return Run(
            experiment_id=experiment_id,
            cycle=cycle,
            parent_ref=parent_ref,
            branch=f"hc/x_cycle_{cycle:03d}",
            worktree=f"hc_x_cycle_{cycle:03d}",
            hypothesis="stub",
            score_before=parent_score,
            score_after=Score(value=value, passed=True, scorer_id="command"),
            status=RunStatus.scored,
        )


def _baseline(value: float = 0.5) -> Score:
    return Score(value=value, passed=True, scorer_id="command")


def _exec_config(cycles: int, target: float | None = None) -> Config:
    config = _config()
    config.budget = Budget(cycles=cycles)
    config.goal = Goal(target=target)
    return config


def test_execute_runs_until_budget_exhausted():
    chain = _StubChain([0.6, 0.7, 0.65])
    status = asyncio.run(chain.execute(_exec_config(cycles=3), _baseline()))

    assert status.completed == 3
    assert status.total == 3
    assert len(status.runs) == 3
    # best is the strongest run (cycle 2 at 0.7), not merely the last.
    assert status.best is not None
    assert status.best.cycle_id == "cyc_002"
    assert status.best.score_after is not None
    assert status.best.score_after.value == 0.7
    assert status.best.delta == pytest.approx(0.2)


def test_execute_chains_each_cycle_off_the_previous_branch():
    chain = _StubChain([0.6, 0.7, 0.65])
    asyncio.run(chain.execute(_exec_config(cycles=3), _baseline()))

    parents = [parent for _, _, parent in chain.cycles_run]
    # Cycle 1 forks from the baseline ref; each later cycle forks from the
    # previous cycle's branch — the chain.
    assert parents == ["baseline", "hc/x_cycle_001", "hc/x_cycle_002"]


def test_execute_mints_one_experiment_id_and_numbers_cycles():
    chain = _StubChain([0.6, 0.7])
    asyncio.run(chain.execute(_exec_config(cycles=2), _baseline()))

    exp_ids = {exp_id for exp_id, _, _ in chain.cycles_run}
    assert len(exp_ids) == 1  # one id for the whole experiment
    assert next(iter(exp_ids)).startswith("exp_")
    assert [cycle for _, cycle, _ in chain.cycles_run] == [1, 2]  # 1-based, sequential


def test_execute_stops_early_when_goal_is_met():
    chain = _StubChain([0.6, 0.75, 0.9])  # budget allows 5, but...
    status = asyncio.run(chain.execute(_exec_config(cycles=5, target=0.7), _baseline()))

    # cycle 2 reaches 0.75 >= target 0.7, so the climb stops before the budget.
    assert status.completed == 2
    assert len(chain.cycles_run) == 2


def test_execute_runs_nothing_when_budget_is_zero():
    chain = _StubChain([])
    status = asyncio.run(chain.execute(_exec_config(cycles=0), _baseline()))

    assert status.completed == 0
    assert status.runs == []
    assert status.best is None
    assert chain.cycles_run == []


def test_execute_runs_nothing_when_baseline_already_meets_goal():
    chain = _StubChain([0.9])
    status = asyncio.run(chain.execute(_exec_config(cycles=3, target=0.5), _baseline(0.5)))

    assert status.completed == 0
    assert chain.cycles_run == []


def test_execute_best_is_the_top_run_even_below_baseline():
    chain = _StubChain([0.3, 0.4, 0.2])
    status = asyncio.run(chain.execute(_exec_config(cycles=3), _baseline(0.5)))

    # No run beats the baseline, but best is still the strongest run (cycle 2).
    assert status.completed == 3
    assert status.best is not None
    assert status.best.cycle_id == "cyc_002"
    assert status.best.delta == pytest.approx(-0.1)
