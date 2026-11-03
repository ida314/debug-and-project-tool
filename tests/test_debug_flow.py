"""The generation gate.

This is the file that matters. `docs/DESIGN.md` states the rule the whole tool rests on:

    the injector must supply a `repro_cmd` that exits non-zero on the bugged tree and
    zero on the clean tree, and we verify both before handing the session to you

Both directions are tested here, because checking only the first one -- which is what
the code did originally -- accepts a repro that fails for reasons unrelated to any
injected bug, and hands over an exercise with nothing to find.
"""

from __future__ import annotations

import json

import pytest

from dpt import debug_flow, srs, store, worktree
from tests.conftest import git

# `check.py` in the tmp_repo fixture asserts add(2,2) == 4.
BUGGED_CALC = "def add(a, b):\n    return a - b\n\n\ndef total(items):\n    return 0\n"
GOOD_SPEC = {
    "title": "Totals come out wrong",
    "issue_body": "Adding two numbers gives the wrong answer.",
    "repro_cmd": "python check.py",
    "expected_symptom": "assertion error",
    "root_cause": "add() subtracts",
    "canonical_fix": "use +",
    "estimated_minutes": 10,
    "hints": ["the math layer", "src/calc.py", "add()", "it subtracts"],
}


def spec(**overrides) -> dict:
    return {**GOOD_SPEC, **overrides}


def response(spec_dict: dict, edits: dict | None = None) -> dict:
    return {"result": json.dumps(spec_dict), "edits": edits or {}}


@pytest.fixture
def project(tmp_repo) -> dict:
    return {
        "path": str(tmp_repo),
        "name": tmp_repo.name,
        "test_cmd": "python check.py",
        "run_cmd": None,
        "setup_cmd": None,
        "debug_eligible": True,
    }


@pytest.fixture
def card() -> dict:
    return srs.new_card("off-by-one-boundary", deck="debug", level=1)


# --- the happy path ---


def test_accepts_a_bug_that_fails_bugged_and_passes_clean(project, card, fake_claude):
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))

    session = debug_flow.start(project, card)
    try:
        assert session.spec["root_cause"] == "add() subtracts"
        # The bug survived the clean-tree check and is still present.
        assert "a - b" in (session.wt.path / "src" / "calc.py").read_text()
        solved, _ = debug_flow.verify(session)
        assert not solved
    finally:
        worktree.teardown(session.wt)


def test_the_answer_key_never_enters_the_worktree(project, card, fake_claude):
    """You must not be able to grep your way to the answer."""
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))

    session = debug_flow.start(project, card)
    try:
        blob = "\n".join(
            p.read_text(errors="ignore")
            for p in session.wt.path.rglob("*")
            if p.is_file() and ".git" not in p.parts
        )
        assert "add() subtracts" not in blob
        assert "the math layer" not in blob

        stored = json.loads((store.session_dir(session.id) / "spec.json").read_text())
        assert stored["root_cause"] == "add() subtracts"
    finally:
        worktree.teardown(session.wt)


def test_public_spec_withholds_the_root_cause_and_hints(project, card, fake_claude):
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))
    session = debug_flow.start(project, card)
    try:
        public = session.public_spec()
        assert set(public) <= {"title", "issue_body", "repro_cmd", "expected_symptom"}
        assert "root_cause" not in public
        assert "hints" not in public
    finally:
        worktree.teardown(session.wt)


def test_injection_is_committed_under_a_mundane_message(project, card, fake_claude):
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))
    session = debug_flow.start(project, card)
    try:
        subject = session.wt.git("log", "-1", "--format=%s").stdout.strip()
        assert subject == "refactor: minor cleanup"
        assert session.wt.diff().strip() == ""
    finally:
        worktree.teardown(session.wt)


# --- the gate rejecting bad exercises ---


def test_rejects_a_repro_that_passes_on_the_bugged_tree(project, card, fake_claude):
    """No bug was actually introduced."""
    fake_claude.script(response(spec()))  # no edits at all

    with pytest.raises(debug_flow.GateError, match="passed on the bugged tree"):
        debug_flow.start(project, card)


def test_rejects_a_repro_that_also_fails_on_the_clean_tree(project, card, fake_claude):
    """The half of the gate that was missing.

    The command fails either way -- it points at a file that does not exist -- so it
    proves nothing about the injected bug. The old code accepted this.
    """
    fake_claude.script(
        response(spec(repro_cmd="python no_such_file.py"), {"src/calc.py": BUGGED_CALC})
    )

    with pytest.raises(debug_flow.GateError, match="also failed on the clean tree"):
        debug_flow.start(project, card)


def test_rejects_a_spec_with_no_repro_cmd(project, card, fake_claude):
    bad = spec()
    del bad["repro_cmd"]
    fake_claude.script(response(bad, {"src/calc.py": BUGGED_CALC}))

    with pytest.raises(debug_flow.GateError, match="no repro_cmd"):
        debug_flow.start(project, card)


