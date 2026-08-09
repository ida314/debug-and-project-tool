"""The debug round: propose -> inject -> verify-the-bug -> brief -> solve -> grade.

Read docs/DESIGN.md#flow-a before changing the injection contract; the fails-then-
passes guarantee on `repro_cmd` is what makes the rest of the flow mechanical.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from . import config, llm, prompts, srs, store, worktree

MAX_INJECT_ATTEMPTS = 3


def load_categories() -> list[dict]:
    with config.DEBUG_CATEGORIES.open() as f:
        return yaml.safe_load(f)


def seed_cards(cards: list[dict]) -> list[dict]:
    """Register a card per bug class. Idempotent, so new taxonomy entries just appear."""
    known = {c["id"] for c in cards}
    for cat in load_categories():
        if cat["id"] not in known:
            cards.append(
                srs.new_card(cat["id"], deck="debug", name=cat["name"],
                             level=cat.get("level", 1))
            )
    return cards


def run_repro(wt: worktree.Worktree, repro_cmd: str, timeout: int = 120) -> tuple[bool, str]:
    """True means the repro PASSED, i.e. the bug is absent."""
    try:
        proc = subprocess.run(
            repro_cmd, shell=True, cwd=str(wt.path),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"repro timed out after {timeout}s"
    output = (proc.stdout + proc.stderr)[-4000:]
    return proc.returncode == 0, output


@dataclass
class DebugSession:
    id: str
    project: dict
    card: dict
    wt: worktree.Worktree
    spec: dict
    started_at: float = field(default_factory=time.monotonic)
    hints_used: int = 0

    @property
    def elapsed_minutes(self) -> float:
        return (time.monotonic() - self.started_at) / 60

    def public_spec(self) -> dict:
        """What the candidate is allowed to see. Root cause and hints stay server-side."""
        return {
            k: self.spec[k]
            for k in ("title", "issue_body", "repro_cmd", "expected_symptom")
            if k in self.spec
        }

    def next_hint(self) -> str | None:
        hints = self.spec.get("hints", [])
        if self.hints_used >= len(hints):
            return None
        hint = hints[self.hints_used]
        self.hints_used += 1
        store.append_timeline(self.id, {"t": time.time(), "event": "hint",
                                        "level": self.hints_used})
        return hint


def start(project: dict, card: dict) -> DebugSession:
    """Set up a session. Raises if we can't produce a bug that actually reproduces --
    better to fail loudly here than to hand over an ungradeable exercise."""
    config.ensure_dirs()
    session_id = f"{date.today().isoformat()}-{uuid.uuid4().hex[:6]}"
    wt = worktree.create(Path(project["path"]), session_id)

    cat = next((c for c in load_categories() if c["id"] == card["id"]), {})
    prompt = prompts.INJECT.format(
        card_name=cat.get("name", card["id"]),
        card_hint=cat.get("hint", ""),
        level=card.get("level", 1),
        techniques=", ".join(cat.get("rewards", ["read"])),
        minutes=10 + 5 * card.get("level", 1),
        test_cmd=project.get("test_cmd") or "",
        run_cmd=project.get("run_cmd") or "",
    )

    last_error = "no attempt made"
    for attempt in range(MAX_INJECT_ATTEMPTS):
        try:
            spec = llm.run_json(prompt, cwd=wt.path, allow_edits=True)
        except llm.LLMError as e:
            last_error = str(e)
            continue

        repro = spec.get("repro_cmd")
        if not repro:
            last_error = "spec had no repro_cmd"
            wt.git("checkout", "--", ".", check=False)
            continue

        # The gate: the bug must actually reproduce. Everything downstream assumes it.
        passed, output = run_repro(wt, repro)
        if passed:
            last_error = f"repro_cmd passed on the bugged tree (attempt {attempt + 1})"
            wt.git("checkout", "--", ".", check=False)
            wt.git("clean", "-fd", check=False)
            continue

        spec["_repro_output"] = output
        store.write_session_file(session_id, "spec.json", json.dumps(spec, indent=2))
        store.write_session_file(session_id, "injection.patch", wt.diff())
        # Plausible message: a `git log` glance shouldn't scream "here it is".
        wt.commit_all("refactor: minor cleanup")
        store.append_timeline(session_id, {"t": time.time(), "event": "start",
                                           "project": project["name"],
                                           "card": card["id"]})
        return DebugSession(id=session_id, project=project, card=card, wt=wt, spec=spec)

    worktree.teardown(wt)
    raise RuntimeError(
        f"could not inject a reproducible bug after {MAX_INJECT_ATTEMPTS} attempts: {last_error}"
    )


def verify(session: DebugSession) -> tuple[bool, str]:
    solved, output = run_repro(session.wt, session.spec["repro_cmd"])
    store.append_timeline(session.id, {"t": time.time(), "event": "verify",
                                       "passed": solved})
    return solved, output


def snapshot(session: DebugSession, note: str | None = None) -> None:
    """Cheap timeline: what the diff looked like, and what you were thinking."""
    store.append_timeline(session.id, {
        "t": time.time(),
        "event": "snapshot",
        "note": note,
        "diff_lines": len(session.wt.diff().splitlines()),
    })


def finish(session: DebugSession, solved: bool, cards: list[dict]) -> dict:
    """Judge the fix, grade the card, write the log, tear the worktree down."""
    diff = session.wt.diff()
    store.write_session_file(session.id, "solution.patch", diff)

    judgment = {"fixed_root_cause": solved, "quality": 3, "feedback": ""}
    if solved and diff.strip():
        try:
            judgment = llm.run_json(prompts.JUDGE_FIX.format(
                issue_body=session.spec.get("issue_body", ""),
                root_cause=session.spec.get("root_cause", ""),
                canonical_fix=session.spec.get("canonical_fix", ""),
                diff=diff[:20000],
            ), cwd=session.wt.path)
        except llm.LLMError as e:
            judgment["feedback"] = f"(fix judge unavailable: {e})"

    quality = srs.debug_quality(
        solved=solved,
        hints_used=session.hints_used,
        minutes=session.elapsed_minutes,
        estimated_minutes=float(session.spec.get("estimated_minutes", 15)),
        fixed_root_cause=bool(judgment.get("fixed_root_cause")),
    )
    srs.review(session.card, quality)

    grade = {
        "session": session.id,
        "project": session.project["name"],
        "card": session.card["id"],
        "solved": solved,
        "minutes": round(session.elapsed_minutes, 1),
        "estimated_minutes": session.spec.get("estimated_minutes"),
        "hints_used": session.hints_used,
        "quality": quality,
        "next_due": session.card["due"],
        "judgment": judgment,
        "root_cause": session.spec.get("root_cause"),
    }
    store.write_session_file(session.id, "grade.json", json.dumps(grade, indent=2))

    session.project["last_debugged"] = date.today().isoformat()
    store.save_cards(cards)
    worktree.teardown(session.wt)
    return grade
