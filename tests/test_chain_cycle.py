"""Tests for the chain cycle orchestration.

``one_cycle`` wires real mechanics (worktree, lock) around two harness calls
(``_propose_hypothesis`` / ``_apply_hypothesis``). These tests drive each seam
through a recording harness so the agent contract — what the worker is asked and
how its result is scored — is pinned without shelling out to a real CLI.
"""

import asyncio
import logging
import subprocess
from pathlib import Path

import pytest

from hillclimber.harnesses import ClaudeHarness, Harness, HarnessRun, TraceEvent, TraceSink
from hillclimber.lockfile import lock_path, read_events
from hillclimber.models import (
    Budget,
    Config,
    Cycle,
    CycleRecorded,
    CycleStatus,
    ExperimentFinished,
    ExperimentStarted,
    Goal,
    Score,
)
from hillclimber.progress import RunEvent, RunEventSink
from hillclimber.sandboxes import PassthroughSandbox
from hillclimber.strategies.base import CycleRecord
from hillclimber.strategies.chain import Chain


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
            "agents": {"orchestrator": agent, "worker": agent},
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
    # The toml set no prompt, so the strategy's role default reaches the harness.
    assert call.system_prompt == Chain.roles["orchestrator"].default_prompt


def test_propose_hypothesis_feeds_past_attempts_into_the_prompt():
    chain = Chain(PassthroughSandbox())
    fake = _RecordingHarness()
    chain.harness = fake
    # Seed the memory the way one_cycle does: two prior hypotheses, one that
    # helped and one that hurt.
    chain._cycle_records.append(CycleRecord(hypothesis="add caching", before=0.50, after=0.60))
    chain._cycle_records.append(CycleRecord(hypothesis="drop validation", before=0.60, after=0.55))

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
    assert call.system_prompt == Chain.roles["worker"].default_prompt
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
    assert events[0].label == "cycle 003/orchestrator"


def test_apply_hypothesis_forwards_labelled_traces_to_the_sink():
    events: list[TraceEvent] = []
    chain = Chain(PassthroughSandbox(), trace_sink=events.append)
    chain.harness = _TracingHarness()

    asyncio.run(chain._apply_hypothesis(_config(), _cycle(), "hc_a1b2_cycle_001"))

    assert [e.label for e in events] == ["cycle 001/worker"]


def test_default_trace_sink_logs_events(caplog):
    chain = Chain(PassthroughSandbox())
    chain.harness = _TracingHarness()

    with caplog.at_level(logging.INFO, logger="hillclimber.strategies.base"):
        asyncio.run(chain._propose_hypothesis(_config(), "hc_a1b2_cycle_001", index=1))

    # With no sink injected, traces surface as ordinary log lines.
    assert any("cycle 001/orchestrator" in message and "Read(pipeline.py)" in message for message in caplog.messages)


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


def _exec_config(cycles: int, target: float | None = None, *, path: str) -> Config:
    # ``path`` is required: execute writes ``.hillclimber/hillclimber.lock``
    # under the artefact path, so every loop test points it at its tmp_path.
    config = _config()
    config.path_to_artefact = path
    config.budget = Budget(cycles=cycles)
    config.goal = Goal(target=target)
    return config


def test_execute_runs_until_budget_exhausted(tmp_path: Path):
    chain = _StubChain([0.6, 0.7, 0.65])
    status = asyncio.run(chain.execute(_exec_config(cycles=3, path=str(tmp_path)), _baseline()))

    assert status.completed == 3
    assert status.total == 3
    assert len(status.cycles) == 3
    # best is the strongest cycle (cycle 2 at 0.7), not merely the last.
    assert status.best is not None
    assert status.best.cycle_id == "cyc_002"
    assert status.best.score_after is not None
    assert status.best.score_after.value == 0.7
    assert status.best.delta == pytest.approx(0.2)


