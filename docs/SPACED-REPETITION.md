# Spaced repetition without a fixed problem list

This was the fuzzy part of the original idea, and it resolves cleanly with one move.

## The move: schedule *skills*, not problems

Anki schedules a card because the card is the thing you're trying to remember. Here,
the specific bug is disposable — you'll never see that exact bug again, and you
shouldn't. What you're actually trying to retain is a **recognition pattern**:

> "The output is off by exactly one element" → *check loop bounds and slice indices.*
>
> "It works alone but fails under load" → *look for shared mutable state.*
>
> "It worked yesterday" → *bisect.*

So the SRS item is the pattern. The LLM generates a fresh *instance* of it each time
it comes due. The card holds the schedule; the proposer holds the variety. Same
structure as a math teacher who reuses the concept and regenerates the numbers.

This also fixes the anti-memorization problem for free: you can't memorize your way
through a card, because the instance is new every time and lives in a different
codebase.

## Three decks

**Debug deck.** Card = a bug class, from `taxonomy/debug_categories.yaml`. Project
selection is deliberately *rotated away* from whatever you last used — the unfamiliar
codebase is part of the exercise.

**Review deck.** Card = an issue class, from `taxonomy/review_issues.yaml`, tagged with
one of four dimensions (correctness, maintainability, performance, testing). This deck
overlaps the debug deck conceptually but can't be merged with it: *a missing test or a
leaky abstraction has no runtime symptom*, so it can never be the subject of a debug
round. It exists only as something you find by reading.

**Project deck.** Card = `(project, question-topic)`. Here the project is pinned:
knowing *your* system deeply is the whole point. New cards get created automatically
when `dpt scan` finds a new project — one per topic archetype.

### One exercise, several cards

The review deck is where the schedule-skills-not-problems design earns its keep. A
single PR plants three or four issues, and **each one scores its own card
independently**. One twenty-minute review can push `n-plus-one` out to three weeks
while pulling `test-tests-the-mock` back to tomorrow. There is no coherent way to do
that if the scheduled item is "the problem."

Issue selection for a PR takes the most overdue review cards but **spreads them across
dimensions**, because a PR that's wrong in only one way trains a narrow eye. Real PRs
go wrong in several at once.

## Scheduling

SM-2, unmodified, because it's twenty lines and well understood. Grade → quality 0–5 →
new interval + ease factor.

Debug quality is derived, not self-reported:

| Signal | Effect |
|---|---|
| Unsolved / gave up | q = 1 |
| Solved with hint level 3–4 | q = 2 |
| Solved with hint level 1–2 | q = 3 |
| Solved unaided, over the estimate | q = 4 |
| Solved unaided, under the estimate, judge says cause not symptom | q = 5 |

Review quality is per planted issue:

| Signal | Effect |
|---|---|
| Missed it | q = 1 |
| Caught it, misjudged the severity | q = 3 |
| Caught it, severity right | q = 5 |

Severity is part of the score rather than a footnote because calling a blocker a nit
means you'd have approved the PR — which in practice is the same as missing it.
Precision and verdict correctness are scored at the *session* level and surfaced as
feedback, since they're properties of how you reviewed, not of any one card.

Project quality is the mean rubric score, rounded, mapped 1–5 → 1–5.

Self-reported "how hard was that?" is deliberately excluded — the whole reason this
domain is trainable is that we have objective signals, so use them.

## Growing the deck

Seed from the taxonomy files. Then after each session, a cheap `claude -p` call tags
which patterns the session actually exercised; unseen tags get registered as new
cards. The deck grows toward the shape of the code you actually write — someone
writing Rust services and someone writing React will diverge within a few weeks, which
is correct.

**Difficulty ladder per card**, independent of the interval:

1. Local and visible — one function, findable by reading
2. Cross-file — requires following a call chain
3. Requires runtime observation — breakpoint, log, or profiler to see the state
4. Nondeterministic — concurrency, ordering, environment

A card levels up when you clear it unaided under the estimate, and levels down after a
miss. So the interval controls *when* it returns and the level controls *how hard it
returns as*.

## The technique tag

Each bug class in the taxonomy carries `rewards: [...]` — the debugging technique the
bug is designed to make you reach for. This is what makes "practice breakpoints"
trainable: you can't drill breakpoints directly, but you *can* generate bugs that are
miserable to find by reading and pleasant to find by stepping. If the tool sees that
you never clear `rewards: [breakpoint]` cards efficiently, it can bias toward them.

## Session shape

A session is a small mixed queue, not one card: overdue debug cards first (they're the
expensive ones and you want fresh attention on them), then a review round — which
clears several cards at once and is the cheapest way to drain a backlog — then
project-QA cards to fill the remaining time budget. You tell the tool "I have 45
minutes"; it packs the queue using `estimated_minutes`.
