"""The passthrough (``none``) sandbox.

Runs the agent CLI unconfined: ``wrap`` returns the argv unchanged. This is the
explicit opt-out from OS sandboxing (``[sandbox] kind = "none"``) and, unlike the
Seatbelt backend, carries no platform restriction — it is the way to run on
Linux (or anywhere Seatbelt is unavailable) until a portable backend lands.
"""

from __future__ import annotations

from sandboxes.base import Sandbox


class PassthroughSandbox(Sandbox):
    """A no-op sandbox: the child runs exactly as given."""

    def wrap(self, argv: list[str], workdir: str) -> list[str]:
        return argv
