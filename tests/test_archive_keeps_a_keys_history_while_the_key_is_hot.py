"""3.16.3, AUDIT-A F-6: a row stays in the hot store while its (tenant, key) still has a row there.

history(), revert(), as_of() and provenance() read the hot rows. On 3.16.2 a key's older value moved to a
segment while its newer value stayed, so history() lost a value and revert() could not reach it (AUDIT-A's
test_f6). apply() now holds such a row back and reports it as `held_back_by_key`. Measured on a copy of the
agora project store: 51 of 64,412 movers held, across 13 keys.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core  # noqa: E402
from inspeximus import archive  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

DAY, T0 = 86400.0, 1790000000.0


def _write(m, monkeypatch, age_days, text, key, **kw):
    monkeypatch.setattr(core.time, "time", lambda: T0 - age_days * DAY)
    rid = m.remember(text, key=key, tags=["bash"], mtype="episodic", **kw)
    monkeypatch.setattr(core.time, "time", lambda: T0)
    return rid


def _store(tmp_path):
    return str(tmp_path / "coding_memory.json")


def test_audit_a_f6_the_key_history_and_revert_survive_the_move_of_the_older_value(tmp_path, monkeypatch):
    p = _store(tmp_path)
    m = Inspeximus(p)
    old = _write(m, monkeypatch, 40, "deploy target is staging-1", "cmd:deploy-target")
    new = _write(m, monkeypatch, 1, "deploy target is prod-2", "cmd:deploy-target")
    assert len(Inspeximus(p).history("cmd:deploy-target")) == 2, "control: two values before the move"
    r = archive.apply(Inspeximus(p), 7, now=T0)
    assert r["held_back_by_key"] == 1 and r["moving"] == 0, r
    h = Inspeximus(p).history("cmd:deploy-target")
    assert {x["id"] for x in h} == {old, new}, h
    s = Inspeximus(p)
    s.revert("cmd:deploy-target")
    cur = [x for x in s._items if x.get("key") == "cmd:deploy-target" and x.get("status") == "active"]
    assert cur and cur[0]["text"] == "deploy target is staging-1", cur


def test_a_key_whose_every_value_is_old_moves_whole_and_history_then_reads_nothing(tmp_path, monkeypatch):
    """The stated limit: with no hot row left for the key, it moves whole, and history() reads the hot store."""
    p = _store(tmp_path)
    m = Inspeximus(p)
    _write(m, monkeypatch, 40, "build flag is -O1", "cmd:build-flag")
    _write(m, monkeypatch, 30, "build flag is -O2", "cmd:build-flag")
    r = archive.apply(Inspeximus(p), 7, now=T0)
    assert r["moving"] == 2 and r["held_back_by_key"] == 0, r
    assert Inspeximus(p).history("cmd:build-flag") == []


def test_the_hold_pins_no_row_of_another_key_or_another_tenant(tmp_path, monkeypatch):
    p = _store(tmp_path)
    base = Inspeximus(p)
    a_old = _write(base.for_tenant("acme"), monkeypatch, 40, "acme value one", "cmd:shared-key")
    _write(base.for_tenant("acme"), monkeypatch, 1, "acme value two", "cmd:shared-key")
    g_old = _write(base.for_tenant("globex"), monkeypatch, 40, "globex old value", "cmd:shared-key")
    other = _write(base, monkeypatch, 40, "an unrelated old capture", "cmd:other-key")
    reasons: dict = {}
    moving, held = archive._select(list(Inspeximus(p)._items), T0 - 7 * DAY, ("cmd",), reasons=reasons)
    moving_ids = {r["id"] for r in moving}
    assert reasons == {a_old: "key"}, reasons
    assert g_old in moving_ids and other in moving_ids, "another tenant's and another key's rows still move"


def test_held_rows_move_once_the_hot_row_is_retired_or_erased(tmp_path, monkeypatch):
    p = _store(tmp_path)
    m = Inspeximus(p)
    old = _write(m, monkeypatch, 40, "cache dir is /tmp/a", "cmd:cache-dir")
    new = _write(m, monkeypatch, 1, "cache dir is /tmp/b", "cmd:cache-dir")
    assert archive.apply(Inspeximus(p), 7, now=T0)["held_back_by_key"] == 1
    Inspeximus(p).forget(ids=[new], request_id="erase-the-hot-row")
    r = archive.apply(Inspeximus(p), 7, now=T0)
    assert r["moving"] == 1 and r["held_back_by_key"] == 0, r
    hot = {x["id"] for x in Inspeximus(p)._items}
    logged = [i for e in archive.read_log(p) for i in e["ids"]]
    assert old not in hot and logged.count(old) == 1, (hot, logged)
    again = archive.apply(Inspeximus(p), 7, now=T0)
    assert again["moving"] == 0 and not again.get("stranded"), again


def test_a_stale_peer_writing_a_new_hot_row_does_not_change_what_moves(tmp_path, monkeypatch):
    """The hold is decided on the rows as merged under the lock; a stale handle's later write follows the merge."""
    p = _store(tmp_path)
    m = Inspeximus(p)
    gone = _write(m, monkeypatch, 40, "port is 8080", "cmd:port")
    peer = Inspeximus(p)
    r = archive.apply(Inspeximus(p), 7, now=T0)
    assert r["moving"] == 1 and r["held_back_by_key"] == 0, "control: the key had no hot row, so it moved"
    _write(peer, monkeypatch, 0, "port is 9090", "cmd:port")
    peer.flush()
    hot = {x["id"] for x in Inspeximus(p)._items}
    assert gone not in hot, "the moved row did not come back with the peer's new value"
