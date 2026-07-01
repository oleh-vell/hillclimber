import asyncio
import subprocess
from pathlib import Path

import pytest

# Import the package first so it fully initialises; importing a submodule
# directly otherwise hits a circular import via hillclimber.run -> strategies.chain.
import hillclimber  # noqa: F401
from hillclimber import git_utils


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").is_dir()


def _git(*args: str, cwd: Path) -> None:
    # Test scaffolding only: set identity inline so it works without global config.
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _repo_with_one_commit(path: Path) -> None:
    _git("init", cwd=path)
    (path / "a.txt").write_text("x\n")
    _git("add", ".", cwd=path)
    _git("commit", "-m", "init", cwd=path)


# --------------------------------------------------------------------------- #
# check_or_init_git
# --------------------------------------------------------------------------- #


def test_inits_git_in_empty_dir(tmp_path: Path):
    assert not _is_git_repo(tmp_path)

    initialised = asyncio.run(git_utils.check_or_init_git(str(tmp_path)))

    assert initialised is True
    assert _is_git_repo(tmp_path)


def test_noop_when_already_a_repo(tmp_path: Path):
    # First call initialises the repo.
    assert asyncio.run(git_utils.check_or_init_git(str(tmp_path))) is True

    # Second call sees the existing .git and does nothing.
    assert asyncio.run(git_utils.check_or_init_git(str(tmp_path))) is False
    assert _is_git_repo(tmp_path)


def test_init_seeds_a_commit_so_head_is_a_valid_worktree_base(tmp_path: Path):
    # Regression: a freshly init'd repo has an unborn HEAD, so creating a worktree
    # off HEAD failed with "invalid reference: HEAD". check_or_init_git now seeds a
    # baseline commit, so the worktree the chain forks off HEAD must succeed.
    (tmp_path / "artefact.py").write_text("print('hi')\n")

    assert asyncio.run(git_utils.check_or_init_git(str(tmp_path))) is True

    worktree = asyncio.run(git_utils.create_worktree(str(tmp_path), "hc_run_1", "hc/run_1", "HEAD"))
    assert Path(worktree).is_dir()
    # The baseline commit captured the artefact, so it is present in the checkout.
    assert (Path(worktree) / "artefact.py").is_file()


def test_init_seeds_commit_even_for_empty_dir(tmp_path: Path):
    # --allow-empty: even an artefact-less dir gets a real HEAD to fork from.
    assert asyncio.run(git_utils.check_or_init_git(str(tmp_path))) is True

    worktree = asyncio.run(git_utils.create_worktree(str(tmp_path), "hc_run_1", "hc/run_1", "HEAD"))
    assert Path(worktree).is_dir()


def test_raises_for_missing_path(tmp_path: Path):
    missing = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        asyncio.run(git_utils.check_or_init_git(str(missing)))


def test_inits_in_parent_dir_when_path_is_a_file(tmp_path: Path):
    # A single-file artefact: git should be initialised in its containing dir.
    file_path = tmp_path / "artefact.py"
    file_path.write_text("print('hi')\n")
    assert not _is_git_repo(tmp_path)

    initialised = asyncio.run(git_utils.check_or_init_git(str(file_path)))

    assert initialised is True
    assert _is_git_repo(tmp_path)


def test_noop_for_file_in_existing_repo(tmp_path: Path):
    file_path = tmp_path / "artefact.py"
    file_path.write_text("print('hi')\n")
    # Initialise once via the directory.
    assert asyncio.run(git_utils.check_or_init_git(str(tmp_path))) is True

    # Passing the file now sees the repo on its parent and does nothing.
    assert asyncio.run(git_utils.check_or_init_git(str(file_path))) is False


# --------------------------------------------------------------------------- #
# create_worktree
# --------------------------------------------------------------------------- #


def test_create_worktree_branches_off_base_ref(tmp_path: Path):
    _repo_with_one_commit(tmp_path)

    worktree = asyncio.run(git_utils.create_worktree(str(tmp_path), "hc_run_1", "hc/run_1", "HEAD"))

    # The worktree lives under the repo's .hillclimber dir, checked out off HEAD.
    assert Path(worktree) == tmp_path / ".hillclimber" / "hc_run_1"
    assert (tmp_path / ".hillclimber" / "hc_run_1" / "a.txt").is_file()
    # ...on the new branch.
    out = subprocess.run(["git", "branch", "--list", "hc/run_1"], cwd=tmp_path, capture_output=True, text=True)
    assert "hc/run_1" in out.stdout


def test_create_worktree_raises_when_branch_exists(tmp_path: Path):
    _repo_with_one_commit(tmp_path)
    asyncio.run(git_utils.create_worktree(str(tmp_path), "hc_run_1", "hc/run_1", "HEAD"))

    # Re-using the branch name must fail rather than silently clobber.
    with pytest.raises(RuntimeError):
        asyncio.run(git_utils.create_worktree(str(tmp_path), "hc_run_2", "hc/run_1", "HEAD"))


