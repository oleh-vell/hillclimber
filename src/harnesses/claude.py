"""The Claude Code harness.

Drives the ``claude`` CLI in headless (``--print``) mode: it runs an agent
against a checkout and hands back the agent's final assistant message. This is
the concrete fill for the harness seam in ``strategies.chain`` (``_propose_``/
``_apply_hypothesis``), which needs to turn a system prompt + a task into an
agent's reply.

Runs use ``--output-format stream-json``, so the CLI narrates itself as NDJSON
events while the agent works. Each line is normalized into the shared
:class:`~harnesses.base.TraceEvent` vocabulary and pushed into the caller's
:data:`~harnesses.base.TraceSink` as it arrives — that is how a consumer watches
the agent think and open files live — and the final reply is read from the
stream's terminal ``result`` event.

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

import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from harnesses._proc import exec_agent, stream_exec_agent
from harnesses.base import Harness, HarnessError, TraceEvent, TraceSink
from hillclimber.telemetry import get_logger
from sandboxes.base import Sandbox

logger = get_logger(__name__)

# The verify probe (see ``ClaudeHarness.verify_model``). A health check, not real
# work: the system prompt pins the reply to a single token and forbids tools, so
# the round-trip proves the model id is accepted and the CLI is authed while
# costing next to nothing. Kept module-level so the probe argv stays pure and
# testable, mirroring ``_build_command``.
_VERIFY_SYSTEM_PROMPT = (
    "You are a connectivity health check. Reply with exactly the two characters "
    "`ok` and nothing else. Do not use any tools or take any other action."
)
_VERIFY_PROMPT = "ok"

# The runtime-state dirs the ``claude`` CLI must be able to write outside the
# worktree (see ``Harness.write_allow``): its Bash tool creates a per-session
# env dir and fails every shell command with EPERM when it can't. The location
# has moved across CLI versions — ``/tmp/claude-<uid>/<cwd-slug>`` today,
# ``~/.claude/session-env`` and ``~/.claude/shell-snapshots`` before that — so
# all three are allowed. Deliberately narrow — NOT ``~/.claude`` wholesale:
# ``settings.json`` (hooks), the global ``CLAUDE.md``, and ``~/.claude.json``
# configure the *user's* own sessions, so write access to them would let an
# agent escape the sandbox.
_CLAUDE_WRITE_ALLOW = (
    f"/tmp/claude-{os.getuid()}",
    "~/.claude/session-env",
    "~/.claude/shell-snapshots",
)


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
    mistaken for a flag. ``stream-json`` makes the CLI emit NDJSON events as the
    agent works (the trace stream); the CLI requires ``--verbose`` alongside it
    in ``--print`` mode.
    """
    cmd = [
        "claude",
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--system-prompt",
        run.system_prompt,
    ]
    if run.model is not None:
        cmd += ["--model", run.model]
    cmd += ["--", run.prompt]
    return cmd


def _build_verify_command(model: str) -> list[str]:
    """Build the ``claude`` argv for a :meth:`ClaudeHarness.verify_model` probe.

    Same headless JSON shape as :func:`_build_command`, but with ``model`` always
    pinned and the fixed health-check prompt/system prompt — a real one-token
    round-trip that fails iff the model id is bad or the CLI can't run. Factored
    out (and pure) so it is testable without shelling out.
    """
    return [
        "claude",
        "--print",
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
        "--model",
        model,
        "--system-prompt",
        _VERIFY_SYSTEM_PROMPT,
        "--",
        _VERIFY_PROMPT,
    ]


def _make_verify_workdir() -> str:
    """Create and return a throwaway temp dir for a verify probe's ``cwd``.

    A thin typed wrapper over ``tempfile.mkdtemp`` (whose return widens to
    ``str | bytes`` when offloaded via ``asyncio.to_thread``) so callers get a
    plain ``str`` path.
    """
    return tempfile.mkdtemp(prefix="hc_verify_")


