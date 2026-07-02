"""Scoring measures the code in ``cwd`` — the worktree, not the original.

Regression tests for the bug where an *absolute* scorer path pinned every cycle
to the baseline. The scorer runs with ``cwd`` set to the cycle's worktree, but a
command like ``python /original/eval.py`` sets ``sys.path[0]`` to the script's
own directory, so ``eval.py`` imported the *original* module regardless of cwd —
the worker's committed changes were never scored (see ``hillclimber.toml`` scorer
cmd). These tests mirror that shape: ``eval.py`` imports a sibling module (as the
real eval imports ``ocr_pipeline``), and the two copies score differently.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from hillclimber.models import CommandScorer, Eval
from hillclimber.scoring import parse_eval, score_artefact


def _make_artefact(root: Path, score: float) -> None:
    """A minimal artefact whose score lives in a *sibling* module ``eval.py``
    imports — so which copy is on ``sys.path`` decides the score."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "target.py").write_text(f"SCORE = {score}\n")
    (root / "eval.py").write_text(
        "import json\nfrom target import SCORE\nprint(json.dumps({'hillclimber_eval': 1, 'score': SCORE}))\n"
    )


def test_command_scorer_scores_the_copy_in_cwd(tmp_path: Path):
    # The fix: a cwd-relative command runs the worktree's eval.py, which imports
    # the worktree's (improved) target — so the score reflects the change.
    original = tmp_path / "original"
    worktree = tmp_path / "worktree"
    _make_artefact(original, 0.10)
    _make_artefact(worktree, 0.90)  # the worker's improved copy

    scorer = CommandScorer(cmd=f"{sys.executable} eval.py")  # relative -> cwd
    score = asyncio.run(score_artefact(scorer, worktree))

    assert score.passed
    assert score.value == 0.90  # the worktree copy, not the original's 0.10


def test_absolute_scorer_path_scores_the_wrong_copy(tmp_path: Path):
    # The bug, pinned so it can't silently come back: an absolute script path sets
    # sys.path[0] to the script's OWN dir, so eval.py imports the original target
    # regardless of cwd. The worktree's changes are ignored and the climb stays at
    # baseline. (This documents why the scorer cmd must be cwd-relative.)
    original = tmp_path / "original"
    worktree = tmp_path / "worktree"
    _make_artefact(original, 0.10)
    _make_artefact(worktree, 0.90)

    scorer = CommandScorer(cmd=f"{sys.executable} {original / 'eval.py'}")  # absolute
    score = asyncio.run(score_artefact(scorer, worktree))

    # Scores the ORIGINAL 0.10, never the worktree's 0.90 — isolation defeated.
    assert score.value == 0.10


# --------------------------------------------------------------------------- #
# envelope recognition (parse_eval)
# --------------------------------------------------------------------------- #


def test_parse_eval_takes_the_last_marked_line():
    stdout = '{"hillclimber_eval": 1, "score": 0.1}\n{"hillclimber_eval": 1, "score": 0.9}\n'
    assert parse_eval(stdout).score == 0.9


def test_parse_eval_ignores_unmarked_json():
    # A score-shaped line without the marker (a metrics dump, an API response the
    # artefact printed) must never be mistaken for the eval result.
    stdout = 'loading...\n{"score": 0.99, "loss": 0.01}\n{"hillclimber_eval": 1, "score": 0.5}\n'
    assert parse_eval(stdout).score == 0.5


def test_parse_eval_raises_without_an_envelope():
    # Score-shaped but unmarked output is "no result", with the contract spelled
    # out in the error.
    with pytest.raises(ValueError, match="hillclimber_eval"):
        parse_eval('{"score": 0.9}\n')


def test_parse_eval_treats_a_broken_envelope_as_an_error_not_noise():
    # The eval clearly tried to emit a result; scanning past it to the earlier
    # (stale) envelope would hide the bug.
    stdout = '{"hillclimber_eval": 1, "score": 0.5}\n{"hillclimber_eval": 1, "details": {}}\n'
    with pytest.raises(ValueError, match="does not match the schema"):
        parse_eval(stdout)


def test_parse_eval_rejects_unsupported_envelope_versions():
    with pytest.raises(ValueError, match="does not match the schema"):
        parse_eval('{"hillclimber_eval": 2, "score": 0.5}\n')


def test_parse_eval_rejects_a_non_finite_score():
    # NaN/inf would poison best-so-far comparisons, so they fail validation.
    with pytest.raises(ValueError, match="finite"):
        parse_eval('{"hillclimber_eval": 1, "score": NaN}\n')


def test_eval_model_serializes_the_envelope_marker():
    # Evals that import hillclimber and print Eval(...).model_dump_json() emit
    # the same envelope the stdlib scaffold does — one wire contract.
    payload = json.loads(Eval(score=0.5).model_dump_json())
    assert payload["hillclimber_eval"] == 1
    assert parse_eval(Eval(score=0.5).model_dump_json()).score == 0.5
