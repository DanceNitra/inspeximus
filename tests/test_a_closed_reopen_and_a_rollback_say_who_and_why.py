"""A rollback carries an author and a reason; a closed reopen leaves a trace on both exits.

WHY. deepseek-ai/DeepSeek-V3#1644 lists, for every automatic operation, a human-readable motive and an
operator identity, and asks that a detected contradiction never be marked resolved by the system on
its own. An adversarial pass of this store against that list (2026-09-14) found three gaps:
`revert()` took no author and no reason, so a history showed corrections with authors and rollbacks
with none; `resolve_reopened(..., "keep_current")` popped every reopen marker, so nothing on disk
said a contradiction had ever been detected; and a later keyed write superseded a reopened record and
the review queue simply emptied, with no decision recorded anywhere. 2.27.7 closes the three.

Each test fails on 2.27.6 and passes on 2.27.7; the last test is the control that the queue still
empties the way callers rely on.
"""
from __future__ import annotations


from inspeximus import Inspeximus


def _store(tmp_path, name="s.json"):
    m = Inspeximus(path=str(tmp_path / name), receipts=True)
    m.remember("The staging database is db-7.internal", key="staging-db", mtype="fact",
               source={"doc": "runbook-v3"}, agent_id="ops-bot")
    return m


def _reopened(tmp_path, name="c.json"):
    m = _store(tmp_path, name)
    m.observe(key="staging-db", text="The staging database is db-9.internal", support="incident-4471")
    out = m.observe(key="staging-db", text="The staging database is db-9.internal", support="pager-log-0913")
    assert out["reopened"] is True and len(m.reopened()) == 1, "the fixture did not reopen; nothing below measures"
    return m, m.reopened()[0]["id"]


def test_a_rollback_carries_its_author_and_its_reason(tmp_path):
    m = _store(tmp_path)
    m.remember("The staging database is db-9.internal", key="staging-db", mtype="fact",
               source={"doc": "chat-2026-09-13"}, agent_id="rasto")
    r = m.revert("staging-db", reason="db-9 was a typo in chat", agent_id="rasto")
    assert r["ok"]
    rec = next(x for x in m.items if x["id"] == r["restored"])
    assert rec["meta"]["reason"] == "db-9 was a typo in chat"
    assert [h["agent"] for h in m.history("staging-db")] == ["ops-bot", "rasto", "rasto"]
    assert m.verify_writes()[0] is True


def test_keep_current_leaves_the_detection_and_the_decision_on_the_record(tmp_path):
    m, rid = _reopened(tmp_path)
    m.resolve_reopened(rid, "keep_current", reason="both pages were about another key", agent_id="rasto")
    rec = next(x for x in m.items if x["id"] == rid)
    trace = rec["meta"]["reopened_resolved"]
    assert trace["decision"] == "keep_current"
    assert trace["reason"] == "both pages were about another key"
    assert trace["aid"] == "rasto"
    assert trace["was"]["reopened_reason"] == "novel_support_contradiction"
    assert trace["was"]["reopened_ts"] > 0
    assert m.reopened() == [], "keep_current must still take the record out of the queue"


def test_reaffirm_prior_names_the_review_it_resolves(tmp_path):
    m = Inspeximus(path=str(tmp_path / "r.json"), receipts=True)
    m.remember("The staging database is db-7.internal", key="staging-db", object="db-7.internal",
               mtype="fact", source={"doc": "runbook-v3"}, agent_id="ops-bot")
    m.remember("The staging database is db-9.internal", key="staging-db", object="db-9.internal",
               mtype="fact", source={"doc": "chat-2026-09-13"}, agent_id="rasto")
    # a bare value-obscuring revert reopens on first sight and surfaces the prior OBJECT value
    out = m.observe(text="go back to what we had", key="staging-db")
    assert out["reopened"] is True and out["surfaced_prior"] is not None, out
    rid = m.reopened()[0]["id"]
    res = m.resolve_reopened(rid, "reaffirm_prior", reason="the runbook is right", agent_id="rasto")
    new = next(x for x in m.items if x["id"] == res["new_id"])
    assert new["meta"]["resolves_reopened"] == rid
    assert new["meta"]["reason"] == "the runbook is right"
    assert new["meta"].get("aid") == "rasto"
    old = next(x for x in m.items if x["id"] == rid)
    assert old["meta"]["reopened_resolved"]["decision"] == "reaffirm_prior"


def test_a_keyed_write_that_closes_a_reopen_says_so_on_both_records(tmp_path):
    m, rid = _reopened(tmp_path, "w.json")
    new_id = m.remember("The staging database is db-9.internal", key="staging-db", mtype="fact",
                        source={"doc": "chat-2026-09-13"}, agent_id="rasto")
    assert m.reopened() == [], "the write must still empty the queue"
    old = next(x for x in m.items if x["id"] == rid)
    new = next(x for x in m.items if x["id"] == new_id)
    assert old["meta"]["reopened_resolved"]["decision"] == "superseded_by_write"
    assert old["meta"]["reopened_resolved"]["was"]["by"] == new_id
    assert new["meta"]["resolves_reopened"] == rid


def test_control_an_ordinary_keyed_write_carries_no_reopen_trace(tmp_path):
    m = _store(tmp_path, "k.json")
    new_id = m.remember("The staging database is db-9.internal", key="staging-db", mtype="fact",
                        source={"doc": "chat-2026-09-13"}, agent_id="rasto")
    new = next(x for x in m.items if x["id"] == new_id)
    old = next(x for x in m.items if x.get("status") == "superseded")
    assert "resolves_reopened" not in new["meta"]
    assert "reopened_resolved" not in old.get("meta", {})
