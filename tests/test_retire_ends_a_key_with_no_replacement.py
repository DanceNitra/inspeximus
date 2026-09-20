"""`retire(key, reason)`: a key ends, its history stays, and nothing new stands in its place.

The request (CREW OS, 2026-09-20) came with its own reproduction: `remember(key=k, object="__end__")`
was meant to end a key and instead left "[RETRACTED]" as the key's ACTIVE value, because a keyed
write replaces. The first test here is that reproduction, kept as the control that shows what the
new call is not.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest

from inspeximus import Inspeximus


def _mk(**kw):
    p = os.path.join(tempfile.mkdtemp(), "s.json")
    return p, Inspeximus(path=p, **kw)


def test_CONTROL_a_keyed_write_with_a_placeholder_leaves_a_new_active_value():
    p, ix = _mk()
    ix.remember("A", key="k::test", object="A")
    ix.remember("[RETRACTED]", key="k::test", object="__end__")
    active = [r for r in ix.items if r["key"] == "k::test" and r["status"] == "active"]
    assert len(active) == 1 and active[0]["text"] == "[RETRACTED]"


def test_retire_ends_the_key_and_keeps_its_history_with_the_reason():
    p, ix = _mk(receipts=True)
    a = ix.remember("hodnoty: X", key="persona::hodnoty", object="X")
    out = ix.retire("persona::hodnoty", "moved to the EN key 'values' (hybrid C)",
                    source={"doc": "hybrid-C-migration"})
    assert out["retired"] == 1 and out["ids"] == [a]
    assert not [r for r in ix.items if r["key"] == "persona::hodnoty" and r["status"] == "active"]
    assert ix.current("persona::hodnoty") is None
    assert not any(h["id"] == a for h in ix.recall("hodnoty"))
    h = ix.history("persona::hodnoty")
    assert len(h) == 1 and h[0]["status"] == "superseded" and h[0]["policy"] == "retired"
    assert h[0]["reason"].startswith("moved to the EN key")
    rec = next(r for r in ix.items if r["id"] == a)
    assert rec["meta"]["retired_source"] == {"doc": "hybrid-C-migration"}
    # declared in the chain, so the verifier reads it as a retirement and not as concealment
    assert Inspeximus(path=p, receipts=True).verify_writes() == (True, [])
    assert ix.retire("persona::hodnoty", "again")["retired"] == 0


def test_a_reason_is_required_and_acl_keys_are_refused():
    p, ix = _mk()
    ix.remember("v", key="k")
    with pytest.raises(ValueError, match="reason"):
        ix.retire("k", "")
    with pytest.raises(ValueError, match="revoke"):
        ix.retire("acl::grant::anything", "no")
    assert ix.current("k")["text"] == "v"


def test_retire_is_scoped_to_the_tenant_and_to_the_agent():
    p, ix = _mk()
    acme, globex = ix.for_tenant("acme"), ix.for_tenant("globex")
    a = acme.remember("acme on-call", key="on-call")
    g = globex.remember("globex on-call", key="on-call")
    assert acme.retire("on-call", "rota moved")["ids"] == [a]
    assert globex.current("on-call")["id"] == g                # untouched
    alice, bob = ix.as_agent("alice"), ix.as_agent("bob")
    r = alice.remember("alice's roadmap", key="roadmap")
    assert bob.retire("roadmap", "not mine")["retired"] == 0   # bob cannot even see it
    assert alice.current("roadmap")["id"] == r
    assert alice.retire("roadmap", "done")["retired"] == 1


def test_the_l1_and_the_event_table_see_the_retirement():
    p, ix = _mk()
    a = ix.remember("v", key="k")
    assert ix.current("k")["id"] == a
    tip = ix.events_tip()
    ix.retire("k", "withdrawn")
    assert ix.current("k") is None
    evs = ix.poll_events(since_seq=tip)
    assert any(e["type"] == "record.changed" and e["memory_id"] == a
               and e["payload"]["status"] == "superseded" for e in evs)


def test_the_cli_retires_and_the_reason_lands():
    p, ix = _mk()
    ix.remember("v", key="k::cli")
    ix.flush()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", p, "retire", "--key", "k::cli",
                        "--reason", "moved"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=root,
                       env={**os.environ, "PYTHONPATH": root, "INSPEXIMUS_HEADS": "0"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "retired 1 value(s)" in r.stdout
    assert Inspeximus(path=p).history("k::cli")[0]["reason"] == "moved"
