"""Pydantic models for hillclimber.

Three levels of types:

- **Write models** (persisted, authoritative): ``Experiment`` -> ``hillclimber.toml``,
  ``Cycle`` -> ``cyc_<NNN>.lock``.
- **Read model** (computed on demand, never stored): ``ExperimentStatus`` /
  ``CycleSummary``. Powers ``hillclimber status``. *Best-so-far* is computed here,
  not persisted anywhere.
- **Shared value types**: ``Agent``, ``Scorer``, ``Score``, ``CycleStatus``, etc.

Convention: anything named ``...Status`` / ``...Summary`` is a read model — build
it, print it, discard it. It must never gain a method that mutates or persists.
"""

from __future__ import annotations

import math
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# --------------------------------------------------------------------------- #
# Shared value types
# --------------------------------------------------------------------------- #


class CycleStatus(StrEnum):
    """Lifecycle of a single cycle."""

    running = "running"
    scored = "scored"
    accepted = "accepted"
    rejected = "rejected"
    failed = "failed"


class Agent(BaseModel):
    """One agent configuration — a ``[agents.<role>]`` table in the toml.

    ``system_prompt`` is optional: when omitted from the config, the strategy
    fills in its role default at access time (see ``Strategy._role_agent``); one
    set here is an override, used verbatim.
    """

    harness: str  # e.g. "claude" (alias: "claude code"); resolved by harnesses.get_harness
    model: str
    system_prompt: str | None = None  # None -> the strategy's role default
    params: dict = Field(default_factory=dict)  # temperature, max tokens, etc.

    @model_validator(mode="before")
    @classmethod
    def _collect_params(cls, data: object) -> object:
        """Fold any extra keys into ``params``.

        Tuning knobs are written flat in the config (``temperature = 0.5``)
        rather than nested under a ``params`` table; anything that isn't a known
        field is gathered into ``params`` here. An explicit ``params`` table
        still works and takes precedence on key collisions.
        """
        if not isinstance(data, dict):
            return data
        known = {"harness", "model", "system_prompt", "params"}
        extra = {k: v for k, v in data.items() if k not in known}
        if not extra:
            return data
        rest = {k: v for k, v in data.items() if k in known}
        # An explicit ``params`` table wins on key collisions; guard its type so a
        # malformed (non-mapping) ``params`` is treated as empty rather than blowing
        # up the spread.
        explicit = rest.get("params", {})
        if not isinstance(explicit, dict):
            explicit = {}
        rest["params"] = {**extra, **explicit}
        return rest


class CommandScorer(BaseModel):
    """Score by running a command (e.g. a test suite)."""

    kind: Literal["command"] = "command"
    cmd: str  # e.g. "pytest test_eval.py"


# The fitness function. One scorer per experiment; the ``kind`` discriminator is
# the seam for adding kinds (e.g. a judge) later — widen to ``CommandScorer |
# JudgeScorer`` when a second arrives.
Scorer = Annotated[
    CommandScorer,
    Field(discriminator="kind"),
]


# Sensitive roots an agent CLI is denied *read* access to by default (see
# ``SeatbeltSandboxConfig``). The active worktree lives under ``~/projects`` and
# is re-allowed by the rendered profile, so the agent still sees its own
# checkout; the CLI's own state dirs (``~/.nvm``, ``~/.claude``, ``~/.codex``)
# are intentionally absent so the Node-based CLI can still boot.
DEFAULT_DENY_READ = [
    "~/projects",
    "~/Documents",
    "~/Desktop",
    "~/.ssh",
    "~/.aws",
    "~/.config/gcloud",
]


class SeatbeltSandboxConfig(BaseModel):
    """Confine agents to their worktree with macOS Seatbelt (``sandbox-exec``).

    ``deny_read`` are roots the agent may not read (secrets, other code);
    ``network`` gates outbound access. Selecting this kind on a non-macOS
    platform hard-errors at sandbox construction (see ``SeatbeltSandbox``) —
    use ``kind = "none"`` to opt out explicitly.
    """

    kind: Literal["seatbelt"] = "seatbelt"
    deny_read: list[str] = Field(default_factory=lambda: list(DEFAULT_DENY_READ))
    network: bool = True


class PassthroughSandboxConfig(BaseModel):
    """No sandbox — run the agent CLI unconfined. The explicit opt-out."""

    kind: Literal["none"] = "none"


