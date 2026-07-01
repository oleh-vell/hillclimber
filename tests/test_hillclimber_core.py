import asyncio
import subprocess
from pathlib import Path

import pytest

import hillclimber
from hillclimber import Config, get_baseline_score
from hillclimber.models import Agent, Budget, CommandScorer

PROJECT_FOLDERS = Path(__file__).parent / "example_project_folders"
EXAMPLE_PROJECT = PROJECT_FOLDERS / "no_toml_file"


def _agent() -> Agent:
    return Agent(harness="api", model="mistral-large", system_prompt="improve it")


def _config(path: Path) -> Config:
    """A minimal but valid config pointed at ``path``."""
    return Config(
        path_to_artefact=str(path),
        scorer=CommandScorer(cmd="pytest test_eval.py"),
        budget=Budget(cycles=1),
        hillclimber_agent=_agent(),
        worker_agent=_agent(),
        reflector_agent=_agent(),
    )


def test_baseline_score_reads_the_eval_score():
    config = _config(EXAMPLE_PROJECT)
    # The scorer emits its Eval as JSON; the runner reads score off that, not
    # the exit code, so a partial score comes through verbatim.
    config.scorer = CommandScorer(cmd="""echo '{"score": 0.42}'""")
    score = asyncio.run(get_baseline_score(config))
    assert score.passed
    assert score.value == 0.42
    assert score.scorer_id == "command"


def test_baseline_score_takes_the_last_eval_line():
    config = _config(EXAMPLE_PROJECT)
    # Noise before the Eval JSON (e.g. pipeline chatter) is ignored: the last
    # parseable Eval on stdout wins.
    config.scorer = CommandScorer(cmd="""printf 'loading...\\n{"score": 0.9}\\n'""")
    score = asyncio.run(get_baseline_score(config))
    assert score.value == 0.9


def test_baseline_score_fails_when_command_fails():
    config = _config(EXAMPLE_PROJECT)
    config.scorer = CommandScorer(cmd="false")  # exits non-zero
    score = asyncio.run(get_baseline_score(config))
    assert not score.passed
    assert score.value == 0.0


def test_baseline_score_raises_when_no_eval_emitted():
    config = _config(EXAMPLE_PROJECT)
    config.scorer = CommandScorer(cmd="true")  # exits 0 but prints no Eval
    with pytest.raises(ValueError):
        asyncio.run(get_baseline_score(config))


# --------------------------------------------------------------------------- #
# run: dirty-artefact guard
# --------------------------------------------------------------------------- #

_GUARD_TOML = """\
path_to_artefact = "{path}"
[scorer]
kind = "command"
cmd = "true"
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


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True
    )


def test_run_refuses_to_start_on_a_dirty_artefact(tmp_path: Path):
    # A committed repo with a valid config...
    _git("init", cwd=tmp_path)
    (tmp_path / "hillclimber.toml").write_text(_GUARD_TOML.format(path=tmp_path))
    (tmp_path / "a.txt").write_text("x\n")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-m", "init", cwd=tmp_path)
    # ...then an uncommitted edit: the run must refuse rather than diverge.
    (tmp_path / "a.txt").write_text("changed\n")

    with pytest.raises(RuntimeError, match="uncommitted"):
        asyncio.run(hillclimber.run(tmp_path))
