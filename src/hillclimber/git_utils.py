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
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
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


async def _git_capture(repo: Path, *args: str, env: Mapping[str, str] | None = None) -> tuple[int, str, str]:
    """Run ``git *args`` in ``repo``; return ``(returncode, stdout, stderr)``.

    A thin wrapper over ``asyncio.create_subprocess_exec`` (never
    ``subprocess.run``; see CLAUDE.md "Concurrency") so git invocations don't
    block the event loop. Used where the command's stdout matters (``rev-parse``,
    ``status``); :func:`_git` wraps it for the common stderr-only case. ``env``
    overrides the child's environment (e.g. ``GIT_INDEX_FILE`` for the
    non-destructive snapshot; see :func:`create_snapshot_commit`).
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=repo,
        env=dict(env) if env is not None else None,
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


def repo_root(path: str) -> Path:
    """Resolve the git repo root for ``path``.

    ``path`` may point at either a directory (the repo) or a file (e.g. a
    single-file artefact), in which case its containing directory is the repo.
    Public because path-derived state (e.g. ``lockfile.lock_path``) needs the
    same file-or-directory resolution the git helpers use.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
    """
    target = Path(path)
    if target.is_dir():
        return target
    if target.is_file():
        return target.parent
    raise FileNotFoundError(f"no such file or directory: {path}")


async def _work_tree_toplevel(repo: Path) -> Path | None:
    """The root of the git work tree containing ``repo``, or ``None`` if outside any.

    ``git rev-parse --show-toplevel`` rather than testing ``repo/.git`` directly:
    the ``.git`` check cannot tell "not a repo" from "a subdirectory of one",
    and that difference matters — a nested artefact must not be silently
    ``git init``-ed inside its containing repository.
    """
    rc, stdout, _stderr = await _git_capture(repo, "rev-parse", "--show-toplevel")
    if rc != 0:
        return None
    return Path(stdout)


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
        RuntimeError: If the artefact sits *inside* another git repository (a
            monorepo subdirectory — worktrees and branches would land in the
            containing repo, and a nested ``git init`` would corrupt the setup),
            or if ``git init``, staging, or the baseline commit fails.
    """
    repo = repo_root(path)

    toplevel = await _work_tree_toplevel(repo)
    if toplevel is not None:
        if toplevel.resolve() != repo.resolve():
            raise RuntimeError(
                f"artefact {repo} sits inside the git repository at {toplevel}; "
                "hillclimber needs the artefact to be its own repository root — "
                "point path_to_artefact at that root, or move the artefact to a "
                "standalone directory"
            )
        return False

    rc, stderr = await _git(repo, "init")
    if rc != 0:
        raise RuntimeError(f"git init failed in {repo}: {stderr}")

    # Seed a baseline commit so HEAD is a valid worktree base. Stage everything
    # except ``.hillclimber`` (a leftover lock/worktree dir from a prior climb is
    # runner state, not artefact), then commit with ``--allow-empty`` so even an
    # empty artefact directory still yields a real HEAD.
    rc, stderr = await _git(repo, "add", "-A", "--", ".", ":(exclude).hillclimber")
    if rc != 0:
        raise RuntimeError(f"git add failed in {repo}: {stderr}")
    rc, stderr = await _git(repo, *_GIT_IDENTITY, "commit", "--allow-empty", "-m", "hillclimber: baseline")
    if rc != 0:
        raise RuntimeError(f"git commit failed in {repo}: {stderr}")

    logger.info("initialised git repo (with baseline commit) in %s", repo)
    return True


