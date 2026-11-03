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

**The debug round works end to end.** `dpt scan` then `dpt debug` injects a real bug
into a throwaway worktree of one of your projects, briefs you, times you, verifies the
fix and grades it. The code review round shares the same engine and runs, but hasn't
been validated against a real project yet. The project round is still a stub.

See [docs/ROADMAP.md](docs/ROADMAP.md) for what's next.

## Quickstart

```bash
uv sync
uv run dpt scan ~/Projects     # build the project index (one time, plus on demand)
uv run dpt debug               # one debug round, headless

uv run dpt                     # launch the TUI (home screen only for now)
uv run dpt review              # one code review round, headless -- unvalidated
uv run dpt status              # what's due
uv run dpt clean               # remove worktrees left behind by a crashed session
```

`dpt` shells out to the `claude` CLI in headless mode for the agentic steps, so you
need Claude Code installed and authenticated. No API key required.

### Configuring a project

Each project needs two commands, and `dpt scan` works them out for you:

| | |
|---|---|
| **setup command** | Gets a *bare checkout* to a runnable state — `uv sync`, `npm ci`, `make build`. A `git worktree` contains tracked files and nothing else: no virtualenv, no `node_modules`, no editable install. |
| **test command** | Must pass on a clean checkout. Checked before every session (cached per commit) so a bug is never injected into a tree that was already red. |
| **run command** | How to start the thing, for projects with no test harness. Derived best-effort, never verified — a long-running process has no exit-zero success condition. |

These can't be reliably *inferred*, but they can be derived and then proved. `dpt scan`
reads them out of the repo — CI workflows first, since a GitHub Actions job is a recipe
someone already proved works with no environment, then lockfiles, task runners and
contributor docs — and shows you what it found:

```
2 candidate command set(s) derived from the repo. Nothing has run yet.

  1  .github/workflows/ci.yml (test)
       setup  uv sync --all-extras
       test   uv run pytest -q
  2  uv.lock
       setup  uv sync --all-extras
       test   uv run pytest

They will run in a throwaway worktree under ~/.cache/dpt/worktrees,
never in your checkout.
[enter] verify in order  [1-2] verify just that one  [s]kip >
```

Nothing executes before that prompt, and only commands headed by a known build tool
(`uv`, `npm`, `cargo`, `make`, …) are ever offered — a `curl … | sh` lifted out of a
README is discarded unread. On approval each candidate is run in a throwaway worktree
and the first one whose suite goes green is kept, along with the commit it passed on,
so onboarding pays the first session's baseline check instead of adding a second one.

If nothing in the repo works, `dpt scan` offers to have `claude` read the code and
propose commands (read-only, and filtered by the same allowlist). If that fails too, it
falls back to asking you. `--manual` skips straight to typing them; `--reconfigure`
re-does an already-configured project. A project with no command that goes green is
marked debug-ineligible rather than guessed at — a bug that can't be reproduced is a bug
that can't be graded.

## Safety

Your working tree is never touched. Every session runs in a `git worktree` under
`~/.cache/dpt/`, on its own `dpt/<session-id>` branch, removed on teardown even when
the session crashes — `dpt clean` sweeps up anything a hard kill left behind. It can't
tell a live session's worktree from litter, so don't run it mid-round; `dpt clean
--dry-run` lists what it would remove.

The bug-injection step runs `claude` with `--permission-mode bypassPermissions`, scoped
to that throwaway worktree, because the injector has to run your project's test command
to confirm the bug it planted actually reproduces.

## Development

```bash
uv run pytest        # the whole suite, ~5s, no LLM and no network
```

`tests/conftest.py` provides a stub `claude` binary that speaks the same JSON handshake
as the real CLI and can edit files in its working directory, so a full round — inject,
gate, verify, grade, teardown — runs in milliseconds against a throwaway git repo. Use
it rather than a live call when changing the flows; a live injection costs minutes.

`tests/test_debug_flow.py` is the one to keep honest. Both halves of the generation gate
have tests that fail if either check is removed.

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
