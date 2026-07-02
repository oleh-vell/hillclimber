"""The HILLCLIMBER wordmark for the interactive wizard.

Both banners were rasterized once from the project's Gameplay pixel font
(``fe/src/fonts/gameplay.ttf``) into half-block characters, so the terminal
wordmark matches the web frontend without a font or imaging dependency at
runtime. Two sizes are embedded: the wide one is truer to the font, the
narrow one fits an 80-column terminal.
"""

from __future__ import annotations

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


def print_banner(console: Console) -> None:
    """Print the wordmark sized to the terminal, followed by a blank line."""
    banner = BANNER_WIDE if console.width > _WIDE_COLUMNS else BANNER_NARROW
    console.print(banner, style="bold green", highlight=False)
    console.print()
