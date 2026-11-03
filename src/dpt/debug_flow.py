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
# A bare checkout may have to build a virtualenv from scratch.
SETUP_TIMEOUT_S = 900
BASELINE_TIMEOUT_S = 900


class GateError(RuntimeError):
    """The exercise could not be generated to spec. Never hand one over anyway."""


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


def run_repro(wt: worktree.Worktree, repro_cmd: str, timeout: int = 300) -> tuple[bool, str]:
    """True means the repro PASSED, i.e. the bug is absent."""
    try:
        proc = wt.run(repro_cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"repro timed out after {timeout}s"
    output = (proc.stdout + proc.stderr)[-4000:]
    return proc.returncode == 0, output


def _reset_worktree(wt: worktree.Worktree) -> None:
    """Undo everything an attempt did, so retries start from a clean tree.

    `git clean -fd` deliberately omits `-x`: ignored files are where the bootstrapped
    virtualenv lives, and re-running `uv sync` on every retry would triple the cost of
    a failed generation.
    """
    wt.git("checkout", "--", ".", check=False)
    wt.git("clean", "-fd", check=False)


def prepare_worktree(project: dict, wt: worktree.Worktree) -> None:
    """Bootstrap the throwaway checkout so the project's own commands can run in it.

    A worktree has tracked files and nothing else -- no virtualenv, no node_modules, no
    editable install -- so without this the project's test command fails for reasons
    that have nothing to do with the injected bug.
    """
    setup_cmd = project.get("setup_cmd")
    if not setup_cmd:
        return
    print(f"  setting up the worktree: {setup_cmd}")
    proc = wt.run(setup_cmd, timeout=SETUP_TIMEOUT_S)
    if proc.returncode != 0:
        raise GateError(
            f"setup command failed in a fresh worktree of {project['name']}:\n"
            f"  $ {setup_cmd}\n{(proc.stdout + proc.stderr)[-2000:]}"
        )


def check_baseline(project: dict, wt: worktree.Worktree) -> None:
    """The project's own tests must pass on the clean worktree before we bug it.

    Without this, a project whose suite is already red (or whose environment didn't
    bootstrap) produces a repro that "fails" for reasons unrelated to any injected bug
    -- which satisfies the fails-on-bugged-tree half of the gate and hands over an
    exercise with nothing to find. Cached per commit; the suite can take a minute.
    """
    test_cmd = project.get("test_cmd")
    if not test_cmd:
        return
    head = wt.git("rev-parse", "HEAD").stdout.strip()
    if project.get("baseline_ok_at") == head:
        return

    print(f"  checking the baseline is green: {test_cmd}")
    started = time.monotonic()
    passed, output = run_repro(wt, test_cmd, timeout=BASELINE_TIMEOUT_S)
    # How slow the suite is decides how the injector should spend its budget, so we
    # measure it here rather than making the model discover it the expensive way.
    project["test_seconds"] = round(time.monotonic() - started)
    if not passed:
        raise GateError(
            f"{project['name']}'s test command does not pass in a fresh worktree, so a "
            f"failing repro would prove nothing.\n"
            f"  $ {test_cmd}\n"
            f"Check `setup_cmd` (currently: {project.get('setup_cmd') or 'none'}) "
            f"via `dpt scan --reconfigure`.\n{output[-1500:]}"
        )
    project["baseline_ok_at"] = head


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


def check_clean_tree(wt: worktree.Worktree, repro: str) -> tuple[bool, str]:
    """The other half of the gate: the repro must PASS once the bug is removed.

    Failing on the bugged tree proves nothing on its own -- a typo'd command, a missing
    dependency or an already-red suite all fail too. Only a command that fails *because
    of the injection* flips back to passing when the injection is reverted.

    Method: revert the injector's tracked edits, leaving any new untracked file (the
    repro script it may have written) in place, then restore from the patch. `git stash`
    would be the obvious tool and is deliberately avoided -- it writes to `refs/stash`
    in the *user's* repo, so a crash mid-check would leak state outside the worktree.
    A patch string leaks nothing.
    """
    patch = wt.diff()
    if not patch.strip():
        return False, "injector made no tracked edits, so there is nothing to revert"

    wt.git("checkout", "--", ".", check=False)
    try:
        passed, output = run_repro(wt, repro)
    finally:
        proc = subprocess.run(
            ["git", "-C", str(wt.path), "apply", "--whitespace=nowarn", "-"],
            input=patch, capture_output=True, text=True,
        )
        if proc.returncode != 0 or wt.diff() != patch:
            # Restoring the bug is the one step with no safe fallback: a partially
            # reapplied patch is a corrupted exercise, so refuse rather than guess.
            raise GateError(
                "could not restore the injected bug after the clean-tree check; "
                f"aborting rather than handing over a corrupted tree: {proc.stderr[-500:]}"
            )
    return passed, output


def start(project: dict, card: dict) -> DebugSession:
    """Set up a session. Raises if we can't produce a bug that actually reproduces --
    better to fail loudly here than to hand over an ungradeable exercise.

    The gate, in order: the tree bootstraps, its own suite is green, the repro fails
    with the bug present, and the repro passes with the bug removed. Everything
    downstream -- `verify`, the grade, the SRS schedule -- assumes all four.
    """
    config.ensure_dirs()
    session_id = f"{date.today().isoformat()}-{uuid.uuid4().hex[:6]}"
    wt = worktree.create(Path(project["path"]), session_id)

    try:
        prepare_worktree(project, wt)
        check_baseline(project, wt)
        return _inject(project, card, wt, session_id)
    except BaseException:
        # Any failure past worktree.create would otherwise leave an orphan worktree and
        # a dpt/* branch behind in a repo the user actually works in.
        worktree.teardown(wt)
        raise


def _inject(project: dict, card: dict, wt: worktree.Worktree,
            session_id: str) -> DebugSession:
    cat = next((c for c in load_categories() if c["id"] == card["id"]), {})
    prompt = prompts.INJECT.format(
        card_name=cat.get("name", card["id"]),
        card_hint=cat.get("hint", ""),
        level=card.get("level", 1),
        techniques=", ".join(cat.get("rewards", ["read"])),
        minutes=10 + 5 * card.get("level", 1),
        test_cmd=project.get("test_cmd") or "",
        run_cmd=project.get("run_cmd") or "",
        test_cost=(f"takes about {project['test_seconds']} seconds"
                   if project.get("test_seconds") else "may be slow"),
    )

    last_error = "no attempt made"
    for attempt in range(MAX_INJECT_ATTEMPTS):
        try:
            spec = llm.run_json(prompt, cwd=wt.path, allow_edits=True,
                                label=f"injecting a bug (attempt {attempt + 1})")
        except llm.LLMError as e:
            last_error = str(e)
            _reset_worktree(wt)
            continue

        repro = spec.get("repro_cmd")
        if not repro:
            last_error = "spec had no repro_cmd"
            _reset_worktree(wt)
            continue

        # Gate, part one: the bug must actually reproduce.
        passed, output = run_repro(wt, repro)
        if passed:
            last_error = f"repro_cmd passed on the bugged tree (attempt {attempt + 1})"
            _reset_worktree(wt)
            continue

        # Gate, part two: and it must stop reproducing once the bug is gone.
        clean_passed, clean_output = check_clean_tree(wt, repro)
        if not clean_passed:
            last_error = (
                f"repro_cmd also failed on the clean tree (attempt {attempt + 1}), so it "
                f"isn't testing the injected bug: {clean_output[-500:]}"
            )
            _reset_worktree(wt)
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

    raise GateError(
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


def finish(session: DebugSession, solved: bool, cards: list[dict],
           forced: bool = False) -> dict:
    """Judge the fix, grade the card, write the log, tear the worktree down.

    `forced` records that the candidate asserted a correct fix the repro command didn't
    recognise. It is flagged rather than blocked -- see docs/OPEN-QUESTIONS.md #2: the
    harness must never tell a correct engineer they're wrong.
    """
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
            ), cwd=session.wt.path, label="judging the fix")
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
        # Self-asserted rather than verified by the repro command; audit these.
        "forced": forced,
    }
    store.write_session_file(session.id, "grade.json", json.dumps(grade, indent=2))

    session.project["last_debugged"] = date.today().isoformat()
    store.save_cards(cards)
    worktree.teardown(session.wt)
    return grade
