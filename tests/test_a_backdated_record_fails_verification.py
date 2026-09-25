"""A record whose recording time was edited on disk fails verify_writes.

Session T, item 2. `as_of(key, when, as_recorded=)` and `believed_at(key, as_recorded)` answer "what did
the store know at time T" by selecting records with `ts <= T`. Every write receipt carries the record's
`ts`, inside the hashed receipt, but `verify_writes()` compared only the fields under `commit`, and `ts`
is not one of them. So backdating a correction on disk made the store report it had known the
correction before it was written, and the chain still verified.

Controls: the untouched store answers with the old value and verifies; the edit changes the answer
(so the case reproduces); a record whose receipt carries no `ts` is not failed for it.
"""
from __future__ import annotations

import json
import os

import pytest

from inspeximus import Inspeximus

pytest.importorskip("cryptography")


def _rows(data):
    return data["items"] if isinstance(data, dict) else data


def _two_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    path = tmp_path / "mem.json"
    key = os.urandom(32).hex()
    m = Inspeximus(str(path), receipts=True, receipt_key=key)
    old = m.remember("retention is 90 days", key="retention", object={"days": 90})
    new = m.remember("retention is 30 days", key="retention", object={"days": 30})
    return path, key, old, new


def _edit_ts(path, rid, fn):
    data = json.loads(path.read_text(encoding="utf-8"))
    rec = next(r for r in _rows(data) if r["id"] == rid)
    rec["ts"] = fn(rec["ts"])
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("direction", ["backdated", "postdated"])
def test_an_edited_recording_time_fails_verify_writes(tmp_path, monkeypatch, direction):
    path, key, old, new = _two_writes(tmp_path, monkeypatch)
    m = Inspeximus(str(path), receipts=True, receipt_key=key)
    t_old = next(r["ts"] for r in m.items if r["id"] == old)
    t_new = next(r["ts"] for r in m.items if r["id"] == new)
    assert m.verify_writes()[0], "control: the untouched store verifies"
    assert t_new > t_old, "control: the two writes have distinct recording times"
    if direction == "backdated":        # the correction claims to predate what it corrected
        probe = t_old - 0.5
        before = m.believed_at("retention", as_recorded=probe)
        _edit_ts(path, new, lambda ts: t_old - 1.0)
    else:                               # the original claims to postdate the time it was known
        probe = (t_old + t_new) / 2
        before = m.believed_at("retention", as_recorded=probe)
        _edit_ts(path, old, lambda ts: t_new + 1.0)
    m = Inspeximus(str(path), receipts=True, receipt_key=key)
    after = m.believed_at("retention", as_recorded=probe)
    assert (before or {}).get("id") != (after or {}).get("id"),         "control: the edit changes what the store says it knew"
    ok, problems = m.verify_writes()
    assert not ok, f"a {direction} record still verifies"
    assert any("WHEN it was recorded" in p for p in problems), problems


def test_a_receipt_without_a_recording_time_is_not_failed_for_it(tmp_path, monkeypatch):
    path, key, old, _ = _two_writes(tmp_path, monkeypatch)
    rpath = next(p for p in tmp_path.iterdir() if "receipts" in p.name)
    real = Inspeximus._append_receipt

    m = Inspeximus(str(path), receipts=True, receipt_key=key)
    assert m.verify_writes()[0]

    def no_ts(self, r, *a, **k):
        r = dict(r)
        r["ts"] = None
        return real(self, r, *a, **k)
    monkeypatch.setattr(Inspeximus, "_append_receipt", no_ts)
    rid = m.remember("an undated write", key="undated")
    monkeypatch.setattr(Inspeximus, "_append_receipt", real)
    assert rpath.exists()
    assert [r for r in Inspeximus(str(path), receipts=True, receipt_key=key)._receipts
            if r["memory_id"] == rid][-1]["ts"] is None, "control: the receipt carries no ts"
    assert Inspeximus(str(path), receipts=True, receipt_key=key).verify_writes()[0]


def test_the_other_readers_of_the_receipt_name_the_recording_time(tmp_path, monkeypatch):
    """The class: every surface that re-checks a record against its receipt compares `ts` as well."""
    from inspeximus.audit_bundle import bind_content, build_bundle

    path, key, old, new = _two_writes(tmp_path, monkeypatch)
    m = Inspeximus(str(path), receipts=True, receipt_key=key)
    bundle = build_bundle(m)
    assert bind_content(bundle, m.items)["mismatched"] == [], "control: the untouched store binds"
    assert m.provenance(id=new)["integrity"]["content_matches_receipt"] is True, "control"

    _edit_ts(path, new, lambda ts: ts - 3600.0)
    m = Inspeximus(str(path), receipts=True, receipt_key=key)
    assert {"memory_id": new, "field": "ts"} in bind_content(bundle, m.items)["mismatched"]
    integ = m.provenance(id=new)["integrity"]
    assert integ["content_matches_receipt"] is False and "ts" in integ["content_mismatch_fields"]
