"""Pydantic models for hillclimber.

Three levels of types:

- **Write models** (persisted, authoritative): ``Experiment`` -> ``hillclimber.toml``,
  ``Cycle`` -> ``cyc_<NNN>.lock``, ``LockEvent`` (``ExperimentStarted`` /
  ``CycleRecorded`` / ``ExperimentFinished``) -> ``hillclimber.lock``.
- **Read model** (computed on demand, never stored): ``ExperimentStatus`` /
  ``CycleSummary``. Powers ``hillclimber status``. *Best-so-far* is computed here,
  not persisted anywhere.
- **Shared value types**: ``Agent``, ``Scorer``, ``Score``, ``CycleStatus``, etc.

Convention: anything named ``...Status`` / ``...Summary`` is a read model — build
it, print it, discard it. It must never gain a method that mutates or persists.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _utcnow() -> datetime:
    """Timestamp for lock events — UTC so logs from different hosts compare."""
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Shared value types
# --------------------------------------------------------------------------- #


class CycleStatus(StrEnum):
    """Lifecycle of a single cycle."""

    running = "running"
    scored = "scored"
    failed = "failed"


class Agent(BaseModel):
    """One agent configuration — a ``[agents.<role>]`` table in the toml.

    ``system_prompt`` is optional: when omitted from the config, the strategy
    fills in its role default at access time (see ``Strategy._role_agent``); one
    set here is an override, used verbatim.

    Unknown keys are rejected (``extra="forbid"``): nothing reads any other
    knob today, so a typo like ``system_promt = "..."`` must fail at load time
    rather than validate cleanly and silently do nothing.
    """

    model_config = ConfigDict(extra="forbid")

    harness: str  # e.g. "claude" (alias: "claude code"); resolved by hillclimber.harnesses.get_harness
    model: str
    system_prompt: str | None = None  # None -> the strategy's role default


class CommandScorer(BaseModel):
    """Score by running a command (e.g. a test suite)."""

    kind: Literal["command"] = "command"
    cmd: str  # e.g. "pytest test_eval.py"


# The fitness function. One scorer per experiment; new kinds (e.g. a judge)
# arrive by widening the ``kind``-discriminated union to ``CommandScorer |
# JudgeScorer``.
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

    Note: ``git`` does not work inside a cycle worktree under this sandbox — the
    worktree's ``.git`` file targets the read-denied ``<repo>/.git/worktrees/...``
    (see ``SeatbeltSandbox``). The ``chain`` strategy commits from outside the
    sandbox so agents never need git; a strategy that does must account for this.
    """

    kind: Literal["seatbelt"] = "seatbelt"
    deny_read: list[str] = Field(default_factory=lambda: list(DEFAULT_DENY_READ))
    network: bool = True


class PassthroughSandboxConfig(BaseModel):
    """No sandbox — run the agent CLI unconfined. The explicit opt-out."""

    kind: Literal["none"] = "none"


# The filesystem sandbox backend. Like ``Scorer``, new backends (bubblewrap,
# docker, ...) slot in as new ``kind`` variants with no caller changes.
SandboxConfig = Annotated[
    SeatbeltSandboxConfig | PassthroughSandboxConfig,
    Field(discriminator="kind"),
]


