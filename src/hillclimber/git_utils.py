"""Git helpers for the climb.

The strategy layer isolates each run in its own checkout and treats the
artefact directory as a git repository (initialising it if need be). Those
git-touching operations live here, as plain coroutines, so the strategy code
stays about orchestration and the shelling-out stays in one place.

All shelling-out goes through ``asyncio.create_subprocess_exec`` so it never
blocks the event loop (see CLAUDE.md "Concurrency").
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from hillclimber.telemetry import get_logger

logger = get_logger(__name__)


# Identity for the seeded baseline commit. Global git config (user.name/email,
# gpgsign) may be unset or hostile in CI, so it is supplied inline rather than
# relied upon.
_GIT_IDENTITY = (
    "-c",
    "user.name=hillclimber",
    "-c",
    "user.email=hillclimber@localhost",
    "-c",
    "commit.gpgsign=false",
)


async def _git_capture(repo: Path, *args: str) -> tuple[int, str, str]:
    """Run ``git *args`` in ``repo``; return ``(returncode, stdout, stderr)``.

    A thin wrapper over ``asyncio.create_subprocess_exec`` (never
    ``subprocess.run``; see CLAUDE.md "Concurrency") so git invocations don't
    block the event loop. Used where the command's stdout matters (``rev-parse``,
    ``status``); :func:`_git` wraps it for the common stderr-only case.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=repo,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, stdout.decode().strip(), stderr.decode().strip()


async def _git(repo: Path, *args: str) -> tuple[int, str]:
    """Run ``git *args`` in ``repo``; return ``(returncode, stderr)``."""
    rc, _stdout, stderr = await _git_capture(repo, *args)
    return rc, stderr


def _repo_root(path: str) -> Path:
    """Resolve the git repo root for ``path``.

    ``path`` may point at either a directory (the repo) or a file (e.g. a
    single-file artefact), in which case its containing directory is the repo.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
    """
    target = Path(path)
    if target.is_dir():
        return target
    if target.is_file():
        return target.parent
    raise FileNotFoundError(f"no such file or directory: {path}")


async def check_or_init_git(path: str) -> bool:
    """Ensure ``path``'s directory is a git repository with a baseline commit.

    ``path`` may point at either a directory or a file. If it is a file (e.g.
    a single-file artefact), the containing directory is used as the repo root.

    A freshly ``git init``'d repo has an *unborn* ``HEAD`` (no commits), which is
    not a valid worktree base — ``git worktree add ... HEAD`` would fail with
    ``invalid reference: HEAD``. So when this initialises the repo, it also seeds
    an initial commit capturing the artefact's current state, giving cycles a
    real ``HEAD`` to fork from (see ``Chain.execute`` ``parent_ref``). An existing
    repo is left untouched.

    Args:
        path: A directory, or a file whose containing directory is the repo.
            Must already exist.

    Returns:
        ``True`` if the repository was initialised (and a baseline commit seeded),
        ``False`` if it was already a git repository.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        RuntimeError: If ``git init``, staging, or the baseline commit fails.
    """
    repo = _repo_root(path)

    if (repo / ".git").exists():
        return False

    rc, stderr = await _git(repo, "init")
    if rc != 0:
        raise RuntimeError(f"git init failed in {repo}: {stderr}")

    # Seed a baseline commit so HEAD is a valid worktree base. Stage everything,
    # then commit with ``--allow-empty`` so even an empty artefact directory still
    # yields a real HEAD.
    rc, stderr = await _git(repo, "add", "-A")
    if rc != 0:
        raise RuntimeError(f"git add failed in {repo}: {stderr}")
    rc, stderr = await _git(repo, *_GIT_IDENTITY, "commit", "--allow-empty", "-m", "hillclimber: baseline")
    if rc != 0:
        raise RuntimeError(f"git commit failed in {repo}: {stderr}")

    logger.info("initialised git repo (with baseline commit) in %s", repo)
    return True


