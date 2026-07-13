"""echo_attack_probe_v2.py — full echo-attack panel: verbatim + paraphrased echo vs 7 supersession policies.

Design note (fairness): the OBJECT (the asserted value) is directly OBSERVABLE in the MemBench fixture
(old-value and answer strings). We therefore grant EVERY structured policy ORACLE object extraction, so
the comparison isolates the SUPERSESSION LOGIC, not extractor quality (MemStrata separately admits ~44%
extraction on messy facts — that is an orthogonal axis we do NOT conflate here). LLMs are used ONLY to
generate realistic paraphrased echoes (attack realism), never to score a policy — so the core numbers are
deterministic and reproducible.

Policies (all get: per-message sims, ordered value-bearing events with their {old|new} object, echo idxs):
  cosine            - similarity only, no update semantics
  recency           - last-mention-wins (strawman)
  tie_recent        - mnemo 0.6.8 near-tie recency reorder (HONEST SELF-ATTACK)
  mem0_faithful     - v1 ADD/UPDATE/DELETE proxy: a contradicting new object UPDATEs (replaces) current
                      => last-writer-wins at the object level (no hash, no ledger)
  graphiti_faithful - bi-temporal: an edge is expired only if old.valid_at < new.valid_at; valid_at
                      defaults to event position (latest). A later echo of the old value thus expires the
                      FRESH edge (exactly the getzep/graphiti resolve_edge_contradictions code path)
  memstrata         - SRO last-assertion-wins + VERBATIM-hash short-circuit (catches verbatim echo only)
  ours              - SUPERSEDED-OBJECT LEDGER: when a correction changes the object, the old object is
                      recorded as superseded for that (subject,relation) key; any later assertion of an
                      already-superseded object (verbatim OR paraphrased — object identity, not surface
                      hash) is a restatement-of-superseded and CANNOT resurrect it

Stale metric (retrieval-level): for cosine/tie_recent, does the best OLD-value message (orig or echo)
outrank the best NEW-value message. For the structured policies (recency/mem0/graphiti/memstrata/ours),
"stale" = the policy's final surviving object for the fact is an OLD value. Controls: no-echo arm =
false-invalidation baseline.

RUN: python -u mnemo/probes/echo_attack_probe_v2.py   (needs echo_attack_paraphrases.json from the gen step)
"""
import json, os, sys, time, hashlib
sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, os.path.dirname(__file__))
from echo_attack_probe import build_fixture, embed, cos, _norm, _cache, CACHE, TIE_EPS

PARA = "mnemo/probes/echo_attack_paraphrases.json"
MODELS = ["deepseek-v4-flash", "kimi-k2.7-code", "glm-5.2"]

def object_of(text, old_vals, answer):
    """Oracle object extraction: which value string this message asserts. Prefer the longest match so
    'Houston, TX' isn't shadowed by 'Houston'. Returns ('new'|'old', value_str) or None."""
    cands = [("new", answer)] + [("old", v) for v in old_vals]
    hits = [(tag, v) for tag, v in cands if v.lower() in text.lower()]
    if not hits:
        return None
    hits.sort(key=lambda x: -len(x[1]))
    return hits[0]

def value_events(texts, tags, old_vals, answer):
    """Ordered list of (idx, tag, object_str) for value-bearing messages, by oracle extraction."""
    ev = []
    for i, t in enumerate(texts):
        o = object_of(t, old_vals, answer)
        if o:
            ev.append((i, o[0], o[1]))
    return ev

# ---- retrieval policies (rank-based) ----
def _best_rank(order, idxs):
    pos = {i: r for r, i in enumerate(order)}
    return min((pos[i] for i in idxs), default=10**9)

def p_cosine(sims, ev, old_idxs, new_idxs, texts):
    order = sorted(range(len(sims)), key=lambda i: -sims[i])
    return _best_rank(order, old_idxs) < _best_rank(order, new_idxs)

def p_tie_recent(sims, ev, old_idxs, new_idxs, texts, eps=TIE_EPS):
    order = sorted(range(len(sims)), key=lambda i: -sims[i])
    top = sims[order[0]]
    tied = sorted([i for i in order if sims[i] >= top - eps], key=lambda i: -i)
    order = tied + [i for i in order if sims[i] < top - eps]
    return _best_rank(order, old_idxs) < _best_rank(order, new_idxs)

# ---- structured policies (object-level) ----
def p_recency(sims, ev, old_idxs, new_idxs, texts):
    return ev[-1][1] == "old" if ev else False

def p_mem0(sims, ev, old_idxs, new_idxs, texts):
    current = None
    for _, tag, _o in ev:                       # every contradicting assertion UPDATEs current
        current = tag
    return current == "old"

