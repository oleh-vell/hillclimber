"""Tests for the experiment lock file (``hillclimber.lock``).

The lock is an append-only JSONL event log — the artefact's durable climb
history (see ``hillclimber.lockfile``). These tests pin the append/read
round-trip, the crash-tolerance of the reader, and the fold that reassembles
``ExperimentStatus`` from the events.
"""

import asyncio
import logging
import subprocess
from pathlib import Path

import pytest

import hillclimber  # noqa: F401  (initialise package before importing submodules)
from hillclimber import lockfile
from hillclimber.lockfile import (
    ExperimentLog,
    append_event,
    fold_statuses,
    load_statuses,
    lock_path,
    read_events,
    reset_history,
)
from hillclimber.models import (
    Budget,
    Cycle,
    CycleRecorded,
    CycleStatus,
    ExperimentFinished,
    ExperimentStarted,
    Score,
)


def _score(value: float = 0.5) -> Score:
    return Score(value=value, passed=True, scorer_id="command")


def _cycle(index: int = 1, after: float | None = 0.7, experiment_id: str = "exp_a1b2c3d4") -> Cycle:
    return Cycle(
        experiment_id=experiment_id,
        index=index,
        parent_ref="baseline",
        branch=f"hc/a1b2_cycle_{index:03d}",
        worktree=f"hc_a1b2_cycle_{index:03d}",
        hypothesis="try X",
        score_before=_score(),
        score_after=_score(after) if after is not None else None,
        status=CycleStatus.scored if after is not None else CycleStatus.failed,
    )


def _started(experiment_id: str = "exp_a1b2c3d4", baseline: float = 0.5, cycles: int = 3) -> ExperimentStarted:
    return ExperimentStarted(
        experiment_id=experiment_id,
        strategy="chain",
        baseline_score=_score(baseline),
        budget=Budget(cycles=cycles),
    )


def _finished(experiment_id: str = "exp_a1b2c3d4", outcome: str = "completed") -> ExperimentFinished:
    return ExperimentFinished.model_validate(
        {"experiment_id": experiment_id, "outcome": outcome, "completed": 1, "best_cycle_id": "cyc_001"}
    )


# --------------------------------------------------------------------------- #
# lock_path
# --------------------------------------------------------------------------- #


def test_lock_path_for_a_directory_artefact(tmp_path: Path):
    assert lock_path(str(tmp_path)) == tmp_path / ".hillclimber" / "hillclimber.lock"


def test_lock_path_for_a_file_artefact(tmp_path: Path):
    artefact = tmp_path / "solo.py"
    artefact.write_text("pass\n")
    assert lock_path(str(artefact)) == tmp_path / ".hillclimber" / "hillclimber.lock"


# --------------------------------------------------------------------------- #
# append_event / read_events
# --------------------------------------------------------------------------- #


def test_append_creates_parent_dirs_and_one_line_per_event(tmp_path: Path):
    path = tmp_path / ".hillclimber" / "hillclimber.lock"

    asyncio.run(append_event(path, _started()))
    asyncio.run(append_event(path, CycleRecorded(cycle=_cycle())))

    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert all(line.startswith("{") and line.endswith("}") for line in lines)


def test_events_round_trip_through_the_log(tmp_path: Path):
    path = tmp_path / "hillclimber.lock"
    log = ExperimentLog(path, "exp_a1b2c3d4")

    async def record_all() -> None:
        await log.record_started(strategy="chain", baseline=_score(), budget=Budget(cycles=3))
        await log.record_cycle(_cycle())
        await log.record_finished(outcome="completed", completed=1, best_cycle_id="cyc_001")

    asyncio.run(record_all())
    events = asyncio.run(read_events(path))

    started, recorded, finished = events
    assert isinstance(started, ExperimentStarted)
    assert started.experiment_id == "exp_a1b2c3d4"
    assert started.strategy == "chain"
    assert started.budget.cycles == 3
    assert isinstance(recorded, CycleRecorded)
    assert recorded.cycle == _cycle()  # the full Cycle round-trips verbatim
    assert isinstance(finished, ExperimentFinished)
    assert finished.outcome == "completed"
    assert finished.best_cycle_id == "cyc_001"
    # Timestamps survive serialization and are ordered like the writes.
    assert started.timestamp <= recorded.timestamp <= finished.timestamp


