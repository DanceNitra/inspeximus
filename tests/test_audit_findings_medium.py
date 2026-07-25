"""The MEDIUM findings from the 2026-07-25 codebase audit.

Every one is the same defect in a different costume: a failure that arrived shaped like a success.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus


def _store(**kw):
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), receipts=True, **kw)


def _retracted():
    m = _store()
    root = m.remember("the db host is old.host", key="db::host", object="old.host",
                      source={"doc": "runbook"})
    m.remember("svc uses old.host for backups", derived=True, derived_from=[root], source={"doc": "ops"})
    m.retract_lineage("runbook")
    m.remember("the db host is new.host", key="db::host", object="new.host", source={"doc": "adr"})
    return m


def test_a_rewriter_that_RAISED_is_not_reported_as_nothing_to_do():
    """`except Exception: nt = None` folded a broken LLM into `skipped`, so the caller read 'paraphrased,
    nothing to do' and never retried."""
    def boom(text, old, new):
        raise RuntimeError("LLM down")

    res = _retracted().rederive("runbook", rewrite=boom)
    assert res["rederived"] == 0
    assert res.get("failed"), "a raising rewriter must be reported separately from a skipped record"
    assert "RuntimeError" in res["failed"][0]["error"]


def test_a_genuinely_unrewritable_record_is_still_just_skipped():
    """The counterpart — over-reporting failures would be its own noise."""
    res = _retracted().rederive("runbook", rewrite=lambda t, o, n: None)
    assert res["skipped"] == 1 and not res.get("failed")


def test_an_extractor_that_raises_does_not_silently_disable_supersession():
    """key=None means the write is unkeyed, so supersession never runs — and the store then looks exactly
    like one that was never keyed at all (supersession_report: 0)."""
    m = _store()
    m.extractor = lambda text: (_ for _ in ()).throw(ValueError("extractor exploded"))
    m.remember("the db host is a.host")
    m.remember("the db host is b.host")

    assert m.supersession_report()["superseded_total"] == 0        # the symptom
    ok, problems = m.verify_writes()
    assert ok is False
    assert any("extractor raised" in p for p in problems)          # ...is now explained


def test_selection_integrity_does_not_report_stable_when_the_seeds_match_nothing():
    """`stable = not displaced` is structurally True when the trusted recall returns nothing. Empty seeds
    already failed closed; WRONG seeds failed open, with the whole top-k untrusted."""
    m = _store()
    m.trust_seeds = {"trusted_doc"}                                # literal, not the canonical form
    m.remember("a fact about widgets", source={"doc": "trusted_doc"})

    res = m.selection_integrity("widgets")
    assert res["stable"] is None, "unknown must not be reported as stable"
    assert "match no record" in (res.get("note") or "")


def test_selection_integrity_still_reports_stable_when_the_seeds_are_right():
    m = _store()
    m.trust_seeds = {Inspeximus._canon_source("trusted_doc")}
    m.remember("a fact about widgets", source={"doc": "trusted_doc"})
    assert m.selection_integrity("widgets")["stable"] is True


def test_verify_claim_catches_a_retired_value_when_the_store_never_recorded_object():
    """`object=` is optional on remember(), so most real stores are in this state. The flagship failure case
    — an agent re-asserting a retired password — verdicted `supported`."""
    m = _store()
    m.remember("the wifi password is hunter2", key="wifi::pw")     # no object= on either write
    m.remember("the wifi password is swordfish", key="wifi::pw")

    assert m.verify_claim("the wifi password is hunter2", key="wifi::pw",
                          object="hunter2")["verdict"] == "stale_superseded"
    assert m.verify_claim("the wifi password is swordfish", key="wifi::pw",
                          object="swordfish")["verdict"] == "supported"


def test_documented_return_keys_survive_the_early_return_paths():
    """A caller who reads res['tombstones'] got a KeyError exactly when the answer was zero."""
    m = _store()
    assert set(m.forget(ids=["no-such-id"])) >= {"forgotten", "ids", "scrubbed_links", "tombstones"}
    assert set(m.rederive("nothing-was-retracted")) >= {"rederived", "skipped", "ids",
                                                        "old_value", "new_value"}
