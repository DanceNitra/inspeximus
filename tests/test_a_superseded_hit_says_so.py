"""A caller who asks for history gets rows that look exactly like current facts.

`include_superseded=True` returns retired records, but a returned hit carried NOTHING that
distinguished one: `status` was absent and `stale_derived` was False on superseded and active rows
alike. No other read surface closes the gap either -- there is no `get(id)`, `provenance()` and
`history()` take a KEY rather than a record id, and `supersession_report()` returns aggregate counts.
So the only way to tell was to hold the store and match ids against `.items`, which is not something
a consumer of `recall()` can do.

WHY IT MATTERS, measured 2026-08-18 (540 probes, deterministic word-boundary scoring, no judge):
handed the SAME retrieved rows, an answerer scored

    presentation                              current-value Q     history Q
    superseded rows last, unlabelled (shipped)      0.075            0.333
    same rows, moved to the front                   0.906            0.852
    same rows, MARKED as superseded                 1.000            1.000

Unlabelled, the model answers a history question with the CURRENT value on 100% of probes and a
current-value question with a RETIRED one on 90.6%. Labelling alone fixes both completely. The fix
was unreachable through the public read surface, which is what these tests pin.

Same contract as `under_review`: the flag is present only when it is true, so the shape of an
ordinary hit is unchanged.
"""
from inspeximus import Inspeximus


def _store(n_filler: int = 400):
    """A store whose kept pool exceeds k, so the superseded row only appears via the explicit ask.

    The filler MUST share the query's vocabulary. A sibling test learned this the hard way: with
    unrelated filler the kept pool never reaches k, the defect never arises, and the test passes
    against the unpatched library.
    """
    m = Inspeximus(path=None)
    m.remember("the deploy target is staging", key="deploy-target")
    m.remember("the deploy target is production", key="deploy-target")   # supersedes the first
    for i in range(n_filler):
        m.remember(f"the deploy target rollout note {i} mentions deploy and target")
    return m


def _recall(m, **kw):
    return m.recall("deploy target", k=20, mode="lexical", reinforce=False, **kw) or []


def test_the_fixture_still_puts_a_superseded_row_in_the_window():
    """CONTROL. If the explicit ask stops returning a retired row, every assertion below is vacuous
    and would go green while testing nothing."""
    m = _store()
    hits = _recall(m, include_superseded=True)
    sup_ids = {r["id"] for r in m.items if (r.get("status") or "") == "superseded"}
    assert sup_ids, "fixture built no superseded record"
    assert [h for h in hits if h["id"] in sup_ids], (
        "the explicit ask returned no superseded row -- the case these tests exist for did not arise"
    )


def test_a_superseded_hit_is_identifiable_without_the_store():
    """The load-bearing one: tell them apart using ONLY what recall returned."""
    m = _store()
    hits = _recall(m, include_superseded=True)
    sup_ids = {r["id"] for r in m.items if (r.get("status") or "") == "superseded"}
    flagged = [h for h in hits if h.get("superseded")]
    assert flagged, "no returned hit reported itself as superseded"
    assert {h["id"] for h in flagged} == {h["id"] for h in hits if h["id"] in sup_ids}, (
        "the flag does not agree with the store's own status"
    )
    # and the retired value is reachable from the flagged hit alone
    assert any("staging" in h["text"] for h in flagged)


def test_an_active_hit_is_not_flagged():
    """The other direction. A flag that is always on is not a flag."""
    m = _store()
    hits = _recall(m, include_superseded=True)
    sup_ids = {r["id"] for r in m.items if (r.get("status") or "") == "superseded"}
    for h in hits:
        if h["id"] not in sup_ids:
            assert not h.get("superseded"), f"active record flagged as superseded: {h['text']!r}"


def test_the_default_read_returns_no_flagged_rows():
    """Without the explicit ask there is nothing retired to mark, so nothing may be marked."""
    m = _store()
    assert not [h for h in _recall(m) if h.get("superseded")]


def test_an_ordinary_hit_keeps_its_shape():
    """The key is absent rather than False on an ordinary hit, matching `under_review` and
    `resolved_over`, so a consumer comparing keys does not see a new field appear everywhere."""
    m = _store()
    for h in _recall(m):
        assert "superseded" not in h
