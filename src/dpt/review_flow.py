"""The code review round: an LLM opens a PR, you review it as the maintainer.

Same engine as the debug round -- worktree, planted defects, hidden answer key in the
session dir -- with the interaction inverted. Debug gives you a symptom and asks for a
fix; review gives you a diff and asks what's wrong with it, without running anything.

The mechanical gate mirrors the debug flow's, and reversed:

    debug:  the repro command MUST FAIL   (or the exercise isn't findable)
    review: the test suite  MUST PASS     (or CI would have caught it, and review
                                           isn't what's being trained)

That second rule is what forces planted issues to be the kind of thing only a human
reader catches.
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

from . import config, debug_flow, llm, prompts, srs, store, worktree

MAX_PR_ATTEMPTS = 3
ISSUES_PER_PR = 3

REVIEW_TEMPLATE = """\
# Review: {title}
#
# Leave one comment per line as `path/to/file.py:LINE - your comment`.
# Set the verdict below. Lines starting with # are ignored.
#
# You are the maintainer. Flagging everything is not reviewing -- precision is graded
# alongside recall, and some of what looks wrong here is fine.

verdict: request-changes

## Comments

"""


def load_issue_classes() -> dict:
    with config.REVIEW_ISSUES.open() as f:
        return yaml.safe_load(f)


def seed_cards(cards: list[dict]) -> list[dict]:
    """One card per issue class. Idempotent."""
    known = {c["id"] for c in cards}
    for issue in load_issue_classes()["issues"]:
        card_id = f"review::{issue['id']}"
        if card_id not in known:
            cards.append(srs.new_card(
                card_id, deck="review", name=issue["name"],
                level=issue.get("level", 1), dimension=issue["dimension"],
            ))
    return cards


def pick_issues(cards: list[dict], n: int = ISSUES_PER_PR) -> list[dict]:
    """Pick the most overdue review cards, spread across dimensions.

    A PR whose problems are all in one dimension trains a narrow eye; real PRs go
    wrong in several ways at once.
    """
    classes = {i["id"]: i for i in load_issue_classes()["issues"]}
    due = srs.due_cards(cards, deck="review")
    picked: list[dict] = []
    seen_dims: set[str] = set()

    for card in due:
        issue = classes.get(card["id"].removeprefix("review::"))
        if issue and issue["dimension"] not in seen_dims:
            picked.append({"card": card, "issue": issue})
            seen_dims.add(issue["dimension"])
        if len(picked) == n:
            return picked

    for card in due:  # backfill if we ran out of distinct dimensions
        issue = classes.get(card["id"].removeprefix("review::"))
        if issue and not any(p["card"]["id"] == card["id"] for p in picked):
            picked.append({"card": card, "issue": issue})
        if len(picked) == n:
            break
    return picked


def tests_pass(wt: worktree.Worktree, test_cmd: str | None, timeout: int = 300) -> bool:
    if not test_cmd:
        return True  # nothing to gate on; the critic pass carries more weight then
    try:
        proc = wt.run(test_cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0


@dataclass
class ReviewSession:
    id: str
    project: dict
    picks: list[dict]
    wt: worktree.Worktree
    pr: dict
    base_ref: str
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed_minutes(self) -> float:
        return (time.monotonic() - self.started_at) / 60

    def diff(self) -> str:
        return self.wt.git("diff", f"{self.base_ref}..HEAD").stdout

    def diff_stat(self) -> str:
        return self.wt.git("diff", "--stat", f"{self.base_ref}..HEAD").stdout

    def review_path(self) -> Path:
        """The review draft lives in the session dir, not the worktree -- teardown
        must not take your work with it."""
        return store.session_dir(self.id) / "review.md"

    def write_review_template(self) -> Path:
        path = self.review_path()
        if not path.exists():
            path.write_text(REVIEW_TEMPLATE.format(title=self.pr["pr_title"]))
        return path


def parse_review(text: str) -> tuple[str, list[str]]:
    """Pull the verdict and comment lines out of the review draft."""
    verdict = "comment"
    comments = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("verdict:"):
            verdict = line.split(":", 1)[1].strip().lower()
            continue
        comments.append(line)
    return verdict, comments


def start(project: dict, picks: list[dict]) -> ReviewSession:
    """Generate the PR. Raises rather than handing over an exercise CI would solve."""
    config.ensure_dirs()
    session_id = f"{date.today().isoformat()}-{uuid.uuid4().hex[:6]}"
    wt = worktree.create(Path(project["path"]), session_id)
    try:
        return _implement(project, picks, wt, session_id)
    except BaseException:
        worktree.teardown(wt)
        raise


def _implement(project: dict, picks: list[dict], wt: worktree.Worktree,
               session_id: str) -> ReviewSession:
    # Same bootstrap the debug flow needs: a bare worktree has no virtualenv, so the
    # tests-must-pass gate would otherwise fail for reasons unrelated to the PR.
    debug_flow.prepare_worktree(project, wt)
    base_ref = wt.git("rev-parse", "HEAD").stdout.strip()

    issue_list = "\n".join(
        f"  - {p['issue']['id']} ({p['issue']['dimension']}, {p['issue']['severity']}): "
        f"{p['issue']['name']} -- {p['issue']['hint']}"
        for p in picks
    )
    prompt = prompts.IMPLEMENT_PR.format(
        issue_list=issue_list,
        test_cmd=project.get("test_cmd") or "",
        minutes=8 + 4 * len(picks),
    )

    last_error = "no attempt made"
    for attempt in range(MAX_PR_ATTEMPTS):
        try:
            pr = llm.run_json(prompt, cwd=wt.path, allow_edits=True)
        except llm.LLMError as e:
            last_error = str(e)
            continue

        if not pr.get("planted_issues"):
            last_error = "PR spec had no planted_issues"
        elif not tests_pass(wt, project.get("test_cmd")):
            # If CI catches it, human review isn't what's being exercised.
            last_error = f"test suite fails on the PR branch (attempt {attempt + 1})"
        else:
            store.write_session_file(session_id, "pr.json", json.dumps(pr, indent=2))
            wt.commit_all(pr.get("pr_title", "feat: update"))
            store.append_timeline(session_id, {
                "t": time.time(), "event": "start", "mode": "review",
                "project": project["name"],
                "cards": [p["card"]["id"] for p in picks],
            })
            return ReviewSession(id=session_id, project=project, picks=picks,
                                 wt=wt, pr=pr, base_ref=base_ref)

        wt.git("reset", "--hard", base_ref, check=False)
        wt.git("clean", "-fd", check=False)

    # Teardown is the wrapper's job, so every exit path gets it.
    raise debug_flow.GateError(
        f"could not generate a reviewable PR after {MAX_PR_ATTEMPTS} attempts: {last_error}"
    )


def grade(session: ReviewSession, cards: list[dict]) -> dict:
    """Match the review against the answer key, score each card, tear down."""
    text = session.review_path().read_text() if session.review_path().exists() else ""
    verdict, comments = parse_review(text)

    store.write_session_file(session.id, "pr.diff", session.diff())

    result = llm.run_json(prompts.GRADE_REVIEW.format(
        planted_json=json.dumps(session.pr.get("planted_issues", []), indent=2),
        decoys_json=json.dumps(session.pr.get("decoys", []), indent=2),
        verdict=verdict,
        comments="\n".join(comments) or "(no comments left)",
    ), cwd=session.wt.path)

    caught_ids = {c["issue_id"]: c for c in result.get("caught", [])}
    by_issue_id = {i["id"]: i for i in session.pr.get("planted_issues", [])}

    # Each planted issue scores its own card. This is exactly why cards are skills:
    # one PR can move four independent schedules in four different directions.
    for issue_id, issue in by_issue_id.items():
        card_id = f"review::{issue.get('class', '')}"
        card = next((c for c in cards if c["id"] == card_id), None)
        if card is None:
            continue
        hit = caught_ids.get(issue_id)
        srs.review(card, srs.review_quality(
            caught=hit is not None,
            severity_correct=bool(hit and hit.get("severity_correct")),
        ))

    n_planted = len(by_issue_id) or 1
    n_flagged = (len(result.get("caught", [])) + len(result.get("bonus_findings", []))
                 + len(result.get("false_positives", [])) + len(result.get("noise", [])))
    recall = len(result.get("caught", [])) / n_planted
    precision = ((len(result.get("caught", [])) + len(result.get("bonus_findings", [])))
                 / n_flagged) if n_flagged else 0.0

    grade_doc = {
        "session": session.id,
        "mode": "review",
        "project": session.project["name"],
        "pr_title": session.pr.get("pr_title"),
        "minutes": round(session.elapsed_minutes, 1),
        "recall": round(recall, 2),
        "precision": round(precision, 2),
        "verdict_given": verdict,
        "verdict_expected": session.pr.get("correct_verdict"),
        "result": result,
    }
    store.write_session_file(session.id, "grade.json", json.dumps(grade_doc, indent=2))

    store.save_cards(cards)
    worktree.teardown(session.wt)
    return grade_doc
