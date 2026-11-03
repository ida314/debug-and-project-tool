"""SM-2 and the quality tables.

The quality functions are checked against the tables in docs/SPACED-REPETITION.md
directly -- they're the contract between "how the session went" and "when it returns",
and a silent drift there is invisible until weeks of scheduling have gone wrong.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dpt import srs


def test_new_card_is_due_today():
    card = srs.new_card("off-by-one", deck="debug")
    assert srs.is_due(card)
    assert card["level"] == 1
    assert card["interval"] == 0


def test_interval_ladder_is_1_then_6_then_scaled():
    card = srs.new_card("c", deck="debug")
    srs.review(card, 5)
    assert card["interval"] == 1
    srs.review(card, 5)
    assert card["interval"] == 6
    ease = card["ease"]
    srs.review(card, 5)
    assert card["interval"] == round(6 * ease)


def test_due_date_follows_the_interval():
    card = srs.new_card("c", deck="debug")
    today = date(2026, 8, 12)
    srs.review(card, 5, today=today)
    assert card["due"] == (today + timedelta(days=1)).isoformat()


def test_failure_resets_reps_and_drops_a_level():
    card = srs.new_card("c", deck="debug")
    for _ in range(3):
        srs.review(card, 5)
    assert card["level"] > 1
    level_before = card["level"]

    srs.review(card, 1)
    assert card["reps"] == 0
    assert card["interval"] == 1
    assert card["level"] == level_before - 1


def test_level_is_clamped_to_the_1_to_4_ladder():
    card = srs.new_card("c", deck="debug")
    for _ in range(10):
        srs.review(card, 5)
    assert card["level"] == 4

    for _ in range(10):
        srs.review(card, 1)
    assert card["level"] == 1


def test_ease_never_falls_below_the_floor():
    card = srs.new_card("c", deck="debug")
    for _ in range(20):
        srs.review(card, 0)
    assert card["ease"] == pytest.approx(srs.MIN_EASE)


def test_only_a_perfect_score_levels_up():
    card = srs.new_card("c", deck="debug")
    srs.review(card, 4)
    assert card["level"] == 1


def test_due_cards_are_most_overdue_first_and_deck_filtered():
    cards = [
        srs.new_card("a", deck="debug"),
        srs.new_card("b", deck="review"),
        srs.new_card("c", deck="debug"),
    ]
    cards[0]["due"] = "2020-01-01"
    cards[2]["due"] = "2019-01-01"
    cards[1]["due"] = "2100-01-01"  # not due

    due = srs.due_cards(cards)
    assert [c["id"] for c in due] == ["c", "a"]
    assert [c["id"] for c in srs.due_cards(cards, deck="debug")] == ["c", "a"]
    assert srs.due_cards(cards, deck="review") == []


# --- the quality tables, straight from docs/SPACED-REPETITION.md ---


@pytest.mark.parametrize(
    "solved,hints,minutes,estimate,root_cause,expected",
    [
        (False, 0, 5, 15, True, 1),    # unsolved
        (True, 3, 5, 15, True, 2),     # leaned on the hint ladder
        (True, 4, 5, 15, True, 2),
        (True, 1, 5, 15, True, 3),     # needed a nudge
        (True, 2, 5, 15, True, 3),
        (True, 0, 20, 15, True, 4),    # unaided but over the estimate
        (True, 0, 5, 15, False, 4),    # fast, but papered over the symptom
        (True, 0, 5, 15, True, 5),     # unaided, under the estimate, real fix
    ],
)
def test_debug_quality_table(solved, hints, minutes, estimate, root_cause, expected):
    assert srs.debug_quality(solved, hints, minutes, estimate, root_cause) == expected


@pytest.mark.parametrize(
    "caught,severity_correct,expected",
    [(False, False, 1), (True, False, 3), (True, True, 5)],
)
def test_review_quality_table(caught, severity_correct, expected):
    assert srs.review_quality(caught, severity_correct) == expected


def test_qa_quality_is_the_rounded_rubric_mean():
    assert srs.qa_quality({"correctness": 4, "specificity": 5}) == 4
    assert srs.qa_quality({}) == 1
