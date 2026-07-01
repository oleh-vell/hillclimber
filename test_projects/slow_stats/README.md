# slow_stats — a performance-climb test project

A tiny, self-contained artefact for exercising hillclimber over **more than one
cycle** and watching the score go up.

`slow_stats.analyze(numbers)` computes four statistics over a list of integers:

| field | meaning |
| --- | --- |
| `total` | how many numbers were seen |
| `distinct` | how many distinct values appear |
| `top_three` | the three most common `(value, count)` pairs |
| `prime_count` | how many of the numbers are prime |

The baseline implementation is **correct but deliberately slow**. It has three
independent bottlenecks, each an obvious, self-contained optimization:

1. **Primes** — `_is_prime` uses trial division against *every* smaller number
   (should stop at `√n`, or use a sieve).
2. **Distinct** — `_distinct_count` does an `x in seen` scan of a growing list,
   making it O(n²) (should use a `set`).
3. **Top-K** — `_top_three` calls `list.count()` once per distinct value,
   re-scanning the whole list each time (should use `collections.Counter`).

Because they're independent, a chain of cycles can knock them out one at a time,
and the score rises at each step.

## How it's scored

`eval.py` is the fitness function. It:

- builds a **fixed, deterministic** list of integers (same every run, so scores
  are comparable across cycles);
- computes the correct answer with an **independent reference** implementation;
- times `analyze` over a few repetitions and keeps the fastest;
- returns `score = correctness × speed`.

`correctness` is the fraction of the four fields that match the reference — a
gate, so a change that breaks a value can't win on speed. `speed` maps elapsed
time into `(0, 1]` via `REFERENCE_MS / (REFERENCE_MS + elapsed_ms)`, so faster
code scores higher and the score only *approaches* 1.0 as the code approaches
instant. That means the goal target (1.0) is never quite reached and the climb
spends its whole budget.

On a typical machine the slow baseline lands around **0.45** and a fully
optimized version reaches **~0.99**.

## Run it

```bash
# Score the baseline once, by hand:
uv run python eval.py

# Drive the full multi-cycle climb (uses the Claude Code harness per cycle):
uv run python -c "import asyncio, hillclimber; asyncio.run(hillclimber.run('test_projects/slow_stats'))"
```

`hillclimber.toml` sets `budget.cycles = 3` — enough to remove all three
bottlenecks. hillclimber `git init`s this directory into its own repo on first
run and forks a worktree per cycle, so your working tree is never touched.

## Tuning

- Machine too fast/slow? Adjust `REFERENCE_MS` in `eval.py` so the baseline lands
  mid-range (around 0.45), or change `SIZE` / `MAX_VALUE` to make the workload
  heavier or lighter.
- Want a longer climb? Raise `budget.cycles` in `hillclimber.toml`.

## Contract for the optimizer

Keep `analyze(numbers: list[int]) -> Report` returning a `Report` with the same
four fields and the same values. Only change *how* they're computed.
