"""The experiment lock file: the artefact's durable climb history.

``<artefact repo root>/.hillclimber/hillclimber.lock`` is an append-only JSONL
event log — one ``LockEvent`` per line (see ``hillclimber.models``) — recording
every experiment ever run against the artefact. It is never rewritten or
truncated: an experiment appends ``experiment_started``, one ``cycle_recorded``
per settled cycle (the promotion of that cycle's ``cyc_<NNN>.lock``), and a
terminal ``experiment_finished``. A missing terminal line means the experiment
is still running or was interrupted — the reader reports it as such rather
than guessing.

The event *shapes* live in ``hillclimber.models`` (they are write models, like
``Cycle``); this module is the I/O and the fold that reassembles read models
(``ExperimentStatus``) from the log — the seam ``hillclimber status`` builds on.

v1 runs one experiment at a time, so appends are not cross-process locked;
each event is a single ``O_APPEND`` line write.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import TypeAdapter, ValidationError

from hillclimber.git_utils import prune_worktrees, repo_root
from hillclimber.models import (
    Budget,
    CycleRecorded,
    CycleSummary,
    ExperimentFinished,
    ExperimentStarted,
    ExperimentStatus,
    LockEvent,
    Score,
)
from hillclimber.telemetry import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hillclimber.models import Cycle

logger = get_logger(__name__)

LOCK_FILENAME = "hillclimber.lock"

# Validates one JSONL line into the right event via the ``event`` discriminator.
_EVENT_ADAPTER: TypeAdapter[LockEvent] = TypeAdapter(LockEvent)


def lock_path(path_to_artefact: str) -> Path:
    """The artefact's experiment lock path: ``<repo root>/.hillclimber/hillclimber.lock``.

    ``path_to_artefact`` may be a directory or a single-file artefact (see
    ``git_utils.repo_root``) — the lock always sits beside the worktrees under
    ``.hillclimber``, which the dirty-tree check already excludes.
    """
    return repo_root(path_to_artefact) / ".hillclimber" / LOCK_FILENAME


async def append_event(path: Path, event: LockEvent) -> None:
    """Append ``event`` to the lock at ``path`` as one JSON line.

    Creates the parent directory if needed, so the log works on artefacts that
    have no ``.hillclimber`` workspace yet. Append-only by construction: prior
    bytes are never touched. Offloaded with ``asyncio.to_thread`` so the write
    never blocks the event loop.

    Torn-write self-healing: a crash mid-append can leave the file's last line
    without its trailing newline. Appending naively would then splice this
    event onto that partial line, fusing two records into one corrupt line and
    poisoning every later record's line numbering. So if the file does not end
    in a newline, a leading one is written first — the partial line stays alone
    (and is skipped on read) instead of contaminating good records.
    """

    def _write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "\n" if _needs_leading_newline(path) else ""
        with path.open("a", encoding="utf-8") as fh:
            fh.write(prefix + event.model_dump_json() + "\n")

    await asyncio.to_thread(_write)


def _needs_leading_newline(path: Path) -> bool:
    """Whether ``path`` has content whose final byte is not a newline (a torn write)."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return False
    if size == 0:
        return False
    with path.open("rb") as fh:
        fh.seek(-1, os.SEEK_END)
        return fh.read(1) != b"\n"


async def read_events(path: Path) -> list[LockEvent]:
    """Read the lock at ``path`` back into its events, in file order.

    A missing file is an empty history. Any unparseable line is skipped with a
    warning rather than raising: a crash mid-append can truncate a write, and
    once later events land on top the damage is no longer at the tail — so an
    interior bad line must not wedge every future read of the whole history.
    The good records around it are still recovered and folded.
    """
    try:
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    except FileNotFoundError:
        return []

    lines = [line for line in text.splitlines() if line.strip()]
    events: list[LockEvent] = []
    for number, line in enumerate(lines, start=1):
        try:
            events.append(_EVENT_ADAPTER.validate_json(line))
        except ValidationError:
            logger.warning(
                "%s: skipping unparseable lock event on line %d (interrupted write or corruption?)", path, number
            )
    return events


