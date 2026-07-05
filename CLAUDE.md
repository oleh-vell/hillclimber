# CLAUDE.md

Guidance for working in this repo. **Ruff** (lint + format) and **ty** (type checking) are
mandatory — every change must pass both before it is considered done.

## Tooling

This project uses [`uv`](https://docs.astral.sh/uv/). Run all tools through `uv run` so they
use the project's pinned versions.

| Task | Command |
| --- | --- |
| Lint | `uv run ruff check .` |
| Lint + auto-fix | `uv run ruff check --fix .` |
| Format | `uv run ruff format .` |
| Format check (CI) | `uv run ruff format --check .` |
| Type check | `uv run ty check` |
| Tests | `uv run pytest` |

Config for both tools lives in `pyproject.toml` (`[tool.ruff]`, `[tool.ty]`). Do not introduce
black, isort, flake8, mypy, or pyright — ruff and ty replace all of them.

## Definition of done

Before finishing any code change, run and pass **all** of:

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

Rules:

- **Never** silence a ruff rule with a blanket `# noqa`. If a specific line genuinely needs an
  exception, use a targeted `# noqa: <CODE>` with a one-line reason, and prefer fixing the code.
- **Never** suppress a ty error with `# type: ignore` to make it pass. Fix the types. If a third
  party is genuinely untyped, narrow the suppression to the exact diagnostic and explain why.
- Do not loosen `[tool.ruff.lint]` `select` or `[tool.ty.rules]` to make existing code pass.


## Concurrency

This project is **async-first**. The runner shells out to score artefacts and fans out
runs, so I/O must never block the event loop.

- **Always use `asyncio`.** New code that does I/O (subprocess, network, file, sleeping,
  waiting on agents) must be written as `async def` and `await`ed.
- The core entry points — `hillclimber.run` and `get_baseline_score` — are coroutines, and
  `Strategy.execute` (and every strategy subclass) is `async def`.
- Shell out with `asyncio.create_subprocess_shell` / `asyncio.create_subprocess_exec`, **not**
  `subprocess.run`. Never call a blocking API directly inside a coroutine; offload unavoidable
  blocking work with `asyncio.to_thread`.
- Run concurrent work with `asyncio.gather` / `asyncio.TaskGroup` rather than serial `await`s
  when the tasks are independent.
- Synchronous entry points (e.g. CLI commands, scripts) drive the async core with `asyncio.run(...)`.
- Tests call coroutines via `asyncio.run(...)` (no `pytest-asyncio` dependency).

## Layout

- `src/hillclimber/` — library package (importable as `hillclimber`).
- `src/hillclimber/strategies/`, `src/hillclimber/harnesses/`, `src/hillclimber/sandboxes/` — pluggable strategy/harness/sandbox implementations.
- `tests/` — pytest suite (`pythonpath = ["src"]`, so import packages directly).
