"""`current(key)` and the L1 behind it: fast on repeat, and wrong in none of the ways a cache can be.

THE THREE WAYS A KEY CACHE GOES WRONG, each with a test that fails when the guard is removed:
a scope leak (alice primes the entry, bob reads it), a stale hit (the record was superseded,
reverted, confirmed, forgotten or replaced by a peer process after it was cached), and an
unbounded size. The counters are checked too, because an L1 whose hits cannot be seen is one
nobody can tell apart from a scan.
"""
from __future__ import annotations

import os
import tempfile
import time

from inspeximus import Inspeximus


def _mk(**kw):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.json")
    return p, Inspeximus(path=p, **kw)


def test_a_repeat_read_is_a_hit_and_a_write_to_the_key_is_a_miss():
    p, ix = _mk()
    a = ix.remember("the deadline is Friday", key="deadline")
    assert ix.current("deadline")["id"] == a
    assert ix.current("deadline")["id"] == a
    s = ix.l1_stats()
    assert s["hits"] == 1 and s["misses"] == 1 and s["size"] == 1 and s["max_size"] == 2000
    b = ix.remember("the deadline is Monday", key="deadline")
    assert ix.current("deadline")["id"] == b                # not the cached, now superseded, record
    assert ix.l1_stats()["misses"] == 2
    assert ix.current("no-such-key") is None


def test_a_hit_costs_under_five_microseconds():
    p, ix = _mk(l1_auto_refresh=False)
    for i in range(200):
        ix.remember(f"fact {i}", key=f"k{i}")
    ix.current("k100")
    n = 20000
    t0 = time.perf_counter()
    for _ in range(n):
        ix.current("k100")
    per = (time.perf_counter() - t0) / n
    assert ix.l1_stats()["hits"] >= n
    assert per < 5e-6, f"{per * 1e6:.2f} us per hit"


def test_an_agent_never_gets_another_agents_entry_from_the_cache():
    p, ix = _mk()
    alice = ix.as_agent("alice")
    bob = ix.as_agent("bob")
    rid = alice.remember("alice's roadmap", key="roadmap")
    assert alice.current("roadmap")["id"] == rid             # primes (None, "alice", "roadmap")
    assert alice.current("roadmap")["id"] == rid             # a hit
    assert bob.current("roadmap") is None                    # a different cache key, a scoped miss
    ix.grant("bob", key="roadmap", by="alice")
    assert bob.current("roadmap")["id"] == rid
    assert bob.current("roadmap")["id"] == rid               # cached under bob now
    ix.revoke("bob", key="roadmap", by="alice")
    assert bob.current("roadmap") is None                    # the ACL write dropped the cache


def test_a_tenant_never_gets_another_tenants_entry():
    p, ix = _mk()
    acme, globex = ix.for_tenant("acme"), ix.for_tenant("globex")
    a = acme.remember("acme's on-call is Dana", key="on-call")
    g = globex.remember("globex's on-call is Lee", key="on-call")
    assert acme.current("on-call")["id"] == a and acme.current("on-call")["id"] == a
    assert globex.current("on-call")["id"] == g
    assert ix.l1_stats()["size"] == 2
    # A keyed write in ANY scope drops that key from every scope: acme's record is untouched and
    # still active, so only the counter can show that the entry was dropped and rebuilt.
    misses = ix.l1_stats()["misses"]
    globex.remember("globex's on-call is Sam", key="on-call")
    assert acme.current("on-call")["id"] == a
    assert ix.l1_stats()["misses"] == misses + 1


def test_a_status_flipped_in_place_is_not_served_from_the_cache():
    """The guard for every path that retires a record WITHOUT a keyed write and WITHOUT replacing
    the list: confirm() of a provisional record, a lineage retraction, a slash. They all flip
    `status` on the record object the L1 holds, so a hit has to re-read it. Driven directly on
    the record, which is what those paths do."""
    p, ix = _mk()
    a = ix.remember("v1", key="k")
    rec = ix.current("k")
    assert rec["id"] == a
    rec["status"] = "superseded"
    assert ix.current("k") is None


def test_a_forgotten_record_is_not_served_from_the_cache():
    p, ix = _mk()
    a = ix.remember("secret plan", key="plan", source={"doc": "u1"})
    assert ix.current("plan")["id"] == a
    ix.forget(ids=[a])
    assert ix.current("plan") is None


def test_a_revert_and_a_confirm_are_not_served_stale():
    p, ix = _mk()
    ix.remember("Frankfurt", key="region", object="frankfurt")
    ix.remember("Dublin", key="region", object="dublin")
    assert ix.current("region")["object"] == "dublin"
    ix.revert("region")
    assert ix.current("region")["object"] == "frankfurt"


def test_a_peer_processs_write_is_seen_within_the_refresh_window():
    p, ix = _mk()
    ix.remember("v1", key="k")
    assert ix.current("k")["text"] == "v1"
    peer = Inspeximus(path=p)
    peer.remember("v2", key="k")
    peer.flush()
    ix._l1_last_stat = 0.0                                   # the window has elapsed
    assert ix.current("k")["text"] == "v2"


def test_the_cache_is_bounded_and_invalidation_is_addressable():
    p, ix = _mk(l1_size=3)
    for i in range(6):
        ix.remember(f"v{i}", key=f"k{i}")
    for i in range(6):
        ix.current(f"k{i}")
    assert ix.l1_stats()["size"] == 3
    assert ix.l1_invalidate(key="k5") == 1
    assert ix.l1_invalidate() == 2
    ix.current("k1"); ix.current("k1")
    ix.l1_flush()
    assert ix.l1_stats() == {"hits": 0, "misses": 0, "size": 0, "max_size": 3, "auto_refresh": True}


def test_l1_size_zero_is_a_plain_scan_with_the_same_answers():
    p, ix = _mk(l1_size=0)
    a = ix.remember("x", key="k")
    assert ix.current("k")["id"] == a and ix.current("k")["id"] == a
    assert ix.l1_stats()["size"] == 0 and ix.l1_stats()["hits"] == 0
