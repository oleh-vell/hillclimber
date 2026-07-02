"""``hillclimber check`` — the eval conformance preflight.

Each test builds a minimal experiment dir whose scorer command is trivially
controllable (``cat`` a canned output file, ``false``), then asserts the check
verdict: green for a conforming envelope, exit 1 with the right diagnosis for
each of the known first-run mistakes.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from hillclimber.cli.app import app

runner = CliRunner()

_TOML = """\
[scorer]
kind = "command"
cmd = "{cmd}"
[budget]
cycles = 1
[hillclimber_agent]
harness = "claude"
model = "m"
[worker_agent]
harness = "claude"
model = "m"
[reflector_agent]
harness = "claude"
model = "m"
"""


def _project(tmp_path: Path, cmd: str, output: str | None = None) -> Path:
    """A minimal experiment whose scorer runs ``cmd`` (with ``output`` on disk)."""
    (tmp_path / "hillclimber.toml").write_text(_TOML.format(cmd=cmd))
    if output is not None:
        (tmp_path / "eval_out").write_text(output)
    return tmp_path


def test_check_passes_a_conforming_eval(tmp_path: Path):
    _project(tmp_path, "cat eval_out", '{"hillclimber_eval": 1, "score": 0.42}\n')

    result = runner.invoke(app, ["check", str(tmp_path)])

    assert result.exit_code == 0
    assert "0.420" in result.output
    assert "ready to climb" in result.output


def test_check_json_reports_the_parsed_score(tmp_path: Path):
    _project(tmp_path, "cat eval_out", '{"hillclimber_eval": 1, "score": 0.42, "details": {"cases": 4}}\n')

    result = runner.invoke(app, ["--json", "check", str(tmp_path)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["score"] == 0.42
    assert payload["details"] == {"cases": 4}


def test_check_fails_when_the_scorer_command_fails(tmp_path: Path):
    _project(tmp_path, "false")

    result = runner.invoke(app, ["--json", "check", str(tmp_path)])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "exited 1" in payload["error"]


def test_check_fails_when_no_envelope_is_printed(tmp_path: Path):
    _project(tmp_path, "echo hello")

    result = runner.invoke(app, ["--json", "check", str(tmp_path)])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "hillclimber_eval" in payload["error"]


def test_check_fails_on_a_score_line_missing_the_marker(tmp_path: Path):
    # The near-miss: a score was computed and printed, but without the marker the
    # runner will never read it. check must reject it (the hint is rendered-only).
    _project(tmp_path, "cat eval_out", '{"score": 0.9}\n')

    result = runner.invoke(app, ["--json", "check", str(tmp_path)])

    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False


def test_check_fails_without_a_config(tmp_path: Path):
    result = runner.invoke(app, ["--json", "check", str(tmp_path)])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "config" in payload["error"]


def test_check_passes_on_a_fresh_scaffold(tmp_path: Path):
    # init -> check must be green out of the box: the scaffolded eval.py is
    # stdlib-only and already emits a valid envelope (the 0.0 stub).
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["check", str(tmp_path)])

    assert result.exit_code == 0
    assert "0.000" in result.output
