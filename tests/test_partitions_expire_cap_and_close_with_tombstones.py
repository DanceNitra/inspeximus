"""Memory partitions (CNIL: per agent and per process, size limit, automatic expiry, context deleted at
the end of the process). Controls: a write through the handle is tagged and a raw write is not in any
partition; recall through the handle sees only the partition; expiry and cap erase with tombstones whose
basis names the partition and the rule, and nothing outside the partition is touched; a context
partition erases at close and a process partition keeps; a closed partition refuses writes; the cap
can refuse instead of evict; the report says when a sweep is due and how much memory is untagged."""
import time

import pytest

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger
from inspeximus.partitions import Partitions, TAG_PREFIX

pytest.importorskip("cryptography")
DAY = 86400.0


def _store(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    return m, pk


def test_writes_are_tagged_reads_are_scoped_and_raw_writes_stay_outside(tmp_path):
    m, pk = _store(tmp_path)
    parts = Partitions(m)
    a = parts.open("triage-1", kind="context", max_records=10, agent="triage-bot")
    b = parts.open("billing", kind="agent", max_age_days=30)
    a.remember("customer asked about invoice 4471", key="a::inv")
    b.remember("invoice 4471 was paid on the ninth", key="b::inv")
    m.remember("invoice policy: net thirty", key="raw::inv")
    assert [h["text"] for h in a.recall("invoice 4471")] == ["customer asked about invoice 4471"]
    assert [h["text"] for h in b.recall("invoice 4471")] == ["invoice 4471 was paid on the ninth"]
    assert len(m.recall("invoice")) == 3                               # the store sees everything
    rep = parts.report()
    by = {r["name"]: r for r in rep["partitions"]}
    assert by["triage-1"]["records"] == 1 and by["triage-1"]["kind"] == "context" and by["triage-1"]["agent"] == "triage-bot"
    assert rep["records_outside_any_partition"] == 1 and rep["open"] == 2
    assert parts.path.exists() and Partitions(m).report()["open"] == 2   # the registry persists


def test_expiry_and_cap_erase_with_named_tombstones_and_touch_nothing_else(tmp_path):
    m, pk = _store(tmp_path)
    parts = Partitions(m)
    p = parts.open("proc", kind="process", max_age_days=7, max_records=3)
    now = time.time()
    # two partition records and the outside one are WRITTEN past the expiry. They used to be written now
    # and have `ts` edited afterwards, which is the out-of-band edit verify_writes catches since 3.12.0.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(time, "time", lambda: now - 30 * DAY)
        ids = [p.remember(f"step {i}", key=f"s::{i}") for i in range(2)]
        m.remember("outside, old", key="raw::old")
    ids.append(p.remember("step 2", key="s::2"))
    rep = parts.report(now=now)
    row = rep["partitions"][0]
    assert row["past_expiry_now"] == 2 and row["sweep_due"] is True
    res = parts.sweep(now=now)
    assert res["partitions"]["proc"] == {"expired": 2, "evicted": 0, "remaining": 1}
    assert {r["key"] for r in m.items if (r.get("status") or "active") == "active"} == {"s::2", "raw::old"}   # outside untouched
    toms = m.erasure_report()["erasures"]
    assert len(toms) == 2
    # the cap: three more writes on a cap of 3 with one live record evict nothing until the fourth
    for i in range(3, 6):
        p.remember(f"step {i}", key=f"s::{i}")
    assert len(p.records()) == 3 and {r["key"] for r in p.records()} == {"s::3", "s::4", "s::5"}
    assert parts.report()["partitions"][0]["evicted_total"] == 1
    # CONTROL: on_cap="refuse" refuses instead
    q = parts.open("strict", kind="context", max_records=1, on_cap="refuse")
    q.remember("one", key="q::1")
    with pytest.raises(ValueError, match="cap"):
        q.remember("two", key="q::2")
    assert m.verify_writes(expected_pubkey=pk)[0]


def test_a_context_partition_erases_at_close_and_a_process_one_keeps(tmp_path):
    m, pk = _store(tmp_path)
    led = ActionLedger(m, actor="agent")
    parts = Partitions(m)
    ctx = parts.open("ctx", kind="context")
    proc = parts.open("proc", kind="process")
    ctx.remember("scratch", key="c::1")
    proc.remember("durable", key="p::1")
    r1 = parts.close("ctx", actor="triage-bot", ledger=led)
    r2 = parts.close("proc", actor="ops", ledger=led)
    assert r1["disposition"] == "erased" and r1["erased"] == 1
    assert r2["disposition"] == "retained" and r2["erased"] == 0
    assert {r["key"] for r in m.items if (r.get("status") or "active") == "active"} == {"p::1"}
    assert [e["event"] for e in led.lifecycle_events()] == ["stop", "stop"]
    assert led.verify() == (True, [])
    with pytest.raises(ValueError, match="closed"):
        ctx.remember("late", key="c::2")
    with pytest.raises(ValueError, match="closed"):
        parts.open("ctx", kind="context")
    with pytest.raises(ValueError):
        parts.close("proc", actor="ops")                              # already closed
    rep = parts.report()
    assert rep["closed"] == 2 and rep["open"] == 0
    # a process partition can be erased at close on request
    p2 = parts.open("proc2", kind="process")
    p2.remember("x", key="p2::1")
    assert parts.close("proc2", actor="ops", disposition="erased")["erased"] == 1
