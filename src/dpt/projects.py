"""Build the project index.

`run_cmd` / `test_cmd` can't be reliably inferred, so we ask once and cache. A project
without a usable command is marked debug-ineligible rather than guessed at -- a bug we
can't reproduce is a bug we can't grade.
"""

from __future__ import annotations

from pathlib import Path

from . import store, worktree

LANG_MARKERS = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "requirements.txt": "python",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "package.json": "javascript",
    "CMakeLists.txt": "cpp",
    "Makefile": "c",
}

TEST_MARKERS = ("tests", "test", "__tests__", "spec")

SKIP_DIRS = {
    ".git", "node_modules", "target", "venv", ".venv", "__pycache__",
    "dist", "build", ".next",
}


def detect_lang(path: Path) -> str | None:
    for marker, lang in LANG_MARKERS.items():
        if (path / marker).exists():
            return lang
    return None


def count_loc(path: Path, limit: int = 20000) -> int:
    """Rough size signal for difficulty calibration. Capped so scanning stays fast."""
    total = 0
    exts = {".py", ".rs", ".go", ".js", ".ts", ".tsx", ".c", ".h", ".cpp", ".java"}
    for f in path.rglob("*"):
        if total > limit:
            break
        if f.is_file() and f.suffix in exts and not (SKIP_DIRS & set(f.parts)):
            try:
                total += sum(1 for _ in f.open(errors="ignore"))
            except OSError:
                continue
    return total


def scan(root: Path) -> list[dict]:
    """One level deep: `~/Projects/*` is the shape everyone actually has."""
    found = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        lang = detect_lang(child)
        if lang is None and not worktree.is_git_repo(child):
            continue
        found.append(
            {
                "path": str(child.resolve()),
                "name": child.name,
                "lang": lang,
                "loc": count_loc(child),
                "is_git": worktree.is_git_repo(child),
                "has_tests": any((child / t).exists() for t in TEST_MARKERS),
                "run_cmd": None,
                "test_cmd": None,
                "debug_eligible": None,  # set once run/test cmd is known
                "last_debugged": None,
                "summary": None,
            }
        )
    return found


def merge(existing: list[dict], scanned: list[dict]) -> list[dict]:
    """Re-scanning must never clobber the per-project commands you typed in."""
    by_path = {p["path"]: p for p in existing}
    out = []
    for proj in scanned:
        prior = by_path.get(proj["path"])
        if prior:
            for key in ("run_cmd", "test_cmd", "debug_eligible",
                        "last_debugged", "summary"):
                proj[key] = prior.get(key)
        out.append(proj)
    return out


def eligible_for_debug(projects: list[dict]) -> list[dict]:
    return [
        p for p in projects
        if p["is_git"] and p.get("debug_eligible") and (p.get("test_cmd") or p.get("run_cmd"))
    ]


def pick_project(projects: list[dict], avoid: str | None = None) -> dict | None:
    """Rotate away from the most recently used project. Reading unfamiliar code is
    the skill being trained, so variety here is a feature, not a nicety."""
    pool = eligible_for_debug(projects)
    if not pool:
        return None
    fresh = [p for p in pool if p["path"] != avoid] or pool
    return min(fresh, key=lambda p: p.get("last_debugged") or "")


def load() -> list[dict]:
    return store.load_projects()