def p_graphiti(sims, ev, old_idxs, new_idxs, texts):
    # Faithful resolve_edge_contradictions: an incoming edge EXPIRES the current one iff it CONTRADICTS
    # (different object for the same subject/relation) AND current.valid_at < incoming.valid_at. valid_at
    # has no explicit temporal cue in these corrections, so it defaults to event position (monotonic) ->
    # the last contradicting assertion always wins. That default is exactly the echo vulnerability the
    # getzep/graphiti code path exhibits; here it numerically coincides with recency.
    cur_tag, cur_obj, cur_valid = None, None, -1
    for pos, tag, obj in ev:
        if cur_obj is None:
            cur_tag, cur_obj, cur_valid = tag, obj.lower(), pos
            continue
        if obj.lower() != cur_obj and cur_valid < pos:      # contradiction + newer valid_at -> expire+replace
            cur_tag, cur_obj, cur_valid = tag, obj.lower(), pos
    return cur_tag == "old"

def p_memstrata(sims, ev, old_idxs, new_idxs, texts):
    seen, current = set(), None
    for i, tag, _o in ev:
        h = hashlib.sha1(_norm(texts[i]).encode()).hexdigest()
        if h in seen:
            continue                            # verbatim dup -> ignored
        seen.add(h); current = tag
    return current == "old"

def p_ours(sims, ev, old_idxs, new_idxs, texts):
    # superseded-object ledger keyed by (subject,relation) — one fact here, so one key.
    current_obj, current_tag = None, None
    superseded = set()                          # objects that have been corrected away
    for _i, tag, obj in ev:
        key = obj.lower()
        if current_obj is None:
            current_obj, current_tag = key, tag
            continue
        if key == current_obj:
            continue                            # restatement of the CURRENT value -> no change
        if key in superseded:
            continue                            # restatement of an ALREADY-SUPERSEDED value -> IGNORE (echo defense)
        superseded.add(current_obj)             # a genuinely new value corrects the current one
        current_obj, current_tag = key, tag
    return current_tag == "old"

POLICIES = [("cosine", p_cosine), ("recency", p_recency), ("tie_recent", p_tie_recent),
            ("mem0_faithful", p_mem0), ("graphiti_faithful", p_graphiti),
            ("memstrata", p_memstrata), ("ours", p_ours)]

def run(cases, echo_texts):
    """echo_texts: fn(case)->list[str] echo messages to append (empty for control)."""
    acc = {n: [] for n, _ in POLICIES}
    skipped = 0
    for c in cases:
        texts = [m for m, _ in c["msgs"]]
        echoes = echo_texts(c)
        if echoes is None:
            skipped += 1; continue
        texts = texts + echoes
        ev = value_events(texts, None, c["old_vals"], c["answer"])
        old_idxs = [i for i, tag, _ in ev if tag == "old"]
        new_idxs = [i for i, tag, _ in ev if tag == "new"]
        if not old_idxs or not new_idxs:
            skipped += 1; continue
        dvecs = embed(texts, "d")
        qvec = embed([c["q"]], "q")[0]
        sims = [cos(qvec, v) for v in dvecs]
        for n, fn in POLICIES:
            acc[n].append(1.0 if fn(sims, ev, old_idxs, new_idxs, texts) else 0.0)
    rates = {n: (sum(v)/len(v) if v else float("nan")) for n, v in acc.items()}
    return rates, (len(acc["ours"]) if acc["ours"] else 0), skipped

def main():
    cases = build_fixture()
    para = json.load(open(PARA, encoding="utf-8")) if os.path.exists(PARA) else {}
    def cid(c): return f"{c['q'][:60]}|{c['old_idx0']}"
    print(f"{len(cases)} cases; paraphrase records: {len(para)}", flush=True)

    arms = {
        "no_echo":        lambda c: [],
        "verbatim_echo":  lambda c: [c["old_text"]],
    }
    for mdl in MODELS:
        def mk(mdl):
            def f(c):
                p = (para.get(cid(c)) or {}).get(mdl)
                return [p] if p else None       # skip case for this model if no valid paraphrase
            return f
        arms[f"paraphrase_{mdl}"] = mk(mdl)

    results = {}
    t0 = time.time()
    for arm, fn in arms.items():
        rates, n, sk = run(cases, fn)
        results[arm] = {"n": n, "skipped": sk, "rates": rates}
        print(f"\n[{arm}] n={n} skipped={sk}")
        for name, _ in POLICIES:
            base = results.get("no_echo", {}).get("rates", {}).get(name)
            d = f"  (Δ {rates[name]-base:+.3f})" if (base is not None and arm != "no_echo") else ""
            print(f"  {name:18s} stale={rates[name]:.3f}{d}")
    json.dump(_cache, open(CACHE, "w"))
    json.dump(results, open("mnemo/probes/echo_attack_probe_v2_result.json", "w"), indent=2)
    print(f"\n{time.time()-t0:.0f}s -> mnemo/probes/echo_attack_probe_v2_result.json")

if __name__ == "__main__":
    main()
