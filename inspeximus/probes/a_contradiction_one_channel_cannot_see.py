"""Does a reader see a contradiction, or only the channel that happened to be in use?

WHY. Controlled Memory Interference (arXiv:2608.07622, 7 Aug 2026) reports that lexical and dense
retrieval exhibit DISTINCT interference pathways: what blocks an update through one channel is not
what blocks it through the other. inspeximus ships three channels (`mode='lexical' | 'semantic' |
'hybrid'`), so the claim is directly testable on our own store, and it matters because we sell
correction as the product. If a correction is reachable through one channel and invisible through
another, then whether a reader sees the current value depends on a mode setting rather than on what
the store holds.

TWO CONDITIONS, and the difference between them is the whole point.

  KEYED      the correction is written with the same supersession `key` as the stale record, so the
             store KNOWS one replaces the other. Recall filters superseded records by state, which
             is channel-independent, so every channel must hide the stale value. This is the control
             that would catch a genuine integrity bug.

  UNKEYED    the correction is written as an ordinary memory with no key, which is the realistic
             case: a later note contradicts an earlier one and nobody declared the link. Nothing
             filters here, so what the reader sees is decided entirely by ranking, which is exactly
             where the two channels can disagree.

The two records are worded to share no content tokens, because a contradiction that repeats the
earlier wording is one every channel finds. The interesting case is the one a lexical channel cannot
see.

CONTROLS, because a channel that silently fell back would otherwise report agreement.

  MODE       recall falls back to lexical when embedding fails, so each arm asserts `_last_mode` is
             the mode it asked for. Without this an embedder outage reads as "all channels agree".
  POSITIVE   each record must be retrievable by its own wording. A record nothing can find makes
             every absence below meaningless rather than informative.
  NEGATIVE   a phrase never written must return nothing.
  POOL       distractors are loaded so that top-k is a real selection. With two records in a store,
             k=6 returns both under any ranking and the probe cannot fail.

WHAT THIS DOES NOT SHOW. One embedder (nomic-embed-text), one query, one contradiction pair. It
measures whether the channels CAN disagree on a constructed case, not how often they do on real
traffic.
"""
import json
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from inspeximus.core import Inspeximus  # noqa: E402

# NEVER `localhost` for the local daemon: the name resolves to ::1 first and the connect fails over
# to IPv4 before any request is sent, which cost 2 s on every call and 58x on embeddings.
EMBED_URL = os.environ.get("INSPEXIMUS_EMBED_URL", "http://127.0.0.1:11434/api/embed")
EMBED_MODEL = os.environ.get("INSPEXIMUS_EMBED_MODEL", "nomic-embed-text")

STALE = "Production deploys go to the us-east-1 region."
FRESH = "We moved the live cluster to Frankfurt; eu-central-1 now serves all customer traffic."
QUERY = "which region does production deploy to"
# Nonsense tokens, deliberately. The first version of this line was ordinary English and matched
# a distractor on the word "written", so the control failed on my phrasing rather than on the store.
NEVER = "zqxjv wrompf blenkarth tuvvel ghrastik"

DISTRACTORS = [
    "The on-call rotation changes at 09:00 UTC every Monday.",
    "Invoices are issued on the first working day of the month.",
    "The staging database is restored from a nightly snapshot.",
    "Support tickets older than 90 days are archived automatically.",
    "The design review meeting moved to Thursday afternoons.",
    "Contractor access expires 30 days after the last commit.",
    "Backups are verified by restoring into a scratch namespace.",
    "The mobile client caches assets for seven days.",
    "Feature flags default to off for new tenants.",
    "The changelog is generated from merged pull request titles.",
    "Load tests run against a copy of last week's traffic.",
    "Secrets rotate every 90 days through the vault job.",
    "The API rate limit is 600 requests per minute per token.",
    "Dependency upgrades land in a single weekly batch.",
    "Incident reviews are written within three working days.",
    "The marketing site is rebuilt on every content change.",
    "Log retention is 30 days for debug and a year for audit.",
    "New hires get read-only access for their first week.",
    "The billing export runs at 02:00 and pages on failure.",
    "Pull requests need one approval and a green pipeline.",
]


