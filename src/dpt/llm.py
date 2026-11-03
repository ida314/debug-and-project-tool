"""Wrapper around the `claude` CLI in headless mode.

Why a subprocess instead of the API: the injection step needs to read a whole
unfamiliar repo and then *edit files in it*. Claude Code already has those tools, so
this is one call instead of a hand-rolled agentic loop. Cost is latency and a slightly
awkward JSON handshake, which is the right trade for an MVP.

On permissions: `allow_edits=True` passes `--permission-mode bypassPermissions`, not
`acceptEdits`. The injector's contract says it must run its own repro command to
confirm the bug fails before handing the session over, and `acceptEdits` grants file
writes but not command execution -- under it, that instruction is silently impossible
and the gate degrades to "whatever the model claimed". The blast radius is bounded by
`cwd`, which is always a throwaway worktree under the cache dir, never the user's
checkout. Read-only calls (the graders, the fix judge) get no permission flag at all.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config, worktree


class LLMError(RuntimeError):
    pass


# A confused injector should fail fast rather than burn the whole timeout budget.
MAX_TURNS = 40


def _tick(label: str, stop: threading.Event) -> None:
    """Elapsed-seconds heartbeat. Injection runs from about a minute on a small project
    to several on a large one, and a silent terminal is indistinguishable from a hang."""
    start = time.monotonic()
    while not stop.wait(1.0):
        secs = int(time.monotonic() - start)
        print(f"\r  {label}... {secs}s", end="", file=sys.stderr, flush=True)
    print("\r" + " " * 40 + "\r", end="", file=sys.stderr, flush=True)


def run(prompt: str, cwd: Path | None = None, allow_edits: bool = False,
        label: str = "thinking", progress: bool = True) -> str:
    """Run one headless Claude turn, returning its final text."""
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--max-turns", str(MAX_TURNS)]
    if allow_edits:
        cmd += ["--permission-mode", "bypassPermissions"]

    stop = threading.Event()
    ticker = None
    if progress and sys.stderr.isatty():
        ticker = threading.Thread(target=_tick, args=(label, stop), daemon=True)
        ticker.start()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            # The injector runs the project's own test commands to check its work, so it
            # needs the same scrubbed environment those commands get -- otherwise it
            # verifies against dpt's virtualenv instead of the worktree's.
            env=worktree.project_env(),
            capture_output=True,
            text=True,
            timeout=config.LLM_TIMEOUT_S,
        )
    except FileNotFoundError as e:
        raise LLMError("`claude` CLI not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise LLMError(f"claude timed out after {config.LLM_TIMEOUT_S}s") from e
    finally:
        stop.set()
        if ticker:
            ticker.join(timeout=2)

    if proc.returncode != 0:
        # Both streams: the useful message is about as often on stdout as on stderr.
        raise LLMError(
            f"claude exited {proc.returncode}\nstderr: {proc.stderr[-2000:]}"
            f"\nstdout: {proc.stdout[-2000:]}"
        )

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout
    if isinstance(payload, dict) and payload.get("is_error"):
        raise LLMError(f"claude reported an error: {str(payload.get('result'))[-2000:]}")
    return payload.get("result", "") if isinstance(payload, dict) else proc.stdout


def run_json(prompt: str, cwd: Path | None = None, allow_edits: bool = False,
             label: str = "thinking") -> dict:
    """Same, but insist on a JSON object back.

    The model likes to wrap JSON in prose or fences, so extract rather than trust.
    """
    text = run(prompt, cwd=cwd, allow_edits=allow_edits, label=label)
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
