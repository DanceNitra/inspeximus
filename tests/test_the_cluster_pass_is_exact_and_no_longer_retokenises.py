"""3.4.0: `_cluster_active` scores only the clusters that can reach the threshold and tokenises each
text once; the clusters it returns are the ones the 3.3.0 pass returned.

Measured on a copy of a live store (probes/the_cluster_pass_is_quadratic_and_the_fix_is_exact.py,
2026-09-21, 3,368 active records, lexical mode): the old pass took 10.4 / 39.0 / 162.5 s on prefixes
of 500 / 1,000 / 2,000 and the new one 0.08 / 0.46 / 0.92 s, with identical clusters on every prefix;
sleep() on the whole store went from Crew OS's 305 to 378 s to 2.0 s.

Two properties, each with a control: (1) identity with the old algorithm on a synthetic store whose
texts share vocabulary so clusters of several members form, including pairs whose overlap sits
EXACTLY on the threshold (a strict bound would drop those and the test would see it); (2) the number
of `_tokens` calls during one pass is at most the number of records (the old pass made one per
comparison: 3,343 records became 5.6 million tokenisations).
"""
from __future__ import annotations

import random

import inspeximus.core as core
from inspeximus import Inspeximus

WORDS = ["release", "publish", "workflow", "trusted", "deploy", "channel", "staging", "database", "rollback",
         "incident", "budget", "quota", "vector", "recall", "memory", "receipt", "ledger", "tenant", "erase"]


def _old(store, active, sim_threshold=0.5):
    """The 3.3.0 pass, verbatim except that `active` is passed in."""
    cents = []
    for r in active:
        rvec = store._qvec(r["text"])
        best = None
        for c in cents:
            s = store._similarity(c["rec"]["text"], r, c["vec"])
            if s >= sim_threshold and (best is None or s > best[1]):
                best = (c, s)
        if best:
            best[0]["members"].append(r)
        else:
            cents.append({"rec": r, "vec": rvec, "members": [r]})
    return [c["members"] for c in cents]


def _store(tmp_path, n=300, seed=7):
    rng = random.Random(seed)
    m = Inspeximus(str(tmp_path / "mem.json"))
    for i in range(n):
        k = rng.choice([2, 3, 4, 6])
        text = " ".join(rng.sample(WORDS, k)) + f" {i % 17}"
        m.remember(text, value=rng.random())
    return m


def _ids(clusters):
    return [[r["id"] for r in c] for c in clusters]


def test_the_new_pass_returns_exactly_the_old_clusters(tmp_path):
    m = _store(tmp_path)
    active = sorted([r for r in m.items if r["status"] == "active"], key=lambda r: -r["value"])
    for thr in (0.5, 0.34, 0.75):
        old = _ids(_old(m, active, thr))
        m._tok_cache.clear()
        new = _ids(m._cluster_active(thr))
        assert new == old, f"threshold {thr}: the clusters differ"
        assert any(len(c) > 1 for c in old), "the fixture must form real clusters or identity is vacuous"


def test_pairs_exactly_on_the_threshold_are_kept(tmp_path):
    """Overlap == threshold must pass (`>=`), so the candidate bound must be `>=` too. Two four-token
    texts sharing exactly two tokens sit on 0.5: a strict bound would never score them."""
    m = Inspeximus(str(tmp_path / "mem.json"))
    m.remember("release publish workflow trusted", value=0.9)
    m.remember("release publish staging database", value=0.5)
    clusters = m._cluster_active(0.5)
    assert len(clusters) == 1 and len(clusters[0]) == 2
    assert _ids(clusters) == _ids(_old(m, sorted(m.items, key=lambda r: -r["value"]), 0.5))


def test_one_pass_tokenises_each_text_at_most_once(tmp_path, monkeypatch):
    m = _store(tmp_path, n=200)
    calls = {"n": 0}
    real = core._tokens

    def counting(text):
        calls["n"] += 1
        return real(text)

    monkeypatch.setattr(core, "_tokens", counting)
    m._tok_cache.clear()
    m._cluster_active(0.5)
    n = sum(1 for r in m.items if r["status"] == "active")
    assert calls["n"] <= n, f"{calls['n']} tokenisations for {n} records: the centroid is tokenised per comparison again"


def test_consolidate_clusters_tokenises_each_member_once(tmp_path, monkeypatch):
    """The same class at the second site: the anchor `a` was re-tokenised for every partner `b`."""
    m = Inspeximus(str(tmp_path / "mem.json"))
    for i in range(20):
        m.remember(f"release publish workflow trusted {i}", value=0.5 + i / 100)
    texts = {r["text"] for r in m.items}
    calls = {"n": 0}
    real = core._tokens

    def counting(text):
        # `_value_clash` tokenises the number-stripped text of a near-duplicate pair, a different string
        # that no record cache can serve; it is gated behind the 0.82 similarity and is not this class.
        if text in texts:
            calls["n"] += 1
        return real(text)

    monkeypatch.setattr(core, "_tokens", counting)
    m._tok_cache.clear()
    m.consolidate_clusters(threshold=5, cluster_sim=0.5)
    assert calls["n"] <= 20, f"{calls['n']} tokenisations of record texts for 20 records"
