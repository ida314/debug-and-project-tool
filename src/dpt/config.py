"""Paths and constants. Everything lives under XDG dirs; nothing is written to the
projects being trained on except inside a throwaway worktree."""

from __future__ import annotations

import os
from pathlib import Path

APP = "dpt"


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / APP
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / APP

PROJECTS_FILE = DATA_DIR / "projects.json"
CARDS_FILE = DATA_DIR / "cards.json"
SESSIONS_DIR = DATA_DIR / "sessions"
# Worktrees are checked out here, never next to the user's repo.
WORKTREE_DIR = CACHE_DIR / "worktrees"

# Package data, not a sibling of the checkout: resolving this relative to the repo root
# works for an editable install and breaks the moment dpt is installed as a wheel.
TAXONOMY_DIR = Path(__file__).resolve().parent / "taxonomy"
DEBUG_CATEGORIES = TAXONOMY_DIR / "debug_categories.yaml"
REVIEW_ISSUES = TAXONOMY_DIR / "review_issues.yaml"
PROJECT_QUESTIONS = TAXONOMY_DIR / "project_questions.yaml"

# `claude -p` on a real repo is slow; injection is the long pole. 300s was measured to
# be too short: on a 14k-LOC project with a 70-second test suite, the injector spends
# most of its budget reading the repo and running the suite to check its own work, and
# timed out on all three attempts. Override for very large repos.
LLM_TIMEOUT_S = int(os.environ.get("DPT_LLM_TIMEOUT") or 900)


def ensure_dirs() -> None:
    for d in (DATA_DIR, CACHE_DIR, SESSIONS_DIR, WORKTREE_DIR):
        d.mkdir(parents=True, exist_ok=True)
