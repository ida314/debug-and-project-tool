# Open questions & risks

Ordered by how likely they are to sink the project.

### 1. Can the injector reliably produce a *good* bug? — *early answer: yes, tentatively*

Failure modes: the bug breaks the build immediately (trivially findable), or it's
buried so deep it's undiscoverable, or it's not actually reproducible. Mitigations
planned: the fails-then-passes `repro_cmd` gate (mechanical, cheap, catches
"not reproducible"), plus a critic pass (catches too-easy/too-hard).

**First real data (2026-08-12, n=5 rounds).** Every injection that was given enough time
cleared the gate — 5 for 5 across four bug classes and two projects. The single round
that failed did so for infrastructure reasons rather than bug quality: the call hit a
300-second timeout on the larger project, three attempts running, and succeeded once the
timeout was raised (see #8). **No generated bug has yet been rejected as
unreproducible**, so the free-form approach is not missing anything like 1 in 3, and the
parameterized-template fallback below stays unbuilt.

Caveat on the sample: 5 rounds across 4 of the 12 bug classes is thin, and three of the
four drawn are ones an LLM is likely to be good at. Treat this as "no evidence of the
feared failure mode" rather than a measured hit rate.

Quality of what came out, judged by hand:

- `off-by-one-boundary` on job-tracker: a single `>` → `>=` in a snooze-window check,
  where `until` is exclusive by construction. Symptom-only issue report, four-rung hint
  ladder correctly graded subsystem → file → function → cause, and hint 3 pointed at a
  breakpoint — which is the technique that bug class is tagged to reward.
- `silent-exception-swallow` on p99: a bare `except Exception: return` around the SRS
  write, so an attempt commits and looks recorded while its card is never created. What
  makes it a *good* one is that it's invisible from the settings screen too, because
  `config.overrides` filters the offending value back out — a genuine multi-component
  interaction rather than one edited line.
- `stale-cache-invalidation` on job-tracker: the parsed `Criteria` memoized on the
  server object and handed to every request for the life of the process, so edits from
  the tuning page do nothing until restart. Correctly level 3 — you have to observe
  runtime state across two requests to see it, which is exactly what that rung means.

That second bug is the encouraging result, because it's the class of thing #5 worries
injected bugs can't be: emergent-feeling rather than obviously inserted.

- `shared-mutable-state` on job-tracker: `LlmClient._judgments` declared as a class
  attribute, so one dict is shared by every instance for the whole process, and the memo
  is keyed on title — two postings with the same title inherit each other's judgment.
  This one was carried all the way through: repro failed, the fix made it pass, and the
  fix judge independently named the cause and scored it 4/5.

**Still unknown:** whether the difficulty *calibration* is right — these were "solved" by
restoring the original file, not by a real attempt, so nothing here says a human finds
them in the estimated time — and whether quality holds across the other eight bug
classes. Revisit after ~20 real sessions.

**If it turns out to be unreliable**, the fallback is a library of *parameterized bug
templates* per language ("swap `<=` for `<` in a boundary check in a function matching
these criteria") where the LLM chooses the site and the template guarantees the shape.
Less varied, far more reliable. Worth prototyping early if the free-form approach
misses more than ~1 in 3.

### 2. Verification false-negatives — *mitigation built*

You fix the bug in a way that's correct but doesn't make `repro_cmd` pass — e.g. you
changed the interface the repro script calls. Mitigation: the LLM judge gets a vote,
and there's a manual "I claim this is fixed" override that flags the session for review
rather than blocking you. Never let the harness tell a correct engineer they're wrong.

`[f]orce solved` in the session REPL does this; it records `"forced": true` in
`grade.json` so the session is auditable rather than silently trusted. Not yet known
how often it's needed — if it's more than rarely, the repro contract is too brittle.

### 3. Peeking

`git log -p` reveals everything in two seconds. Options considered: rewriting history
so the injection isn't a distinct commit (fragile), squashing the whole project into a
single synthetic commit (destroys the `git log` that a real debugger would legitimately
use), or the honor system. **Going with the honor system**, because the tool is for a
person training themselves and cheating costs them their own practice hour. Revisit
only if it turns out to be tempting in practice.

### 4. Projects that can't run

Most personal projects have no tests and a non-obvious run command. **Largely resolved
by `onboard.py`**: the commands are derived from the repo and proved in a worktree, so
first use costs a keystroke per project rather than three answers. Measured over
`~/Projects`, 9 of 12 repos yield a plausible candidate from static evidence alone; the
rest fall through to the LLM proposer.

What's left is the genuinely unrunnable: projects that can't run headlessly (GUI apps,
the Vulkan renderer) are debug-ineligible whoever fills the field in. They're still fine
for the project-QA flow. Open sub-question: a repo whose suite is red *today* verifies
as ineligible even though it would be a fine training target at an older commit.

### 5. Does injected-bug practice transfer?

Real interview bugs are written by humans and often *emergent* (a bad interaction
between two reasonable decisions) rather than *inserted* (one wrong character).
Inserted bugs may over-train "spot the edited line." Partial mitigation: bias the
taxonomy toward interaction-level bug classes, and let the critic reject anything
findable by reading a single function. Worth revisiting after ~20 real sessions.

### 6. Review grading is a matching problem, and matching is fuzzy

Unlike the debug round, there's no return code here — deciding whether "this cache
looks like it could get big" counts as catching `unbounded-growth` is an LLM judgment.
It's *tractable* judgment, because we planted the issues and therefore know the answer
key; it's matching, not open-ended evaluation. But expect it to be generous. The
grade file records the matched comment text next to each caught issue precisely so you
can audit whether it's being soft on you.

Related and unresolved: the LLM writing the PR is also the source of the answer key, so
if it plants an issue *badly* (the described problem isn't really in the diff), the
grader will happily mark you as having missed something that was never there. A critic
pass that re-derives the issues from the diff alone, without seeing the key, would
catch this. Not built.

### 7. Do the decoys actually work?

Precision scoring is only as meaningful as the decoys are convincing. If the LLM's idea
of "looks wrong but is fine" is transparently fine, nobody ever flags it, precision is
always 1.0, and the metric is decorative. One decoy per PR is a guess. Worth checking
the false-positive rate after a handful of real sessions and tuning the count.

### 8. Latency and cost — *worse than estimated, and it scales with the project*

Injection is an agentic call over a real repo. The 30–90s estimate held for a small,
fast project and was badly wrong for a large one:

| Project | Size | Suite | Injection |
|---|---|---|---|
| job-tracker | 8.7k LOC | 0.6s | succeeded first attempt |
| p99 | 14k LOC | 70s | timed out at 300s, three times |

The driver is the *test suite*, not the line count: the injector reads the repo and then
runs the suite to check its own work, so a 70-second suite eats the budget several times
over. Two changes followed — `LLM_TIMEOUT_S` is now 900s (overridable via
`DPT_LLM_TIMEOUT`), and the injector is told up front how long the suite takes and that
it is already green, so it doesn't spend a run establishing a baseline we've established
for it.

The deeper consequence is for scheduling: **a project's suite runtime, not its size,
decides whether it is usable material.** The repro command runs at least three times
during generation and once per verification. Worth capturing `test_seconds` (now
recorded on the project) as a scheduling input, and worth pre-generating in the
background — phase 2's answer — before the mixed queue is built.

### 9. Grading the verbal answers credibly

"Backed by actual interviewing rubrics" is the stated goal, but an LLM grading a
transcript will reward fluent-and-wrong over halting-and-right. Keeping rubric
dimensions fixed and asking for a one-line justification per dimension makes the
grades auditable — if the tool is being generous, you'll see it in the
justifications. Calibration against real rubrics is a phase-3 research task, not an
MVP claim.

### 10. Is a code review round worth training for at entry level?

It's a less common dedicated round than debug or project screens — more of a
mid-level-and-up format, and most common in async or take-home-shaped loops. The
counter-argument is that it cost almost nothing to build (it reuses the debug engine
wholesale) and it's the round that most directly transfers to the actual job. Full
reasoning in [SCOPE.md](SCOPE.md). Revisit if it turns out to be the flow that never
gets used.

### 11. Unresolved product questions

- Should the debug clock be visible? (pressure is realistic; a visible clock also
  induces panic that a real interviewer's presence doesn't)
- Should you be allowed to run the project's own test suite during the round, or only
  `repro_cmd`? Real interviews usually let you.
- Should the review round be timed at all? Real review is asynchronous and unhurried,
  so a clock may train the wrong instinct — but an untimed round invites re-reading the
  diff twelve times, which isn't the skill either.
- Should you be told how many issues were planted? Knowing "there are 3" makes it a
  search with a stopping condition; not knowing is realistic but makes precision
  scoring feel arbitrary.
- How does a card retire? Never, or after N clean clears at level 4?
