"""CLI-level behaviour: the project index, and cleaning up after a crash."""

from __future__ import annotations

import json

from dpt import cli, config, store, worktree
from tests.conftest import git


def test_scan_records_the_commands_it_is_given(tmp_repo, monkeypatch, capsys):
    answers = iter(["uv sync", "pytest -q", "python -m sample"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    assert cli.main(["scan", "--manual", str(tmp_repo.parent)]) == 0

    proj = next(p for p in store.load_projects() if p["name"] == tmp_repo.name)
    assert proj["setup_cmd"] == "uv sync"
    assert proj["test_cmd"] == "pytest -q"
    assert proj["run_cmd"] == "python -m sample"
    assert proj["debug_eligible"] is True
    assert proj["configured"] is True


def test_rescanning_does_not_re_ask_or_clobber(tmp_repo, monkeypatch):
    answers = iter(["uv sync", "pytest -q", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    cli.main(["scan", "--manual", str(tmp_repo.parent)])

    def explode(_):
        raise AssertionError("should not re-ask an already-configured project")

    monkeypatch.setattr("builtins.input", explode)
    assert cli.main(["scan", "--manual", str(tmp_repo.parent)]) == 0

    proj = next(p for p in store.load_projects() if p["name"] == tmp_repo.name)
    assert proj["test_cmd"] == "pytest -q"


def test_reconfigure_re_asks_and_keeps_blanks_as_the_old_value(tmp_repo, monkeypatch):
    answers = iter(["uv sync", "pytest -q", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    cli.main(["scan", "--manual", str(tmp_repo.parent)])

    # Blank means "keep what's there" -- otherwise changing one field means retyping all.
    answers = iter(["", "make test", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert cli.main(["scan", "--manual", "--reconfigure", str(tmp_repo.parent)]) == 0

    proj = next(p for p in store.load_projects() if p["name"] == tmp_repo.name)
    assert proj["setup_cmd"] == "uv sync"
    assert proj["test_cmd"] == "make test"


def test_changing_commands_invalidates_the_cached_baseline(tmp_repo, monkeypatch):
    answers = iter(["", "python check.py", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    cli.main(["scan", "--manual", str(tmp_repo.parent)])

    found = store.load_projects()
    for p in found:
        p["baseline_ok_at"] = "deadbeef"
    store.save_projects(found)

    answers = iter(["", "make test", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    cli.main(["scan", "--manual", "--reconfigure", str(tmp_repo.parent)])

    proj = next(p for p in store.load_projects() if p["name"] == tmp_repo.name)
    assert proj["baseline_ok_at"] is None


def test_a_non_git_directory_is_marked_ineligible(tmp_path, monkeypatch):
    root = tmp_path / "roots"
    (root / "plain").mkdir(parents=True)
    (root / "plain" / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.setattr("builtins.input", lambda _: "")

    cli.main(["scan", "--manual", str(root)])
    proj = next(p for p in store.load_projects() if p["name"] == "plain")
    assert proj["debug_eligible"] is False


def test_clean_removes_worktrees_left_by_a_crashed_session(tmp_repo, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "")
    cli.main(["scan", "--manual", str(tmp_repo.parent)])

    wt = worktree.create(tmp_repo, "crashed")
    assert wt.path.exists()

    assert cli.main(["clean", "--dry-run"]) == 0
    assert wt.path.exists()  # dry run must not remove anything
    assert "dpt/crashed" in capsys.readouterr().out

    assert cli.main(["clean"]) == 0
    assert not wt.path.exists()
    assert git(tmp_repo, "branch", "--list", "dpt/*").strip() == ""


def test_clean_is_quiet_when_there_is_nothing_to_do(tmp_repo, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "")
    cli.main(["scan", "--manual", str(tmp_repo.parent)])

    cli.main(["clean"])
    assert "nothing to clean" in capsys.readouterr().out


def test_stale_sessions_ignores_unrelated_worktrees(tmp_repo, tmp_path):
    git(tmp_repo, "worktree", "add", "-b", "feature/x", str(tmp_path / "feat"), "HEAD")
    dpt_wt = worktree.create(tmp_repo, "mine")
    try:
        found = worktree.stale_sessions(tmp_repo)
        assert [w.branch for w in found] == ["dpt/mine"]
    finally:
        worktree.teardown(dpt_wt)
        git(tmp_repo, "worktree", "remove", "--force", str(tmp_path / "feat"))


# --- a whole round, driven through the REPL ---


def _configure(tmp_repo, monkeypatch):
    answers = iter(["", "python check.py", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    cli.main(["scan", "--manual", str(tmp_repo.parent)])


def test_a_full_round_solved_by_fixing_the_code(tmp_repo, monkeypatch, fake_claude, capsys):
    from tests.test_debug_flow import BUGGED_CALC, response, spec

    _configure(tmp_repo, monkeypatch)
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))

    fixed = "def add(a, b):\n    return a + b\n\n\ndef total(items):\n    return sum(items)\n"
    prompts_seen = {"n": 0}

    def fake_input(prompt):
        prompts_seen["n"] += 1
        if prompts_seen["n"] == 2:
            # Stand in for the human editing the worktree between prompts.
            wt = worktree.stale_sessions(tmp_repo)[0]
            (wt.path / "src" / "calc.py").write_text(fixed)
            # The fix judge is the next `claude` call; queue its reply.
            fake_claude.script({"result": json.dumps(
                {"fixed_root_cause": True, "quality": 5, "feedback": "clean fix"})})
        return "v"

    monkeypatch.setattr("builtins.input", fake_input)
    assert cli.main(["debug"]) == 0

    out = capsys.readouterr().out
    assert "still failing" in out
    assert "SOLVED" in out
    assert "clean fix" in out
    assert worktree.stale_sessions(tmp_repo) == []

    card = next(c for c in store.load_cards() if c["id"] == "off-by-one-boundary")
    assert card["reps"] == 1


def test_force_solved_is_recorded_not_blocked(tmp_repo, monkeypatch, fake_claude, capsys):
    """docs/OPEN-QUESTIONS.md #2 -- never tell a correct engineer they're wrong."""
    from tests.test_debug_flow import BUGGED_CALC, response, spec

    _configure(tmp_repo, monkeypatch)
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))

    steps = iter(["f"])
    monkeypatch.setattr("builtins.input", lambda _: next(steps))
    assert cli.main(["debug"]) == 0

    session_id = json.loads(
        (config.SESSIONS_DIR / sorted(p.name for p in config.SESSIONS_DIR.iterdir())[-1]
         / "grade.json").read_text()
    )
    assert session_id["solved"] is True
    assert session_id["forced"] is True


def test_giving_up_reveals_the_root_cause_and_tears_down(tmp_repo, monkeypatch,
                                                        fake_claude, capsys):
    from tests.test_debug_flow import BUGGED_CALC, response, spec

    _configure(tmp_repo, monkeypatch)
    fake_claude.script(response(spec(), {"src/calc.py": BUGGED_CALC}))

    steps = iter(["h", "g"])
    monkeypatch.setattr("builtins.input", lambda _: next(steps))
    assert cli.main(["debug"]) == 0

    out = capsys.readouterr().out
    assert "the math layer" in out          # hint 1 was handed out
    assert "add() subtracts" in out          # root cause revealed on give-up
    assert worktree.stale_sessions(tmp_repo) == []


def test_a_failed_generation_reports_cleanly_and_leaves_nothing(tmp_repo, monkeypatch,
                                                                fake_claude, capsys):
    from tests.test_debug_flow import response, spec

    _configure(tmp_repo, monkeypatch)
    fake_claude.script(response(spec()))  # never introduces a bug

    assert cli.main(["debug"]) == 1
    err = capsys.readouterr().err
    assert "could not start a round" in err
    assert "Traceback" not in err
    assert worktree.stale_sessions(tmp_repo) == []


def test_a_red_baseline_is_reported_with_the_fix_to_apply(tmp_repo, monkeypatch,
                                                          fake_claude, capsys):
    answers = iter(["", "exit 1", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    cli.main(["scan", "--manual", str(tmp_repo.parent)])

    assert cli.main(["debug"]) == 1
    err = capsys.readouterr().err
    assert "does not pass in a fresh worktree" in err
    assert "dpt scan --reconfigure" in err
    assert fake_claude.calls() == []


def test_status_reports_seeded_decks(tmp_repo, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "")
    cli.main(["scan", "--manual", str(tmp_repo.parent)])
    cli.main(["status"])

    out = capsys.readouterr().out
    assert "cards" in out
    cards = store.load_cards()
    assert any(c["deck"] == "debug" for c in cards)
    assert any(c["deck"] == "review" for c in cards)
