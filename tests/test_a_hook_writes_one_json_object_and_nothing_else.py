"""Codex parses hook stdout as JSON, so the shape of stdout IS the feature.

WHY THIS FILE EXISTS SEPARATELY FROM test_claude_code_hooks.py. The envelope landed in 64c9db2 and
shipped with no test, because every existing hook test asserts a SUBSTRING of stdout:

    assert "models.py" in capsys.readouterr().out

That passes whether stdout is `models.py ...` or `{"hookSpecificOutput": {... "models.py" ...}}`,
so the whole suite was blind to the one property the change exists to guarantee. Claude Code injects
raw stdout as context and accepts either; Codex 0.154.0 refuses what it cannot parse, with
`hook returned invalid session start JSON output`. The tests below read stdout as JSON rather than
searching it, and one of them counts the objects, because session_start emitted up to three blocks
and two JSON objects on one stream is not JSON either.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from inspeximus import claude_code as cc


@pytest.fixture()
def project(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.chdir(d)
    return d


def _one_object(out: str) -> dict:
    """Parse stdout the way Codex does, and fail with the raw text when it is not one JSON object."""
    text = out.strip()
    assert text, "the hook printed nothing, so there is no envelope to check"
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise AssertionError(
            "a hook's stdout must be ONE JSON object, and Codex refuses anything else with "
            "'hook returned invalid session start JSON output'. Parsing failed at %s.\n"
            "STDOUT WAS:\n%s" % (e, out)) from None
    assert isinstance(obj, dict), "the envelope must be an object, got %r" % type(obj).__name__
    return obj


def _seeded(project):
    cc.capture({"hook_event_name": "PostToolUse", "tool_name": "Write",
                "tool_input": {"file_path": "src/payments.py", "content": "def charge(): ..."}})
    return os.path.join(project, ".inspeximus", "coding_memory.json")


def test_recall_prints_one_json_envelope_naming_its_event(project, capsys):
    _seeded(project)
    capsys.readouterr()
    cc.recall({"hook_event_name": "UserPromptSubmit", "prompt": "what is in payments.py"})

    obj = _one_object(capsys.readouterr().out)
    spec = obj.get("hookSpecificOutput")
    assert isinstance(spec, dict), "the envelope must carry hookSpecificOutput, got %r" % (obj,)
    assert spec.get("hookEventName") == "UserPromptSubmit", spec
    assert "payments" in spec.get("additionalContext", ""), spec


def test_session_start_joins_its_blocks_into_a_single_object(project, capsys):
    """session_start had three prints. Three objects on one stream is not JSON, so they are joined."""
    _seeded(project)
    capsys.readouterr()
    cc.session_start({"hook_event_name": "SessionStart"})

    out = capsys.readouterr().out
    obj = _one_object(out)
    assert obj["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "payments" in obj["hookSpecificOutput"]["additionalContext"]

    # COUNT THE OBJECTS. `json.loads` on the whole string already rejects a second one, but it does
    # so with a decode error that reads like a corrupt payload rather than like "you printed twice",
    # and that is the failure this handler actually had.
    decoder = json.JSONDecoder()
    _, end = decoder.raw_decode(out.strip())
    assert not out.strip()[end:].strip(), (
        "the hook printed more than one JSON object; Codex reads the first and rejects the rest.\n"
        "TRAILING:\n%s" % out.strip()[end:])


def test_nothing_to_say_means_silence_not_an_empty_envelope(capsys):
    """An empty envelope is not the same as silence: the hosts read empty stdout as "no context".

    Asserted on the emitter rather than on `recall`, because recall also reads the user's global
    store and therefore has something to say even in an empty project. The first version of this
    test drove it through `recall` in a fresh temp directory and failed on a real recall from that
    global store, which is the test being wrong rather than the hook.
    """
    cc._emit("UserPromptSubmit")
    assert capsys.readouterr().out == ""
    cc._emit("UserPromptSubmit", "", None)
    assert capsys.readouterr().out == ""


def test_the_control_the_parser_rejects_the_shape_this_replaced(capsys):
    """If `_one_object` accepted bare text, every assertion above would be decoration.

    This is the arm that fails when the checker stops checking, rather than when the hook breaks.
    """
    with pytest.raises(AssertionError, match="ONE JSON object"):
        _one_object("[inspeximus] relevant project memory:\n- src/payments.py")
    with pytest.raises(AssertionError, match="ONE JSON object"):
        _one_object('{"hookSpecificOutput": {}}\n{"hookSpecificOutput": {}}')
