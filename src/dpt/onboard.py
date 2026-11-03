"""Derive a project's setup/test commands instead of asking for them.

`projects.py` says the commands can't be reliably *inferred*, and that's true. But dpt
doesn't need inference -- it has an oracle. `debug_flow.check_baseline` already answers
mechanically whether a pair of commands is right: run them in a throwaway worktree and
see whether both exit zero. That turns onboarding from guesswork into a search with a
checkable answer, and a verified guess is worth strictly more than the typed command it
replaces, which nothing checks until the first session dies at the gate.

Three sources feed one ranked candidate list, cheapest first:

  1. CI workflows. The highest-value source by a distance: a GitHub Actions job is a
     recipe someone already proved works on a bare checkout with no environment, which
     is exactly the situation a dpt worktree is in.
  2. Manifests and task runners -- lockfiles, `package.json` scripts, Makefile targets,
     tox/nox. Deterministic, instant.
  3. `claude -p`, read-only, only when the first two come up empty or all fail.

Everything derived passes `is_safe` before it is shown, let alone run: a command whose
head isn't a known build tool never reaches the confirmation screen. The screen is the
second half of that story -- nothing derived here executes until the user has seen it.

Deliberately *not* verified: `run_cmd`. A long-running process has no exit-zero success
condition, so there is no gate to hold it to. It is derived best-effort and left alone.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import llm, prompts, worktree

# A bare checkout may have to build a virtualenv from scratch; a probe must not.
SETUP_TIMEOUT_S = 900
BASELINE_TIMEOUT_S = 900
PROBE_TIMEOUT_S = 180

# Only commands headed by a known build tool are ever offered. This is what makes the
# confirmation screen meaningful: the user is vetting plausible build commands, not
# reading arbitrary shell lifted out of a README by a regex.
ALLOWED_HEADS = {
    "bundle", "cargo", "cmake", "composer", "ctest", "dotnet", "go", "gradle",
    "just", "make", "meson", "mix", "mvn", "ninja", "nox", "npm", "pdm", "pip",
    "pip3", "pipenv", "pnpm", "poetry", "pytest", "python", "python3", "rake",
    "rebar3", "sbt", "swift", "task", "tox", "uv", "yarn",
}

# Belt and braces: a line can be headed by an allowed tool and still do something no
# onboarding step has any business doing.
FORBIDDEN = re.compile(
    r"\b(sudo|curl|wget|ssh|scp|docker|systemctl|shutdown|reboot|mkfs|dd)\b"
    r"|rm\s+-[rf]|>\s*/|git\s+push|\bpublish\b|--registry"
)

_ENV_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")

# Classified by the command's verb rather than by keywords anywhere in the line:
# `pip install -e .[test]` is a setup step, and a substring match calls it a test one.
_INSTALL_VERB = re.compile(
    r"\b(install|sync|ci|restore|download|bootstrap|deps|dependencies|setup|prepare)\b",
    re.I,
)
_TEST_VERB = re.compile(r"\b(test|tests|pytest|jest|vitest|tox|nox|unittest|spec|check)\b", re.I)
_BUILD_VERB = re.compile(r"\b(build|compile)\b", re.I)

# A line that merely mentions tests is not a test command. Two real examples from
# ~/Projects: `python -m compileall -q jobtracker tests` and `pnpm run format:check`
# both match `_TEST_VERB` and both sit *above* the real suite in their CI file. Getting
# one of those adopted is the expensive failure -- it goes green, so verification can't
# catch it, and every session afterwards is graded against a lint step.
_TEST_RUNNER = re.compile(
    r"\b(pytest|tox|nox|jest|vitest|mocha|rspec|minitest|phpunit|ctest|gotestsum)\b"
    r"|\b(cargo|go|swift|dotnet|mix|sbt|gradle|mvn|ctest)\s+test\b"
    r"|\b(npm|pnpm|yarn|bun|make|just|task|rake|poetry|uv|pdm|pipenv|bundle)\s+"
    r"(run\s+|exec\s+)?(tests?|check)\b"
    r"|\bpython3?\s+-m\s+(pytest|unittest)\b",
    re.I,
)

STRONG, WEAK = 2, 1


def _strength(test_cmd: str) -> int:
    """How much this looks like the project's actual suite. Used to rank, not to reject:
    a repo with a bespoke `python check.py` runner is unusual, not wrong."""
    return STRONG if _TEST_RUNNER.search(test_cmd) else WEAK


@dataclass
class Candidate:
    """One guess at how this project bootstraps and tests itself."""

    test_cmd: str
    setup_cmd: str | None = None
    run_cmd: str | None = None
    source: str = ""  # provenance, shown on the confirmation screen

    def key(self) -> tuple[str | None, str]:
        return (self.setup_cmd, self.test_cmd)

    @property
    def strength(self) -> int:
        return _strength(self.test_cmd)


@dataclass
class Attempt:
    """What happened when one candidate was tried, for the failure report."""

    candidate: Candidate
    stage: str  # setup | probe | tests
    ok: bool
    detail: str = ""


@dataclass
class Result:
    winner: Candidate | None = None
    head: str | None = None
    test_seconds: int | None = None
    attempts: list[Attempt] = field(default_factory=list)


# --------------------------------------------------------------------------- safety


def is_safe(cmd: str) -> bool:
    """Every `&&`/`;`/`|`-separated segment must be headed by a known build tool."""
    if not cmd or not cmd.strip() or FORBIDDEN.search(cmd):
        return False
    for segment in re.split(r"&&|\|\||;|\|", cmd):
        tokens = segment.split()
        while tokens and _ENV_PREFIX.match(tokens[0]):
            tokens = tokens[1:]  # `CI=1 pytest` is headed by pytest
        if not tokens or tokens[0] not in ALLOWED_HEADS:
            return False
    return True


_TRAILING_PROSE = re.compile(r"\s+#.*$|\s{3,}\S.*$")


def _clean_line(line: str) -> str:
    """Strip what a human wrote for a human.

    README command blocks are full of `uv run pytest        # the whole suite, ~5s` and
    column-aligned annotations. Both survive the allowlist -- the head is still `uv` --
    so they have to come off here or they end up in the candidate verbatim.
    """
    return _TRAILING_PROSE.sub("", line.strip().lstrip("$ ").strip()).strip()


def _classify(line: str) -> str | None:
    if _INSTALL_VERB.search(line):
        return "setup"
    if _TEST_VERB.search(line):
        return "test"
    if _BUILD_VERB.search(line):
        return "setup"
    return None


# ------------------------------------------------------------------ evidence: CI


_CI_JOB_HINT = re.compile(r"test|check|^ci$|build|lint", re.I)


def _workflow_commands(job: dict) -> tuple[list[str], list[str]]:
    setups, tests = [], []
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for raw in str(step.get("run") or "").splitlines():
            # `${{ }}` is a GitHub expression, not shell: it won't run anywhere else.
            if "${{" in raw:
                continue
            line = _clean_line(raw)
            if not line or line.startswith("#") or not is_safe(line):
                continue
            kind = _classify(line)
            if kind == "setup" and line not in setups:
                setups.append(line)
            elif kind == "test" and line not in tests:
                tests.append(line)
    return setups, tests


def from_ci(path: Path) -> list[Candidate]:
    """Read `.github/workflows/*` -- a recipe proven to work with no environment."""
    out = []
    wf_dir = path / ".github" / "workflows"
    if not wf_dir.is_dir():
        return out
    for wf in sorted(wf_dir.iterdir()):
        if wf.suffix not in (".yml", ".yaml"):
            continue
        try:
            doc = yaml.safe_load(wf.read_text(errors="ignore"))
        except (yaml.YAMLError, OSError):
            continue
        if not isinstance(doc, dict):
            continue
        jobs = doc.get("jobs")
        if not isinstance(jobs, dict):
            continue
        named = [(n, j) for n, j in jobs.items() if isinstance(j, dict)]
        preferred = [(n, j) for n, j in named if _CI_JOB_HINT.search(str(n))] or named
        for name, job in preferred:
            setups, tests = _workflow_commands(job)
            if not tests:
                continue
            # A CI job runs lint, typecheck and compile steps before the suite, and
            # several of them mention tests. Take the one that looks most like a runner,
            # not the one that comes first.
            best = max(tests, key=_strength)
            # Several install steps in a row are one bootstrap; keep the order CI used.
            setup = " && ".join(setups[:3]) or None
            out.append(
                Candidate(
                    test_cmd=best,
                    setup_cmd=setup,
                    source=f".github/workflows/{wf.name} ({name})",
                )
            )
    return out


# ----------------------------------------------------------- evidence: manifests


# Keyed on the lockfile, not the language: `uv.lock` and `poetry.lock` sit under the
# same `pyproject.toml` and want completely different commands.
LOCKFILE_COMMANDS = [
    ("uv.lock", "uv sync --all-extras", ["uv run pytest -q", "uv run pytest"]),
    ("poetry.lock", "poetry install", ["poetry run pytest -q"]),
    ("pdm.lock", "pdm install", ["pdm run pytest -q"]),
    ("Pipfile.lock", "pipenv install --dev", ["pipenv run pytest -q"]),
    ("Cargo.lock", "cargo fetch", ["cargo test"]),
    ("go.sum", "go mod download", ["go test ./..."]),
    ("Gemfile.lock", "bundle install", ["bundle exec rake test"]),
]

MANIFEST_COMMANDS = [
    ("Cargo.toml", "cargo fetch", ["cargo test"]),
    ("go.mod", "go mod download", ["go test ./..."]),
    ("requirements.txt", "pip install -r requirements.txt", ["pytest -q"]),
    ("setup.py", "pip install -e .", ["pytest -q"]),
    ("pyproject.toml", "pip install -e .", ["pytest -q"]),
]

NODE_INSTALL = [
    ("pnpm-lock.yaml", "pnpm", "pnpm install --frozen-lockfile"),
    ("yarn.lock", "yarn", "yarn install --immutable"),
    ("package-lock.json", "npm", "npm ci"),
    ("package.json", "npm", "npm install"),
]


def from_manifests(path: Path) -> list[Candidate]:
    out = []
    for marker, setup, tests in LOCKFILE_COMMANDS + MANIFEST_COMMANDS:
        if not (path / marker).exists():
            continue
        for test in tests:
            out.append(Candidate(test_cmd=test, setup_cmd=setup, source=marker))

    pkg_path = path / "package.json"
    if pkg_path.exists():
        try:
            pkg = json.loads(pkg_path.read_text(errors="ignore"))
        except (json.JSONDecodeError, OSError):
            pkg = {}
        scripts = pkg.get("scripts") if isinstance(pkg, dict) else None
        scripts = scripts if isinstance(scripts, dict) else {}
        manager, install = next(
            ((m, i) for f, m, i in NODE_INSTALL if (path / f).exists()),
            ("npm", "npm install"),
        )
        if "test" in scripts:
            run = f"{manager} start" if "start" in scripts else None
            out.append(
                Candidate(
                    test_cmd=f"{manager} test",
                    setup_cmd=install,
                    run_cmd=run,
                    source="package.json scripts.test",
                )
            )
    return out


# -------------------------------------------------------- evidence: task runners


_TARGET = re.compile(r"^([a-zA-Z0-9_.-]+):(?!=)", re.M)
_JUST_TARGET = re.compile(r"^([a-zA-Z0-9_-]+)(?:\s+[a-zA-Z0-9_]+)*\s*:(?!=)", re.M)

SETUP_TARGETS = ("setup", "install", "bootstrap", "deps", "dev", "init")
TEST_TARGETS = ("test", "tests", "check", "unit")


def _runner_candidate(path: Path, filename: str, tool: str,
                      pattern: re.Pattern) -> list[Candidate]:
    f = path / filename
    if not f.exists():
        return []
    targets = set(pattern.findall(f.read_text(errors="ignore")))
    test = next((t for t in TEST_TARGETS if t in targets), None)
    if not test:
        return []
    setup = next((t for t in SETUP_TARGETS if t in targets), None)
    return [
        Candidate(
            test_cmd=f"{tool} {test}",
            setup_cmd=f"{tool} {setup}" if setup else None,
            run_cmd=f"{tool} run" if "run" in targets else None,
            source=f"{filename} targets",
        )
    ]


def from_task_runners(path: Path) -> list[Candidate]:
    out = []
    out += _runner_candidate(path, "Makefile", "make", _TARGET)
    out += _runner_candidate(path, "justfile", "just", _JUST_TARGET)
    out += _runner_candidate(path, "Taskfile.yml", "task", _TARGET)
    if (path / "tox.ini").exists():
        out.append(Candidate(test_cmd="tox", source="tox.ini"))
    if (path / "noxfile.py").exists():
        out.append(Candidate(test_cmd="nox", source="noxfile.py"))
    return out


# --------------------------------------------------------------- evidence: docs


_DOC_HEADING = re.compile(
    r"^#{1,4}\s*.*(test|develop|contribut|getting started|build|install|setup)",
    re.I | re.M,
)
_FENCE = re.compile(r"```(?:sh|bash|shell|console)?\s*\n(.*?)```", re.S)


def from_docs(path: Path) -> list[Candidate]:
    """Last resort, and the most aggressively filtered: README prose is not a spec.

    Only fenced blocks under a heading that promises setup or testing instructions are
    read at all, and every line still has to clear `is_safe`.
    """
    out = []
    for name in ("README.md", "CONTRIBUTING.md", "docs/CONTRIBUTING.md"):
        doc = path / name
        if not doc.exists():
            continue
        text = doc.read_text(errors="ignore")
        setups, tests = [], []
        for heading in _DOC_HEADING.finditer(text):
            section = text[heading.end() : heading.end() + 2000]
            for block in _FENCE.findall(section):
                for raw in block.splitlines():
                    line = _clean_line(raw)
                    if not line or not is_safe(line):
                        continue
                    kind = _classify(line)
                    if kind == "setup" and line not in setups:
                        setups.append(line)
                    elif kind == "test" and line not in tests:
                        tests.append(line)
        if tests:
            out.append(
                Candidate(
                    test_cmd=max(tests, key=_strength),
                    setup_cmd=" && ".join(setups[:2]) or None,
                    source=f"{name} code blocks",
                )
            )
    return out


# ------------------------------------------------------------------- derivation


def derive(path: Path) -> list[Candidate]:
    """The full static sweep, best evidence first, deduped.

    Ranked by how much the command looks like a real suite, then by source quality --
    a lockfile's `uv run pytest` is worth more than a CI job's `pnpm run format:check`,
    even though CI is the better source in general. The sort is stable, so within one
    strength the source order above still holds.
    """
    found = from_ci(path) + from_manifests(path) + from_task_runners(path) + from_docs(path)
    return sorted(_dedupe(found), key=lambda c: -c.strength)


def _dedupe(cands: list[Candidate]) -> list[Candidate]:
    seen, out = set(), []
    for cand in cands:
        if not is_safe(cand.test_cmd):
            continue
        if cand.setup_cmd and not is_safe(cand.setup_cmd):
            cand.setup_cmd = None
        if cand.key() in seen:
            continue
        seen.add(cand.key())
        out.append(cand)
    return out


def propose(path: Path, lang: str | None = None, limit: int = 3) -> list[Candidate]:
    """Ask Claude, read-only, when the static sweep found nothing that works.

    No `allow_edits`: this reads the repo and answers. Its output is filtered by exactly
    the same allowlist as everything else -- a proposal is evidence, not authority.
    """
    prompt = prompts.ONBOARD.format(
        lang=lang or "unknown",
        limit=limit,
        allowed=", ".join(sorted(ALLOWED_HEADS)),
    )
    try:
        payload = llm.run_json(prompt, cwd=path, label="reading the project")
    except llm.LLMError:
        return []
    out = []
    for item in (payload.get("candidates") or [])[:limit]:
        if not isinstance(item, dict) or not item.get("test"):
            continue
        why = str(item.get("why") or "").strip()
        out.append(
            Candidate(
                test_cmd=str(item["test"]).strip(),
                setup_cmd=(str(item.get("setup")).strip() or None)
                if item.get("setup") else None,
                run_cmd=(str(item.get("run")).strip() or None) if item.get("run") else None,
                source=f"claude: {why}" if why else "claude",
            )
        )
    return _dedupe(out)


# ----------------------------------------------------------------- verification


# A wrong test command is usually cheap to unmask: collection or compilation fails long
# before a suite would. The probe only ever *rejects* -- the full run is still the gate.
PROBES = (
    ("cargo test", lambda c: c.replace("cargo test", "cargo test --no-run", 1)),
    ("go test", lambda c: c.replace("go test", "go test -run ^$", 1)),
    ("pytest", lambda c: f"{c} --collect-only -q"),
)


def probe_for(test_cmd: str) -> str | None:
    for marker, build in PROBES:
        if marker in test_cmd:
            return build(test_cmd)
    return None


def _run(wt: worktree.Worktree, cmd: str, timeout: int) -> tuple[bool, str]:
    try:
        proc = wt.run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-2000:]


def verify(repo: Path, candidates: list[Candidate], on_event=None) -> Result:
    """Try each candidate in a throwaway worktree; the first that goes green wins.

    Candidates sharing a `setup_cmd` share its run -- `uv sync` costs minutes and the
    interesting variation is almost always in the test command, not the bootstrap.

    Raises `worktree.WorktreeError` if the repo is dirty; the caller decides what to do
    about that, since it's the user's checkout and not ours to tidy.
    """
    say = on_event or (lambda _msg: None)
    result = Result()
    wt = worktree.create(repo, f"onboard-{uuid.uuid4().hex[:8]}")
    try:
        setup_ok: dict[str | None, tuple[bool, str]] = {}
        for cand in candidates:
            if cand.setup_cmd is not None and cand.setup_cmd not in setup_ok:
                say(f"    setup: {cand.setup_cmd}")
                setup_ok[cand.setup_cmd] = _run(wt, cand.setup_cmd, SETUP_TIMEOUT_S)
            ok, detail = setup_ok.get(cand.setup_cmd, (True, ""))
            if not ok:
                result.attempts.append(Attempt(cand, "setup", False, detail))
                continue

            probe = probe_for(cand.test_cmd)
            if probe:
                say(f"    probe: {probe}")
                ok, detail = _run(wt, probe, PROBE_TIMEOUT_S)
                if not ok:
                    result.attempts.append(Attempt(cand, "probe", False, detail))
                    continue

            say(f"    tests: {cand.test_cmd}")
            started = time.monotonic()
            ok, detail = _run(wt, cand.test_cmd, BASELINE_TIMEOUT_S)
            elapsed = round(time.monotonic() - started)
            result.attempts.append(Attempt(cand, "tests", ok, detail))
            if ok:
                result.winner = cand
                result.head = wt.git("rev-parse", "HEAD").stdout.strip()
                result.test_seconds = elapsed
                break
    finally:
        worktree.teardown(wt)
    return result


def apply(project: dict, result: Result) -> None:
    """Adopt a verified candidate.

    `baseline_ok_at` is set from the commit we just proved green, so onboarding pays the
    first session's baseline check rather than adding a second one. `run_cmd` is only
    filled from a derivation, never verified -- see the module docstring.
    """
    if not result.winner:
        return
    cand = result.winner
    project["setup_cmd"] = cand.setup_cmd
    project["test_cmd"] = cand.test_cmd
    project["run_cmd"] = cand.run_cmd or project.get("run_cmd")
    project["debug_eligible"] = True
    project["configured"] = True
    project["derived_from"] = cand.source
    project["baseline_ok_at"] = result.head
    project["test_seconds"] = result.test_seconds
