"""``hillclimber status`` — the read-only history view and its CTA.

Each test builds an experiment dir (config on disk, history written through the
real ``ExperimentLog`` appender) and asserts two things: the rendered state of
play, and — the command's whole point — that the last line is the right call
to action for that state.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from typer.testing import CliRunner

from hillclimber.cli.app import app
from hillclimber.lockfile import ExperimentLog, lock_path
from hillclimber.models import Budget, Cycle, CycleStatus, Score

runner = CliRunner()

# Rendered lines carry tmp paths and merge commands that must be asserted whole;
# a wide console keeps Rich from wrapping them mid-path. (Rich reads COLUMNS
# dynamically, and CliRunner patches the environment for the invocation.)
_WIDE = {"COLUMNS": "400"}

_TOML = """\
[scorer]
kind = "command"
cmd = "true"
[budget]
cycles = 3
[agents.orchestrator]
harness = "claude"
model = "m"
[agents.worker]
harness = "claude"
model = "m"
"""


def _project(tmp_path: Path) -> Path:
    (tmp_path / "hillclimber.toml").write_text(_TOML)
    return tmp_path


def _score(value: float) -> Score:
    return Score(value=value, passed=True, scorer_id="command")


def _cycle(index: int, after: float | None, experiment_id: str = "exp_a1b2c3d4") -> Cycle:
    return Cycle(
        experiment_id=experiment_id,
        index=index,
        parent_ref="baseline",
        branch=f"hc/a1b2_cycle_{index:03d}",
        worktree=f"hc_a1b2_cycle_{index:03d}",
        hypothesis="try X",
        score_before=_score(0.5),
        score_after=_score(after) if after is not None else None,
        status=CycleStatus.scored if after is not None else CycleStatus.failed,
        commit_sha="c0ffee" if after is not None else None,
    )


def _record_history(
    project: Path,
    *cycles: Cycle,
    baseline: float = 0.5,
    finish: bool = True,
    experiment_id: str = "exp_a1b2c3d4",
) -> None:
    """Write one experiment's history through the real appender."""
    log = ExperimentLog(lock_path(str(project)), experiment_id)

    async def record() -> None:
        await log.record_started(strategy="chain", baseline=_score(baseline), budget=Budget(cycles=3))
        for cycle in cycles:
            await log.record_cycle(cycle)
        if finish:
            best = max(
                (c for c in cycles if c.score_after is not None),
                key=lambda c: c.score_after.value if c.score_after else 0.0,
                default=None,
            )
            await log.record_finished(
                outcome="completed", completed=len(cycles), best_cycle_id=best.cycle_id if best else None
            )

    asyncio.run(record())


# --------------------------------------------------------------------------- #
# nothing here yet
# --------------------------------------------------------------------------- #


def test_status_without_a_config_points_at_init(tmp_path: Path):
    result = runner.invoke(app, ["status", str(tmp_path)], env=_WIDE)

    assert result.exit_code == 0
    assert "no hillclimber.toml" in result.output
    assert "hillclimber init" in result.output
    # The CTA is the last line.
    assert "hillclimber init" in result.output.strip().splitlines()[-1]


def test_status_with_a_config_but_no_runs_points_at_run(tmp_path: Path):
    _project(tmp_path)

    result = runner.invoke(app, ["status", str(tmp_path)], env=_WIDE)

    assert result.exit_code == 0
    assert "no experiments have been run" in result.output
    assert "hillclimber run" in result.output.strip().splitlines()[-1]


# --------------------------------------------------------------------------- #
# history exists
# --------------------------------------------------------------------------- #


def test_status_shows_cycles_delta_and_the_merge_cta(tmp_path: Path):
    _project(tmp_path)
    _record_history(tmp_path, _cycle(1, after=0.6), _cycle(2, after=0.8))

    result = runner.invoke(app, ["status", str(tmp_path)], env=_WIDE)

    assert result.exit_code == 0
    assert "Experiment completed" in result.output
    # The experiment id is machine detail — it stays out of the rendered view.
    assert "exp_a1b2c3d4" not in result.output
    assert "2/3" in result.output  # cycles run vs budget
    assert "+0.300" in result.output  # resulting delta of the best cycle
    assert "cyc_002" in result.output
    # Hypotheses are deliberately absent — status stays terse.
    assert "try X" not in result.output
    # The CTA is a runnable merge command.
    assert "To merge best score" in result.output
    assert "merge hc/a1b2_cycle_002" in result.output
    # status runs from outside the artefact repo, so the command locates it.
    assert f"-C {tmp_path}" in result.output


