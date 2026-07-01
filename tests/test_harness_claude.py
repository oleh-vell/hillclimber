import asyncio

import pytest

# Import the package first so it fully initialises (mirrors test_strategy_base).
import hillclimber  # noqa: F401
from harnesses import ClaudeHarness, get_harness
from harnesses.claude import HarnessRun, _build_command, run
from sandboxes import PassthroughSandbox


def test_build_command_includes_required_flags():
    cmd = _build_command(HarnessRun(system_prompt="be terse", path=".", prompt="do it"))

    assert cmd[0] == "claude"
    assert "--print" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert cmd[cmd.index("--system-prompt") + 1] == "be terse"
    assert cmd[cmd.index("--output-format") + 1] == "json"
    # The task prompt is passed positionally after a "--" terminator.
    assert cmd[-2:] == ["--", "do it"]


def test_build_command_omits_model_by_default():
    cmd = _build_command(HarnessRun(system_prompt="sp", path=".", prompt="p"))
    assert "--model" not in cmd


def test_build_command_includes_model_when_set():
    cmd = _build_command(HarnessRun(system_prompt="sp", path=".", prompt="p", model="opus"))
    assert cmd[cmd.index("--model") + 1] == "opus"


class _FakeProc:
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


def test_run_returns_assistant_result(monkeypatch):
    _patch_proc(monkeypatch, _FakeProc(0, b'{"is_error": false, "result": "pong"}'))
    out = asyncio.run(run(HarnessRun(system_prompt="sp", path=".", prompt="ping"), PassthroughSandbox()))
    assert out == "pong"


def test_run_raises_on_nonzero_exit(monkeypatch):
    _patch_proc(monkeypatch, _FakeProc(1, b"", b"boom"))
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(run(HarnessRun(system_prompt="sp", path=".", prompt="ping"), PassthroughSandbox()))


def test_run_raises_on_error_envelope(monkeypatch):
    _patch_proc(monkeypatch, _FakeProc(0, b'{"is_error": true, "result": "nope"}'))
    with pytest.raises(RuntimeError, match="nope"):
        asyncio.run(run(HarnessRun(system_prompt="sp", path=".", prompt="ping"), PassthroughSandbox()))


def test_run_raises_on_unparsable_output(monkeypatch):
    _patch_proc(monkeypatch, _FakeProc(0, b"not json"))
    with pytest.raises(RuntimeError, match="unparsable"):
        asyncio.run(run(HarnessRun(system_prompt="sp", path=".", prompt="ping"), PassthroughSandbox()))


def test_get_harness_returns_claude():
    assert isinstance(get_harness("claude", PassthroughSandbox()), ClaudeHarness)
    assert isinstance(get_harness("claude_code", PassthroughSandbox()), ClaudeHarness)


def test_get_harness_rejects_unknown():
    with pytest.raises(ValueError, match="unknown harness"):
        get_harness("gpt", PassthroughSandbox())


def test_claude_harness_delegates_to_module_run(monkeypatch):
    _patch_proc(monkeypatch, _FakeProc(0, b'{"is_error": false, "result": "pong"}'))
    harness = ClaudeHarness(PassthroughSandbox())
    out = asyncio.run(harness.run(HarnessRun(system_prompt="sp", path=".", prompt="ping")))
    assert out == "pong"