class Score(BaseModel):
    """A scorer's verdict: the climbable number plus whether the eval ran cleanly."""

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

    v1 is intentionally minimal: maximize the eval score. ``direction`` is a
    one-member Literal so a config asking for ``"minimize"`` fails at load time
    instead of being accepted and silently maximized; widen the Literal when
    minimizing actually exists.
    """

    direction: Literal["maximize"] = "maximize"
    target: float | None = None  # optional success threshold; reaching it stops the climb early

    def is_met(self, best: Score | None) -> bool:
        """Whether ``best`` satisfies the goal — the loop's early-stop check.

        With no ``target`` set (or nothing scored yet) the goal is never met,
        so the climb runs until the budget is exhausted.
        """
        if best is None or self.target is None:
            return False
        return best.value >= self.target


class Timeouts(BaseModel):
    """Wall-clock ceilings for the subprocesses a climb shells out to.

    Nothing the runner spawns — an agent CLI, the preflight probe, the scorer —
    can be trusted to always return: a wedged ``claude`` process or a hung eval
    would otherwise stall a multi-hour climb forever. Each ceiling caps one kind
    of child; hitting it kills the child and fails that step (the agent/scorer
    with an error, a per-cycle scorer as a ``0.0``). ``None`` disables a ceiling.

    Defaults are deliberately generous for real agent/scorer work and short for
    the health-check probe, which is a single one-token round-trip.
    """

    agent_seconds: float | None = 1800.0  # a single agent run (propose/apply)
    verify_seconds: float | None = 120.0  # the preflight model probe (see Harness.verify_model)
    scorer_seconds: float | None = 600.0  # one scorer invocation (see scoring.run_scorer_command)


class Budget(BaseModel):
    """Hard stop condition. v1: number of iterations only."""

    cycles: int  # number of runs to attempt

    def is_exhausted(self, completed: int) -> bool:
        """Whether ``completed`` cycles have used up the budget."""
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
    scorer: Scorer  # the fitness function (v1: exactly one)
    # The OS sandbox that confines every agent CLI to its run's worktree. A
    # ``hillclimber.toml`` with no ``[sandbox]`` table gets the Seatbelt default.
    sandbox: SandboxConfig = Field(default_factory=SeatbeltSandboxConfig)
    # Which strategy runs the climb; resolved and validated by hillclimber.strategies.registry.
    strategy: str = DEFAULT_STRATEGY
    goal: Goal = Field(default_factory=Goal)  # what the climb optimizes toward
    budget: Budget  # hard stop condition (v1: cycles only)
    # Wall-clock ceilings on the subprocesses the climb spawns, so a wedged agent
    # CLI or a hung eval can never stall the run forever (see ``Timeouts``).
    timeout: Timeouts = Field(default_factory=Timeouts)
    # One ``[agents.<role>]`` table per role. Which roles are required is the
    # strategy's declaration, checked by ``hillclimber.strategies.registry.verify_agents``.
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
    parent_ref: str  # the ref this cycle forked from
    branch: str  # the experiment branch
    worktree: str  # e.g. hc_a1b2_cycle_001/
    hypothesis: str  # what this cycle tried (e.g. "Use pydantic...")
    score_before: Score
    score_after: Score | None = None
    status: CycleStatus
    commit_sha: str | None = None  # pointer to the actual change

    @property
    def cycle_id(self) -> str:
        """Human-facing cycle label, e.g. ``cyc_001`` for ``index == 1``."""
        return f"cyc_{self.index:03d}"


# The experiment lock events. One artefact has one ``hillclimber.lock`` — an
# append-only JSONL log under ``.hillclimber/`` holding every experiment ever
# run against it (see ``hillclimber.lockfile``). Each line is one of these
# events; the ``event`` field is the discriminator. The log is never rewritten
# or truncated — history only accumulates.


class ExperimentStarted(BaseModel):
    """The first line an experiment appends to ``hillclimber.lock``."""

    event: Literal["experiment_started"] = "experiment_started"
    experiment_id: str  # exp_<8hex>, minted by Strategy.new_experiment_id
    strategy: str  # which strategy drives the climb, e.g. "chain"
    baseline_score: Score  # the number every cycle must beat
    budget: Budget  # total planned cycles
    timestamp: datetime = Field(default_factory=_utcnow)


class CycleRecorded(BaseModel):
    """A settled cycle promoted from its ``cyc_<NNN>.lock`` into the experiment log.

    Carries the full ``Cycle`` verbatim — the promotion *is* the cycle record,
    and ``cycle.experiment_id`` is the single owner reference (not duplicated
    here, so the two can never drift).
    """

    event: Literal["cycle_recorded"] = "cycle_recorded"
    cycle: Cycle
    timestamp: datetime = Field(default_factory=_utcnow)


class ExperimentFinished(BaseModel):
    """The terminal line for one experiment. Absent -> running or interrupted."""

    event: Literal["experiment_finished"] = "experiment_finished"
    experiment_id: str
    outcome: Literal["completed", "failed"]
    completed: int  # cycles that settled
    best_cycle_id: str | None = None  # e.g. "cyc_002"; None if nothing scored
    timestamp: datetime = Field(default_factory=_utcnow)


# One line of ``hillclimber.lock``; ``event`` discriminates the kind.
LockEvent = Annotated[
    ExperimentStarted | CycleRecorded | ExperimentFinished,
    Field(discriminator="event"),
]


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
    delta: float  # score_after - baseline (computed)
    # Where the cycle's change lives, so ``hillclimber status`` can point at the
    # winner and print a real merge command. The branch outlives the worktree
    # (reset keeps branches), so it is the durable ref to merge.
    branch: str = ""
    worktree: str = ""
    commit_sha: str | None = None

    @classmethod
    def from_cycle(cls, cycle: Cycle, baseline: Score) -> CycleSummary:
        """Flatten a settled ``cycle`` into its display summary.

        ``delta`` is the cycle's improvement over the *baseline*; an unscored
        cycle (no ``score_after``) reports a zero delta. The one flattening
        used by both the live loop (``Chain.execute``) and the lock-file
        fold (``lockfile.fold_statuses``), so the two can never drift.
        """
        after = cycle.score_after
        delta = after.value - baseline.value if after is not None else 0.0
        return cls(
            experiment_id=cycle.experiment_id,
            cycle_id=cycle.cycle_id,
            status=cycle.status,
            hypothesis=cycle.hypothesis,
            score_after=after,
            delta=delta,
            branch=cycle.branch,
            worktree=cycle.worktree,
            commit_sha=cycle.commit_sha,
        )


class ExperimentStatus(BaseModel):
    """Assembled fresh by folding together the cycles on disk. Powers
    ``hillclimber status``. ``best`` is COMPUTED (``max(cycles, key=score)``),
    not persisted anywhere."""

    experiment_id: str = ""
    baseline_score: Score
    cycles: list[CycleSummary]
    best: CycleSummary | None = None  # COMPUTED, not stored
    completed: int
    total: int
    # Folded from the lock: no ExperimentFinished line -> "running", which also
    # covers *interrupted* — the log cannot tell a live run from a crashed one.
    state: Literal["running", "completed", "failed"] = "completed"