def test_execute_chains_each_cycle_off_the_previous_branch(tmp_path: Path):
    chain = _StubChain([0.6, 0.7, 0.65])
    asyncio.run(chain.execute(_exec_config(cycles=3, path=str(tmp_path)), _baseline()))

    parents = [parent for _, _, parent in chain.cycles_run]
    # Cycle 1 forks from the baseline ref; each later cycle forks from the
    # previous cycle's branch — the chain.
    assert parents == ["baseline", "hc/x_cycle_001", "hc/x_cycle_002"]


def test_execute_does_not_chain_onto_a_failed_cycle(tmp_path: Path):
    parent_scores: list[float] = []

    class _BrokenSecond(_StubChain):
        """Cycle 2's hypothesis breaks the eval: 0.0, ``passed`` false, failed."""

        async def one_cycle(
            self,
            config: Config,
            experiment_id: str,
            index: int,
            parent_ref: str,
            parent_score: Score,
        ) -> Cycle:
            parent_scores.append(parent_score.value)
            cycle = await super().one_cycle(config, experiment_id, index, parent_ref, parent_score)
            if index == 2:
                cycle.score_after = Score(value=0.0, passed=False, scorer_id="command")
                cycle.status = CycleStatus.failed
            return cycle

    chain = _BrokenSecond([0.6, 0.0, 0.7])
    asyncio.run(chain.execute(_exec_config(cycles=3, path=str(tmp_path)), _baseline()))

    parents = [parent for _, _, parent in chain.cycles_run]
    # Cycle 3 forks from cycle 1's branch, not the broken cycle 2's — otherwise
    # merely un-breaking the eval would read as a huge win over its 0.0.
    assert parents == ["baseline", "hc/x_cycle_001", "hc/x_cycle_001"]
    # And the score to beat stays cycle 1's, not the failed cycle's 0.0.
    assert parent_scores == [0.5, 0.6, 0.6]


def test_execute_mints_one_experiment_id_and_numbers_cycles(tmp_path: Path):
    chain = _StubChain([0.6, 0.7])
    asyncio.run(chain.execute(_exec_config(cycles=2, path=str(tmp_path)), _baseline()))

    exp_ids = {exp_id for exp_id, _, _ in chain.cycles_run}
    assert len(exp_ids) == 1  # one id for the whole experiment
    assert next(iter(exp_ids)).startswith("exp_")
    assert [index for _, index, _ in chain.cycles_run] == [1, 2]  # 1-based, sequential


def test_execute_stops_early_when_goal_is_met(tmp_path: Path):
    chain = _StubChain([0.6, 0.75, 0.9])  # budget allows 5, but...
    status = asyncio.run(chain.execute(_exec_config(cycles=5, target=0.7, path=str(tmp_path)), _baseline()))

    # cycle 2 reaches 0.75 >= target 0.7, so the climb stops before the budget.
    assert status.completed == 2
    assert len(chain.cycles_run) == 2


def test_execute_runs_nothing_when_budget_is_zero(tmp_path: Path):
    chain = _StubChain([])
    status = asyncio.run(chain.execute(_exec_config(cycles=0, path=str(tmp_path)), _baseline()))

    assert status.completed == 0
    assert status.cycles == []
    assert status.best is None
    assert chain.cycles_run == []


def test_execute_runs_nothing_when_baseline_already_meets_goal(tmp_path: Path):
    chain = _StubChain([0.9])
    status = asyncio.run(chain.execute(_exec_config(cycles=3, target=0.5, path=str(tmp_path)), _baseline(0.5)))

    assert status.completed == 0
    assert chain.cycles_run == []


