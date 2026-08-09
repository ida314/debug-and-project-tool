"""Throwaway git worktrees.

Hard rule: the user's working tree is never touched. Every session gets its own
worktree on its own branch, under the cache dir, and it is removed on teardown even
if the session crashed.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config


class WorktreeError(RuntimeError):
    pass


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if check and proc.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def is_git_repo(path: Path) -> bool:
    return _git(path, "rev-parse", "--git-dir", check=False).returncode == 0


def is_clean(path: Path) -> bool:
    return not _git(path, "status", "--porcelain").stdout.strip()


def current_branch(path: Path) -> str:
    return _git(path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


@dataclass
class Worktree:
    repo: Path
    path: Path
    branch: str

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return _git(self.path, *args, check=check)

    def diff(self, against: str = "HEAD") -> str:
        return self.git("diff", against).stdout

    def commit_all(self, message: str) -> None:
        self.git("add", "-A")
        self.git("-c", "user.name=dpt", "-c", "user.email=dpt@local",
                 "commit", "-q", "-m", message)


def create(repo: Path, session_id: str) -> Worktree:
    """Branch off whatever the repo is currently on and check it out elsewhere."""
    repo = repo.resolve()
    if not is_git_repo(repo):
        raise WorktreeError(f"{repo} is not a git repository")
    if not is_clean(repo):
        raise WorktreeError(
            f"{repo} has uncommitted changes; commit or stash before training on it"
        )

    branch = f"dpt/{session_id}"
    path = config.WORKTREE_DIR / f"{repo.name}-{session_id}"
    _git(repo, "worktree", "add", "-b", branch, str(path), "HEAD")
    return Worktree(repo=repo, path=path, branch=branch)


def teardown(wt: Worktree) -> None:
    """Best-effort and idempotent -- a failed teardown must never block the next
    session, but it also must not leave a branch lying around in the user's repo."""
    _git(wt.repo, "worktree", "remove", "--force", str(wt.path), check=False)
    _git(wt.repo, "worktree", "prune", check=False)
    _git(wt.repo, "branch", "-D", wt.branch, check=False)
