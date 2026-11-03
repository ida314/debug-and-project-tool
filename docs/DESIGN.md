# Design

## Stack

| Concern | Choice | Why |
|---|---|---|
| TUI | Python + [Textual](https://textual.textualize.io/) | Fastest to a working screen; good enough widgets |
| LLM | shell out to `claude -p --output-format json` | Claude Code headless already has file read/edit tools, so the bug-injection step is one subprocess call instead of a hand-rolled agentic loop. No API key. |
| Sandboxing | `git worktree` | Never touches your working tree. Teardown is one command. |
| Storage | JSON files under `~/.local/share/dpt/` | Hackable, greppable, diffable. Migrate to SQLite if/when queries get real. |
| Audio | `sounddevice` → `.wav` on disk | Record now, transcribe later. |

## Layout

```
src/dpt/
  cli.py         entrypoint: `dpt`, `dpt scan`, `dpt debug`, `dpt review`,
                 `dpt status`, `dpt clean`
  config.py      XDG paths, constants
  store.py       JSON persistence: projects, cards, sessions
  projects.py    scan a directory tree -> project index
  onboard.py     derive a project's setup/test commands, then prove them
  llm.py         `claude -p` wrapper (text + JSON-schema'd calls)
  worktree.py    create / teardown throwaway worktrees
  srs.py         SM-2 scheduling
  prompts.py     every LLM prompt, together, because they're what you tune most
  debug_flow.py  propose -> inject -> brief -> verify -> grade -> teardown
  review_flow.py implement PR -> plant issues -> review -> match -> grade -> teardown
  qa_flow.py     propose -> record -> transcribe -> grade   (stub in MVP)
  app.py         Textual app + screens
taxonomy/
  debug_categories.yaml    bug classes, and which debugging *technique* each rewards
  review_issues.yaml       issue classes by dimension: correctness/maintain/perf/testing
  project_questions.yaml   interviewer question archetypes
```

**Flows A and C are the same engine.** Both create a worktree, have an LLM plant
defects in it, keep the answer key outside the worktree, grade against it, and tear
down. `worktree.py` and `store.py` are the shared substrate; the flows differ in what
they plant and how you're asked to respond. Flow B shares almost nothing mechanically —
see [SCOPE.md](SCOPE.md).

---

## Flow A — the debug round

```
 pick due card ──► pick project ──► claude -p: propose bug ──► critic check
                                                                    │
      teardown ◄── grade ◄── verify ◄── you debug ◄── brief ◄── inject into worktree
```

### 1. Propose

Take the due debug card (see [SPACED-REPETITION.md](SPACED-REPETITION.md)) — a *bug
class*, e.g. `off-by-one-boundary` or `stale-cache-invalidation`. Pick a project from
the index that (a) can actually be run, (b) is plausibly susceptible to that class,
and (c) you've debugged least recently — rotating projects is a feature, since the
skill being trained is *reading unfamiliar code*.

### 2. Inject — the hard contract

`claude -p` runs inside the fresh worktree and must return a **bug spec** and leave
the tree modified. The spec is the whole design; everything downstream depends on it:

```jsonc
{
  // shown to you
  "title": "Retry loop drops the final attempt under load",
  "issue_body": "…written as a real bug report from a teammate/user…",
  "repro_cmd": "python -m pytest tests/test_retry.py -q",   // MUST fail now, pass when fixed
  "expected_symptom": "…what you observe, not what's wrong…",
  "difficulty": 2,
  "estimated_minutes": 15,
  // hidden until you finish
  "root_cause": "…",
  "canonical_fix": "…",
  "files_touched": ["src/retry.py"],
  "skill_card": "off-by-one-boundary",
  "techniques_rewarded": ["breakpoint", "print-tracing"]
}
```

**The non-negotiable rule: the injector must supply a `repro_cmd` that exits non-zero
on the bugged tree and zero on the clean tree, and we verify both before handing the
session to you.** If it can't, we reject the proposal and regenerate. This one rule
turns "did the user solve it?" from an LLM judgment call into `subprocess.run(...).returncode`.

When a project has no test harness, the injector's job includes *writing* a small
repro script. That script lives in the worktree (you're allowed to read it — a real
bug report comes with repro steps). The `root_cause` and `canonical_fix` live
**outside** the worktree, in the session directory, so you can't grep your way to the
answer.

A second `claude -p` pass acts as **critic**: is this findable in the estimated time?
Is it visible from a five-second `git status`-level glance (too easy)? Does it depend
on nondeterminism the harness can't reproduce (unfair)? Reject → regenerate.

### 3. Brief

The injection commit lands as the tip of branch `dpt/<session-id>` with a mundane
message ("refactor: simplify retry handling"). You get the issue body, the repro
command, and a running clock. House rules are honor-system: **no `git log -p`, no
diffing against the base branch.** Peeking only wastes your own hour — and the ladder
of hints below exists so you don't need to.

Hints are on a ladder, each one costing grade points, mirroring an interviewer's
escalating nudges:

1. "Which subsystem would you look at first?" (directional)
2. Narrow to a file
3. Narrow to a function
4. State the root cause

### 4. Verify

`repro_cmd` passes → candidate solve. Then a cheap `claude -p` judge reads your diff
and answers two questions: did you fix the *cause* or paper over the *symptom*, and
did you break anything else? Fixing it differently from `canonical_fix` is fine, and
sometimes better — the judge is told that explicitly.

### 5. Grade → schedule → teardown

Signals: solved (y/n), wall-clock vs `estimated_minutes`, hints used, fix quality from
the judge. These map to an SM-2 quality score for the card. The session log, the
injection patch, your final diff, and the timeline are written to
`~/.local/share/dpt/sessions/<id>/`, then `git worktree remove --force` and the branch
is deleted.

### 6. Post-mortem (the bridge between the two flows)

Right after the solve, the tool asks — out loud, recorded — *"walk me through how you
found it. What was your first hypothesis, and why did you check there?"* This is
exactly what a real interviewer does after a debug round, and it means the debug flow
also exercises the verbal-explanation muscle.

### Session recording

Cheap and good enough: snapshot `git diff` every 15s and on each hint, timestamp
everything, and give you a keybind to drop a one-line hypothesis note. Replayed at
review time as a timeline, so you can see *"I spent 11 minutes in the wrong file."*
That, not the pass/fail, is the useful artifact.

---

## Flow C — the code review round

```
 pick due issue cards ──► claude -p: implement a real feature, plant the issues
                                              │
                                    tests must still pass  ──(fail)──► regenerate
                                              │
   grade ◄── match vs answer key ◄── you review the diff ◄── PR opened
```

### The inverted gate

The debug flow's gate is *the repro command must fail*. This flow's is the mirror image:

> **The test suite must still pass on the PR branch.**

If CI would catch the problem, human review isn't what's being trained. Enforcing this
mechanically forces the LLM to plant the kind of issue only a reader catches — an
unbounded cache, a test that asserts on its own mock, a breaking change to a public
default. That's precisely the class of problem code review exists for, and you get it
for free from one `subprocess.run`.

### Generating the PR

`claude -p` picks a small, plausible feature by reading the repo, implements it, and
plants the due issues. The constraints that make it an exercise rather than a mess:

- **Mostly good code.** The planted issues are a minority of the diff. A uniformly bad
  PR teaches nothing except that the tool is generating slop.
- **At least one decoy** — something that looks wrong to a hasty reviewer and is
  actually fine. This is how precision gets measured.
- **Findable from the diff alone.** You're reviewing, not running.
- **No tells.** No `# TODO`, no suspicious naming, written the way someone who believed
  it was correct would write it.

The answer key (`planted_issues`, `decoys`, `correct_verdict`) is written to the session
dir, never into the worktree.

### Reviewing

You get the worktree path, the PR title and body in the contributor's voice, a diffstat,
and a `review.md` draft in the session dir:

```
verdict: request-changes

## Comments
src/cache.py:88 - this dict grows without bound, one entry per user, never evicted
tests/test_cache.py:14 - this asserts on the mock, it'd pass if get() returned None
```

Read the diff however you like — `git diff`, your editor's diff view, `delta`. The
review draft lives in the session dir, not the worktree, so teardown never eats your
work. A line-anchored diff pane in the TUI is phase 2.

### Grading — precision matters as much as recall

Comments get matched against the answer key by substance, not wording. The interesting
part is what happens to comments that match *nothing*, because they split three ways:

| | |
|---|---|
| **bonus finding** | A real issue we didn't plant. Your projects have real problems. Credit it. |
| **false positive** | You objected to a decoy. This is a genuine review error. |
| **noise** | A vague or stylistic nit with no substance. Neither credited nor punished. |

Scoring is `recall` (caught / planted) **and** `precision` (real findings / total
comments). Recall alone would train you to flag everything, which is the single most
common failure mode of a junior reviewer and the thing this round should be beating out
of you.

The **verdict** is graded separately and harshly: approving a PR that contains a blocker
is a failure regardless of how many minor things you caught, because in the real world
that PR just shipped.

### Scheduling

One PR moves several cards at once — each planted issue scores *its own* card
independently (caught with correct severity → 5, caught but severity misjudged → 3,
missed → 1). This is the clearest demonstration of why cards are skills rather than
problems: a single exercise can push `n-plus-one` out to three weeks while pulling
`test-tests-the-mock` back to tomorrow.

---

## Flow B — the project round

Mirror image of Flow A in one important way: there, **project variety is the point**
(train unfamiliarity); here, **project fixity is the point** (you must know *your* code
cold). Cards are `(project × question-topic)` pairs.

```
 pick due card ──► claude -p reads the project ──► question
                                                      │
   grade + follow-up ◄── transcribe ◄── record you answering out loud
```

Question archetypes live in `taxonomy/project_questions.yaml`: design tradeoffs,
"why not X instead", failure modes, scaling, testing strategy, security, what you'd
redo, hardest bug you hit. The proposer reads the actual repo so the question is
concrete ("you used a `Mutex<HashMap>` here — why not sharding?") rather than generic.

Grading uses a rubric with fixed dimensions — *correctness, specificity, tradeoff
awareness, structure, conciseness* — each scored 1–5 with a one-line justification and
one concrete improvement. Rubric dimensions are stable across questions so you can
watch a dimension trend over weeks. Then one follow-up question, because interviewers
always push once more.

---

## Data model

```
~/.local/share/dpt/
  projects.json          index: path, lang, LOC, has_tests, setup_cmd, run_cmd,
                         test_cmd, test_seconds, baseline_ok_at, summary
  cards.json             SRS cards (both decks) with SM-2 state
  sessions/<id>/
    spec.json            debug: full bug spec incl. hidden fields
    injection.patch      debug: what we broke
    solution.patch       debug: what you did
    pr.json              review: PR body + answer key (planted issues, decoys)
    pr.diff              review: the diff you were given
    review.md            review: your draft, kept out of the worktree on purpose
    timeline.jsonl       diff snapshots, hints, notes, events
    grade.json
    postmortem.wav
```

`run_cmd` / `test_cmd` cannot be reliably *inferred*, but they don't have to be: the
clean-tree baseline check is already an oracle for them. `onboard.py` derives candidates
from the repo (CI workflows, lockfiles, task runners, docs; `claude -p` read-only when
those come up empty), shows them for approval, and keeps the first pair that goes green
in a throwaway worktree. A verified derivation is worth strictly more than a typed
command, which nothing checks until the first session dies at the generation gate.

Two things hold that together. Everything offered must be headed by a known build tool,
so the confirmation screen is a review of plausible build commands rather than of
arbitrary shell a regex pulled out of a README; and nothing derived executes before the
user has seen it. Ranking is by how much a command looks like a real suite, because the
expensive failure is adopting a *green* non-suite — a CI lint step passes, so
verification can't catch it, and every session afterwards is graded against a linter.

A project with no command that goes green is marked ineligible for the debug flow rather
than guessed at.

`setup_cmd` is the third question, and the one the original design missed: a worktree is
a checkout of *tracked files only*, so a project's own test command generally cannot run
there until its dependencies are installed. `test_seconds` and `baseline_ok_at` are
measured rather than asked — the first because injection cost turns out to scale with
suite runtime, the second so a slow suite is verified green once per commit instead of
once per session.
