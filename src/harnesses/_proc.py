"""The single chokepoint every harness shells out through.

Funnelling all agent subprocesses through ``exec_agent`` / ``stream_exec_agent``
means OS confinement (the :class:`~sandboxes.base.Sandbox`) is applied in
exactly one place — it can't be forgotten by a harness and is identical across
them. The child runs with its ``cwd`` set to the (realpath'd) worktree; the
sandbox wraps the argv with the policy that confines it there.

Two flavours share that guarantee: ``exec_agent`` buffers the child's output
until it exits (probes, one-shot calls), ``stream_exec_agent`` hands stdout to
the caller line by line as the child produces it (agent runs that narrate
themselves, see ``harnesses.base.TraceSink``).

Per CLAUDE.md the subprocess is spawned with ``asyncio.create_subprocess_exec``
(never ``subprocess.run``) so it never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable, Sequence

from sandboxes.base import Sandbox

# Per-line buffer limit for streaming reads. asyncio's default (64 KiB) is far
# too small for stream-JSON lines that embed large tool results — an oversized
# line would raise instead of parse — so the reader gets generous headroom.
_STREAM_LIMIT = 16 * 1024 * 1024


class AgentTimeout(RuntimeError):
    """An agent subprocess overran its wall-clock ceiling and was killed.

    Raised by :func:`exec_agent` / :func:`stream_exec_agent` when a child does
    not finish within ``timeout`` seconds. The child is SIGKILL'd and reaped
    before this propagates, so no orphan process is left behind (see the
    ``timeout`` handling / ``Timeouts`` config).
    """


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Best-effort kill-and-reap of ``proc`` if it is still running.

    The one teardown both exec paths funnel through: on a timeout, a cancelled
    climb, or an exception raised out of the stream loop, the sandboxed child
    must not be left orphaned. ``kill`` is a no-op once the child has already
    exited; the reap is guarded so teardown never raises over the real error.
    """
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    with contextlib.suppress(Exception):
        await proc.wait()


async def exec_agent(
    argv: list[str], cwd: str, sandbox: Sandbox, write_allow: Sequence[str] = (), timeout: float | None = None
) -> tuple[bytes, bytes, int]:
    """Run ``argv`` under ``sandbox``, confined to ``cwd``, and collect its output.

    ``cwd`` is ``realpath``'d once and used both for the child's working
    directory and for the sandbox policy, so the profile's worktree path and the
    actual child cwd can never disagree across symlinks (e.g. ``/tmp`` ->
    ``/private/tmp``) or relative paths.

    Args:
        argv: The child command to run (e.g. the full ``claude ...`` argv).
        cwd: The run's worktree — the child's working directory and the path the
            sandbox confines it to.
        sandbox: The sandbox that wraps ``argv`` with its confinement policy.
        write_allow: The harness's runtime-state dirs the child may write beyond
            ``cwd`` (see ``Harness.write_allow``), handed to the sandbox policy.
        timeout: Wall-clock ceiling in seconds; ``None`` waits indefinitely. On
            overrun the child is killed and :class:`AgentTimeout` is raised.

    Returns:
        ``(stdout, stderr, returncode)`` from the finished child.

    Raises:
        AgentTimeout: If the child does not finish within ``timeout`` seconds.
    """
    real_cwd = os.path.realpath(cwd)
    wrapped = sandbox.wrap(argv, real_cwd, write_allow)
    proc = await asyncio.create_subprocess_exec(
        *wrapped,
        cwd=real_cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError as exc:
        raise AgentTimeout(f"agent process exceeded the {timeout}s timeout") from exc
    finally:
        # Covers the timeout, a cancelled climb, and any other early exit: the
        # child is killed and reaped rather than orphaned. A clean exit already
        # set ``returncode``, so this is a no-op there.
        await _terminate(proc)
    # ``returncode`` is always set after ``communicate`` returns; coalesce only to
    # satisfy the ``int`` return type (it is never actually ``None`` here).
    return stdout, stderr, proc.returncode if proc.returncode is not None else -1


async def stream_exec_agent(
    argv: list[str],
    cwd: str,
    sandbox: Sandbox,
    on_line: Callable[[bytes], None],
    write_allow: Sequence[str] = (),
    timeout: float | None = None,
) -> tuple[bytes, int]:
    """Run ``argv`` under ``sandbox`` like :func:`exec_agent`, streaming stdout.

    The streaming sibling of :func:`exec_agent` — same realpath'd ``cwd``, same
    sandbox wrapping — but stdout is handed to ``on_line`` one line at a time as
    the child produces it, instead of buffered until exit. This is how a harness
    surfaces an agent's progress live (see ``harnesses.base.TraceSink``).

    ``on_line`` must be cheap and non-blocking (it runs on the read loop) and
    receives each non-blank line with its trailing newline still attached. If it
    raises, the child is killed and reaped before the exception propagates.

    Args:
        argv: The child command to run (e.g. the full ``claude ...`` argv).
        cwd: The run's worktree — the child's working directory and the path the
            sandbox confines it to.
        sandbox: The sandbox that wraps ``argv`` with its confinement policy.
        on_line: Called with each non-blank stdout line as it arrives.
        write_allow: The harness's runtime-state dirs the child may write beyond
            ``cwd`` (see ``Harness.write_allow``), handed to the sandbox policy.
        timeout: Wall-clock ceiling in seconds; ``None`` waits indefinitely. On
            overrun the child is killed and :class:`AgentTimeout` is raised.

    Returns:
        ``(stderr, returncode)`` from the finished child; stdout was already
        delivered line by line.

    Raises:
        AgentTimeout: If the child does not finish within ``timeout`` seconds.
    """
    real_cwd = os.path.realpath(cwd)
    wrapped = sandbox.wrap(argv, real_cwd, write_allow)
    proc = await asyncio.create_subprocess_exec(
        *wrapped,
        cwd=real_cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=_STREAM_LIMIT,
    )
    # Both pipes were requested above, so the readers are always present; the
    # assert narrows the ``StreamReader | None`` types.
    stdout_reader, stderr_reader = proc.stdout, proc.stderr
    assert stdout_reader is not None and stderr_reader is not None

    async def drain_stdout() -> None:
        async for line in stdout_reader:
            if line.strip():
                on_line(line)

    async def pump() -> tuple[bytes, int]:
        # Drain both pipes concurrently so a chatty stderr can never fill its
        # pipe and stall the child while stdout is being read (or vice versa).
        _, stderr = await asyncio.gather(drain_stdout(), stderr_reader.read())
        return stderr, await proc.wait()

    try:
        return await asyncio.wait_for(pump(), timeout)
    except TimeoutError as exc:
        raise AgentTimeout(f"agent process exceeded the {timeout}s timeout") from exc
    finally:
        # Timeout, cancellation, or an ``on_line`` sink that raised: kill and
        # reap the child so a wedged/aborted run never leaks the sandboxed CLI.
        await _terminate(proc)
