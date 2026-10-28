"""A store the agent is NOT talking to must be named, not left to be discovered by its absence.

MEASURED 2026-10-28 while someone dogfooded inspeximus inside Hermes. The memory provider writes to
`<hermes_home>/inspeximus/memory.json`. The MCP server's default store path is a relative filename,
so the same machine ran two stores and the same-named tools saw two different memories, with nothing
on any surface saying so. Writes succeeded, recalls came back thin, and the only way to notice was
to go looking.

This is reported, never silently redirected. Changing the default path would move an existing store
out from under whoever already uses it, and guessing which of two stores a user meant is the error
this file exists to make visible.

The controls are the point: no report when there is no second store, and no report when the file at
a sibling location IS the store in use.
"""
from __future__ import annotations

import json
import os

from inspeximus._surface import other_stores


def _store(path, texts):
    """A store file is a JSON list of records, which is what the library writes."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([{"id": "m%d" % i, "text": t, "type": "fact"} for i, t in enumerate(texts)], fh)


def test_a_hermes_provider_store_is_named_when_the_server_uses_another_path(tmp_path):
    hermes = tmp_path / "hermes_home"
    other = hermes / "inspeximus" / "memory.json"
    _store(str(other), ["The deploy target is prod-7"])
    in_use = tmp_path / "cwd" / "inspeximus_memory.json"
    _store(str(in_use), [])

    found = other_stores(str(in_use), env={"HERMES_HOME": str(hermes)}, home=str(tmp_path))
    paths = [f["path"] for f in found]
    assert str(other.resolve()) in paths, (
        "the provider's store holds a record and the server is reading a different file, and "
        "nothing named it. Found: %r" % (found,))
    named = [f for f in found if f["path"] == str(other.resolve())][0]
    assert named["memories"] == 1, named
    assert "hermes" in named["why"].lower(), named


def test_nothing_is_reported_when_there_is_no_second_store(tmp_path):
    """Control. A false report is worse than none: it sends a reader looking for a file."""
    in_use = tmp_path / "only" / "inspeximus_memory.json"
    _store(str(in_use), ["one"])
    assert other_stores(str(in_use), env={"HERMES_HOME": str(tmp_path / "nothing_here")},
                        home=str(tmp_path)) == []


def test_the_store_in_use_is_never_reported_against_itself(tmp_path):
    """Control. The path in use sits at a well-known location in a normal Hermes install."""
    hermes = tmp_path / "hermes_home"
    in_use = hermes / "inspeximus" / "memory.json"
    _store(str(in_use), ["one"])
    assert other_stores(str(in_use), env={"HERMES_HOME": str(hermes)}, home=str(tmp_path)) == []


def test_an_empty_second_store_is_not_worth_a_warning(tmp_path):
    """A file with no records is not a memory someone is missing."""
    hermes = tmp_path / "hermes_home"
    _store(str(hermes / "inspeximus" / "memory.json"), [])
    in_use = tmp_path / "cwd" / "inspeximus_memory.json"
    _store(str(in_use), ["one"])
    assert other_stores(str(in_use), env={"HERMES_HOME": str(hermes)}, home=str(tmp_path)) == []


def test_a_home_relative_sibling_is_found_too(tmp_path):
    """The catalog manifest puts the MCP store at ~/.hermes/inspeximus_memory.json."""
    other = tmp_path / ".hermes" / "inspeximus_memory.json"
    _store(str(other), ["a", "b"])
    in_use = tmp_path / "cwd" / "inspeximus_memory.json"
    _store(str(in_use), [])
    found = other_stores(str(in_use), env={}, home=str(tmp_path))
    assert [f["memories"] for f in found] == [2], found


# --- the surfaces that must actually read it -------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _start_server(tmp_path, hermes_home):
    """Start the real server module on a store and read its stderr and where_am_i."""
    import subprocess
    import sys

    import pytest
    pytest.importorskip("mcp")
    in_use = tmp_path / "cwd" / "inspeximus_memory.json"
    _store(str(in_use), [])
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(INSPEXIMUS_PATH=str(in_use), INSPEXIMUS_KEY_HOME=str(tmp_path / "keys"),
               INSPEXIMUS_NO_UPDATE_CHECK="1", PYTHONIOENCODING="utf-8")
    if hermes_home:
        env["HERMES_HOME"] = str(hermes_home)
    else:
        env.pop("HERMES_HOME", None)
    code = ("import json, inspeximus.mcp_server as s\n"
            "print('WAI' + json.dumps(s.where_am_i().get('other_stores')))\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True,
                       text=True, encoding="utf-8", timeout=180)
    assert "WAI" in r.stdout, r.stderr[-800:]
    return json.loads(r.stdout.split("WAI", 1)[1]), r.stderr


def test_the_server_says_it_at_start_and_in_where_am_i(tmp_path):
    hermes = tmp_path / "hermes_home"
    _store(str(hermes / "inspeximus" / "memory.json"), ["The deploy target is prod-7"])
    other, err = _start_server(tmp_path, hermes)
    assert other and other[0]["memories"] == 1, other
    lines = [ln for ln in err.splitlines() if "another inspeximus store" in ln]
    assert len(lines) == 1, err[-800:]
    assert "memory.json" in lines[0], lines


def test_control_a_server_with_no_second_store_says_nothing(tmp_path):
    """CONTROL. A line printed on every start would pass the test above and mean nothing."""
    other, err = _start_server(tmp_path, tmp_path / "no_hermes_here")
    assert other == [], other
    assert "another inspeximus store" not in err, err[-800:]
