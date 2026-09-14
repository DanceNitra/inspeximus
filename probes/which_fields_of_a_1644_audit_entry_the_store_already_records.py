"""Which fields of the audit entry deepseek-ai/DeepSeek-V3#1644 asks for does this store already record?

WHY THIS FILE EXISTS. Issue #1644 (Li Guanghao, 2026-09-14) proposes an audit entry for every automatic
operation of an AI system, with eight fields: an operation id, a millisecond timestamp, an operation
type, a human-readable motive, a full content snapshot, a list of the alternatives the system did not
take, a rollback path, and an operator identity. It also proposes a contradiction lifecycle in which
the system only detects (DORMANT to EMERGENT) and a human does the rest, and a no-prescription rule:
the system reports, it does not recommend. This probe runs the store through one keyed correction,
one revert, and one corroborated contradiction, and reads each field back from what the store wrote,
so a claim that the store "already does this" is a table of PRESENT, PARTIAL and MISSING with the
evidence beside it, not a sentence.

WHAT IT MEASURES. Nothing about quality. Only whether each field is on disk after the operation, and
where. PARTIAL means the information exists in a different shape than the field asks for. MISSING is
a gap this store has, printed as such.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inspeximus import Inspeximus  # noqa: E402
from _receipt import write_receipt  # noqa: E402

FIELDS = ("operation_id", "timestamp_ms", "operation_type", "motive", "content_snapshot",
          "alternatives", "rollback_path", "operator_identity")


def main() -> int:
    d = tempfile.mkdtemp(prefix="audit1644_")
    path = os.path.join(d, "s.json")
    m = Inspeximus(path=path, receipts=True)

    # 1. a keyed correction: the value of one key changes, the old value is retired, not deleted
    first = m.remember("The staging database is db-7.internal", key="staging-db", mtype="fact",
                       source={"doc": "runbook-v3"}, agent_id="ops-bot")
    second = m.remember("The staging database is db-9.internal", key="staging-db", mtype="fact",
                        source={"doc": "chat-2026-09-13"}, agent_id="rasto")
    # 2. a revert: the prior value comes back as a NEW record; nothing is overwritten
    rv = m.revert("staging-db")
    # 3. a contradiction with corroboration: one observation is pending, the second reopens
    m2 = Inspeximus(path=os.path.join(d, "c.json"), receipts=True)
    m2.remember("The staging database is db-7.internal", key="staging-db", mtype="fact",
                source={"doc": "runbook-v3"}, agent_id="ops-bot")
    o1 = m2.observe(key="staging-db", text="The staging database is db-9.internal", support="incident-4471")
    o2 = m2.observe(key="staging-db", text="The staging database is db-9.internal", support="pager-log-0913")
    queue = m2.reopened()

    receipts = json.load(open(path + ".receipts.json", encoding="utf-8"))
    hist = m.history("staging-db")
    prov = m.provenance("staging-db")
    restored = next(r for r in m.items if r["id"] == rv["restored"])
    supers = [r for r in m.items if r.get("status") == "superseded"]
    assert len(receipts) >= 3 and len(hist) == 3, (len(receipts), len(hist))
    assert supers and all(r.get("text") for r in supers), "a retired value lost its text"
    assert o1["reopened"] is False and o2["reopened"] is True and len(queue) == 1, (o1, o2, queue)

    table = {
        "operation_id": {
            "verdict": "PRESENT",
            "where": "receipts sidecar: seq %d..%d, each with memory_id and a hash chained to prev"
                     % (receipts[0]["seq"], receipts[-1]["seq"]),
            "example": {"seq": receipts[1]["seq"], "memory_id": receipts[1]["memory_id"],
                        "prev": receipts[1]["prev"][:12] + "...", "hash": receipts[1]["hash"][:12] + "..."},
        },
        "timestamp_ms": {
            "verdict": "PRESENT",
            "where": "receipts sidecar ts (float seconds, sub-millisecond); history valid_from and invalidated_at",
            "example": {"ts": receipts[1]["ts"], "valid_from": hist[1]["valid_from"],
                        "invalidated_at": hist[1]["invalidated_at"]},
        },
        "operation_type": {
            "verdict": "PARTIAL",
            "where": "history policy labels the retirement (keyed_lww, keyed_reaffirm); the revert record carries "
                     "meta.revert_of and meta.reverted_from; there is no single enum field named type",
            "example": {"policies": [h["policy"] for h in hist],
                        "revert_record_meta": restored.get("meta")},
        },
        "motive": {
            "verdict": "MISSING",
            "where": "no human-readable reason is stored with a correction or a revert; the policy label says "
                     "WHAT rule retired a value, not WHY the caller wrote it",
            "example": None,
        },
        "content_snapshot": {
            "verdict": "PRESENT",
            "where": "every version keeps its full text; history returns all three versions verbatim, "
                     "superseded ones included",
            "example": [h["text"] for h in hist],
        },
        "alternatives": {
            "verdict": "MISSING",
            "where": "no record of what the store could have done and did not (for example: refused, "
                     "queued for review, kept both)",
            "example": None,
        },
        "rollback_path": {
            "verdict": "PRESENT",
            "where": "revert(key) restores the prior value as a new record and retires the current one; both "
                     "stay in history, so the rollback is itself auditable and reversible",
            "example": {"revert": rv, "history_after": [(h["status"], h["text"][-14:]) for h in hist]},
        },
        "operator_identity": {
            "verdict": "PARTIAL",
            "where": "remember() records agent_id per version (history shows ops-bot then rasto) and source "
                     "binds an origin document; the record a revert creates carries no agent_id",
            "example": {"history_agents": [h["agent"] for h in hist],
                        "revert_record_agent": restored.get("agent_id")},
        },
    }
    lifecycle = {
        "detect_only": "observe() counts contradicting observations with distinct support; one is pending "
                       "(need 2), the second reopens the settled record into a review queue",
        "first_observation": o1, "second_observation": o2, "queue": queue,
        "human_step": "resolve_reopened(id, 'keep_current' | 'reaffirm_prior') is the only exit; the store "
                      "never marks a reopened record resolved on its own",
        "no_prescription": "contradictions() and check_conflict() return pairs and similarity; neither "
                           "returns a recommendation",
    }
    counts = {v: sum(1 for f in FIELDS if table[f]["verdict"] == v) for v in ("PRESENT", "PARTIAL", "MISSING")}
    out = {"probe": os.path.basename(__file__), "issue": "deepseek-ai/DeepSeek-V3#1644 section 3.2 and 3.4",
           "fields": table, "counts": counts, "lifecycle": lifecycle}
    for f in FIELDS:
        print("  %-18s %-8s %s" % (f, table[f]["verdict"], table[f]["where"][:100]))
    print("  counts:", counts)
    print("  lifecycle: pending %d/%d then reopened=%s, queue %d, exit is a human decision"
          % (o1["pending"], o1["need"], o2["reopened"], len(queue)))
    write_receipt(__file__, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
