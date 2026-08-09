"""The project round -- STUB (phase 3).

Shape is settled; only the audio and grading legs are unimplemented. Note the
deliberate inversion from the debug flow: there we rotate projects to train reading
unfamiliar code, here the project is pinned because knowing *your* system cold is the
entire point.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from . import config, llm, prompts, srs


def load_archetypes() -> tuple[list[dict], list[str]]:
    with config.PROJECT_QUESTIONS.open() as f:
        data = yaml.safe_load(f)
    return data["archetypes"], data["rubric_dimensions"]


def seed_cards(cards: list[dict], projects: list[dict]) -> list[dict]:
    """One card per (project x archetype)."""
    archetypes, _ = load_archetypes()
    known = {c["id"] for c in cards}
    for proj in projects:
        for arch in archetypes:
            card_id = f"{proj['name']}::{arch['id']}"
            if card_id not in known:
                cards.append(srs.new_card(card_id, deck="project",
                                          name=f"{proj['name']}: {arch['name']}",
                                          project=proj["path"], archetype=arch["id"]))
    return cards


def propose(project: dict, archetype: dict) -> dict:
    return llm.run_json(
        prompts.PROPOSE_QUESTION.format(
            archetype_name=archetype["name"], prompt_seed=archetype["prompt_seed"]
        ),
        cwd=Path(project["path"]),
    )


def grade(question: dict, transcript: str) -> dict:
    return llm.run_json(prompts.GRADE_ANSWER.format(
        question=question["question"],
        what_good_looks_like=question.get("what_good_looks_like", ""),
        transcript=transcript,
    ))


def record_answer(session_id: str) -> Path:  # pragma: no cover - phase 3
    """Push-to-talk -> .wav in the session dir. `sounddevice`, no transcription yet."""
    raise NotImplementedError("phase 3: see docs/ROADMAP.md")


def transcribe(wav: Path) -> str:  # pragma: no cover - phase 3
    """Local faster-whisper, small model."""
    raise NotImplementedError("phase 3: see docs/ROADMAP.md")