# Width of a trace event's one-line ``summary``. Full payloads stay in
# ``TraceEvent.raw``; the summary is for a log line or a live-view row.
_SUMMARY_WIDTH = 120


def _clip(text: str) -> str:
    """Collapse ``text`` to one line and clip it to summary width."""
    flat = " ".join(text.split())
    if len(flat) <= _SUMMARY_WIDTH:
        return flat
    return flat[: _SUMMARY_WIDTH - 1] + "…"


def _summarize_tool_use(block: dict[str, Any]) -> str:
    """Render a ``tool_use`` block as one call-like line, e.g. ``Read(file_path='x.py')``."""
    name = block.get("name") or "tool"
    tool_input = block.get("input")
    if isinstance(tool_input, dict) and tool_input:
        rendered = ", ".join(f"{key}={value!r}" for key, value in tool_input.items())
        return _clip(f"{name}({rendered})")
    return f"{name}()"


def _summarize_tool_result(block: dict[str, Any]) -> str:
    """Render a ``tool_result`` block as one line of what came back."""
    content = block.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        # Tool results may arrive as a list of content parts; keep the text ones.
        text = " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    else:
        text = ""
    prefix = "tool errored" if block.get("is_error") else "tool returned"
    return _clip(f"{prefix}: {text}") if text.strip() else prefix


def _parse_trace_line(line: bytes) -> list[TraceEvent]:
    """Normalize one ``stream-json`` NDJSON line into zero or more trace events.

    Pure (testable without shelling out), and deliberately lenient: the trace is
    a narration channel, so an unparsable or unrecognized line yields no events
    rather than failing the run — only a missing terminal ``result`` event is
    fatal, and :func:`run` checks for that itself.

    The mapping from the CLI's event shapes:

    - ``system``/``init`` -> one ``init`` event (session started, which model).
    - ``assistant`` / ``user`` messages -> one event per content block:
      ``thinking``, ``text``, ``tool_use``, or ``tool_result``.
    - ``result`` -> one ``result`` event carrying the final envelope in ``raw``.
    """
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        logger.debug("skipping unparsable stream-json line: %r", line[:200])
        return []
    if not isinstance(payload, dict):
        return []

    event_type = payload.get("type")
    if event_type == "system":
        if payload.get("subtype") != "init":
            return []
        summary = f"session started (model {payload.get('model', '?')})"
        return [TraceEvent(kind="init", summary=summary, raw=payload)]

    if event_type in ("assistant", "user"):
        message = payload.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return []
        events: list[TraceEvent] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                events.append(TraceEvent(kind="text", summary=_clip(str(block.get("text", ""))), raw=block))
            elif block_type == "thinking":
                events.append(TraceEvent(kind="thinking", summary=_clip(str(block.get("thinking", ""))), raw=block))
            elif block_type == "tool_use":
                events.append(TraceEvent(kind="tool_use", summary=_summarize_tool_use(block), raw=block))
            elif block_type == "tool_result":
                events.append(TraceEvent(kind="tool_result", summary=_summarize_tool_result(block), raw=block))
        return events

    if event_type == "result":
        outcome = "errored" if payload.get("is_error") else "finished"
        summary = _clip(f"agent {outcome}: {payload.get('result', '')}")
        return [TraceEvent(kind="result", summary=summary, raw=payload)]

    return []


