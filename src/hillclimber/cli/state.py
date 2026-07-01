"""Shared CLI state, kept dependency-free to break an import cycle.

Both ``app`` (which populates it in the root callback) and the command modules
(which read it off ``ctx.obj``) need :class:`CLIState`. Housing it here — with no
imports back into the CLI package — lets both sides depend on it without ``app``
and the commands importing each other.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CLIState:
    """Global presentation flags, shared by every command via ``ctx.obj``.

    Presentation *intent* only — never experiment state. Commands read this to
    decide how loud to be (``verbose``) and whether to emit machine-readable
    output (``json``) instead of a rendered view.
    """

    json: bool = False
    verbose: bool = False
