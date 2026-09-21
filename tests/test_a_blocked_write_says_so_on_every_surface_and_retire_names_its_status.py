"""3.5.1: a write that did not land says so on every surface, and retire() names the status it set.

Measured on the Crew OS store 2026-09-21 (crew-os/07 Workflow/REPORT PRE BUILDERA): four rewrites
of one key in a row returned an id each and changed nothing (the objectless guard, by design, and
`last_write` said so, but nothing the caller read did); an audit filtering `status == "retired"`
reported 0 over four keys retire() had ended; a retire() + remember() took a layer from 10
`derived_from` anchors to 0 without a word. The library's verdict existed; the MCP result and the
CLI did not carry it, and the docstring did not name it.
"""
import json
import os
import subprocess
import sys

import pytest

from inspeximus import Inspeximus

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _store(tmp_path):
    return Inspeximus(str(tmp_path / "s.json"))


# ------------------------------------------------------------------ the library
def test_an_objectless_rewrite_of_a_ledgered_key_is_blocked_and_last_write_says_so(tmp_path):
    m = _store(tmp_path)
    k = "crew-os::persona::16-cinematographer::kto-ma-postavil"
    first = m.remember("v1", key=k, object="v1")
    rid = m.remember("v2 without an object", key=k)
    assert isinstance(rid, str) and rid != first
    lw = m.last_write
    assert lw["id"] == rid and lw["blocked"] is True and lw["policy"] == "objectless_guard"
    assert lw["current_id"] == first and lw["status"] == "superseded"
    assert "previous" not in lw, "a blocked write followed nothing; the current value stands"
    assert m.current(k)["text"] == "v1"


def test_a_landed_rewrite_names_the_value_it_followed_and_the_anchors_it_dropped(tmp_path):
    m = _store(tmp_path)
    a1, a2 = m.remember("anchor one"), m.remember("anchor two")
    k = "crew-os::persona::layer"
    old = m.remember("v1", key=k, object="v1", derived_from=[a1, a2])
    m.retire(k, reason="rewrite")
    new = m.remember("v2", key=k, object="v2")
    lw = m.last_write
    assert lw["id"] == new and lw["blocked"] is False
    assert lw["previous"] == {"id": old, "status": "superseded", "derived_from": 2}
    assert lw["lineage_dropped"] == 2
    # the same rewrite carrying its anchors drops nothing
    m.remember("v3", key=k, object="v3", derived_from=[a1, a2])
    assert m.last_write["lineage_dropped"] == 0
    assert m.last_write["previous"]["id"] == new
    # a previous value with no anchors is not a drop, and ordinary supersession reports the same way
    k2 = "crew-os::persona::plain"
    p = m.remember("p1", key=k2, object="p1")
    m.remember("p2", key=k2, object="p2")
    assert m.last_write["previous"] == {"id": p, "status": "superseded", "derived_from": 0}
    assert m.last_write["lineage_dropped"] == 0
    # a first write on a key followed nothing
    m.remember("fresh", key="crew-os::persona::fresh", object="f")
    assert "previous" not in m.last_write and "lineage_dropped" not in m.last_write


def test_retire_returns_the_status_it_set_and_there_is_no_retired_status(tmp_path):
    m = _store(tmp_path)
    k = "crew-os::persona::17-zvukar"
    m.remember("old", key=k, object="o")
    res = m.retire(k, reason="migrated to 17-sound-designer")
    assert res["retired"] == 1 and res["status"] == "superseded" and res["policy"] == "retired"
    recs = [r for r in m.items if r.get("key") == k]
    assert [r["status"] for r in recs] == ["superseded"]
    assert not [r for r in m.items if r.get("status") == "retired"], "no fourth status"
    assert [r for r in recs if (r.get("meta") or {}).get("superseded_by_policy") == "retired"] == recs
    assert m.retire(k, reason="again") == {"key": k, "retired": 0, "ids": [], "reason": "again",
                                            "status": "superseded", "policy": "retired"}


# ------------------------------------------------------------------ the MCP surface
@pytest.fixture
def mcp(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import importlib
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "mcp.json"))
    return importlib.reload(importlib.import_module("inspeximus.mcp_server"))


def test_the_mcp_write_tools_carry_the_verdict(mcp):
    k = "crew-os::persona::mcp"
    first = mcp.remember("v1", key=k, object="v1")
    assert first["blocked"] is False and first["status"] == "active" and first["policy"] is None
    assert first["lineage_dropped"] == 0 and "previous" not in first
    blocked = mcp.remember("v2 without an object", key=k)
    assert blocked["blocked"] is True and blocked["policy"] == "objectless_guard"
    assert blocked["current_id"] == first["id"] and blocked["status"] == "superseded"
    assert "Pass `object`" in blocked["note"]
    a = mcp.remember("an anchor")
    with_anchor = mcp.remember("v2", key=k, object="v2", derived_from=[a["id"]])
    assert with_anchor["blocked"] is False and with_anchor["previous"]["id"] == first["id"]
    dropped = mcp.remember("v3", key=k, object="v3")
    assert dropped["lineage_dropped"] == 1 and dropped["previous"]["derived_from"] == 1
    d = mcp.remember_decision("use Postgres", topic="db")
    assert d["blocked"] is False and d["lineage_dropped"] == 0
    d2 = mcp.remember_decision("use Postgres", topic="db")   # a verbatim echo of the current value
    assert d2["blocked"] is False, "an echo of the CURRENT value is a reaffirmation, not a block"
    r = mcp.retire_key(k, reason="done")
    assert r["status"] == "superseded" and r["policy"] == "retired" and r["retired"] == 1


# ------------------------------------------------------------------ the CLI
def _cli(store, *args, as_json=True):
    """`--json` is a global flag and sits before the subcommand."""
    head = [sys.executable, "-m", "inspeximus.cli", "--path", str(store)] + (["--json"] if as_json else [])
    return subprocess.run(head + list(args), capture_output=True, text=True, cwd=ROOT, encoding="utf-8")


def test_the_cli_exits_nonzero_on_a_blocked_write_and_warns_on_dropped_lineage(tmp_path):
    store = tmp_path / "cli.json"
    k = "crew-os::persona::cli"
    ok = _cli(store, "remember", "v1", "--key", k, "--object", "v1")
    assert ok.returncode == 0, ok.stderr
    first = json.loads(ok.stdout)
    assert first["blocked"] is False and first["status"] == "active"
    blocked = _cli(store, "remember", "v2 without an object", "--key", k)
    assert blocked.returncode == 3, (blocked.stdout, blocked.stderr)
    out = json.loads(blocked.stdout)
    assert out["blocked"] is True and out["policy"] == "objectless_guard" and out["current_id"] == first["id"]
    assert "NOT LANDED: objectless_guard" in blocked.stderr
    anchor = json.loads(_cli(store, "remember", "an anchor").stdout)["id"]
    linked = _cli(store, "remember", "v2", "--key", k, "--object", "v2", "--derived-from", anchor)
    assert linked.returncode == 0 and json.loads(linked.stdout)["lineage_dropped"] == 0
    dropped = _cli(store, "remember", "v3", "--key", k, "--object", "v3")
    assert dropped.returncode == 0, "a landed write with dropped lineage is a landed write"
    assert json.loads(dropped.stdout)["lineage_dropped"] == 1
    assert "1 derived_from anchor(s) and this write carries none" in dropped.stderr
    plain = _cli(store, "remember", "hello", as_json=False)
    assert plain.returncode == 0 and plain.stdout.startswith("remembered ") and plain.stderr == ""
