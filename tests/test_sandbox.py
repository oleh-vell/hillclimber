"""Sandbox + strategy-base tests, consolidated.

Sections, from purest to most integrated:

* ``Strategy`` helpers — workspace creation, experiment ids, run locks.
* ``Sandbox.wrap`` — argv rewriting only (no enforcement).
* ``get_sandbox`` — config -> concrete backend, plus platform gating.
* Seatbelt profile renderer — pure string shape of ``_render_profile``.
* Seatbelt enforcement — end-to-end ``sandbox-exec`` tests (macOS only).
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Import the package first so it fully initialises; importing src modules
# (strategies.base, sandboxes, harnesses) directly otherwise hits a circular
# import via hillclimber.run -> strategies.chain.
import hillclimber  # noqa: F401
from harnesses._proc import exec_agent
from hillclimber.models import (
    DEFAULT_DENY_READ,
    Cycle,
    CycleStatus,
    PassthroughSandboxConfig,
    Score,
    SeatbeltSandboxConfig,
)
from sandboxes import PassthroughSandbox, SeatbeltSandbox, get_sandbox
from sandboxes.seatbelt import _render_profile
from strategies.base import Strategy

# --------------------------------------------------------------------------- #
# Strategy.create_workspace
# --------------------------------------------------------------------------- #


def test_create_workspace_makes_dir_and_returns_name(tmp_path: Path):
    name = asyncio.run(Strategy.create_workspace(str(tmp_path), "ws1"))

    assert name == "ws1"
    workspace = tmp_path / ".hillclimber" / "ws1"
    assert workspace.is_dir()


def test_create_workspace_is_idempotent(tmp_path: Path):
    asyncio.run(Strategy.create_workspace(str(tmp_path), "ws1"))
    # A second call with the same name does not raise and returns the name.
    name = asyncio.run(Strategy.create_workspace(str(tmp_path), "ws1"))

    assert name == "ws1"
    assert (tmp_path / ".hillclimber" / "ws1").is_dir()


def test_create_workspace_supports_multiple_workspaces(tmp_path: Path):
    asyncio.run(Strategy.create_workspace(str(tmp_path), "ws1"))
    asyncio.run(Strategy.create_workspace(str(tmp_path), "ws2"))

    assert (tmp_path / ".hillclimber" / "ws1").is_dir()
    assert (tmp_path / ".hillclimber" / "ws2").is_dir()


def test_create_workspace_raises_for_missing_path(tmp_path: Path):
    missing = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        asyncio.run(Strategy.create_workspace(str(missing), "ws1"))


def test_create_workspace_rejects_invalid_name(tmp_path: Path):
    with pytest.raises(ValueError):
        asyncio.run(Strategy.create_workspace(str(tmp_path), "nested/ws"))
    with pytest.raises(ValueError):
        asyncio.run(Strategy.create_workspace(str(tmp_path), ""))


# --------------------------------------------------------------------------- #
# Strategy.new_experiment_id
# --------------------------------------------------------------------------- #


def test_new_experiment_id_is_unique():
    ids = [Strategy.new_experiment_id() for _ in range(100)]
    assert len(set(ids)) == 100


def test_new_experiment_id_is_prefixed_and_shaped():
    exp_id = Strategy.new_experiment_id()
    assert exp_id.startswith("exp_")
    suffix = exp_id.removeprefix("exp_")
    assert len(suffix) == 8
    assert all(c in "0123456789abcdef" for c in suffix)


# --------------------------------------------------------------------------- #
# Strategy.write_lock
# --------------------------------------------------------------------------- #


def _cycle() -> Cycle:
    return Cycle(
        experiment_id="exp_a1b2c3d4",
        index=1,
        parent_ref="baseline",
        branch="hc/a1b2_cycle_001",
        worktree="hc_a1b2_cycle_001",
        hypothesis="try X",
        score_before=Score(value=0.5, passed=True, scorer_id="command"),
        status=CycleStatus.running,
    )


def test_write_lock_round_trips(tmp_path: Path):
    cycle = _cycle()

    lock = asyncio.run(Strategy.write_lock(str(tmp_path), cycle))

    assert Path(lock) == tmp_path / "cyc_001.lock"
    assert Cycle.model_validate_json(Path(lock).read_text()) == cycle


# --------------------------------------------------------------------------- #
# Sandbox.wrap — argv rewriting (no enforcement, just shape)
# --------------------------------------------------------------------------- #


def test_passthrough_returns_argv_unchanged():
    argv = ["claude", "--print", "--", "hello"]
    assert PassthroughSandbox().wrap(argv, "/some/workdir") == argv


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt construction requires macOS")
def test_seatbelt_prefixes_sandbox_exec(tmp_path):
    sandbox = SeatbeltSandbox(deny_read=["~/.ssh"], network=True)
    argv = ["claude", "--print"]

    wrapped = sandbox.wrap(argv, str(tmp_path))

    assert wrapped[0] == "sandbox-exec"
    assert wrapped[1] == "-p"
    assert "(version 1)" in wrapped[2]  # the inline profile
    assert wrapped[3:] == argv  # the original argv, untouched, after the profile


# --------------------------------------------------------------------------- #
# get_sandbox — config -> concrete backend, plus platform gating
# --------------------------------------------------------------------------- #


def test_get_sandbox_builds_passthrough():
    assert isinstance(get_sandbox(PassthroughSandboxConfig()), PassthroughSandbox)


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt requires macOS")
def test_get_sandbox_builds_seatbelt():
    sandbox = get_sandbox(SeatbeltSandboxConfig())
    assert isinstance(sandbox, SeatbeltSandbox)


def test_seatbelt_construction_rejects_non_darwin(monkeypatch):
    # A sandbox that silently no-ops is worse than none, so selecting Seatbelt off
    # macOS hard-errors and points at the ``none`` backend.
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="macOS"):
        SeatbeltSandbox(deny_read=[], network=True)


# --------------------------------------------------------------------------- #
# Seatbelt profile renderer — pure string shape of ``_render_profile``
#
# ``_render_profile`` is pure string work (no shelling out), so these exercise
# the profile *shape* directly — the ordering invariant that makes
# last-match-wins rescue the worktree, the deny/allow blocks, network gating,
# the empty-denylist guard, and symlink realpath'ing — without invoking
# ``sandbox-exec``. The real enforcement is covered below (macOS only).
# --------------------------------------------------------------------------- #


def _resolve(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def test_worktree_is_write_allowed():
    profile = _render_profile("/x/wt", list(DEFAULT_DENY_READ), network=True)
    work = _resolve("/x/wt")
    write_block = profile[profile.index("(allow file-write*") : profile.index("(deny file-read*")]
    assert f'(subpath "{work}")' in write_block


def test_worktree_read_allow_comes_after_read_deny():
    # Last-match-wins: the worktree usually sits under a denied root, so its read
    # re-allow MUST follow the deny block to rescue it.
    profile = _render_profile("/x/wt", list(DEFAULT_DENY_READ), network=True)
    read_deny = profile.index("(deny file-read*")
    read_allow = profile.index("(allow file-read*")
    assert read_deny < read_allow
    # ...and the final read-allow is the worktree.
    assert profile.rstrip().endswith(f'(subpath "{_resolve("/x/wt")}"))')


def test_each_default_deny_root_appears_in_read_deny():
    profile = _render_profile("/x/wt", list(DEFAULT_DENY_READ), network=True)
    read_deny = profile[profile.index("(deny file-read*") : profile.index("(allow file-read*")]
    for root in DEFAULT_DENY_READ:
        assert f'(subpath "{_resolve(root)}")' in read_deny


def test_network_false_adds_deny_network():
    assert "(deny network*)" in _render_profile("/x/wt", list(DEFAULT_DENY_READ), network=False)


def test_network_true_omits_deny_network():
    assert "(deny network*)" not in _render_profile("/x/wt", list(DEFAULT_DENY_READ), network=True)


def test_empty_deny_read_emits_no_read_rules():
    # An empty denylist must NOT degrade into a bare ``(deny file-read*)`` (which
    # would deny-read everything and starve the CLI). With nothing to deny, the
    # profile emits no read rules at all — ``(allow default)`` covers reads.
    profile = _render_profile("/x/wt", [], network=True)
    assert "file-read" not in profile


def test_symlinked_paths_are_realpathed(tmp_path):
    # Seatbelt matches on the realpath, so a symlinked input must resolve to its
    # target (this is why ``/tmp`` -> ``/private/tmp`` on macOS must be resolved).
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    profile = _render_profile(str(link), [str(link)], network=True)

    resolved = os.path.realpath(real)
    assert f'(subpath "{resolved}")' in profile
    # The unresolved symlink path must not leak into the profile.
    assert f'(subpath "{link}")' not in profile


@pytest.mark.skipif(sys.platform != "darwin", reason="the /tmp -> /private/tmp symlink is macOS-specific")
def test_tmp_is_realpathed_on_macos():
    profile = _render_profile("/tmp/wt", ["/tmp/secret"], network=True)
    assert "/private/tmp/wt" in profile
    assert "/private/tmp/secret" in profile


# --------------------------------------------------------------------------- #
# Seatbelt enforcement — end-to-end ``sandbox-exec`` tests (macOS only)
#
# These actually invoke ``sandbox-exec`` (via the ``exec_agent`` chokepoint)
# against real ``sh``/``git`` commands and assert the boundary holds: writes
# outside the worktree and reads of denied roots fail, while reads/writes inside
# the worktree succeed. This is the automated counterpart to the manual
# verification that informed the profile shape.
#
# Everything runs under a dedicated temp tree on ``/private/tmp`` rather than
# pytest's ``tmp_path`` — the latter lives under ``/private/var/folders``, which
# the profile write-allows for the CLI, so it could not distinguish "inside
# worktree" from "denied outside".
# --------------------------------------------------------------------------- #


@pytest.fixture
def sandbox_root() -> Iterator[str]:
    # Realpath'd /private/tmp tree: NOT under /private/var/folders, so writes here
    # are denied unless the profile explicitly re-allows the worktree subpath.
    root = os.path.realpath(tempfile.mkdtemp(dir="/tmp"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _sh(sandbox, workdir: str, script: str) -> tuple[bytes, bytes, int]:
    return asyncio.run(exec_agent(["/bin/sh", "-c", script], workdir, sandbox))


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt (sandbox-exec) is macOS-only")
class TestSeatbeltEnforcement:
    @staticmethod
    def _seatbelt(deny_read: list[str]) -> SeatbeltSandbox:
        return SeatbeltSandbox(deny_read=deny_read, network=True)

    def test_write_inside_worktree_succeeds(self, sandbox_root):
        work = Path(sandbox_root) / "wt"
        work.mkdir()
        sandbox = self._seatbelt(deny_read=[sandbox_root])

        _, _, rc = _sh(sandbox, str(work), "echo hi > inside.txt")

        assert rc == 0
        assert (work / "inside.txt").read_text() == "hi\n"

    def test_write_outside_worktree_is_denied(self, sandbox_root):
        work = Path(sandbox_root) / "wt"
        work.mkdir()
        outside = Path(sandbox_root) / "elsewhere"
        outside.mkdir()
        sandbox = self._seatbelt(deny_read=[sandbox_root])

        _, _, rc = _sh(sandbox, str(work), f'echo hi > "{outside}/out.txt"')

        assert rc != 0
        assert not (outside / "out.txt").exists()

    def test_read_inside_worktree_succeeds(self, sandbox_root):
        work = Path(sandbox_root) / "wt"
        work.mkdir()
        (work / "readable.txt").write_text("PUBLIC")
        sandbox = self._seatbelt(deny_read=[sandbox_root])

        out, _, rc = _sh(sandbox, str(work), "cat readable.txt")

        assert rc == 0
        assert out == b"PUBLIC"

    def test_read_of_denied_root_is_denied(self, sandbox_root):
        work = Path(sandbox_root) / "wt"
        work.mkdir()
        secrets = Path(sandbox_root) / "secrets"
        secrets.mkdir()
        (secrets / "key.txt").write_text("TOPSECRET")
        sandbox = self._seatbelt(deny_read=[str(secrets)])

        out, _, rc = _sh(sandbox, str(work), f'cat "{secrets}/key.txt"')

        assert rc != 0
        assert b"TOPSECRET" not in out

    def test_git_metadata_under_denied_parent_is_blocked(self, sandbox_root):
        """KNOWN LIMITATION (surfaced in plan review): a git worktree whose repo is a
        denied root cannot read its own git metadata under the sandbox.

        The real climb runs agents in a worktree under the artefact repo (e.g.
        ``~/projects/<repo>/hc_...``) while ``~/projects`` is denied. The worktree
        subpath is re-allowed, but the repo's ``.git`` (the worktree's commondir)
        stays denied — so ``git status`` and friends fail with EPERM. If agents need
        git inside the sandbox, the profile must additionally re-allow the resolved
        git metadata; that is out of scope for this change. This test pins the
        current behaviour so the limitation can't regress silently.
        """
        if shutil.which("git") is None:
            pytest.skip("git not available")

        repo = Path(sandbox_root) / "proj"
        repo.mkdir()
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@e",
        }
        subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q"], cwd=repo, check=True, env=env)
        (repo / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True, env=env)
        worktree = repo / "wt"
        subprocess.run(["git", "worktree", "add", "-q", str(worktree)], cwd=repo, check=True, env=env)

        # Deny the whole repo: the worktree subpath is re-allowed, but repo/.git is not.
        sandbox = self._seatbelt(deny_read=[str(repo)])
        _, _, rc = _sh(sandbox, str(worktree), "git status")

        assert rc != 0
