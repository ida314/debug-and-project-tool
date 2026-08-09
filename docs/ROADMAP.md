# Roadmap

## The one-hour MVP

Target: **one complete debug round, start to finish, on a real project.** Everything
else is stubbed. Six ten-minute blocks:

| # | Block | Deliverable |
|---|---|---|
| 1 | Skeleton + store | `dpt` runs, `store.py` reads/writes JSON, XDG paths exist |
| 2 | `dpt scan` | Walks `~/Projects`, writes `projects.json`, prompts once per project for `run_cmd` / `test_cmd` |
| 3 | Worktree lifecycle | Create `dpt/<id>` worktree from a clean repo, tear it down. Test it on a throwaway repo first. |
| 4 | Injection call | `claude -p` inside the worktree returns a valid bug spec + modified tree; assert `repro_cmd` fails |
| 5 | Session loop | Brief screen, clock, `[v]erify` runs `repro_cmd`, `[h]int`, `[q]uit` |
| 6 | Grade + log + teardown | Write session dir, SM-2 the card, remove the worktree |

Non-goals for the hour: the TUI being pretty, the critic pass, audio, the QA flow,
diff-snapshot timelines. Print-to-terminal is acceptable in blocks 4–6 if Textual is
fighting you — the flow's correctness is what matters, and the screens can be bolted
on after.

**Ship-blocker to watch:** block 4 is the risky one. If `claude -p` can't reliably
produce a bug whose `repro_cmd` fails-then-passes, nothing downstream works. Budget
the slip there and cut block 6's polish, not block 4's verification.

Note the code review round is already scaffolded and runs via `dpt review`. It is
*not* part of the hour — it shares the debug flow's engine, so it only becomes
trustworthy once the debug round's generation gate is proven. Don't polish it first.

## Phase 2 — make the worktree rounds good

Debug:

- Critic pass on proposals (too easy / too hard / unfair-nondeterministic)
- Hint ladder with grade cost
- Diff-snapshot timeline + hypothesis notes keybind, replayed in a review screen
- Fix-quality judge (cause vs. symptom)
- Pre-generate the next session's bug in the background so briefing is instant
  (injection is a 30–90s call; you should never watch that spinner)

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
