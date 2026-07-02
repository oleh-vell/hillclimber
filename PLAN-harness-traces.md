# Plan: streaming traces from harnesses

## Goal

Today a strategy calls `await self.harness.run(...)` and blocks silently until the agent
finishes — minutes with zero visibility. The goal is for a harness to **yield trace events as
the agent works** (thinking, which tool it ran, which file it opened) and then hand back the
final reply, exactly as now. v1 consumer: the strategy logs traces through the existing
`telemetry` logger. Later consumer: the rich CLI renders them live. The design below keeps one
generic pattern so any future harness (API harness, codex, etc.) plugs into the same seam.

## Current shape (what has to change)

- `harnesses/_proc.exec_agent` buffers the whole child output via `proc.communicate()` —
  nothing can be observed until exit.
- `harnesses/claude.py` runs `claude --print --output-format json`, one JSON envelope at the
  end; `run()` returns `payload["result"]`.
- `Harness.run(harness_run) -> str` is the only contract; `strategies/chain.py` awaits it in
  `_propose_hypothesis` / `_apply_hypothesis`.

Verified against the installed CLI (2.1.170): `--output-format stream-json` emits realtime
NDJSON in `--print` mode and **requires `--verbose`**; `--include-partial-messages` exists for
token-level deltas (not needed for v1).

## Design

### 1. Generic trace vocabulary — `harnesses/base.py`

A small, harness-agnostic event model. Every harness translates its native protocol into this;
consumers (strategy logging now, CLI rendering later) only ever see this shape.

```python
class TraceKind(StrEnum):
    init = "init"              # agent session started (model, tools, cwd)
    thinking = "thinking"      # extended-thinking snippet
    text = "text"              # intermediate assistant text
    tool_use = "tool_use"      # agent invoked a tool (Read/Bash/Edit...)
    tool_result = "tool_result"  # tool came back (ok/error, size)
    result = "result"          # terminal event; carries the final reply

class TraceEvent(BaseModel):
    kind: TraceKind
    summary: str               # one human-readable line, ready to log/display
    text: str | None = None    # final reply, set only when kind == result
    raw: dict[str, Any] | None = None  # harness-native payload, for rich consumers
```

