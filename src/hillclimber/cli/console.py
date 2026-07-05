"""Shared Rich consoles for the CLI.

Two streams, deliberately split so the CLI is scriptable:

- ``console`` writes *results* to stdout (rendered summaries, ``--json`` payloads).
- ``err_console`` writes *diagnostics* to stderr (errors, interrupts, warnings).

Keeping results on stdout and everything else on stderr means a caller can pipe
``hillclimber run --json ... > out.json`` and get a clean payload. Rich also
auto-detects a non-TTY and drops styling, so redirected output stays plain.
"""

from __future__ import annotations

import sys

from rich.console import Console

from hillclimber.cli.state import CLIState

console = Console()
err_console = Console(stderr=True)


def can_prompt(state: CLIState) -> bool:
    """Whether an interactive question can actually be asked.

    Needs a real interactive session: a terminal on both ends and no ``--json``
    (whose stdout must stay machine-clean). Everything else — CI, piped output —
    should fail with a hint instead of hanging on a prompt nobody will answer.
    """
    return not state.json and console.is_terminal and sys.stdin.isatty()
