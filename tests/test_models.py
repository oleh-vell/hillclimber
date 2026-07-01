"""Tests for the stop-condition predicates on ``Goal`` and ``Budget``.

These are the two checks that drive the chain loop: it continues while the goal
is not met and the budget is not exhausted. The predicates are pure and sync, so
no asyncio is needed here.
"""

import pytest
from pydantic import ValidationError

from hillclimber.models import (
    Budget,
    Config,
    Goal,
    PassthroughSandboxConfig,
    Score,
    SeatbeltSandboxConfig,
)


def _score(value: float) -> Score:
    return Score(value=value, passed=True, scorer_id="s")


def _config_data(**overrides: object) -> dict:
    """Minimal valid ``Config`` payload, with ``overrides`` merged in."""
    agent = {"harness": "claude", "model": "m"}
    data: dict = {
        "path_to_artefact": ".",
        "scorer": {"kind": "command", "cmd": "true"},
        "budget": {"cycles": 1},
        "hillclimber_agent": agent,
        "worker_agent": agent,
        "reflector_agent": agent,
    }
    data.update(overrides)
    return data


# --------------------------------------------------------------------------- #
# Goal.is_met
# --------------------------------------------------------------------------- #


def test_is_met_false_when_nothing_scored_yet():
    # No best yet (first iteration) is never "met".
    assert Goal(target=0.9).is_met(None) is False


def test_is_met_false_when_no_target():
    # The v1 default: no target -> never met, loop runs until budget is spent.
    goal = Goal()
    assert goal.target is None
    assert goal.is_met(_score(1.0)) is False


def test_is_met_false_below_target():
    assert Goal(target=0.9).is_met(_score(0.89)) is False


def test_is_met_true_at_target():
    # Reaching the target exactly counts as met (>=).
    assert Goal(target=0.9).is_met(_score(0.9)) is True


def test_is_met_true_above_target():
    assert Goal(target=0.9).is_met(_score(0.95)) is True


# --------------------------------------------------------------------------- #
# Budget.is_exhausted
# --------------------------------------------------------------------------- #


def test_is_exhausted_false_before_budget_spent():
    assert Budget(cycles=3).is_exhausted(0) is False
    assert Budget(cycles=3).is_exhausted(2) is False


def test_is_exhausted_true_at_budget():
    assert Budget(cycles=3).is_exhausted(3) is True


def test_is_exhausted_true_past_budget():
    assert Budget(cycles=3).is_exhausted(4) is True


def test_is_exhausted_zero_budget_is_immediately_exhausted():
    # A zero-cycle budget is exhausted before any cycle runs.
    assert Budget(cycles=0).is_exhausted(0) is True


# --------------------------------------------------------------------------- #
# Config.sandbox (discriminated union)
# --------------------------------------------------------------------------- #


def test_sandbox_defaults_to_seatbelt_when_table_omitted():
    config = Config.model_validate(_config_data())
    assert isinstance(config.sandbox, SeatbeltSandboxConfig)
    assert config.sandbox.network is True
    assert config.sandbox.deny_read  # populated from DEFAULT_DENY_READ


def test_sandbox_none_table_parses_to_passthrough():
    config = Config.model_validate(_config_data(sandbox={"kind": "none"}))
    assert isinstance(config.sandbox, PassthroughSandboxConfig)


def test_sandbox_seatbelt_table_parses_with_overrides():
    config = Config.model_validate(_config_data(sandbox={"kind": "seatbelt", "deny_read": ["~/x"], "network": False}))
    assert isinstance(config.sandbox, SeatbeltSandboxConfig)
    assert config.sandbox.deny_read == ["~/x"]
    assert config.sandbox.network is False


def test_sandbox_unknown_kind_raises():
    with pytest.raises(ValidationError):
        Config.model_validate(_config_data(sandbox={"kind": "docker"}))


def test_default_deny_read_is_copied_not_shared():
    # Each config gets its own list; mutating one must not leak into the next.
    a = SeatbeltSandboxConfig()
    b = SeatbeltSandboxConfig()
    a.deny_read.append("~/mutated")
    assert "~/mutated" not in b.deny_read
