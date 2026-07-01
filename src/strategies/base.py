"""The strategy interface.

A strategy is the *how* of the climb: given a validated ``Config``, it decides
how cycles are produced and orchestrated (iteratively, as a chain, etc.) and
drives them to completion. ``Config.strategy`` names which one to use; the
runner (see ``hillclimber.run``) picks the matching subclass and calls
``execute``.

Subclasses (e.g. ``chain``) implement the loop; this base only fixes the
contract so the runner stays agnostic to which strategy it is driving.
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from harnesses import Harness, get_harness
from hillclimber.git_utils import commit_all, head_sha, is_dirty
from hillclimber.models import Config, Cycle, ExperimentStatus, Score
from hillclimber.telemetry import get_logger
from sandboxes.base import Sandbox

logger = get_logger(__name__)

# v1 drives every agent through the Claude harness. Per-agent harness selection
# (from ``Agent.harness``) is a later refinement; for now the strategy holds one.
DEFAULT_HARNESS = "claude"


@dataclass(frozen=True)
class CycleRecord:
    """One past hypothesis and how it moved the eval score.

    A strategy's memory of a cycle it already ran (see ``Strategy._cycle_records``):
    each scored cycle records the hypothesis it tried alongside the score before
    and after, so the next hypothesis can be steered off what already worked,
    flopped, or did nothing. ``after`` is ``None`` for a cycle that never produced
    a score.
    """

    hypothesis: str
    before: float
    after: float | None


class Strategy(ABC):
    """Base class for climb strategies."""

    def __init__(self, sandbox: Sandbox) -> None:
        self._state = {}
        # Held for future per-``Agent.harness`` selection, which will need to
        # build other harnesses with the same confinement policy.
        self.sandbox = sandbox
        self.harness: Harness = get_harness(DEFAULT_HARNESS, sandbox)

    @staticmethod
    async def create_workspace(path: str, workspace_name: str) -> str:
        """Create a named workspace directory under ``path/.hillclimber``.

        Workspaces are isolated working directories for the climb, kept under a
        ``.hillclimber`` folder so they sit alongside (but never clobber) the
        artefact.

        Args:
            path: The base directory the workspace is created in. Must exist.
            workspace_name: The workspace's name; becomes the directory name.

        Returns:
            The workspace name.

        Raises:
            FileNotFoundError: If ``path`` is not an existing directory.
            ValueError: If ``workspace_name`` is empty or contains a path separator.
        """
        base = Path(path)
        if not base.is_dir():
            raise FileNotFoundError(f"not a directory: {path}")
        if not workspace_name or "/" in workspace_name or "\\" in workspace_name:
            raise ValueError(f"invalid workspace name: {workspace_name!r}")

        workspace = base / ".hillclimber" / workspace_name
        await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)
        return workspace_name

    @staticmethod
    def new_experiment_id() -> str:
        """Mint an experiment id, e.g. ``exp_a1b2c3d4``.

        Minted once per experiment (see ``Cycle.experiment_id``). The 8 hex chars
        give ~4 billion of headroom; the first 4 seed the worktree/branch names
        (see ``Chain.one_cycle``). Cycles within an experiment are numbered, not
        minted (see ``Cycle.index`` / ``Cycle.cycle_id``).
        """
        return f"exp_{uuid.uuid4().hex[:8]}"

    @staticmethod
    async def write_lock(worktree: str, cycle: Cycle) -> str:
        """Persist ``cycle`` as ``cyc_<NNN>.lock`` inside its ``worktree``.

        The lock file is a cycle's authoritative on-disk record (see ``Cycle``);
        the set of these files *is* the cycle history. The write is offloaded with
        ``asyncio.to_thread`` so it never blocks the event loop.

        Args:
            worktree: The worktree directory the lock lives in.
            cycle: The cycle to serialize.

        Returns:
            The path to the written lock file.
        """
        lock = Path(worktree) / f"{cycle.cycle_id}.lock"
        await asyncio.to_thread(lock.write_text, cycle.model_dump_json(indent=2))
        return str(lock)

    def _cycle_records(self) -> list[CycleRecord]:
        """The strategy's memory of past cycles, kept in ``self._state``.

        Each scored cycle appends the hypothesis it tried and how it moved the
        score; strategies read the list back so the next hypothesis builds on what
        came before instead of rediscovering it. Lazily seeded on first access.
        """
        records = self._state.setdefault("cycle_records", [])
        assert isinstance(records, list)
        return records

    async def _commit_cycle(self, worktree: str, base_sha: str, index: int) -> str:
        """Ensure the worker's change is committed; return the cycle's commit sha.

        The worker is asked to commit its own change (see ``_apply_hypothesis``).
        This verifies it did: any uncommitted leftovers are committed on its behalf
        so the cycle's result is a clean commit — the score is read from it and the
        next cycle forks from it. A cycle that produced no new commit at all (no
        change applied) is logged but not an error.

        Args:
            worktree: The cycle's checkout the worker edited.
            base_sha: The commit the branch forked from, to detect a no-op cycle.
            index: The 1-based cycle number, for log context.

        Returns:
            The sha of this cycle's resulting commit.
        """
        if await is_dirty(worktree):
            logger.warning("cycle %d: worker left uncommitted changes; committing them", index)
            await commit_all(worktree, f"hillclimber: cycle {index:03d}")
        sha = await head_sha(worktree)
        if sha == base_sha:
            logger.warning("cycle %d: worker produced no new commit (no change applied)", index)
        return sha

    @staticmethod
    def _render_history(records: list[CycleRecord]) -> str:
        """Render past cycles as a prompt block, or ``""`` when there are none.

        Turns the ``CycleRecord`` memory into a "here's what we already tried and
        where it moved the needle" briefing for the proposer, ending with a blank
        line so it slots cleanly ahead of the task instruction.
        """
        if not records:
            return ""
        lines: list[str] = []
        for record in records:
            if record.after is None:
                outcome = f"was not scored ({record.before:.3f} -> ?)"
            else:
                delta = record.after - record.before
                move = f"{record.before:.3f} -> {record.after:.3f}"
                if delta > 0:
                    outcome = f"raised the score {move} (+{delta:.3f})"
                elif delta < 0:
                    outcome = f"lowered the score {move} ({delta:.3f})"
                else:
                    outcome = f"did not move the score ({move})"
            lines.append(f'- "{record.hypothesis}" — {outcome}')
        body = "\n".join(lines)
        return (
            "Earlier cycles in this experiment already tried these hypotheses, "
            "and here is where each moved the eval score:\n"
            f"{body}\n"
            "Do not repeat any of them. Build on the changes that raised the "
            "score, and steer away from the ones that lowered it or made no "
            "difference.\n\n"
        )

    @abstractmethod
    async def execute(self, config: Config, baseline: Score) -> ExperimentStatus:
        """Drive the climb described by ``config`` to completion.

        Args:
            config: The validated experiment config (see ``hillclimber.models``).
            baseline: The artefact's baseline ``Score``, scored once before any
                cycle — the number each cycle must beat.

        Returns:
            The final ``ExperimentStatus`` — cycles attempted and the best so far.
        """
        ...
