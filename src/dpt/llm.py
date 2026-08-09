"""Wrapper around the `claude` CLI in headless mode.

Why a subprocess instead of the API: the injection step needs to read a whole
unfamiliar repo and then *edit files in it*. Claude Code already has those tools, so
this is one call instead of a hand-rolled agentic loop. Cost is latency and a slightly
awkward JSON handshake, which is the right trade for an MVP.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import config


class LLMError(RuntimeError):
    pass


def run(prompt: str, cwd: Path | None = None, allow_edits: bool = False) -> str:
    """Run one headless Claude turn, returning its final text."""
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if allow_edits:
        # The injector has to modify the worktree; read-only calls should not.
        cmd += ["--permission-mode", "acceptEdits"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=config.LLM_TIMEOUT_S,
        )
    except FileNotFoundError as e:
        raise LLMError("`claude` CLI not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise LLMError(f"claude timed out after {config.LLM_TIMEOUT_S}s") from e

    if proc.returncode != 0:
        raise LLMError(f"claude exited {proc.returncode}: {proc.stderr[-2000:]}")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout
    return payload.get("result", "")


def run_json(prompt: str, cwd: Path | None = None, allow_edits: bool = False) -> dict:
    """Same, but insist on a JSON object back.

    The model likes to wrap JSON in prose or fences, so extract rather than trust.
    """
    text = run(prompt, cwd=cwd, allow_edits=allow_edits)
    return extract_json(text)


def extract_json(text: str) -> dict:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"no JSON object in response: {text[:500]}")
        candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise LLMError(f"malformed JSON in response: {candidate[:500]}") from e
