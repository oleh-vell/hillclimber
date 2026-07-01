"""Pluggable filesystem sandboxes.

``get_sandbox`` maps a ``SandboxConfig`` (the discriminated union in
``hillclimber.models``) to a concrete :class:`Sandbox`. v1 ships the macOS
Seatbelt backend plus a passthrough (``none``) opt-out; the registry is the seam
for adding more (bubblewrap, docker, ...) later, mirroring
``harnesses.get_harness``.
"""

from __future__ import annotations

from hillclimber.models import PassthroughSandboxConfig, SandboxConfig, SeatbeltSandboxConfig
from sandboxes.base import Sandbox
from sandboxes.passthrough import PassthroughSandbox
from sandboxes.seatbelt import SeatbeltSandbox


def get_sandbox(config: SandboxConfig) -> Sandbox:
    """Build the concrete sandbox for ``config``.

    Args:
        config: A validated sandbox config variant (its ``kind`` selects the
            backend and carries the policy).

    Returns:
        A new :class:`Sandbox` of the matching kind.

    Raises:
        ValueError: If ``config`` is an unknown sandbox kind.
        RuntimeError: If a Seatbelt sandbox is built on a non-macOS platform.
    """
    if isinstance(config, SeatbeltSandboxConfig):
        return SeatbeltSandbox(deny_read=config.deny_read, network=config.network)
    if isinstance(config, PassthroughSandboxConfig):
        return PassthroughSandbox()
    raise ValueError(f"unknown sandbox kind: {config.kind!r}")


__all__ = ["PassthroughSandbox", "Sandbox", "SeatbeltSandbox", "get_sandbox"]
