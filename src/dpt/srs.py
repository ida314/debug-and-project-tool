"""SM-2 over *skills*, not problems.

The card is a recognition pattern ("output is off by one -> check loop bounds"); the
LLM generates a fresh instance of it each time it comes due. See
docs/SPACED-REPETITION.md for why this is the right unit.
"""

from __future__ import annotations

from datetime import date, timedelta

MIN_EASE = 1.3


def new_card(card_id: str, deck: str, **extra) -> dict:
    return {
        "id": card_id,
        "deck": deck,  # "debug" | "project"
        "ease": 2.5,
        "interval": 0,
        "reps": 0,
        "level": 1,  # difficulty ladder, independent of the interval
        "due": date.today().isoformat(),
        "last_project": None,
        **extra,
    }


def is_due(card: dict, today: date | None = None) -> bool:
    today = today or date.today()
    return date.fromisoformat(card["due"]) <= today


def due_cards(cards: list[dict], deck: str | None = None) -> list[dict]:
    out = [c for c in cards if is_due(c) and (deck is None or c["deck"] == deck)]
    # Most overdue first: those are the ones at real risk of being forgotten.
    return sorted(out, key=lambda c: c["due"])


def review(card: dict, quality: int, today: date | None = None) -> dict:
    """Apply SM-2. `quality` is 0-5 and is *derived* from measured performance
    (time, hints, fix quality), never self-reported -- objective signals are the
    reason this domain is trainable at all."""
    today = today or date.today()
    q = max(0, min(5, quality))

    if q < 3:
        card["reps"] = 0
        card["interval"] = 1
        card["level"] = max(1, card["level"] - 1)
    else:
        card["reps"] += 1
        if card["reps"] == 1:
            card["interval"] = 1
        elif card["reps"] == 2:
            card["interval"] = 6
        else:
            card["interval"] = round(card["interval"] * card["ease"])
        if q == 5:
            card["level"] = min(4, card["level"] + 1)

    card["ease"] = max(
        MIN_EASE, card["ease"] + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    )
    card["due"] = (today + timedelta(days=card["interval"])).isoformat()
    return card


def debug_quality(solved: bool, hints_used: int, minutes: float,
                  estimated_minutes: float, fixed_root_cause: bool) -> int:
    """Map measured debug performance onto SM-2's 0-5 scale."""
    if not solved:
        return 1
    if hints_used >= 3:
        return 2
    if hints_used >= 1:
        return 3
    if minutes > estimated_minutes or not fixed_root_cause:
        return 4
    return 5


def review_quality(caught: bool, severity_correct: bool) -> int:
    """One planted issue scores one card.

    Spotting a blocker and calling it a nit is a real review failure -- you'd have
    approved the PR -- so severity is part of the score, not a footnote.
    """
    if not caught:
        return 1
    return 5 if severity_correct else 3


def qa_quality(scores: dict[str, int]) -> int:
    """Mean rubric score across dimensions, rounded onto SM-2's scale."""
    if not scores:
        return 1
    return max(1, min(5, round(sum(scores.values()) / len(scores))))
