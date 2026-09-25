"""An erasure holds in every handle on the store, and leaves nothing of the record in any of them.

Two defects of one class, both measured on 3.9.6 before the fix:

1. `forget` dropped the erased record's token set and signature from the handle's caches but not its
   BM25 term-frequency map (`_tc_cache`). After `forget(ids=[x])` the handle still held
   `{'courier': 1, 'password': 1, 'zyxwvq': 1}` for the erased record: the erased text, as terms, in
   process memory. `shred` cleared only the token set, so the signature and term maps outlived the key.

2. The tombstone sidecar had no merge with a peer. Tombstones were read once, when the store opened, and
   every flush wrote this handle's whole list over the file. So with two handles on one store (the MCP
   server and the Claude Code hooks, since 3.9.6 share one), handle B's `forget(x)` was undone by handle
   A: A's reload did not see B's tombstone, re-added x as its own unsaved write, and saved it back to
   disk. A's next `forget` then overwrote the sidecar and B's proof of the erasure was gone as well.
   Measured: sidecar ['x'] after B, ['y'] after A, and x on disk again.

The walk in `_residue` reads every attribute of the handle, so a cache added later is checked without a
test naming it. Its positive control asserts the walk finds the term BEFORE the erasure.
"""
import hashlib
import json
import os

import pytest

from inspeximus import Inspeximus, verify_erasure_certificate

TERM = "zyxwvq"


def _embed(text):
    h = hashlib.sha256(text.encode()).digest()
    return [b / 255 for b in h[:16]]


def _walk(o, seen, hits, path):
    if id(o) in seen:
        return
    seen.add(id(o))
    if isinstance(o, str):
        if TERM in o:
            hits.append(path)
    elif isinstance(o, dict):
        for k, v in o.items():
            _walk(k, seen, hits, path)
            _walk(v, seen, hits, path)
    elif isinstance(o, (list, tuple, set, frozenset)):
        for v in o:
            _walk(v, seen, hits, path)


def _residue(m):
    """Names of the handle's attributes that still hold the erased term anywhere inside them."""
    hits = []
    for name, value in vars(m).items():
        _walk(value, set(), hits, name)
    return sorted(set(hits))


def _warm(m):
    for i in range(3):
        m.remember(f"filler note number {i} about the courier")
    x = m.remember(f"the courier password is {TERM}")
    m.recall(f"courier {TERM}", mode="hybrid")      # fills the BM25 term maps
    m.recall(f"courier {TERM}", mode="lexical")     # fills the token sets
    m._rec_sig(next(r for r in m._items if r["id"] == x))
    return x


def _ids_on_disk(path):
    return {r["id"] for r in Inspeximus(path)._items}


def test_forget_leaves_no_term_of_the_erased_record_in_the_handle(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"), embed=_embed)
    x = _warm(m)
    assert "_tc_cache" in _residue(m), "control: the walk must see the term map before the erasure"
    m.forget(ids=[x])
    assert _residue(m) == []
    # The ranking control: over the live corpus the erased term scores nothing.
    assert not any(m._bm25_scores({TERM}, list(m._items)))
    assert x not in [r["id"] for r in m.recall(TERM, mode="hybrid", k=10)]


def test_shred_leaves_no_term_in_the_handle(tmp_path):
    pytest.importorskip("cryptography")
    m = Inspeximus(str(tmp_path / "s.json"), embed=_embed, encrypt_passphrase="correct horse")
    _warm(m)
    assert _residue(m), "control: the walk must see the term before the shred"
    m.shred()
    assert _residue(m) == []


def test_a_peer_erasure_is_not_resurrected_by_a_handle_that_refreshes(tmp_path):
    p = str(tmp_path / "s.json")
    a = Inspeximus(p, embed=_embed)
    x = _warm(a)
    Inspeximus(p).forget(ids=[x])
    assert x not in _ids_on_disk(p), "control: the peer's erasure reached the disk"
    a.refresh()
    assert x not in {r["id"] for r in a._items}
    assert _residue(a) == []
    a.remember("another courier note")
    assert x not in _ids_on_disk(p)


def test_a_peer_erasure_is_not_resurrected_by_a_handle_that_never_refreshed(tmp_path):
    p = str(tmp_path / "s.json")
    a = Inspeximus(p, embed=_embed)
    x = _warm(a)
    Inspeximus(p).forget(ids=[x])
    a.remember("another courier note")              # the save finds the file moved and merges with it
    assert x not in _ids_on_disk(p)
    assert x not in {r["id"] for r in a._items}


@pytest.mark.parametrize("refresh_first", [True, False], ids=["refreshed", "stale handle"])
def test_a_peer_tombstone_survives_another_handles_erasure(tmp_path, refresh_first):
    p = str(tmp_path / "s.json")
    a = Inspeximus(p)
    x = a.remember("record x")
    y = a.remember("record y")
    Inspeximus(p).forget(ids=[x], request_id="R-peer")
    if refresh_first:
        a.refresh()
    a.forget(ids=[y], request_id="R-mine")
    side = json.loads(open(p + ".tombstones.json", encoding="utf-8").read())
    assert sorted(t["memory_id"] for t in side) == sorted([x, y])
    assert x not in _ids_on_disk(p) and y not in _ids_on_disk(p)
    fresh = Inspeximus(p)
    for rid in ("R-peer", "R-mine"):
        cert = fresh.erasure_certificate(rid)
        assert cert["count"] == 1
        assert verify_erasure_certificate(cert, store_path=p)["valid"], rid


def test_a_handle_opened_before_a_peers_erasure_chains_its_own_after_it(tmp_path):
    """b opened before a's erasure, so its in-memory chain is empty. Its tombstone must land at seq 1 on
    a's tip, and a's tombstone must stay exactly as a wrote it."""
    p = str(tmp_path / "s.json")
    a = Inspeximus(p)
    x = a.remember("record x")
    y = a.remember("record y")
    b = Inspeximus(p)
    a.forget(ids=[y], request_id="R-a")
    old = a._tombstones[-1]["hash"]
    # b never saw a's tombstone and writes its own on the same prev: a's must survive and move
    b.forget(ids=[x], request_id="R-b")
    side = json.loads(open(p + ".tombstones.json", encoding="utf-8").read())
    assert [t["memory_id"] for t in side] == [y, x]
    assert [t["seq"] for t in side] == [0, 1]
    assert side[1]["prev"] == side[0]["hash"]
    assert side[0]["hash"] == old
    assert "rechained_from" not in side[0]
