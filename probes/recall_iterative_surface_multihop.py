"""recall_iterative_surface_multihop.py — does the TWO-PHASE surface actually reach the bridge record?

WHAT IS UNDER TEST. `recall_iterative()` is the one retrieval lever our lab measured to move multi-hop
recall, and it takes a Python CALLABLE (`ask_followup`), so no MCP client and no shell could reach it. The
surface added in this change inverts it into two stateless calls — `recall_iterative_start()` (round-1 hits +
the instruction + a continuation token) and `recall_iterative_followup()` (the caller's queries come back, we
retrieve and return only what is NEW). This probe measures whether that inversion RETRIEVES THE BRIDGE.

THE METRIC, and it is the one that can fail:

    bridge rate = of the questions where a single recall(k=6) MISSES a required record,
                  the fraction where the two-call sequence retrieves it.

CONTROL 1 (fixture is not too easy). A question whose bridge record is ALREADY in the single recall(k=6)
cannot measure anything — the lever has nothing to do there. On the synthetic fixture the probe FAILS if a
single one of them is easy: the fixture is ours, so a leak is a defect to fix, not a row to drop. On LoCoMo
the fixture is external, so easy questions are EXCLUDED and their count is reported, because dropping them
silently is how a denominator flatters itself.

CONTROL 2 (no regression on single-hop). On questions answerable from round-1, the two-call path must not do
worse than plain recall. Asserted as a set relation — the records the caller ends with must be a SUPERSET of
plain recall's — which is the only formulation that stays true if the merge is ever rewritten.

THE READER IS A STAND-IN, AND IT IS DELIBERATELY WEAK. In production the follow-up queries come from the
CALLER's model; that is the entire point of the inversion. A probe cannot call one and stay zero-dependency,
so it uses a mechanical, gold-BLIND reader: proper nouns appearing in round-1 but not in the question, then
the rarest content tokens under the same rule. It sees exactly what a model would (question + round-1 hits)
and never the answer. It is strictly less capable than a model, so every number here is a LOWER BOUND on the
surface's reach, never an estimate of the lever's ceiling. Our own lab already measured this class of
zero-LLM bridging as null on LoCoMo (entity-bridge 0.116-0.149 against a 0.145 flat baseline), so a weak or
null LoCoMo arm here is the EXPECTED result and is reported as such rather than tuned away.

    RUN (synthetic fixture, ships with the repo, no external data):
        python probes/recall_iterative_surface_multihop.py
    RUN (also the LoCoMo arm — needs the corpus, which this repo does not ship):
        python probes/recall_iterative_surface_multihop.py --locomo path/to/locomo10.json

Exit code 0 = every control held. Non-zero = a control failed (the fixture leaked, or the surface regressed).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))                    # the repo under test, not pip's copy

from inspeximus import Inspeximus                            # noqa: E402

# ── the caller's-model stand-in ───────────────────────────────────────────────────────────────────────
# Defined ONCE, before either fixture is written, and applied unchanged to both. A reader tuned per fixture
# would be measuring my tuning rather than the surface.
_PROPER = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b")
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")
_READER_STOP = frozenset("""
the a an of for to in on at and or is are was were be been being with this that it its as by from into
what which who whom whose where when why how does did do done would will can could should may might
person people thing about their there here they them his her he she you your our we us not no yes
""".split())


def mechanical_reader(question: str, hits: list, max_followups: int = 3) -> list:
    """(question, round-1 hits) -> up to `max_followups` follow-up queries. Gold-blind and deterministic.

    Rule, in order: proper-noun phrases named in the hits but NOT in the question (the classic bridge
    entity), then the rarest content tokens under the same "in the hits, not in the question" rule. This is
    a floor, not a model — see the module docstring."""
    qwords = {w.lower() for w in _WORD.findall(question or "")}
    texts = [h.get("text", "") or "" for h in hits]
    out, seen = [], set()
    for t in texts:
        for name in _PROPER.findall(t):
            low = name.lower()
            if low in seen or any(w in qwords for w in low.split()):
                continue
            seen.add(low)
            out.append(name)
    if len(out) < max_followups:
        df: dict = {}
        for t in texts:
            for w in {x.lower() for x in _WORD.findall(t)}:
                df[w] = df.get(w, 0) + 1
        rare = sorted((w for w, c in df.items()
                       if w not in qwords and w not in _READER_STOP and w not in seen and len(w) > 3),
                      key=lambda w: (df[w], w))
        out.extend(rare[: max_followups - len(out)])
    return out[:max_followups]


# ── fixture A: synthetic, ships with the repo, runs anywhere ──────────────────────────────────────────
_NAMES = ["Priya Raman", "Tomas Vlk", "Hannah Delgado", "Ingrid Solberg", "Kwame Boateng",
          "Yuki Tanabe", "Rosa Ferreira", "Dimitri Kovac", "Nadia Haddad", "Owen Fitzgerald",
          "Mei Chen", "Sofia Ricci", "Alexei Petrov", "Grace Mwangi", "Bjorn Lindqvist",
          "Amara Okonkwo", "Lucas Moreau", "Hana Novak", "Rafael Duarte", "Anya Sokolova"]
_TOPICS = ["marketing spend", "warehouse leasing", "payroll consolidation", "vendor onboarding",
           "cloud migration", "fleet insurance", "retail packaging", "data retention",
           "field logistics", "seasonal hiring", "channel rebates", "plant maintenance",
           "export licensing", "customer refunds", "network upgrade", "tooling renewal",
           "site security", "translation costs", "audit remediation", "product photography"]
_CITIES = ["Lisbon", "Oslo", "Naha", "Cork", "Tallinn", "Perth", "Bruges", "Kandy", "Salta", "Malmo",
           "Aarhus", "Utrecht", "Nantes", "Bergen", "Cebu", "Rijeka", "Trieste", "Gdansk", "Leon", "Turku"]
_MONTHS = ["April", "June", "August", "October", "February", "December", "March", "May", "July",
           "September", "November", "January", "April", "June", "August", "October", "February",
           "December", "March", "May"]


def build_synthetic(n_items: int = 20, n_filler: int = 200, path: str | None = None):
    """A store with a genuine BRIDGE structure, plus crowding.

    For each item: a hop-1 record that shares the question's vocabulary, and a hop-2 record that shares the
    question's vocabulary NOT AT ALL — only the entity name links them. That is what makes the second hop
    unreachable by ranking: it is not about the question, it is about the bridge.

    Crowding is deliberate. Committee records put SEVERAL names into round-1, so the reader has to spend its
    follow-up budget on candidates and only one of them is right. A fixture where round-1 names exactly one
    entity would measure nothing but the reader's ability to copy it."""
    m = Inspeximus(path=path or os.path.join(os.environ.get("TEMP", "/tmp"),
                                             f"ri_probe_{os.getpid()}_{time.time_ns()}.json"))
    items = []
    for i in range(n_items):
        name, topic = _NAMES[i % len(_NAMES)], _TOPICS[i % len(_TOPICS)]
        city, month = _CITIES[i % len(_CITIES)], _MONTHS[i % len(_MONTHS)]
        hop1_text = f"{name} signed off on the {topic} review"
        bridge_text = f"{name} relocated to {city} in {month}"                 # the BRIDGE record
        m.remember(hop1_text)
        m.remember(bridge_text)
        # crowding: the same review named with OTHER people on it, so round-1 carries rival names
        other = _NAMES[(i + 7) % len(_NAMES)]
        third = _NAMES[(i + 13) % len(_NAMES)]
        m.remember(f"the {topic} review committee also included {other} and {third}")
        m.remember(f"the {topic} review was reopened after the quarterly close")
        items.append({
            "question": f"which city does the person who signed off on the {topic} review work from",
            # identity is the TEXT, not the id: ids are per-store, and comparing them across two stores is
            # how the first version of this probe scored a real result as 0/20.
            "hop1_text": hop1_text, "bridge_text": bridge_text, "entity": name,
            # CONTROL 2's arm: answerable from round-1 alone, no bridge needed
            "single_hop_question": f"who signed off on the {topic} review",
            "single_hop_text": hop1_text,
        })
    for j in range(n_filler):
        m.remember(f"filler note {j}: the operations handbook section {j % 37} was reviewed and archived")
    return m, items


