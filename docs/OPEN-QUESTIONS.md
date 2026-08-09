# Open questions & risks

Ordered by how likely they are to sink the project.

### 1. Can the injector reliably produce a *good* bug? (highest risk)

Failure modes: the bug breaks the build immediately (trivially findable), or it's
buried so deep it's undiscoverable, or it's not actually reproducible. Mitigations
planned: the fails-then-passes `repro_cmd` gate (mechanical, cheap, catches
"not reproducible"), plus a critic pass (catches too-easy/too-hard). Unknown until
block 4 of the MVP how often a proposal survives both.

**If it turns out to be unreliable**, the fallback is a library of *parameterized bug
templates* per language ("swap `<=` for `<` in a boundary check in a function matching
these criteria") where the LLM chooses the site and the template guarantees the shape.
Less varied, far more reliable. Worth prototyping early if the free-form approach
misses more than ~1 in 3.

### 2. Verification false-negatives

You fix the bug in a way that's correct but doesn't make `repro_cmd` pass — e.g. you
changed the interface the repro script calls. Mitigation: the LLM judge gets a vote,
and there's a manual "I claim this is fixed" override that flags the session for review
rather than blocking you. Never let the harness tell a correct engineer they're wrong.

### 3. Peeking

`git log -p` reveals everything in two seconds. Options considered: rewriting history
so the injection isn't a distinct commit (fragile), squashing the whole project into a
single synthetic commit (destroys the `git log` that a real debugger would legitimately
use), or the honor system. **Going with the honor system**, because the tool is for a
person training themselves and cheating costs them their own practice hour. Revisit
only if it turns out to be tempting in practice.

### 4. Projects that can't run

Most personal projects have no tests and a non-obvious run command. `dpt scan` asking
once per project and caching handles this, but it's real friction on first use, and
projects that genuinely can't be run headlessly (GUI apps, the Vulkan renderer) may be
debug-ineligible entirely. They're still fine for the project-QA flow.

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

### 8. Latency and cost

Injection is an agentic call over a real repo: 30–90s and non-trivial tokens. Fine for
one session, annoying for a mixed queue. Phase 2's background pre-generation is the
answer; until then, brief the user during the wait. The review round is worse than the
debug round here — writing a whole feature is a longer call than introducing one bug.

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
