"""The harness interface.

A *harness* runs an agent: given a system prompt and a task in a working
directory, it drives some backend (the ``claude`` CLI, an API, ...) and hands
back the agent's final reply. Strategies hold a pluggable ``self.harness`` (see
``Strategy.__init__``) so the loop stays agnostic to which backend runs the
agent; ``get_harness`` (see ``harnesses.__init__``) maps a name to a concrete one.

``HarnessRun`` (defined alongside the Claude harness) is the call payload shared
by every harness; concrete harnesses implement :meth:`Harness.run`. While an
agent runs, the harness narrates its progress as :class:`TraceEvent`\\ s pushed
into an optional :data:`TraceSink` — the shared vocabulary that lets one
consumer (a logger today, the CLI's live view later) render any backend's run.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from harnesses.claude import HarnessRun
    from hillclimber.models import Agent


class TraceEvent(BaseModel):
    """One step of a running agent, normalized across harnesses.

    Every harness translates its backend-specific stream into this small shared
    vocabulary, so a consumer written once (the logging sink in
    ``strategies.base``, the CLI's live view later) renders any backend's run.
    The ``kind``s are deliberately few — what a reader of the run needs to see:

    - ``init``: the session started (model, tools).
    - ``thinking`` / ``text``: the agent reasoning or speaking.
    - ``tool_use`` / ``tool_result``: the agent acting (opening files, running
      commands) and what came back.
    - ``result``: the run finished; the final reply.

    Anything backend-specific survives untouched in ``raw`` for richer
    rendering later; ``summary`` is the one-line human-readable fallback.
    """

    kind: Literal["init", "thinking", "text", "tool_use", "tool_result", "result"]
    summary: str  # one human-readable line, e.g. "Read: src/pipeline.py"
    raw: dict[str, Any]  # the untouched backend payload behind this event
    label: str | None = None  # who is running, stamped by the strategy (e.g. "cycle 003/worker")


# Where trace events land. Deliberately a *sync* callable — a sink forwards
# (logs a line, put_nowait's onto a queue) and must never block the harness's
# stream-reading loop. ``None`` everywhere means "no tracing", today's behavior.
TraceSink = Callable[[TraceEvent], None]


class HarnessError(RuntimeError):
    """This harness can't run a model a config asks of it.

    Raised by :meth:`Harness.verify` when a preflight probe fails — a bad model
    id, a missing/mis-authed CLI, or the backend otherwise being unreachable.
    Distinct from the ``RuntimeError`` a *run* raises so a caller can tell a
    preflight failure ("won't work before we start") from a mid-climb one.
    """


class Harness(ABC):
    """Runs an agent for one invocation and returns its reply."""

    # Extra directories (``~``-relative allowed) this harness's backend needs
    # writable *outside* the worktree — per-session runtime state its CLI keeps
    # in the home dir, without which the agent's tools break under a sandbox
    # that denies writes (see ``Sandbox.wrap``). Each concrete harness declares
    # its own; the chokepoint (``harnesses._proc``) hands them to the sandbox on
    # every invocation. Keep the list minimal and *state-only*: never include a
    # path that configures behavior (settings, hooks) — an agent that can write
    # those can escape the sandbox on the user's next interactive session.
    write_allow: tuple[str, ...] = ()

    @abstractmethod
    async def run(self, harness_run: HarnessRun, on_trace: TraceSink | None = None) -> str:
        """Run the agent described by ``harness_run`` and return its final reply.

        Args:
            harness_run: The system prompt, task prompt, working directory, and
                optional model for this invocation.
            on_trace: Where to push :class:`TraceEvent`\\ s as the run progresses,
                or ``None`` to run silently. A harness that cannot stream simply
                emits nothing (or a single ``result`` event) — the contract is
                zero or more events, then the returned reply.

        Returns:
            The agent's final assistant message as plain text.
        """
        ...

    async def verify(self, agents: Iterable[Agent]) -> None:
        """Preflight: confirm this harness can run every model ``agents`` ask of it.

        Called once before a climb spends any real work (see ``hillclimber.run``),
        so a mistyped model alias or a broken/unauthed CLI fails fast. Callers
        pass only the agents the strategy will actually drive — a configured but
        unused ``[agents.<role>]`` table must not abort a run. Each distinct
        model is probed once, concurrently, via :meth:`verify_model` (which must
        stay a trivial backend round-trip, never real agent work).

        The probes run in a ``TaskGroup`` so the first failure cancels the
        siblings — no probe subprocess is left running past the point the
        preflight is already doomed.

        Raises:
            HarnessError: If any model can't be run by this harness.
        """
        models = dict.fromkeys(agent.model for agent in agents)
        try:
            async with asyncio.TaskGroup() as group:
                for model in models:
                    group.create_task(self.verify_model(model))
        except* HarnessError as failures:
            # TaskGroup wraps failures in an ExceptionGroup; surface the first
            # probe error directly so callers keep the flat HarnessError contract.
            raise failures.exceptions[0] from None

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