# ── fixture B: LoCoMo (external; this repo does not ship the corpus) ──────────────────────────────────
def build_locomo(path: str, max_convs: int = 10):
    """Yield (store, items, rec2turn) per conversation. Multi-hop is defined HERE, explicitly: a question
    with >= 2 gold evidence turns, excluding category 5 (adversarial). That is this probe's operating point
    and it is NOT the n=276 filter the published 0.145 -> 0.297 run used — see the report footer."""
    data = json.load(open(path, encoding="utf-8"))[:max_convs]
    for sample in data:
        conv = sample["conversation"]
        turns = []
        for skey in sorted([k for k in conv if k.startswith("session_") and not k.endswith("date_time")],
                           key=lambda s: int(s.split("_")[1])):
            date = conv.get(skey + "_date_time", "")
            for t in conv[skey]:
                tid = t.get("dia_id") or t.get("id") or ""
                body = t.get("text", "") or t.get("clean_text", "")
                if body.strip():
                    turns.append((tid, t.get("speaker", ""), f"[{date}] {body}" if date else body))
        m = Inspeximus(path=os.path.join(os.environ.get("TEMP", "/tmp"),
                                         f"ri_locomo_{os.getpid()}_{time.time_ns()}.json"))
        # id -> turn, NOT text -> turn. Two LoCoMo turns can carry identical text (short replies), and a
        # text-keyed map overwrites on collision: a genuinely new bridge record would then resolve to a turn
        # id round 1 had already seen and be subtracted out of `new - got`, undercounting the lever. Keying
        # on the store's own record id cannot collide.
        rec2turn = {}
        for tid, sp, tx in turns:
            rec2turn[m.remember(f"{sp}: {tx}")] = tid
        items = [{"question": q["question"], "gold": {str(e) for e in (q.get("evidence") or [])}}
                 for q in sample["qa"]
                 if q.get("category") != 5 and len(q.get("evidence") or []) >= 2]
        yield m, items, rec2turn


