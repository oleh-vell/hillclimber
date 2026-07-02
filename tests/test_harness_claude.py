import asyncio
import json
import os
from collections.abc import Sequence

import pytest

# Import the package first so it fully initialises (mirrors test_strategy_base).
import hillclimber  # noqa: F401
from harnesses import ClaudeHarness, Harness, TraceEvent, get_harness
from harnesses.base import HarnessError
from harnesses.claude import HarnessRun, _build_command, _build_verify_command, _parse_trace_line, run
from hillclimber.models import Agent, Budget, CommandScorer, Config
from sandboxes import PassthroughSandbox


def test_build_command_includes_required_flags():
    cmd = _build_command(HarnessRun(system_prompt="be terse", path=".", prompt="do it"))

    assert cmd[0] == "claude"
    assert "--print" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert cmd[cmd.index("--system-prompt") + 1] == "be terse"
    # Runs stream NDJSON trace events; the CLI requires --verbose with it.
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in cmd
    # The task prompt is passed positionally after a "--" terminator.
    assert cmd[-2:] == ["--", "do it"]


def test_build_command_omits_model_by_default():
    cmd = _build_command(HarnessRun(system_prompt="sp", path=".", prompt="p"))
    assert "--model" not in cmd


def test_build_command_includes_model_when_set():
    cmd = _build_command(HarnessRun(system_prompt="sp", path=".", prompt="p", model="opus"))
    assert cmd[cmd.index("--model") + 1] == "opus"


class _FakeProc:
    """A buffered fake for the ``exec_agent`` path (verify probes)."""

    def __init__(self, returncode: int, stdout: bytes, stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


def _patch_proc(monkeypatch, proc: _FakeProc):
    async def _fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)


def _patch_stream_proc(monkeypatch, lines: list[bytes], returncode: int = 0, stderr: bytes = b""):
    """Patch subprocess creation with a fake whose stdout streams ``lines``.

    The readers are built inside the (async) factory so they bind to the running
    loop, mirroring how ``stream_exec_agent`` reads a real child's pipes.
    """

    async def _fake_exec(*args, **kwargs):
        class _FakeStreamProc:
            def __init__(self):
                self.stdout = asyncio.StreamReader()
                for line in lines:
                    self.stdout.feed_data(line + b"\n")
                self.stdout.feed_eof()
                self.stderr = asyncio.StreamReader()
                if stderr:
                    self.stderr.feed_data(stderr)
                self.stderr.feed_eof()
                self.returncode = returncode

            async def wait(self) -> int:
                return self.returncode

        return _FakeStreamProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)


_RESULT_LINE = b'{"type": "result", "is_error": false, "result": "pong"}'


def test_run_returns_assistant_result(monkeypatch):
    _patch_stream_proc(monkeypatch, [_RESULT_LINE])
    out = asyncio.run(run(HarnessRun(system_prompt="sp", path=".", prompt="ping"), PassthroughSandbox()))
    assert out == "pong"


def test_run_raises_on_nonzero_exit(monkeypatch):
    _patch_stream_proc(monkeypatch, [], returncode=1, stderr=b"boom")
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(run(HarnessRun(system_prompt="sp", path=".", prompt="ping"), PassthroughSandbox()))


def test_run_raises_on_error_envelope(monkeypatch):
    _patch_stream_proc(monkeypatch, [b'{"type": "result", "is_error": true, "result": "nope"}'])
    with pytest.raises(RuntimeError, match="nope"):
        asyncio.run(run(HarnessRun(system_prompt="sp", path=".", prompt="ping"), PassthroughSandbox()))


def test_run_raises_when_stream_has_no_result_event(monkeypatch):
    # Garbage lines are tolerated (they're just narration), but a stream that
    # ends without a terminal result event has no reply to hand back.
    _patch_stream_proc(monkeypatch, [b"not json", b'{"type": "assistant"}'])
    with pytest.raises(RuntimeError, match="no result event"):
        asyncio.run(run(HarnessRun(system_prompt="sp", path=".", prompt="ping"), PassthroughSandbox()))


