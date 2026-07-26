"""echo_attack_probe.py — the ECHO ATTACK benchmark: does re-stating a corrected-away fact resurrect it?

THREAT MODEL (prior-art positioned; see agora_output/strategy/20260709_echo-attack-prior-art-*.md):
  A fact is asserted (old value), then CORRECTED in dialogue (fresh value). Later, the OLD value is
  re-stated ("echo") — either a benign restatement or an attacker re-injection. Question: does the memory
  policy now serve the STALE value? Nobody has measured this against real production policies (Zep/Graphiti
  code path suggests it fails; mem0 add-only sends both to retrieval; MemStrata catches only VERBATIM echoes).

FAIRNESS (method-skeptic baked in, per the standing gate):
  - The attack must beat STRONG baselines, not a strawman. Panel includes a provenance-ordered
    (Graphiti-faithful) policy and a MemStrata-style SRO+hash policy, implemented faithfully.
  - Two control axes: (1) NO-ECHO trajectories measure each policy's false-invalidation baseline;
    (2) verbatim vs paraphrased echo separates "hash catches it" from "semantics needed".
  - Metric is retrieval-level stale-answer-rate: does the best OLD-value message (original OR echo)
    outrank the best FRESH-value message for the question. (Answer-level LLM arm is a later stage.)

STAGE 1 (this file): deterministic policies only (free after embedding) + VERBATIM echo. Confirms the hole
  and the MemStrata verbatim-vs-paraphrase contrast before spending LLM tokens.
  Policies:
    cosine        - plain similarity ranking (no update semantics)
    recency       - last-mention-wins (the strawman; included to show it's NOT what we claim against)
    tie_recent    - inspeximus 0.6.8 near-tie recency reorder (HONEST SELF-ATTACK: newest-first => echo wins)
    memstrata     - SRO (subject,relation) last-assertion-wins + VERBATIM-hash short-circuit
                    (a verbatim echo of an already-superseded value is recognized as a dup and ignored)

DATA: agora_output/lab/data/membench/knowledge_update.json. Own embed cache. nomic + asymmetric prefixes.
RUN: python -u research/probes/echo_attack_probe.py
"""
import json, os, sys, re, time, hashlib, urllib.request

sys.stdout.reconfigure(errors="replace")

DATA = os.environ.get("MEMBENCH_DATA", "agora_output/lab/data/membench") + "/knowledge_update.json"
CACHE = "research/probes/echo_attack_embcache_v1.json"
EMB_URL = os.environ.get("OLLAMA_EMBED_URL", "http://localhost:11434/api/embed")
MODEL = "nomic-embed-text"
QP, DP = "search_query: ", "search_document: "
N_PER_CAT = 60
TIE_EPS = 0.05

_cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
_dirty = 0
def _key(role, text): return hashlib.sha1(f"{MODEL}|{role}|{text[:2000]}".encode()).hexdigest()
def _post(inputs):
    inputs = [(s if (s and s.strip()) else " ") for s in inputs]
    body = json.dumps({"model": MODEL, "input": inputs}).encode()
    for a in range(3):
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                EMB_URL, data=body, headers={"Content-Type": "application/json"}), timeout=300)
            return json.loads(r.read())["embeddings"]
        except Exception:
            if a == 2: raise
            time.sleep(2)
def embed(texts, role):
    global _dirty
    pref = QP if role == "q" else DP
    miss = [t for t in texts if _key(role, t) not in _cache]
    for i in range(0, len(miss), 64):
        ch = miss[i:i+64]
        for t, v in zip(ch, _post([pref + t for t in ch])):
            _cache[_key(role, t)] = v
        _dirty += len(ch)
        if _dirty >= 512:
            json.dump(_cache, open(CACHE, "w")); _dirty = 0
    return [_cache[_key(role, t)] for t in texts]
def cos(a, b):
    return sum(x*y for x, y in zip(a, b)) / ((sum(x*x for x in a)**0.5)*(sum(x*x for x in b)**0.5)+1e-12)

def build_fixture():
    """-> list of cases: dict(question, msgs=[(text, tag)], old_idx0, new_idx0). tag in
    {'ctx','old','new'}; 'old'/'new' mark value-bearing messages. Echoes are appended by the runner."""
    d = json.load(open(DATA, encoding="utf-8"))
    cases = []
    for cat in ("roles", "events"):
        for t in d[cat][:N_PER_CAT]:
            flat = [m["user_message"] for s in t["message_list"] for m in s]
            qa = t["QA"]; ans = qa["answer"]
            gt_new = [i for i, m in enumerate(flat) if ans.lower() in m.lower()]
            old_vals = [v for c, v in qa["choices"].items() if c != qa["ground_truth"]]
            gt_old = [i for i, m in enumerate(flat)
                      if i not in gt_new and any(v.lower() in m.lower() for v in old_vals)]
            if not (gt_new and gt_old and min(gt_old) < min(gt_new)):
                continue
            msgs = [(m, ("new" if i in gt_new else "old" if i in gt_old else "ctx"))
                    for i, m in enumerate(flat)]
            cases.append({"q": qa["question"], "msgs": msgs,
                          "old_idx0": min(gt_old), "new_idx0": min(gt_new),
                          "old_text": flat[min(gt_old)], "answer": ans,
                          "old_vals": old_vals})
    return cases