# ── the measurement ───────────────────────────────────────────────────────────────────────────────────
def _ids(hits):
    return [h.get("id") for h in hits]


def measure_synthetic(k: int = 6, max_followups: int = 3, n_items: int = 20, n_filler: int = 200) -> dict:
    """Single recall(k) vs the two-call surface, on the shipped fixture.

    ONE store, and every read passes reinforce=False. The first version of this used two stores to keep the
    arms from re-ranking each other, and compared record IDS across them -- which are per-store, so the
    single-hop arm scored 0/20 and the bridge arm was comparing ids that could never match. The control
    caught it, which is the only reason it is not still in here. Identity across arms is now the record TEXT
    (unique by construction in this fixture), and non-mutation is asserted directly rather than engineered
    around: the (id, value, last_access) snapshot must be identical before and after the whole measurement,
    so the instrument provably did not move what it was measuring."""
    kw = {"reinforce": False}
    m, items = build_synthetic(n_items, n_filler)
    snapshot_before = [(r["id"], r.get("value"), r.get("last_access")) for r in m.items]

    def texts(hits):
        return {h.get("text", "") for h in hits}

    def texts_of_ids(ids):
        by_id = {r["id"]: r.get("text", "") for r in m.items}
        return {by_id.get(i, "") for i in ids}

    too_easy, eligible, bridged, missed = [], 0, 0, []
    for it in items:
        plain = m.recall(it["question"], k=k, **kw) or []
        if it["bridge_text"] in texts(plain):
            too_easy.append(it["question"])                  # CONTROL 1: this question measures nothing
            continue
        eligible += 1
        start = m.recall_iterative_start(it["question"], k=k, max_followups=max_followups, **kw)
        fups = mechanical_reader(it["question"], start["hits"], max_followups)
        nxt = m.recall_iterative_followup(it["question"], followups=fups,
                                          prior_ids=start["prior_ids"], k=k,
                                          max_followups=max_followups, **kw)
        if it["bridge_text"] in texts(nxt["new_hits"]):
            bridged += 1
        else:
            missed.append({"question": it["question"], "followups": fups})

    # CONTROL 2: single-hop must not regress. The set the caller ends with must contain plain recall's.
    regressions, sh_plain, sh_iter = [], 0, 0
    for it in items:
        q = it["single_hop_question"]
        plain = m.recall(q, k=k, **kw) or []
        start = m.recall_iterative_start(q, k=k, max_followups=max_followups, **kw)
        fups = mechanical_reader(q, start["hits"], max_followups)
        nxt = m.recall_iterative_followup(q, followups=fups, prior_ids=start["prior_ids"],
                                          k=k, max_followups=max_followups, **kw)
        if not set(start["prior_ids"]) <= set(nxt["merged_ids"]):
            regressions.append(q)
        sh_plain += int(it["single_hop_text"] in texts(plain))
        sh_iter += int(it["single_hop_text"] in texts_of_ids(nxt["merged_ids"]))
    if sh_iter < sh_plain:
        regressions.append(f"single-hop hit rate fell: plain {sh_plain} -> iterative {sh_iter}")

    snapshot_after = [(r["id"], r.get("value"), r.get("last_access")) for r in m.items]
    if snapshot_before != snapshot_after:
        regressions.append("the measurement MUTATED the store it was measuring (reinforce leaked)")

    return {"fixture": "synthetic (shipped)", "k": k, "max_followups": max_followups,
            "questions": len(items), "too_easy": len(too_easy), "eligible": eligible,
            "bridged": bridged,
            "bridge_rate_single": 0.0,                       # by construction of `eligible`
            "bridge_rate_iterative": round(bridged / eligible, 3) if eligible else None,
            "single_hop_hits_plain": sh_plain, "single_hop_hits_iterative": sh_iter,
            "regressions": regressions, "misses": missed[:5],
            "controls_ok": not too_easy and not regressions}


