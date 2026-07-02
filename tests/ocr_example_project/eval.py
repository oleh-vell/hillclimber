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
import re
from dataclasses import dataclass, field


@dataclass
class EvalResult:
    """What ``evaluate()`` returns: the score, plus anything worth inspecting."""

    score: float  # higher is better, typically in [0, 1]
    details: dict = field(default_factory=dict)

    def to_json(self) -> str:
        """The envelope hillclimber reads — keep its shape exactly."""
        return json.dumps({"hillclimber_eval": 1, "score": float(self.score), "details": self.details})


# Ground truth keyed by filename in eval_data/.
_GROUND_TRUTH: dict[str, tuple[str, str]] = {
    "james_bond_01_casino_royale_600x.jpg.webp": ("Casino Royale", "Ian Fleming"),
    "james_bond_02_live_and_let_die_1200x.jpg.webp": ("Live and Let Die", "Ian Fleming"),
    "james_bond_04_diamonds_are_forever_600x.jpg.webp": ("Diamonds Are Forever", "Ian Fleming"),
    "james_bond_07_goldfinger_600x.jpg.webp": ("Goldfinger", "Ian Fleming"),
}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def evaluate() -> EvalResult:
    from ocr_pipeline import run  # noqa: PLC0415

    predictions = run()

    correct = 0
    total = 0
    per_book: dict[str, int] = {}
    for name, (exp_title, exp_author) in _GROUND_TRUTH.items():
        total += 2
        book = predictions.get(name)
        if book is None:
            per_book[name] = 0
            continue
        hits = int(_normalize(book.title) == _normalize(exp_title)) + int(
            _normalize(book.author) == _normalize(exp_author)
        )
        correct += hits
        per_book[name] = hits

    score = correct / total if total else 0.0
    return EvalResult(
        score=score,
        details={"correct_fields": correct, "total_fields": total, "per_book": per_book},
    )


if __name__ == "__main__":
    print(evaluate().to_json())  # must stay the last line printed
