"""Pluggable agent harnesses.

``get_harness`` maps a harness name (as written in ``Agent.harness``) to a
concrete :class:`Harness`, built with the OS :class:`~sandboxes.base.Sandbox`
that confines its agent runs. v1 ships the Claude Code harness only; the registry
is the seam for adding more (an API harness, etc.) later.
"""

from __future__ import annotations

from collections.abc import Callable

from harnesses.base import Harness, HarnessError, TraceEvent, TraceSink
from harnesses.claude import ClaudeHarness
from sandboxes.base import Sandbox

_HARNESSES: dict[str, Callable[[Sandbox], Harness]] = {
    "claude": ClaudeHarness,
    "claude_code": ClaudeHarness,
}


def get_harness(name: str, sandbox: Sandbox) -> Harness:
    """Return a fresh harness instance for ``name``, built with ``sandbox``.

    Args:
        name: The harness identifier (see ``Agent.harness``), e.g. ``"claude"``.
        sandbox: The OS sandbox confining the harness's agent runs.

    Returns:
        A new :class:`Harness` of the matching kind.

    Raises:
        ValueError: If ``name`` is not a known harness.
    """
    try:
        factory = _HARNESSES[name]
    except KeyError:
        known = ", ".join(sorted(_HARNESSES))
        raise ValueError(f"unknown harness: {name!r} (known: {known})") from None
    return factory(sandbox)


__all__ = ["ClaudeHarness", "Harness", "HarnessError", "TraceEvent", "TraceSink", "get_harness"]
