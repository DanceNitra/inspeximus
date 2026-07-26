"""The chain proves nobody rewrote the past. It never said the new entries were ones you asked for.

That is the whole of the post-compromise gap (Schneier & Kelsey, USENIX Security 1998): once an attacker
can write, appended entries are attacker-chosen and internally valid. Laundering an edited record costs
exactly ONE extra receipt, and the chain cannot mark it as unexpected, because from the chain's point of
view it is an ordinary amendment.

The missing piece is a DENOMINATOR, and only the application has it. `explain_growth(prior_anchor,
writes=, amendments=, erasures=)` reconciles what the chain actually grew by against what the caller says
it did, and itemises the surplus — seq and memory_id, so an operator gets somewhere to look.

Its limits are asserted here as deliberately as its behaviour: it is blind to substitution that appends
nothing (that is `bind_content`'s job), and a caller who passes whatever makes it pass has built a gate
that cannot fail.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus


def _store():
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), receipts=True)
    rid = m.remember("Revenue is 100M", mtype="semantic")
    for _ in range(20):
        m.credit([rid], outcome=True)
    assert next(r for r in m.items if r["id"] == rid)["mtype"] == "semantic", \
        "fixture must graduate, or slash() emits no amendment and the case under test never occurs"
    m.flush()
    return m, rid, m.anchor()


def test_accounted_growth_reconciles():
    m, _rid, a = _store()
    m.remember("two")
    m.remember("three")
    res = m.explain_growth(a, writes=2)
    assert res["ok"] is True, res["problems"]
    assert res["actual"] == {"writes": 2, "amendments": 0, "erasures": 0}


def test_an_unexplained_amendment_is_caught_and_located():
    """THE case: laundering costs one amendment the caller never made."""
    m, rid, a = _store()
    m.remember("two")
    m.remember("three")
    next(x for x in m._items if x["id"] == rid)["text"] = "Revenue is 900M"
    m._save(force=True)
    m.slash([rid], scope="memory")

    res = m.explain_growth(a, writes=2)
    assert res["ok"] is False
    assert len(res["unexplained"]) == 1, res["unexplained"]
    item = res["unexplained"][0]
    assert item["kind"] == "amendment" and item["memory_id"] == rid, item
    assert item["seq"] is not None, "an operator needs somewhere to look, not just a count"


def test_an_amendment_you_DID_make_reconciles():
    """The check must not fire on ordinary use, or it gets switched off."""
    m, rid, a = _store()
    m.slash([rid], scope="memory")
    res = m.explain_growth(a, writes=0, amendments=1)
    assert res["ok"] is True, res["problems"]


def test_an_unexplained_plain_write_is_caught():
    m, _rid, a = _store()
    m.remember("one the caller knows about")
    m.remember("one the caller does NOT")
    res = m.explain_growth(a, writes=1)
    assert res["ok"] is False
    assert [u["kind"] for u in res["unexplained"]] == ["write"], res["unexplained"]


def test_missing_receipts_are_reported_as_their_own_problem():
    """Fewer receipts than accounted for is not the same failure as more, and must not read as one."""
    m, _rid, a = _store()
    m.remember("only one write happened")
    res = m.explain_growth(a, writes=5)
    assert res["ok"] is False
    assert any("LESS than you accounted for" in p for p in res["problems"]), res["problems"]


def test_a_rewritten_past_is_reported_separately_from_growth():
    """Growth reconciliation and append-only are different properties; conflating them hides which broke."""
    m, _rid, a = _store()
    m._receipts[0]["ts"] = 1.0                      # rewrite history, breaking the witnessed prefix
    res = m.explain_growth(a, writes=0)
    assert res["prefix_intact"] is False
    assert any("no longer re-derives" in p for p in res["problems"]), res["problems"]


def test_erasures_are_reconciled_too():
    m, rid, a = _store()
    m.forget(ids=[rid])
    assert m.explain_growth(a, erasures=1)["ok"] is True
    res = m.explain_growth(a, erasures=0)
    assert res["ok"] is False
    assert any("erasure" in p for p in res["problems"]), res["problems"]


def test_it_is_blind_to_substitution_that_appends_nothing():
    """Stated in the docstring and asserted here, because a check whose limits are not pinned gets used
    as though it had none. Editing a record without touching the chain is bind_content's job."""
    m, rid, a = _store()
    next(x for x in m._items if x["id"] == rid)["text"] = "Revenue is 900M"
    m._save(force=True)

    assert m.explain_growth(a, writes=0)["ok"] is True, \
        "no receipt was appended, so growth reconciliation has nothing to see -- by design"

    from inspeximus.audit_bundle import bind_content, build_bundle
    assert bind_content(build_bundle(m), list(m.items))["ok"] is False, \
        "and the content check is what catches this one"
