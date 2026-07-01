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