def test_execute_emits_cycle_start_and_done_events(tmp_path: Path):
    events: list[RunEvent] = []
    chain = _StubChain([0.6, 0.4], progress_sink=events.append)

    asyncio.run(chain.execute(_exec_config(cycles=2, path=str(tmp_path)), _baseline()))

    assert [e.kind for e in events] == ["cycle_start", "cycle_done", "cycle_start", "cycle_done"]
    starts = [e for e in events if e.kind == "cycle_start"]
    assert [(e.index, e.total) for e in starts] == [(1, 2), (2, 2)]
    done_1, done_2 = (e for e in events if e.kind == "cycle_done")
    # Cycle 1: 0.5 -> 0.6 against its parent (the baseline).
    assert done_1.score == pytest.approx(0.6)
    assert done_1.parent_delta == pytest.approx(0.1)
    assert done_1.hypothesis == "stub"
    # Cycle 2 chains off cycle 1, so its delta is measured against 0.6, not baseline.
    assert done_2.score == pytest.approx(0.4)
    assert done_2.parent_delta == pytest.approx(-0.2)


def test_execute_best_is_the_top_run_even_below_baseline(tmp_path: Path):
    chain = _StubChain([0.3, 0.4, 0.2])
    status = asyncio.run(chain.execute(_exec_config(cycles=3, path=str(tmp_path)), _baseline(0.5)))

    # No cycle beats the baseline, but best is still the strongest cycle (cycle 2).
    assert status.completed == 3
    assert status.best is not None
    assert status.best.cycle_id == "cyc_002"
    assert status.best.delta == pytest.approx(-0.1)


# --------------------------------------------------------------------------- #
# execute -> hillclimber.lock (the experiment log)
# --------------------------------------------------------------------------- #


def test_execute_appends_started_cycle_and_finished_events(tmp_path: Path):
    chain = _StubChain([0.6, 0.7])
    status = asyncio.run(chain.execute(_exec_config(cycles=2, path=str(tmp_path)), _baseline()))

    events = asyncio.run(read_events(lock_path(str(tmp_path))))
    started, first, second, finished = events

    assert isinstance(started, ExperimentStarted)
    assert started.experiment_id == status.experiment_id
    assert started.strategy == "chain"
    assert started.baseline_score.value == 0.5
    assert started.budget.cycles == 2

    assert isinstance(first, CycleRecorded)
    assert isinstance(second, CycleRecorded)
    # The promotion carries the full settled Cycle, scores included.
    assert first.cycle.index == 1
    assert first.cycle.score_after is not None
    assert first.cycle.score_after.value == 0.6
    assert second.cycle.score_after is not None
    assert second.cycle.score_after.value == 0.7

    assert isinstance(finished, ExperimentFinished)
    assert finished.experiment_id == status.experiment_id
    assert finished.outcome == "completed"
    assert finished.completed == 2
    assert finished.best_cycle_id == "cyc_002"


def test_execute_records_a_failed_finish_when_a_cycle_raises(tmp_path: Path):
    class _FailingChain(_StubChain):
        async def one_cycle(
            self,
            config: Config,
            experiment_id: str,
            index: int,
            parent_ref: str,
            parent_score: Score,
        ) -> Cycle:
            if index == 2:
                raise RuntimeError("scorer exploded")
            return await super().one_cycle(config, experiment_id, index, parent_ref, parent_score)

    chain = _FailingChain([0.6, 0.7])
    with pytest.raises(RuntimeError, match="scorer exploded"):
        asyncio.run(chain.execute(_exec_config(cycles=2, path=str(tmp_path)), _baseline()))

    events = asyncio.run(read_events(lock_path(str(tmp_path))))
    started, recorded, finished = events
    assert isinstance(started, ExperimentStarted)
    assert isinstance(recorded, CycleRecorded)
    assert recorded.cycle.index == 1
    # The terminal line still lands, marking the experiment failed — cycle 1's
    # settled result is preserved as the best so far.
    assert isinstance(finished, ExperimentFinished)
    assert finished.outcome == "failed"
    assert finished.completed == 1
    assert finished.best_cycle_id == "cyc_001"


