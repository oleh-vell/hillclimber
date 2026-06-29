import asyncio
from pathlib import Path

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


def test_baseline_score_passes_when_command_succeeds():
    config = _config(EXAMPLE_PROJECT)
    config.scorer = CommandScorer(cmd="true")  # exits 0
    score = asyncio.run(get_baseline_score(config))
    assert score.passed
    assert score.value == 1.0
    assert score.scorer_id == "command"


def test_baseline_score_fails_when_command_fails():
    config = _config(EXAMPLE_PROJECT)
    config.scorer = CommandScorer(cmd="false")  # exits non-zero
    score = asyncio.run(get_baseline_score(config))
    assert not score.passed
    assert score.value == 0.0
