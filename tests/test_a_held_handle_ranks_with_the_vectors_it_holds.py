"""3.21: the numpy vector matrix is rebuilt when a vector changes, not only when the record count does.

On 3.20.0 `_vec_matrix` was keyed on `len(self.items)`. A held handle (an MCP server, an app) that took a peer's re-embedded
records through `refresh()` kept its old matrix, because the count had not moved, and ranked against the vectors from
before the reembed. The pure-Python path and a fresh handle ranked the new vectors. The same happened to an in-place edit
of a vector, which keeps the list's identity.
"""
import hashlib
import random

import pytest

from inspeximus import core
from inspeximus.core import Inspeximus


@pytest.fixture(autouse=True)
def _needs_numpy():
    """Inside a fixture, not at module level: the base CI job has no numpy, and a module-level guard would hide the
    whole file as one skip line (test_skip_census.py). Here each test skips by name."""
    pytest.importorskip("numpy")


DIM = 32
QUERY = "the stale matrix query"
TARGET = "record 5"


def _emb(text):
    rnd = random.Random(int(hashlib.sha256(text.encode()).hexdigest()[:12], 16))
    return [rnd.gauss(0, 1) for _ in range(DIM)]


def _peer_emb(text):
    """The peer's embedder: it gives the target record the query's own vector."""
    return _emb(QUERY) if text == TARGET else _emb(text)


def _store(path, n=120):
    h = Inspeximus(str(path), embed=_emb, persist_vectors=True)
    for i in range(n):
        h.remember("record %d" % i)
    h.flush()
    h.center_embeddings = False                       # the same cosine on both paths, so they can be compared
    return h


def _top(h, k=3):
    return [r["text"] for r in h.recall(QUERY, k=k, mode="semantic")]


def _pure_top(h, monkeypatch, k=3):
    with monkeypatch.context() as m:
        m.setattr(core, "_np", None)
        return _top(h, k)


@pytest.mark.parametrize("fmt", ["rows", "json"])
def test_a_peers_reembed_reached_through_refresh_is_ranked(tmp_path, monkeypatch, fmt):
    if fmt == "json":
        monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    path = tmp_path / ("s.json" if fmt == "json" else "s.db")
    held = _store(path)
    with open(path, "rb") as fh:
        head = fh.read(16)
    assert head.startswith(b"[" if fmt == "json" else b"SQLite format 3"), "CONTROL: the store is in the %s format" % fmt
    assert TARGET not in _top(held), "CONTROL: before the peer's reembed the target is not near the query"
    peer = Inspeximus(str(path), embed=_peer_emb, persist_vectors=True)
    assert peer.reembed(only_missing=False)["reembedded"] == 120
    peer.flush()
    n = len(held.items)
    assert held.refresh().get("changed"), "CONTROL: the held handle saw the peer's write"
    assert len(held.items) == n, "CONTROL: the record count did not change, which is the case the count key missed"
    assert _pure_top(held, monkeypatch)[0] == TARGET, "CONTROL: the pure path ranks the peer's vector"
    assert _top(held)[0] == TARGET, "the held handle ranked with its matrix from before the peer's reembed"


def test_an_in_place_edit_of_a_vector_is_ranked(tmp_path, monkeypatch):
    h = _store(tmp_path / "s.db")
    assert TARGET not in _top(h), "CONTROL: the target is not near the query yet"
    rec = next(r for r in h.items if r["text"] == TARGET)
    vec = rec["vec"]
    for i, x in enumerate(_emb(QUERY)):
        vec[i] = x                                    # the same list object: only the edit generation sees it
    assert rec["vec"] is vec, "CONTROL: the vector kept its identity"
    assert _pure_top(h, monkeypatch)[0] == TARGET, "CONTROL: the pure path ranks the edited vector"
    assert _top(h)[0] == TARGET, "the matrix kept the vector from before the in-place edit"


def test_the_matrix_is_kept_while_no_vector_changes(tmp_path):
    """The other direction: a recall, a credit or a text-free edit must not rebuild an unchanged matrix."""
    h = _store(tmp_path / "s.db")
    _top(h)
    built = h._mat
    assert built is not None, "CONTROL: the numpy matrix was built"
    first = h.recall(QUERY, k=1, mode="semantic")[0]
    h.credit(first["id"], True) if hasattr(h, "credit") else None
    rec = next(r for r in h.items if r["text"] == "record 7")
    rec.setdefault("meta", {})["note"] = "an edit that is not a vector"
    _top(h)
    assert h._mat is built, "the matrix was rebuilt although no vector changed"
