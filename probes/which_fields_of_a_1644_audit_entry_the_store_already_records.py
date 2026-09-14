"""Which fields of the audit entry deepseek-ai/DeepSeek-V3#1644 asks for does this store already record?

WHY THIS FILE EXISTS. Issue #1644 (Li Guanghao, 2026-09-14) proposes an audit entry for every automatic
operation of an AI system, with eight fields: an operation id, a millisecond timestamp, an operation
type, a human-readable motive, a full content snapshot, a list of the alternatives the system did not
take, a rollback path, and an operator identity. It also proposes a contradiction lifecycle in which
the system only detects (DORMANT to EMERGENT) and a human does the rest, and a no-prescription rule:
the system reports, it does not recommend. This probe runs the store through one keyed correction,
one revert, one corroborated contradiction closed by a steward, and one closed by a later write, and
reads each field back from disk, so a claim that the store "already does this" is a table of PRESENT,
PARTIAL and MISSING with the evidence beside it, not a sentence.

THE VERDICTS ARE COMPUTED, NOT TYPED. The first version of this file assigned each verdict as a string
literal next to evidence it never checked; an adversarial re-run mutated the evidence six ways and
the table did not move. Every verdict below is a function of what the store returned, and
`--mutate <field>` blanks that field's evidence before scoring so the reader can watch the verdict
change. A probe whose table cannot change is a demonstration.

WHAT IT FOUND, 2.27.6 against 2.27.7. On 2.27.6 the adversarial pass corrected two of the first
version's verdicts upward (a motive fits in `meta`, and several refusal paths do record the road not
taken) and found three real gaps: `revert()` carried no author and no reason, `keep_current` erased
the detection it resolved, and a keyed write on a reopened key emptied the review queue with no
decision recorded. 2.27.7 closes the three; this file scores whichever version it runs against.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inspeximus  # noqa: E402
from inspeximus import Inspeximus  # noqa: E402
from _receipt import write_receipt  # noqa: E402

FIELDS = ("operation_id", "timestamp_ms", "operation_type", "motive", "content_snapshot",
          "alternatives", "rollback_path", "operator_identity")


def _kw(fn, **kw):
    """Pass only the keyword arguments this version of `fn` accepts, so the probe scores old builds too."""
    import inspect
    ok = inspect.signature(fn).parameters
    return {k: v for k, v in kw.items() if k in ok}


def gather(d: str) -> dict:
    """Run the four operations and collect raw evidence. No verdicts here."""
    ev = {}
    p = os.path.join(d, "s.json")
    m = Inspeximus(path=p, receipts=True)
    m.remember("The staging database is db-7.internal", key="staging-db", mtype="fact",
               source={"doc": "runbook-v3"}, agent_id="ops-bot")
    m.remember("The staging database is db-9.internal", key="staging-db", mtype="fact",
               source={"doc": "chat-2026-09-13"}, agent_id="rasto",
               meta={"reason": "ops chat on 13 Sep named db-9"})
    rv = m.revert("staging-db", **_kw(m.revert, reason="db-9 was a typo in chat", agent_id="rasto"))
    ev["revert"] = rv
    ev["receipts"] = json.load(open(p + ".receipts.json", encoding="utf-8"))
    ev["history"] = m.history("staging-db")
    ev["records"] = {r["id"]: r for r in m.items}
    ev["restored"] = ev["records"].get(rv.get("restored"), {})
    ev["verify_writes"] = m.verify_writes()
    # a store opened WITHOUT receipts, to score what the default configuration keeps
    m0 = Inspeximus(path=os.path.join(d, "plain.json"))
    m0.remember("The staging database is db-7.internal", key="staging-db", object="db-7.internal",
                mtype="fact", source={"doc": "runbook-v3"})
    ev["plain_has_sidecar"] = os.path.exists(os.path.join(d, "plain.json.receipts.json"))
    ev["plain_history"] = m0.history("staging-db")
    # an echo write, a RETIRED value written again: the road not taken is recorded on the write itself
    m0.remember("The staging database is db-9.internal", key="staging-db", object="db-9.internal",
                mtype="fact", source={"doc": "chat-2026-09-13"})
    echo_id = m0.remember("The staging database is db-7.internal", key="staging-db", object="db-7.internal",
                          mtype="fact", source={"doc": "old-runbook"})
    ev["echo_record"] = next((r for r in m0.items if r["id"] == echo_id), {})
    # a corroborated contradiction, closed by a steward
    m2 = Inspeximus(path=os.path.join(d, "c.json"), receipts=True)
    m2.remember("The staging database is db-7.internal", key="staging-db", mtype="fact",
                source={"doc": "runbook-v3"}, agent_id="ops-bot")
    ev["obs1"] = m2.observe(key="staging-db", text="The staging database is db-9.internal", support="incident-4471")
    ev["obs2"] = m2.observe(key="staging-db", text="The staging database is db-9.internal", support="pager-log-0913")
    ev["queue"] = m2.reopened()
    rid = ev["queue"][0]["id"] if ev["queue"] else None
    if rid:
        m2.resolve_reopened(rid, "keep_current", **_kw(m2.resolve_reopened, reason="pages were about another key",
                                                       agent_id="rasto"))
        ev["after_keep_current"] = {"queue": len(m2.reopened()),
                                    "trace": next(r for r in m2.items if r["id"] == rid).get("meta", {}).get("reopened_resolved")}
    # the same contradiction, closed by a later keyed write instead of a decision
    m3 = Inspeximus(path=os.path.join(d, "w.json"), receipts=True)
    m3.remember("The staging database is db-7.internal", key="staging-db", mtype="fact",
                source={"doc": "runbook-v3"}, agent_id="ops-bot")
    m3.observe(key="staging-db", text="The staging database is db-9.internal", support="incident-4471")
    m3.observe(key="staging-db", text="The staging database is db-9.internal", support="pager-log-0913")
    rid3 = m3.reopened()[0]["id"] if m3.reopened() else None
    new3 = m3.remember("The staging database is db-9.internal", key="staging-db", mtype="fact",
                       source={"doc": "chat-2026-09-13"}, agent_id="rasto")
    ev["after_write"] = {"queue": len(m3.reopened()),
                         "old_trace": next((r for r in m3.items if r["id"] == rid3), {}).get("meta", {}).get("reopened_resolved"),
                         "new_resolves": next((r for r in m3.items if r["id"] == new3), {}).get("meta", {}).get("resolves_reopened")}
    ev["contradictions_sample"] = m3.contradictions(sim_threshold=0.3)
    ev["check_conflict_sample"] = m3.check_conflict("The staging database is db-7.internal", key="staging-db")
    return ev


def score(ev: dict) -> dict:
    """Verdicts as functions of the evidence. Each line names the evidence it reads."""
    t = {}
    rc = ev["receipts"]
    chained = bool(rc) and all(("seq" in r and r.get("memory_id") and r.get("hash") and r.get("prev")) for r in rc) \
        and all(rc[i]["prev"] == rc[i - 1]["hash"] for i in range(1, len(rc)))
    t["operation_id"] = ("PRESENT" if chained else "MISSING",
                         "receipts sidecar (opt-in, receipts=True): seq, memory_id, hash chained to prev; "
                         "without it every record still has an id",
                         {"entries": len(rc), "chained": chained, "plain_store_has_sidecar": ev["plain_has_sidecar"]})
    ts_ok = bool(rc) and all(isinstance(r.get("ts"), float) for r in rc) and \
        all(h.get("valid_from") for h in ev["history"])
    t["timestamp_ms"] = ("PRESENT" if ts_ok else "MISSING",
                         "receipt ts (float seconds, sub-millisecond); history valid_from and invalidated_at",
                         {"ts": rc[1]["ts"] if len(rc) > 1 else None,
                          "valid_from": [h.get("valid_from") for h in ev["history"]]})
    policies = [h.get("policy") for h in ev["history"]]
    has_type_field = any("type" in r for r in ev["records"].values())
    t["operation_type"] = ("PRESENT" if has_type_field else ("PARTIAL" if any(policies) else "MISSING"),
                           "a policy label on the retired version (keyed_lww, keyed_reaffirm) and meta.revert_of on "
                           "a revert; no single type field",
                           {"policies": policies, "revert_meta_keys": sorted(ev["restored"].get("meta", {}).keys())})
    corr_reason = any(r.get("meta", {}).get("reason") for r in ev["records"].values()
                      if r.get("status") == "superseded")
    revert_reason = bool(ev["restored"].get("meta", {}).get("reason"))
    keep = (ev.get("after_keep_current") or {}).get("trace") or {}
    t["motive"] = ("PRESENT" if (corr_reason and revert_reason and keep.get("reason")) else
                   ("PARTIAL" if corr_reason else "MISSING"),
                   "a correction carries meta.reason; a revert and a steward decision carry one only since 2.27.7",
                   {"correction_reason": corr_reason, "revert_reason": revert_reason,
                    "keep_current_reason": bool(keep.get("reason"))})
    texts = [h.get("text") for h in ev["history"]]
    t["content_snapshot"] = ("PRESENT" if len(texts) >= 3 and all(texts) else "MISSING",
                             "every version keeps its full text; a retired value is never deleted",
                             {"versions": texts})
    echo = ev["echo_record"].get("meta", {})
    refusal_recorded = bool(echo.get("echo_blocked") or echo.get("superseded_by_policy"))
    landed_alternatives = any(r.get("meta", {}).get("alternatives") for r in ev["records"].values())
    t["alternatives"] = ("PRESENT" if landed_alternatives else ("PARTIAL" if refusal_recorded else "MISSING"),
                         "a refused write records what stopped it (echo_blocked, superseded_by_policy) and a "
                         "reopened record surfaces the prior it contests; a plain landed write records no "
                         "alternatives",
                         {"echo_meta": {k: echo.get(k) for k in ("echo_blocked", "superseded_by_policy")}})
    rb = ev["revert"].get("ok") and ev["history"][-1].get("status") == "active" and \
        ev["history"][-1].get("text") == ev["history"][0].get("text") and ev["verify_writes"][0] is True
    t["rollback_path"] = ("PRESENT" if rb else "MISSING",
                          "revert(key) restores the prior value as a new record; both stay in history and the "
                          "receipt chain verifies after it",
                          {"revert": ev["revert"], "verify_writes": ev["verify_writes"]})
    agents = [h.get("agent") for h in ev["history"]]
    t["operator_identity"] = ("PRESENT" if all(agents) else ("PARTIAL" if any(agents) else "MISSING"),
                              "agent_id per version (history.agent, stored as meta.aid) and a bound source "
                              "document; the record a revert creates carries one only since 2.27.7",
                              {"history_agents": agents})
    table = {f: {"verdict": v, "where": w, "evidence": e} for f, (v, w, e) in t.items()}
    ak = ev.get("after_keep_current") or {}
    aw = ev.get("after_write") or {}
    lifecycle = {
        "detect_only": {"first_observation": ev["obs1"], "second_observation": ev["obs2"],
                        "queue_after": len(ev["queue"])},
        "steward_exit": {"queue_after": ak.get("queue"), "trace": ak.get("trace"),
                         "verdict": "TRACED" if (ak.get("trace") or {}).get("decision") == "keep_current"
                         else "ERASED"},
        "write_exit": {"queue_after": aw.get("queue"), "old_trace": aw.get("old_trace"),
                       "new_resolves": aw.get("new_resolves"),
                       "verdict": "TRACED" if aw.get("new_resolves") and (aw.get("old_trace") or {}).get("decision")
                       == "superseded_by_write" else "SILENT"},
        "no_prescription": {"contradictions_keys": sorted({k for c in ev["contradictions_sample"] for k in c}),
                            "check_conflict_keys": sorted({k for c in ev["check_conflict_sample"] for k in c}),
                            "verdict": "NO_RECOMMENDATION" if not any(
                                k in ("recommendation", "suggest", "should") for c in
                                ev["contradictions_sample"] + ev["check_conflict_sample"] for k in c)
                            else "PRESCRIBES"},
    }
    counts = {v: sum(1 for f in FIELDS if table[f]["verdict"] == v) for v in ("PRESENT", "PARTIAL", "MISSING")}
    return {"fields": table, "counts": counts, "lifecycle": lifecycle}


def mutate(ev: dict, field: str) -> dict:
    """Blank one field's evidence so the reader can see the verdict move."""
    ev = json.loads(json.dumps(ev, default=str))
    if field == "operation_id":
        for r in ev["receipts"]:
            r["prev"] = "x"
    elif field == "timestamp_ms":
        for h in ev["history"]:
            h["valid_from"] = None
    elif field == "operation_type":
        for h in ev["history"]:
            h["policy"] = None
    elif field == "motive":
        for r in ev["records"].values():
            r.get("meta", {}).pop("reason", None)
        ev["restored"].get("meta", {}).pop("reason", None)
    elif field == "content_snapshot":
        ev["history"][0]["text"] = ""
    elif field == "alternatives":
        ev["echo_record"]["meta"] = {}
    elif field == "rollback_path":
        ev["revert"]["ok"] = False
    elif field == "operator_identity":
        for h in ev["history"]:
            h["agent"] = None
    else:
        raise SystemExit("unknown field %r" % field)
    return ev


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutate", default=None, help="blank one field's evidence before scoring")
    a = ap.parse_args()
    d = tempfile.mkdtemp(prefix="audit1644_")
    ev = gather(d)
    if a.mutate:
        ev = mutate(ev, a.mutate)
    out = score(ev)
    out.update({"probe": os.path.basename(__file__), "version": inspeximus.__version__,
                "issue": "deepseek-ai/DeepSeek-V3#1644 sections 3.2 to 3.4", "mutated": a.mutate})
    for f in FIELDS:
        print("  %-18s %-8s %s" % (f, out["fields"][f]["verdict"], out["fields"][f]["where"][:100]))
    print("  counts:", out["counts"])
    lc = out["lifecycle"]
    print("  lifecycle: pending %d/%d then reopened=%s; steward exit %s; write exit %s; %s"
          % (ev["obs1"]["pending"], ev["obs1"]["need"], ev["obs2"]["reopened"], lc["steward_exit"]["verdict"],
             lc["write_exit"]["verdict"], lc["no_prescription"]["verdict"]))
    if a.mutate:
        print("  (mutated %s; not written as the receipt)" % a.mutate)
        return 0
    write_receipt(__file__, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