def test_read_events_missing_file_is_empty_history(tmp_path: Path):
    assert asyncio.run(read_events(tmp_path / "hillclimber.lock")) == []


def test_read_events_skips_an_unparseable_trailing_line(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    path = tmp_path / "hillclimber.lock"
    asyncio.run(append_event(path, _started()))
    # A crash mid-append leaves a truncated final line; the reader tolerates it.
    with path.open("a") as fh:
        fh.write('{"event": "cycle_reco')

    with caplog.at_level(logging.WARNING, logger="hillclimber.lockfile"):
        events = asyncio.run(read_events(path))

    assert len(events) == 1
    assert any("line 2" in message for message in caplog.messages)


def test_read_events_skips_a_corrupt_interior_line(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    # A torn write is no longer at the tail once later events land on top of it;
    # an interior bad line must be skipped (with a warning), not wedge every read
    # of the whole history. The good records around it still come back.
    path = tmp_path / "hillclimber.lock"
    asyncio.run(append_event(path, _started()))
    with path.open("a") as fh:
        fh.write("garbage\n")
    asyncio.run(append_event(path, _finished()))

    with caplog.at_level(logging.WARNING, logger="hillclimber.lockfile"):
        events = asyncio.run(read_events(path))

    # The started + finished records survive; only the garbage line is dropped.
    assert [type(event) for event in events] == [ExperimentStarted, ExperimentFinished]
    assert any("line 2" in message for message in caplog.messages)


def test_append_recovers_from_a_torn_previous_write(tmp_path: Path):
    # A crash mid-append can leave the last line without its newline. The next
    # append must not splice its JSON onto that partial line (which would fuse two
    # records into one corrupt line); it writes a leading newline first.
    path = tmp_path / "hillclimber.lock"
    asyncio.run(append_event(path, _started()))
    with path.open("a") as fh:
        fh.write('{"event": "cycle_reco')  # torn: no trailing newline

    asyncio.run(append_event(path, _finished()))

    lines = path.read_text().splitlines()
    # Three distinct lines: the good start, the torn partial (still alone), the new finish.
    assert len(lines) == 3
    assert lines[1] == '{"event": "cycle_reco'
    # And the two good records still read back — only the partial is skipped.
    events = asyncio.run(read_events(path))
    assert [type(event) for event in events] == [ExperimentStarted, ExperimentFinished]


# --------------------------------------------------------------------------- #
# fold_statuses / load_statuses
# --------------------------------------------------------------------------- #


def test_fold_running_experiment(tmp_path: Path):
    events = [
        _started(baseline=0.5, cycles=3),
        CycleRecorded(cycle=_cycle(index=1, after=0.6)),
        CycleRecorded(cycle=_cycle(index=2, after=0.7)),
    ]

    status = fold_statuses(events)["exp_a1b2c3d4"]

    # No finished line -> still running (or interrupted; the log can't tell).
    assert status.state == "running"
    assert status.experiment_id == "exp_a1b2c3d4"
    assert status.completed == 2
    assert status.total == 3
    assert status.in_progress == []
    assert status.best is not None
    assert status.best.cycle_id == "cyc_002"
    # Delta is measured against the experiment's baseline, not the parent.
    assert status.best.delta == pytest.approx(0.2)


def test_fold_settles_state_from_the_finish_line():
    completed = fold_statuses([_started(), _finished(outcome="completed")])["exp_a1b2c3d4"]
    assert completed.state == "completed"

    failed = fold_statuses([_started(), _finished(outcome="failed")])["exp_a1b2c3d4"]
    assert failed.state == "failed"


def test_fold_keeps_multiple_experiments_apart_in_log_order():
    events = [
        _started(experiment_id="exp_11111111", baseline=0.5),
        CycleRecorded(cycle=_cycle(index=1, after=0.6, experiment_id="exp_11111111")),
        _finished(experiment_id="exp_11111111"),
        _started(experiment_id="exp_22222222", baseline=0.6),
        CycleRecorded(cycle=_cycle(index=1, after=0.9, experiment_id="exp_22222222")),
    ]

    statuses = fold_statuses(events)

    assert list(statuses) == ["exp_11111111", "exp_22222222"]  # insertion order
    assert statuses["exp_11111111"].state == "completed"
    assert statuses["exp_22222222"].state == "running"
    # Each experiment's deltas fold against its own baseline.
    assert statuses["exp_11111111"].cycles[0].delta == pytest.approx(0.1)
    assert statuses["exp_22222222"].cycles[0].delta == pytest.approx(0.3)


def test_fold_warns_and_skips_events_for_unknown_experiments(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING, logger="hillclimber.lockfile"):
        statuses = fold_statuses([CycleRecorded(cycle=_cycle()), _finished()])

    assert statuses == {}
    assert sum("unknown experiment" in message for message in caplog.messages) == 2


def test_fold_unscored_cycle_never_becomes_best():
    events = [
        _started(),
        CycleRecorded(cycle=_cycle(index=1, after=None)),
        CycleRecorded(cycle=_cycle(index=2, after=0.6)),
    ]

    status = fold_statuses(events)["exp_a1b2c3d4"]

    assert status.cycles[0].delta == 0.0  # unscored -> zero delta
    assert status.best is not None
    assert status.best.cycle_id == "cyc_002"


def test_load_statuses_reads_and_folds(tmp_path: Path):
    path = tmp_path / "hillclimber.lock"
    log = ExperimentLog(path, "exp_a1b2c3d4")

    async def record() -> None:
        await log.record_started(strategy="chain", baseline=_score(), budget=Budget(cycles=1))
        await log.record_cycle(_cycle())
        await log.record_finished(outcome="completed", completed=1, best_cycle_id="cyc_001")

    asyncio.run(record())
    statuses = asyncio.run(load_statuses(path))

    assert statuses["exp_a1b2c3d4"].state == "completed"
    assert statuses["exp_a1b2c3d4"].completed == 1


def test_lock_filename_constant():
    assert lockfile.LOCK_FILENAME == "hillclimber.lock"


# --------------------------------------------------------------------------- #
# reset_history (the explicit opt-in reset)
# --------------------------------------------------------------------------- #


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True
    )


def test_reset_history_removes_the_workdir_without_git(tmp_path: Path):
    workdir = tmp_path / ".hillclimber"
    workdir.mkdir()
    (workdir / "hillclimber.lock").write_text("{}\n")

    asyncio.run(reset_history(str(tmp_path)))

    assert not workdir.exists()
    # Resetting again with nothing left is a no-op, not an error.
    asyncio.run(reset_history(str(tmp_path)))


def test_reset_history_prunes_worktrees_and_keeps_branches(tmp_path: Path):
    _git("init", cwd=tmp_path)
    (tmp_path / "a.txt").write_text("x\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-m", "init", cwd=tmp_path)
    worktree = tmp_path / ".hillclimber" / "hc_a1b2_cycle_001"
    _git("worktree", "add", "-b", "hc/a1b2_cycle_001", str(worktree), "HEAD", cwd=tmp_path)
    (tmp_path / ".hillclimber" / "hillclimber.lock").write_text("{}\n")

    asyncio.run(reset_history(str(tmp_path)))

    assert not (tmp_path / ".hillclimber").exists()
    # The worktree's registration is pruned along with its checkout...
    listing = subprocess.run(["git", "worktree", "list"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert "hc_a1b2_cycle_001" not in listing
    # ...but the cycle branch survives — git history is never destroyed.
    branches = subprocess.run(["git", "branch", "--list", "hc/*"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert "hc/a1b2_cycle_001" in branches
    # And the pruned path is immediately reusable by the next climb.
    _git("worktree", "add", "-b", "hc/again", str(worktree), "HEAD", cwd=tmp_path)
