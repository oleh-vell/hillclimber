"""``hillclimber run`` — the sync CLI shell around the async core.

The core coroutine is stubbed at the command's import site, so these pin the
shell's own responsibilities: bridging into asyncio, choosing a presentation
(summary table vs. ``--json``), and turning the core's known failures into a
clean exit instead of a traceback.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from harnesses import TraceEvent
from hillclimber.cli.app import app
from hillclimber.cli.banner import RUN_PHRASES, run_phrase
from hillclimber.cli.commands import run as run_cmd
from hillclimber.models import CycleStatus, CycleSummary, ExperimentStatus, Score
from hillclimber.scoring import ScorerError

runner = CliRunner()


def _status() -> ExperimentStatus:
    best = CycleSummary(
        experiment_id="exp_a1b2c3d4",
        cycle_id="cyc_001",
        status=CycleStatus.scored,
        hypothesis="use a regex instead of str.split()",
        score_after=Score(value=0.5, passed=True, scorer_id="command"),
        delta=0.05,
    )
    return ExperimentStatus(
        baseline_score=Score(value=0.45, passed=True, scorer_id="command"),
        cycles=[best],
        best=best,
        completed=1,
        total=2,
    )


def _stub_run(monkeypatch, status: ExperimentStatus):
    async def fake_run(path, trace_sink=None, progress_sink=None):
        return status

    monkeypatch.setattr(run_cmd, "run_experiment", fake_run)


def test_run_renders_the_summary_table(monkeypatch):
    _stub_run(monkeypatch, _status())

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0
    # Headline plus the per-cycle row: id, score, delta, hypothesis.
    assert "baseline" in result.output
    assert "cyc_001" in result.output
    assert "0.500" in result.output
    assert "+0.050" in result.output
    assert "use a regex" in result.output


def test_run_json_emits_the_status_payload(monkeypatch):
    _stub_run(monkeypatch, _status())

    result = runner.invoke(app, ["--json", "run"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["completed"] == 1
    assert payload["baseline_score"]["value"] == 0.45
    assert payload["cycles"][0]["cycle_id"] == "cyc_001"


def test_run_turns_known_failures_into_a_clean_exit(monkeypatch):
    async def failing_run(path, trace_sink=None, progress_sink=None):
        raise ScorerError("scorer 'python eval.py' failed (exit=1)")

    monkeypatch.setattr(run_cmd, "run_experiment", failing_run)

    result = runner.invoke(app, ["run"])

    # A known failure exits 1 with a message (on stderr), never a traceback.
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_run_phrase_is_one_of_the_climbing_openers():
    assert run_phrase() in RUN_PHRASES


# --------------------------------------------------------------------------- #
# past experiment history (the overwrite prompt / --overwrite / --append)
# --------------------------------------------------------------------------- #

_TOML = """\
[scorer]
kind = "command"
cmd = "true"

