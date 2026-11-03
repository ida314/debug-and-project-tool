"""Worktree lifecycle.

The hard rule from the module docstring -- "the user's working tree is never touched"
-- is the one thing here that has to hold even when a session crashes, so teardown
gets more attention than creation.
"""

from __future__ import annotations

import pytest

from dpt import worktree
from tests.conftest import git


def test_create_checks_out_elsewhere_on_its_own_branch(tmp_repo):
    wt = worktree.create(tmp_repo, "s1")
    try:
        assert wt.path.exists()
        assert wt.path != tmp_repo
        assert wt.branch == "dpt/s1"
        assert (wt.path / "src" / "calc.py").exists()
        assert worktree.current_branch(wt.path) == "dpt/s1"
        assert worktree.current_branch(tmp_repo) != "dpt/s1"
    finally:
        worktree.teardown(wt)


def test_create_refuses_a_dirty_repo(tmp_repo):
    (tmp_repo / "src" / "calc.py").write_text("# scribbled on\n")
    with pytest.raises(worktree.WorktreeError, match="uncommitted changes"):
        worktree.create(tmp_repo, "s1")


def test_teardown_leaves_nothing_behind(tmp_repo):
    wt = worktree.create(tmp_repo, "s1")
    worktree.teardown(wt)

    assert not wt.path.exists()
    assert "dpt/s1" not in git(tmp_repo, "branch", "--list", "dpt/*")
    assert str(wt.path) not in git(tmp_repo, "worktree", "list")


def test_teardown_is_idempotent(tmp_repo):
    """A failed teardown must never block the next session."""
    wt = worktree.create(tmp_repo, "s1")
    worktree.teardown(wt)
    worktree.teardown(wt)  # must not raise


def test_edits_in_the_worktree_do_not_reach_the_real_repo(tmp_repo):
    wt = worktree.create(tmp_repo, "s1")
    try:
        (wt.path / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        assert "a - b" in wt.diff()
        assert worktree.is_clean(tmp_repo)
        assert "a - b" not in (tmp_repo / "src" / "calc.py").read_text()
    finally:
        worktree.teardown(wt)


def test_project_env_drops_our_own_virtualenv(monkeypatch):
    """Regression: `uv run dpt` exports VIRTUAL_ENV, and a project command that
    inherited it installed the trained project's dependencies into dpt's own venv."""
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/dpt/.venv")
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")

    env = worktree.project_env()
    assert "VIRTUAL_ENV" not in env
    assert "PYTHONPATH" not in env


def test_project_env_strips_our_bin_from_path(monkeypatch):
    import sys

    ours = f"{sys.prefix}/bin"
    monkeypatch.setenv("PATH", f"{ours}:/usr/bin")
    env = worktree.project_env()

    if sys.prefix != sys.base_prefix:  # only meaningful when we're in a venv
        assert ours not in env["PATH"].split(":")
    assert "/usr/bin" in env["PATH"].split(":")


def test_run_executes_in_the_worktree_with_a_clean_env(tmp_repo, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/dpt/.venv")
    wt = worktree.create(tmp_repo, "s1")
    try:
        assert wt.run("pwd", timeout=30).stdout.strip() == str(wt.path)
        assert wt.run("echo -n ${VIRTUAL_ENV:-unset}", timeout=30).stdout == "unset"
    finally:
        worktree.teardown(wt)


def test_commit_all_works_without_a_configured_git_identity(tmp_repo, monkeypatch):
    """dpt supplies its own identity, so a machine with no global user.name still works."""
    monkeypatch.setenv("HOME", str(tmp_repo.parent / "nohome"))
    wt = worktree.create(tmp_repo, "s1")
    try:
        (wt.path / "new.txt").write_text("hi\n")
        wt.commit_all("refactor: minor cleanup")
        assert "refactor: minor cleanup" in wt.git("log", "-1", "--format=%s").stdout
        assert wt.diff().strip() == ""
    finally:
        worktree.teardown(wt)
