"""Scores ``slow_stats.analyze`` on correctness **and** speed.

The climb here is a *performance* climb: the baseline already returns the right
answer, it is just slow. Each cycle should make ``analyze`` faster while keeping
every returned value identical.

How the score is built:

- A fixed, deterministic list of integers is generated (same list every run, so
  scores are comparable across cycles).
- An independent, trusted reference computes the correct ``Report`` values.
- ``analyze`` is timed over a few repetitions; the fastest run is used.
- ``score = correctness * speed`` where ``correctness`` is the fraction of the
  four fields that match the reference (a hard-ish gate: wrong answers can't win
  on speed) and ``speed`` maps the elapsed time smoothly into ``(0, 1]``.

Because ``speed`` only *approaches* 1.0 as the code approaches instant, the goal
target of 1.0 is never quite reached — the climb spends its whole budget and you
see the score rise cycle over cycle.
"""

from __future__ import annotations

import random
import time
from collections import Counter
from math import isqrt

from slow_stats import Report, analyze

import hillclimber

# ----------------------------------------------------------------------------- #
# Fixed workload
# ----------------------------------------------------------------------------- #

SEED = 20260701
SIZE = 15000  # how many integers to analyze
MAX_VALUE = 6000  # values are drawn from [2, MAX_VALUE]
REPEATS = 3  # time this many runs and keep the fastest

# Calibration knob: the elapsed time (ms) at which the speed component is 0.5.
# Tuned so the intentionally-slow baseline lands around the middle of the range
# (~0.45), leaving clear room to climb toward ~0.99 as the bottlenecks go away.
# Lower it if your machine is fast; raise it if slow.
REFERENCE_MS = 1200.0


def _workload() -> list[int]:
    """The fixed list of integers every cycle is scored against."""
    rng = random.Random(SEED)
    return [rng.randint(2, MAX_VALUE) for _ in range(SIZE)]


# ----------------------------------------------------------------------------- #
# Independent reference — the source of truth for correctness
# ----------------------------------------------------------------------------- #


def _is_prime(n: int) -> bool:
    """A correct, efficient primality test used only to check the artefact."""
    if n < 2:
        return False
    return all(n % d != 0 for d in range(2, isqrt(n) + 1))


def _reference(numbers: list[int]) -> Report:
    """Compute the known-correct ``Report`` independently of the artefact."""
    counts = Counter(numbers)
    top_three = sorted(counts.items(), key=lambda vc: (-vc[1], vc[0]))[:3]
    return Report(
        total=len(numbers),
        distinct=len(counts),
        top_three=[(value, count) for value, count in top_three],
        prime_count=sum(1 for x in numbers if _is_prime(x)),
    )


# ----------------------------------------------------------------------------- #
# Scoring
# ----------------------------------------------------------------------------- #


def _field_matches(predicted: Report, expected: Report) -> dict[str, bool]:
    """Which of the four report fields the artefact got right."""
    return {
        "total": predicted.total == expected.total,
        "distinct": predicted.distinct == expected.distinct,
        # Normalize to lists so a tuple/list mismatch doesn't count as wrong.
        "top_three": [list(t) for t in predicted.top_three] == [list(t) for t in expected.top_three],
        "prime_count": predicted.prime_count == expected.prime_count,
    }


def _time_analyze(numbers: list[int]) -> tuple[Report, float]:
    """Run ``analyze`` ``REPEATS`` times; return one result and the best time (ms)."""
    best_ms = float("inf")
    result: Report | None = None
    for _ in range(REPEATS):
        start = time.perf_counter()
        result = analyze(numbers)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        best_ms = min(best_ms, elapsed_ms)
    assert result is not None  # REPEATS >= 1
    return result, best_ms


def evaluate() -> hillclimber.Eval:
    numbers = _workload()
    expected = _reference(numbers)

    try:
        predicted, elapsed_ms = _time_analyze(numbers)
        fields = _field_matches(predicted, expected)
    except Exception as exc:  # a broken artefact scores zero, it never wins on speed
        return hillclimber.Eval(score=0.0, details={"error": repr(exc)})

    correctness = sum(fields.values()) / len(fields)
    speed = REFERENCE_MS / (REFERENCE_MS + elapsed_ms)
    score = correctness * speed

    return hillclimber.Eval(
        score=score,
        details={
            "correctness": correctness,
            "speed": speed,
            "elapsed_ms": round(elapsed_ms, 3),
            "reference_ms": REFERENCE_MS,
            "fields": fields,
        },
    )


if __name__ == "__main__":
    # The runner scores by reading the last JSON line on stdout (see
    # hillclimber.scoring._parse_eval). Keep the Eval the last thing printed.
    print(evaluate().model_dump_json())