[budget]
cycles = 1
"""


def _experiment_with_history(tmp_path):
    """A valid experiment dir whose artefact already has a hillclimber.lock."""
    (tmp_path / "hillclimber.toml").write_text(_TOML)
    lock = tmp_path / ".hillclimber" / "hillclimber.lock"
    lock.parent.mkdir()
    lock.write_text('{"event": "experiment_started"}\n')
    return lock


def _all_output(result) -> str:
    """stdout + stderr regardless of whether the runner captured them mixed."""
    try:
        return result.output + result.stderr
    except ValueError:
        return result.output


def test_run_without_history_asks_nothing(monkeypatch, tmp_path):
    _stub_run(monkeypatch, _status())
    (tmp_path / "hillclimber.toml").write_text(_TOML)

    result = runner.invoke(app, ["run", str(tmp_path)])

    assert result.exit_code == 0


def test_run_with_history_and_no_tty_fails_with_a_hint(monkeypatch, tmp_path):
    _stub_run(monkeypatch, _status())
    lock = _experiment_with_history(tmp_path)

    result = runner.invoke(app, ["run", str(tmp_path)])

    assert result.exit_code == 1
    assert "--overwrite" in _all_output(result)
    assert "--append" in _all_output(result)
    assert lock.exists()  # nothing was touched


def test_run_append_keeps_history_and_runs(monkeypatch, tmp_path):
    _stub_run(monkeypatch, _status())
    lock = _experiment_with_history(tmp_path)

    result = runner.invoke(app, ["run", str(tmp_path), "--append"])

    assert result.exit_code == 0
    assert lock.exists()


def test_run_overwrite_resets_history_and_runs(monkeypatch, tmp_path):
    _stub_run(monkeypatch, _status())
    lock = _experiment_with_history(tmp_path)

    result = runner.invoke(app, ["run", str(tmp_path), "--overwrite"])

    assert result.exit_code == 0
    assert not lock.exists()


def test_run_overwrite_and_append_are_mutually_exclusive(tmp_path):
    result = runner.invoke(app, ["run", str(tmp_path), "--overwrite", "--append"])

    assert result.exit_code == 2  # a usage error, before anything runs


def test_run_prompt_default_yes_overwrites(monkeypatch, tmp_path):
    _stub_run(monkeypatch, _status())
    lock = _experiment_with_history(tmp_path)
    monkeypatch.setattr(run_cmd, "can_prompt", lambda state: True)

    # Bare Enter takes the default: Y -> overwrite, then the run proceeds.
    result = runner.invoke(app, ["run", str(tmp_path)], input="\n")

    assert result.exit_code == 0
    assert not lock.exists()
    assert "overwrite" in _all_output(result)


def test_run_prompt_decline_aborts_and_keeps_history(monkeypatch, tmp_path):
    _stub_run(monkeypatch, _status())
    lock = _experiment_with_history(tmp_path)
    monkeypatch.setattr(run_cmd, "can_prompt", lambda state: True)

    result = runner.invoke(app, ["run", str(tmp_path)], input="n\n")

    assert result.exit_code == 1
    assert lock.exists()
    assert "aborted" in _all_output(result)


# --------------------------------------------------------------------------- #
# the trace log and the closing goal/CTA lines
# --------------------------------------------------------------------------- #

_TOML_WITH_TARGET = _TOML + "\n[goal]\ntarget = {target}\n"


def _status_with_branch() -> ExperimentStatus:
    """A finished run whose best cycle improved and has a mergeable branch."""
    status = _status()
    assert status.best is not None
    status.best.branch = "hc/a1b2_cycle_001"
    return status


def test_run_tees_traces_into_the_trace_log_and_announces_it(monkeypatch, tmp_path):
    async def fake_run(path, trace_sink=None, progress_sink=None):
        assert trace_sink is not None
        trace_sink(TraceEvent(kind="tool_use", summary="Bash: uv run pytest", raw={}, label="cycle 001/worker"))
        return _status()

    monkeypatch.setattr(run_cmd, "run_experiment", fake_run)
    (tmp_path / "hillclimber.toml").write_text(_TOML)

    result = runner.invoke(app, ["run", str(tmp_path)])

    assert result.exit_code == 0
    content = (tmp_path / ".hillclimber" / "trace.log").read_text()
    assert "[cycle 001/worker] tool_use: Bash: uv run pytest" in content
    assert "trace.log" in result.output  # the path is announced


def test_run_json_still_writes_the_trace_log_but_keeps_stdout_clean(monkeypatch, tmp_path):
    _stub_run(monkeypatch, _status())
    (tmp_path / "hillclimber.toml").write_text(_TOML)

    result = runner.invoke(app, ["--json", "run", str(tmp_path)])

    assert result.exit_code == 0
    json.loads(result.output)  # nothing but the payload on stdout
    assert (tmp_path / ".hillclimber" / "trace.log").exists()


def test_run_ends_with_the_merge_cta_when_a_cycle_improved(monkeypatch, tmp_path):
    _stub_run(monkeypatch, _status_with_branch())
    (tmp_path / "hillclimber.toml").write_text(_TOML)

    result = runner.invoke(app, ["run", str(tmp_path)])

    assert result.exit_code == 0
    assert "merge" in result.output
    assert "hc/a1b2_cycle_001" in result.output


def test_run_ends_with_the_append_cta_when_nothing_improved(monkeypatch, tmp_path):
    status = _status()
    assert status.best is not None
    status.best.delta = 0.0
    _stub_run(monkeypatch, status)
    (tmp_path / "hillclimber.toml").write_text(_TOML)

    result = runner.invoke(app, ["run", str(tmp_path)])

    assert result.exit_code == 0
    assert "--append" in result.output


def test_run_reports_the_goal_met(monkeypatch, tmp_path):
    _stub_run(monkeypatch, _status_with_branch())  # best 0.500
    (tmp_path / "hillclimber.toml").write_text(_TOML_WITH_TARGET.format(target=0.5))

    result = runner.invoke(app, ["run", str(tmp_path)])

    assert result.exit_code == 0
    assert "goal met" in result.output
    assert "merge" in result.output  # the merge command still closes the run


def test_run_reports_the_goal_not_met_with_an_append_hint(monkeypatch, tmp_path):
    _stub_run(monkeypatch, _status_with_branch())  # best 0.500, improved
    (tmp_path / "hillclimber.toml").write_text(_TOML_WITH_TARGET.format(target=0.9))

    result = runner.invoke(app, ["run", str(tmp_path)])

    assert result.exit_code == 0
    assert "goal not met" in result.output
    assert "--append" in result.output
