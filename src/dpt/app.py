"""Textual shell.

Intentionally thin for the MVP -- a home screen over the real work in `debug_flow`.
The session screens (brief + clock + hint ladder) come in phase 2; `dpt debug` runs
the same sequence in the terminal until then.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from . import projects, srs, store


class DptApp(App):
    TITLE = "dpt"
    SUB_TITLE = "debug & project interview trainer"

    BINDINGS = [
        ("d", "debug_round", "Debug round"),
        ("c", "review_round", "Code review"),
        ("p", "project_round", "Project round"),
        ("r", "refresh", "Refresh"),
        ("q", "quit", "Quit"),
    ]

    CSS = """
    Screen { layout: vertical; }
    #summary { padding: 1 2; }
    DataTable { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Static(id="summary")
            yield DataTable(id="due")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#due", DataTable)
        table.add_columns("due", "deck", "lvl", "card")
        self.action_refresh()

    def action_refresh(self) -> None:
        cards = store.load_cards()
        due = srs.due_cards(cards)
        eligible = projects.eligible_for_debug(store.load_projects())

        by_deck = {d: len([c for c in due if c["deck"] == d])
                   for d in ("debug", "review", "project")}
        self.query_one("#summary", Static).update(
            f"{len(due)} due of {len(cards)} cards   "
            f"({by_deck['debug']} debug, {by_deck['review']} review, "
            f"{by_deck['project']} project)   "
            f"{len(eligible)} eligible projects\n"
            f"[d] debug round   [c] code review   [p] project round"
        )
        table = self.query_one("#due", DataTable)
        table.clear()
        for card in due[:50]:
            table.add_row(card["due"], card["deck"], f"L{card['level']}", card["id"])

    def action_debug_round(self) -> None:
        # Phase 2: push a BriefScreen, run injection in a worker (it's a 30-90s call,
        # so it cannot block the event loop), then the timed session screen.
        self.notify("Debug round: run `dpt debug` for now (phase 2 wires this up)")

    def action_review_round(self) -> None:
        # Phase 2: a side-by-side diff pane with line-anchored comments. Until then
        # the CLI hands you the worktree and a review draft.
        self.notify("Code review: run `dpt review` for now (phase 2 wires this up)")

    def action_project_round(self) -> None:
        self.notify("Project round: phase 3 -- see docs/ROADMAP.md")


if __name__ == "__main__":
    DptApp().run()