def test_create_worktree_accepts_a_file_path(tmp_path: Path):
    # A single-file artefact: the worktree is rooted at the file's repo.
    _repo_with_one_commit(tmp_path)
    artefact = tmp_path / "a.txt"

    worktree = asyncio.run(git_utils.create_worktree(str(artefact), "hc_run_1", "hc/run_1", "HEAD"))

    assert Path(worktree) == tmp_path / ".hillclimber" / "hc_run_1"


# --------------------------------------------------------------------------- #
# create_detached_worktree / remove_worktree
# --------------------------------------------------------------------------- #


def test_detached_worktree_checks_out_the_named_ref(tmp_path: Path):
    # main holds "v1"; a second branch (the current HEAD) holds "v2".
    _git("init", "-b", "main", cwd=tmp_path)
    (tmp_path / "a.txt").write_text("v1\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-m", "v1", cwd=tmp_path)
    _git("checkout", "-b", "wip", cwd=tmp_path)
    (tmp_path / "a.txt").write_text("v2\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-m", "v2", cwd=tmp_path)

    worktree = asyncio.run(git_utils.create_detached_worktree(str(tmp_path), "hc_baseline", "main"))

    # Checked out at main's content (v1), not the current HEAD (wip/v2)...
    assert Path(worktree) == tmp_path / ".hillclimber" / "hc_baseline"
    assert (Path(worktree) / "a.txt").read_text() == "v1\n"
    # ...and on no branch (detached) — it mints none.
    out = subprocess.run(["git", "branch", "--list"], cwd=tmp_path, capture_output=True, text=True)
    assert set(out.stdout.split()) <= {"*", "main", "wip"}


def test_detached_worktree_raises_for_unknown_ref(tmp_path: Path):
    _repo_with_one_commit(tmp_path)
    with pytest.raises(RuntimeError):
        asyncio.run(git_utils.create_detached_worktree(str(tmp_path), "hc_baseline", "no-such-branch"))


def test_remove_worktree_tears_down_the_checkout(tmp_path: Path):
    _repo_with_one_commit(tmp_path)
    worktree = asyncio.run(git_utils.create_detached_worktree(str(tmp_path), "hc_baseline", "HEAD"))
    assert Path(worktree).is_dir()

    asyncio.run(git_utils.remove_worktree(str(tmp_path), "hc_baseline"))

    assert not Path(worktree).exists()


# --------------------------------------------------------------------------- #
# head_sha / is_dirty / commit_all
# --------------------------------------------------------------------------- #


def _rev_parse(path: Path) -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_head_sha_matches_rev_parse(tmp_path: Path):
    _repo_with_one_commit(tmp_path)
    assert asyncio.run(git_utils.head_sha(str(tmp_path))) == _rev_parse(tmp_path)


def test_is_dirty_reflects_uncommitted_changes(tmp_path: Path):
    _repo_with_one_commit(tmp_path)
    assert asyncio.run(git_utils.is_dirty(str(tmp_path))) is False

    (tmp_path / "a.txt").write_text("changed\n")
    assert asyncio.run(git_utils.is_dirty(str(tmp_path))) is True


def test_commit_all_commits_everything_and_returns_new_sha(tmp_path: Path):
    _repo_with_one_commit(tmp_path)
    before = _rev_parse(tmp_path)
    (tmp_path / "b.txt").write_text("new\n")  # untracked too

    sha = asyncio.run(git_utils.commit_all(str(tmp_path), "msg"))

    assert sha != before
    assert sha == _rev_parse(tmp_path)
    assert asyncio.run(git_utils.is_dirty(str(tmp_path))) is False


# --------------------------------------------------------------------------- #
# check_uncommitted_changes
# --------------------------------------------------------------------------- #


def test_check_uncommitted_changes_false_for_non_repo(tmp_path: Path):
    # Not a git repo yet: treated as clean (check_or_init_git will commit it).
    assert asyncio.run(git_utils.check_uncommitted_changes(str(tmp_path))) is False


def test_check_uncommitted_changes_false_when_clean(tmp_path: Path):
    _repo_with_one_commit(tmp_path)
    assert asyncio.run(git_utils.check_uncommitted_changes(str(tmp_path))) is False


def test_check_uncommitted_changes_true_when_dirty(tmp_path: Path):
    _repo_with_one_commit(tmp_path)
    (tmp_path / "a.txt").write_text("edited\n")
    assert asyncio.run(git_utils.check_uncommitted_changes(str(tmp_path))) is True


def test_check_uncommitted_changes_ignores_hillclimber_workdir(tmp_path: Path):
    _repo_with_one_commit(tmp_path)
    # Leftover worktrees/locks from a previous run must not count as changes.
    workdir = tmp_path / ".hillclimber"
    workdir.mkdir()
    (workdir / "junk.lock").write_text("noise\n")

    assert asyncio.run(git_utils.check_uncommitted_changes(str(tmp_path))) is False
