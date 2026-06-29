"""Pydantic models for hillclimber.

Three levels of types:

- **Write models** (persisted, authoritative): ``Experiment`` -> ``hillclimber.toml``,
  ``Run`` -> ``run_<id>.lock``.
- **Read model** (computed on demand, never stored): ``ExperimentStatus`` /
  ``RunSummary``. Powers ``hillclimber status``. *Best-so-far* is computed here,
  not persisted anywhere.
- **Shared value types**: ``Agent``, ``Scorer``, ``Score``, ``RunStatus``, etc.

Convention: anything named ``...Status`` / ``...Summary`` is a read model — build
it, print it, discard it. It must never gain a method that mutates or persists.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

from hillclimber import prompt

# --------------------------------------------------------------------------- #
# Shared value types
# --------------------------------------------------------------------------- #


class RunStatus(str, Enum):
    """Lifecycle of a single run."""

    running = "running"
    scored = "scored"
    accepted = "accepted"
    rejected = "rejected"
    failed = "failed"


class Agent(BaseModel):
    """A reusable agent configuration. Three roles reference it (see AgentRoles).

    ``system_prompt`` is optional: when omitted from the config, ``Config`` fills
    in the role default from ``hillclimber.prompt`` (see ``Config`` validator).
    """

    harness: str  # e.g. "claude_code", "api"
    model: str
    system_prompt: str | None = None  # None -> role default from hillclimber.prompt
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
        rest["params"] = {**extra, **rest.get("params", {})}
        return rest


class CommandScorer(BaseModel):
    """Score by running a command (e.g. a test suite)."""

    kind: Literal["command"] = "command"
    cmd: str  # e.g. "pytest test_eval.py"


# The fitness function. One scorer per experiment; the discriminated union is
# the seam for adding kinds (e.g. a judge) later.
Scorer = Annotated[
    Union[CommandScorer],
    Field(discriminator="kind"),
]


class Score(BaseModel):
    """Comparable and composable — not a bare float, so accept logic stays
    uniform across scorer kinds."""

    value: float
    passed: bool
    scorer_id: str  # which scorer produced it


class Eval(BaseModel):
    """The score returned by the user's eval file.

    This is the contract between hillclimber and the artefact. The scaffolded
    ``test_eval_hillclimber.py`` exposes ``evaluate() -> Eval``; the user fills in
    the body to produce ``score``. Higher is better; the climb pushes it up
    (e.g. 0.6 -> 0.9). ``details`` is optional richness for the trace/viewer
    (per-case breakdown, sub-metrics) and never affects the climb.
    """

    score: float  # the climbable number, typically in [0, 1]
    details: dict = Field(default_factory=dict)  # optional, for tracing/inspection


class Goal(BaseModel):
    """Definition of success — what the climb optimizes toward.

    v1 is intentionally minimal: maximize the eval score. The field is kept
    explicit (rather than hardcoded in the loop) so direction/target can grow
    later without reshaping the model.
    """

    direction: str = "maximize"  # v1: "maximize" only
    target: float | None = (
        None  # optional success threshold (early-stop hook; unused in v1)
    )


class Budget(BaseModel):
    """Hard stop condition. v1: number of iterations only."""

    cycles: int  # number of runs to attempt


# --------------------------------------------------------------------------- #
# Write models (persisted, authoritative)
# --------------------------------------------------------------------------- #


class Config(BaseModel):
    """The config. Describes what to do. Maps to ``hillclimber.toml``."""

    path_to_artefact: str
    baseline_score: Score | None = None  # scored once, before any run
    scorer: Scorer  # the fitness function (v1: exactly one)
    strategy: Literal["chain"] = "chain"  # valid strategies; v1: "chain" only
    goal: Goal = Field(default_factory=Goal)  # what the climb optimizes toward
    budget: Budget  # hard stop condition (v1: cycles only)
    hillclimber_agent: Agent
    worker_agent: Agent
    reflector_agent: Agent

    @model_validator(mode="after")
    def _fill_role_prompts(self) -> Config:
        """Default each role's ``system_prompt`` from ``hillclimber.prompt``.

        Prompts are optional in the config (see ``Agent``): a role left without a
        ``system_prompt`` inherits the role default, while one set in the toml is
        left untouched as an override.
        """
        defaults = {
            "hillclimber_agent": prompt.HILLCLIMBER_AGENT,
            "worker_agent": prompt.WORKER_AGENT,
            "reflector_agent": prompt.REFLECTOR_AGENT,
        }
        for role, default in defaults.items():
            agent = getattr(self, role)
            if agent.system_prompt is None:
                agent.system_prompt = default
        return self


class Run(BaseModel):
    """One hypothesis attempt in its own git worktree. Maps to ``run_<id>.lock``.

    The set of runs is just the collection of these files on disk — there is no
    parent object holding the list.
    """

    id: str  # ULID-suffixed, globally unique & sortable
    parent_ref: str  # baseline in v1; seam for later strategies
    branch: str  # the experiment branch
    worktree: str  # e.g. hc_run_<id>/
    hypothesis: str  # what this run tried (e.g. "Use pydantic...")
    score_before: Score
    score_after: Score | None = None
    accepted: bool = False
    status: RunStatus
    commit_sha: str | None = None  # pointer to the actual change
    agents_used: AgentRoles | None = None  # only if agents vary per run; else omit


# --------------------------------------------------------------------------- #
# Read model (computed on demand, never stored)
# --------------------------------------------------------------------------- #


class RunSummary(BaseModel):
    """A flattened, display-oriented view of a single run."""

    id: str
    status: RunStatus
    score_after: Score | None = None
    accepted: bool
    delta: float  # score_after - baseline (computed)


class ExperimentStatus(BaseModel):
    """Assembled fresh by folding together the runs on disk. Powers
    ``hillclimber status``. ``best`` is COMPUTED (``max(runs, key=score)``),
    not persisted anywhere."""

    baseline_score: Score
    runs: list[RunSummary]
    best: RunSummary | None = None  # COMPUTED, not stored
    in_progress: list[str] = Field(default_factory=list)  # run ids currently running
    completed: int
    total: int
