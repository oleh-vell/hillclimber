"""The Claude Code harness.

Drives the ``claude`` CLI in headless (``--print``) mode: it runs an agent
against a checkout and hands back the agent's final assistant message. This is
the concrete fill for the harness seam in ``strategies.chain`` (``_propose_``/
``_apply_hypothesis``), which needs to turn a system prompt + a task into an
agent's reply.

Per CLAUDE.md the CLI is shelled out to (via the ``harnesses._proc.exec_agent``
chokepoint, never ``subprocess.run``) so it never blocks the event loop. Runs are
always ``--dangerously-skip-permissions``: that removes permission *prompts*
(which would deadlock a non-interactive run) but provides no filesystem boundary.
The per-cycle worktree isolates *git state*, not the *filesystem* — an agent can
still read and write anywhere on the machine. The real boundary is the OS
:class:`~sandboxes.base.Sandbox` the harness holds: ``exec_agent`` wraps every
invocation so the CLI is confined to its worktree.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from harnesses._proc import exec_agent
from harnesses.base import Harness
from hillclimber.telemetry import get_logger
from sandboxes.base import Sandbox

logger = get_logger(__name__)


class HarnessRun(BaseModel):
    """One headless invocation of the ``claude`` CLI.

    ``system_prompt`` (the role's "SP") and ``prompt`` (the task) are the two
    halves of what the agent is asked; ``path`` is the checkout it runs in (a
    run's worktree).
    """

    system_prompt: str  # the agent's system prompt (SP)
    path: str  # working directory the agent runs in (a worktree/checkout)
    prompt: str  # the task/message to send the agent
    model: str | None = None  # model alias or full id; None -> CLI default


def _build_command(run: HarnessRun) -> list[str]:
    """Build the ``claude`` argv for ``run``.

    Factored out of :func:`run` so the (pure) command construction is testable
    without shelling out. The task ``prompt`` is passed positionally after a
    ``--`` terminator so a prompt that happens to start with ``-`` is never
    mistaken for a flag.
    """
    cmd = [
        "claude",
        "--print",
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
        "--system-prompt",
        run.system_prompt,
    ]
    if run.model is not None:
        cmd += ["--model", run.model]
    cmd += ["--", run.prompt]
    return cmd


async def run(harness_run: HarnessRun, sandbox: Sandbox) -> str:
    """Run the Claude Code agent described by ``harness_run`` and return its reply.

    Shells out to the ``claude`` CLI in ``--print`` mode with JSON output, in the
    ``harness_run.path`` working directory and confined to it by ``sandbox``, and
    returns the agent's final assistant message (the ``result`` field of the JSON
    envelope).

    Args:
        harness_run: The system prompt, task prompt, working directory, and
            optional model for this invocation.
        sandbox: The OS sandbox confining the CLI to ``harness_run.path``.

    Returns:
        The agent's final assistant message as plain text.

    Raises:
        RuntimeError: If the CLI exits non-zero, emits unparsable output, or
            reports an error in its JSON envelope.
    """
    logger.debug("invoking claude in %s (model=%s)", harness_run.path, harness_run.model or "<default>")
    stdout, stderr, returncode = await exec_agent(_build_command(harness_run), harness_run.path, sandbox)
    if returncode != 0:
        logger.error("claude exited %s in %s", returncode, harness_run.path)
        raise RuntimeError(f"claude exited {returncode}: {stderr.decode().strip()}")

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude produced unparsable output: {stdout.decode().strip()}") from exc

    if payload.get("is_error"):
        logger.error("claude reported an error in %s", harness_run.path)
        raise RuntimeError(f"claude reported an error: {payload.get('result')}")
    logger.debug("claude finished in %s", harness_run.path)
    return payload["result"]


class ClaudeHarness(Harness):
    """Object adapter over the module-level :func:`run`.

    Lets a strategy hold a pluggable ``self.harness`` (see ``get_harness`` and
    ``Strategy.__init__``) while the actual work stays in the module-level
    functions above, which are unit-tested directly. The ``sandbox`` it is built
    with (see ``get_harness``) is passed through to every invocation.
    """

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    async def run(self, harness_run: HarnessRun) -> str:
        # Bare ``run`` resolves to the module-level function above — class scope
        # is not part of method name resolution, so this is not a self-call.
        return await run(harness_run, self.sandbox)
