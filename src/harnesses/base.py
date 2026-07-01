"""The harness interface.

A *harness* runs an agent: given a system prompt and a task in a working
directory, it drives some backend (the ``claude`` CLI, an API, ...) and hands
back the agent's final reply. Strategies hold a pluggable ``self.harness`` (see
``Strategy.__init__``) so the loop stays agnostic to which backend runs the
agent; ``get_harness`` (see ``harnesses.__init__``) maps a name to a concrete one.

``HarnessRun`` (defined alongside the Claude harness) is the call payload shared
by every harness; concrete harnesses implement :meth:`Harness.run`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harnesses.claude import HarnessRun
    from hillclimber.models import Config


class HarnessError(RuntimeError):
    """This harness can't run a model a config asks of it.

    Raised by :meth:`Harness.verify` when a preflight probe fails — a bad model
    id, a missing/mis-authed CLI, or the backend otherwise being unreachable.
    Distinct from the ``RuntimeError`` a *run* raises so a caller can tell a
    preflight failure ("won't work before we start") from a mid-climb one.
    """


class Harness(ABC):
    """Runs an agent for one invocation and returns its reply."""

    @abstractmethod
    async def run(self, harness_run: HarnessRun) -> str:
        """Run the agent described by ``harness_run`` and return its final reply.

        Args:
            harness_run: The system prompt, task prompt, working directory, and
                optional model for this invocation.

        Returns:
            The agent's final assistant message as plain text.
        """
        ...

    async def verify(self, config: Config) -> None:
        """Preflight: confirm this harness can run every model ``config`` asks of it.

        Called once before a climb spends any real work (see ``hillclimber.run``),
        so a mistyped model alias or a broken/unauthed CLI fails fast instead of
        after minutes of scoring and worktree churn. Each *distinct* model across
        the config's three role agents is probed once, in order, via
        :meth:`verify_model`; the first that fails aborts the whole preflight.

        The contract is that this stays **cheap**: :meth:`verify_model` must be a
        trivial backend round-trip, never real agent work (see its docstring). v1
        drives every agent through one harness (see ``Strategy.__init__``), so
        this checks all three role models; when per-``Agent.harness`` selection
        lands, a harness will verify only the agents routed to it.

        Args:
            config: The validated experiment config whose role-agent models are
                probed.

        Raises:
            HarnessError: If any model can't be run by this harness.
        """
        # Dedupe while preserving order: the three roles usually share one model,
        # so this collapses to a single probe in the common case.
        seen: list[str] = []
        for agent in (config.hillclimber_agent, config.worker_agent, config.reflector_agent):
            if agent.model not in seen:
                seen.append(agent.model)
        for model in seen:
            await self.verify_model(model)

    @abstractmethod
    async def verify_model(self, model: str) -> None:
        """Confirm this harness can actually run ``model`` — cheaply, and for real.

        The per-harness seam behind :meth:`verify`. It must make one *minimal*
        real round-trip to the backend (enough to prove the model id is accepted
        and the CLI is installed and authed) and must **not** ask the agent to do
        any real work — no code generation, no tools, a one-token reply at most.

        Args:
            model: The model alias or full id to probe.

        Raises:
            HarnessError: If the model can't be run by this harness.
        """
        ...