def test_status_merge_command_omits_dash_c_inside_the_artefact(tmp_path: Path, monkeypatch):
    _project(tmp_path)
    _record_history(tmp_path, _cycle(1, after=0.8))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["status"], env=_WIDE)

    assert result.exit_code == 0
    assert "git merge hc/a1b2_cycle_001" in result.output
    assert "-C" not in result.output


def test_status_without_an_improvement_suggests_climbing_again(tmp_path: Path):
    _project(tmp_path)
    _record_history(tmp_path, _cycle(1, after=0.4), _cycle(2, after=None))

    result = runner.invoke(app, ["status", str(tmp_path)], env=_WIDE)

    assert result.exit_code == 0
    assert "No cycle beat the baseline" in result.output
    assert "--append" in result.output
    assert "git merge" not in result.output


def test_status_marks_an_unfinished_experiment_as_running_or_interrupted(tmp_path: Path):
    _project(tmp_path)
    _record_history(tmp_path, _cycle(1, after=0.6), finish=False)

    result = runner.invoke(app, ["status", str(tmp_path)], env=_WIDE)

    assert result.exit_code == 0
    assert "running or interrupted" in result.output
    # An in-flight improvement is still mergeable — the CTA points at it.
    assert "merge hc/a1b2_cycle_001" in result.output


def test_status_details_the_latest_of_several_experiments(tmp_path: Path):
    _project(tmp_path)
    _record_history(tmp_path, _cycle(1, after=0.6), experiment_id="exp_11111111")
    _record_history(tmp_path, _cycle(1, after=0.9, experiment_id="exp_22222222"), experiment_id="exp_22222222")

    result = runner.invoke(app, ["status", str(tmp_path)], env=_WIDE)

    assert result.exit_code == 0
    assert "1 earlier experiment(s)" in result.output
    assert "+0.400" in result.output  # the latest experiment's delta, not the first's


def test_status_recovers_from_a_corrupt_lock_line(tmp_path: Path):
    # A single torn/corrupt line must not wedge status forever: the reader skips
    # it and folds the good records around it, so history still renders.
    _project(tmp_path)
    _record_history(tmp_path, _cycle(1, after=0.6))
    lock = lock_path(str(tmp_path))
    lines = lock.read_text().splitlines()
    lines.insert(1, "garbage")  # a corrupt interior line between good records
    lock.write_text("\n".join(lines) + "\n")

    result = runner.invoke(app, ["status", str(tmp_path)], env=_WIDE)

    assert result.exit_code == 0
    assert "+0.100" in result.output  # the recovered cycle's delta vs baseline


# --------------------------------------------------------------------------- #
# --json
# --------------------------------------------------------------------------- #


def test_status_json_reports_uninitialized(tmp_path: Path):
    result = runner.invoke(app, ["--json", "status", str(tmp_path)], env=_WIDE)

    assert result.exit_code == 0
    assert json.loads(result.output) == {"initialized": False, "experiments": []}


def test_status_json_reports_the_folded_experiments(tmp_path: Path):
    _project(tmp_path)
    _record_history(tmp_path, _cycle(1, after=0.6), _cycle(2, after=0.8))

    result = runner.invoke(app, ["--json", "status", str(tmp_path)], env=_WIDE)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["initialized"] is True
    (experiment,) = payload["experiments"]
    assert experiment["experiment_id"] == "exp_a1b2c3d4"
    assert experiment["state"] == "completed"
    assert experiment["best"]["cycle_id"] == "cyc_002"
    # The summary carries the merge pointers, so machine consumers get them too.
    assert experiment["best"]["branch"] == "hc/a1b2_cycle_002"
    assert experiment["best"]["worktree"] == "hc_a1b2_cycle_002"
    assert experiment["best"]["commit_sha"] == "c0ffee"
