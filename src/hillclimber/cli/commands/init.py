"""``hillclimber init`` — scaffold a new experiment.

The fast, synchronous counterpart to ``run``: it writes a starter
``hillclimber.toml`` and ``eval.py`` into a directory, then points the user at
``run``. No async core is involved, so there is no ``asyncio.run`` bridge here —
it returns in milliseconds.

``--interactive`` / ``-i`` swaps the fixed template for a short wizard (the
intended first-run path): it confirms where the artefact lives, asks for the
budget, model, and target with sensible defaults, and generates a
``hillclimber.toml`` that says exactly what the user answered — no surprise
config. The wizard stays synchronous too: it is pure terminal I/O.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt

from hillclimber.cli.banner import print_banner
from hillclimber.cli.console import console, err_console
from hillclimber.config import HILLCLIMBER_TOML
from hillclimber.models import DEFAULT_STRATEGY
from hillclimber.strategies.base import DEFAULT_HARNESS
from hillclimber.strategies.registry import get_strategy

EVAL_PY = "eval.py"

# The models the wizard offers. hillclimber currently drives agents through the
# ``claude`` CLI only, so these are the current Anthropic model aliases; the
# first entry is the default. ``run`` preflight-verifies whatever ends up in the
# toml, so a hand-edited model id is still caught before a climb starts.
MODEL_CHOICES = [
    "claude-sonnet-4-6",
    "claude-opus-4-8",
    "claude-haiku-4-5",
]

DEFAULT_CYCLES = 5
DEFAULT_TARGET = 1.0


def _render_toml(
    *,
    cycles: int = DEFAULT_CYCLES,
    target: float = DEFAULT_TARGET,
    model: str = MODEL_CHOICES[0],
) -> str:
    """Render the starter ``hillclimber.toml`` with the given knobs filled in.

    The ``[agents.<role>]`` tables are derived from the default strategy's own
    role declaration (``Strategy.roles``), so the scaffold and the requirement
    check can never drift apart.
    """
    agent_tables = "\n".join(
        f'# {spec.description}\n[agents.{role}]\nharness = "{DEFAULT_HARNESS}"\nmodel = "{model}"\n'
        for role, spec in get_strategy(DEFAULT_STRATEGY).roles.items()
    )
    return f"""\
# hillclimber.toml — describes one experiment. This file lives at the artefact
# root: the scorer runs at each cycle's checkout root, so every path in here is
# relative to this directory.
path_to_artefact = "."
strategy = "{DEFAULT_STRATEGY}"

# What the climb optimizes toward; the run stops early once `target` is reached.
[goal]
direction = "maximize"
target = {target}

# Hard stop: how many improvement cycles to attempt.
[budget]
cycles = {cycles}

# The fitness function: a command that prints an Eval as JSON on stdout
# (see eval.py). Its score is the number the climb pushes up.
[scorer]
kind = "command"
cmd = "python eval.py"


# kind = "none" to switch the sandbox off.
[sandbox]
kind = "seatbelt"

{agent_tables}"""


# The starter config. Must stay loadable by ``hillclimber.config.load_config``
# as-is (tests assert this), so a user can fill in evaluate() and run without
# touching the toml.
TOML_TEMPLATE = _render_toml()

EVAL_TEMPLATE = '''\
"""Fitness function for this experiment — fill in ``evaluate()``.

hillclimber runs this file (the ``[scorer]`` cmd in hillclimber.toml) at the
root of each cycle's checkout and reads the last stdout line that is a JSON
object marked ``"hillclimber_eval": 1`` — the envelope ``EvalResult.to_json()``
produces. ``score`` is the climbable number: higher is better, typically in
[0, 1], and it must be finite. ``details`` is optional richness for
tracing/inspection and never affects the climb.

Deliberately stdlib-only: nothing (not even hillclimber) needs to be installed
in your project for this file to run. Verify it conforms with:

    hillclimber check
