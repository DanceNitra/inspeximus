"""AUDIT-B B-04: opening a store must not infer a type, or format a date, that the record already has.

`_normalise_loaded` fills fields an older or foreign record lacks, with `r.setdefault("mtype",
_infer_type(text))` and `r.setdefault("iso", time.strftime(...))`. Python evaluates a call's arguments
before the call, so the default is computed for EVERY record and thrown away for every record that
already carries the field -- which is every record the library itself wrote. `_infer_type` runs two
regex searches over the record's text.

Measured 2026-09-27 on a copy of this project's hook store (67,165 records): 132,186 regex searches
and 1.78 s of a 5.51 s open, on every open, for a result that was discarded 67,165 times out of 67,165.

The counters are the number of `_infer_type` and `strftime` calls made while opening. The control
removes the fields from exactly one row on disk and requires exactly that row to be filled, with the
value a fresh inference gives, so the test cannot pass by never normalising at all.
"""
import json
import os
import sqlite3
import sys
import time as _time
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core  # noqa: E402
from inspeximus._surface import open_store  # noqa: E402

N = 50


@pytest.fixture
def store(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    p = str(tmp_path / "s.json")
    m = open_store(p)
    for i in range(N):
        m.remember(f"we must always deploy on friday {i}", key=f"k{i}")
    m.flush()
    return p


def _open_counting(p, monkeypatch):
    calls = {"infer": 0, "strftime": 0}
    real_infer = core._infer_type

    def infer(text):
        calls["infer"] += 1
        return real_infer(text)

    def strftime(*a):
        calls["strftime"] += 1
        return _time.strftime(*a)

    proxy = types.SimpleNamespace(**{k: getattr(_time, k) for k in dir(_time) if not k.startswith("__")})
    proxy.strftime = strftime
    monkeypatch.setattr(core, "_infer_type", infer)
    monkeypatch.setattr(core, "time", proxy)
    try:
        m = open_store(p)
    finally:
        monkeypatch.setattr(core, "_infer_type", real_infer)
        monkeypatch.setattr(core, "time", _time)
    return m, calls


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="B-04: setdefault evaluates _infer_type and strftime for every record at open")
def test_open_infers_and_formats_only_what_a_record_lacks(store, monkeypatch):
    # CONTROL: strip mtype and iso from one row on disk. Opening must fill exactly that row, with the
    # values the library would compute, or the counters below measure a path that never normalises.
    con = sqlite3.connect(store)
    rid, doc = con.execute("SELECT id, doc FROM records ORDER BY ord LIMIT 1").fetchone()
    rec = json.loads(doc)
    want_type = core._infer_type(rec.get("text") or "")
    want_iso = rec.pop("iso")
    rec.pop("mtype")
    con.execute("UPDATE records SET doc=? WHERE id=?", (json.dumps(rec), rid))
    con.commit()
    con.close()

    m, calls = _open_counting(store, monkeypatch)
    got = next(r for r in m._items if r["id"] == rid)
    if got.get("mtype") != want_type or got.get("iso") != want_iso:
        pytest.fail(f"control: the stripped row came back as mtype={got.get('mtype')!r} "
                    f"iso={got.get('iso')!r}, expected {want_type!r} {want_iso!r}")
    if len(m._items) != N:
        pytest.fail(f"control: {len(m._items)} records loaded, expected {N}")

    assert calls["infer"] == 1, f"_infer_type ran {calls['infer']} times for 1 record without a type"
    assert calls["strftime"] == 1, f"strftime ran {calls['strftime']} times for 1 record without an iso"
