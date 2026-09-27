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


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="B-08: every open serialises every row to build the save baseline")
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