def test_run_streams_trace_events_in_order_and_returns_result(monkeypatch):
    _patch_stream_proc(
        monkeypatch,
        [
            b'{"type": "system", "subtype": "init", "model": "claude-opus-4-8"}',
            b'{"type": "assistant", "message": {"content": ['
            b'{"type": "thinking", "thinking": "let me look around"},'
            b'{"type": "tool_use", "name": "Read", "input": {"file_path": "src/pipeline.py"}}]}}',
            b'{"type": "user", "message": {"content": [{"type": "tool_result", "content": "the file body"}]}}',
            b'{"type": "assistant", "message": {"content": [{"type": "text", "text": "found it"}]}}',
            _RESULT_LINE,
        ],
    )
    events: list[TraceEvent] = []

    out = asyncio.run(
        run(HarnessRun(system_prompt="sp", path=".", prompt="ping"), PassthroughSandbox(), on_trace=events.append)
    )

    assert out == "pong"
    assert [e.kind for e in events] == ["init", "thinking", "tool_use", "tool_result", "text", "result"]
    # Summaries carry the "what is the agent doing" line the CLI will render.
    assert "claude-opus-4-8" in events[0].summary
    assert "src/pipeline.py" in events[2].summary


def test_get_harness_returns_claude():
    assert isinstance(get_harness("claude", PassthroughSandbox()), ClaudeHarness)
    assert isinstance(get_harness("claude_code", PassthroughSandbox()), ClaudeHarness)


def test_get_harness_rejects_unknown():
    with pytest.raises(ValueError, match="unknown harness"):
        get_harness("gpt", PassthroughSandbox())


def test_claude_harness_delegates_to_module_run(monkeypatch):
    _patch_stream_proc(monkeypatch, [_RESULT_LINE])
    harness = ClaudeHarness(PassthroughSandbox())
    events: list[TraceEvent] = []
    out = asyncio.run(harness.run(HarnessRun(system_prompt="sp", path=".", prompt="ping"), on_trace=events.append))
    assert out == "pong"
    # The sink is threaded through: the terminal result event reached it too.
    assert [e.kind for e in events] == ["result"]


# --------------------------------------------------------------------------- #
# write_allow (the harness's runtime-state dirs, handed to the sandbox)
# --------------------------------------------------------------------------- #


class _RecordingSandbox(PassthroughSandbox):
    """A passthrough that records the ``write_allow`` handed to each wrap."""

    def __init__(self) -> None:
        self.write_allows: list[Sequence[str]] = []

    def wrap(self, argv: list[str], workdir: str, write_allow: Sequence[str] = ()) -> list[str]:
        self.write_allows.append(write_allow)
        return argv


def test_harness_base_declares_no_write_allow_dirs():
    # The seam's safe default: a harness must opt in to every extra writable dir.
    assert Harness.write_allow == ()


def test_claude_harness_declares_its_session_state_dirs():
    # The Bash tool's session dirs across CLI versions — and never ~/.claude
    # wholesale, whose settings/hooks would let an agent escape the sandbox.
    assert f"/tmp/claude-{os.getuid()}" in ClaudeHarness.write_allow
    assert "~/.claude/session-env" in ClaudeHarness.write_allow
    assert "~/.claude/shell-snapshots" in ClaudeHarness.write_allow
    assert "~/.claude" not in ClaudeHarness.write_allow
    assert "/tmp" not in ClaudeHarness.write_allow


def test_run_hands_the_write_allow_dirs_to_the_sandbox(monkeypatch):
    _patch_stream_proc(monkeypatch, [_RESULT_LINE])
    sandbox = _RecordingSandbox()
    harness = ClaudeHarness(sandbox)

    asyncio.run(harness.run(HarnessRun(system_prompt="sp", path=".", prompt="ping")))

    assert sandbox.write_allows == [ClaudeHarness.write_allow]


def test_verify_model_hands_the_write_allow_dirs_to_the_sandbox(monkeypatch):
    _patch_proc(monkeypatch, _FakeProc(0, b'{"is_error": false, "result": "ok"}'))
    sandbox = _RecordingSandbox()
    harness = ClaudeHarness(sandbox)

    asyncio.run(harness.verify_model("opus"))

    assert sandbox.write_allows == [ClaudeHarness.write_allow]


# --------------------------------------------------------------------------- #
# verify / verify_model
# --------------------------------------------------------------------------- #


def test_build_verify_command_pins_model_and_uses_health_check_prompt():
    cmd = _build_verify_command("opus")

    assert cmd[0] == "claude"
    assert "--print" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--model") + 1] == "opus"
    # A fixed one-token health check, never real work.
    assert "health check" in cmd[cmd.index("--system-prompt") + 1]
    assert cmd[-2] == "--"


def test_verify_model_passes_on_ok_envelope(monkeypatch):
    _patch_proc(monkeypatch, _FakeProc(0, b'{"is_error": false, "result": "ok"}'))
    harness = ClaudeHarness(PassthroughSandbox())
    # No raise == verified.
    asyncio.run(harness.verify_model("opus"))


