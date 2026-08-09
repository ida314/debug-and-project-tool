# dpt — debug & project interview trainer

A TUI that drills the interview formats that actually decide entry-to-mid-level
software engineering loops, using **your own projects** as the material:

1. **Debug round** — you're dropped into an unfamiliar codebase with a bug report and
   told to find and fix it. `dpt` injects a real, reproducible bug into one of your
   projects (in a throwaway git worktree), hands you an issue writeup, times you,
   verifies your fix, grades it, and tears the worktree down.
2. **Code review round** — an LLM opens a plausible PR against one of your projects,
   with issues planted in it across correctness, maintainability, performance, and
   testing. You review it as the maintainer. Graded on recall *and* precision, because
   a reviewer who flags everything is not a good reviewer.
3. **Project round** — the interviewer grills you on a project you built: why that
   database, what breaks at 10x traffic, what you'd do differently. `dpt` proposes a
   question, records you answering it *out loud*, and grades the answer against a
   rubric.

All three feed a spaced-repetition scheduler so the weak spots come back.

## Why

There's no shortage of algorithm practice tools. There's essentially nothing for
"read code you've never seen and find the bug under time pressure" or "defend your
own architectural decisions to a skeptic" — despite those being the rounds most
candidates actually fail.

The three rounds are one skill viewed from three angles: **reading code you didn't
write and forming a correct opinion about it.** Debug gives you a symptom and asks for
the cause. Review gives you a diff and asks what's wrong with it before it ships.
Project inverts it — the code *is* yours, and you have to defend it.

The insight this tool leans on: **you already have a folder full of projects.** That
folder is a free, infinite, personally-relevant question bank for both rounds.

## Status

Pre-MVP scaffold. See [docs/ROADMAP.md](docs/ROADMAP.md) for what's built and what's next.

## Quickstart

```bash
uv sync
uv run dpt scan ~/Projects     # build the project index (one time, plus on demand)
uv run dpt                     # launch the TUI

uv run dpt debug               # one debug round, headless
uv run dpt review              # one code review round, headless
uv run dpt status              # what's due
```

`dpt` shells out to the `claude` CLI in headless mode for the agentic steps, so you
need Claude Code installed and authenticated. No API key required.

## Docs

| | |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | Architecture, the three flows, data model |
| [docs/SPACED-REPETITION.md](docs/SPACED-REPETITION.md) | How SRS works when there's no fixed problem list |
| [docs/SCOPE.md](docs/SCOPE.md) | Why three flows and not one — the case for each |
| [docs/ROADMAP.md](docs/ROADMAP.md) | The one-hour MVP plan, then phases |
| [docs/OPEN-QUESTIONS.md](docs/OPEN-QUESTIONS.md) | Known risks and unresolved decisions |

## Anti-goals

- Not an autonomous agent that fixes your code. It *breaks* your code, on purpose, in a sandbox.
- Not a general codebase Q&A tool.
- Not a LeetCode trainer.
- No accounts, no cloud sync, no web UI. Local files, local git, local TUI.
