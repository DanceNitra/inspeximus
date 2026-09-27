"""AUDIT-B B-08: opening a row store must not serialise every record back to JSON.

A row store keeps `_row_snapshot`, id -> the row text as stored, so a later save can tell what changed.
It was built at open by `_doc(r)` for every record: a `json.dumps` of the whole store on every open,
including the opens that never save (every UserPromptSubmit hook, every read-only CLI call). Measured
2026-09-27 on a copy of a 67,165-record hook store: 1.27 s of a 5.51 s open.

The rows were just read from the file, and a row whose record normalisation did not touch serialises
back to exactly the text that was read: measured on the two real store copies, 78,048 of 78,048 such
rows, 0 different. So the baseline can be the stored text, and only a row that normalisation changed
needs `_doc`.

The counter is the number of `_doc` calls while opening. The control strips `iso` from one row on disk,
which normalisation fills, and requires the baseline to equal `{id: _doc(record)}` computed
independently for every row: the saving must not change a single baseline entry.
"""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core  # noqa: E402
from inspeximus import sqlite_store as ss  # noqa: E402
from inspeximus._surface import open_store  # noqa: E402

N = 60


def test_opening_serialises_only_the_rows_normalisation_changed(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    p = str(tmp_path / "s.json")
    m = open_store(p)
    for i in range(N):
        m.remember(f"note {i} about the rélease — ok", key=f"k{i % 20}", tags=["t"], value=0.5 + i / 100)
    m.flush()
    con = sqlite3.connect(p)
    rid, doc = con.execute("SELECT id, doc FROM records ORDER BY ord LIMIT 1").fetchone()
    rec = json.loads(doc)
    rec.pop("iso")
    con.execute("UPDATE records SET doc=? WHERE id=?", (json.dumps(rec), rid))
    con.commit()
    con.close()

    calls = [0]
    real = ss._doc

    def counting(r, keep_vec=True):
        calls[0] += 1
        return real(r, keep_vec)

    monkeypatch.setattr(ss, "_doc", counting)
    h = open_store(p)
    opened = calls[0]
    monkeypatch.setattr(ss, "_doc", real)

    # CONTROL: the baseline is exactly what serialising every loaded record gives.
    want = {r["id"]: real(r, h._persist_vectors) for r in h._items}
    if h._row_snapshot != want:
        bad = [k for k in want if h._row_snapshot.get(k) != want[k]]
        pytest.fail(f"control: {len(bad)} baseline entries differ from _doc(record), first {bad[:3]}")
    if len(want) != N:
        pytest.fail(f"control: {len(want)} records loaded, expected {N}")

    assert opened <= 1, f"opening serialised {opened} rows; only the 1 row normalisation changed needs it"


def test_a_row_another_writer_encoded_differently_is_not_a_change(tmp_path, monkeypatch):
    """The baseline now holds a row's stored text. A row another writer stored with different key order
    and escapes is the same record, and a full reconcile must neither rewrite it nor emit an event for
    it, as it did not before. Byte comparison alone would do both (measured: 5 rewrites and 5 events for
    5 such rows); `sqlite_store._same` settles a byte mismatch the way the old baseline did."""
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    p = str(tmp_path / "f.json")
    recs = [{"id": f"n{i}", "text": f"Drahos\u030cova\u0301 note {i}", "ts": 1.7e9 + i, "last_access": 1.7e9 + i,
             "valid_from": 1.7e9 + i, "status": "active", "tags": [], "links": [], "meta": {}, "value": 1.0,
             "mtype": "episodic", "iso": "2023-11-14T22:13:20Z"} for i in range(20)]
    ss.save(p, recs, {})
    con = sqlite3.connect(p)
    for r in recs[:5]:
        con.execute("UPDATE records SET doc=? WHERE id=?", (json.dumps(dict(reversed(list(r.items())))), r["id"]))
    con.commit()
    before = dict(con.execute("SELECT id, doc FROM records").fetchall())
    con.close()

    m = open_store(p)
    m.open_session("s")
    m.remember("boundary note", tags=["x"])
    m.close_session("s")
    m.flush()

    con = sqlite3.connect(p)
    after = dict(con.execute("SELECT id, doc FROM records").fetchall())
    events = [mid for (mid,) in con.execute("SELECT memory_id FROM memory_events") if mid in before]
    con.close()
    if len(after) <= len(before):
        pytest.fail("control: the boundary wrote nothing, so no full reconcile was exercised")
    assert {k: after[k] for k in before} == before, "a row another writer encoded differently was rewritten"
    assert events == [], f"events were emitted for unchanged rows: {events}"