# The filesystem sandbox backend. Like ``Scorer``, the ``kind`` discriminator is
# the seam for adding backends (bubblewrap, docker, ...) later — they slot in as
# new variants with no caller changes.
SandboxConfig = Annotated[
    SeatbeltSandboxConfig | PassthroughSandboxConfig,
    Field(discriminator="kind"),
]


class Score(BaseModel):
    """Comparable and composable — not a bare float, so accept logic stays
    uniform across scorer kinds."""

    value: float
    passed: bool
    scorer_id: str  # which scorer produced it


class Eval(BaseModel):
    """The score returned by the user's eval — the wire contract with the artefact.

    The eval command ends by printing this as one JSON line on stdout (the
    *envelope*); the runner recognises it by the ``hillclimber_eval`` marker and
    validates it here (see ``scoring.parse_eval``). The marker makes recognition
    deterministic — stray JSON the artefact prints can never be mistaken for the
    score — and doubles as the schema version for later evolution.

    The user's file does **not** need this class (or hillclimber installed): the
    scaffolded ``eval.py`` builds the same envelope with a stdlib-only dataclass
    (see ``cli.commands.init``). Evals that do have hillclimber available may
    print ``Eval(...).model_dump_json()`` instead — same envelope.

    Higher ``score`` is better; the climb pushes it up (e.g. 0.6 -> 0.9), and it
    must be finite (NaN/inf would poison best-so-far comparisons). ``details``
    is optional richness for the trace/viewer (per-case breakdown, sub-metrics)
    and never affects the climb.
    """

    hillclimber_eval: Literal[1] = 1  # envelope marker + schema version
    score: float  # the climbable number, typically in [0, 1]
    details: dict = Field(default_factory=dict)  # optional, for tracing/inspection

    @field_validator("score")
    @classmethod
    def _score_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("score must be a finite number, not NaN or infinity")
        return value


class Goal(BaseModel):
    """Definition of success — what the climb optimizes toward.

    v1 is intentionally minimal: maximize the eval score. The field is kept
    explicit (rather than hardcoded in the loop) so direction/target can grow
    later without reshaping the model.
    """

    direction: str = "maximize"  # v1: "maximize" only
    target: float | None = None  # optional success threshold (early-stop hook; unused in v1)

    def is_met(self, best: Score | None) -> bool:
        """Whether ``best`` satisfies the goal — the loop's early-stop check.

        v1 only maximizes toward an optional ``target``. With no target set (or
        nothing scored yet), the goal is never met, so the climb runs until the
        budget is exhausted. This is the early-stop seam: dormant until a
        ``target`` is configured.

        Args:
            best: The best ``Score`` achieved so far, or ``None`` before any run.

        Returns:
            ``True`` if ``best`` reaches ``target`` (maximizing), else ``False``.
        """
        if best is None or self.target is None:
            return False
        return best.value >= self.target


class Budget(BaseModel):
    """Hard stop condition. v1: number of iterations only."""

    cycles: int  # number of runs to attempt

    def is_exhausted(self, completed: int) -> bool:
        """Whether ``completed`` cycles have used up the budget.

        Args:
            completed: The number of cycles attempted so far.

        Returns:
            ``True`` once ``completed`` reaches the ``cycles`` budget.
        """
        return completed >= self.cycles


# --------------------------------------------------------------------------- #
# Write models (persisted, authoritative)
# --------------------------------------------------------------------------- #


# The strategy a config gets when it names none. ``hillclimber init`` scaffolds
# its agent tables from this same name, so the two can't drift.
DEFAULT_STRATEGY = "chain"

# Pre-``[agents.<role>]`` table names -> the role that replaced each. Pydantic
# ignores unknown top-level keys, so without an explicit check a legacy config
# would load "cleanly" with no agents and fail later with a misleading error.
_LEGACY_AGENT_TABLES = {
    "hillclimber_agent": "orchestrator",
    "worker_agent": "worker",
    "reflector_agent": "reflector",
}


