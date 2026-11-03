"""The `claude` subprocess wrapper.

`extract_json` gets the most attention because it sits between a model that likes to
narrate and a pipeline that needs a dict -- it is the single most likely thing to
break a live session.
"""

from __future__ import annotations

import json

import pytest

from dpt import llm


def test_extract_json_bare_object():
    assert llm.extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert llm.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_wrapped_in_prose():
    text = 'Sure! Here is the spec:\n```json\n{"repro_cmd": "pytest"}\n```\nLet me know.'
    assert llm.extract_json(text) == {"repro_cmd": "pytest"}


def test_extract_json_handles_prose_without_fences():
    text = 'Here you go: {"a": 1, "b": [2, 3]} -- hope that helps'
    assert llm.extract_json(text) == {"a": 1, "b": [2, 3]}


def test_extract_json_survives_nested_braces():
    payload = {"planted_issues": [{"id": "i1", "meta": {"line": 4}}]}
    assert llm.extract_json(f"prefix {json.dumps(payload)} suffix") == payload


def test_extract_json_raises_on_no_object():
    with pytest.raises(llm.LLMError, match="no JSON object"):
        llm.extract_json("I could not do that.")


def test_extract_json_raises_on_malformed():
    with pytest.raises(llm.LLMError, match="malformed JSON"):
        llm.extract_json('{"a": 1,,}')


def test_run_json_parses_the_cli_envelope(fake_claude):
    fake_claude.script({"result": '```json\n{"title": "bug"}\n```'})
    assert llm.run_json("prompt") == {"title": "bug"}


def test_read_only_calls_do_not_get_write_permissions(fake_claude):
    fake_claude.script({"result": "{}"})
    llm.run_json("prompt")
    argv = fake_claude.calls()[0]["argv"]
    assert "--permission-mode" not in argv


def test_edit_calls_can_run_commands(fake_claude):
    """The injector has to run its own repro to confirm it fails; `acceptEdits`
    would let it write files but not execute anything."""
    fake_claude.script({"result": "{}"})
    llm.run_json("prompt", allow_edits=True)
    argv = fake_claude.calls()[0]["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"


def test_runs_in_the_given_cwd(fake_claude, tmp_path):
    fake_claude.script({"result": "{}"})
    workdir = tmp_path / "work"
    workdir.mkdir()
    llm.run_json("prompt", cwd=workdir)
    assert fake_claude.calls()[0]["cwd"] == str(workdir.resolve())


def test_missing_binary_raises_llm_error(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(llm.LLMError, match="not found on PATH"):
        llm.run("prompt")


def test_nonzero_exit_reports_stderr(fake_claude):
    fake_claude.script({"exit_code": 2, "stderr": "usage error: bad flag"})
    with pytest.raises(llm.LLMError, match="bad flag"):
        llm.run("prompt")
