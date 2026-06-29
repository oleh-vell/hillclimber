# eval.py  — user fills in evaluate()
"""Scores ``ocr_pipeline`` against known ground truth.

Runs the pipeline over every cover in ``eval_data/`` and compares the extracted
``Book`` to the expected title/author. The score is the fraction of fields
(title + author, across all books) that match after light normalization, so it
moves smoothly from 0.0 (all wrong) to 1.0 (every field correct).
"""

import re

import hillclimber

from ocr_pipeline import Book, run

# Ground truth, keyed by the image filename in eval_data/.
GROUND_TRUTH: dict[str, Book] = {
    "james_bond_01_casino_royale_600x.jpg.webp": Book(
        title="Casino Royale", author="Ian Fleming"
    ),
    "james_bond_02_live_and_let_die_1200x.jpg.webp": Book(
        title="Live and Let Die", author="Ian Fleming"
    ),
    "james_bond_04_diamonds_are_forever_600x.jpg.webp": Book(
        title="Diamonds Are Forever", author="Ian Fleming"
    ),
    "james_bond_07_goldfinger_600x.jpg.webp": Book(
        title="Goldfinger", author="Ian Fleming"
    ),
}


def _normalize(text: str) -> str:
    """Lowercase and strip everything but alphanumerics for forgiving matching."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _field_matches(predicted: Book, expected: Book) -> int:
    """Count how many of (title, author) match for one book (0, 1, or 2)."""
    matches = 0
    matches += _normalize(predicted.title) == _normalize(expected.title)
    matches += _normalize(predicted.author) == _normalize(expected.author)
    return matches


def evaluate() -> hillclimber.Eval:
    predictions = run()

    correct = 0
    total = 0
    per_book: dict[str, int] = {}
    for name, expected in GROUND_TRUTH.items():
        total += 2  # two fields per book
        predicted = predictions.get(name)
        hits = _field_matches(predicted, expected) if predicted else 0
        correct += hits
        per_book[name] = hits

    score = correct / total if total else 0.0
    return hillclimber.Eval(
        score=score,
        details={"correct_fields": correct, "total_fields": total, "per_book": per_book},
    )


if __name__ == "__main__":
    result = evaluate()
    print(f"score={result.score:.3f} {result.details}")
