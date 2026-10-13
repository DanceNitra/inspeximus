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


# ── 5. recall reads the edit log while another thread writes it ─────────────────────────────────────────────────────
class _RevisionThatLogs(int):
    """A logged revision whose comparison writes the log once: another thread's edit, logged by `_note_dirty` outside the
    handle lock, while recall reads the log."""
    store, fired = None, []

    def __gt__(self, other):
        if not _RevisionThatLogs.fired:
            _RevisionThatLogs.fired.append(1)
            _RevisionThatLogs.store._ix_log["an-edit-on-another-thread"] = int(self)
        return int.__gt__(self, other)


def test_recall_survives_an_edit_logged_while_it_reads_the_log(tmp_path, monkeypatch):
    """G-2 (3.19): recall read the edit log (`_ix_log`) by iterating it, under the handle lock, while edits write it from
    `_note_dirty` outside that lock. An edit on another thread in between raised "dictionary changed size during
    iteration" from recall(). Forced here: the log is written while recall iterates it, on the path that extends an
    index entry with appended records."""
    h = _held(tmp_path)
    assert h._recall_ix, "CONTROL: the held handle built an index entry"
    rid = h.remember("note new on the deploy window")
    monkeypatch.setattr(_RevisionThatLogs, "store", h)
    monkeypatch.setattr(_RevisionThatLogs, "fired", [])
    h._ix_log[rid] = _RevisionThatLogs(h._rev)
    hits = h.recall("deploy window", k=5)
    assert _RevisionThatLogs.fired, "CONTROL: recall did not read the log"
    assert hits, "recall returned nothing"


# ── 6. the token index, read and built beside an extension of its entry ─────────────────────────────────────────────
def test_a_recall_skips_index_positions_added_after_it_copied_its_pool(tmp_path, monkeypatch):
    """G-5 (3.19): a lexical recall copied the entry's pool under the handle lock, then read the token index outside it.
    Another recall that extended the entry in between added positions past the copy, and `pool[_i]` raised IndexError.
    Forced here: the extension runs as this recall tokenizes its query, after the copy and before the read."""
    h = _held(tmp_path)
    assert any(e.get("post") for e in h._recall_ix.values()), "CONTROL: the entry has its token index"
    real, fired = core._tokens, []

    def tokens(text):
        if text == "deploy window please" and not fired:
            fired.append(1)
            h.remember("note new on the deploy window")
            h.recall("deploy window", k=5)                 # extends the entry and its token index
        return real(text)
    monkeypatch.setattr(core, "_tokens", tokens)
    hits = h.recall("deploy window please", k=5)
    assert fired, "CONTROL: the extension ran inside the recall"
    assert hits, "recall returned nothing"


def test_a_token_index_built_beside_an_extension_keeps_the_appended_records(tmp_path, monkeypatch):
    """G-5 (3.19): the token index was built outside the handle lock. Another thread's recall that extended the entry
    meanwhile found no index to extend, and the build then stored an index of the pool it had read, without the
    appended records: a lexical recall never found them again. Forced here: the other thread appends a record and
    recalls while this recall builds the index."""
    h = _held(tmp_path)
    for e in h._recall_ix.values():
        e["post"] = None                                    # the next lexical recall builds the index
    real_tokens, real_rec = core._tokens, Inspeximus._rec_tokens
    armed, fired, done = [], [], threading.Event()

    def other():
        h.remember("note new on the quokka migration")
        h.recall("deploy window", k=5)                       # extends the entry
        done.set()

    def tokens(text):
        if text == "deploy window please":
            armed.append(1)                                  # the next record tokenized is the index build's
        return real_tokens(text)

    def rec_tokens(self, r):
        if self is h and armed and not fired:
            fired.append(threading.Thread(target=other))
            fired[0].start()
            done.wait(2)                                     # with the build under the lock, the other recall waits
        return real_rec(self, r)
    monkeypatch.setattr(core, "_tokens", tokens)
    monkeypatch.setattr(Inspeximus, "_rec_tokens", rec_tokens)
    h.recall("deploy window please", k=5)
    monkeypatch.undo()
    assert fired, "CONTROL: the other thread ran during the index build"
    fired[0].join(30)
    assert done.is_set(), "CONTROL: the other thread's recall finished"
    hits = h.recall("quokka migration", k=5)
    assert any("quokka" in r.get("text", "") for r in hits), "a record appended during the index build is not found"


