"""The code review round.

Not validated against a real project yet -- see docs/ROADMAP.md. What's covered here is
the part that shares the debug engine: the tests-must-pass gate, and the guarantee that
a failed generation doesn't leave a worktree behind in a repo the user works in.
"""

from __future__ import annotations

import json

import pytest

from dpt import debug_flow, review_flow, srs, worktree
from tests.conftest import git

PR = {
    "pr_title": "feat: add a subtotal helper",
    "pr_body": "Adds a helper for running subtotals.",
    "files_changed": ["src/calc.py"],
    "planted_issues": [
        {"id": "i1", "class": "unbounded-growth", "dimension": "performance",
         "file": "src/calc.py", "line": 3, "severity": "major",
         "description": "cache never evicts", "why_it_matters": "memory grows"},
    ],
    "decoys": [{"file": "src/calc.py", "line": 1, "looks_wrong": "shadowing",
                "why_its_fine": "it's local"}],
    "correct_verdict": "request-changes",
}
# Keeps `check.py` passing, so the tests-must-pass gate is satisfied.
BENIGN = (
    "_cache = {}\n\n\ndef add(a, b):\n    _cache[(a, b)] = a + b\n    return a + b\n\n\n"
    "def total(items):\n    return sum(items)\n"
)


@pytest.fixture
def project(tmp_repo) -> dict:
    return {
        "path": str(tmp_repo),
        "name": tmp_repo.name,
        "test_cmd": "python check.py",
        "setup_cmd": None,
        "debug_eligible": True,
    }


@pytest.fixture
def picks() -> list[dict]:
    card = srs.new_card("review::unbounded-growth", deck="review",
                        dimension="performance")
    issue = next(i for i in review_flow.load_issue_classes()["issues"])
    return [{"card": card, "issue": issue}]


def test_accepts_a_pr_whose_tests_still_pass(project, picks, fake_claude):
    fake_claude.script({"result": json.dumps(PR), "edits": {"src/calc.py": BENIGN}})

    session = review_flow.start(project, picks)
    try:
        assert session.pr["pr_title"] == PR["pr_title"]
        assert "_cache" in session.diff()
    finally:
        worktree.teardown(session.wt)


def test_rejects_a_pr_that_breaks_the_suite(project, picks, fake_claude, tmp_repo):
    """If CI would catch it, human review isn't what's being trained."""
    broken = "def add(a, b):\n    return a - b\n\n\ndef total(items):\n    return 0\n"
    fake_claude.script({"result": json.dumps(PR), "edits": {"src/calc.py": broken}})

    with pytest.raises(debug_flow.GateError, match="test suite fails"):
        review_flow.start(project, picks)

    # And nothing is left behind in the user's repo.
    assert git(tmp_repo, "branch", "--list", "dpt/*").strip() == ""


def test_setup_command_runs_for_review_too(project, picks, fake_claude):
    project["setup_cmd"] = "echo x > .bootstrapped"
    fake_claude.script({"result": json.dumps(PR), "edits": {"src/calc.py": BENIGN}})

    session = review_flow.start(project, picks)
    try:
        assert (session.wt.path / ".bootstrapped").exists()
    finally:
        worktree.teardown(session.wt)


def test_parse_review_pulls_out_verdict_and_comments():
    verdict, comments = review_flow.parse_review(
        "# a comment line to ignore\n"
        "verdict: approve\n"
        "\n"
        "src/calc.py:3 - the cache never evicts\n"
        "src/calc.py:9 - nit: naming\n"
    )
    assert verdict == "approve"
    assert comments == [
        "src/calc.py:3 - the cache never evicts",
        "src/calc.py:9 - nit: naming",
    ]


def test_pick_issues_spreads_across_dimensions():
    cards = review_flow.seed_cards([])
    picked = review_flow.pick_issues(cards, n=3)

    assert len(picked) == 3
    dims = [p["issue"]["dimension"] for p in picked]
    assert len(set(dims)) == 3  # a PR wrong in only one way trains a narrow eye
