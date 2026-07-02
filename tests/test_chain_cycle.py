"""Tests for the chain cycle orchestration.

``one_cycle`` wires real mechanics (worktree, lock) around two harness calls
(``_propose_hypothesis`` / ``_apply_hypothesis``). These tests drive each seam
through a recording harness so the agent contract — what the worker is asked and
how its result is scored — is pinned without shelling out to a real CLI.
"""

import asyncio
import logging
from pathlib import Path

import pytest

import hillclimber  # noqa: F401  (initialise package before importing strategies)
from harnesses import ClaudeHarness, Harness, TraceEvent, TraceSink
from harnesses.claude import HarnessRun
from hillclimber.models import Budget, Config, Cycle, CycleStatus, Goal, Score
from hillclimber.progress import RunEvent, RunEventSink
from sandboxes import PassthroughSandbox
from strategies.base import CycleRecord
from strategies.chain import Chain


class _RecordingHarness(Harness):
    """A fake harness that records its calls and returns a canned hypothesis."""

    def __init__(self) -> None:
        self.calls: list[HarnessRun] = []

    async def run(self, harness_run: HarnessRun, on_trace: TraceSink | None = None) -> str:
        self.calls.append(harness_run)
        return "use a regex instead of str.split()"

    async def verify_model(self, model: str) -> None:
        # These tests drive the harness directly, never the preflight; a no-op
        # satisfies the abstract contract.
        return None


class _TracingHarness(_RecordingHarness):
    """A fake harness that emits one trace event per run, like a real one."""

    async def run(self, harness_run: HarnessRun, on_trace: TraceSink | None = None) -> str:
        if on_trace is not None:
            on_trace(TraceEvent(kind="tool_use", summary="Read(pipeline.py)", raw={}))
        return await super().run(harness_run, on_trace)


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


def _cycle() -> Cycle:
    return Cycle(
        experiment_id="exp_a1b2c3d4",
        index=1,
        parent_ref="baseline",
        branch="hc/a1b2_cycle_001",
        worktree="hc_a1b2_cycle_001",
        hypothesis="try X",
        score_before=Score(value=0.5, passed=True, scorer_id="command"),
        status=CycleStatus.running,
    )


def test_chain_uses_the_claude_harness_by_default():
    assert isinstance(Chain(PassthroughSandbox()).harness, ClaudeHarness)


def test_propose_hypothesis_runs_the_agent_through_the_harness():
    chain = Chain(PassthroughSandbox())
    fake = _RecordingHarness()
    chain.harness = fake

    hypothesis = asyncio.run(chain._propose_hypothesis(_config(), "hc_a1b2_cycle_001", index=1))

    assert hypothesis == "use a regex instead of str.split()"
    # The agent's config drives the call: its worktree, model, and (filled) prompt.
    call = fake.calls[0]
    assert call.path == "hc_a1b2_cycle_001"
    assert call.model == "claude-opus-4-8"
    assert call.system_prompt  # the role default, filled in by Config


def test_propose_hypothesis_feeds_past_attempts_into_the_prompt():
    chain = Chain(PassthroughSandbox())
    fake = _RecordingHarness()
    chain.harness = fake
    # Seed the memory the way one_cycle does: two prior hypotheses, one that
    # helped and one that hurt.
    chain._cycle_records().append(CycleRecord(hypothesis="add caching", before=0.50, after=0.60))
    chain._cycle_records().append(CycleRecord(hypothesis="drop validation", before=0.60, after=0.55))

    asyncio.run(chain._propose_hypothesis(_config(), "hc_a1b2_cycle_001", index=1))

    prompt = fake.calls[0].prompt
    # Both past hypotheses and their score movement are surfaced to the proposer.
    assert "add caching" in prompt
    assert "raised the score 0.500 -> 0.600 (+0.100)" in prompt
    assert "drop validation" in prompt
    assert "lowered the score 0.600 -> 0.550 (-0.050)" in prompt
    assert "Do not repeat" in prompt