def measure_locomo(path: str, k: int = 6, max_followups: int = 3, max_convs: int = 10) -> dict:
    """The same measurement on an EXTERNAL multi-hop corpus, lexical recall, gold-blind mechanical reader."""
    kw = {"reinforce": False}
    total, too_easy, eligible, bridged = 0, 0, 0, 0
    for m, items, rec2turn in build_locomo(path, max_convs):
        for it in items:
            total += 1
            gold = it["gold"]
            plain = m.recall(it["question"], k=k, **kw) or []
            got = {rec2turn.get(h.get("id")) for h in plain} - {None}
            if gold <= got:
                too_easy += 1                                # nothing left for a second hop to find
                continue
            eligible += 1
            start = m.recall_iterative_start(it["question"], k=k, max_followups=max_followups, **kw)
            fups = mechanical_reader(it["question"], start["hits"], max_followups)
            nxt = m.recall_iterative_followup(it["question"], followups=fups,
                                              prior_ids=start["prior_ids"], k=k,
                                              max_followups=max_followups, **kw)
            new = {rec2turn.get(h.get("id")) for h in nxt["new_hits"]} - {None}
            if gold & new - got:
                bridged += 1
    return {"fixture": f"LoCoMo ({os.path.basename(path)})", "k": k, "max_followups": max_followups,
            "multi_hop_definition": ">=2 gold evidence turns, category != 5",
            "questions": total, "too_easy_excluded": too_easy, "eligible": eligible,
            "bridged": bridged,
            "bridge_rate_iterative": round(bridged / eligible, 3) if eligible else None,
            "reader": "mechanical, gold-blind — a LOWER BOUND, not the lever's ceiling"}


