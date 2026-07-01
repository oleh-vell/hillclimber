"""Shared Rich consoles for the CLI.

Two streams, deliberately split so the CLI is scriptable:

- ``console`` writes *results* to stdout (rendered summaries, ``--json`` payloads).
- ``err_console`` writes *diagnostics* to stderr (errors, interrupts, warnings).

Keeping results on stdout and everything else on stderr means a caller can pipe
``hillclimber run --json ... > out.json`` and get a clean payload. Rich also
auto-detects a non-TTY and drops styling, so redirected output stays plain.
"""

from __future__ import annotations

from rich.console import Console

console = Console()
err_console = Console(stderr=True)