async def run(
    harness_run: HarnessRun,
    sandbox: Sandbox,
    on_trace: TraceSink | None = None,
    write_allow: Sequence[str] = _CLAUDE_WRITE_ALLOW,
) -> str:
    """Run the Claude Code agent described by ``harness_run`` and return its reply.

    Shells out to the ``claude`` CLI in ``--print`` mode with ``stream-json``
    output, in the ``harness_run.path`` working directory and confined to it by
    ``sandbox``. Stream events are normalized into trace events and pushed into
    ``on_trace`` as they arrive (see :func:`_parse_trace_line`); the reply is
    read from the stream's terminal ``result`` event. There is one code path
    whether or not a sink is given — tracing is a tap on it, not a fork.

    Args:
        harness_run: The system prompt, task prompt, working directory, and
            optional model for this invocation.
        sandbox: The OS sandbox confining the CLI to ``harness_run.path``.
        on_trace: Where trace events are pushed as the agent works, or ``None``
            to run silently.
        write_allow: The CLI's runtime-state dirs the sandbox re-allows writes
            to (defaults to :data:`_CLAUDE_WRITE_ALLOW`).

    Returns:
        The agent's final assistant message as plain text.

    Raises:
        RuntimeError: If the CLI exits non-zero, ends its stream without a
            ``result`` event, or reports an error in that event.
    """
    logger.debug("invoking claude in %s (model=%s)", harness_run.path, harness_run.model or "<default>")
    result_payload: dict[str, Any] | None = None

    def handle_line(line: bytes) -> None:
        nonlocal result_payload
        for event in _parse_trace_line(line):
            if event.kind == "result":
                result_payload = event.raw
            if on_trace is not None:
                on_trace(event)

    stderr, returncode = await stream_exec_agent(
        _build_command(harness_run), harness_run.path, sandbox, handle_line, write_allow
    )
    if returncode != 0:
        logger.error("claude exited %s in %s", returncode, harness_run.path)
        raise RuntimeError(f"claude exited {returncode}: {stderr.decode().strip()}")
    if result_payload is None:
        raise RuntimeError("claude produced no result event (output unparsable or truncated)")
    if result_payload.get("is_error"):
        logger.error("claude reported an error in %s", harness_run.path)
        raise RuntimeError(f"claude reported an error: {result_payload.get('result')}")
    logger.debug("claude finished in %s", harness_run.path)
    return result_payload["result"]


class ClaudeHarness(Harness):
    """Object adapter over the module-level :func:`run`.

    Lets a strategy hold a pluggable ``self.harness`` (see ``get_harness`` and
    ``Strategy.__init__``) while the actual work stays in the module-level
    functions above, which are unit-tested directly. The ``sandbox`` it is built
    with (see ``get_harness``) is passed through to every invocation.
    """

    # The claude CLI's per-session state dirs (see the constant's rationale).
    write_allow = _CLAUDE_WRITE_ALLOW

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    async def run(self, harness_run: HarnessRun, on_trace: TraceSink | None = None) -> str:
        # Bare ``run`` resolves to the module-level function above — class scope
        # is not part of method name resolution, so this is not a self-call.
        return await run(harness_run, self.sandbox, on_trace, self.write_allow)

    async def verify_model(self, model: str) -> None:
        """Probe the ``claude`` CLI with ``model`` and a one-token health check.

        Runs :func:`_build_verify_command` through the same sandboxed chokepoint
        (:func:`exec_agent`) every real run uses, in a throwaway temp directory so
        the probe touches nothing under the artefact. A non-zero exit, unparsable
        output, or an error envelope all mean the model can't be run.

        Raises:
            HarnessError: If the CLI exits non-zero, emits unparsable output, or
                reports an error verifying ``model``.
        """
        logger.debug("verifying claude model %r", model)
        # A scratch cwd for the probe: the sandbox confines writes to it, and it
        # is removed regardless of outcome so no artefact state is touched.
        workdir = await asyncio.to_thread(_make_verify_workdir)
        try:
            # Same write-allow as a real run: the probe forbids tools, but the
            # CLI itself may still touch its session state at startup.
            stdout, stderr, returncode = await exec_agent(
                _build_verify_command(model), workdir, self.sandbox, self.write_allow
            )
        finally:
            await asyncio.to_thread(shutil.rmtree, workdir, ignore_errors=True)

        if returncode != 0:
            raise HarnessError(f"claude cannot run model {model!r} (exited {returncode}): {stderr.decode().strip()}")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise HarnessError(
                f"claude produced unparsable output verifying model {model!r}: {stdout.decode().strip()}"
            ) from exc
        if payload.get("is_error"):
            raise HarnessError(f"claude cannot run model {model!r}: {payload.get('result')}")
        logger.debug("claude model %r verified", model)
