"""Shared by the bad-record tests: a project with a hand-editable store, and the hook's own entry point."""
import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import inspeximus.claude_code as cc  # noqa: E402
from inspeximus import _isolate  # noqa: E402

QUERY = "what did we note about the release"
EVENT = {"hook_event_name": "UserPromptSubmit", "prompt": QUERY, "session_id": "t"}


def _deep_list(n):
    x = []
    for _ in range(n):
        x = [x]
    return x


def _deep_dict(n):
    x = {}
    for _ in range(n):
        x = {"a": x}
    return x


VALUES = [None, "x", "", 0, -1, 1.5, True, [], [1, "a"], {}, {"a": 1}, 10 ** 400, float("inf"), float("-inf"), float("nan"),
          "\ud800", "x" * 50000, _deep_list(400), _deep_dict(400)]
IDS = ["none", "str", "empty", "zero", "neg", "float", "bool", "list0", "list2", "dict0", "dict1", "bigint", "inf", "-inf", "nan",
       "surrogate", "longstr", "deeplist", "deepdict"]
META_EXTRA = ["quarantined", "read_guards", "malformed", "superseded_by_toggle", "project", "scope", "tier", "source"]


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "kh"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("INSPEXIMUS_NO_NUDGE", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    _isolate._SAID.clear()
    return tmp_path


def _project(base, name):
    """A project with three good records (one a decision) and one `victim` record to hand-edit, stamped under a sandbox key."""
    p = os.path.join(str(base), name)
    os.makedirs(os.path.join(p, ".git"))
    m = cc._store(p)
    m._guard_key(create=True)
    m.remember_decision("the release process uses the gate", because="it is the only check that runs", topic="release")
    m.remember("a note about the release, number one", key="n1")
    m.remember("a note about the release, number two", key="n2")
    m.remember("another note about the release from the victim", key="victim")
    m.flush()
    return p, str(m.path)


def _hand_edit(store_path, mutate):
    """Edit the raw row of the `victim` record the way a person with a text editor would: the JSON text of its document."""
    con = sqlite3.connect(store_path)
    try:
        for rid, doc in con.execute("SELECT id, doc FROM records").fetchall():
            d = json.loads(doc)
            if d.get("key") == "victim":
                mutate(d)
                con.execute("UPDATE records SET doc=? WHERE id=?", (json.dumps(d), rid))
        con.commit()
    finally:
        con.close()


def _prompt(project):
    """The hook's own entry point on a UserPromptSubmit: (stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    real_in = sys.stdin
    sys.stdin = io.StringIO(json.dumps(dict(EVENT, cwd=project)))
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cc.main()
    finally:
        sys.stdin = real_in
        if hasattr(cc, "_FAST_EXIT"):
            cc._FAST_EXIT[0] = False
    return out.getvalue(), err.getvalue()