"""

import json
from dataclasses import dataclass, field


@dataclass
class EvalResult:
    """What ``evaluate()`` returns: the score, plus anything worth inspecting."""

    score: float  # higher is better, typically in [0, 1]
    details: dict = field(default_factory=dict)

    def to_json(self) -> str:
        """The envelope hillclimber reads — keep its shape exactly."""
        return json.dumps({"hillclimber_eval": 1, "score": float(self.score), "details": self.details})


def evaluate() -> EvalResult:
    # TODO: measure your artefact and turn the result into a score, e.g.:
    #   predictions = run_my_pipeline()
    #   score = fraction_correct(predictions)
    #   return EvalResult(score=score, details={"per_case": ...})
    return EvalResult(score=0.0, details={"todo": "implement evaluate()"})


if __name__ == "__main__":
    print(evaluate().to_json())  # must stay the last line printed
'''


def _inside_git_repo(directory: Path) -> bool:
    """Whether ``directory`` sits inside a git repository (``.git`` in it or any parent)."""
    resolved = directory.resolve()
    return any((candidate / ".git").exists() for candidate in (resolved, *resolved.parents))


def _write_scaffold(target_dir: Path, toml_content: str, *, force: bool, confirm_overwrite: bool) -> None:
    """Write ``hillclimber.toml`` and ``eval.py`` into ``target_dir``.

    The overwrite guard is all-or-nothing: nothing is written if any scaffold
    file already exists and the user hasn't opted in — either via ``--force``
    or, in the wizard (``confirm_overwrite``), by answering an explicit prompt.
    """
    files = {
        target_dir / HILLCLIMBER_TOML: toml_content,
        target_dir / EVAL_PY: EVAL_TEMPLATE,
    }

    existing = [p for p in files if p.exists()]
    if existing and not force:
        names = ", ".join(str(p) for p in existing)
        if not confirm_overwrite:
            err_console.print(f"[red]refusing to overwrite[/] existing {names} (pass [bold]--force[/] to replace)")
            raise typer.Exit(code=1)
        if not Confirm.ask(f"Overwrite existing [bold]{names}[/]?", default=False):
            err_console.print("[red]aborted[/] — existing files left untouched")
            raise typer.Exit(code=1)

    target_dir.mkdir(parents=True, exist_ok=True)
    for file_path, content in files.items():
        file_path.write_text(content)
        console.print(f"[green]wrote[/] {file_path}")


def _ask_target_dir(suggestion: Path) -> Path:
    """Confirm the artefact directory, defaulting to ``suggestion`` (usually the cwd)."""
    if Confirm.ask(f"Set up the experiment in [bold]{suggestion.resolve()}[/]?", default=True):
        return suggestion
    raw = Prompt.ask("Path to the project you want to climb")
    return Path(raw).expanduser()


def _ask_cycles() -> int:
    """Ask for the cycle budget; re-ask until it is a positive integer."""
    while True:
        cycles = IntPrompt.ask("Improvement cycles (the hard stop)", default=DEFAULT_CYCLES)
        if cycles >= 1:
            return cycles
        console.print("[red]cycles must be at least 1[/]")


def _ask_model() -> str:
    """Ask which Claude model powers the agent roles."""
    console.print("Which Claude model should power the agents?")
    for index, model in enumerate(MODEL_CHOICES, start=1):
        console.print(f"  {index}) {model}")
    choice = IntPrompt.ask("Model", choices=[str(i) for i in range(1, len(MODEL_CHOICES) + 1)], default=1)
    return MODEL_CHOICES[choice - 1]


def _run_wizard(suggestion: Path, force: bool) -> None:
    """The ``--interactive`` flow: banner, questions, scaffold, next steps."""
    print_banner(console)
    console.print("Let's set up your experiment. Enter accepts the [bold]\\[default][/].\n")

    target_dir = _ask_target_dir(suggestion)
    if not _inside_git_repo(target_dir):
        console.print(
            f"[yellow]note:[/] {target_dir.resolve()} is not inside a git repository — "
            "[bold]hillclimber run[/] needs one (run [bold]git init[/] there first)."
        )

    cycles = _ask_cycles()
    model = _ask_model()
    target = FloatPrompt.ask("Target score (the climb stops early once reached)", default=DEFAULT_TARGET)

    console.print()
    _write_scaffold(
        target_dir,
        _render_toml(cycles=cycles, target=target, model=model),
        force=force,
        confirm_overwrite=True,
    )

    console.print("\nNext steps:")
    console.print(f"  1. Fill in [bold]evaluate()[/] in {target_dir / EVAL_PY} so it scores your artefact.")
    console.print(f"  2. Run [bold]hillclimber check {target_dir}[/] until the eval conforms.")
    console.print(f"  3. Run [bold]hillclimber run {target_dir}[/] to start climbing.")


def init(
    path: Annotated[Path | None, typer.Argument(help="Directory to scaffold the experiment in.")] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite existing scaffold files.")] = False,
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive", "-i", help="Answer a few questions to compose the config instead of a fixed template."
        ),
    ] = False,
) -> None:
    """Scaffold a new experiment"""
    target_dir = path or Path()

    if interactive:
        # Ctrl-C or a closed stdin mid-wizard is an abort, not a stack trace.
        try:
            _run_wizard(target_dir, force)
        except (KeyboardInterrupt, EOFError):
            err_console.print("\n[red]aborted[/] — nothing was written")
            raise typer.Exit(code=1) from None
        return

    _write_scaffold(target_dir, TOML_TEMPLATE, force=force, confirm_overwrite=False)

    console.print("\nNext steps:")
    console.print(f"  1. Fill in [bold]evaluate()[/] in {target_dir / EVAL_PY} so it scores your artefact.")
    console.print(f"  2. Run [bold]hillclimber check {target_dir}[/] until the eval conforms.")
    console.print(
        f"  3. Adjust [bold]{target_dir / HILLCLIMBER_TOML}[/] (budget, models), then [bold]hillclimber run {target_dir}[/]."
    )
