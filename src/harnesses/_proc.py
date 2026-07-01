"""The single chokepoint every harness shells out through.

Funnelling all agent subprocesses through ``exec_agent`` means OS confinement
(the :class:`~sandboxes.base.Sandbox`) is applied in exactly one place — it can't
be forgotten by a harness and is identical across them. The child runs with its
``cwd`` set to the (realpath'd) worktree; the sandbox wraps the argv with the
policy that confines it there.

Per CLAUDE.md the subprocess is spawned with ``asyncio.create_subprocess_exec``
(never ``subprocess.run``) so it never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import os

from sandboxes.base import Sandbox


async def exec_agent(argv: list[str], cwd: str, sandbox: Sandbox) -> tuple[bytes, bytes, int]:
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

    Returns:
        ``(stdout, stderr, returncode)`` from the finished child.
    """
    real_cwd = os.path.realpath(cwd)
    wrapped = sandbox.wrap(argv, real_cwd)
    proc = await asyncio.create_subprocess_exec(
        *wrapped,
        cwd=real_cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    # ``returncode`` is always set after ``communicate`` returns; coalesce only to
    # satisfy the ``int`` return type (it is never actually ``None`` here).
    return stdout, stderr, proc.returncode if proc.returncode is not None else -1
