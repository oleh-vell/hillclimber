"""The sandbox interface.

A *sandbox* confines an agent CLI to its run's worktree at the OS level: given a
child argv and the per-run worktree, it returns a new argv that runs that child
under an OS sandbox enforcing the filesystem (and network) policy. Harnesses
shell out through a single chokepoint (``harnesses._proc.exec_agent``) that wraps
every argv with the configured sandbox, so confinement can never be forgotten and
is identical across harnesses.

``wrap`` is pure and **synchronous** — it only rewrites argv (the Seatbelt
profile is passed inline via ``sandbox-exec -p``, so there is no file I/O and
nothing to block the event loop). ``get_sandbox`` (see ``sandboxes.__init__``)
maps a config to a concrete backend, mirroring ``harnesses.get_harness``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Sandbox(ABC):
    """Wraps a child argv so it runs confined to a per-run worktree."""

    @abstractmethod
    def wrap(self, argv: list[str], workdir: str) -> list[str]:
        """Return ``argv`` rewritten to run under the sandbox, confined to ``workdir``.

        Args:
            argv: The child command to run (e.g. the full ``claude ...`` argv).
            workdir: The per-run worktree the child is confined to.

        Returns:
            A new argv that runs ``argv`` under the sandbox.
        """
        ...
