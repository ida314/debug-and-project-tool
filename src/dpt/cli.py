"""Entrypoint. `dpt` launches the TUI; the subcommands exist so each leg of the flow
can be exercised without fighting the UI."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import (
    config, debug_flow, onboard, projects, review_flow, srs, store, worktree,
)


def _ask(label: str, current: str | None) -> str | None:
    """Prompt with the existing value as the default, so `--reconfigure` on one field
    doesn't mean retyping the other two."""
    suffix = f" [{current}]" if current else " (blank if none)"
    answer = input(f"{label}{suffix}: ").strip()
    return answer or current


def _manual_configure(proj: dict) -> None:
    """The typed path. Still here because a project can be genuinely unguessable, and
    because a command you typed should never be silently replaced by a derived one."""
    print("  (these run in a throwaway worktree, which has no .venv/node_modules)")
    proj["setup_cmd"] = _ask("  setup command for a bare checkout, e.g. `uv sync`",
                             proj.get("setup_cmd"))
    proj["test_cmd"] = _ask("  test command", proj.get("test_cmd"))
    proj["run_cmd"] = _ask("  run command", proj.get("run_cmd"))
    proj["debug_eligible"] = bool(proj["test_cmd"] or proj["run_cmd"])
    proj["configured"] = True
    proj["derived_from"] = None
    proj["baseline_ok_at"] = None  # commands changed; re-verify on next session


def _show_candidates(cands: list[onboard.Candidate], heading: str) -> None:
    """The confirmation screen.

    Load-bearing, not decoration: these commands came out of a CI file, a README, or a
    model, and this is the last point before they execute. Nothing above this line has
    run anything.
    """
    print(f"\n  {len(cands)} candidate command set(s) {heading}. Nothing has run yet.\n")
    for i, cand in enumerate(cands, 1):
        print(f"    {i}  {cand.source}")
        print(f"         setup  {cand.setup_cmd or '(none needed)'}")
        print(f"         test   {cand.test_cmd}")
        if cand.run_cmd:
            print(f"         run    {cand.run_cmd}  (not verified)")
    print(f"\n  They will run in a throwaway worktree under {config.WORKTREE_DIR},")
    print("  never in your checkout.")


