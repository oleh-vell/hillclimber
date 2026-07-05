"""Pluggable filesystem sandboxes.

``get_sandbox`` maps a ``SandboxConfig`` (the discriminated union in
``hillclimber.models``) to a concrete :class:`Sandbox`. v1 ships the macOS
Seatbelt backend plus a passthrough (``none``) opt-out; the registry is the seam
for adding more (bubblewrap, docker, ...) later, mirroring
``hillclimber.harnesses.get_harness``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hillclimber.sandboxes.base import Sandbox
from hillclimber.sandboxes.passthrough import PassthroughSandbox
from hillclimber.sandboxes.seatbelt import SeatbeltSandbox

if TYPE_CHECKING:
    from hillclimber.models import SandboxConfig


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
    # Dispatch on the ``kind`` discriminator (not isinstance) so the config
    # classes are only needed as annotations — no runtime import of the models.
    if config.kind == "seatbelt":
        return SeatbeltSandbox(deny_read=config.deny_read, network=config.network)
    if config.kind == "none":
        return PassthroughSandbox()
    raise ValueError(f"unknown sandbox kind: {config.kind!r}")


__all__ = ["PassthroughSandbox", "Sandbox", "SeatbeltSandbox", "get_sandbox"]