class Config(BaseModel):
    """The config. Describes what to do. Maps to ``hillclimber.toml``."""

    path_to_artefact: str
    # The git ref the climb starts from: the baseline is scored at it and cycle 1
    # forks from it. Empty/omitted -> the artefact's current ``HEAD`` (see
    # ``Chain._prepare_repo`` / ``get_baseline_score``).
    start_branch: str | None = None
    # Opt-in convenience for a dirty artefact tree. Off by default, the runner
    # refuses to start on uncommitted changes (baseline and cycles would measure
    # different code; see ``run``). When on, the runner instead snapshots the
    # dirty tree into a commit and climbs from that (see ``create_snapshot_commit``).
    auto_commit: bool = False
    baseline_score: Score | None = None  # scored once, before any run
    scorer: Scorer  # the fitness function (v1: exactly one)
    # The OS sandbox that confines every agent CLI to its run's worktree. A
    # ``hillclimber.toml`` with no ``[sandbox]`` table gets the Seatbelt default.
    sandbox: SandboxConfig = Field(default_factory=SeatbeltSandboxConfig)
    # Which strategy runs the climb; resolved and validated by strategies.registry.
    strategy: str = DEFAULT_STRATEGY
    goal: Goal = Field(default_factory=Goal)  # what the climb optimizes toward
    budget: Budget  # hard stop condition (v1: cycles only)
    # One ``[agents.<role>]`` table per role. Which roles are required is the
    # strategy's declaration, checked by ``strategies.registry.verify_agents``.
    agents: dict[str, Agent] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_agent_tables(cls, data: object) -> object:
        """Fail a pre-rename config with a hint instead of silently dropping its agents."""
        if isinstance(data, dict):
            renames = ", ".join(
                f"[{table}] -> [agents.{role}]" for table, role in _LEGACY_AGENT_TABLES.items() if table in data
            )
            if renames:
                raise ValueError(f"agent tables were renamed; move {renames} in hillclimber.toml")
        return data

    @model_validator(mode="after")
    def _reject_absolute_scorer_path(self) -> Config:
        """Reject a scorer command that hard-codes the artefact's absolute path.

        The scorer runs at each cycle's worktree root (a clone of the artefact),
        so its ``cmd`` must reference files by paths relative to that root. An
        absolute path into the artefact would run the *original* tree instead of
        the worktree — silently scoring unmodified code, so every cycle stays
        pinned at the baseline. Catch it here rather than let it corrupt a whole
        run. Cheap and high-signal: flag the resolved artefact path appearing
        verbatim in ``cmd`` (only meaningful once it is absolute).
        """
        artefact = self.path_to_artefact
        if Path(artefact).is_absolute() and artefact in self.scorer.cmd:
            raise ValueError(
                f"scorer cmd must use paths relative to the artefact root, but it contains "
                f"the absolute artefact path {artefact!r}: {self.scorer.cmd!r}. "
                "Write it relative to the artefact root (e.g. 'uv run python eval.py')."
            )
        return self


class Cycle(BaseModel):
    """One hypothesis iteration in its own git worktree. Maps to ``cyc_<NNN>.lock``.

    A cycle belongs to an experiment (``experiment_id``, e.g. ``exp_a1b2c3d4``)
    and is the ``index``-th cycle within it (1-based). The set of cycles is just
    the collection of these lock files on disk — there is no parent object
    holding the list.
    """

    experiment_id: str  # e.g. exp_a1b2c3d4, minted once per experiment
    index: int  # 1-based cycle number within the experiment
    parent_ref: str  # baseline in v1; seam for later strategies
    branch: str  # the experiment branch
    worktree: str  # e.g. hc_a1b2_cycle_001/
    hypothesis: str  # what this cycle tried (e.g. "Use pydantic...")
    score_before: Score
    score_after: Score | None = None
    accepted: bool = False
    status: CycleStatus
    commit_sha: str | None = None  # pointer to the actual change

    @property
    def cycle_id(self) -> str:
        """Human-facing cycle label, e.g. ``cyc_001`` for ``index == 1``."""
        return f"cyc_{self.index:03d}"


# --------------------------------------------------------------------------- #
# Read model (computed on demand, never stored)
# --------------------------------------------------------------------------- #


class CycleSummary(BaseModel):
    """A flattened, display-oriented view of a single cycle."""

    experiment_id: str
    cycle_id: str  # e.g. cyc_001
    status: CycleStatus
    hypothesis: str = ""  # what this cycle tried, for display
    score_after: Score | None = None
    accepted: bool
    delta: float  # score_after - baseline (computed)


class ExperimentStatus(BaseModel):
    """Assembled fresh by folding together the cycles on disk. Powers
    ``hillclimber status``. ``best`` is COMPUTED (``max(cycles, key=score)``),
    not persisted anywhere."""

    baseline_score: Score
    cycles: list[CycleSummary]
    best: CycleSummary | None = None  # COMPUTED, not stored
    in_progress: list[str] = Field(default_factory=list)  # cycle ids currently running
    completed: int
    total: int
