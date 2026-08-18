"""A caller who asks for superseded values and gets none has been told nothing.

`suppress_stale_values=True` withholds retired values from the default read and appended them after
the kept pool, with `include_superseded=True` documented as the way to see them again. The result is
truncated at `scored[:k]`, and in any store of real size the kept pool fills k on its own -- so the
withheld records never survived the cut.

MEASURED 2026-08-18 on a 2,979-record store: at k=20, k=50 and k=200 the `include_superseded` result
was byte-identical to the suppressed one; only at k=3000, the entire store, did 4 of 24 superseded
rows reappear. The flag was decoration at every k a caller would actually pass.

These tests pin both directions: the explicit ask must return something, and the default read must
stay clean.
"""
from inspeximus import Inspeximus


def _store(n_filler: int = 400):
    """A store whose kept pool comfortably exceeds any sane k, which is the whole point."""
    m = Inspeximus(path=None)
    m.remember("the deploy target is staging", key="deploy-target")
    m.remember("the deploy target is production", key="deploy-target")   # supersedes the first
    # The filler MUST match the query lexically. A first version used unrelated text, so only a
    # handful of records scored and the kept pool never reached k -- the tests then passed against
    # the unpatched library too, which is the "green suite that cannot tell the fix works from the
    # case never arises" failure. The defect needs len(_keep) >= k; these produce it.
    for i in range(n_filler):
        m.remember(f"the deploy target rollout note {i} mentions deploy and target")
    return m


def test_an_explicit_ask_returns_a_superseded_record_at_a_normal_k():
    m = _store()
    retired = {r["id"] for r in m.items if r.get("status") == "superseded"}
    assert retired, "fixture built no superseded record - the test would pass vacuously"
    hits = m.recall("deploy target", k=20, mode="lexical",
                    suppress_stale_values=True, include_superseded=True) or []
    assert {h.get("id") for h in hits} & retired, (
        "include_superseded returned no superseded record at k=20; it was appended behind a kept "
        "pool larger than k and truncated away")


def test_the_default_read_still_withholds_them():
    m = _store()
    retired = {r["id"] for r in m.items if r.get("status") == "superseded"}
    hits = m.recall("deploy target", k=20, mode="lexical",
                    suppress_stale_values=True, include_superseded=False) or []
    assert not ({h.get("id") for h in hits} & retired), (
        "a retired value leaked into the default read - suppression is the feature, not a hint")


def test_reserving_slots_does_not_starve_the_kept_pool():
    """The reservation is bounded: most of k must still be current records."""
    m = _store()
    retired = {r["id"] for r in m.items if r.get("status") == "superseded"}
    hits = m.recall("deploy target", k=20, mode="lexical",
                    suppress_stale_values=True, include_superseded=True) or []
    stale_n = len([h for h in hits if h.get("id") in retired])
    assert stale_n <= max(1, 20 // 4), f"{stale_n} of 20 slots went to retired values"
    assert len(hits) - stale_n >= 1, "the kept pool was starved out of the result"
