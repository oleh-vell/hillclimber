"""A small statistics pipeline over a list of integers.

The public contract is one function::

    analyze(numbers: list[int]) -> Report

``Report`` carries four fields (see below). The numbers are supplied by the
scorer (``eval.py``); this module only has to turn them into a correct
``Report``.

The implementation here is intentionally naive: every metric is computed in the
most obvious, brute-force way. It produces the *right* answer, just slowly — so
there is real headroom for an optimizing pass to make it faster **without**
changing any of the returned values. Keep ``analyze`` returning a ``Report`` with
the same four fields and the same numbers; only the *how* should change.
"""

from __future__ import annotations

from pydantic import BaseModel


class Report(BaseModel):
    """The computed statistics for one list of integers."""

    total: int  # how many numbers were seen
    distinct: int  # how many distinct values appear
    top_three: list[tuple[int, int]]  # the 3 most common (value, count), most common first
    prime_count: int  # how many of the numbers are prime


def _is_prime(n: int) -> bool:
    """Whether ``n`` is prime, by trial division against every smaller number."""
    if n < 2:
        return False
    # Test every candidate divisor from 2 up to n-1. This is far more work than
    # necessary, but it is unambiguously correct.
    return all(n % d != 0 for d in range(2, n))


def _distinct_count(numbers: list[int]) -> int:
    """Count distinct values by remembering the ones already seen in a list."""
    seen: list[int] = []
    for x in numbers:
        # ``x in seen`` scans the whole list every time, so this loop is O(n^2).
        if x not in seen:
            seen.append(x)
    return len(seen)


def _top_three(numbers: list[int]) -> list[tuple[int, int]]:
    """Find the three most common values, ties broken by smaller value first."""
    counts: list[tuple[int, int]] = []
    for x in numbers:
        # Skip values we have already tallied.
        if any(value == x for value, _ in counts):
            continue
        # Re-scan the entire list to count this value.
        counts.append((x, numbers.count(x)))
    counts.sort(key=lambda vc: (-vc[1], vc[0]))
    return counts[:3]


def _prime_count(numbers: list[int]) -> int:
    """Count how many of ``numbers`` are prime."""
    total = 0
    for x in numbers:
        if _is_prime(x):
            total += 1
    return total


def analyze(numbers: list[int]) -> Report:
    """Compute the full :class:`Report` for ``numbers``."""
    return Report(
        total=len(numbers),
        distinct=_distinct_count(numbers),
        top_three=_top_three(numbers),
        prime_count=_prime_count(numbers),
    )


if __name__ == "__main__":
    import random

    rng = random.Random(0)
    sample = [rng.randint(2, 6000) for _ in range(15000)]
    print(analyze(sample).model_dump_json(indent=2))