def _confirm(cands: list[onboard.Candidate]) -> list[onboard.Candidate] | None:
    """None means the user declined; otherwise the subset to actually verify."""
    try:
        choice = input(
            f"  [enter] verify in order  [1-{len(cands)}] verify just that one  "
            "[s]kip > "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if choice in ("s", "n", "q"):
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(cands):
        return [cands[int(choice) - 1]]
    return cands


def _verify_and_apply(proj: dict, cands: list[onboard.Candidate], heading: str) -> bool:
    _show_candidates(cands, heading)
    chosen = _confirm(cands)
    if not chosen:
        return False
    print()
    try:
        result = onboard.verify(Path(proj["path"]), chosen, on_event=print)
    except worktree.WorktreeError as e:
        print(f"  cannot verify: {e}")
        return False

    if result.winner:
        onboard.apply(proj, result)
        print(f"\n  verified in {result.test_seconds}s -- baseline is green.")
        print(f"    setup  {result.winner.setup_cmd or '(none)'}")
        print(f"    test   {result.winner.test_cmd}")
        return True

    print("\n  no candidate produced a green baseline:")
    for i, att in enumerate(result.attempts, 1):
        cmd = att.candidate.setup_cmd if att.stage == "setup" else att.candidate.test_cmd
        print(f"    {i}  {att.stage} failed: {cmd}")
        detail = att.detail.strip().splitlines()
        if detail:
            print(f"         {detail[-1][:120]}")
    return False


def _auto_configure(proj: dict) -> bool:
    """Derive, confirm, verify. True if the project ended up with proven commands.

    The LLM is asked only once the free sources have been exhausted -- it costs seconds
    and a subprocess, and on a normal repo the CI file already has the answer.
    """
    path = Path(proj["path"])
    cands = onboard.derive(path)
    if cands and _verify_and_apply(proj, cands, "derived from the repo"):
        return True

    if not cands:
        print("  nothing conclusive in CI, lockfiles, task runners or docs")
    try:
        if input("  ask claude to work it out from the code? [Y/n] ").strip().lower() == "n":
            return False
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    proposed = onboard.propose(path, proj.get("lang"))
    if not proposed:
        print("  claude had no usable suggestion")
        return False
    return _verify_and_apply(proj, proposed, "proposed by claude")


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 1

    config.ensure_dirs()
    found = projects.merge(store.load_projects(), projects.scan(root))

    for proj in found:
        if proj.get("configured") and not args.reconfigure:
            continue
        print(f"\n{proj['name']}  [{proj['lang'] or 'unknown'}, ~{proj['loc']} LOC]")
        if not proj["is_git"]:
            print("  not a git repo -- debug flow unavailable")
            proj["debug_eligible"] = False
            proj["configured"] = True
            continue
        # A guess is only as good as its verifier, and there is one: the commands are
        # derived from the repo, shown for approval, then proved green in a throwaway
        # worktree before they're kept. Typing them by hand is the fallback, not the
        # default -- an unverified typed command fails later, at the generation gate.
        if not args.manual and _auto_configure(proj):
            continue
        _manual_configure(proj)

    store.save_projects(found)

    cards = review_flow.seed_cards(debug_flow.seed_cards(store.load_cards()))
    store.save_cards(cards)

    eligible = projects.eligible_for_debug(found)
    print(f"\nindexed {len(found)} projects, {len(eligible)} debug-eligible")
    print(f"{len(srs.due_cards(cards))} cards due")
    return 0


def cmd_debug(args: argparse.Namespace) -> int:
    """One debug round, headless. The TUI wraps this same sequence."""
    all_projects = store.load_projects()
    cards = debug_flow.seed_cards(store.load_cards())

    due = srs.due_cards(cards, deck="debug")
    if not due:
        print("nothing due in the debug deck")
        return 0
    card = due[0]

    project = projects.pick_project(all_projects)
    if project is None:
        print("no debug-eligible projects -- run `dpt scan` first", file=sys.stderr)
        return 1

    print(f"card: {card['id']} (level {card['level']})")
    print(f"project: {project['name']}")
    try:
        session = debug_flow.start(project, card)
    except (debug_flow.GateError, worktree.WorktreeError) as e:
        # Generation is the flakiest step in the tool; say what went wrong rather than
        # dumping a traceback, and save the project index so a passed baseline sticks.
        print(f"\ncould not start a round:\n{e}", file=sys.stderr)
        store.save_projects(all_projects)
        return 1

    spec = session.public_spec()
    print(f"\n  worktree: {session.wt.path}")
    print(f"\n  {spec['title']}\n")
    print(f"  {spec['issue_body']}\n")
    print(f"  repro: {spec['repro_cmd']}\n")

    solved = False
    forced = False
    try:
        while True:
            status = f"{session.elapsed_minutes:.0f}m  {session.hints_used} hints"
            choice = input(
                f"[{status}]  [v]erify  [h]int  [n]ote  [f]orce solved  [g]ive up > "
            ).strip().lower()
            if choice == "v":
                solved, output = debug_flow.verify(session)
                print("SOLVED" if solved else f"still failing:\n{output[-1000:]}")
                if solved:
                    break
            elif choice == "h":
                print(session.next_hint() or "no hints left")
            elif choice == "n":
                debug_flow.snapshot(session, input("  note: ").strip())
            elif choice == "f":
                # docs/OPEN-QUESTIONS.md #2: a correct fix the repro command doesn't
                # recognise must never be scored as a failure. Flagged, not blocked.
                print("  recorded as solved and flagged for review")
                solved = forced = True
                break
            elif choice == "g":
                break
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        # A crash while grading must still take the worktree with it.
        grade = debug_flow.finish(session, solved, cards, forced=forced)
        store.save_projects(all_projects)

    print(f"\nquality {grade['quality']}/5 in {grade['minutes']}min "
          f"({grade['hints_used']} hints) -- next due {grade['next_due']}")
    if grade["judgment"].get("feedback"):
        print(f"\n{grade['judgment']['feedback']}")
    if not solved:
        print(f"\nroot cause was: {grade['root_cause']}")
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    """Remove worktrees and branches left behind by a crashed session.

    Generation is expected to be flaky early on, and litter accumulates in repos the
    user actually works in, where there is otherwise no obvious way to find it.

    A live session's worktree is indistinguishable from litter, so this would happily
    delete a round you're in the middle of. Hence `--dry-run`, and the warning below.
    """
    print("(don't run this while a session is open -- it can't tell one from litter)")
    found = 0
    for proj in store.load_projects():
        repo = Path(proj["path"])
        if not proj.get("is_git") or not repo.exists():
            continue
        stale = worktree.stale_sessions(repo)
        for wt in stale:
            found += 1
            print(f"  {proj['name']}: {wt.branch}")
            if not args.dry_run:
                worktree.teardown(wt)
    if not found:
        print("nothing to clean")
    elif args.dry_run:
        print(f"\n{found} to remove -- re-run without --dry-run")
    else:
        print(f"\nremoved {found}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """One code review round, headless."""
    all_projects = store.load_projects()
    cards = review_flow.seed_cards(store.load_cards())

    picks = review_flow.pick_issues(cards)
    if not picks:
        print("nothing due in the review deck")
        return 0

    project = projects.pick_project(all_projects)
    if project is None:
        print("no eligible projects -- run `dpt scan` first", file=sys.stderr)
        return 1

    print(f"project: {project['name']}")
    print(f"planting: {', '.join(p['issue']['id'] for p in picks)}")
    print("writing the PR -- this takes a couple of minutes...")
    try:
        session = review_flow.start(project, picks)
    except (debug_flow.GateError, worktree.WorktreeError) as e:
        print(f"\ncould not start a round:\n{e}", file=sys.stderr)
        store.save_projects(all_projects)
        return 1

    print(f"\n  {session.pr['pr_title']}\n")
    print(f"  {session.pr['pr_body']}\n")
    print(f"  worktree: {session.wt.path}")
    print(f"  diff:     git -C {session.wt.path} diff {session.base_ref}..HEAD\n")
    print(session.diff_stat())

    review_file = session.write_review_template()
    print(f"  write your review in: {review_file}")
    editor = os.environ.get("EDITOR")
    if editor and input("  open in $EDITOR? [Y/n] ").strip().lower() != "n":
        subprocess.run([editor, str(review_file)])

    input("\n  press enter when your review is submitted > ")
    print("  grading...")
    grade = review_flow.grade(session, cards)

    res = grade["result"]
    print(f"\n  caught {len(res.get('caught', []))}/{len(session.pr['planted_issues'])}"
          f"   recall {grade['recall']}  precision {grade['precision']}")
    print(f"  verdict: {grade['verdict_given']} "
          f"(expected {grade['verdict_expected']}) -- {res.get('verdict_note', '')}")
    for missed_id in res.get("missed", []):
        issue = next((i for i in session.pr["planted_issues"] if i["id"] == missed_id), {})
        print(f"  MISSED  {issue.get('file')}:{issue.get('line')}  "
              f"[{issue.get('severity')}] {issue.get('description')}")
    for fp in res.get("false_positives", []):
        print(f"  FALSE+  {fp.get('comment')} -- {fp.get('why_wrong')}")
    for bonus in res.get("bonus_findings", []):
        print(f"  BONUS   {bonus.get('comment')}")
    print(f"\n{res.get('feedback', '')}")

    store.save_projects(all_projects)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cards = store.load_cards()
    due = srs.due_cards(cards)
    print(f"{len(cards)} cards, {len(due)} due")
    for card in due[:20]:
        print(f"  {card['due']}  L{card['level']}  {card['deck']:8} {card['id']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dpt", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")

    p_scan = sub.add_parser("scan", help="index a directory of projects")
    p_scan.add_argument("root", nargs="?", default="~/code/projects")
    p_scan.add_argument("--reconfigure", action="store_true",
                        help="re-ask for commands on projects already configured")
    p_scan.add_argument("--manual", action="store_true",
                        help="type the commands instead of deriving and verifying them")
    p_scan.set_defaults(func=cmd_scan)

    sub.add_parser("debug", help="run one debug round").set_defaults(func=cmd_debug)
    sub.add_parser("review", help="run one code review round").set_defaults(func=cmd_review)
    sub.add_parser("status", help="show due cards").set_defaults(func=cmd_status)

    p_clean = sub.add_parser("clean", help="remove worktrees left by crashed sessions")
    p_clean.add_argument("--dry-run", action="store_true", help="list without removing")
    p_clean.set_defaults(func=cmd_clean)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        from .app import DptApp
        DptApp().run()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
