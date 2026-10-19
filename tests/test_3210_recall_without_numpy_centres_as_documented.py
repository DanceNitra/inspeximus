"""3.21, 1b: recall without numpy honours `center_embeddings`, as the numpy path always did.

`center_embeddings` is documented and on by default. On 3.20.0 only the numpy matrix subtracted the corpus mean; the
pure-Python path scored raw cosines, so a store ranked differently depending on whether numpy happened to be
installed (the integrations job has numpy, the base job does not). Measured by AUDIT-B on the crew-store copy
(audit_b_321_baseline.md section 6): centred, the pure path's hybrid MRR goes from 0.533 to 0.617, and its top 10
equals numpy's on 8 of 8 queries.
"""
import hashlib
import math
import random

import pytest

from inspeximus import core
from inspeximus.core import Inspeximus

DIM = 48
COMMON = [random.Random(11).gauss(0, 1) * 3 for _ in range(DIM)]   # an anisotropic embedder: one shared component
QUERIES = ["query %d" % i for i in range(8)]


def _emb(text):
    rnd = random.Random(int(hashlib.sha256(text.encode()).hexdigest()[:12], 16))
    return [c + rnd.gauss(0, 1) for c in COMMON]


def _store(path, n=400, center=True):
    h = Inspeximus(str(path), embed=_emb, persist_vectors=True)
    for i in range(n):
        h.remember("record %d about topic %d" % (i, i % 29))
    h.flush()
    h.center_embeddings = center
    return h


def _top(h, q, monkeypatch, numpy, k=10):
    with monkeypatch.context() as m:
        if not numpy:
            m.setattr(core, "_np", None)
        return [r["id"] for r in h.recall(q, k=k, mode="semantic")]


def _centred(q, v, mean):
    a = [x - y for x, y in zip(q, mean)]
    b = [x - y for x, y in zip(v, mean)]
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b)))


def _raw(q, v):
    return sum(x * y for x, y in zip(q, v)) / (math.sqrt(sum(x * x for x in q)) * math.sqrt(sum(x * x for x in v)))


def test_the_pure_path_ranks_as_the_numpy_path_with_centering_on(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    h = _store(tmp_path / "s.db")
    same = [q for q in QUERIES if _top(h, q, monkeypatch, True) == _top(h, q, monkeypatch, False)]
    assert same == QUERIES, "the pure path's top 10 differs from numpy's on %d of 8 queries" % (8 - len(same))


def test_the_pure_path_scores_the_centred_cosine(tmp_path, monkeypatch):
    """No numpy needed: the expected scores are computed here in plain arithmetic."""
    h = _store(tmp_path / "s.db")
    vecs = [r["vec"] for r in h.items if r.get("vec")]
    mean = [sum(c) / len(vecs) for c in zip(*vecs)]
    q = _emb("query 3")
    want = sorted(((_centred(q, r["vec"], mean), r["id"]) for r in h.items), reverse=True)[:10]
    raw = sorted(((_raw(q, r["vec"]), r["id"]) for r in h.items), reverse=True)[:10]
    assert [i for _, i in want] != [i for _, i in raw], "CONTROL: centring changes this fixture's top 10"
    assert _top(h, "query 3", monkeypatch, numpy=False) == [i for _, i in want]


def test_centering_off_is_the_3_20_pure_path_exactly(tmp_path, monkeypatch):
    h = _store(tmp_path / "s.db", center=False)
    with monkeypatch.context() as m:
        m.setattr(core, "_np", None)
        got = [(r["id"], r.get("relevance")) for q in QUERIES for r in h.recall(q, k=10, mode="semantic")]
        m.setattr(Inspeximus, "_pure_scorer", lambda self, qv: (lambda r: self._rec_cos(qv, r, core._norm(qv))))
        old = [(r["id"], r.get("relevance")) for q in QUERIES for r in h.recall(q, k=10, mode="semantic")]
    assert got == old, "with centering off the pure path no longer scores as 3.20.0 did"


def test_the_mean_follows_a_peers_reembed(tmp_path, monkeypatch):
    """Item 1's stale case on the pure path: the count does not move, the vectors do."""
    p = tmp_path / "s.db"
    held = _store(p, n=120)
    q = "query 5"
    target = next(r for r in held.items if r["text"] == "record 7 about topic 7")["id"]
    assert target not in _top(held, q, monkeypatch, numpy=False), "CONTROL: the target is not near the query yet"

    def peer_emb(text):
        return _emb(q) if text == "record 7 about topic 7" else [x * -1.0 for x in _emb(text)]
    peer = Inspeximus(str(p), embed=peer_emb, persist_vectors=True)
    peer.reembed(only_missing=False)
    peer.flush()
    assert held.refresh().get("changed"), "CONTROL: the held handle saw the peer's write"
    fresh = Inspeximus(str(p), embed=_emb, persist_vectors=True)
    fresh.center_embeddings = True
    want = _top(fresh, q, monkeypatch, numpy=False)
    assert want[0] == target, "CONTROL: a fresh handle ranks the re-embedded target first"
    assert _top(held, q, monkeypatch, numpy=False) == want, "the held handle ranked with the mean from before the reembed"


@pytest.mark.parametrize("center", [True, False])
def test_why_recalled_explains_with_the_score_recall_ranked_by(tmp_path, monkeypatch, center):
    h = _store(tmp_path / "s.db", center=center)
    monkeypatch.setattr(core, "_np", None)
    q = _emb("query 2")
    vecs = [r["vec"] for r in h.items if r.get("vec")]
    mean = [sum(c) / len(vecs) for c in zip(*vecs)]
    rid = h.recall("query 2", k=1, mode="semantic")[0]["id"]
    rec = next(r for r in h.items if r["id"] == rid)
    want = _centred(q, rec["vec"], mean) if center else _raw(q, rec["vec"])
    got = h.why_recalled("query 2", id=rid)["semantic"]
    assert got == round(max(0.0, want), 4), (got, want)
