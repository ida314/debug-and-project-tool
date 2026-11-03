"""Throwaway git worktrees.

Hard rule: the user's working tree is never touched. Every session gets its own
worktree on its own branch, under the cache dir, and it is removed on teardown even
if the session crashed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import config


class WorktreeError(RuntimeError):
    pass


def project_env() -> dict[str, str]:
    """The environment a project's own commands should run under.

    dpt itself runs from a virtualenv -- `uv run dpt` exports `VIRTUAL_ENV` and puts its
    own `bin/` first on PATH. Inherited, that makes the trained project's tooling target
    *dpt's* environment: `uv pip install` installs the project's dependencies into dpt's
    venv, and the worktree's own venv stays empty. The result is a test command that
    fails for environment reasons, which is indistinguishable from a successfully
    injected bug unless the clean-tree gate catches it.
    """
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    # Anything uv put in front to activate our interpreter has to come back out.
    if sys.prefix != sys.base_prefix:
        ours = str(Path(sys.prefix) / "bin")
        env["PATH"] = os.pathsep.join(
            p for p in env.get("PATH", "").split(os.pathsep) if p and p != ours
        )
    return env


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

    def run(self, cmd: str, timeout: int) -> subprocess.CompletedProcess:
        """Run one of the project's own shell commands inside the worktree.

        Every command that belongs to the trained project -- setup, tests, the repro --
        goes through here, so the environment scrubbing in `project_env` applies
        uniformly instead of being remembered at four call sites.
        """
        return subprocess.run(
            cmd, shell=True, cwd=str(self.path), env=project_env(),
            capture_output=True, text=True, timeout=timeout,
        )

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


def stale_sessions(repo: Path) -> list[Worktree]:
    """Every `dpt/*` worktree still registered in this repo.

    A crashed session leaves both a checkout and a branch behind. Reconstructing them
    from `git worktree list` rather than from our own state means litter is findable
    even when the session that made it never got to write anything down.
    """
    repo = repo.resolve()
    out = []
    for block in _git(repo, "worktree", "list", "--porcelain",
                      check=False).stdout.split("\n\n"):
        path = branch = None
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree ").strip())
            elif line.startswith("branch "):
                branch = line.removeprefix("branch ").strip().removeprefix("refs/heads/")
        if path and branch and branch.startswith("dpt/"):
            out.append(Worktree(repo=repo, path=path, branch=branch))
    return out


def teardown(wt: Worktree) -> None:
    """Best-effort and idempotent -- a failed teardown must never block the next
    session, but it also must not leave a branch lying around in the user's repo."""
    _git(wt.repo, "worktree", "remove", "--force", str(wt.path), check=False)
    _git(wt.repo, "worktree", "prune", check=False)
    _git(wt.repo, "branch", "-D", wt.branch, check=False)
