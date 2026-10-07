"""An erased record's vector leaves every file inspeximus wrote, in both encodings (3.17).

From 3.17 a row store keeps a vector as base64 float16 under `vec16`. Erasure has to reach that text the
way it reaches the record's own text, so each test erases one record and then reads the BYTES of every
file in the store's directory: the store, its WAL, its sidecars, archive segments and the migration
backup. A parsed read would miss a free page, a sidecar or a backup, which is where a residue lives.

Each scan has a control: the needle is found before the erasure, and a kept record's needle is found
after it. Without the first, a needle that was never written would pass; without the second, a scan
that reads nothing would.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core  # noqa: E402
from inspeximus import archive  # noqa: E402
from inspeximus import sqlite_store as rows  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

DAY = 86400.0
DIM = 16


def _emb(text):
    """A distinct vector per text, with values whose float16 bytes differ between texts."""
    h = sum(map(ord, text)) % 251
    return [round(0.01 + h * 0.0013 + i * 0.00071, 7) for i in range(DIM)]


def _b64(vec):
    return base64.b64encode(struct.pack("<%de" % len(vec), *vec)).decode("ascii")


def _bytes_in_dir(d):
    out = {}
    for name in os.listdir(d):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                out[name] = fh.read()
    return out


def _where(d, needle):
    n = needle.encode("utf-8")
    return sorted(name for name, data in _bytes_in_dir(d).items() if n in data)


def _store(tmp_path, **kw):
    return Inspeximus(path=str(tmp_path / "m.json"), embed=_emb, persist_vectors=True, **kw)


def test_forget_removes_the_float16_vector_from_every_file(tmp_path):
    m = _store(tmp_path, receipts=True)
    gone = m.remember("the subject's home address is 12 Linden Street", key="addr")
    kept = m.remember("the deploy window is Tuesday", key="deploy")
    m.flush()
    v_gone = _b64(next(r["vec"] for r in m.items if r["id"] == gone))
    v_kept = _b64(next(r["vec"] for r in m.items if r["id"] == kept))
    assert v_gone != v_kept, "precondition: two different needles"
    assert _where(tmp_path, v_gone), "control: the vector reached disk in float16 before the erasure"

    m.forget(gone)
    m.flush()
    assert _where(tmp_path, v_gone) == [], "the erased record's vector is still in a file"
    assert _where(tmp_path, v_kept), "control: a kept record's vector is still found by the same scan"


def test_archive_segments_never_carry_the_vector_and_erasure_reaches_them(tmp_path, monkeypatch):
    """Segments are written without vectors, so an archived record's vector leaves the disk on the move.
    The erasure is then checked against the segment file as well."""
    p = tmp_path / "coding_memory.json"
    t0 = time.time()
    m = Inspeximus(str(p), embed=_emb, persist_vectors=True)
    ids = []
    for i in range(6):
        monkeypatch.setattr(core.time, "time", lambda i=i: t0 - 40 * DAY + i)
        ids.append(m.remember("ran: make target %d" % i, key="cmd:%d" % i, mtype="episodic"))
    m.flush()
    monkeypatch.undo()
    vecs = {r["id"]: _b64(r["vec"]) for r in m.items if r["id"] in ids}
    assert all(_where(tmp_path, v) for v in vecs.values()), "control: every vector is on disk before the move"

    res = archive.apply(Inspeximus(str(p), embed=_emb, persist_vectors=True), 7)
    assert res["applied"] is True, res
    segs = [n for n in os.listdir(tmp_path) if archive.is_segment(str(tmp_path / n))]
    assert segs, "precondition: the move wrote a segment"
    assert all(_where(tmp_path, v) == [] for v in vecs.values()), (
        "an archived record's vector is still on disk after the move")

    m2 = Inspeximus(str(p), embed=_emb, persist_vectors=True)
    target = ids[0]
    seg_bytes = b"".join(_bytes_in_dir(tmp_path)[n] for n in segs)
    assert target.encode() in seg_bytes, "control: the archived record is in a segment before the erasure"
    m2.forget(target)
    m2.flush()
    seg_bytes = b"".join(open(tmp_path / n, "rb").read() for n in segs if (tmp_path / n).exists())
    assert target.encode() not in seg_bytes, "the erasure did not reach the segment"


def test_the_pre_rows_backup_with_list_vectors_is_reached_by_erasure(tmp_path, monkeypatch):
    """A JSON store with list vectors is converted to rows on open, and the original is kept as
    `<store>.pre-rows.bak`. That backup holds the vector as a JSON list, so the scan looks for both
    encodings."""
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    m = _store(tmp_path)
    gone = m.remember("the subject's phone number is 0905 123 456", key="phone")
    m.remember("the deploy window is Tuesday", key="deploy")
    m.flush()
    vec = next(r["vec"] for r in m.items if r["id"] == gone)
    as_list = json.dumps(vec[3])                     # one value as the JSON writer prints it
    monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT")
    m2 = _store(tmp_path)
    assert rows.looks_like_sqlite(str(tmp_path / "m.json")), "precondition: the store was converted to rows"
    assert any(n.endswith(".pre-rows.bak") for n in os.listdir(tmp_path)), "precondition: the backup exists"
    assert _where(tmp_path, as_list), "control: the list vector is in the backup before the erasure"

    m2.forget(gone)
    m2.flush()
    assert _where(tmp_path, as_list) == [], "the erased record's list vector survived in a file"
    assert _where(tmp_path, _b64(vec)) == [], "the erased record's float16 vector survived in a file"