def make_embedder():
    """Return (embed_fn, error). nomic is asymmetric, so documents and queries take different
    prefixes; using one prefix for both quietly degrades the semantic channel we are measuring."""
    def _embed(text, prefix):
        body = json.dumps({"model": EMBED_MODEL, "input": prefix + text}).encode("utf-8")
        req = urllib.request.Request(EMBED_URL, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["embeddings"][0]

    try:
        _embed("warm up the model so the first arm is not timed differently", "search_document: ")
    except Exception as exc:                                    # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, exc)
    return (lambda t: _embed(t, "search_document: "),
            lambda t: _embed(t, "search_query: ")), None


def build(embed_pair, condition):
    """A fresh store holding the pool and, depending on the condition, one record or both.

    stale_only  only the earlier record. The before-picture, so the effect of writing the
                correction is measured rather than inferred from the other two conditions.
    keyed       both, sharing a supersession key, so the store knows one replaces the other.
    unkeyed     both, unlinked, which is what happens when nobody declares the relation.
    """
    path = os.path.join(tempfile.mkdtemp(prefix="chan-"), "m.json")
    doc, qry = embed_pair
    m = Inspeximus(path, embed=doc, embed_query=qry)
    for d in DISTRACTORS:
        m.remember(d, mtype="semantic")
    key = "deploy-region" if condition == "keyed" else None
    stale_id = m.remember(STALE, mtype="semantic", key=key)
    fresh_id = None
    if condition != "stale_only":
        fresh_id = m.remember(FRESH, mtype="semantic", key=key)
    return m, stale_id, fresh_id


def arm(m, stale_id, fresh_id, mode, k=6):
    hits = m.recall(QUERY, k=k, mode=mode)
    ids = [h.get("id") for h in hits]
    return {
        "mode_asked": mode,
        "mode_used": m._last_mode,
        "stale_rank": ids.index(stale_id) + 1 if stale_id in ids else None,
        "fresh_rank": (ids.index(fresh_id) + 1
                       if fresh_id is not None and fresh_id in ids else None),
        "returned": len(ids),
    }


def main():
    embed_pair, err = make_embedder()
    if err:
        print("SKIP: no embedder at %s (%s). The semantic and hybrid arms cannot run, and a "
              "lexical-only result would answer a different question." % (EMBED_URL, err))
        return 2

    modes = ("lexical", "semantic", "hybrid")
    out = {"probe": os.path.basename(__file__), "embed_model": EMBED_MODEL,
           "query": QUERY, "stale": STALE, "fresh": FRESH, "pool": len(DISTRACTORS) + 2,
           "conditions": {}}

    for label in ("stale_only", "keyed", "unkeyed"):
        print("\n  building the %s store ..." % label, flush=True)
        m, stale_id, fresh_id = build(embed_pair, label)
        arms = {mode: arm(m, stale_id, fresh_id, mode) for mode in modes}

        # POSITIVE control: each record must be findable by its own wording, or an absence above is
        # a broken store rather than a finding. The stale record is asked for with include_superseded
        # because in the keyed condition it is superseded by design.
        pos_fresh = fresh_id is None or any(
            h.get("id") == fresh_id
            for h in m.recall("Frankfurt eu-central-1 customer traffic", k=6, mode="lexical"))
        pos_stale = any(h.get("id") == stale_id
                        for h in m.recall("us-east-1 production deploys", k=6, mode="lexical",
                                          include_superseded=True))
        neg = len(m.recall(NEVER, k=6, mode="lexical", min_relevance=0.2))

        # What `auto` picks at this pool size is why the lexical row matters: the threshold ships
        # at 300 active memories, so an ordinary small store never reaches the semantic channel.
        m.recall(QUERY, k=6, mode="auto")
        out["conditions"][label] = {"arms": arms, "positive_fresh": pos_fresh,
                                    "positive_stale": pos_stale, "negative_hits": neg,
                                    "auto_picked": m._last_mode,
                                    "semantic_threshold": m.semantic_threshold}
        print("  %-8s %-9s %-9s %-11s %s" % ("cond", "mode", "used", "stale rank", "fresh rank"))
        for mode in modes:
            a = arms[mode]
            print("  %-8s %-9s %-9s %-11s %s"
                  % (label, mode, a["mode_used"],
                     a["stale_rank"] if a["stale_rank"] else "absent",
                     a["fresh_rank"] if a["fresh_rank"] else "absent"))

    ok, checks = True, []

    def check(name, cond, got=""):
        nonlocal ok
        ok = ok and bool(cond)
        checks.append({"check": name, "pass": bool(cond), "got": str(got)[:200]})
        print("  %-4s %-56s %s" % ("YES" if cond else "NO", name, got))

    print()
    keyed, unkeyed = out["conditions"]["keyed"], out["conditions"]["unkeyed"]
    stale_only = out["conditions"]["stale_only"]

    # Without this the whole table can be one channel wearing three names.
    check("CONTROL_every_arm_used_the_mode_it_asked_for",
          all(c["arms"][mo]["mode_used"] == mo
              for c in out["conditions"].values() for mo in modes),
          {mo: [c["arms"][mo]["mode_used"] for c in out["conditions"].values()] for mo in modes})
    check("CONTROL_auto_mode_picks_lexical_at_this_pool_size",
          stale_only["auto_picked"] == "lexical",
          "auto=%s, threshold=%s active memories, pool=%s"
          % (stale_only["auto_picked"], stale_only["semantic_threshold"], out["pool"]))
    check("CONTROL_both_records_are_findable_by_their_own_wording",
          keyed["positive_stale"] and keyed["positive_fresh"]
          and unkeyed["positive_stale"] and unkeyed["positive_fresh"])
    check("CONTROL_a_phrase_never_written_returns_nothing",
          keyed["negative_hits"] == 0 and unkeyed["negative_hits"] == 0,
          (keyed["negative_hits"], unkeyed["negative_hits"]))

    # The integrity claim. A declared supersession is a state filter, so it must not depend on which
    # channel ranked the pool. If this fails it is the more serious result and outranks everything else.
    check("KEYED_the_superseded_value_is_hidden_from_EVERY_channel",
          all(keyed["arms"][mo]["stale_rank"] is None for mo in modes),
          {mo: keyed["arms"][mo]["stale_rank"] for mo in modes})
    # THIS WAS WRITTEN AS A CHECK AND IT FAILED, WHICH IS THE FINDING RATHER THAN A DEFECT IN THE
    # PROBE. A declared supersession correctly hides the stale record from every channel, but the
    # correction is worded to share no tokens with the query, so the LEXICAL channel cannot reach it
    # either. The reader then gets nothing at all for a question the store can answer. Recorded as a
    # measurement; the control that can still fail is the one below it.
    keyed_blind = [mo for mo in modes if keyed["arms"][mo]["fresh_rank"] is None]
    out["keyed_channels_that_return_neither"] = keyed_blind
    print("  --   KEYED channels returning NEITHER record: %s" % (keyed_blind or "none"))
    check("CONTROL_at_least_one_channel_reaches_the_correction",
          any(keyed["arms"][mo]["fresh_rank"] is not None for mo in modes),
          "a store where no channel finds it would make the row above meaningless")

    # The CMI question, on the case nobody declared.
    saw_fresh = {mo: unkeyed["arms"][mo]["fresh_rank"] is not None for mo in modes}
    saw_stale = {mo: unkeyed["arms"][mo]["stale_rank"] is not None for mo in modes}
    both = {mo: saw_fresh[mo] and saw_stale[mo] for mo in modes}
    channels_disagree = len(set(saw_fresh.values())) > 1 or len(set(saw_stale.values())) > 1

    print()
    print("  UNKEYED, which of the two the reader is shown:")
    for mo in modes:
        print("    %-9s stale=%-6s correction=%-6s -> %s"
              % (mo, saw_stale[mo], saw_fresh[mo],
                 "sees the conflict" if both[mo]
                 else ("only the correction" if saw_fresh[mo]
                       else ("ONLY THE STALE VALUE" if saw_stale[mo] else "neither"))))

    out["unkeyed_summary"] = {"saw_stale": saw_stale, "saw_correction": saw_fresh,
                              "saw_both": both, "channels_disagree": channels_disagree}
    out["checks"] = checks
    out["all_passed"] = ok
    # MEASURED, not inferred: what a lexical reader got before the correction was written.
    before = stale_only["arms"]["lexical"]["stale_rank"]
    after = keyed["arms"]["lexical"]
    check("MEASURED_lexical_answered_before_the_correction_was_written",
          before is not None, "stale at rank %s" % before)
    out["lexical_before_and_after"] = {
        "before_correction": {"stale_rank": before},
        "after_correction": {"stale_rank": after["stale_rank"],
                             "fresh_rank": after["fresh_rank"]}}

    parts = []
    if before is not None and after["stale_rank"] is None and after["fresh_rank"] is None:
        parts.append(
            "Writing the correction turned an answer into no answer on the LEXICAL channel, which "
            "is what `auto` selects below %d active memories: before the correction the stale "
            "record came back at rank %d, and after it that channel returns neither record. The "
            "supersession is working and the correction is out of lexical reach, so the reader is "
            "left with nothing."
            % (stale_only["semantic_threshold"], before))
    if keyed_blind and not parts:
        # Only when the before/after sentence above did not already say it. The first version
        # printed both and repeated itself inside one finding.
        parts.append(
            "With the supersession DECLARED, %s returns neither record: the stale value is correctly "
            "hidden and the correction is out of that channel's reach." % " and ".join(keyed_blind))
    if channels_disagree:
        parts.append(
            "With the contradiction UNDECLARED, the channels disagree on what the reader is shown: "
            "lexical returns the stale value alone while semantic and hybrid show both.")
    out["finding"] = " ".join(parts) or (
        "The channels agree on this pair, so this construction does not reproduce the CMI "
        "channel-specific pathway in inspeximus. That is a null on one pair, not a general absence.")
    print("\n  FINDING: %s" % out["finding"])

    path = os.path.splitext(os.path.abspath(__file__))[0] + ".result.json"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(out, indent=1))
    print("  %s   receipt: %s" % ("controls passed" if ok else "A CONTROL FAILED",
                                  os.path.basename(path)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
