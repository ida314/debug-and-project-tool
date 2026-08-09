# Is three flows too much?

Written because it's the right question to ask, and the answer is not the obvious one.

## The intuition, and why it's backwards

The natural read is: *debug and project-screen are siblings, and code review is the
bolt-on.* Mechanically it's the reverse.

| | Debug | **Review** | Project screen |
|---|---|---|---|
| Project index | ✓ | ✓ | ✓ |
| Git worktree lifecycle | ✓ | ✓ | — |
| LLM plants defects in a repo | ✓ | ✓ | — |
| Hidden answer key in session dir | ✓ | ✓ | — |
| Mechanical gate on generation | repro must fail | tests must pass | — |
| Grading | diff vs. answer key | comments vs. answer key | rubric on a transcript |
| New dependencies | — | **none** | audio capture, transcription |
| New concepts | — | precision/recall, verdict | rubric dimensions, voice UX |

Code review reuses the entire debug engine. It needed one taxonomy file, one flow
module, two prompts, and about twenty lines of scheduling — no new dependencies, no new
UI paradigm, no new storage. It is the *cheapest* thing that could have been added.

The project screen is the expensive one: it needs a microphone, a transcription model,
a rubric grader with no ground truth, and a completely different interaction loop. It
feels adjacent because it's conceptually about the same *interviews*. It isn't adjacent
in the code.

So the honest count isn't three systems. It's **two**: a defect-planting engine that
serves two rounds, and a verbal-answer engine that serves one.

## The unifying skill

There's also a real conceptual through-line, which is what keeps this from being a
grab bag:

> Reading code you didn't write and forming a correct opinion about it, under time
> pressure, and defending that opinion out loud.

- **Debug** — here's a symptom, find the cause.
- **Review** — here's a diff, find what's wrong before it ships.
- **Project** — the code is yours, defend the decisions in it.

Each is the same muscle at a different distance from the code. Review sits precisely
between the other two: it demands the diagnostic instinct of debugging *and* the
articulated-tradeoff vocabulary of the project screen. You can't leave a good review
comment without saying why it matters, which is the project screen in miniature.

## The honest case against

Two real objections, neither fatal:

**A dedicated code-review round is less common at entry level** than debug rounds or
project screens. It's more of a mid-level-and-up format, and it shows up most often in
async or take-home-shaped loops. If the goal is strictly "maximize pass rate on my next
entry-level loop," review is the lowest-ROI of the three per hour of *practice*.

The counterweight: it's the highest-ROI per hour of *building* (see the table), it's
the one that most directly makes you better at the actual job, and PR-review-shaped
take-homes are common enough that the skill isn't hypothetical.

**Three flows means three things that can be mediocre.** This is the real risk, and
the mitigation is sequencing, not scope-cutting: finish the debug round to genuinely
good before the review round gets a TUI, and don't start the audio work until both
worktree-based rounds are solid. The roadmap is ordered that way deliberately.

## Where the line is

The flows are the scope. Things that would be doing too much, and are explicitly out:

- System design rounds — no artifact to ground it in, and whiteboard practice is a
  different tool
- Algorithm/LeetCode practice — solved to death elsewhere
- Behavioral question practice — the voice pipeline would tempt you into it; resist,
  it has nothing to do with reading code
- Resume or job-application tooling — different problem entirely

If a fourth round is ever proposed, the test it has to pass is the one review passed:
**does it reuse an engine that already exists, and does it train the same underlying
muscle?**
