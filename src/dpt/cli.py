"""Entrypoint. `dpt` launches the TUI; the subcommands exist so each leg of the flow
can be exercised without fighting the UI."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import config, debug_flow, projects, review_flow, srs, store


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 1

    config.ensure_dirs()
    found = projects.merge(store.load_projects(), projects.scan(root))

    for proj in found:
        if proj["run_cmd"] is not None or proj["test_cmd"] is not None:
            continue  # already configured
        print(f"\n{proj['name']}  [{proj['lang'] or 'unknown'}, ~{proj['loc']} LOC]")
        if not proj["is_git"]:
            print("  not a git repo -- debug flow unavailable")
            proj["debug_eligible"] = False
            continue
        # Can't be inferred, and guessing produces ungradeable sessions.
        test = input("  test command (blank if none): ").strip()
        run = input("  run command (blank if none): ").strip()
        proj["test_cmd"] = test or None
        proj["run_cmd"] = run or None
        proj["debug_eligible"] = bool(test or run)

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
    print("injecting bug -- this takes a minute...")
    session = debug_flow.start(project, card)

    spec = session.public_spec()
    print(f"\n  worktree: {session.wt.path}")
    print(f"\n  {spec['title']}\n")
    print(f"  {spec['issue_body']}\n")
    print(f"  repro: {spec['repro_cmd']}\n")

    solved = False
    try:
        while True:
            choice = input("[v]erify  [h]int  [n]ote  [g]ive up > ").strip().lower()
            if choice == "v":
                solved, output = debug_flow.verify(session)
                print("SOLVED" if solved else f"still failing:\n{output[-1000:]}")
                if solved:
                    break
            elif choice == "h":
                print(session.next_hint() or "no hints left")
            elif choice == "n":
                debug_flow.snapshot(session, input("  note: ").strip())
            elif choice == "g":
                break
    except (KeyboardInterrupt, EOFError):
        print()

    grade = debug_flow.finish(session, solved, cards)
    print(f"\nquality {grade['quality']}/5 in {grade['minutes']}min "
          f"({grade['hints_used']} hints) -- next due {grade['next_due']}")
    if grade["judgment"].get("feedback"):
        print(f"\n{grade['judgment']['feedback']}")
    if not solved:
        print(f"\nroot cause was: {grade['root_cause']}")
    store.save_projects(all_projects)
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
    session = review_flow.start(project, picks)

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
    p_scan.add_argument("root", nargs="?", default="~/Projects")
    p_scan.set_defaults(func=cmd_scan)

    sub.add_parser("debug", help="run one debug round").set_defaults(func=cmd_debug)
    sub.add_parser("review", help="run one code review round").set_defaults(func=cmd_review)
    sub.add_parser("status", help="show due cards").set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        from .app import DptApp
        DptApp().run()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