def test_verify_model_raises_harness_error_on_nonzero_exit(monkeypatch):
    _patch_proc(monkeypatch, _FakeProc(1, b"", b"unknown model"))
    harness = ClaudeHarness(PassthroughSandbox())
    with pytest.raises(HarnessError, match="unknown model"):
        asyncio.run(harness.verify_model("nope"))


def test_verify_model_raises_harness_error_on_error_envelope(monkeypatch):
    _patch_proc(monkeypatch, _FakeProc(0, b'{"is_error": true, "result": "bad model id"}'))
    harness = ClaudeHarness(PassthroughSandbox())
    with pytest.raises(HarnessError, match="bad model id"):
        asyncio.run(harness.verify_model("nope"))


def test_verify_model_raises_harness_error_on_unparsable_output(monkeypatch):
    _patch_proc(monkeypatch, _FakeProc(0, b"not json"))
    harness = ClaudeHarness(PassthroughSandbox())
    with pytest.raises(HarnessError, match="unparsable"):
        asyncio.run(harness.verify_model("opus"))


def _config_with_models(*models: str) -> Config:
    agents = [Agent(harness="claude", model=m) for m in models]
    return Config(
        path_to_artefact=".",
        scorer=CommandScorer(cmd="true"),
        budget=Budget(cycles=1),
        hillclimber_agent=agents[0],
        worker_agent=agents[1],
        reflector_agent=agents[2],
    )


def test_verify_probes_each_distinct_model_once(monkeypatch):
    calls: list[str] = []

    async def _fake_verify_model(self, model: str) -> None:
        calls.append(model)

    monkeypatch.setattr(ClaudeHarness, "verify_model", _fake_verify_model)
    harness = ClaudeHarness(PassthroughSandbox())
    # Two roles share "m"; only the distinct set is probed, order preserved.
    asyncio.run(harness.verify(_config_with_models("m", "m", "other")))
    assert calls == ["m", "other"]


def test_verify_aborts_on_first_failing_model(monkeypatch):
    async def _fake_verify_model(self, model: str) -> None:
        raise HarnessError(f"cannot run {model!r}")

    monkeypatch.setattr(ClaudeHarness, "verify_model", _fake_verify_model)
    harness = ClaudeHarness(PassthroughSandbox())
    with pytest.raises(HarnessError, match="cannot run 'm'"):
        asyncio.run(harness.verify(_config_with_models("m", "m", "m")))


# --------------------------------------------------------------------------- #
# _parse_trace_line (stream-json -> TraceEvent normalization)
# --------------------------------------------------------------------------- #


def test_parse_trace_line_maps_init():
    events = _parse_trace_line(b'{"type": "system", "subtype": "init", "model": "opus"}')
    assert [e.kind for e in events] == ["init"]
    assert "opus" in events[0].summary


def test_parse_trace_line_maps_assistant_blocks():
    line = (
        b'{"type": "assistant", "message": {"content": ['
        b'{"type": "thinking", "thinking": "hmm"},'
        b'{"type": "text", "text": "hello"},'
        b'{"type": "tool_use", "name": "Bash", "input": {"command": "git status"}}]}}'
    )
    events = _parse_trace_line(line)
    assert [e.kind for e in events] == ["thinking", "text", "tool_use"]
    assert events[0].summary == "hmm"
    assert events[1].summary == "hello"
    assert events[2].summary.startswith("Bash(")
    assert "git status" in events[2].summary


def test_parse_trace_line_maps_tool_results():
    line = b'{"type": "user", "message": {"content": [{"type": "tool_result", "content": "42 lines"}]}}'
    events = _parse_trace_line(line)
    assert [e.kind for e in events] == ["tool_result"]
    assert "42 lines" in events[0].summary


def test_parse_trace_line_maps_result_and_keeps_raw_envelope():
    events = _parse_trace_line(b'{"type": "result", "is_error": false, "result": "done"}')
    assert [e.kind for e in events] == ["result"]
    # run() reads the reply and the error flag from the untouched envelope.
    assert events[0].raw["result"] == "done"
    assert events[0].raw["is_error"] is False


def test_parse_trace_line_clips_long_summaries_to_one_line():
    text = "word\n" * 200
    events = _parse_trace_line(
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}).encode()
    )
    assert len(events) == 1
    assert "\n" not in events[0].summary
    assert len(events[0].summary) <= 120


def test_parse_trace_line_tolerates_garbage_and_unknown_events():
    assert _parse_trace_line(b"not json") == []
    assert _parse_trace_line(b'"just a string"') == []
    assert _parse_trace_line(b'{"type": "stream_event"}') == []
    assert _parse_trace_line(b'{"type": "system", "subtype": "compact"}') == []