def fold_statuses(events: Sequence[LockEvent]) -> dict[str, ExperimentStatus]:
    """Fold lock events into one ``ExperimentStatus`` per experiment, in log order.

    The read side of the lock: ``experiment_started`` opens a status (state
    ``"running"``), each ``cycle_recorded`` folds in as a ``CycleSummary``
    (delta vs the experiment's baseline, best-so-far recomputed), and
    ``experiment_finished`` settles the state to its outcome. An experiment
    with no terminal event stays ``"running"`` — live or interrupted, the log
    cannot tell. The log only ever sees settled cycles; a running cycle's
    record is its ``cyc_<NNN>.lock``.

    A cycle or finish for an unknown experiment id is skipped with a warning —
    the read path stays lenient so one stray line cannot hide the rest of the
    history.
    """
    statuses: dict[str, ExperimentStatus] = {}
    for event in events:
        if isinstance(event, ExperimentStarted):
            statuses[event.experiment_id] = ExperimentStatus(
                experiment_id=event.experiment_id,
                baseline_score=event.baseline_score,
                cycles=[],
                completed=0,
                total=event.budget.cycles,
                state="running",
            )
        elif isinstance(event, CycleRecorded):
            status = statuses.get(event.cycle.experiment_id)
            if status is None:
                logger.warning("cycle for unknown experiment %s; skipping", event.cycle.experiment_id)
                continue
            summary = CycleSummary.from_cycle(event.cycle, status.baseline_score)
            status.cycles.append(summary)
            status.completed += 1
            if status.best is None or _score_value(summary) > _score_value(status.best):
                status.best = summary
        else:
            status = statuses.get(event.experiment_id)
            if status is None:
                logger.warning("finish for unknown experiment %s; skipping", event.experiment_id)
                continue
            status.state = event.outcome
    return statuses


async def load_statuses(path: Path) -> dict[str, ExperimentStatus]:
    """Read the lock at ``path`` and fold it — the ``hillclimber status`` entry point."""
    return fold_statuses(await read_events(path))


async def reset_history(path_to_artefact: str) -> None:
    """Delete the artefact's climb history — the explicit opt-in reset.

    The one sanctioned way to lose history (behind ``hillclimber run``'s
    overwrite prompt / ``--overwrite``; the lock itself is never rewritten in
    place). Removes the whole ``.hillclimber`` working directory — the lock
    file and any leftover cycle worktrees/workspaces — then prunes git's stale
    worktree records so future climbs can reuse the same paths. The artefact's
    git history, past ``hc/*`` cycle branches included, is left untouched and
    stays recoverable.
    """
    root = repo_root(path_to_artefact)
    workdir = root / ".hillclimber"
    await asyncio.to_thread(shutil.rmtree, workdir, ignore_errors=True)
    if (root / ".git").exists():
        await prune_worktrees(str(root))


def _score_value(summary: CycleSummary) -> float:
    """The summary's comparable score; an unscored cycle ranks lowest."""
    return summary.score_after.value if summary.score_after is not None else float("-inf")


class ExperimentLog:
    """One experiment's appender, bound to a lock path and experiment id.

    A strategy creates one per ``execute`` and records the experiment's
    lifecycle through it (see ``Chain.execute``); each ``record_*`` builds the
    event (timestamped at creation) and appends it as one line.
    """

    def __init__(self, path: Path, experiment_id: str) -> None:
        self.path = path
        self.experiment_id = experiment_id

    async def record_started(self, strategy: str, baseline: Score, budget: Budget) -> None:
        """Open this experiment in the log."""
        await append_event(
            self.path,
            ExperimentStarted(
                experiment_id=self.experiment_id,
                strategy=strategy,
                baseline_score=baseline,
                budget=budget,
            ),
        )

    async def record_cycle(self, cycle: Cycle) -> None:
        """Promote a settled cycle (its ``cyc_<NNN>.lock`` state) into the log."""
        await append_event(self.path, CycleRecorded(cycle=cycle))

    async def record_finished(
        self,
        outcome: Literal["completed", "failed"],
        completed: int,
        best_cycle_id: str | None,
    ) -> None:
        """Close this experiment in the log with its terminal outcome."""
        await append_event(
            self.path,
            ExperimentFinished(
                experiment_id=self.experiment_id,
                outcome=outcome,
                completed=completed,
                best_cycle_id=best_cycle_id,
            ),
        )
