"""CLI flavor: the HILLCLIMBER wordmark and the run's opening phrases.

Both banners were rasterized once from the project's Gameplay pixel font
(``fe/src/fonts/gameplay.ttf``) into half-block characters, so the terminal
wordmark matches the web frontend without a font or imaging dependency at
runtime. Two sizes are embedded: the wide one is truer to the font, the
narrow one fits an 80-column terminal.
"""

from __future__ import annotations

import random

from rich.console import Console

# Gameplay @ 12px, 88 columns.
BANNER_WIDE = """\
██    ██ ██ ██       ██       ▄██████▄ ██       ██ ██▄    ▄██ ███████▄ ▄███████ ███████▄
██    ██ ██ ██       ██       ██    ▀▀ ██       ██ ████  ████ ██    ██ ██       ██    ██
████████ ██ ██       ██       ██       ██       ██ ██▀██▄█▀██ ███████▀ ██████   ███████▀
██    ██ ██ ██       ██       ██       ██       ██ ██  ▀█  ██ ██    ██ ██       ██    ██
██    ██ ██ ██▄▄▄▄▄▄ ██▄▄▄▄▄▄ ██▄▄▄▄██ ██▄▄▄▄▄▄ ██ ██      ██ ██▄▄▄▄██ ██▄▄▄▄▄▄ ██    ██
▀▀    ▀▀ ▀▀  ▀▀▀▀▀▀▀  ▀▀▀▀▀▀▀  ▀▀▀▀▀▀   ▀▀▀▀▀▀▀ ▀▀ ▀▀      ▀▀ ▀▀▀▀▀▀▀   ▀▀▀▀▀▀▀ ▀▀    ▀▀"""

# Gameplay @ 8px, 69 columns.
BANNER_NARROW = """\
██  ██ ██ ██     ██     ▄████▄ ██     ██ ██  ▄██ █████▄ ▄█████ █████▄
██▄▄██ ██ ██     ██     ██  ▀▀ ██     ██ ███▄███ ██▄▄██ ██▄▄▄  ██▄▄██
██▀▀██ ██ ██     ██     ██     ██     ██ ████▀██ ██▀▀██ ██▀▀▀  ██▀▀██
██  ██ ██ ▀█████ ▀█████ ██████ ▀█████ ██ ██   ██ █████▀ ▀█████ ██  ██"""

_WIDE_COLUMNS = 88

# One of these opens every interactive ``hillclimber run`` — a wink before the
# climb starts. Keep them short and lowercase: they render as one dim line.
RUN_PHRASES = (
    "lacing up the boots…",
    "one hill to climb",
    "starting the ascent",
    "chalking up…",
    "the summit won't come to us",
    "checking the weather at base camp",
    "every score is a foothold",
    "up and to the right",
    "first, the trailhead",
    "altitude is just a number",
)


def run_phrase() -> str:
    """A random opening phrase for ``hillclimber run``."""
    return random.choice(RUN_PHRASES)


def print_banner(console: Console) -> None:
    """Print the wordmark sized to the terminal, followed by a blank line."""
    banner = BANNER_WIDE if console.width >= _WIDE_COLUMNS else BANNER_NARROW
    # "bold default" uses the terminal's own foreground colour, so the wordmark
    # renders black on light themes and white on dark themes.
    console.print(banner, style="bold default", highlight=False)
    console.print()
