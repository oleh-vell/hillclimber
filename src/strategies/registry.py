"""The strategy registry: name -> class, and config-vs-strategy agent checks.

``get_strategy`` resolves ``Config.strategy`` to its ``Strategy`` subclass;
``verify_agents`` checks the config's ``[agents]`` tables against that
strategy's declared roles. Registration is a plain dict: a new strategy adds
itself to ``STRATEGIES``.
"""

from __future__ import annotations

from harnesses import resolve_harness
from hillclimber.models import Config
from strategies.base import Strategy, missing_role_message
from strategies.chain import Chain

STRATEGIES: dict[str, type[Strategy]] = {"chain": Chain}


def get_strategy(name: str) -> type[Strategy]:
    """Resolve a strategy name from the config to its class.

    Raises:
        ValueError: If no strategy is registered under ``name``.
    """
    try:
        return STRATEGIES[name]
    except KeyError:
        known = ", ".join(sorted(STRATEGIES))
        raise ValueError(f'unknown strategy "{name}"; known strategies: {known}') from None


def verify_agents(config: Config) -> list[str]:
    """Check the config's ``[agents]`` against its strategy's declared roles.

    Missing required roles raise a ValueError with one line per role; an
    unknown harness name on any configured agent is fatal too. Configured
    roles the strategy never drives only produce warnings.

    Returns:
        One warning string per configured agent the strategy does not use, in
        config order. Empty when the agents match the roles exactly.

    Raises:
        ValueError: If the strategy is unknown, a required role has no
            ``[agents.<role>]`` table, or an agent names an unknown harness.
    """
    strategy_cls = get_strategy(config.strategy)
    missing = [role for role in strategy_cls.roles if role not in config.agents]
    if missing:
        raise ValueError("\n".join(missing_role_message(config.strategy, role) for role in missing))
    for role, agent in config.agents.items():
        try:
            resolve_harness(agent.harness)
        except ValueError as exc:
            raise ValueError(f"[agents.{role}]: {exc}") from None
    return [
        f'strategy "{config.strategy}" does not use agent "{role}"; ignoring [agents.{role}] in hillclimber.toml'
        for role in config.agents
        if role not in strategy_cls.roles
    ]
