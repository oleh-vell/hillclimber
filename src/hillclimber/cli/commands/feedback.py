"""``hillclimber feedback`` — send feedback straight to the author.

Two modes, one command: ``hillclimber feedback "..."`` fires the message off
immediately, while a bare ``hillclimber feedback`` prompts for it first. Either
way the text is POSTed to the hillclimber feedback endpoint (which relays it to
Telegram), so giving feedback costs one line in the terminal — no issue tracker,
no login.

The endpoint is baked in but overridable via ``HILLCLIMBER_FEEDBACK_URL`` (which
is also how tests stay hermetic). The POST itself is stdlib ``urllib`` offloaded
to a thread — one call per invocation does not justify an HTTP client dependency.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Annotated

import typer

from hillclimber.cli import render
from hillclimber.cli.console import can_prompt, console
from hillclimber.cli.state import CLIState

# The live endpoint (the hillclimber.dev API route, which relays to Telegram).
# HILLCLIMBER_FEEDBACK_URL overrides it for tests and self-hosting.
DEFAULT_FEEDBACK_URL = "https://hillclimber.dev/api/feedback"

# Mirrors MAX_MESSAGE_LENGTH in the API route; rejecting locally saves a round
# trip that would come back 413 anyway.
MAX_MESSAGE_LENGTH = 2000

_TIMEOUT_S = 10.0


def _feedback_url() -> str:
    return os.environ.get("HILLCLIMBER_FEEDBACK_URL", DEFAULT_FEEDBACK_URL)


def _server_error(body: bytes) -> str | None:
    """Extract the API's ``{"error": ...}`` message from an error response, if any."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("error"), str):
        return payload["error"]
    return None


async def _send_feedback(message: str) -> None:
    """POST the message to the feedback endpoint; raise ``ValueError`` on failure.

    ``urllib`` is blocking, so the request runs in a thread (see CLAUDE.md
    "Concurrency") — the coroutine shape keeps this callable from async code.
    """

    def _post() -> None:
        request = urllib.request.Request(
            _feedback_url(),
            data=json.dumps({"message": message}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_S):
                pass
        except urllib.error.HTTPError as exc:
            detail = _server_error(exc.read()) or f"server responded {exc.code}"
            raise ValueError(detail) from exc
        except OSError as exc:  # URLError, timeouts, refused connections
            raise ValueError(f"could not reach {_feedback_url()} ({exc})") from exc

    await asyncio.to_thread(_post)


def feedback(
    ctx: typer.Context,
    message: Annotated[
        str | None,
        typer.Argument(help="The feedback to send. Omit it to be prompted interactively."),
    ] = None,
) -> None:
    """Send your feedback"""
    state: CLIState = ctx.obj

    if message is None:
        # Prompting needs a real interactive session; in --json mode or piped
        # output the prompt would pollute the machine-readable stream (or die
        # with a raw Aborted in CI), so fail with the fix instead.
        if not can_prompt(state):
            render.fail(state, 'no message given; pass it as an argument: hillclimber feedback "..."')
        message = typer.prompt("What feedback would you like to give?")

    message = message.strip()
    if not message:
        render.fail(state, "feedback message is empty")
    if len(message) > MAX_MESSAGE_LENGTH:
        render.fail(state, f"feedback too long ({len(message)} characters, max {MAX_MESSAGE_LENGTH})")

    try:
        asyncio.run(_send_feedback(message))
    except ValueError as exc:
        render.fail(state, f"delivery failed: {exc}")

    if state.json:
        console.print_json(json.dumps({"ok": True}))
        return
    console.print("[green]✓[/] feedback sent — thank you!")
