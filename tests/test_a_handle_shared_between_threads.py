"""A handle shared between threads keeps recall's index and its derived caches consistent (3.19 item 5).

3.18's Known limits named it: recall's index and the norm cache were not safe for concurrent recall and write on one
handle. A re-run does not find these races (measured: 1,428 recalls beside a writer, 0 errors), so each test here holds
a window open with a stall at the exact point, the way 3.18's reclaim races were forced:

1. the prune iterates a cache another thread is filling;
2. two recalls extend the same index entry, and the second appends the tail again;
3. a norm computed before an in-place edit of the vector is stored after the edit dropped it.

The last test checks the other half of the design: the lock is never held while a save, an embed call, a refresh or a
reload runs, so a recall never waits for disk or the network behind it.
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

WORDS = "deploy window release slot hook daemon erasure receipt mutation guard stamp flush".split()


def _held(tmp_path, n=60, **kw):
    p = str(tmp_path / "s.db")
    m = Inspeximus(p, **kw)
    for i in range(n):
        m.remember("note %d on the %s %s" % (i, WORDS[i % len(WORDS)], WORDS[(i * 5) % len(WORDS)]))
    m.flush()
    h = Inspeximus(p, **kw)
    h._ix_held = True
    for _ in range(3):
        h.recall("deploy window", k=5)
    return h


def _in_thread(fn):
    err = []

    def run():
        try:
            fn()
        except Exception as e:                              # noqa: BLE001
            err.append(e)
    t = threading.Thread(target=run)
    t.start()
    return t, err


# ── 1. the prune against a cache being filled ──────────────────────────────────────────────────────────────────────
class _SlowIter(dict):
    """A cache whose Python-level iteration takes a moment per key: a large store, made visible. A C-level snapshot
    (`dict.keys`) does not go through it."""
    def __iter__(self):
        for k in dict.__iter__(self):
            time.sleep(0.002)
            yield k


def test_the_prune_survives_a_cache_filled_meanwhile(tmp_path):
    h = _held(tmp_path)
    for r in list(h._items):
        h._rec_tokens(r)
    h._tok_cache = _SlowIter(h._tok_cache)
    assert len(h._tok_cache) > 20, "CONTROL: the cache holds tokens to iterate"
    stop = threading.Event()

    def fill():
        i = 0
        while not stop.is_set():
            h._tok_cache["fake%d" % i] = {"x"}               # what _rec_tokens does for a record not cached yet
            i += 1
            time.sleep(0.0005)
    filler = threading.Thread(target=fill)
    filler.start()
    try:
        t, err = _in_thread(h._prune_derived_caches)
        t.join(30)
    finally:
        stop.set()
        filler.join(5)
    assert not err, "the prune failed beside a thread filling the cache: %r" % err


# ── 2. two recalls extending one index entry ───────────────────────────────────────────────────────────────────────
def test_two_recalls_extending_one_entry_add_the_tail_once(tmp_path):
    h = _held(tmp_path)
    assert h._recall_ix, "CONTROL: the handle holds an index entry"
    rid = h.remember("a fresh note on the deploy window")
    real = h._assess_read_guards
    entered = threading.Event()

    def slow(rec, *a, **k):
        if rec.get("id") == rid and not entered.is_set():
            entered.set()
            time.sleep(0.3)                                  # the first recall stops inside the extension
        return real(rec, *a, **k)
    h._assess_read_guards = slow
    t1, e1 = _in_thread(lambda: h.recall("deploy window", k=5))
    assert entered.wait(10), "CONTROL: the first recall reached the extension"
    t2, e2 = _in_thread(lambda: h.recall("deploy window", k=5))
    t1.join(30)
    t2.join(30)
    assert not e1 and not e2, (e1, e2)
    for entry in h._recall_ix.values():
        ids = [r.get("id") for r in entry["pool"]]
        assert len(ids) == len(set(ids)), "an index entry holds %d records twice" % (len(ids) - len(set(ids)))


# ── 3. a norm against an in-place edit of the vector ───────────────────────────────────────────────────────────────
def _embed(text):
    v = [0.0] * 8
    for w in text.split():
        v[hash(w) % 8] += 1.0
    v[0] += 0.5
    return v


def test_a_norm_computed_before_an_in_place_edit_is_not_kept(tmp_path, monkeypatch):
    h = _held(tmp_path, n=10, embed=_embed)
    rec = h._items[0]
    h._set_vec(rec, _embed(rec["text"]))
    vec = rec["vec"]
    assert isinstance(vec, list) and vec, "CONTROL: the record has a vector"
    h._vnorm.pop(rec["id"], None)
    real_norm = core._norm
    entered = threading.Event()

    def slow_norm(v):
        if v is vec and not entered.is_set():
            out = real_norm(v)                               # the norm of the vector before the edit
            entered.set()
            time.sleep(0.3)                                  # the edit lands here
            return out
        return real_norm(v)
    monkeypatch.setattr(core, "_norm", slow_norm)
    q = _embed("deploy window")
    t, err = _in_thread(lambda: h._rec_cos(q, rec, real_norm(q)))
    assert entered.wait(10), "CONTROL: the scorer is computing the norm"
    vec[0] = vec[0] + 7.0                                    # an in-place edit: same list, new values
    t.join(30)
    assert not err, err
    monkeypatch.setattr(core, "_norm", real_norm)
    want = core._cosine(q, rec["vec"])
    got = h._rec_cos(q, rec, real_norm(q))
    assert math.isclose(got, want, rel_tol=1e-12), "a stale norm was kept for the edited vector: %r != %r" % (got, want)


# ── the lock is never held across a save, an embed, a refresh or a reload ──────────────────────────────────────────
def test_the_lock_is_not_held_across_a_save_an_embed_or_a_read_of_the_file(tmp_path, monkeypatch):
    seen = {"save": [], "embed": [], "refresh": [], "reload": []}
    h = None

    def owned():
        lk = getattr(h, "_ix_lock", None)
        return bool(lk is not None and lk._is_owned())

    def embed(text):
        if h is not None:
            seen["embed"].append(owned())
        return _embed(text)
    h = _held(tmp_path, n=40, embed=embed)
    for name in ("_save", "refresh", "reload"):
        real = getattr(Inspeximus, name)

        def wrapped(self, *a, _real=real, _name=name.strip("_"), **k):
            if self is h:
                seen[_name].append(owned())
            return _real(self, *a, **k)
        monkeypatch.setattr(Inspeximus, name, wrapped)
    for i in range(3):
        h.remember("another note %d on the deploy window" % i)
        h.recall("deploy window", k=5, mode="hybrid")
        h.recall("release slot", k=5)
    h.flush()
    h.refresh()
    peer = Inspeximus(str(tmp_path / "s.db"), embed=_embed)
    peer.remember("a peer's note on the hook daemon")
    peer.flush()
    h.recall("hook daemon", k=5)
    h.reload()
    h.recall("hook daemon", k=5)
    assert seen["save"] and seen["embed"] and seen["refresh"] and seen["reload"], "CONTROL: each path ran: %r" % (
        {k: len(v) for k, v in seen.items()})
    held = {k: v.count(True) for k, v in seen.items() if any(v)}
    assert not held, "the index lock was held while these ran: %r" % held
