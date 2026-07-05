"""Pluggable agent harnesses.

``get_harness`` maps a harness name (as written in ``Agent.harness``) to a
concrete :class:`Harness`, built with the OS :class:`~hillclimber.sandboxes.base.Sandbox`
that confines its agent runs. v1 ships the Claude Code harness only; the registry
is the seam for adding more (an API harness, etc.) later.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from hillclimber.harnesses.base import Harness, HarnessError, HarnessRun, TraceEvent, TraceSink
from hillclimber.harnesses.claude import ClaudeHarness
from hillclimber.sandboxes.base import Sandbox

if TYPE_CHECKING:
    from hillclimber.models import Timeouts

# Canonical name -> factory. Alternate spellings live in ``_ALIASES``, never here.
# Every factory takes ``(sandbox, timeouts)`` so the runner's wall-clock ceilings
# reach the harness (see ``get_harness`` / ``Timeouts``).
_HARNESSES: dict[str, Callable[..., Harness]] = {
    "claude": ClaudeHarness,
}

# Normalized alternate spellings -> canonical name. Keys are post-``_canonical``,
# so one entry covers "claude code", "Claude Code", "claude-code", "claude_code".
_ALIASES: dict[str, str] = {
    "claude_code": "claude",
}


def _canonical(name: str) -> str:
    """Fold a user-written harness name (case/space/hyphen variants) to its registry key."""
    normalized = name.strip().lower().replace("-", "_").replace(" ", "_")
    return _ALIASES.get(normalized, normalized)


def resolve_harness(name: str) -> Callable[..., Harness]:
    """The factory behind ``name`` — the sandbox-free half of :func:`get_harness`.

    Lets config checks validate a harness name without building anything.

    Raises:
        ValueError: If ``name`` is not a known harness or alias.
    """
    try:
        return _HARNESSES[_canonical(name)]
    except KeyError:
        known = ", ".join(sorted(_HARNESSES))
        raise ValueError(f"unknown harness: {name!r} (known: {known})") from None


def get_harness(name: str, sandbox: Sandbox, timeouts: Timeouts | None = None) -> Harness:
    """Return a fresh harness instance for ``name``, built with ``sandbox``.

    Args:
        name: The harness name (as written in ``Agent.harness``).
        sandbox: The OS sandbox confining the harness's agent runs.
        timeouts: Wall-clock ceilings applied to the harness's subprocesses;
            ``None`` lets the harness fall back to its generous defaults.

    Raises:
        ValueError: If ``name`` is not a known harness or alias.
    """
    return resolve_harness(name)(sandbox, timeouts)


__all__ = [
    "ClaudeHarness",
    "Harness",
    "HarnessError",
    "HarnessRun",
    "TraceEvent",
    "TraceSink",
    "get_harness",
    "resolve_harness",
]
