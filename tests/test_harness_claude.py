import asyncio

import pytest

# Import the package first so it fully initialises (mirrors test_strategy_base).
import hillclimber  # noqa: F401
from harnesses import ClaudeHarness, get_harness
from harnesses.base import HarnessError
from harnesses.claude import HarnessRun, _build_command, _build_verify_command, run
from hillclimber.models import Agent, Budget, CommandScorer, Config
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
