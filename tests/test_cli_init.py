"""``hillclimber init`` scaffolding.

Covers the three promises of the command: both starter files land in the target
directory, the scaffold is immediately runnable (the toml validates via
``load_config`` and the eval prints a parseable ``Eval``), and existing files are
never clobbered without ``--force``.

The ``--interactive`` wizard gets the same treatment: every answer must land in
the generated toml, defaults must produce the exact template scaffold, and the
overwrite guard must hold unless the user explicitly opts in at the prompt.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from typer.testing import CliRunner

from hillclimber.cli.app import app
from hillclimber.cli.commands import init as init_cmd
from hillclimber.config import HILLCLIMBER_TOML, load_config
from hillclimber.models import CommandScorer
from hillclimber.scoring import score_artefact

runner = CliRunner()


def test_init_scaffolds_toml_and_eval(tmp_path: Path):
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / HILLCLIMBER_TOML).is_file()
    assert (tmp_path / "eval.py").is_file()


def test_init_defaults_to_the_current_directory(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / HILLCLIMBER_TOML).is_file()
    assert (tmp_path / "eval.py").is_file()


def test_scaffolded_toml_is_a_valid_config(tmp_path: Path):
    runner.invoke(app, ["init", str(tmp_path)])
    config = load_config(tmp_path)
    assert config.strategy == "chain"
    assert config.scorer.cmd == "python eval.py"
    # Role prompts were left out of the toml, so the defaults must fill in.
    assert config.hillclimber_agent.system_prompt
    assert config.worker_agent.system_prompt
    assert config.reflector_agent.system_prompt


def test_scaffolded_eval_is_stdlib_only(tmp_path: Path):
    # The design guarantee: users never install a package (hillclimber included)
    # into their project just to write an eval. The envelope marker is the
    # contract instead.
    runner.invoke(app, ["init", str(tmp_path)])
    content = (tmp_path / "eval.py").read_text()
    assert "import hillclimber" not in content
    assert '"hillclimber_eval": 1' in content


def test_scaffolded_eval_emits_a_parseable_eval(tmp_path: Path):
    runner.invoke(app, ["init", str(tmp_path)])
    # Run the scaffold through the real scorer, exactly as `hillclimber run`
    # would; sys.executable pins the interpreter that can import hillclimber.
    scorer = CommandScorer(cmd=f'"{sys.executable}" eval.py')
    score = asyncio.run(score_artefact(scorer, tmp_path))
    assert score.passed
    assert score.value == 0.0  # the TODO stub scores zero until evaluate() is filled in


def test_init_refuses_to_overwrite_without_force(tmp_path: Path):
    (tmp_path / "eval.py").write_text("# mine\n")
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 1
    assert (tmp_path / "eval.py").read_text() == "# mine\n"
    # The guard is all-or-nothing: the missing toml must not be written either.
    assert not (tmp_path / HILLCLIMBER_TOML).exists()


def test_force_overwrites_existing_files(tmp_path: Path):
    (tmp_path / HILLCLIMBER_TOML).write_text("stale")
    result = runner.invoke(app, ["init", str(tmp_path), "--force"])
    assert result.exit_code == 0
    assert "stale" not in (tmp_path / HILLCLIMBER_TOML).read_text()


# --------------------------------------------------------------------------- #
# --interactive wizard
# --------------------------------------------------------------------------- #

# One answer per prompt, in wizard order: confirm directory, cycles, model, target.
ALL_DEFAULTS = "\n\n\n\n"


def test_interactive_defaults_produce_the_template_scaffold(tmp_path: Path):
    result = runner.invoke(app, ["init", str(tmp_path), "--interactive"], input=ALL_DEFAULTS)
    assert result.exit_code == 0
    # Accepting every default must yield the exact same config as plain `init`.
    assert (tmp_path / HILLCLIMBER_TOML).read_text() == init_cmd.TOML_TEMPLATE
    assert (tmp_path / "eval.py").read_text() == init_cmd.EVAL_TEMPLATE


def test_interactive_shows_the_banner(tmp_path: Path):
    result = runner.invoke(app, ["init", str(tmp_path), "-i"], input=ALL_DEFAULTS)
    assert result.exit_code == 0
    assert "█" in result.output  # the HILLCLIMBER wordmark leads the wizard


def test_interactive_answers_land_in_the_toml(tmp_path: Path):
    # yes to the dir, 10 cycles, model choice 2 (opus), target 0.9
    result = runner.invoke(app, ["init", str(tmp_path), "-i"], input="y\n10\n2\n0.9\n")
    assert result.exit_code == 0
    config = load_config(tmp_path)
    assert config.budget.cycles == 10
    assert config.goal.target == 0.9
    assert config.hillclimber_agent.model == "claude-opus-4-8"
    assert config.worker_agent.model == "claude-opus-4-8"
    assert config.reflector_agent.model == "claude-opus-4-8"


def test_interactive_declining_the_cwd_asks_for_a_path(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    elsewhere = tmp_path / "other_project"
    elsewhere.mkdir()
    # no to the cwd, then the real path, then defaults for cycles/model/target
    result = runner.invoke(app, ["init", "-i"], input=f"n\n{elsewhere}\n\n\n\n")
    assert result.exit_code == 0
    assert (elsewhere / HILLCLIMBER_TOML).is_file()
    assert not (tmp_path / HILLCLIMBER_TOML).exists()


def test_interactive_rejects_a_nonpositive_cycle_budget(tmp_path: Path):
    # 0 is rejected and the prompt re-asks; 3 is then accepted
    result = runner.invoke(app, ["init", str(tmp_path), "-i"], input="\n0\n3\n\n\n")
    assert result.exit_code == 0
    assert load_config(tmp_path).budget.cycles == 3


def test_interactive_overwrite_declined_leaves_files_untouched(tmp_path: Path):
    (tmp_path / "eval.py").write_text("# mine\n")
    result = runner.invoke(app, ["init", str(tmp_path), "-i"], input=ALL_DEFAULTS + "n\n")
    assert result.exit_code == 1
    assert (tmp_path / "eval.py").read_text() == "# mine\n"
    # The guard is all-or-nothing, same as the non-interactive path.
    assert not (tmp_path / HILLCLIMBER_TOML).exists()


def test_interactive_overwrite_confirmed_replaces_files(tmp_path: Path):
    (tmp_path / HILLCLIMBER_TOML).write_text("stale")
    result = runner.invoke(app, ["init", str(tmp_path), "-i"], input=ALL_DEFAULTS + "y\n")
    assert result.exit_code == 0
    assert "stale" not in (tmp_path / HILLCLIMBER_TOML).read_text()


def test_interactive_warns_when_target_is_not_a_git_repo(tmp_path: Path):
    result = runner.invoke(app, ["init", str(tmp_path), "-i"], input=ALL_DEFAULTS)
    assert result.exit_code == 0
    assert "not inside a git repository" in result.output


def test_interactive_stays_quiet_inside_a_git_repo(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    result = runner.invoke(app, ["init", str(tmp_path), "-i"], input=ALL_DEFAULTS)
    assert result.exit_code == 0
    assert "not inside a git repository" not in result.output
