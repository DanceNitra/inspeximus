"""3.18 prototype: a held handle keeps recall's pool and a token index, and they never serve a stale answer.

The pool and the index are valid while the handle's content revision, its list object and length, and the pool arguments
stand. Every edit moves the revision (at any depth, through `_touch`, and on a status change), except a reader's note
(`_stale_derived`), which recall writes on every candidate and which is never stored. These tests take each path that can
change what recall may serve and check the next recall sees it; and they check the cache is actually used, or every one
of them would pass on a cache that is rebuilt each time.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import inspeximus.core as core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


def _store(tmp_path, n=30):
    p = str(tmp_path / "m.json")
    m = Inspeximus(path=p)
    for i in range(n):
        m.remember("note %d about the deploy window and the release train" % i, key="k%d" % i)
    m.remember("the zanzibar token appears only here", key="z")
    m.flush()
    return p


def _ids(m, q, k=10):
    return [h["id"] for h in m.recall(q, k=k)]


def _has(m, word):
    return any(word in str(h.get("text")) for h in m.recall(word, k=10))


def _entry(m):
    """The most recently built cache entry of the store (views share the store's dict)."""
    d = m._recall_ix
    return list(d.values())[-1] if d else None


def test_control_the_index_is_built_and_reused(tmp_path):
    m = Inspeximus(path=_store(tmp_path))
    m.recall("deploy window", k=5)
    ix = _entry(m)
    assert ix is not None and ix["post"] is not None, "CONTROL: the index was never built, so nothing here is tested"
    m.recall("release train", k=5)
    assert _entry(m) is ix and len(m._recall_ix) == 1, "the cache is rebuilt on every recall: there is nothing held"


def test_a_readers_note_does_not_invalidate_it(tmp_path):
    m = Inspeximus(path=_store(tmp_path))
    m.recall("deploy window", k=5)
    rev = m._rev
    m.recall("deploy window", k=5)                       # writes _stale_derived on every candidate
    assert m._rev == rev, "a reader's note moved the content revision, so the cache never survives a recall"


def test_a_new_write_is_found(tmp_path):
    m = Inspeximus(path=_store(tmp_path))
    assert not _has(m, "quokka")
    m.remember("a quokka visited the build server", key="q")
    assert _has(m, "quokka")


def test_an_erased_record_never_ranks(tmp_path):
    m = Inspeximus(path=_store(tmp_path))
    assert _has(m, "zanzibar"), "CONTROL: the record ranks before the erasure"
    rid = next(r["id"] for r in m.items if r.get("key") == "z")
    m.forget(rid)
    assert not _has(m, "zanzibar")


def test_a_superseded_value_never_ranks(tmp_path):
    m = Inspeximus(path=_store(tmp_path))
    assert _has(m, "zanzibar")
    m.remember("the key now says something else entirely", key="z")
    assert not _has(m, "zanzibar")


def test_a_retired_key_never_ranks(tmp_path):
    m = Inspeximus(path=_store(tmp_path))
    assert _has(m, "zanzibar")
    m.retire("z", "no longer true")
    assert not _has(m, "zanzibar")


def test_a_direct_status_edit_is_seen(tmp_path):
    m = Inspeximus(path=_store(tmp_path))
    assert _has(m, "zanzibar")
    next(r for r in m._items if r.get("key") == "z")["status"] = "superseded"
    assert not _has(m, "zanzibar")


def test_a_nested_edit_is_seen(tmp_path):
    """A project stamp written into meta moves the record out of another project's pool."""
    m = Inspeximus(path=_store(tmp_path))
    assert any("zanzibar" in str(h["text"]) for h in m.recall("zanzibar", k=10, project="alpha"))
    next(r for r in m._items if r.get("key") == "z")["meta"]["project"] = "beta"
    assert not any("zanzibar" in str(h["text"]) for h in m.recall("zanzibar", k=10, project="alpha"))


def test_a_peers_write_and_erasure_reach_a_held_handle_through_refresh_and_reload(tmp_path):
    p = _store(tmp_path)
    held = Inspeximus(path=p)
    assert not _has(held, "wombat")
    peer = Inspeximus(path=p)
    rid = peer.remember("a wombat was seen in the data centre", key="w")
    peer.flush()
    held.refresh()
    assert _has(held, "wombat"), "a peer's write is not served after refresh"
    peer.forget(rid)
    peer.flush()
    held.reload()
    assert not _has(held, "wombat"), "a peer's erasure is still served after reload"


def test_the_ranking_matches_the_unindexed_path(tmp_path):
    """Same records, same scores, same order of equal scores, for every question drawn from the store."""
    m = Inspeximus(path=_store(tmp_path))
    qs = ["deploy window", "release train", "note 7", "zanzibar token", "nothing matches this"]
    for q in qs:
        core._RECALL_INDEX_ON = False
        try:
            off = [(round(h["score"], 9), h["id"]) for h in m.recall(q, k=10)]
        finally:
            core._RECALL_INDEX_ON = True
        on = [(round(h["score"], 9), h["id"]) for h in m.recall(q, k=10)]
        assert sorted(off, key=lambda x: (-x[0], x[1])) == sorted(on, key=lambda x: (-x[0], x[1])), q


def _stamp_beta(m):
    next(r for r in m._items if r.get("key") == "z")["meta"]["project"] = "beta"


def test_a_field_removed_with_pop_is_seen(tmp_path):
    m = Inspeximus(path=_store(tmp_path))
    _stamp_beta(m)
    assert not any("zanzibar" in str(h["text"]) for h in m.recall("zanzibar", k=10, project="alpha")), "CONTROL"
    next(r for r in m._items if r.get("key") == "z")["meta"].pop("project")
    assert any("zanzibar" in str(h["text"]) for h in m.recall("zanzibar", k=10, project="alpha"))


def test_a_pool_built_for_other_arguments_is_not_served(tmp_path):
    m = Inspeximus(path=_store(tmp_path))
    _stamp_beta(m)
    assert not any("zanzibar" in str(h["text"]) for h in m.recall("zanzibar", k=10, project="alpha")), "CONTROL"
    assert _has(m, "zanzibar"), "the pool built for project alpha answered a question asked without a project"


def test_an_untracked_edit_declared_through_touch_is_seen(tmp_path):
    """The contract for code that writes a record without the tracked dict (a vector shelved with dict.__setitem__):
    the edit is declared through _touch, and the declaration alone must invalidate the cache."""
    m = Inspeximus(path=_store(tmp_path))
    assert _has(m, "zanzibar")
    rec = next(r for r in m._items if r.get("key") == "z")
    dict.__setitem__(rec, "status", "superseded")
    m._touch(rec)
    assert not _has(m, "zanzibar")