# ── 7. the shared-state inventory (3.19): F1 to F4 ──────────────────────────────────────────────────────────────────
def test_words_read_before_an_edit_on_another_thread_are_not_kept(tmp_path, monkeypatch):
    """F1: the token caches were filled outside the handle lock. A fill that read a record's text before another thread
    edited it was stored after the edit had dropped the entry, so the record kept its old words in the index. Forced
    here: the edit runs on another thread while the fill tokenizes the old text."""
    h = _held(tmp_path)
    rid = h.remember("note on the marmot burrow")
    rec = [r for r in h._items if r["id"] == rid][0]
    h._tok_cache.pop(rid, None)
    real, started, done = core._tokens, [], threading.Event()

    def edit():
        rec["text"] = "note on the okapi enclosure"
        done.set()

    def tokens(text):
        if "marmot" in text and not started:
            started.append(threading.Thread(target=edit))
            started[0].start()
            done.wait(2)                                     # with the fill under the lock, the edit's drop waits
        return real(text)
    monkeypatch.setattr(core, "_tokens", tokens)
    h._rec_tokens(rec)
    monkeypatch.undo()
    assert started, "CONTROL: the edit ran during the fill"
    started[0].join(30)
    assert done.is_set() and rec["text"] == "note on the okapi enclosure", "CONTROL: the edit landed"
    assert "okapi" in h._rec_tokens(rec), "the record kept the words it had before the edit"


def test_a_semantic_recall_scores_with_one_build_of_the_vector_matrix(tmp_path, monkeypatch):
    """F2: the vector matrix, its row map and its mean were three attributes. A recall that computed its scores with one
    build and looked rows up in the map of a later build scored each record with another record's row. Forced here:
    a record is erased and the matrix rebuilt between the scores and the lookups."""
    pytest.importorskip("numpy")
    words = ["alpha", "bravo", "charlie", "delta"]

    def emb(text):
        return [1.0 if w in text else 0.0 for w in words]
    h = Inspeximus(str(tmp_path / "v.db"), embed=emb)       # one handle: vectors are not stored by default
    ids = {w: h.remember("the %s record" % w) for w in words}
    assert h.recall("delta", k=1, mode="semantic")[0]["id"] == ids["delta"], "CONTROL: delta ranks first"
    real_q, real_norm, armed, fired = Inspeximus._qvec, core._norm, [], []

    def qvec(self, *a, **k):
        armed.append(1)
        return real_q(self, *a, **k)

    def norm(v):
        if armed and not fired:
            fired.append(1)
            h.forget(ids["alpha"])                           # the first row goes; every later row moves up one
            h._vec_matrix()
        return real_norm(v)
    monkeypatch.setattr(Inspeximus, "_qvec", qvec)
    monkeypatch.setattr(core, "_norm", norm)
    top = h.recall("delta", k=1, mode="semantic")
    monkeypatch.undo()
    assert fired, "CONTROL: the matrix was rebuilt during the recall"
    assert top and top[0]["id"] == ids["delta"], "the recall scored records with rows of another build: %r" % (
        [r.get("text") for r in top],)


class _LogThatMoves(dict):
    """An edit log in which an id is edited again just before the prune pops it."""
    target, newer, fired = None, None, []

    def pop(self, k, *d):
        if k == _LogThatMoves.target and not _LogThatMoves.fired:
            _LogThatMoves.fired.append(1)
            dict.__setitem__(self, k, _LogThatMoves.newer)
        return dict.pop(self, k, *d)