async def create_worktree(path: str, name: str, branch: str, base_ref: str) -> str:
    """Create an isolated git worktree for a run.

    Each run gets its own checkout (see ``Run.worktree``) so hypotheses are
    applied in isolation and never touch the artefact's working tree. The
    worktree lives under the repo's ``.hillclimber`` directory (alongside the
    workspaces, never clobbering the artefact), on a new ``branch`` forked from
    ``base_ref``.

    Args:
        path: A directory or file inside the artefact repo; its root is used.
        name: The worktree directory name (e.g. ``hc_run_<id>``).
        branch: The new branch to create for this run's checkout.
        base_ref: The git ref the worktree/branch starts from (e.g. ``HEAD``).

    Returns:
        The absolute path to the created worktree (``<repo>/.hillclimber/<name>``).

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        RuntimeError: If ``git worktree add`` fails.
    """
    repo = _repo_root(path)
    worktree = repo / ".hillclimber" / name
    logger.debug("creating worktree %s on branch %s off %s", worktree, branch, base_ref)
    proc = await asyncio.create_subprocess_exec(
        "git",
        "worktree",
        "add",
        "-b",
        branch,
        str(worktree),
        base_ref,
        cwd=repo,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git worktree add failed in {repo}: {stderr.decode().strip()}")
    return str(worktree)


async def head_sha(worktree: str) -> str:
    """Resolve ``worktree``'s current ``HEAD`` to a commit sha.

    Args:
        worktree: A directory inside the repo/worktree to resolve ``HEAD`` for.

    Returns:
        The 40-char commit sha ``HEAD`` points at.

    Raises:
        RuntimeError: If ``git rev-parse`` fails (e.g. an unborn ``HEAD``).
    """
    rc, stdout, stderr = await _git_capture(Path(worktree), "rev-parse", "HEAD")
    if rc != 0:
        raise RuntimeError(f"git rev-parse HEAD failed in {worktree}: {stderr}")
    return stdout


async def is_dirty(worktree: str) -> bool:
    """Whether ``worktree`` has uncommitted changes (staged, unstaged, or untracked).

    Args:
        worktree: The checkout to inspect.

    Returns:
        ``True`` if ``git status --porcelain`` reports anything, else ``False``.

    Raises:
        RuntimeError: If ``git status`` fails.
    """
    rc, stdout, stderr = await _git_capture(Path(worktree), "status", "--porcelain")
    if rc != 0:
        raise RuntimeError(f"git status failed in {worktree}: {stderr}")
    return bool(stdout)


async def commit_all(worktree: str, message: str) -> str:
    """Stage everything in ``worktree`` and commit it; return the new commit sha.

    Used both to snapshot a dirty baseline and as the backstop when a worker
    applies a change but forgets to commit it (see ``Chain._commit_cycle``). The
    hillclimber identity is supplied inline so it works without global git config.

    Args:
        worktree: The checkout to commit.
        message: The commit message.

    Returns:
        The sha of the commit just created.

    Raises:
        RuntimeError: If staging or committing fails.
    """
    repo = Path(worktree)
    rc, stderr = await _git(repo, "add", "-A")
    if rc != 0:
        raise RuntimeError(f"git add failed in {worktree}: {stderr}")
    rc, stderr = await _git(repo, *_GIT_IDENTITY, "commit", "-m", message)
    if rc != 0:
        raise RuntimeError(f"git commit failed in {worktree}: {stderr}")
    return await head_sha(worktree)


async def check_uncommitted_changes(path: str) -> bool:
    """Whether the artefact repo has uncommitted changes.

    The climb forks each cycle from a commit, so it must start from a clean tree:
    a dirty artefact would mean the baseline (scored on the working tree) and the
    cycles (forked from ``HEAD``) measure different code. ``hillclimber.run`` calls
    this up front and refuses to start when it returns ``True``.

    A directory that is not yet a git repo counts as clean — ``check_or_init_git``
    will initialise it and commit its current state. The climb's own
    ``.hillclimber`` working directory (leftover worktrees and lock files) is
    excluded, so a previous run never blocks the next.

    Args:
        path: A directory or file inside the artefact repo.

    Returns:
        ``True`` if the artefact is a git repo with uncommitted changes (outside
        ``.hillclimber``), else ``False``.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        RuntimeError: If ``git status`` fails.
    """
    repo = _repo_root(path)
    if not (repo / ".git").exists():
        return False
    rc, stdout, stderr = await _git_capture(repo, "status", "--porcelain", "--", ".", ":(exclude).hillclimber")
    if rc != 0:
        raise RuntimeError(f"git status failed in {repo}: {stderr}")
    return bool(stdout)
