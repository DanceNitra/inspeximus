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

THREE MORE ROWS, from @icophy's comment of 2026-09-15 on the same issue, scored the same way and
each movable by `--mutate`: a decision anchored to an artifact outside the agent layer (the motive
field is otherwise the operator's own narrative); the audit writer sitting below the agent, in the
harness or runtime; and "recorded" kept distinct from "authorized". On 2.28.0 all three are
PARTIAL: the anchor exists and fails when the file changes but is opt-in per record; the Claude
Code hook writes from the harness and a signed chain refuses an unsigned peer entry, but the
signing key is a constructor argument the agent's own process holds; a restore is refused without
a harness-minted capability, but an ordinary landed write carries no authorization mark.
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

# Three more rows from @icophy's comment of 2026-09-15 on the same issue, each a failure mode of the
# eight above: a motive written by the operator it describes is narrative, not evidence (anchor every
# decision to an artifact outside the agent layer); the audit writer must sit below the agent, in the
# harness or runtime, or the agent can rewrite its own log; and "recorded" is not "authorized", so a
# write carries an authorization mark and the default is deny.
ICOPHY = ("landing_anchor", "writer_below_the_agent", "authorized_vs_recorded")


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

    # --- icophy 1: landing anchor. A record bound to a file by content hash; the witness re-checks it.
    import hashlib
    m4 = Inspeximus(path=os.path.join(d, "anchor.json"), receipts=True)
    doc = os.path.join(d, "policy.txt")
    body = b"deployment needs two approvers"
    open(doc, "wb").write(body)
    anchored = m4.remember("deployment needs two approvers", key="pol", object="two",
                           source={"doc": doc, "observed_sha256": hashlib.sha256(body).hexdigest()})
    bare = m4.remember("the release train leaves on Thursday", key="train", object="thursday")
    w = m4.witness([anchored], bind_sources=True)
    v_before = m4.verify_witness(w)
    open(doc, "wb").write(b"deployment needs ONE approver")
    v_after = m4.verify_witness(w)
    w_bare = m4.witness([bare], bind_sources=True)
    keys = ("sources_match", "stale_at_use", "digest_match")
    ev["anchor"] = {"bound": w.get("sources_bound"), "before": {k: v_before.get(k) for k in keys},
                    "after": {k: v_after.get(k) for k in keys},
                    "unsourced_record_bound": w_bare.get("sources_bound")}

    # --- icophy 2: writer below the agent. The receipt signer is a constructor argument, so whoever
    # holds the handle holds the key; the Claude Code hook is a second writer that runs in the harness.
    import inspect
    from inspeximus import claude_code as cc
    from inspeximus.core import new_receipt_keypair
    kp = new_receipt_keypair()
    priv, pub = (kp[0], kp[1]) if isinstance(kp, (tuple, list)) else (kp["private"], kp["public"])
    m5 = Inspeximus(path=os.path.join(d, "signed.json"), receipts=True, receipt_key=priv)
    m5.remember("the signed handle wrote this", key="s1", object="a")
    peer = Inspeximus(path=os.path.join(d, "signed.json"), receipts=True)     # same file, no key
    try:
        peer.remember("a handle without the key wrote this", key="s2", object="b")
        peer_write = "landed"
    except Exception as e:
        peer_write = type(e).__name__
    m5.reload()
    ok_signed, problems = m5.verify_writes(expected_pubkey=pub, require_signed=True)
    drop = ("CODEX_HOME", "CODEX_CLI_PATH", "INSPEXIMUS_AGENT_ID")
    env_none = {k: v for k, v in os.environ.items() if not (k.startswith("CLAUDE_CODE_") or k in drop)}
    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env_none)
        slug_bare = cc.agent_id()
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
        slug_cc = cc.agent_id()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    ev["writer"] = {"receipt_key_is_a_constructor_argument": "receipt_key" in inspect.signature(Inspeximus.__init__).parameters,
                    "unkeyed_peer_write": peer_write, "require_signed_verify_ok": ok_signed,
                    "require_signed_problems": problems[:3],
                    "hook_slug_without_harness_env": slug_bare, "hook_slug_with_claude_code_env": slug_cc,
                    "hook_module_writes_agent_id": "agent_id=agent_id(" in inspect.getsource(cc)}

    # --- icophy 3: authorized vs recorded. With an authority configured, a restore without a
    # capability is refused (default deny); a plain landed write carries no authorization mark.
    m6 = Inspeximus(path=os.path.join(d, "auth.json"), revert_authority="harness-held-secret")
    m6.remember("The staging database is db-7.internal", key="staging-db", object="db-7.internal", mtype="fact")
    m6.remember("The staging database is db-9.internal", key="staging-db", object="db-9.internal", mtype="fact")
    # The refusal is a RETURN VALUE, not an exception: {"ok": False, "reason": "authorization_required",
    # "challenge": ...}. The first version of this row caught exceptions and read a refusal as "landed".
    res = m6.revert("staging-db")
    denied = ("authorization_required" if res.get("ok") is False and res.get("reason") == "authorization_required"
              else "landed" if res.get("ok") else str(res.get("reason")))
    granted = m6.revert("staging-db", capability=m6.revert_capability("staging-db"))
    plain = next(r for r in m6.items if r.get("object") == "db-9.internal")
    marks = ("authorized", "authorization", "capability", "grant")
    ev["authz"] = {"revert_without_capability": denied, "revert_with_capability_ok": bool(granted.get("ok")),
                   "plain_write_meta_keys": sorted((plain.get("meta") or {}).keys()),
                   "plain_write_has_authorization_mark": any(k in (plain.get("meta") or {}) for k in marks)}
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

    an, wr, az = ev["anchor"], ev["writer"], ev["authz"]
    anchor_fails_when_it_must = (an["before"].get("sources_match") is True and an["after"].get("sources_match") is False
                                 and an["after"].get("stale_at_use") is True)
    icophy = {}
    icophy["landing_anchor"] = {
        "verdict": "MISSING" if not anchor_fails_when_it_must else
                   ("PARTIAL" if str(an.get("unsourced_record_bound", "")).startswith("0/") else "PRESENT"),
        "where": "source.observed_sha256 on the record; witness(bind_sources=True) re-reads the file at verify time "
                 "and reports stale_at_use; per record, opt-in: a write without a source binds nothing",
        "evidence": an}
    icophy["writer_below_the_agent"] = {
        "verdict": "MISSING" if not (wr["require_signed_verify_ok"] is False and wr["hook_module_writes_agent_id"]) else
                   ("PARTIAL" if wr["receipt_key_is_a_constructor_argument"] else "PRESENT"),
        "where": "the Claude Code hook writes from the harness and stamps the harness as agent; a signed receipt "
                 "chain refuses a peer's unsigned entry under require_signed; but the signing key is a constructor "
                 "argument, so an agent holding the library handle holds the key",
        "evidence": wr}
    icophy["authorized_vs_recorded"] = {
        "verdict": "MISSING" if not (az["revert_without_capability"] == "authorization_required" and az["revert_with_capability_ok"]) else
                   ("PARTIAL" if not az["plain_write_has_authorization_mark"] else "PRESENT"),
        "where": "revert and promote require a capability minted from a harness-held authority, refused by default; "
                 "an ordinary landed write carries no authorization mark",
        "evidence": az}
    icophy_counts = {v: sum(1 for f in ICOPHY if icophy[f]["verdict"] == v) for v in ("PRESENT", "PARTIAL", "MISSING")}
    return {"fields": table, "counts": counts, "lifecycle": lifecycle,
            "icophy_fields": icophy, "icophy_counts": icophy_counts}


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
    elif field == "landing_anchor":
        ev["anchor"]["after"]["sources_match"] = True        # a witness that cannot notice the file changed
    elif field == "writer_below_the_agent":
        ev["writer"]["require_signed_verify_ok"] = True     # a chain that accepts the unsigned peer entry
    elif field == "authorized_vs_recorded":
        ev["authz"]["revert_without_capability"] = "landed"  # a restore nobody authorized
    else:
        raise SystemExit("unknown field %r" % field)
    return ev


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutate", default=None,
                    help="blank one field's evidence before scoring (any of %s)" % ", ".join(FIELDS + ICOPHY))
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
    for f in ICOPHY:
        print("  %-24s %-8s %s" % (f, out["icophy_fields"][f]["verdict"], out["icophy_fields"][f]["where"][:100]))
    print("  icophy counts:", out["icophy_counts"])
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