def test_the_edit_log_prune_keeps_an_edit_logged_after_its_check(tmp_path, monkeypatch):
    """F3: recall prunes the edit log of ids every index entry has already seen. Edits write the log outside the handle
    lock, so an id checked at an old revision could be edited before the pop, and the pop lost that edit; the next
    recall then extended an entry over a record that had moved. Forced here: the id is edited between check and pop."""
    h = _held(tmp_path)
    log = _LogThatMoves(h._ix_log)
    log["an-old-edit"] = 0
    h._ix_log = log
    monkeypatch.setattr(_LogThatMoves, "target", "an-old-edit")
    monkeypatch.setattr(_LogThatMoves, "newer", h._rev + 1000)
    monkeypatch.setattr(_LogThatMoves, "fired", [])
    for e in h._recall_ix.values():
        e["rev"] = -1                                       # the entry is stale, so this recall rebuilds it and prunes
    h.recall("deploy window", k=5)
    assert _LogThatMoves.fired, "CONTROL: the prune reached the id"
    assert h._ix_log.get("an-old-edit") == h._rev + 1000, "the prune dropped an edit logged after its check"


class _KeyThatFills(str):
    """A cached key whose comparison fills the cache once: another thread's read, landing while a write drops a key."""
    __hash__ = str.__hash__
    cache, fired = None, []

    def __eq__(self, other):
        if _KeyThatFills.cache is not None and not _KeyThatFills.fired:
            _KeyThatFills.fired.append(1)
            _KeyThatFills.cache[(None, None, "filled-meanwhile")] = None
        return str.__eq__(self, other)


def test_dropping_a_key_from_the_read_cache_survives_a_fill_meanwhile(tmp_path, monkeypatch):
    """F4: a keyed write drops the key's entries from the read cache by iterating it, and a read on another thread
    filled it meanwhile: "mutated during iteration". Forced here: the cache is filled during the iteration."""
    h = _held(tmp_path)
    h._l1[(None, None, _KeyThatFills("deploy"))] = None
    h._l1[(None, None, "another key")] = None            # the iteration must have a next step to notice the fill
    monkeypatch.setattr(_KeyThatFills, "cache", h._l1)
    monkeypatch.setattr(_KeyThatFills, "fired", [])
    h._l1_drop_key("deploy")
    assert _KeyThatFills.fired, "CONTROL: the cache was filled during the drop"
    assert not [ck for ck in h._l1 if ck[2] == "deploy"], "the key's entry was not dropped"


def test_a_tenant_view_never_returns_another_tenants_records_read_meanwhile(tmp_path, monkeypatch):
    """F5: a view's attribute writes go to its parent, so every tenant view shared one cached view of the records,
    written as two attributes (the rows, then their key). A read by another tenant between the two left this tenant's
    key beside the other tenant's rows: this tenant was handed them, and read them again from the cache. Forced here:
    the other tenant reads between the two writes."""
    m = Inspeximus(str(tmp_path / "t.db"))
    acme, beta = m.for_tenant("acme"), m.for_tenant("beta")
    acme.remember("acme keeps the quarterly numbers")
    beta.remember("beta keeps the payroll file")
    real, fired = core._TenantView.__setattr__, []

    def setattr_then_other_tenant_reads(self, name, value):
        real(self, name, value)
        if self.tenant == "acme" and name.startswith("_items_view") and not fired:
            fired.append(1)
            beta.items                                       # another tenant's read, between this read's writes
    monkeypatch.setattr(core._TenantView, "__setattr__", setattr_then_other_tenant_reads)
    first = acme.items
    monkeypatch.undo()
    assert fired, "CONTROL: the other tenant read during this tenant's read"
    for got in (first, acme.items):
        assert {r.get("tenant") for r in got} == {"acme"}, "a tenant view returned %r" % (
            sorted((r.get("tenant"), r.get("text")) for r in got),)
