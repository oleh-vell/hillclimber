"""``hillclimber run`` — the sync CLI shell around the async core.

The core coroutine is stubbed at the command's import site, so these pin the
shell's own responsibilities: bridging into asyncio, choosing a presentation
(summary table vs. ``--json``), and turning the core's known failures into a
clean exit instead of a traceback.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from hillclimber.cli.app import app
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
        accepted=False,
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
