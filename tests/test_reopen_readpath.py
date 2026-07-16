"""Read-path REOPEN (marintkael's mirror of the Fellegi-Sunter clerical-review band, r/RAG 2026-07-16).
observe() is a POST-write review trigger: when independent evidence CONTRADICTS a high-confidence settled
record, corroborate it and REOPEN the interval for steward review — catching the confident wrong-merge that
write-time can never catch, and giving the value-obscuring revert something to key on, WITHOUT flooding on a
single benign restatement. Receipt: probes/reopen_interval_readpath.py."""
from mnemo import Mnemo


def _store(tmp_path, name="m.json"):
    return Mnemo(str(tmp_path / name))


def test_single_contradiction_does_not_reopen(tmp_path):
    m = _store(tmp_path)
    m.remember("the region is Frankfurt", key="a/region", object="Frankfurt")
    r = m.observe("actually Ohio", key="a/region", object="Ohio")   # one stray contradiction
    assert r["reopened"] is False and r["pending"] == 1 and r["need"] == 2
    assert m.reopened() == []                                       # flood control: nothing to review yet


def test_corroborated_contradiction_reopens(tmp_path):
    m = _store(tmp_path)
    m.remember("the region is Frankfurt", key="a/region", object="Frankfurt")
    m.observe("actually Ohio", key="a/region", object="Ohio")
    r = m.observe("it's Ohio", key="a/region", object="Ohio")       # 2nd independent -> corroborated
    assert r["reopened"] is True and r["review_id"]
    q = m.reopened()
    assert len(q) == 1 and q[0]["reason"] == "corroborated_contradiction" and q[0]["contradiction"] == "Ohio"


def test_benign_restatement_of_old_value_does_not_reopen(tmp_path):
    """The OP scenario: after a correction, a user restates the OLD value ONCE (forgot they changed it).
    A single stray restatement must not reopen."""
    m = _store(tmp_path)
    m.remember("color is blue", key="u/color", object="blue")
    m.remember("color is green", key="u/color", object="green")     # correction blue -> green
    r = m.observe("I think it's blue", key="u/color", object="blue")  # ONE stray restatement of the old value
    assert r["reopened"] is False
    assert m.reopened() == []


def test_value_obscuring_revert_surfaces_prior(tmp_path):
    m = _store(tmp_path)
    m.remember("color is blue", key="u/color", object="blue")
    m.remember("color is green", key="u/color", object="green")     # correction blue -> green (blue superseded)
    r = m.observe("go back to the old one", key="u/color", object=None)  # names nothing
    assert r["reopened"] is True and r["surfaced_prior"] == "blue"


def test_reopened_record_still_recallable(tmp_path):
    m = _store(tmp_path)
    m.remember("the region is Frankfurt", key="a/region", object="Frankfurt")
    m.observe("Ohio", key="a/region", object="Ohio"); m.observe("Ohio", key="a/region", object="Ohio")
    assert m.reopened()                                             # it is reopened
    texts = " ".join(h["text"] for h in m.recall("region", k=5))
    assert "Frankfurt" in texts                                     # ...yet recall still returns the current value


def test_resolve_keep_current_clears_flag(tmp_path):
    m = _store(tmp_path)
    m.remember("x is 1", key="k/x", object="1")
    m.observe("2", key="k/x", object="2"); m.observe("2", key="k/x", object="2")
    rid = m.reopened()[0]["id"]
    out = m.resolve_reopened(rid, "keep_current")
    assert out["decision"] == "keep_current" and m.reopened() == []


def test_resolve_reaffirm_prior_restores_old_value(tmp_path):
    m = _store(tmp_path)
    m.remember("color is blue", key="u/color", object="blue")
    m.remember("color is green", key="u/color", object="green")
    r = m.observe("undo that change", key="u/color", object=None)
    m.resolve_reopened(r["review_id"], "reaffirm_prior")
    # current authoritative value is blue again
    cur = m._current_active("u/color")
    assert cur is not None and cur.get("object") == "blue"
    assert m.reopened() == []


def test_agreement_never_reopens(tmp_path):
    m = _store(tmp_path)
    m.remember("status is open", key="t/status", object="open")
    r = m.observe("still open", key="t/status", object="open")      # agrees with current
    assert r["reopened"] is False and r.get("agreed") is True


def test_low_confidence_write_has_no_authoritative_value_to_reopen(tmp_path):
    """A below-fork_below write forks a CANDIDATE (not active), so there is no authoritative interval for
    observe() to reopen — it correctly reports no_current rather than reopening a value that was never settled."""
    m = _store(tmp_path)
    m.remember("guess is A", key="g/x", object="A", identity_confidence=0.5)  # forks a candidate
    r = m.observe("B", key="g/x", object="B")
    assert r["reopened"] is False and r.get("no_current") is True
    assert m.reopened() == []


def test_legacy_unaffected_no_observe(tmp_path):
    """A store that never calls observe() is byte-identical: no record carries a 'reopened' flag."""
    m = _store(tmp_path)
    m.remember("a is 1", key="k/a", object="1")
    m.remember("a is 2", key="k/a", object="2")
    assert all("reopened" not in r for r in m.items)
    assert m.reopened() == []


def test_recall_surfaces_under_review_after_reopen(tmp_path):
    """The read-path review-trigger must reach the AGENT: once observe() reopens a settled record, recall()
    hits for it carry `under_review` + the surfaced prior, so the consumer can branch instead of acting on a
    contested value with full confidence."""
    m = _store(tmp_path)
    m.remember("the region is Frankfurt", key="a/region", object="Frankfurt")
    m.remember("correction: the region is now Ohio", key="a/region", object="Ohio")
    assert "under_review" not in m.recall("region", k=3)[0]            # clean before any contradiction
    m.observe("Ohio? no, Berlin", key="a/region", object="Berlin")
    m.observe("Berlin, again", key="a/region", object="Berlin")        # corroborated -> reopen
    hit = next(h for h in m.recall("region", k=5) if "Ohio" in h["text"])
    assert hit["under_review"] is True
    assert hit["review_reason"] == "corroborated_contradiction"
    assert hit["review_prior"] == "Frankfurt"


def test_recall_under_review_clears_after_resolve(tmp_path):
    m = _store(tmp_path)
    m.remember("the width is narrow", key="k/width", object="narrow")
    m.observe("wide", key="k/width", object="wide"); m.observe("wide", key="k/width", object="wide")
    assert any(h.get("under_review") for h in m.recall("width", k=5))
    m.resolve_reopened(m.reopened()[0]["id"], "keep_current")
    assert not any(h.get("under_review") for h in m.recall("width", k=5))   # steward closed it -> no longer contested
