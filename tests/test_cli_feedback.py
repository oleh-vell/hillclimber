"""``hillclimber feedback`` behaviour.

Covers both modes (message as an argument, interactive prompt), local
validation (empty and oversized messages never leave the machine), and how
delivery failures surface. The network seam is ``_send_feedback`` — tests
monkeypatch it, so nothing here talks HTTP.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from hillclimber.cli.app import app
from hillclimber.cli.commands import feedback as feedback_cmd

runner = CliRunner()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture messages passed to ``_send_feedback`` instead of POSTing them."""
    messages: list[str] = []

    async def fake_send(message: str) -> None:
        messages.append(message)

    monkeypatch.setattr(feedback_cmd, "_send_feedback", fake_send)
    return messages


def test_immediate_mode_sends_the_argument(sent: list[str]):
    result = runner.invoke(app, ["feedback", "love the tool"])
    assert result.exit_code == 0
    assert sent == ["love the tool"]
    assert "feedback sent" in result.output


def test_interactive_mode_prompts_for_the_message(sent: list[str]):
    result = runner.invoke(app, ["feedback"], input="the chain strategy is great\n")
    assert result.exit_code == 0
    assert "What feedback would you like to give?" in result.output
    assert sent == ["the chain strategy is great"]


def test_message_is_stripped_before_sending(sent: list[str]):
    result = runner.invoke(app, ["feedback", "  padded  "])
    assert result.exit_code == 0
    assert sent == ["padded"]


def test_empty_message_fails_without_sending(sent: list[str]):
    result = runner.invoke(app, ["feedback", "   "])
    assert result.exit_code == 1
    assert sent == []


def test_oversized_message_fails_without_sending(sent: list[str]):
    result = runner.invoke(app, ["feedback", "x" * (feedback_cmd.MAX_MESSAGE_LENGTH + 1)])
    assert result.exit_code == 1
    assert sent == []


def test_delivery_failure_exits_nonzero(monkeypatch: pytest.MonkeyPatch):
    async def failing_send(message: str) -> None:
        raise ValueError("server responded 502")

    monkeypatch.setattr(feedback_cmd, "_send_feedback", failing_send)
    result = runner.invoke(app, ["feedback", "hello"])
    assert result.exit_code == 1
    assert "server responded 502" in result.output


def test_json_mode_emits_machine_readable_result(sent: list[str]):
    result = runner.invoke(app, ["--json", "feedback", "hi"])
    assert result.exit_code == 0
    assert '"ok": true' in result.output


def test_url_defaults_and_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HILLCLIMBER_FEEDBACK_URL", raising=False)
    assert feedback_cmd._feedback_url() == feedback_cmd.DEFAULT_FEEDBACK_URL
    monkeypatch.setenv("HILLCLIMBER_FEEDBACK_URL", "http://localhost:3000/api/feedback")
    assert feedback_cmd._feedback_url() == "http://localhost:3000/api/feedback"