def test_retries_and_accepts_a_later_good_attempt(project, card, fake_claude):
    fake_claude.script(
        response(spec()),                                        # no bug introduced
        response(spec(repro_cmd="python no_such_file.py"),       # fails both ways
                 {"src/calc.py": BUGGED_CALC}),
        response(spec(), {"src/calc.py": BUGGED_CALC}),          # finally good
    )

    session = debug_flow.start(project, card)
    try:
        assert len(fake_claude.calls()) == 3
    finally:
        worktree.teardown(session.wt)


def test_a_failed_attempt_does_not_leak_files_into_the_next(project, card, fake_claude):
    fake_claude.script(
        response(spec(), {"junk.txt": "left over from attempt 1"}),
        response(spec(), {"src/calc.py": BUGGED_CALC}),
    )

    session = debug_flow.start(project, card)
    try:
        assert not (session.wt.path / "junk.txt").exists()
    finally:
        worktree.teardown(session.wt)


# --- failure must not litter the user's repo ---


def test_a_failed_generation_tears_the_worktree_down(project, card, tmp_repo, fake_claude):
    fake_claude.script(response(spec()))

    with pytest.raises(debug_flow.GateError):
        debug_flow.start(project, card)

    assert git(tmp_repo, "branch", "--list", "dpt/*").strip() == ""
    assert str(tmp_repo) in git(tmp_repo, "worktree", "list")
    assert len(git(tmp_repo, "worktree", "list").strip().splitlines()) == 1


def test_an_llm_error_tears_the_worktree_down(project, card, tmp_repo, fake_claude):
    fake_claude.script({"exit_code": 1, "stderr": "overloaded"})

    with pytest.raises(debug_flow.GateError):
        debug_flow.start(project, card)

    assert git(tmp_repo, "branch", "--list", "dpt/*").strip() == ""


# --- environment checks, which run before we spend an LLM call ---


def test_a_red_baseline_is_refused_before_any_llm_call(project, card, fake_claude):
    """A project whose suite is already failing can't prove anything with a repro."""
    project["test_cmd"] = "exit 1"
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))

    with pytest.raises(debug_flow.GateError, match="does not pass in a fresh worktree"):
        debug_flow.start(project, card)
    assert fake_claude.calls() == []


def test_a_failing_setup_command_is_refused(project, card, fake_claude):
    project["setup_cmd"] = "exit 3"
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))

    with pytest.raises(debug_flow.GateError, match="setup command failed"):
        debug_flow.start(project, card)
    assert fake_claude.calls() == []


def test_setup_command_runs_in_the_worktree(project, card, fake_claude):
    project["setup_cmd"] = "echo bootstrapped > .venv-marker"
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))

    session = debug_flow.start(project, card)
    try:
        assert (session.wt.path / ".venv-marker").exists()
        assert not (session.wt.repo / ".venv-marker").exists()
    finally:
        worktree.teardown(session.wt)


def test_baseline_is_cached_per_commit(project, card, fake_claude, tmp_path):
    """A 70-second suite must not be re-run on every session."""
    marker = tmp_path / "baseline-runs"
    project["test_cmd"] = f"echo x >> {marker}; python check.py"
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))

    session = debug_flow.start(project, card)
    worktree.teardown(session.wt)
    assert len(marker.read_text().splitlines()) == 1
    assert project["baseline_ok_at"]

    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))
    session = debug_flow.start(project, card)
    worktree.teardown(session.wt)
    assert len(marker.read_text().splitlines()) == 1  # not re-run


# --- solving and grading ---


def test_a_correct_fix_verifies_and_grades_well(project, card, fake_claude):
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))
    session = debug_flow.start(project, card)

    (session.wt.path / "src" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef total(items):\n    return sum(items)\n"
    )
    solved, _ = debug_flow.verify(session)
    assert solved

    fake_claude.script({"result": json.dumps(
        {"fixed_root_cause": True, "quality": 5, "feedback": "good"}
    )})
    cards = [card]
    grade = debug_flow.finish(session, solved, cards)

    assert grade["solved"] is True
    assert grade["quality"] == 5
    assert card["due"] > grade["session"][:10]  # scheduled into the future
    assert (store.session_dir(session.id) / "solution.patch").exists()
    assert json.loads((store.session_dir(session.id) / "grade.json").read_text())


def test_giving_up_grades_as_a_failure_and_reveals_the_cause(project, card, fake_claude):
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))
    session = debug_flow.start(project, card)

    grade = debug_flow.finish(session, solved=False, cards=[card])
    assert grade["quality"] == 1
    assert grade["root_cause"] == "add() subtracts"


def test_hints_are_handed_out_in_order_then_run_dry(project, card, fake_claude):
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))
    session = debug_flow.start(project, card)
    try:
        assert session.next_hint() == "the math layer"
        assert session.next_hint() == "src/calc.py"
        assert session.next_hint() == "add()"
        assert session.next_hint() == "it subtracts"
        assert session.next_hint() is None
        assert session.hints_used == 4
    finally:
        worktree.teardown(session.wt)


def test_finish_tears_the_worktree_down(project, card, tmp_repo, fake_claude):
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))
    session = debug_flow.start(project, card)
    debug_flow.finish(session, solved=False, cards=[card])

    assert not session.wt.path.exists()
    assert git(tmp_repo, "branch", "--list", "dpt/*").strip() == ""
