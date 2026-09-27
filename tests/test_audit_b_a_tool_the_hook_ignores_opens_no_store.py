"""AUDIT-B B-01: a PostToolUse event for a tool the hook does not capture must not open the store.

`capture()` handles four tools: Edit, MultiEdit, Write and Bash. Its own comment records that every
other tool "fell through with nothing remembered and still rewrote the entire store", and the fix for
that made the SAVE conditional. The OPEN stayed unconditional: `_store(cwd)` runs before the tool is
looked at, so a Read event still reads and parses every row of the project store to write nothing.

Measured 2026-09-27 on a copy of this project's hook store (67,165 records, 58 MB): a PostToolUse Read
took 5.2 s median of 5, and cProfile put 5.51 of 5.62 s inside `open_store`. The installed hook has no
PostToolUse matcher, so that is every Read, Grep and Glob of a session.

The counter is the number of store loads, which does not vary between runs or machines. The control
runs first and fails the test (not the assertion) if a captured tool stops opening the store, so a
broken fixture cannot pass as the expected failure.
"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.claude_code as cc  # noqa: E402
import inspeximus.core as core  # noqa: E402

IGNORED = ("Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "Task", "TodoWrite", "NotebookEdit")


@pytest.fixture
def project(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("INSPEXIMUS_NO_NUDGE", "1")
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    m = cc._store(str(proj))
    for i in range(20):
        m.remember(f"ran: echo {i}", key=f"cmd:{i}", mtype="episodic", tags=["bash"])
    m.flush()
    loads = []
    real = core.Inspeximus._load_from_disk

    def counting(self):
        loads.append(str(self.path))
        return real(self)

    monkeypatch.setattr(core.Inspeximus, "_load_from_disk", counting)
    return proj, loads


def _post(proj, tool, tool_input, capsys):
    ev = {"hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": tool_input,
          "cwd": str(proj).replace("\\", "/"), "session_id": "b01"}
    capsys.readouterr()
    cc.capture(ev)
    return capsys.readouterr()


def _count(proj):
    return len(cc._store(str(proj)).items)


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="B-01: capture() opens the store before it checks the tool")
def test_an_ignored_tool_opens_no_store_and_a_captured_one_still_does(project, capsys):
    proj, loads = project
    # CONTROL: a captured tool opens the store exactly once and writes. If this stops holding, the
    # counter or the fixture is broken, and that must not read as the expected failure below.
    _post(proj, "Edit", {"file_path": str(proj / "a.py"), "new_string": "x = 1"}, capsys)
    if len(loads) != 1:
        pytest.fail(f"control: an Edit event made {len(loads)} store loads, expected 1")
    before = _count(proj)
    del loads[:]

    outs = []
    for tool in IGNORED:
        out = _post(proj, tool, {"file_path": str(proj / "x.py"), "pattern": "foo"}, capsys)
        outs.append((tool, out.out, out.err))
    opened = list(loads)
    del loads[:]
    after = _count(proj)

    assert after == before, f"an ignored tool changed the store: {before} -> {after} records"
    assert all(o == "" and e == "" for _, o, e in outs), f"an ignored tool printed something: {outs}"
    assert opened == [], (f"{len(opened)} store load(s) for {len(IGNORED)} events that capture nothing; "
                          f"expected 0")