async def create_worktree(path: str, name: str, branch: str, base_ref: str) -> str:
    """Create an isolated git worktree for a cycle.

    Each cycle gets its own checkout (see ``Cycle.worktree``) so hypotheses are
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
    repo = repo_root(path)
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


async def create_detached_worktree(path: str, name: str, ref: str) -> str:
    """Check out ``ref`` in a throwaway worktree, detached (no new branch).

    Unlike :func:`create_worktree`, this mints no branch — it is for reading a
    committed state (e.g. scoring the baseline at the start ref) rather than
    building on it. Pair it with :func:`remove_worktree_if_present` to clean up
    once scored.

    Args:
        path: A directory or file inside the artefact repo; its root is used.
        name: The worktree directory name (under ``.hillclimber``).
        ref: The git ref to check out (e.g. ``main``, a branch, or a sha).

    Returns:
        The absolute path to the created worktree (``<repo>/.hillclimber/<name>``).

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        RuntimeError: If ``git worktree add`` fails (e.g. ``ref`` does not exist).
    """
    repo = repo_root(path)
    worktree = repo / ".hillclimber" / name
    logger.debug("creating detached worktree %s at %s", worktree, ref)
    rc, stderr = await _git(repo, "worktree", "add", "--detach", str(worktree), ref)
    if rc != 0:
        raise RuntimeError(f"git worktree add failed in {repo}: {stderr}")
    return str(worktree)


async def prune_worktrees(path: str) -> None:
    """Drop git's records of worktrees whose directories no longer exist.

    The companion to deleting worktree checkouts wholesale (rmtree, not
    ``git worktree remove`` — see ``lockfile.reset_history``): git keeps its
    per-worktree metadata until pruned, and a stale entry would block adding
    a new worktree at the same path later.

    Args:
        path: A directory or file inside the artefact repo; its root is used.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        RuntimeError: If ``git worktree prune`` fails.
    """
    repo = repo_root(path)
    rc, stderr = await _git(repo, "worktree", "prune")
    if rc != 0:
        raise RuntimeError(f"git worktree prune failed in {repo}: {stderr}")


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


async def commit_all(worktree: str, message: str, exclude: Sequence[str] = ()) -> str:
    """Stage everything in ``worktree`` and commit it; return the new commit sha.

    Used both to snapshot a dirty baseline and as the backstop when a worker
    applies a change but forgets to commit it (see ``Chain._commit_cycle``). The
    hillclimber identity is supplied inline so it works without global git config.

    ``exclude`` patterns are kept out of the commit (git pathspec globs, e.g.
    ``cyc_*.lock`` — runner state that must not enter the artefact's history).
    When exclusion leaves nothing staged (only excluded files changed), no
    commit is made and the current ``HEAD`` is returned instead — callers see
    an unchanged sha rather than a "nothing to commit" failure.

    Args:
        worktree: The checkout to commit.
        message: The commit message.
        exclude: Pathspec glob patterns to keep out of the commit.

    Returns:
        The sha of the commit just created, or the current ``HEAD`` when
        nothing remained to commit after exclusions.

    Raises:
        RuntimeError: If staging or committing fails.
    """
    repo = Path(worktree)
    pathspec = [".", *(f":(exclude){pattern}" for pattern in exclude)]
    rc, stderr = await _git(repo, "add", "-A", "--", *pathspec)
    if rc != 0:
        raise RuntimeError(f"git add failed in {worktree}: {stderr}")
    # ``diff --cached --quiet`` exits 0 when nothing is staged, 1 when something
    # is; anything else is a real error.
    rc, _stdout, stderr = await _git_capture(repo, "diff", "--cached", "--quiet")
    if rc == 0:
        return await head_sha(worktree)
    if rc != 1:
        raise RuntimeError(f"git diff --cached failed in {worktree}: {stderr}")
    rc, stderr = await _git(repo, *_GIT_IDENTITY, "commit", "-m", message)
    if rc != 0:
        raise RuntimeError(f"git commit failed in {worktree}: {stderr}")
    return await head_sha(worktree)


def _make_snapshot_index() -> str:
    """Create and return the path to a throwaway git index file.

    A thin typed wrapper over ``tempfile.mkstemp`` (whose return widens to
    ``str | bytes`` when offloaded via ``asyncio.to_thread``) so the caller gets
    a plain ``str`` path. The fd is closed immediately; git reopens the path.
    """
    fd, path = tempfile.mkstemp(prefix="hc_snapshot_index_")
    os.close(fd)
    return path


async def create_snapshot_commit(path: str, message: str) -> str:
    """Snapshot the artefact's dirty working tree into a commit to climb from.

    The opt-in alternative to refusing on a dirty tree (see ``Config.auto_commit``
    / ``run``): instead of forcing the user to commit by hand, capture their
    current uncommitted state — tracked *and* untracked changes — as a single
    commit, and return its sha so the baseline and every cycle fork from the same
    snapshot. That keeps the climb's core invariant (baseline and cycles measure
    the same code) intact while including the user's in-progress edits.

    Intended to be *non-destructive*: build the commit object without moving the
    user's branch or mutating their working tree (e.g. via a temporary index), so
    running the climb never leaves a surprise commit on their branch.

    Args:
        path: A directory or file inside the artefact repo; its root is used.
        message: The commit message for the snapshot.

    Returns:
        The sha of the snapshot commit the climb should fork from.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        RuntimeError: If any of the underlying git plumbing commands fail.
    """
    repo = repo_root(path)

    # A throwaway index so staging never touches the user's real index or
    # working tree. ``GIT_INDEX_FILE`` redirects every git command below at it;
    # seeded from HEAD so unchanged tracked files carry over, then the working
    # tree's changes (tracked and untracked) are staged on top.
    index_file = await asyncio.to_thread(_make_snapshot_index)
    env = {**os.environ, "GIT_INDEX_FILE": index_file}
    try:
        rc, _stdout, stderr = await _git_capture(repo, "read-tree", "HEAD", env=env)
        if rc != 0:
            raise RuntimeError(f"git read-tree failed in {repo}: {stderr}")
        rc, _stdout, stderr = await _git_capture(repo, "add", "-A", "--", ".", ":(exclude).hillclimber", env=env)
        if rc != 0:
            raise RuntimeError(f"git add failed in {repo}: {stderr}")
        rc, tree, stderr = await _git_capture(repo, "write-tree", env=env)
        if rc != 0:
            raise RuntimeError(f"git write-tree failed in {repo}: {stderr}")
        # ``commit-tree`` builds a commit object parented on HEAD without moving
        # any ref — the snapshot is reachable only by the sha it returns, so the
        # user's branch and HEAD stay exactly where they were.
        rc, sha, stderr = await _git_capture(
            repo, *_GIT_IDENTITY, "commit-tree", tree, "-p", "HEAD", "-m", message, env=env
        )
        if rc != 0:
            raise RuntimeError(f"git commit-tree failed in {repo}: {stderr}")
    finally:
        await asyncio.to_thread(lambda: os.path.exists(index_file) and os.remove(index_file))

    logger.info("snapshotted dirty artefact tree as %s (non-destructive)", sha)
    return sha


async def remove_worktree_if_present(path: str, name: str) -> None:
    """Best-effort teardown of the worktree ``name`` — never raises.

    Built for cleanup paths that must not mask a real error (a cycle's
    ``finally``, a stale ``hc_baseline`` from a killed run): it force-removes
    the checkout, deletes any leftover directory git's own remove could not,
    and prunes the now-dangling registration so the same path is immediately
    reusable. Failures are logged, not raised.

    Args:
        path: A directory or file inside the artefact repo; its root is used.
        name: The worktree directory name (under ``.hillclimber``) to remove.
    """
    repo = repo_root(path)
    worktree = repo / ".hillclimber" / name
    rc, stderr = await _git(repo, "worktree", "remove", "--force", str(worktree))
    if rc != 0:
        logger.debug("git worktree remove for %s returned %d: %s", worktree, rc, stderr)
    if await asyncio.to_thread(worktree.exists):
        await asyncio.to_thread(shutil.rmtree, worktree, ignore_errors=True)
    # Drop any registration left dangling by the rmtree so a later add can reuse
    # the path (see ``prune_worktrees``).
    await _git(repo, "worktree", "prune")


async def check_uncommitted_changes(path: str) -> bool:
    """Whether the artefact repo has uncommitted changes.

    The climb forks each cycle from a commit, so it must start from a clean tree:
    a dirty artefact would mean the baseline (scored on the working tree) and the
    cycles (forked from ``HEAD``) measure different code. ``hillclimber.run`` calls
    this up front and refuses to start when it returns ``True``.

    A directory outside any git work tree counts as clean — ``check_or_init_git``
    will initialise it and commit its current state. The climb's own
    ``.hillclimber`` working directory (leftover worktrees and lock files) is
    excluded, so a previous run never blocks the next.

    Args:
        path: A directory or file inside the artefact repo.

    Returns:
        ``True`` if the artefact is in a git work tree with uncommitted changes
        (outside ``.hillclimber``), else ``False``.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        RuntimeError: If ``git status`` fails.
    """
    repo = repo_root(path)
    if await _work_tree_toplevel(repo) is None:
        return False
    rc, stdout, stderr = await _git_capture(repo, "status", "--porcelain", "--", ".", ":(exclude).hillclimber")
    if rc != 0:
        raise RuntimeError(f"git status failed in {repo}: {stderr}")
    return bool(stdout)