# ---------------- policies: given per-message sims + tags + texts, return stale? (bool) ----------------
def _best_rank(order, idxs):
    pos = {i: r for r, i in enumerate(order)}
    return min((pos[i] for i in idxs), default=10**9)

def policy_cosine(sims, tags, texts, echo_idxs):
    order = sorted(range(len(sims)), key=lambda i: -sims[i])
    old = [i for i, t in enumerate(tags) if t == "old"] + echo_idxs
    new = [i for i, t in enumerate(tags) if t == "new"]
    return _best_rank(order, old) < _best_rank(order, new)

def policy_recency(sims, tags, texts, echo_idxs):
    # last-mention-wins among value-bearing messages: whichever of old/new was asserted LAST wins;
    # echoes are late -> old wins. (The strawman, shown for contrast.)
    old = [i for i, t in enumerate(tags) if t == "old"] + echo_idxs
    new = [i for i, t in enumerate(tags) if t == "new"]
    return max(old) > max(new)

def policy_tie_recent(sims, tags, texts, echo_idxs, eps=TIE_EPS):
    # inspeximus 0.6.8: within eps of top sim, newest-first (index as time). Honest self-attack.
    order = sorted(range(len(sims)), key=lambda i: -sims[i])
    top = sims[order[0]]
    tied = sorted([i for i in order if sims[i] >= top - eps], key=lambda i: -i)
    rest = [i for i in order if sims[i] < top - eps]
    order = tied + rest
    old = [i for i, t in enumerate(tags) if t == "old"] + echo_idxs
    new = [i for i, t in enumerate(tags) if t == "new"]
    return _best_rank(order, old) < _best_rank(order, new)

def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()

def policy_memstrata(sims, tags, texts, echo_idxs, old_vals=None, answer=None):
    """SRO last-assertion-wins + VERBATIM-hash short-circuit. Faithful approximation: all old/new
    value-bearing messages share one (subject,relation) key (they answer the same question). Process
    messages in order; a message asserting the CURRENT value supersedes. A message whose normalized text
    hashes to an ALREADY-SEEN asserted text is a verbatim dup -> ignored (the hash short-circuit).
    Stale iff the LAST surviving asserted value is an OLD value."""
    seen_hashes = set()
    current = None   # 'old' or 'new'
    for i, (t) in enumerate(tags):
        if t not in ("old", "new"):
            continue
        h = hashlib.sha1(_norm(texts[i]).encode()).hexdigest()
        if h in seen_hashes:
            continue                      # verbatim restatement of a seen assertion -> ignored
        seen_hashes.add(h)
        current = t                        # last non-dup assertion wins
    return current == "old"

POLICIES = [("cosine", policy_cosine), ("recency", policy_recency),
            ("tie_recent", policy_tie_recent), ("memstrata", policy_memstrata)]

def run_arm(cases, echo_kind):
    """echo_kind in {'none','verbatim'}. Returns {policy: stale_rate}."""
    res = {name: [] for name, _ in POLICIES}
    for c in cases:
        texts = [m for m, _ in c["msgs"]]
        tags = [t for _, t in c["msgs"]]
        echo_idxs = []
        if echo_kind == "verbatim":
            texts = texts + [c["old_text"]]
            tags = tags + ["echo"]        # echo is not tagged 'old' for policies that read tags, but
            echo_idxs = [len(texts) - 1]  # ...it IS an old-value message: pass its index explicitly
        dvecs = embed(texts, "d")
        qvec = embed([c["q"]], "q")[0]
        sims = [cos(qvec, v) for v in dvecs]
        for name, fn in POLICIES:
            if name == "memstrata":
                # memstrata reads tags; make the echo carry the OLD tag so its hash is checked
                tg = list(tags)
                for ei in echo_idxs: tg[ei] = "old"
                stale = fn(sims, tg, texts, echo_idxs, old_vals=c["old_vals"], answer=c["answer"])
            else:
                stale = fn(sims, tags, texts, echo_idxs)
            res[name].append(1.0 if stale else 0.0)
    return {name: sum(v)/len(v) for name, v in res.items()}, len(cases)

def main():
    cases = build_fixture()
    print(f"echo-attack fixture: {len(cases)} clean cases (old-before-correction)", flush=True)
    t0 = time.time()
    noecho, n = run_arm(cases, "none")
    print(f"\n[CONTROL: no echo] n={n}  (false-invalidation / baseline stale rate)")
    for name, _ in POLICIES:
        print(f"  {name:12s} stale={noecho[name]:.3f}")
    verb, _ = run_arm(cases, "verbatim")
    print(f"\n[ATTACK: verbatim echo of the corrected-away value] n={n}")
    for name, _ in POLICIES:
        print(f"  {name:12s} stale={verb[name]:.3f}  (Δ {verb[name]-noecho[name]:+.3f})")
    json.dump(_cache, open(CACHE, "w"))
    out = {"n": n, "no_echo": noecho, "verbatim_echo": verb}
    json.dump(out, open("research/probes/echo_attack_probe_result.json", "w"), indent=2)
    print(f"\n{time.time()-t0:.0f}s -> research/probes/echo_attack_probe_result.json")

if __name__ == "__main__":
    main()
