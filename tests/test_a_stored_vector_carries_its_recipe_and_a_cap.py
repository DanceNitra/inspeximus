"""A stored vector has the embedder's size cap, and carries the recipe it was made with (3.17).

F-40 (AUDIT-A, vec16 review): `decode_vec` accepted any length. A `vec16` of 5,000,000 half floats, a
10 MB string, decoded to a 160 MB list, and 40 such rows made a store take 72 s to open. The cap is the
one an embedder's answer has had since F-17: 16,384 numbers, checked on the text before it is decoded.
A JSON list over the cap is refused at load as well, which is the same defect in the older encoding.

THE RECIPE TAG. A vector made under another model ranks a query from this model as if the two spaces
were one. A release before 3.17 can leave one behind: it re-embeds under a new model, fails on one row,
and that row keeps the old model's `vec16`. So `vec16` is written as `<tag>:<base64>`, the tag naming the
embed recipe and dimension, and a vector whose tag is not this handle's recipe is not ranked.
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import sqlite_store as S  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

DIM = 8


def _b64(n, v=0.001):
    return base64.b64encode(struct.pack("<%de" % n, *([v] * n))).decode("ascii")


def _emb(text):
    h = sum(map(ord, text)) % 97
    return [round(((h * (i + 3)) % 97) / 97.0 - 0.5, 6) for i in range(DIM)]


def _docs(p):
    con = sqlite3.connect(str(p))
    try:
        return {json.loads(d)["id"]: json.loads(d) for (d,) in con.execute("SELECT doc FROM records")}
    finally:
        con.close()


def _set_doc(p, rid, doc):
    con = sqlite3.connect(str(p))
    con.execute("UPDATE records SET doc=? WHERE id=?", (json.dumps(doc, sort_keys=True), rid))
    con.commit()
    con.close()


def _open(p, embed_id="model-a", **kw):
    return Inspeximus(path=str(p), embed=_emb, embed_id=embed_id, persist_vectors=True, **kw)


def _seed(tmp_path, embed_id="model-a"):
    p = tmp_path / "m.json"
    m = _open(p, embed_id)
    ids = [m.remember("fact %d about the deploy window" % i, key="k%d" % i) for i in range(3)]
    m.flush()
    return p, ids


# ── F-40, AUDIT-A's checks taken in-tree ─────────────────────────────────────────────────────────────
def test_v1_a_vec16_with_an_absurd_dimension_is_refused_before_it_is_decoded():
    assert S.decode_vec(_b64(100_000)) is None, "a 100,000-dimension vec16 was decoded"


def test_control_a_normal_dimension_still_decodes():
    v = S.decode_vec(_b64(1024))
    assert v is not None and len(v) == 1024


def test_the_cap_is_the_embedders_cap_exactly():
    """At the cap it decodes; one number over, it does not. Pins the arithmetic of the length check."""
    assert S.decode_vec(_b64(S.MAX_VEC_LEN)) is not None
    assert S.decode_vec(_b64(S.MAX_VEC_LEN + 1)) is None


def test_a_json_list_vector_over_the_cap_is_not_ranked(tmp_path):
    """The same defect in the older encoding: a list row is read by json.loads and then ranked."""
    p, ids = _seed(tmp_path)
    d = _docs(p)[ids[0]]
    d.pop(S.VEC_KEY)
    d["vec"] = [0.001] * (S.MAX_VEC_LEN + 1)
    _set_doc(p, ids[0], d)
    got = {r["id"]: r.get("vec") for r in _open(p).items}
    assert got[ids[0]] is None, "an over-cap list vector was kept for ranking"
    assert got[ids[1]], "control: the other rows keep their vectors"


# ── the recipe tag ───────────────────────────────────────────────────────────────────────────────────
def test_the_tag_is_inside_vec16_and_not_a_field_of_its_own(tmp_path):
    p, ids = _seed(tmp_path)
    d = _docs(p)[ids[0]]
    tag, _, b64 = d[S.VEC_KEY].partition(":")
    assert tag == S.recipe_tag("model-a", DIM) and S.decode_vec(b64), d[S.VEC_KEY][:40]
    assert "vec_recipe" not in d, "a separate field would survive an older release that rewrites the vector"


def _leave_a_foreign_row(p, rid, recipe="model-a"):
    """The row an older release leaves behind: the store is under model-b, this one row still holds the
    vector it had under model-a. A store-wide recipe change is not this case; the open realigns that."""
    d = _docs(p)[rid]
    b64 = d[S.VEC_KEY].partition(":")[2]
    d[S.VEC_KEY] = "%s:%s" % (S.recipe_tag(recipe, DIM), b64)
    _set_doc(p, rid, d)
    return d


def test_a_vector_from_another_recipe_is_not_ranked_and_reembed_replaces_it(tmp_path):
    p, ids = _seed(tmp_path, "model-b")
    _leave_a_foreign_row(p, ids[0])
    m = _open(p, "model-b")
    got = {r["id"]: r.get("vec") for r in m.items}
    assert got[ids[0]] is None, "a vector made under model-a was kept for ranking under model-b"
    assert got[ids[1]] and got[ids[2]], "control: the rows made under model-b keep their vectors"
    ic = m.index_coherence()
    assert ic["foreign_recipe_vecs"] == 1, ic
    assert any("another embed recipe" in x for x in ic["problems"]), ic["problems"]
    out = m.reembed()
    assert out["reembedded"] == 1, out
    ic = m.index_coherence()
    assert ic["foreign_recipe_vecs"] == 0 and ic["problems"] == [], ic
    tag = _docs(p)[ids[0]][S.VEC_KEY].partition(":")[0]
    assert tag == S.recipe_tag("model-b", DIM), "reembed did not stamp the new recipe"


def test_control_the_same_recipe_keeps_every_vector(tmp_path):
    p, ids = _seed(tmp_path, "model-a")
    m = _open(p, "model-a")
    assert all(r.get("vec") for r in m.items), "a vector of the current recipe was dropped"
    assert m.index_coherence()["foreign_recipe_vecs"] == 0


def test_without_an_embed_id_nothing_is_judged(tmp_path):
    """No embed_id means the recipe is unknown: nothing is stamped and nothing is dropped."""
    p, ids = _seed(tmp_path, "model-a")
    m = Inspeximus(path=str(p), embed=_emb, persist_vectors=True)
    assert all(r.get("vec") for r in m.items)
    q = tmp_path / "n.json"
    n = Inspeximus(path=str(q), embed=_emb, persist_vectors=True)
    rid = n.remember("no recipe named")
    n.flush()
    assert ":" not in _docs(q)[rid][S.VEC_KEY], "a vector was stamped without a known recipe"


def test_a_list_an_older_release_wrote_beside_a_tagged_vec16_is_not_judged_by_the_old_tag(tmp_path):
    """An older release re-embedding under model-b writes a list and keeps the model-a vec16 beside it.
    The list wins and carries no tag, so the model-a tag must not take it out of ranking."""
    p, ids = _seed(tmp_path, "model-b")
    d = _leave_a_foreign_row(p, ids[0])
    d["vec"] = _emb("written by an older release under model-b")
    _set_doc(p, ids[0], d)
    m = _open(p, "model-b")
    assert next(r for r in m.items if r["id"] == ids[0])["vec"] == d["vec"]