def measure_bounds(k: int = 6, max_followups: int = 3) -> dict:
    """The response-size and work bounds, measured rather than asserted in prose.

    `mcp_server.py` documents one surface at O(n^2)/162 s (n=8,000) and another at ~150 MB of JSON-RPC
    (n=2,000). The claim for this one is that its cost depends on (k, max_followups) and NOT on n, so it is
    measured at two store sizes an order of magnitude apart and both numbers are printed."""
    out = {}
    for n in (250, 4000):
        m, items = build_synthetic(n_items=20, n_filler=n)
        calls = {"n": 0}
        raw = m.recall

        def counted(*a, **kw):
            calls["n"] += 1
            return raw(*a, **kw)

        m.recall = counted
        q = items[0]["question"]
        t0 = time.perf_counter()
        start = m.recall_iterative_start(q, k=k, max_followups=max_followups, reinforce=False)
        c1 = calls["n"]
        fups = mechanical_reader(q, start["hits"], max_followups)
        nxt = m.recall_iterative_followup(q, followups=fups, prior_ids=start["prior_ids"], k=k,
                                          max_followups=max_followups, reinforce=False)
        elapsed = time.perf_counter() - t0
        out[f"n={len(m.items)}"] = {
            "recall_calls_phase1": c1, "recall_calls_phase2": calls["n"] - c1,
            "records_phase1": len(start["hits"]), "records_phase2": len(nxt["new_hits"]),
            "bytes_phase1": len(json.dumps(start, default=str)),
            "bytes_phase2": len(json.dumps(nxt, default=str)),
            "seconds_both_phases": round(elapsed, 4),
        }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-k", type=int, default=6)
    ap.add_argument("--max-followups", type=int, default=3)
    ap.add_argument("--items", type=int, default=20)
    ap.add_argument("--filler", type=int, default=200)
    ap.add_argument("--locomo", default=os.environ.get("LOCOMO_JSON", ""),
                    help="path to locomo10.json (this repo does not ship it); env LOCOMO_JSON also works")
    ap.add_argument("--convs", type=int, default=10)
    a = ap.parse_args(argv)

    syn = measure_synthetic(a.k, a.max_followups, a.items, a.filler)
    bounds = measure_bounds(a.k, a.max_followups)
    report = {"synthetic": syn, "bounds": bounds}

    print("=" * 78)
    print("TWO-PHASE recall_iterative SURFACE — bridge retrieval, single vs iterative")
    print("=" * 78)
    print(json.dumps(syn, indent=1))
    print("\nBOUNDS (cost must depend on k and max_followups, never on store size):")
    print(json.dumps(bounds, indent=1))

    if a.locomo and os.path.exists(a.locomo):
        loc = measure_locomo(a.locomo, a.k, a.max_followups, a.convs)
        report["locomo"] = loc
        print("\nLoCoMo arm:")
        print(json.dumps(loc, indent=1))
    elif a.locomo:
        print(f"\nLoCoMo arm SKIPPED: no such file {a.locomo!r}")
    else:
        print("\nLoCoMo arm SKIPPED: pass --locomo <path> (the corpus is not shipped with this repo)")

    print("\nPUBLISHED NUMBERS FOR THIS LEVER, and what they are NOT:")
    print("  n=276, all 10 LoCoMo conversations : flat 0.145 -> iterative 0.297  = 2.05x   <- the citable one")
    print("  n=70,  the 3 hardest conversations : flat 0.057 -> iterative 0.186  = 3.3x    <- a SUBSET")
    print("  Both used a MODEL reader plus local nomic-embed vectors and full-evidence recall@50. Neither is")
    print("  reproduced above: this probe runs lexical recall at k=6 with a mechanical reader, which is a")
    print("  different and much weaker operating point. The numbers above are this probe's own.")

    with open(os.path.join(HERE, "recall_iterative_surface_multihop_result.json"), "w",
              encoding="utf-8") as f:
        json.dump({k: v for k, v in report.items() if k != "locomo"}, f, indent=1)
    # The LoCoMo receipt goes in its OWN file. The corpus is not shipped, so the standard run cannot produce
    # this arm -- and if it shared the file above, every ordinary run (including CI's) would silently
    # overwrite the receipt that the docs' 19/394 claim rests on.
    if "locomo" in report:
        with open(os.path.join(HERE, "recall_iterative_surface_multihop_locomo_result.json"), "w",
                  encoding="utf-8") as f:
            json.dump(report["locomo"], f, indent=1)

    ok = syn["controls_ok"] and syn["bridged"] > 0
    if not syn["controls_ok"]:
        print("\nFAIL: a control did not hold — "
              f"too_easy={syn['too_easy']}, regressions={syn['regressions']}")
    elif not ok:
        print("\nFAIL: the two-call sequence bridged nothing; the surface did not reach a single record "
              "that plain recall missed")
    else:
        print(f"\nPASS: controls held; the two-call sequence reached the bridge on "
              f"{syn['bridged']}/{syn['eligible']} questions where a single recall(k={a.k}) did not.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
