"""A row store keeps a persisted vector as base64 float16 under `vec16` (3.17).

Measured on a 13,498-record store with 1,024-dimension vectors: JSON lists of float32 took 186 MB, this
encoding 37 MB, and on 8 real queries the top 10 matched float32's on all 8. These tests pin the
format: what is written, what is read back, what an older row reads as, and what a malformed one does.
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import sqlite_store as rows  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

DIM = 8


def _emb(text):
    h = sum(map(ord, text)) % 97
    return [round(((h * (i + 3)) % 97) / 97.0 - 0.5, 6) for i in range(DIM)]


def _f16(vec):
    return list(struct.unpack("<%de" % len(vec), struct.pack("<%de" % len(vec), *vec)))


def _docs(p):
    con = sqlite3.connect(str(p))
    try:
        return {json.loads(d)["id"]: json.loads(d) for (d,) in con.execute("SELECT doc FROM records")}
    finally:
        con.close()


def _set_doc(p, rid, doc):
    con = sqlite3.connect(str(p))
    con.execute("UPDATE records SET doc=? WHERE id=?", (json.dumps(doc, sort_keys=True, ensure_ascii=False), rid))
    con.commit()
    con.close()


def _open(p, **kw):
    kw.setdefault("persist_vectors", True)
    return Inspeximus(path=str(p), embed=_emb, **kw)


def _seed(tmp_path, n=3):
    p = tmp_path / "m.json"
    m = _open(p)
    ids = [m.remember("fact %d about the deploy window" % i, key="k%d" % i) for i in range(n)]
    m.flush()
    return p, ids


def test_a_row_holds_float16_and_no_list(tmp_path):
    p, ids = _seed(tmp_path)
    docs = _docs(p)
    assert all(rows.VEC_KEY in docs[i] and "vec" not in docs[i] for i in ids), docs
    raw = base64.b64decode(docs[ids[0]][rows.VEC_KEY])
    assert len(raw) == 2 * DIM, "two bytes per dimension"


def test_reopening_reads_the_float16_values_back(tmp_path):
    p, ids = _seed(tmp_path)
    m = _open(p)
    for r in m.items:
        assert r["vec"] == _f16(_emb(r["text"])), "a vector read back is not the float16 of what was embedded"
        assert rows.VEC_KEY not in r, "the encoded text stayed on the record in memory"


def test_a_list_row_written_before_317_still_reads(tmp_path):
    p, ids = _seed(tmp_path)
    d = _docs(p)[ids[0]]
    legacy = _emb(d["text"])
    d.pop(rows.VEC_KEY)
    d["vec"] = legacy
    _set_doc(p, ids[0], d)
    m = _open(p)
    assert next(r for r in m.items if r["id"] == ids[0])["vec"] == legacy


def test_a_list_beside_vec16_wins(tmp_path):
    """Only a release before 3.17 writes a list beside `vec16` (its reembed), and it wrote it later."""
    p, ids = _seed(tmp_path)
    d = _docs(p)[ids[0]]
    d["vec"] = [0.25] * DIM
    _set_doc(p, ids[0], d)
    assert next(r for r in _open(p).items if r["id"] == ids[0])["vec"] == [0.25] * DIM


def test_a_malformed_vec16_ranks_lexically_and_opens(tmp_path):
    p, ids = _seed(tmp_path)
    bad = {"not base64": "%%%", "odd bytes": base64.b64encode(b"\x00\x01\x02").decode(),
           "infinity": base64.b64encode(struct.pack("<2e", 1.0, float("inf"))).decode()}
    for (label, text), rid in zip(bad.items(), ids):
        d = _docs(p)[rid]
        d[rows.VEC_KEY] = text
        _set_doc(p, rid, d)
    m = _open(p)
    got = {r["id"]: r.get("vec") for r in m.items}
    assert all(got[i] is None for i in ids), got
    assert m.recall("fact about the deploy window", k=3), "the store must still answer lexically"


def test_compact_vectors_rewrites_every_list_row_and_only_those(tmp_path):
    p, ids = _seed(tmp_path)
    d = _docs(p)[ids[1]]
    d.pop(rows.VEC_KEY)
    d["vec"] = _emb(d["text"])
    _set_doc(p, ids[1], d)
    m = _open(p)
    assert m.compact_vectors() == {"compacted": 1, "kept_as_list": 0}
    docs = _docs(p)
    assert all(rows.VEC_KEY in docs[i] and "vec" not in docs[i] for i in ids), docs
    assert _open(p).compact_vectors() == {"compacted": 0, "kept_as_list": 0}, "a second run found work"


def test_compact_vectors_refuses_without_persistence(tmp_path):
    p, _ = _seed(tmp_path)
    out = _open(p, persist_vectors=False).compact_vectors()
    assert out["compacted"] == 0 and "persist_vectors=False" in out["error"], out


def test_a_value_beyond_float16_keeps_its_list(tmp_path):
    """Half floats end at 65504. A vector that does not fit is kept exactly, not clipped."""
    p = tmp_path / "m.json"
    m = Inspeximus(path=str(p), embed=lambda t: [1e6, 0.5], persist_vectors=True)
    rid = m.remember("an out-of-range embedding")
    m.flush()
    d = _docs(p)[rid]
    assert d.get("vec") == [1e6, 0.5] and rows.VEC_KEY not in d, d


def test_an_unchanged_row_writes_nothing_on_the_next_save(tmp_path):
    """The float16 round trip is exact from the second write on, so reading a store and writing one new
    record rewrites one row, not every row that holds a vector."""
    p, ids = _seed(tmp_path)
    con = sqlite3.connect(str(p))
    con.executescript("CREATE TABLE rewrites(id TEXT); CREATE TRIGGER t AFTER UPDATE OF doc ON records "
                      "BEGIN INSERT INTO rewrites VALUES(new.id); END;")
    con.commit()
    con.close()
    m = _open(p)
    m.remember("one more record", key="extra")
    m.flush()
    con = sqlite3.connect(str(p))
    rewritten = [r[0] for r in con.execute("SELECT id FROM rewrites")]
    con.close()
    assert not set(rewritten) & set(ids), "rows whose record did not change were rewritten: %s" % rewritten

    # CONTROL: the trigger sees a real rewrite, or the assertion above passes on a trigger that never fires.
    m.remember("a changed value", key="k0")
    m.flush()
    con = sqlite3.connect(str(p))
    assert con.execute("SELECT count(*) FROM rewrites WHERE id=?", (ids[0],)).fetchone()[0] >= 1
    con.close()