def test_propose_hypothesis_omits_history_on_the_first_cycle():
    chain = Chain(PassthroughSandbox())
    fake = _RecordingHarness()
    chain.harness = fake

    asyncio.run(chain._propose_hypothesis(_config(), "hc_a1b2_cycle_001", index=1))

    # No attempts yet -> no "we already tried" block, just the bare task.
    assert "already tried" not in fake.calls[0].prompt


def test_apply_hypothesis_runs_the_worker_and_asks_for_a_commit():
    chain = Chain(PassthroughSandbox())
    fake = _RecordingHarness()
    chain.harness = fake
    cycle = _cycle()

    result = asyncio.run(chain._apply_hypothesis(_config(), cycle, "hc_a1b2_cycle_001"))

    # _apply_hypothesis only drives the worker — scoring is read from the commit
    # by one_cycle, so it returns nothing.
    assert result is None
    # The worker's config drives the call: worktree, model, (filled) prompt with
    # the hypothesis verbatim and an explicit instruction to commit.
    call = fake.calls[0]
    assert call.path == "hc_a1b2_cycle_001"
    assert call.model == "claude-opus-4-8"
    assert call.system_prompt  # the worker role default, filled in by Config
    assert cycle.hypothesis in call.prompt
    assert "commit" in call.prompt.lower()


# --------------------------------------------------------------------------- #
# trace events (harness -> labelled sink)
# --------------------------------------------------------------------------- #


def test_propose_hypothesis_forwards_labelled_traces_to_the_sink():
    events: list[TraceEvent] = []
    chain = Chain(PassthroughSandbox(), trace_sink=events.append)
    chain.harness = _TracingHarness()

    asyncio.run(chain._propose_hypothesis(_config(), "hc_a1b2_cycle_001", index=3))

    # The harness emitted an anonymous event; the strategy stamped who ran.
    assert len(events) == 1
    assert events[0].kind == "tool_use"
    assert events[0].summary == "Read(pipeline.py)"
    assert events[0].label == "cycle 003/hillclimber"


def test_apply_hypothesis_forwards_labelled_traces_to_the_sink():
    events: list[TraceEvent] = []
    chain = Chain(PassthroughSandbox(), trace_sink=events.append)
    chain.harness = _TracingHarness()

    asyncio.run(chain._apply_hypothesis(_config(), _cycle(), "hc_a1b2_cycle_001"))

    assert [e.label for e in events] == ["cycle 001/worker"]


def test_default_trace_sink_logs_events(caplog):
    chain = Chain(PassthroughSandbox())
    chain.harness = _TracingHarness()

    with caplog.at_level(logging.INFO, logger="strategies.base"):
        asyncio.run(chain._propose_hypothesis(_config(), "hc_a1b2_cycle_001", index=1))

    # With no sink injected, traces surface as ordinary log lines.
    assert any("cycle 001/hillclimber" in message and "Read(pipeline.py)" in message for message in caplog.messages)


# --------------------------------------------------------------------------- #
# progress events (strategy -> run-level sink)
# --------------------------------------------------------------------------- #


def test_propose_hypothesis_emits_the_proposing_stage():
    events: list[RunEvent] = []
    chain = Chain(PassthroughSandbox(), progress_sink=events.append)
    chain.harness = _RecordingHarness()

    asyncio.run(chain._propose_hypothesis(_config(), "hc_a1b2_cycle_001", index=1))

    assert [(e.kind, e.stage) for e in events] == [("cycle_stage", "proposing")]
    assert events[0].index == 1
    assert events[0].total == 1  # the budget's cycle count


def test_apply_hypothesis_emits_the_applying_stage_with_the_hypothesis():
    events: list[RunEvent] = []
    chain = Chain(PassthroughSandbox(), progress_sink=events.append)
    chain.harness = _RecordingHarness()

    asyncio.run(chain._apply_hypothesis(_config(), _cycle(), "hc_a1b2_cycle_001"))

    assert [(e.kind, e.stage) for e in events] == [("cycle_stage", "applying")]
    # The applying event carries the hypothesis so a consumer can show what the
    # worker is about to do.
    assert events[0].hypothesis == "try X"


