"""JSON persistence. Deliberately dumb: these files should be greppable and hand-
editable while the design is still moving. Swap for SQLite when queries get real."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config


def _read(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open() as f:
        return json.load(f)


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def load_projects() -> list[dict]:
    return _read(config.PROJECTS_FILE, [])


def save_projects(projects: list[dict]) -> None:
    _write(config.PROJECTS_FILE, projects)


def load_cards() -> list[dict]:
    return _read(config.CARDS_FILE, [])


def save_cards(cards: list[dict]) -> None:
    _write(config.CARDS_FILE, cards)


def session_dir(session_id: str) -> Path:
    d = config.SESSIONS_DIR / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_session_file(session_id: str, name: str, content: str) -> Path:
    path = session_dir(session_id) / name
    path.write_text(content)
    return path


def append_timeline(session_id: str, event: dict) -> None:
    """One JSON object per line: hints, diff snapshots, hypothesis notes, verify
    attempts. Replayed as a timeline at review time -- the useful artifact is seeing
    where the time actually went, not the pass/fail."""
    path = session_dir(session_id) / "timeline.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(event) + "\n")
