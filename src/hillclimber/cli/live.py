"""The live dashboard for ``hillclimber run``.

A climb can run for hours, so the display has to answer "what is going on?" at
a glance without drowning the user in agent chatter. It does that with three
layers, quietest at the bottom:

- **Milestones** — one persistent line per event that matters (baseline scored,
  models verified, each cycle's hypothesis and score). Printed above the live
  region, they accumulate into a readable history of the climb.
- **Header** — a spinner line with where the run is (cycle N/M, current stage)
  and the numbers that matter (baseline, best so far, elapsed).
- **Trace tail** — the last few agent trace events, dim, replaced as new ones
  arrive and cleared at each stage change. Enough to see the agent is alive and
  what it is touching; never a scrollback flood. The whole live region is
  transient — it vanishes when the run ends, leaving milestones + summary.

The dashboard is fed by the core's two narration channels (``TraceSink`` and
``RunEventSink``) and is a pure consumer: it never reaches back into the run.
While it is active it also takes over the project's log handlers, so INFO
chatter doesn't tear the display — WARNING and above surface as milestone
lines instead of being lost.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from types import TracebackType

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from hillclimber.harnesses import TraceEvent
from hillclimber.progress import RunEvent
from hillclimber.telemetry import PACKAGE_LOGGERS

# How many trace lines the tail keeps. Four is enough to see what the agent is
# doing right now without turning the region into a wall of text.
_TRACE_TAIL = 4

# Tail line style per trace kind: tool calls pop slightly (they are the "agent
# is acting" signal); everything else stays uniformly dim.
_TRACE_STYLES = {
    "tool_use": "dim cyan",
    "thinking": "dim italic",
}
_TRACE_DEFAULT_STYLE = "dim"


def _format_elapsed(seconds: float) -> str:
    """Render elapsed wall time as ``m:ss`` (or ``h:mm:ss`` past an hour)."""
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class _MilestoneLogHandler(logging.Handler):
    """Route WARNING+ log records into the dashboard's milestone lines.

    While the live view owns the terminal, raw stderr log lines would tear the
    display; anything important enough to warn about becomes a persistent line
    above the live region instead.
    """

    def __init__(self, dashboard: RunDashboard) -> None:
        super().__init__(level=logging.WARNING)
        self._dashboard = dashboard

    def emit(self, record: logging.LogRecord) -> None:
        text = Text("⚠ ", style="yellow")
        text.append(record.getMessage(), style="yellow")
        self._dashboard.note(text)


class RunDashboard:
    """Live view of a running climb; also the run's trace/progress sinks.

    Use as a context manager around the run itself::

        with RunDashboard(console) as dashboard:
            status = asyncio.run(run(path, dashboard.on_trace, dashboard.on_progress))

    The sinks are called on the event loop thread and only mutate plain
    attributes / print a line; the ``Live`` refresh thread re-renders from that
    state on its own clock (which also keeps the elapsed timer ticking between
    events).
    """

    def __init__(self, console: Console, trace_tail: int = _TRACE_TAIL) -> None:
        self._console = console
        self._traces: deque[TraceEvent] = deque(maxlen=trace_tail)
        self._started = time.monotonic()
        self._activity = "loading experiment"
        self._cycle: int | None = None
        self._total: int | None = None
        self._baseline: float | None = None
        self._best: float | None = None
        self._hypothesis: str | None = None
        self._live = Live(
            console=console,
            get_renderable=self._render,
            refresh_per_second=8,
            transient=True,
        )
        self._log_handler = _MilestoneLogHandler(self)
        self._saved_handlers: list[tuple[logging.Logger, list[logging.Handler]]] = []

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    def __enter__(self) -> RunDashboard:
        self._started = time.monotonic()
        self._take_over_logging()
        try:
            self._live.start()
        except BaseException:
            # A failure starting the live region must not leave the project's
            # loggers redirected for the rest of the process.
            self._restore_logging()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Stop the live region first (transient: it clears itself), then give
        # the loggers back so post-run output flows normally again. The restore
        # runs even if ``stop`` throws, so the takeover is never left in place.
        try:
            self._live.stop()
        finally:
            self._restore_logging()

    def _take_over_logging(self) -> None:
        """Quiet the console log handlers for the run, without dropping the rest.

        Only the console handlers (raw stderr ``StreamHandler``s) would tear the
        live display, so those are removed for the duration and the dashboard's
        own milestone handler is added in their place. Everything else stays —
        crucially the OTEL export handler ``configure_logging`` may have
        installed, whose telemetry must keep flowing on a real run. The removed
        console handlers are restored verbatim by ``_restore_logging`` on exit.
        """
        for name in PACKAGE_LOGGERS:
            package_logger = logging.getLogger(name)
            original = package_logger.handlers[:]
            self._saved_handlers.append((package_logger, original))
            # Keep everything except the console handlers (raw stderr, which would
            # tear the live region); the dashboard's milestone handler replaces
            # them. ``FileHandler`` is a ``StreamHandler`` subclass but writes to
            # a file, so it (and OTEL export, NullHandler, ...) is kept.
            kept = [
                handler
                for handler in original
                if not (isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler))
            ]
            package_logger.handlers = [*kept, self._log_handler]

    def _restore_logging(self) -> None:
        # Restore the exact original handler list (order included) so the takeover
        # leaves the loggers precisely as it found them.
        for package_logger, original in self._saved_handlers:
            package_logger.handlers = original
        self._saved_handlers.clear()

    # ------------------------------------------------------------------ #
    # sinks (fed by the core)
    # ------------------------------------------------------------------ #

    def on_trace(self, event: TraceEvent) -> None:
        """``TraceSink``: append to the dim tail (bounded by the deque)."""
        self._traces.append(event)

    def on_progress(self, event: RunEvent) -> None:
        """``RunEventSink``: fold the milestone into header state and history."""
        if event.kind == "run_start":
            # The run's opening statement — one persistent line above the whole
            # climb; the header shows real activity from the next event on.
            self._activity = "starting"
            text = Text("▶ ", style="cyan")
            text.append(event.message, style="bold")
            self.note(text)
            return
        self._activity = event.message
        if event.index is not None:
            self._cycle, self._total = event.index, event.total

        if event.kind == "baseline_done" and event.score is not None:
            self._baseline = event.score
            self._note_check(f"baseline {event.score:.3f}")
        elif event.kind == "preflight_done":
            self._note_check("models verified")
        elif event.kind == "cycle_start":
            self._hypothesis = None
            self._traces.clear()
        elif event.kind == "cycle_stage":
            # A new stage means a new agent (or the scorer): stale trace lines
            # from the previous one would just mislead.
            self._traces.clear()
            if event.hypothesis is not None:
                self._hypothesis = event.hypothesis
                self._note_hypothesis(event)
        elif event.kind == "cycle_done":
            # The header already says which cycle this is; repeating the index
            # from the message would read "cycle 2/2 — cycle 2 scored ...".
            self._activity = f"scored {event.score:.3f}" if event.score is not None else "produced no score"
            if event.score is not None and (self._best is None or event.score > self._best):
                self._best = event.score
            self._note_cycle_done(event)

    # ------------------------------------------------------------------ #
    # persistent milestone lines
    # ------------------------------------------------------------------ #

    def note(self, text: Text) -> None:
        """Print one persistent milestone above the live region.

        Milestones are scrollback history, so they wrap rather than ellipsize —
        a long hypothesis stays complete (and copyable) after the run. Only the
        live region itself keeps to one line per entry.
        """
        self._console.print(text)

    def _note_check(self, message: str) -> None:
        text = Text("✓ ", style="green")
        text.append(message)
        self.note(text)

    def _note_hypothesis(self, event: RunEvent) -> None:
        text = Text("◆ ", style="cyan")
        text.append(f"cycle {event.index:03d}: " if event.index is not None else "cycle: ", style="bold")
        text.append(event.hypothesis or "", style="italic")
        self.note(text)

    def _note_cycle_done(self, event: RunEvent) -> None:
        if event.score is None:
            text = Text("• ", style="dim")
            text.append(event.message, style="dim")
            self.note(text)
            return
        delta = event.parent_delta or 0.0
        glyph, style = ("▲", "green") if delta > 0 else ("▼", "red") if delta < 0 else ("•", "dim")
        text = Text(f"{glyph} ", style=style)
        text.append(f"cycle {event.index:03d} scored " if event.index is not None else "scored ")
        text.append(f"{event.score:.3f}", style="bold")
        text.append(f" ({delta:+.3f})", style=style)
        self.note(text)

    # ------------------------------------------------------------------ #
    # the live region
    # ------------------------------------------------------------------ #

    def _render(self) -> RenderableType:
        lines: list[RenderableType] = [self._render_header()]

        if self._baseline is not None:
            scores = Text("  ")
            scores.append(f"baseline {self._baseline:.3f}", style="cyan")
            if self._best is not None:
                style = "green" if self._best > self._baseline else "dim"
                scores.append("  ·  ")
                scores.append(f"best {self._best:.3f}", style=style)
            lines.append(scores)

        if self._hypothesis:
            hypothesis = Text(f'  "{self._hypothesis}"', style="italic", no_wrap=True, overflow="ellipsis")
            lines.append(hypothesis)

        tail: list[RenderableType] = []
        for event in list(self._traces):
            trace = Text("  │ ", style="dim")
            style = _TRACE_STYLES.get(event.kind, _TRACE_DEFAULT_STYLE)
            if event.kind == "tool_result" and event.raw.get("is_error"):
                # A failed tool call is the one tail line worth spotting.
                style = "dim red"
            trace.append(event.summary, style=style)
            trace.no_wrap = True
            trace.overflow = "ellipsis"
            tail.append(trace)
        if not tail and self._cycle is not None:
            # A stage just changed and its agent hasn't streamed yet: keep one
            # dim placeholder so the region still reads as "working".
            tail.append(Text("  │ …", style="dim"))
        if tail:
            # Blank lines above and below so the tail breathes instead of
            # butting against the header and the bottom of the terminal.
            lines.append(Text())
            lines.extend(tail)
            lines.append(Text())

        return Group(*lines)

    def _render_header(self) -> RenderableType:
        where = f"cycle {self._cycle}/{self._total} — " if self._cycle is not None else ""
        status = Text(f"{where}{self._activity}", style="bold")
        header = Table.grid(expand=True)
        header.add_column(ratio=1)
        header.add_column(justify="right")
        header.add_row(
            Spinner("dots", text=status, style="cyan"),
            Text(_format_elapsed(time.monotonic() - self._started), style="dim"),
        )
        return header
