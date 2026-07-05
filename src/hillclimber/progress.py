"""Run-level progress events.

The coarse counterpart to the harness trace stream: a ``TraceEvent`` narrates
one *agent's* steps (a tool call, a thought), while a ``RunEvent`` narrates the
*run* — baseline scored, models verified, a cycle starting, moving through its
stages, and landing on a score. A consumer that wants to show "where the climb
is" (the CLI's live dashboard) listens here; one that wants to watch an agent
work listens to traces. Both channels are optional taps: the core also narrates
itself through ordinary logs, so with no sink attached nothing is lost.

Like ``TraceSink``, a ``RunEventSink`` is deliberately *sync*: it forwards
(mutates a view, prints a line) and must never block the event loop.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

# The stages a cycle moves through, in order (see ``strategies.chain``): the
# orchestrator agent proposes, the worker applies, the scorer measures.
CycleStage = Literal["proposing", "applying", "scoring"]


class RunEvent(BaseModel):
    """One run-level milestone or stage change, pushed as the climb progresses.

    ``kind`` is the small fixed vocabulary a consumer switches on; ``message``
    is the ready-made human line so a minimal consumer can just print it. The
    optional fields carry the structure a richer view (the CLI dashboard) folds
    into its state — which cycle, what score, how it moved.
    """

    kind: Literal[
        "run_start",
        "baseline_start",
        "baseline_done",
        "preflight_start",
        "preflight_done",
        "cycle_start",
        "cycle_stage",
        "cycle_done",
    ]
    message: str  # one human-readable line, e.g. "cycle 2/5: applying the hypothesis"
    index: int | None = None  # 1-based cycle number, on cycle_* events
    total: int | None = None  # the budget's cycle count, on cycle_* events
    stage: CycleStage | None = None  # on cycle_stage events
    score: float | None = None  # on baseline_done / cycle_done
    delta: float | None = None  # cycle_done: score movement vs. the parent cycle
    hypothesis: str | None = None  # on cycle_stage (applying) / cycle_done


# Where run events land. ``None`` everywhere means "no narration" — the logs
# already tell the same story, so the default sink is silent, not a logger.
RunEventSink = Callable[[RunEvent], None]


def ignore_progress(event: RunEvent) -> None:
    """The default sink: drop the event.

    Unlike traces (whose default sink logs, see ``strategies.base.log_trace``),
    every run milestone already has an INFO log line at its emission site — a
    logging default here would just double-print them.
    """