Design rules that keep it general:
- `summary` is always renderable as-is — a consumer needs zero harness knowledge to show
  something useful ("Read: src/eval.py", "Bash: git commit -m ...", "thinking: The scorer
  penalises...").
- `raw` preserves the native event for consumers that want more (the future CLI can show tool
  inputs, token counts). Nothing generic ever depends on its shape.
- Exactly one `result` event, always last. Errors don't get an event kind: the stream raises,
  same as `run()` does today.

### 2. Harness contract — `stream()` is the primitive, `run()` comes free

```python
class Harness(ABC):
    @abstractmethod
    def stream(self, harness_run: HarnessRun) -> AsyncIterator[TraceEvent]:
        """Yield trace events as the agent works; the last event is kind=result."""

    async def run(self, harness_run: HarnessRun) -> str:
        """Drain stream() and return the final reply (concrete, shared)."""
        async for event in self.stream(harness_run):
            if event.kind is TraceKind.result and event.text is not None:
                return event.text
        raise RuntimeError("agent stream ended without a result event")
```

Why this shape:
- **One method to implement per harness.** A streaming backend yields as it goes; a
  non-streaming backend (e.g. a plain API call) yields a single `result` event — still
  conforms, zero extra work.
- `run()` stays on the interface, so existing call sites and tests keep working; callers that
  don't care about traces are untouched.
- An async iterator (vs. a callback argument) composes naturally with asyncio, supports
  cancellation for free (closing the generator kills the child — see §4), and lets the
  consumer decide pacing.

### 3. Trace sink — the consumer seam (v1: logging)

The strategy shouldn't hardcode *what happens* to a trace, only that traces flow. One tiny
protocol in `harnesses/base.py`:

```python
class TraceSink(Protocol):
    def emit(self, role: str, event: TraceEvent) -> None: ...

class LoggingTraceSink:                # v1 default
    def emit(self, role, event):       # logger.info("[worker] Read: src/eval.py")
        logger.info("[%s] %s", role, event.summary)
```

`Strategy.__init__` grows `sink: TraceSink | None = None`, defaulting to `LoggingTraceSink`.
Later, the CLI passes a rich-rendering sink down through `hillclimber.run` → `get_strategy` —
that's the only future plumbing needed; harnesses and strategies won't change again.
`emit` is sync and must be non-blocking (a log call, a queue put); anything slow belongs
behind a queue the sink owns.

New helper on `Strategy` (in `strategies/base.py`), which becomes the one way strategies drive
agents:

```python
async def _run_agent(self, harness_run: HarnessRun, *, role: str) -> str:
    async for event in self.harness.stream(harness_run):
        if event.kind is TraceKind.result and event.text is not None:
            return event.text
        self.sink.emit(role, event)
    raise RuntimeError(f"{role} agent stream ended without a result")
```

`chain.py` changes are two lines: `_propose_hypothesis` → `self._run_agent(..., role="hillclimber")`,
`_apply_hypothesis` → `self._run_agent(..., role="worker")`.

### 4. Streaming subprocess — `harnesses/_proc.py`

`exec_agent` stays (verify probes still want buffered output). Add a sibling through the same
sandbox chokepoint:

```python
@dataclass(frozen=True)
class ProcExit:
    returncode: int
    stderr: bytes

async def stream_agent(argv, cwd, sandbox) -> AsyncIterator[bytes | ProcExit]:
    """Yield stdout lines as they arrive, then exactly one ProcExit."""
```

Implementation notes:
- Same `realpath` + `sandbox.wrap` treatment as `exec_agent`.
- stderr is drained concurrently into a buffer (an `asyncio` task) — never left unread, or a
  chatty child deadlocks on a full pipe.
- `try/finally` around the yield loop: if the consumer stops early (cancellation, Ctrl-C, an
  exception upstream), the child is killed and reaped. Today's `asyncio.run` Ctrl-C path in
  the CLI then cleans up the `claude` process instead of leaking it.
- Lines longer than the `StreamReader` default limit (64 KiB — a big tool result in one NDJSON
  line will exceed it) are handled by raising the limit on the reader (e.g. 16 MiB), matching
  what the buffered path tolerated implicitly.

### 5. Claude harness — `harnesses/claude.py`

- `_build_command`: `--output-format json` → `--output-format stream-json --verbose`
  (CLI-enforced pairing, verified above). Everything else (`--print`,
  `--dangerously-skip-permissions`, `--system-prompt`, `--` terminator) unchanged.
- New **pure** parser, unit-testable without a subprocess, mirroring `_build_command`:

  ```python
  def _parse_stream_line(line: bytes) -> list[TraceEvent]:
  ```

  Mapping from the CLI's NDJSON events:
  | CLI event | TraceEvent |
  | --- | --- |
  | `{"type":"system","subtype":"init",...}` | `init` — "claude started (model X)" |
  | `{"type":"assistant"}` content block `thinking` | `thinking` — first ~120 chars |
  | `{"type":"assistant"}` content block `text` | `text` — first ~120 chars |
  | `{"type":"assistant"}` content block `tool_use` | `tool_use` — "Read: <file_path>", "Bash: <command>" (name + the salient input field, truncated) |
  | `{"type":"user"}` tool result | `tool_result` — "tool result: ok, 2.1 KB" / "tool result: error" |
  | `{"type":"result"}` | `result` — `text=payload["result"]`; `is_error` handled by the caller |
  | anything unrecognised / unparsable line | **skipped** (returns `[]`) — a CLI format drift must degrade traces, never crash a climb |

  One assistant message can carry several content blocks, hence `list[TraceEvent]`.

- Module-level `stream(harness_run, sandbox)` async generator replaces the body of the
  module-level `run()`:
  - iterate `stream_agent(_build_command(run), run.path, sandbox)`;
  - yield parsed events as they come; remember the `result` payload;
  - on `ProcExit`: non-zero returncode → `RuntimeError(stderr)` (as today); zero returncode
    with `is_error` in the result payload → `RuntimeError` (as today); zero returncode with no
    result event seen → `RuntimeError("claude produced no result event")` (replaces today's
    "unparsable output" case); otherwise yield the final `result` event.
- Module-level `run()` stays as a thin drain of `stream()` so its unit tests and docstring
  contract survive; `ClaudeHarness.stream`/`.run` adapt both, passing `self.sandbox`.
- `verify_model` / `_build_verify_command`: **unchanged** — the probe keeps the single-envelope
  `--output-format json`, buffered `exec_agent`.

### 6. Tests

- `_build_command` asserts `stream-json` + `--verbose` are present (update existing test).
- `_parse_stream_line`: table-driven pure tests — init, thinking, tool_use (Read/Bash input
  summarisation), tool_result, result, multi-block assistant message, unknown type, garbage
  bytes.
- `_proc.stream_agent`: fake process whose `stdout` is a real `asyncio.StreamReader` fed
  NDJSON lines; assert line-by-line yield order, final `ProcExit`, stderr capture, and that
  early generator close kills the child.
- `claude.stream` / `run`: same fake-proc monkeypatch style as today's `_FakeProc`, feeding a
  scripted NDJSON transcript; assert event sequence, final text, and the three error paths.
- `Strategy._run_agent`: fake harness with a scripted `stream()`; assert the sink received
  every non-result event with the right role and the final text is returned; `caplog` check on
  the default `LoggingTraceSink`.
- Existing chain/strategy tests keep passing — fakes that only implemented `run()` gain a
  one-line `stream()` (or switch to yielding a single result event).

All per CLAUDE.md: asyncio throughout, `asyncio.run(...)` in tests, ruff + ty clean.

## Later (out of scope now, but this design anticipates it)

- **CLI live view**: `cli/commands/run.py` builds a Rich sink (Live/status panel per role
  showing the latest `summary`, scrollback of tool calls) and threads it through
  `hillclimber.run` → strategy ctor. Only plumbing; no harness/strategy logic changes.
- **Token-level streaming**: add `--include-partial-messages` and map `stream_event` deltas to
  a new `TraceKind.delta` when the CLI view wants typewriter output.
- **Per-agent harnesses**: `stream()` being the single abstract method keeps the cost of each
  new harness at "translate your native events into TraceEvent".
- **OTEL**: trace events already flow through the logging sink, so OTLP export of traces works
  on day one via the existing `telemetry` bridge.

## Execution order

1. `harnesses/base.py`: `TraceKind`, `TraceEvent`, `TraceSink`, `LoggingTraceSink`, new
   `Harness.stream` abstract + concrete `run`.
2. `harnesses/_proc.py`: `ProcExit` + `stream_agent` (with kill-on-close, stderr drain).
3. `harnesses/claude.py`: command change, `_parse_stream_line`, module-level `stream`, rewire
   `run` and `ClaudeHarness`.
4. `strategies/base.py` + `chain.py`: sink on ctor, `_run_agent`, two call-site swaps.
5. Tests for each layer; full `ruff check`, `ruff format --check`, `ty check`, `pytest`.