def test_execute_appends_across_runs_and_never_truncates(tmp_path: Path):
    first = asyncio.run(_StubChain([0.6]).execute(_exec_config(cycles=1, path=str(tmp_path)), _baseline()))
    second = asyncio.run(_StubChain([0.7]).execute(_exec_config(cycles=1, path=str(tmp_path)), _baseline()))

    # Two experiments, one file: 2 x (started + cycle + finished), in order.
    events = asyncio.run(read_events(lock_path(str(tmp_path))))
    assert len(events) == 6
    started_ids = [e.experiment_id for e in events if isinstance(e, ExperimentStarted)]
    assert started_ids == [first.experiment_id, second.experiment_id]
    assert first.experiment_id != second.experiment_id


# --------------------------------------------------------------------------- #
# one_cycle against a real repo (lock kept out of the cycle commit)
# --------------------------------------------------------------------------- #


class _EditingHarness(_RecordingHarness):
    """A fake harness whose worker actually edits the artefact, like a real one."""

    async def run(self, harness_run: HarnessRun, on_trace: TraceSink | None = None) -> str:
        if harness_run.system_prompt == Chain.roles["worker"].default_prompt:
            (Path(harness_run.path) / "improvement.txt").write_text("better\n")
        return await super().run(harness_run, on_trace)


_EVAL_CMD = """echo '{"hillclimber_eval": 1, "score": 0.7}'"""


def _git_repo(path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=path, check=True, capture_output=True
        )

    git("init")
    (path / "a.txt").write_text("x\n")
    git("add", ".")
    git("commit", "-m", "init")


def _tracked_files(repo: Path, ref: str = "HEAD") -> set[str]:
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", ref], cwd=repo, capture_output=True, text=True)
    return set(out.stdout.split())


# The cycle's worktree/branch slug — the experiment's full 8-hex id (see
# ``Chain.one_cycle``), which keeps branch names from colliding across runs.
_CYCLE_WORKTREE = "hc_a1b2c3d4_cycle_001"
_CYCLE_BRANCH = "hc/a1b2c3d4_cycle_001"


def _one_cycle(chain: Chain, tmp_path: Path) -> Cycle:
    config = _exec_config(cycles=1, path=str(tmp_path))
    config.scorer.cmd = _EVAL_CMD
    return asyncio.run(chain.one_cycle(config, "exp_a1b2c3d4", index=1, parent_ref="HEAD", parent_score=_baseline()))


def test_one_cycle_keeps_the_lock_out_of_the_commit(tmp_path: Path):
    _git_repo(tmp_path)
    chain = Chain(PassthroughSandbox())
    chain.harness = _EditingHarness()

    cycle = _one_cycle(chain, tmp_path)

    # The checkout is torn down after scoring; the branch keeps the commit, so the
    # change lives on and no full worktree accumulates.
    assert not (tmp_path / ".hillclimber" / _CYCLE_WORKTREE).exists()
    tracked = _tracked_files(tmp_path, _CYCLE_BRANCH)
    # The commit carries the worker's change but not the cycle's lock.
    assert "improvement.txt" in tracked
    assert "cyc_001.lock" not in tracked
    # The settled state is on the returned cycle (its on-disk lock went with the
    # worktree, but it was already promoted into hillclimber.lock by execute).
    assert cycle.status == CycleStatus.scored
    assert cycle.score_after is not None
    assert cycle.score_after.value == 0.7
    assert cycle.commit_sha is not None


def test_one_cycle_tolerates_a_noop_worker(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    _git_repo(tmp_path)
    chain = Chain(PassthroughSandbox())
    chain.harness = _RecordingHarness()  # edits nothing — only the lock is dirty

    with caplog.at_level(logging.WARNING, logger="hillclimber.strategies.base"):
        cycle = _one_cycle(chain, tmp_path)

    # Exclusion left nothing to commit: no crash, and the fork point is kept as
    # the cycle's commit, flagged as a no-op. The worktree is still torn down.
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True)
    assert cycle.commit_sha == base.stdout.strip()
    assert not (tmp_path / ".hillclimber" / _CYCLE_WORKTREE).exists()
    assert "cyc_001.lock" not in _tracked_files(tmp_path, _CYCLE_BRANCH)
    assert any("no new commit" in message for message in caplog.messages)


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
