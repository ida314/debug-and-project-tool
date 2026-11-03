# Roadmap

## The MVP — done

Target was **one complete debug round, start to finish, on a real project.** That
works: `dpt scan` then `dpt debug` injects a bug, briefs, times, verifies, grades and
tears down.

| # | Block | Status |
|---|-------|--------|
| 1 | Skeleton + store | done |
| 2 | `dpt scan` | done — derives `setup_cmd`/`test_cmd` and proves them (`onboard.py`) |
| 3 | Worktree lifecycle | done, plus `dpt clean` for litter from crashed sessions |
| 4 | Injection call | done — **both** halves of the gate enforced |
| 5 | Session loop | done, with a `[f]orce solved` override |
| 6 | Grade + log + teardown | done, teardown guaranteed on any exception |

Four things the first real runs turned up that the design hadn't accounted for:

- **A worktree has no environment.** `git worktree add` checks out tracked files and
  nothing else — no virtualenv, no `node_modules`, no editable install — so a project's
  own test command fails there for reasons unrelated to any bug. Hence `setup_cmd`, and
  a baseline check that the suite is green *before* a bug is injected.
- **Only half the gate was implemented.** The code checked that `repro_cmd` fails on
  the bugged tree and never that it passes on a clean one, so any command that failed
  for an unrelated reason was accepted as a successful injection. Both directions are
  now enforced, and `tests/test_debug_flow.py` fails if either is removed.
- **dpt's own virtualenv leaked into the projects it trains on.** `uv run dpt` exports
  `VIRTUAL_ENV`; inherited by a subprocess, it made `uv pip install` install the trained
  project's dependencies into dpt's venv while the worktree's stayed empty. Project
  commands now run under a scrubbed environment (`worktree.project_env`).
- **Injection cost scales with the test suite, not the repo.** 300s was enough for a
  project with a sub-second suite and not enough for one with a 70-second suite, where
  all three attempts timed out. `LLM_TIMEOUT_S` is now 900s, the baseline result is
  cached per commit, and the injector is told what the suite costs. See
  [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) #8.

The code review round shares this engine and inherits all four fixes, but has not been
run against a real project. Don't polish it before validating it.

## Phase 2 — make the worktree rounds good

Debug:

- Critic pass on proposals (too easy / too hard / unfair-nondeterministic). The
  `CRITIC` prompt exists in `prompts.py` and is still called from nowhere.
- Hint ladder with grade cost
- Diff-snapshot timeline + hypothesis notes keybind, replayed in a review screen
- ~~Fix-quality judge (cause vs. symptom)~~ — already wired into `debug_flow.finish`
- Pre-generate the next session's bug in the background so briefing is instant.
  Now the highest-value item here: injection measured at ~1 minute on a small project
  and several minutes on a large one, and it is dead time either way.

Review (shares all the above plumbing):

- Line-anchored diff pane in the TUI, comment with `c` on the focused line — replaces
  the `review.md` draft file
- Show the answer key inline on the diff after grading; seeing the line you scrolled
  past is the whole lesson
- Calibrate the decoy count — one is a guess, and precision scoring is only as good as
  the decoys are convincing
- Per-dimension trends (are you blind to performance issues specifically?)

## Phase 3 — the project round

- `taxonomy/project_questions.yaml` → proposer reads the repo, asks something concrete
- Push-to-talk recording via `sounddevice`
- Local transcription (`faster-whisper`, small model)
- Rubric grader + one follow-up question
- Recorded post-mortem on the debug round, using the same machinery

## Phase 4 — the trainer, not just the drill

- Mixed-queue sessions packed to a time budget
- Per-dimension rubric trends over time; per-technique debug efficiency; review
  precision-vs-recall trend (the interesting shape is recall rising while precision
  holds — recall rising *because* precision fell means you've learned to flag everything)
- "You've never cleared a concurrency bug unaided" → targeted week
- Export a session as a shareable writeup (the artifact is genuinely good interview prep in itself)

## Later / maybe

- Import someone else's repo as an unfamiliar-codebase source when your own projects
  get too familiar
- Difficulty calibration from real timing data instead of the LLM's `estimated_minutes` guess
- SQLite once "due cards" queries outgrow a JSON scan
