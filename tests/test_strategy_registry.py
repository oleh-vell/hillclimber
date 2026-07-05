"""The strategy registry: name -> class dispatch and agent-role verification.

``verify_agents`` is the contract both ``hillclimber run`` and ``hillclimber
check`` enforce before spending any work: a role the strategy requires but the
config lacks is a hard error (with an actionable, per-role message), a role the
config defines but the strategy never drives is only a warning.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

import pytest

import hillclimber
from hillclimber.harnesses import ClaudeHarness
from hillclimber.models import Config, ExperimentStatus, Score
from hillclimber.progress import RunEvent
from hillclimber.strategies.base import Strategy
from hillclimber.strategies.chain import Chain
from hillclimber.strategies.registry import STRATEGIES, get_strategy, verify_agents


def _config(roles: list[str]) -> Config:
    agent = {"harness": "claude", "model": "m"}
    return Config.model_validate(
        {
            "path_to_artefact": ".",
            "scorer": {"kind": "command", "cmd": "true"},
            "budget": {"cycles": 1},
            "agents": dict.fromkeys(roles, agent),
        }
    )


# --------------------------------------------------------------------------- #
# get_strategy
# --------------------------------------------------------------------------- #


def test_get_strategy_resolves_chain():
    assert get_strategy("chain") is Chain


def test_get_strategy_rejects_an_unknown_name():
    with pytest.raises(ValueError, match='unknown strategy "foo"; known strategies: chain'):
        get_strategy("foo")


# --------------------------------------------------------------------------- #
# verify_agents
# --------------------------------------------------------------------------- #


def test_verify_agents_passes_on_exactly_the_declared_roles():
    assert verify_agents(_config(["orchestrator", "worker"])) == []


def test_verify_agents_raises_on_a_missing_role():
    expected = 'strategy "chain" requires agent "worker"; please add [agents.worker] to hillclimber.toml'
    with pytest.raises(ValueError, match=re.escape(expected)):
        verify_agents(_config(["orchestrator"]))


def test_verify_agents_reports_every_missing_role_at_once():
    # [agents] absent entirely -> one actionable line per required role, not a
    # first-only error (or a raw pydantic dump).
    with pytest.raises(ValueError) as excinfo:
        verify_agents(_config([]))
    message = str(excinfo.value)
    for role in ("orchestrator", "worker"):
        assert f'strategy "chain" requires agent "{role}"; please add [agents.{role}] to hillclimber.toml' in message


def test_verify_agents_warns_on_an_unused_role():
    warnings = verify_agents(_config(["orchestrator", "worker", "reflector"]))
    assert warnings == [
        'strategy "chain" does not use agent "reflector"; ignoring [agents.reflector] in hillclimber.toml'
    ]


def test_verify_agents_rejects_an_unknown_strategy():
    config = _config(["orchestrator", "worker"])
    config.strategy = "genetic"
    with pytest.raises(ValueError, match='unknown strategy "genetic"'):
        verify_agents(config)


def test_verify_agents_rejects_an_unknown_harness():
    config = _config(["orchestrator", "worker"])
    config.agents["worker"].harness = "claud"
    with pytest.raises(ValueError, match=r"\[agents.worker\]: unknown harness: 'claud'"):
        verify_agents(config)


# --------------------------------------------------------------------------- #
# run: registry dispatch and fail-before-work
# --------------------------------------------------------------------------- #

_TOML = """\
[scorer]
kind = "command"
cmd = "echo '{{\\"hillclimber_eval\\": 1, \\"score\\": 0.5}}'"
[budget]
cycles = 0
[sandbox]
kind = "none"
{agents}
"""

_AGENT_TABLES = """\
[agents.orchestrator]
harness = "claude"
model = "m"
[agents.worker]
harness = "claude"
model = "m"
"""


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True
    )


def _experiment_dir(tmp_path: Path, agents: str, extra: str = "") -> Path:
    _git("init", cwd=tmp_path)
    (tmp_path / "hillclimber.toml").write_text(extra + _TOML.format(agents=agents))
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-m", "init", cwd=tmp_path)
    return tmp_path


class _FakeStrategy(Strategy):
    """Declares no roles and does no work — proves run() dispatches by name."""

    executed: list[str] = []

    async def execute(self, config: Config, baseline: Score) -> ExperimentStatus:
        _FakeStrategy.executed.append(config.strategy)
        return ExperimentStatus(baseline_score=baseline, cycles=[], best=None, completed=0, total=0)


def test_run_dispatches_the_strategy_through_the_registry(tmp_path: Path, monkeypatch):
    monkeypatch.setitem(STRATEGIES, "fake", _FakeStrategy)
    monkeypatch.setattr(_FakeStrategy, "executed", [])
    _experiment_dir(tmp_path, agents="", extra='strategy = "fake"\n')

    status = asyncio.run(hillclimber.run(tmp_path))

    assert _FakeStrategy.executed == ["fake"]
    assert status.completed == 0


def test_run_fails_on_a_missing_role_before_any_work(tmp_path: Path):
    # Only the orchestrator is configured; the run must fail on the agents
    # check — before scoring the baseline, so nothing lands after the run's
    # opening statement.
    _experiment_dir(tmp_path, agents='[agents.orchestrator]\nharness = "claude"\nmodel = "m"\n')

    events: list[RunEvent] = []
    with pytest.raises(ValueError, match=re.escape("please add [agents.worker]")):
        asyncio.run(hillclimber.run(tmp_path, progress_sink=events.append))
    assert [e.kind for e in events] == ["run_start"]


def test_run_warns_but_continues_on_an_unused_role(tmp_path: Path, monkeypatch, caplog):
    monkeypatch.setitem(STRATEGIES, "fake", _FakeStrategy)
    monkeypatch.setattr(_FakeStrategy, "executed", [])
    _experiment_dir(tmp_path, agents=_AGENT_TABLES, extra='strategy = "fake"\n')

    # The preflight would probe model "m" through the real claude CLI; stub it —
    # this test proves the warnings, not the preflight.
    async def _verified(self: ClaudeHarness, model: str) -> None:
        return None

    monkeypatch.setattr(ClaudeHarness, "verify_model", _verified)

    with caplog.at_level("WARNING", logger="hillclimber.run"):
        asyncio.run(hillclimber.run(tmp_path))

    # Both configured agents are unused by the fake strategy -> two warnings,
    # and the run still executed.
    assert sum("does not use agent" in m for m in caplog.messages) == 2
    assert _FakeStrategy.executed == ["fake"]


# --------------------------------------------------------------------------- #
# Strategy._role_agent (prompt resolution)
# --------------------------------------------------------------------------- #


def _chain() -> Chain:
    from hillclimber.sandboxes import PassthroughSandbox

    return Chain(PassthroughSandbox())


def test_role_agent_fills_the_strategy_default_prompt():
    agent = _chain()._role_agent(_config(["orchestrator", "worker"]), "orchestrator")
    assert agent.system_prompt == Chain.roles["orchestrator"].default_prompt


def test_role_agent_keeps_a_toml_prompt_override():
    config = _config(["orchestrator", "worker"])
    config.agents["worker"].system_prompt = "you are a careful patcher"
    agent = _chain()._role_agent(config, "worker")
    assert agent.system_prompt == "you are a careful patcher"


def test_role_agent_does_not_mutate_the_config():
    config = _config(["orchestrator", "worker"])
    _chain()._role_agent(config, "orchestrator")
    assert config.agents["orchestrator"].system_prompt is None


def test_role_agent_raises_the_actionable_message_on_a_missing_role():
    # The safety net for callers that skip verify_agents: same message shape.
    expected = 'strategy "chain" requires agent "worker"; please add [agents.worker] to hillclimber.toml'
    with pytest.raises(ValueError, match=re.escape(expected)):
        _chain()._role_agent(_config(["orchestrator"]), "worker")
