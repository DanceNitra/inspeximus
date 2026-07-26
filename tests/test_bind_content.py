"""An auditor could verify the chain and never the content. `bind_content` closes that.

The gap, named by an adversarial review of this project's own audit story: no artifact bound content
across time for someone OUTSIDE the store. `verify_bundle` proves the chain is internally consistent and
matches the signed anchor -- and the bundle is content-free by design, carrying hashes and never text. So
an auditor holding only a bundle can be shown a clean chain over substituted content.

That is not hypothetical here: it is exactly the shape produced by an out-of-band edit followed by a
legitimate amendment, which is how a public `slash()` under <=1.67 cleared this library's own tamper
alarm.

`bind_content(bundle, store_items)` re-derives each record's commitment and compares it to the EARLIEST
receipt covering that record -- deliberately not the latest, because the latest is precisely what an
amendment rewrites.

Prior art for the class, cited rather than reinvented: RFC 6962's inclusion-is-not-validity (a log proves
a thing was logged, never that it was true) and Schneier & Kelsey, USENIX Security 1998, on post-
compromise entries being attacker-chosen by construction. The contribution here is not the principle; it
is that the check is now a function an auditor can run.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus
from inspeximus.audit_bundle import bind_content, build_bundle, verify_bundle


def _store():
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), receipts=True)
    rid = m.remember("Revenue is 100M", mtype="semantic", source={"doc": "bigfour-auditor.com"})
    m.remember("an unrelated fact that must stay clean")
    m.flush()
    return m, rid


def test_an_untouched_store_binds():
    m, _rid = _store()
    res = bind_content(build_bundle(m), list(m.items))
    assert res["ok"] is True, res["problems"]
    assert res["checked"] == 2 and not res["mismatched"]


def test_it_catches_content_substitution_the_chain_cannot_see():
    """THE case. The chain is intact, the anchor matches, verify_bundle passes on the store's own new
    bundle -- and the content is not what was committed."""
    m, rid = _store()
    witnessed = build_bundle(m)                       # what the auditor took away

    next(r for r in m._items if r["id"] == rid)["text"] = "Revenue is 900M"
    m._save(force=True)

    res = bind_content(witnessed, list(m.items))
    assert res["ok"] is False
    assert [x["memory_id"] for x in res["mismatched"]] == [rid], res
    assert any("no longer match" in p for p in res["problems"])


def test_it_compares_against_the_FIRST_receipt_not_the_latest():
    """An amendment rewrites the latest commitment, so comparing against it would bless the forgery --
    which is how the original defect worked."""
    m, rid = _store()
    witnessed = build_bundle(m)

    next(r for r in m._items if r["id"] == rid)["text"] = "Revenue is 900M"
    m._save(force=True)
    m.slash([rid], scope="memory")                    # appends a receipt over the FORGED text

    fresh = build_bundle(m)
    assert bind_content(fresh, list(m.items))["ok"] is False, \
        "even a bundle exported AFTER the laundering must fail, because the first receipt still binds"
    assert bind_content(witnessed, list(m.items))["ok"] is False


def test_a_record_added_out_of_band_is_reported_but_is_not_a_mismatch():
    """Distinguish 'the store grew' from 'the content changed' -- conflating them makes the check noise."""
    m, _rid = _store()
    witnessed = build_bundle(m)
    m.remember("a record written after the bundle was taken")

    res = bind_content(witnessed, list(m.items))
    assert res["ok"] is True, "later writes are not tampering"
    assert len(res["unreceipted"]) == 1, res
    assert any("out of band" in p or "predates" in p for p in res["problems"])


def test_a_record_missing_from_the_store_is_reported_separately():
    m, rid = _store()
    witnessed = build_bundle(m)
    m.forget(ids=[rid])

    res = bind_content(witnessed, list(m.items))
    assert rid in res["orphaned"], res
    assert any("tombstone" in p for p in res["problems"]), \
        "a legitimate erasure leaves one, so the message must not read as tampering"


def test_an_empty_bundle_is_refused_rather_than_passed():
    res = bind_content({"write_chain": []}, [{"id": "x", "text": "anything"}])
    assert res["ok"] is False and res["checked"] == 0
    assert res["problems"]


def test_attribution_substitution_is_caught_too():
    """A RELABEL changes who a record is attributable to without touching its text."""
    m, rid = _store()
    witnessed = build_bundle(m)
    next(r for r in m._items if r["id"] == rid)["taint"] = ["someoneelse"]
    m._save(force=True)

    res = bind_content(witnessed, list(m.items))
    assert res["ok"] is False
    assert res["mismatched"][0]["field"] in ("attrib_sha256", "immutable_sha256"), res["mismatched"]


def test_the_auditors_own_bundle_verifies_while_the_content_no_longer_binds():
    """The two answer different questions, and this is the case that shows it.

    A freshly exported bundle happens to fail here for a reason that is NOT the chain: `build_bundle`
    records the store's own `verify_writes` verdict at export time, so an honest exporter reports its own
    failure. That is a SELF-REPORT -- the module's own docstring calls those checks advisory -- and it is
    worth nothing against an exporter whose store already reads clean, which is precisely what laundering
    produces.

    So the discriminating case is the auditor's OLD bundle: it verifies completely (chain, anchor, and a
    self-report that was true when taken), and binding it to the store as it stands today fails.
    """
    m, rid = _store()
    witnessed = build_bundle(m)                       # taken while everything was honest
    assert verify_bundle(witnessed)["ok"] is True

    next(r for r in m._items if r["id"] == rid)["text"] = "Revenue is 900M"
    m._save(force=True)

    assert verify_bundle(witnessed)["ok"] is True, \
        "the bundle the auditor holds is untouched, so chain-only verification still passes"
    assert bind_content(witnessed, list(m.items))["ok"] is False, \
        "and binding it to the store catches what the chain alone cannot"
