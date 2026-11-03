"""Fixtures that let the whole engine run without an LLM, a network, or your real repos.

The load-bearing one is `fake_claude`: it writes a stub `claude` onto PATH that speaks
the same `--output-format json` handshake as the real CLI and can edit files in its
`cwd`. That turns a 90-second live injection into a millisecond, which is the only
reason changing the generation gate is cheap.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Point every dpt path at tmp_path.

    `config` reads XDG vars at import time, so setting the environment isn't enough --
    the module-level constants have to be reassigned too.
    """
    from dpt import config

    data = tmp_path / "data"
    cache = tmp_path / "cache"
    monkeypatch.setenv("XDG_DATA_HOME", str(data.parent))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache.parent))
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "CACHE_DIR", cache)
    monkeypatch.setattr(config, "PROJECTS_FILE", data / "projects.json")
    monkeypatch.setattr(config, "CARDS_FILE", data / "cards.json")
    monkeypatch.setattr(config, "SESSIONS_DIR", data / "sessions")
    monkeypatch.setattr(config, "WORKTREE_DIR", cache / "worktrees")
    config.ensure_dirs()
    return tmp_path


@pytest.fixture
def tmp_repo(tmp_path) -> Path:
    """A real git repo with a trivially testable module and its own pytest-free runner.

    `check.py` exits non-zero when `add` is wrong, so it works as a `repro_cmd` without
    dragging pytest into the fixture.
    """
    repo = tmp_path / "sample"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "calc.py").write_text(
        textwrap.dedent(
            """\
            def add(a, b):
                return a + b


            def total(items):
                out = 0
                for item in items:
                    out += add(out * 0, item)
                return out
            """
        )
    )
    (repo / "check.py").write_text(
        textwrap.dedent(
            """\
            import sys

            sys.path.insert(0, "src")
            from calc import add, total

            assert add(2, 2) == 4, f"add(2,2) == {add(2, 2)}"
            assert total([1, 2, 3]) == 6, f"total == {total([1, 2, 3])}"
            print("ok")
            """
        )
    )
    (repo / ".gitignore").write_text(".venv/\n")

    git(repo.parent, "init", "-q", str(repo))
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial")
    return repo


class FakeClaude:
    """Scripts the stub `claude` binary that the flows shell out to."""

    def __init__(self, bindir: Path):
        self.bindir = bindir
        self.script_file = bindir / "responses.json"
        self.calls_file = bindir / "calls.jsonl"

    def script(self, *responses: dict) -> None:
        """Queue one response per invocation.

        Each response is `{"result": <text the model returns>}` plus optional
        `edits` ({relative path: new content}) and `unlink` (paths to delete),
        applied in the invocation's cwd -- that's how we simulate an injector that
        actually modifies the worktree.
        """
        self.script_file.write_text(json.dumps(list(responses)))

    def calls(self) -> list[dict]:
        if not self.calls_file.exists():
            return []
        return [json.loads(line) for line in self.calls_file.read_text().splitlines()]


@pytest.fixture
def fake_claude(tmp_path, monkeypatch) -> FakeClaude:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = FakeClaude(bindir)

    (bindir / "claude").write_text(
        textwrap.dedent(
            f"""\
            #!{os.sys.executable}
            import json, os, sys
            from pathlib import Path

            here = Path({str(bindir)!r})
            calls = here / "calls.jsonl"
            with calls.open("a") as f:
                f.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}) + "\\n")

            script = json.loads((here / "responses.json").read_text())
            n = sum(1 for _ in calls.open())
            spec = script[min(n - 1, len(script) - 1)]

            for rel, content in (spec.get("edits") or {{}}).items():
                p = Path(rel)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
            for rel in (spec.get("unlink") or []):
                Path(rel).unlink(missing_ok=True)

            if spec.get("exit_code"):
                sys.stderr.write(spec.get("stderr", "boom"))
                sys.exit(spec["exit_code"])

            print(json.dumps({{"result": spec.get("result", "")}}))
            """
        )
    )
    (bindir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    fake.script({"result": "{}"})
    return fake
