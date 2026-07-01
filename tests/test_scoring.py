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
import sys
from pathlib import Path

from hillclimber.models import CommandScorer
from hillclimber.scoring import score_artefact


def _make_artefact(root: Path, score: float) -> None:
    """A minimal artefact whose score lives in a *sibling* module ``eval.py``
    imports — so which copy is on ``sys.path`` decides the score."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "target.py").write_text(f"SCORE = {score}\n")
    (root / "eval.py").write_text("import json\nfrom target import SCORE\nprint(json.dumps({'score': SCORE}))\n")


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
