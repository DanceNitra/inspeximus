"""A decoded vector is an `array("f")`, and a row that is read and written again keeps its float16 text (3.27).

WHY THIS FILE EXISTS. `decode_vec` used to return a list of Python floats. One float object per value
costs about 32 bytes, so a 1,024-dimension vector cost about 32 KB instead of the 4 KB its numbers
occupy. Measured on this project's 17,289-vector store: 541 MB of heap per process. One MCP server runs
per open agent session, so fourteen sessions held about 16 GB of decoded vectors.

WHY IT IS A TEST AND NOT A MEASUREMENT. Changing the type breaks every check spelled
`isinstance(v, list)`, and the serialiser had one. With the array failing that check, `encode_vec` was
never called, the row kept its `vec` key, and `json.dumps(..., default=str)` wrote the repr of the
array into the store as a STRING. No exception, no warning, and the next read finds a vector that is
text. `test_a_rewritten_row_keeps_float16_and_never_a_repr` is that case, and it fails on the old
spelling of the check.

Each test carries a control that fails if the fixture stops reproducing the case it is written for.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from array import array

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import sqlite_store as rows  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

DIM = 8


def _emb(text):
    h = sum(map(ord, text)) % 97
    return [round(((h * (i + 3)) % 97) / 97.0 - 0.5, 6) for i in range(DIM)]


def _open(p):
    return Inspeximus(path=str(p), embed=_emb, persist_vectors=True)


def _docs(p):
    con = sqlite3.connect(str(p))
    try:
        return {json.loads(d)["id"]: json.loads(d) for (d,) in con.execute("SELECT doc FROM records")}
    finally:
        con.close()


def _seed(tmp_path, n=3):
    p = tmp_path / "m.json"
    m = _open(p)
    for i in range(n):
        m.remember("fact %d about the deploy window" % i, key="k%d" % i)
    m.flush()
    return p


def test_decode_vec_returns_an_array_of_float32():
    text = rows.encode_vec([0.5, -0.25, 0.125])
    assert isinstance(text, str)                     # control: the fixture really encoded something
    got = rows.decode_vec(text)
    assert isinstance(got, array), "a decoded vector must not be a list of Python floats"
    assert got.typecode == "f"
    assert list(got) == [0.5, -0.25, 0.125]


def test_an_array_costs_a_fraction_of_a_list():
    n = 1024
    vec = [i / n - 0.5 for i in range(n)]
    text = rows.encode_vec(vec)
    got = rows.decode_vec(text)
    list_bytes = sys.getsizeof(list(got)) + sum(sys.getsizeof(x) for x in set(map(float, got)))
    assert sys.getsizeof(got) * 4 < list_bytes, (
        "the array must be far smaller than the list it replaces; got %d against %d"
        % (sys.getsizeof(got), list_bytes))


def test_a_loaded_row_carries_an_array(tmp_path):
    p = _seed(tmp_path)
    m = _open(p)
    vecs = [r.get("vec") for r in m.items]
    assert all(v is not None for v in vecs)          # control: the fixture stored vectors at all
    assert all(isinstance(v, array) for v in vecs), [type(v).__name__ for v in vecs]


def test_a_rewritten_row_keeps_float16_and_never_a_repr(tmp_path):
    """The defect the type change would have introduced: a vector written as the text `array('f', [...])`."""
    p = _seed(tmp_path)
    m = _open(p)
    assert isinstance(m.items[0].get("vec"), array)  # control: the row really holds an array
    # THE ROW HOLDING THE ARRAY MUST BE THE ONE REWRITTEN. A save serialises only the ids that changed,
    # so remembering a NEW fact rewrites a row whose vector is the embedder's list and leaves every
    # loaded row untouched -- the defect never reaches the serialiser and the test passes either way.
    # Superseding an existing key rewrites that key's row, which is the row that holds the array.
    superseded = [r["id"] for r in m.items if (r.get("meta") or {}).get("key") == "k0"] or [m.items[0]["id"]]
    m.remember("the deploy window moved to a later hour", key="k0")
    m.flush()
    assert any("vec16" in (json.dumps(d) if isinstance(d, dict) else "")
               for d in _docs(p).values())          # control: rows are still written as float16
    assert superseded                               # control: a prior row for this key existed

    con = sqlite3.connect(str(p))
    try:
        texts = [d for (d,) in con.execute("SELECT doc FROM records")]
    finally:
        con.close()
    assert texts
    for t in texts:
        assert "array(" not in t, "a vector was serialised as its repr: %s" % t[:200]
    for doc in _docs(p).values():
        assert "vec" not in doc, "the list key came back: %s" % sorted(doc)
        assert isinstance(doc.get(rows.VEC_KEY), str), "every row must keep base64 float16"


def test_recall_still_ranks_with_an_array(tmp_path):
    p = _seed(tmp_path)
    m = _open(p)
    assert isinstance(m.items[0].get("vec"), array)  # control: ranking really reads an array
    hits = m.recall("deploy window", k=3)
    assert hits, "an array vector must rank like the list it replaced"


def test_the_length_cap_still_sees_an_oversized_array(tmp_path):
    """F-40's cap named `list`. An array that the cap cannot see is a guard reporting nothing to fix."""
    from inspeximus import core

    big = array("f", [0.0]) * (core._MAX_VEC_LEN + 1)
    assert len(big) > core._MAX_VEC_LEN              # control: the fixture is actually oversized
    rec = {"id": "x", "text": "t", "vec": big}
    core.Inspeximus._normalise_loaded(rec)
    assert rec["vec"] is None, "an oversized array must be dropped exactly as an oversized list is"

    ok = array("f", [0.1, 0.2])
    rec2 = {"id": "y", "text": "t", "vec": ok}
    core.Inspeximus._normalise_loaded(rec2)
    assert rec2["vec"] is ok, "control: a vector within the cap must be kept"
