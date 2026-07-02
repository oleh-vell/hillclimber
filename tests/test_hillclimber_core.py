import asyncio
import subprocess
from pathlib import Path

import pytest

import hillclimber
from harnesses import ClaudeHarness
from hillclimber import Config, RunEvent, ScorerError, get_baseline_score
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
    config.scorer = CommandScorer(cmd="""echo '{"hillclimber_eval": 1, "score": 0.42}'""")
    score = asyncio.run(get_baseline_score(config))
    assert score.passed
    assert score.value == 0.42
    assert score.scorer_id == "command"


def test_baseline_score_takes_the_last_eval_line():
    config = _config(EXAMPLE_PROJECT)
    # Noise before the Eval JSON (e.g. pipeline chatter) is ignored: the last
    # parseable Eval on stdout wins.
    config.scorer = CommandScorer(cmd="""printf 'loading...\\n{"hillclimber_eval": 1, "score": 0.9}\\n'""")
    score = asyncio.run(get_baseline_score(config))
    assert score.value == 0.9


def test_baseline_score_raises_when_command_fails():
    # A baseline scorer that cannot run is a misconfiguration, not a score of
    # zero: without a valid baseline there is no hill to climb, so abort loudly
    # rather than fabricate a 0.0 the whole run would then climb against.
    config = _config(EXAMPLE_PROJECT)
    config.scorer = CommandScorer(cmd="false")  # exits non-zero
    with pytest.raises(ScorerError):
        asyncio.run(get_baseline_score(config))


def test_baseline_score_raises_when_no_eval_emitted():
    config = _config(EXAMPLE_PROJECT)
    config.scorer = CommandScorer(cmd="true")  # exits 0 but prints no Eval
    with pytest.raises(ValueError):
        asyncio.run(get_baseline_score(config))


def test_baseline_score_uses_start_branch_when_set(tmp_path: Path):
    # main holds score 0.7; the current branch (wip) holds a different score.
    _git("init", "-b", "main", cwd=tmp_path)
    (tmp_path / "score.json").write_text('{"hillclimber_eval": 1, "score": 0.7}\n')
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-m", "main", cwd=tmp_path)
    _git("checkout", "-b", "wip", cwd=tmp_path)
    (tmp_path / "score.json").write_text('{"hillclimber_eval": 1, "score": 0.1}\n')
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-m", "wip", cwd=tmp_path)

    config = _config(tmp_path)
    config.scorer = CommandScorer(cmd="cat score.json")
    config.start_branch = "main"

    score = asyncio.run(get_baseline_score(config))

    # Scored at start_branch (main -> 0.7), not the checked-out wip (0.1)...
    assert score.value == 0.7
    # ...and the throwaway baseline worktree is cleaned up afterward.
    assert not (tmp_path / ".hillclimber" / "hc_baseline").exists()


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


def test_run_snapshots_a_dirty_artefact_when_auto_commit_set(tmp_path: Path):
    # Same dirty repo, but auto_commit opts into snapshotting instead of refusing.
    _git("init", cwd=tmp_path)
    # Inject the top-level auto_commit key before the first table so it doesn't
    # land inside [reflector_agent].
    toml = _GUARD_TOML.format(path=tmp_path).replace("[scorer]", "auto_commit = true\n[scorer]", 1)
    (tmp_path / "hillclimber.toml").write_text(toml)
    (tmp_path / "a.txt").write_text("x\n")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-m", "init", cwd=tmp_path)
    (tmp_path / "a.txt").write_text("changed\n")

    # The snapshot path is scaffolded but not yet implemented, so it surfaces the
    # NotImplementedError rather than the refusal — the run took the opt-in branch.
    with pytest.raises(NotImplementedError, match="not implemented"):
        asyncio.run(hillclimber.run(tmp_path))


# --------------------------------------------------------------------------- #
# run: progress events
# --------------------------------------------------------------------------- #

# A config whose scorer emits a fixed Eval, with a zero-cycle budget so run()
# goes baseline -> preflight -> (no cycles) without touching a harness for real.
_PROGRESS_TOML = """\
[scorer]
kind = "command"
cmd = "echo '{\\"hillclimber_eval\\": 1, \\"score\\": 0.42}'"
[budget]
cycles = 0
[sandbox]
kind = "none"
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


def test_run_emits_baseline_and_preflight_progress_events(tmp_path: Path, monkeypatch):
    _git("init", cwd=tmp_path)
    (tmp_path / "hillclimber.toml").write_text(_PROGRESS_TOML)
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-m", "init", cwd=tmp_path)

    # The preflight is a real CLI round-trip; stub it so the test proves the
    # events around it, not the claude binary.
    async def _verified(self: ClaudeHarness, model: str) -> None:
        return None

    monkeypatch.setattr(ClaudeHarness, "verify_model", _verified)

    events: list[RunEvent] = []
    status = asyncio.run(hillclimber.run(tmp_path, progress_sink=events.append))

    assert [e.kind for e in events] == ["baseline_start", "baseline_done", "preflight_start", "preflight_done"]
    baseline_done = events[1]
    assert baseline_done.score == pytest.approx(0.42)
    assert status.completed == 0
