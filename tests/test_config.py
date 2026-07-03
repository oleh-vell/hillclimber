"""``load_config`` path resolution and the scorer-path guardrail.

Paths in the toml are relative to the config file, which must live at the
artefact root; a scorer ``cmd`` that hard-codes the artefact's absolute path is
rejected, since it would score the original tree instead of each cycle's worktree
(see ``Config._reject_absolute_scorer_path``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hillclimber.config import load_config
from hillclimber.models import Agent, Budget, CommandScorer, Config

# A valid toml with no path_to_artefact line; tests prepend one when needed.
_BASE_TOML = """\
[scorer]
kind = "command"
cmd = "true"
[budget]
cycles = 1
[agents.orchestrator]
harness = "claude"
model = "m"
[agents.worker]
harness = "claude"
model = "m"
"""


def _write(tmp_path: Path, body: str) -> Path:
    (tmp_path / "hillclimber.toml").write_text(body)
    return tmp_path


def test_path_to_artefact_defaults_to_the_toml_dir(tmp_path: Path):
    # Omitted entirely -> the config's own directory, resolved absolute.
    config = load_config(_write(tmp_path, _BASE_TOML))
    assert config.path_to_artefact == str(tmp_path.resolve())


def test_relative_path_to_artefact_resolves_against_the_toml(tmp_path: Path):
    # "." names the toml's dir; stored absolute.
    config = load_config(_write(tmp_path, 'path_to_artefact = "."\n' + _BASE_TOML))
    assert config.path_to_artefact == str(tmp_path.resolve())


def test_toml_not_at_artefact_root_is_rejected(tmp_path: Path):
    # path_to_artefact points at a subdir, not the toml's own dir.
    (tmp_path / "sub").mkdir()
    body = 'path_to_artefact = "sub"\n' + _BASE_TOML
    with pytest.raises(ValueError, match="must live at the artefact root"):
        load_config(_write(tmp_path, body))


def test_absolute_scorer_path_is_rejected(tmp_path: Path):
    # The footgun: a cmd hard-coding the absolute artefact path would score the
    # original tree, not each cycle's worktree.
    abs_path = str(tmp_path.resolve())
    body = (
        f'[scorer]\nkind = "command"\ncmd = "python {abs_path}/eval.py"\n'
        "[budget]\ncycles = 1\n"
        '[agents.orchestrator]\nharness = "claude"\nmodel = "m"\n'
        '[agents.worker]\nharness = "claude"\nmodel = "m"\n'
    )
    with pytest.raises(ValueError, match="relative to the artefact root"):
        load_config(_write(tmp_path, body))


def test_legacy_agent_tables_are_rejected_with_a_rename_hint(tmp_path: Path):
    # A pre-rename config would otherwise load with agents={} and fail later
    # with a confusing missing-role error; catch it at load with a hint.
    body = _BASE_TOML + '[hillclimber_agent]\nharness = "claude"\nmodel = "m"\n'
    with pytest.raises(ValueError, match=r"\[hillclimber_agent\] -> \[agents.orchestrator\]"):
        load_config(_write(tmp_path, body))


def test_relative_scorer_path_is_accepted():
    # A relative cmd is fine even with an absolute path_to_artefact (constructed
    # directly, bypassing load_config).
    agent = Agent(harness="claude", model="m")
    config = Config(
        path_to_artefact="/some/abs/artefact",
        scorer=CommandScorer(cmd="uv run python eval.py"),
        budget=Budget(cycles=1),
        agents={"orchestrator": agent, "worker": agent},
    )
    assert config.scorer.cmd == "uv run python eval.py"
