"""A record written around the library with an ISO-string `ts` no longer breaks recall.

Found 2026-09-26 on the shared MCP store: 53 records written into it directly, not through
`remember()`, carried `ts` as "2026-09-25T18:40:03Z" and no `last_access`, `valid_from` or `iso`.
`recall()` computes an age as `now - last_access`, so one such row made every recall on the store
raise `TypeError: unsupported operand type(s) for -: 'float' and 'str'`, including the MCP tool.

Two defects: the load-time normalisation only filled ABSENT fields, never a wrongly typed one; and a
row store (the default format) returned from `_load_from_disk` before that normalisation ran at all.
The shape below is the broken row's, with synthetic text.

Controls: a record written by the library is untouched, and opening the store does not rewrite it.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import time

import pytest

from inspeximus import Inspeximus

ISO = "2026-09-25T18:40:03Z"
EPOCH = float(calendar.timegm(time.strptime(ISO, "%Y-%m-%dT%H:%M:%SZ")))


def _foreign(rid="f0reign0001", ts=ISO):
    return {"id": rid, "key": "crew::persona::37::battery-meta", "text": "pricing battery for persona 37",
            "status": "active", "mtype": "semantic", "tags": ["persona", "battery"],
            "derived_from": ["crew::persona::37::identity"], "ts": ts}


def _write_store(tmp_path, fmt, rows):
    path = tmp_path / "mem.json"
    if fmt == "json":
        path.write_text(json.dumps(rows), encoding="utf-8")
    else:
        from inspeximus import sqlite_store
        sqlite_store.save(str(path), rows, {})
    return path


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.mark.parametrize("fmt", ["json", "rows"])
def test_recall_works_on_a_store_holding_a_string_timestamp(tmp_path, monkeypatch, fmt):
    if fmt == "json":
        monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    else:
        monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT", raising=False)
    native = {"id": "nat1ve00001", "key": "k::native", "text": "a native budget review record",
              "status": "active", "mtype": "semantic", "tags": [], "links": [], "meta": {}, "value": 1.0,
              "ts": 1790000000.5, "last_access": 1790000000.5, "valid_from": 1790000000.5,
              "iso": "2026-09-21T13:33:20Z"}
    path = _write_store(tmp_path, fmt, [dict(native), _foreign()])
    before = _sha(path)

    m = Inspeximus(str(path))
    rec = next(r for r in m.items if r["id"] == "f0reign0001")
    assert rec["ts"] == EPOCH and rec["last_access"] == EPOCH and rec["valid_from"] == EPOCH
    assert rec["iso"] == ISO
    hits = m.recall("pricing battery persona", k=3)
    assert any(h["id"] == "f0reign0001" for h in hits), hits
    nat = next(r for r in m.items if r["id"] == "nat1ve00001")
    assert {k: nat[k] for k in native} == native, "control: a library-written record is untouched"
    assert _sha(path) == before, "control: opening and recalling did not rewrite the store"


@pytest.mark.parametrize("raw, expected", [
    ("2026-09-25T18:40:03Z", EPOCH),
    ("2026-09-25T18:40:03+00:00", EPOCH),
    ("2026-09-25T20:40:03+02:00", EPOCH),
    ("2026-09-25T18:40:03.500Z", EPOCH + 0.5),
    ("1790381346.5", 1790381346.5),
])
def test_the_timestamp_spellings_a_foreign_writer_uses(tmp_path, monkeypatch, raw, expected):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    m = Inspeximus(str(_write_store(tmp_path, "json", [_foreign(ts=raw)])))
    assert m.items[0]["ts"] == pytest.approx(expected)


def test_an_unparseable_timestamp_is_undated_and_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    m = Inspeximus(str(_write_store(tmp_path, "json", [_foreign(ts="last tuesday")])))
    rec = m.items[0]
    assert rec["ts"] == 0.0, "an undated record is honestly undated, as for a missing ts"
    assert rec["meta"]["unparsed_time"] == {"ts": "last tuesday"}, "the original value is not lost"
    m.recall("pricing battery", k=3)                                     # does not raise
