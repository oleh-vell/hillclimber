"""macOS Seatbelt sandbox (``sandbox-exec``).

Wraps the agent argv with ``sandbox-exec -p <profile> ...`` so the child runs
under a Seatbelt policy that denies writes outside the worktree and reads of the
configured sensitive roots, while leaving the system/CLI boot paths readable.

Empirically validated on macOS (these facts drive the profile shape):

- ``(deny default)`` starves the Node-based CLI of boot paths and it aborts at
  startup (exit 134). The working recipe is ``(allow default)`` plus targeted
  ``(deny file-write*)`` / ``(deny file-read* ...)`` and re-allows.
- Seatbelt is **last-match-wins**: the worktree read re-allow MUST come after the
  read-deny block, because the worktree usually sits *under* a denied root (e.g.
  the worktree is ``~/projects/<repo>/hc_...`` and ``~/projects`` is denied) — the
  trailing allow rescues it.
- ``/tmp`` is a symlink to ``/private/tmp``; profile paths must be the realpath or
  rules silently fail to match. Every embedded path is ``os.path.realpath``'d.

Every path embedded in the profile is also run through :func:`_sb_quote`: the
profile is an S-expression with double-quoted string literals, so a worktree
path containing a ``"`` or ``\\`` (unusual, but the config author's to choose)
would otherwise break profile compilation or inject stray policy rules.

``git`` is **not** usable inside a cycle worktree under this sandbox: the
worktree's ``.git`` file points at ``<repo>/.git/worktrees/...``, which sits
under a read-denied root, so any ``git`` invocation there fails with a
misleading "not a git repository". The ``chain`` strategy is designed around
this (the runner commits the worker's edits from outside the sandbox); a
strategy that needs git inside the worktree must widen ``deny_read`` or run
those steps unsandboxed.

Selecting this backend on a non-macOS platform hard-errors at construction: a
sandbox that silently no-ops is worse than none. Use the ``none`` backend
(``PassthroughSandbox``) to opt out explicitly.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from sandboxes.base import Sandbox


def _sb_quote(path: str) -> str:
    """Escape ``path`` for embedding inside a double-quoted Seatbelt string literal.

    The profile is an S-expression; a raw ``"`` would close the literal early
    (injecting policy) and a raw ``\\`` could escape the closing quote. Backslash
    is escaped first so the escapes added for quotes are not themselves doubled.
    """
    return path.replace("\\", "\\\\").replace('"', '\\"')


def _render_profile(workdir: str, deny_read: list[str], network: bool, write_allow: Sequence[str] = ()) -> str:
    """Render the Seatbelt profile confining a child to ``workdir``.

    Pure string work (no shelling out, no I/O beyond ``realpath``) so it is unit
    testable in isolation. ``workdir``, every ``deny_read`` root, and every
    ``write_allow`` dir are ``~``-expanded and ``realpath``'d before being
    embedded, so symlinked inputs (e.g. ``/tmp`` -> ``/private/tmp``) match the
    rules.

    Args:
        workdir: The worktree the child is confined to (read+write allowed).
        deny_read: Sensitive roots the child may not read.
        network: When ``False``, append ``(deny network*)``; when ``True``, the
            default profile's ``(allow default)`` already permits network.
        write_allow: Extra dirs the child may write beyond the worktree — the
            harness's declared runtime-state dirs (see ``Harness.write_allow``).
            Keep these narrow: anything here is writable by the *agent*, so a
            path that configures other tooling (hooks, settings) would be an
            escape hatch out of the sandbox.

    Returns:
        The Seatbelt profile text to pass to ``sandbox-exec -p``.
    """
    work = os.path.realpath(os.path.expanduser(workdir))
    roots = [os.path.realpath(os.path.expanduser(r)) for r in deny_read]
    allows = [os.path.realpath(os.path.expanduser(a)) for a in write_allow]

    lines = [
        "(version 1)",
        "(allow default)",
        "",
        "; writes: deny everything, then re-allow the worktree + essentials",
        "(deny file-write*)",
        "(allow file-write*",
        f'  (subpath "{_sb_quote(work)}")',
        '  (subpath "/private/var/folders")',
        # The harness's runtime-state dirs (per-session scratch its CLI needs).
        *(f'  (subpath "{_sb_quote(a)}")' for a in allows),
        '  (regex #"^/dev/tty")',
        '  (literal "/dev/null"))',
    ]

    # Only emit the read-deny block (and its rescuing worktree re-allow) when
    # there is something to deny. An empty ``deny_read`` must NOT degrade into a
    # bare ``(deny file-read*)`` — that would deny-read everything and starve the
    # CLI. With no denies, ``(allow default)`` already permits all reads.
    if roots:
        lines.append("")
        lines.append("; reads: deny the sensitive trees, then re-allow the worktree LAST (last-match-wins)")
        lines.append("(deny file-read*")
        deny_lines = [f'  (subpath "{_sb_quote(r)}")' for r in roots]
        deny_lines[-1] += ")"
        lines.extend(deny_lines)
        lines.append("(allow file-read*")
        lines.append(f'  (subpath "{_sb_quote(work)}"))')

    if not network:
        lines.append("")
        lines.append("(deny network*)")

    return "\n".join(lines) + "\n"


class SeatbeltSandbox(Sandbox):
    """Confine an agent CLI with macOS Seatbelt (``sandbox-exec``)."""

    def __init__(self, deny_read: list[str], network: bool) -> None:
        """Build the backend from its resolved policy.

        Args:
            deny_read: Sensitive roots the agent may not read.
            network: Whether outbound network access is allowed.

        Raises:
            RuntimeError: If constructed on a non-macOS platform — Seatbelt is
                macOS-only; use the ``none`` sandbox backend to opt out.
        """
        if sys.platform != "darwin":
            raise RuntimeError(
                f"the seatbelt sandbox requires macOS (sandbox-exec); platform is {sys.platform!r}. "
                'Set [sandbox] kind = "none" to run without a sandbox.'
            )
        self.deny_read = deny_read
        self.network = network

    def wrap(self, argv: list[str], workdir: str, write_allow: Sequence[str] = ()) -> list[str]:
        profile = _render_profile(workdir, self.deny_read, self.network, write_allow)
        return ["sandbox-exec", "-p", profile, *argv]
