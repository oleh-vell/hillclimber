"""The strategy interface.

A strategy is the *how* of the climb: given a validated ``Config``, it decides
how runs are produced and orchestrated (iteratively, as a chain, etc.) and
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
from pathlib import Path

from harnesses import Harness, get_harness
from hillclimber.models import Config, ExperimentStatus, Run, Score
from sandboxes.base import Sandbox

# v1 drives every agent through the Claude harness. Per-agent harness selection
# (from ``Agent.harness``) is a later refinement; for now the strategy holds one.
DEFAULT_HARNESS = "claude"


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

        Minted once per experiment (see ``Run.experiment_id``). The 8 hex chars
        give ~4 billion of headroom; the first 4 seed the worktree/branch names
        (see ``Chain.one_cycle``). Cycles within an experiment are numbered, not
        minted (see ``Run.cycle`` / ``Run.cycle_id``).
        """
        return f"exp_{uuid.uuid4().hex[:8]}"

    @staticmethod
    async def write_lock(worktree: str, run: Run) -> str:
        """Persist ``run`` as ``cyc_<NNN>.lock`` inside its ``worktree``.

        The lock file is a run's authoritative on-disk record (see ``Run``); the
        set of these files *is* the run history. The write is offloaded with
        ``asyncio.to_thread`` so it never blocks the event loop.

        Args:
            worktree: The worktree directory the lock lives in.
            run: The run to serialize.

        Returns:
            The path to the written lock file.
        """
        lock = Path(worktree) / f"{run.cycle_id}.lock"
        await asyncio.to_thread(lock.write_text, run.model_dump_json(indent=2))
        return str(lock)

    @abstractmethod
    async def execute(self, config: Config, baseline: Score) -> ExperimentStatus:
        """Drive the climb described by ``config`` to completion.

        Args:
            config: The validated experiment config (see ``hillclimber.models``).
            baseline: The artefact's baseline ``Score``, scored once before any
                cycle — the number each run must beat.

        Returns:
            The final ``ExperimentStatus`` — runs attempted and the best so far.
        """
        ...