# --------------------------------------------------------------------------- #
# execute (the cycle loop)
# --------------------------------------------------------------------------- #


class _StubChain(Chain):
    """A ``Chain`` whose ``one_cycle`` returns canned scores instead of touching
    git or the harness, so the loop itself can be tested in isolation."""

    def __init__(self, scores: list[float], progress_sink: RunEventSink | None = None) -> None:
        super().__init__(PassthroughSandbox(), progress_sink=progress_sink)
        self._scores = scores
        self.cycles_run: list[tuple[str, int, str]] = []

    async def _prepare_repo(self, config: Config) -> str:
        # Skip git entirely; the first cycle "forks" from this sentinel ref.
        return "baseline"

    async def one_cycle(
        self,
        config: Config,
        experiment_id: str,
        index: int,
        parent_ref: str,
        parent_score: Score,
    ) -> Cycle:
        self.cycles_run.append((experiment_id, index, parent_ref))
        value = self._scores[index - 1]
        return Cycle(
            experiment_id=experiment_id,
            index=index,
            parent_ref=parent_ref,
            branch=f"hc/x_cycle_{index:03d}",
            worktree=f"hc_x_cycle_{index:03d}",
            hypothesis="stub",
            score_before=parent_score,
            score_after=Score(value=value, passed=True, scorer_id="command"),
            status=CycleStatus.scored,
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
    assert len(status.cycles) == 3
    # best is the strongest cycle (cycle 2 at 0.7), not merely the last.
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
    assert [index for _, index, _ in chain.cycles_run] == [1, 2]  # 1-based, sequential


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
    assert status.cycles == []
    assert status.best is None
    assert chain.cycles_run == []


def test_execute_runs_nothing_when_baseline_already_meets_goal():
    chain = _StubChain([0.9])
    status = asyncio.run(chain.execute(_exec_config(cycles=3, target=0.5), _baseline(0.5)))

    assert status.completed == 0
    assert chain.cycles_run == []


def test_execute_emits_cycle_start_and_done_events():
    events: list[RunEvent] = []
    chain = _StubChain([0.6, 0.4], progress_sink=events.append)

    asyncio.run(chain.execute(_exec_config(cycles=2), _baseline()))

    assert [e.kind for e in events] == ["cycle_start", "cycle_done", "cycle_start", "cycle_done"]
    starts = [e for e in events if e.kind == "cycle_start"]
    assert [(e.index, e.total) for e in starts] == [(1, 2), (2, 2)]
    done_1, done_2 = (e for e in events if e.kind == "cycle_done")
    # Cycle 1: 0.5 -> 0.6 against its parent (the baseline).
    assert done_1.score == pytest.approx(0.6)
    assert done_1.delta == pytest.approx(0.1)
    assert done_1.hypothesis == "stub"
    # Cycle 2 chains off cycle 1, so its delta is measured against 0.6, not baseline.
    assert done_2.score == pytest.approx(0.4)
    assert done_2.delta == pytest.approx(-0.2)


def test_execute_best_is_the_top_run_even_below_baseline():
    chain = _StubChain([0.3, 0.4, 0.2])
    status = asyncio.run(chain.execute(_exec_config(cycles=3), _baseline(0.5)))

    # No cycle beats the baseline, but best is still the strongest cycle (cycle 2).
    assert status.completed == 3
    assert status.best is not None
    assert status.best.cycle_id == "cyc_002"
    assert status.best.delta == pytest.approx(-0.1)


# --------------------------------------------------------------------------- #
# _prepare_repo (start ref resolution)
# --------------------------------------------------------------------------- #


def test_prepare_repo_resolves_the_start_ref(tmp_path: Path):
    chain = Chain(PassthroughSandbox())
    config = _config()
    config.path_to_artefact = str(tmp_path)

    # Default: the first cycle forks from HEAD.
    assert asyncio.run(chain._prepare_repo(config)) == "HEAD"

    # An explicit start_branch is used verbatim as the fork ref.
    config.start_branch = "main"
    assert asyncio.run(chain._prepare_repo(config)) == "main"
