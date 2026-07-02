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
