"""The full-trace file behind ``hillclimber run``.

The live dashboard's trace tail is a glance — a few repainting lines that can
never be reliably selected or copied. The complete record lands here instead:
one plain-text line per :class:`~hillclimber.harnesses.TraceEvent`, full width, written to
``<artefact repo root>/.hillclimber/trace.log`` and truncated at the start of
each run. ``run`` announces the path so "what did the agent actually do?" is a
``less``/``grep`` away, during the run or after it.

The sink (:meth:`TraceLog.on_trace`) stays synchronous, as every trace sink must
(see ``hillclimber.harnesses.TraceSink``), but it must not do disk I/O on the stream-reading
loop: a per-line flush there would block the event loop on every agent step. So
the sink only formats the line and hands it to a background writer *thread* via a
queue; the thread owns the file and does the writing and flushing, decoupled from
the loop entirely (the same "offload the blocking write" shape ``lockfile``
uses with ``asyncio.to_thread``). The thread flushes as it catches up and on a
short idle timer, so a reader tailing the file still sees events promptly.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from types import TracebackType
from typing import IO

from hillclimber.git_utils import repo_root
from hillclimber.harnesses import TraceEvent
from hillclimber.telemetry import get_logger

logger = get_logger(__name__)

TRACE_FILENAME = "trace.log"

# How long the writer thread blocks waiting for the next line before flushing
# what it has. Bounds how stale a tailed trace.log can get while the agent is
# quiet, without busy-spinning.
_FLUSH_INTERVAL = 0.5

# Sentinel queued on exit: the writer drains every real line ahead of it, then stops.
_STOP = object()


def trace_path(path_to_artefact: str) -> Path:
    """The artefact's trace-log path: ``<repo root>/.hillclimber/trace.log``.

    Beside the experiment lock (see ``lockfile.lock_path``), so all runner
    state shares the one ``.hillclimber`` workspace the dirty-tree check
    already excludes.
    """
    return repo_root(path_to_artefact) / ".hillclimber" / TRACE_FILENAME


class TraceLog:
    """A context manager owning one run's trace file; also a ``TraceSink``.

    Use around the run, teed with the display sink::

        with TraceLog(trace_path(artefact)) as trace_log:
            run(..., trace_sink=tee(dashboard.on_trace, trace_log.on_trace))

    The file is truncated on entry — it is *this run's* record, not history
    (history lives in ``hillclimber.lock``).
    """

    def __init__(self, path: Path) -> None:
        self.path = path  # public: the run command announces it to the user
        self._file: IO[str] | None = None
        self._queue: queue.SimpleQueue[object] = queue.SimpleQueue()
        self._writer: threading.Thread | None = None

    def __enter__(self) -> TraceLog:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", encoding="utf-8")
        self._writer = threading.Thread(target=self._drain, name="hc-tracelog", daemon=True)
        self._writer.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Signal the writer to finish the queued lines, then wait for it to land
        # them and close the file — so the record is complete once ``run`` exits.
        if self._writer is not None:
            self._queue.put(_STOP)
            self._writer.join()
            self._writer = None
        if self._file is not None:
            self._file.close()
            self._file = None

    def on_trace(self, event: TraceEvent) -> None:
        """``TraceSink``: queue ``event`` as one full-width plain-text line.

        Non-blocking and does no disk I/O — it only formats and enqueues, so the
        harness's stream-reading loop is never stalled by the file. The writer
        thread does the actual write (see :meth:`_drain`).
        """
        stamp = time.strftime("%H:%M:%S")
        self._queue.put(f"{stamp} [{event.label or 'agent'}] {event.kind}: {event.summary}\n")

    def _drain(self) -> None:
        """Writer-thread loop: write queued lines, flushing as it catches up.

        Runs off the event loop so its blocking writes and flushes never touch
        the stream loop. Flushes whenever it drains the queue and on each idle
        timeout, so a tailed ``trace.log`` stays close to live; the final flush
        happens when :data:`_STOP` is seen (queued by ``__exit__``).
        """
        assert self._file is not None
        while True:
            try:
                item = self._queue.get(timeout=_FLUSH_INTERVAL)
            except queue.Empty:
                self._flush()  # idle: make sure a tailer sees the latest
                continue
            if item is _STOP:
                self._flush()
                return
            assert isinstance(item, str)  # the only non-_STOP items queued are lines
            try:
                self._file.write(item)
                if self._queue.empty():
                    self._file.flush()  # caught up: surface the batch promptly
            except OSError:
                logger.exception("failed writing to trace log %s", self.path)

    def _flush(self) -> None:
        """Flush without letting a disk error (ENOSPC, a yanked volume) kill the
        writer thread — the trace file is a convenience, never worth the run."""
        assert self._file is not None
        try:
            self._file.flush()
        except OSError:
            logger.exception("failed flushing trace log %s", self.path)
