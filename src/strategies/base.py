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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from harnesses import Harness, TraceEvent, TraceSink, get_harness
from hillclimber.git_utils import commit_all, head_sha, is_dirty
from hillclimber.models import Agent, Config, Cycle, ExperimentStatus, Score
from hillclimber.progress import RunEventSink, ignore_progress
from hillclimber.telemetry import get_logger
from sandboxes.base import Sandbox

logger = get_logger(__name__)

# v1 drives every agent through the Claude harness. Per-agent harness selection
# (from ``Agent.harness``) is a later refinement; for now the strategy holds one.
DEFAULT_HARNESS = "claude"


def missing_role_message(strategy: str, role: str) -> str:
    """The error for a config that lacks a role's ``[agents.<role>]`` table.

    One builder so ``verify_agents`` (the up-front check) and ``_role_agent``
    (the safety net for library callers) can never drift apart.
    """
    return f'strategy "{strategy}" requires agent "{role}"; please add [agents.{role}] to hillclimber.toml'


def log_trace(event: TraceEvent) -> None:
    """The default trace sink: one log line per agent step.

    Where agent traces land when no other sink is injected (see
    ``Strategy.__init__``) — the run narrates itself through the ordinary
    logging setup. A consumer wanting richer display (the CLI's live view)
    passes its own ``TraceSink`` instead; nothing else changes.
    """
    logger.info("[%s] %s: %s", event.label or "agent", event.kind, event.summary)


@dataclass(frozen=True)
class RoleSpec:
    """What a strategy needs from one agent role it declares (see ``Strategy.roles``).

    ``default_prompt`` is the role's system prompt when the config doesn't
    override it (see ``Strategy._role_agent``); ``description`` is the one-line
    human summary of the role — it becomes the comment above the role's
    ``[agents.<role>]`` table in the scaffolded ``hillclimber.toml``.
    """

    default_prompt: str
    description: str


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

    # The agent roles this strategy drives — exactly the ``[agents.<role>]``
    # tables a config must define for it. A class attribute so ``check``/``init``
    # can read it without building a sandbox or harness.
    roles: ClassVar[Mapping[str, RoleSpec]] = {}

    def __init__(
        self,
        sandbox: Sandbox,
        trace_sink: TraceSink | None = None,
        progress_sink: RunEventSink | None = None,
    ) -> None:
        self._state = {}
        # Held for future per-``Agent.harness`` selection, which will need to
        # build other harnesses with the same confinement policy.
        self.sandbox = sandbox
        self.harness: Harness = get_harness(DEFAULT_HARNESS, sandbox)
        # Where this strategy's agent traces land: the injected sink (the CLI's
        # live view, later) or the logging default. Strategies don't call this
        # directly — they wrap it per agent run via ``_make_trace_sink``.
        self.trace_sink: TraceSink = trace_sink if trace_sink is not None else log_trace
        # Where run-level milestones land (cycle started/staged/scored — see
        # ``hillclimber.progress``). Silent by default: the logs already narrate
        # every milestone, so only an injected consumer (the CLI dashboard) taps in.
        self.progress_sink: RunEventSink = progress_sink if progress_sink is not None else ignore_progress

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

    def _role_agent(self, config: Config, role: str) -> Agent:
        """The config's agent for ``role``, with its system prompt resolved.

        A ``system_prompt`` set in the toml wins; one left unset is filled from
        this strategy's role default (on a copy — the config stays as loaded).
        The missing-role check is a safety net for library callers that skip
        ``verify_agents``.

        Raises:
            ValueError: If the config defines no agent for ``role``.
        """
        spec = type(self).roles[role]
        agent = config.agents.get(role)
        if agent is None:
            raise ValueError(missing_role_message(config.strategy, role))
        if agent.system_prompt is None:
            return agent.model_copy(update={"system_prompt": spec.default_prompt})
        return agent

    def _make_trace_sink(self, label: str) -> TraceSink:
        """Return a sink that stamps ``label`` onto each event and forwards it.

        The harness emits anonymous trace events; the strategy is the layer that
        knows *who* is running (which cycle, which role), so it stamps that
        context here before events reach ``self.trace_sink``. A consumer then
        renders "cycle 003/worker opened a file" without parsing anything.

        Args:
            label: The runner's identity, e.g. ``"cycle 003/worker"``.
        """

        def sink(event: TraceEvent) -> None:
            self.trace_sink(event.model_copy(update={"label": label}))

        return sink

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
        """Commit the worker's change; return the cycle's commit sha.

        The sandbox denies the worker git access (a worktree's metadata lives in
        the parent repo, outside the sandbox boundary), so the worker only edits
        and committing is the runner's job — this method runs outside the
        sandbox. The result is a clean commit: the score is read from it and the
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
            logger.info("cycle %d: committing the worker's edits", index)
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
