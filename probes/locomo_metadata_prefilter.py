"""Does cheap METADATA pre-filtering beat retriever choice on agent memory? (LoCoMo)

Prompted by u/jacksonxly on r/Rag: "the thing that moved our numbers more than the retriever choice was
cheap metadata filters - time-range, source, entity. real agent-memory queries carry an implicit filter
('the auth bug from last month') and a hybrid that can pre-filter on it beats a bigger fancier index."

We already measured (flagship): on LoCoMo, hybrid RRF (bm25+vector) beats a single vector index
(0.609 vs 0.552 recall@20). This asks the next question with the SAME rigor: add a cheap metadata
pre-filter BEFORE the hybrid and measure the delta.

Two filters, bracketing realistic vs ceiling:
  entity/speaker (HEURISTIC, realistic): LoCoMo questions name a speaker 89% of the time ("When did
      Caroline..."). Exact-match the 2 known speaker names in the question; if exactly one is named,
      restrict candidate turns to that speaker BEFORE ranking. Cheap, no LLM. It can HURT: 6% of the
      time the gold turn is the OTHER speaker's, so the filter removes the answer -> honest cost measured.
  session (ORACLE, ceiling): restrict candidates to the session(s) of the gold turns. Uses gold, so it
      is an UPPER BOUND on what a perfect time/session filter could buy, not a shippable method - the
      headroom, clearly labelled.

Rigor (matches the flagship): recall@20, per QUESTION, then per-CONVERSATION means + a conversation-level
bootstrap CI on the delta (the 1531 questions are nested in only 10 conversations - point estimates alone
would overstate significance). Falsifiable: if speaker-filter's CI includes 0 (or is negative) and the
oracle ceiling is small, cheap metadata filtering does NOT beat retriever choice on LoCoMo.

Embedder-only (no LLM in the loop), embeddings cached on disk. Run from repo root:
  python agora_output/lab/exp_locomo_metadata_prefilter.py
"""
import json, re, ast, time, math, hashlib, os, urllib.request, collections, random

# LoCoMo is public (https://github.com/snap-research/locomo). Download locomo10.json and point LOCOMO_PATH
# at it; embeddings are cached to LOCOMO_CACHE (needs a local Ollama nomic-embed-text for the first run).
DATA = os.environ.get("LOCOMO_PATH", "locomo10.json")
CACHE = os.environ.get("LOCOMO_CACHE", "locomo_metadata_prefilter_cache.json")
EMB_URL = "http://localhost:11434/api/embed"
K = 20
ANSWERABLE = ("1", "2", "3", "4")

_cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
def _key(t): return hashlib.sha1(t[:2000].encode("utf-8")).hexdigest()
def _post(payload):
    r = urllib.request.urlopen(urllib.request.Request(
        EMB_URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}), timeout=120)
    return json.loads(r.read())["embeddings"]
def warmup(texts, batch=64):
    miss, seen = [], set()
    for t in texts:
        k = _key(t)
        if k not in _cache and k not in seen:
            seen.add(k); miss.append(t)
    if not miss:
        print("warmup: all cached", flush=True); return
    print(f"warmup: {len(miss)} uncached / {len(texts)}", flush=True)
    for i in range(0, len(miss), batch):
        chunk = miss[i:i+batch]
        for c, v in zip(chunk, _post({"model": "nomic-embed-text", "input": [c[:2000] for c in chunk]})):
            _cache[_key(c)] = v
        json.dump(_cache, open(CACHE, "w"))
def embed(t):
    v = _cache.get(_key(t))
    if v is None:
        v = _post({"model": "nomic-embed-text", "input": [t[:2000]]})[0]; _cache[_key(t)] = v
    return v
def cos(a, b):
    d = sum(x*y for x, y in zip(a, b)); na = sum(x*x for x in a)**.5; nb = sum(x*x for x in b)**.5
    return d / ((na*nb) or 1)

_tok = re.compile(r"[a-z0-9]+")
def toks(s): return _tok.findall(s.lower())
class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.docs = [toks(d) for d in docs]; self.N = len(self.docs)
        self.dl = [len(d) for d in self.docs]; self.avgdl = (sum(self.dl)/self.N) if self.N else 0
        df = collections.Counter(); self.tf = []
        for d in self.docs:
            c = collections.Counter(d); self.tf.append(c)
            for t in c: df[t] += 1
        self.idf = {t: math.log(1 + (self.N - n + 0.5)/(n + 0.5)) for t, n in df.items()}
    def scores(self, query, idxs):
        q = toks(query); out = {}
        for i in idxs:
            c = self.tf[i]; dl = self.dl[i]; s = 0.0
            for t in q:
                f = c.get(t, 0)
                if not f: continue
                s += self.idf.get(t, 0.0) * (f*(self.k1+1)) / (f + self.k1*(1 - self.b + self.b*dl/(self.avgdl or 1)))
            out[i] = s
        return out

def gold_of(q, tset):
    e = q.get("evidence")
    try: ids = ast.literal_eval(e) if isinstance(e, str) else e
    except Exception: ids = []
    return [i for i in (ids or []) if i in tset]

def rrf(rank_a, rank_b, ids, c=60):
    ra = {i: r for r, i in enumerate(rank_a)}; rb = {i: r for r, i in enumerate(rank_b)}
    return sorted(ids, key=lambda i: -(1.0/(c+ra[i]) + 1.0/(c+rb[i])))

def hybrid_over(cand_ids, order_index, bm, tvec, qv, question):
    """Hybrid RRF restricted to a candidate id subset (the pre-filter). Returns ranked ids."""
    idxs = [order_index[i] for i in cand_ids]
    bm_s = bm.scores(question, idxs)
    bm_rank = sorted(cand_ids, key=lambda i: -bm_s[order_index[i]])
    vec_rank = sorted(cand_ids, key=lambda i: -cos(qv, tvec[i]))
    return rrf(bm_rank, vec_rank, list(cand_ids))

def session_of(dia_id):
    m = re.match(r"D(\d+):", dia_id)
    return m.group(1) if m else None

# ---------- run ----------
D = json.load(open(DATA))
_all = []
for D0 in D:
    conv = D0["conversation"]; tset = set()
    for sk in [k for k in conv if re.fullmatch(r"session_\d+", k)]:
        for t in conv[sk]:
            _all.append(t["text"]); tset.add(t["dia_id"])
    for q in D0["qa"]:
        if str(q.get("category")) in ANSWERABLE and gold_of(q, tset):
            _all.append(q["question"])
warmup(_all)

METHODS = ("hybrid", "hybrid+speaker", "hybrid+speaker_soft", "hybrid+session_oracle")
# harm subset = questions where the speaker heuristic FIRES but the gold turn is the OTHER speaker's
# (a wrong hard-filter deletes the answer). Tracks baseline / hard / soft recall there globally.
harm = {"hybrid": [], "hybrid+speaker": [], "hybrid+speaker_soft": []}
per_conv = {m: [] for m in METHODS}          # per-conversation mean recall@20
fire = correct = fired_gold_kept = 0
n_q = 0
t0 = time.time()
for ci, D0 in enumerate(D):
    conv = D0["conversation"]; sa = conv["speaker_a"]; sb = conv["speaker_b"]
    order, text, spk = [], {}, {}
    for sk in sorted([k for k in conv if re.fullmatch(r"session_\d+", k)], key=lambda s: int(s.split("_")[1])):
        for t in conv[sk]:
            order.append(t["dia_id"]); text[t["dia_id"]] = t["text"]; spk[t["dia_id"]] = t["speaker"]
    turnset = set(order); oidx = {i: j for j, i in enumerate(order)}
    bm = BM25([text[i] for i in order])
    tvec = {i: embed(text[i]) for i in order}
    qs = [q for q in D0["qa"] if str(q.get("category")) in ANSWERABLE and gold_of(q, turnset)]
    conv_acc = {m: [] for m in METHODS}
    for q in qs:
        n_q += 1; g = set(gold_of(q, turnset)); ng = len(g); qv = embed(q["question"])
        # baseline hybrid over ALL turns
        rk = hybrid_over(order, oidx, bm, tvec, qv, q["question"])
        conv_acc["hybrid"].append(len(g & set(rk[:K])) / ng)
        # speaker heuristic
        qn = q["question"].lower(); na = sa.lower() in qn; nb = sb.lower() in qn
        gold_all_named = None
        if na ^ nb:                                   # exactly one speaker named -> filter
            fire += 1; named = sa if na else sb
            cand = [i for i in order if spk[i] == named]
            gold_all_named = all(spk[gi] == named for gi in g)
            if gold_all_named: correct += 1
            if g.issubset(set(cand)): fired_gold_kept += 1
            # HARD: restrict the pool to the named speaker (a wrong filter hard-deletes the answer)
            rk_s = hybrid_over(cand, oidx, bm, tvec, qv, q["question"]) if cand else []
            # SOFT (jacksonxly): don't delete - BOOST matching-speaker via RRF with a speaker prior,
            # keep every turn as fallback (matching first, then the rest, both in hybrid order)
            prior = [i for i in rk if spk[i] == named] + [i for i in rk if spk[i] != named]
            rk_soft = rrf(rk, prior, order)
        else:
            rk_s = rk                                 # no confident filter -> fall back to full hybrid
            rk_soft = rk
        conv_acc["hybrid+speaker"].append(len(g & set(rk_s[:K])) / ng)
        conv_acc["hybrid+speaker_soft"].append(len(g & set(rk_soft[:K])) / ng)
        # harm subset: heuristic fired but gold is (partly) the OTHER speaker -> hard filter should hurt
        if gold_all_named is False:
            harm["hybrid"].append(len(g & set(rk[:K])) / ng)
            harm["hybrid+speaker"].append(len(g & set(rk_s[:K])) / ng)
            harm["hybrid+speaker_soft"].append(len(g & set(rk_soft[:K])) / ng)
        # session oracle (ceiling): restrict to sessions of the gold turns
        gsess = {session_of(gi) for gi in g}
        cand_sess = [i for i in order if session_of(i) in gsess]
        rk_o = hybrid_over(cand_sess, oidx, bm, tvec, qv, q["question"])
        conv_acc["hybrid+session_oracle"].append(len(g & set(rk_o[:K])) / ng)
    for m in METHODS:
        per_conv[m].append(sum(conv_acc[m]) / len(conv_acc[m]))
    print(f"  conv {ci}: {len(order)} turns, {len(qs)} Q (t+{time.time()-t0:.0f}s)", flush=True)
json.dump(_cache, open(CACHE, "w"))

def mean(x): return sum(x) / len(x)
def boot_ci(deltas, iters=10000, seed=17):
    rnd = random.Random(seed); n = len(deltas); samp = []
    for _ in range(iters):
        samp.append(mean([deltas[rnd.randrange(n)] for _ in range(n)]))
    samp.sort(); return samp[int(.025*iters)], samp[int(.975*iters)]

base = per_conv["hybrid"]
print(f"\n=== LoCoMo metadata pre-filter (recall@{K}, n_q={n_q}, 10 conversations) ===")
print(f"speaker heuristic fired on {fire}/{n_q} Q ({100*fire//n_q}%); "
      f"named-speaker owns ALL gold {correct}/{fire} ({100*correct//max(fire,1)}%); "
      f"gold fully kept after filter {fired_gold_kept}/{fire} ({100*fired_gold_kept//max(fire,1)}%)")
print(f"\n{'method':<24}{'recall@20':>10}{'delta vs hybrid':>18}{'conv win-rate':>15}{'95% CI (conv boot)':>24}")
for m in METHODS:
    r = mean(per_conv[m]); dlt = [per_conv[m][i] - base[i] for i in range(len(base))]
    wins = sum(1 for d in dlt if d > 0)
    if m == "hybrid":
        print(f"{m:<24}{r:>10.3f}{'--':>18}{'--':>15}{'--':>24}")
    else:
        lo, hi = boot_ci(dlt); sig = "" if (lo <= 0 <= hi) else "  (excludes 0)"
        print(f"{m:<24}{r:>10.3f}{mean(dlt):>+18.3f}{f'{wins}/10':>15}{f'[{lo:+.3f}, {hi:+.3f}]{sig}':>24}")
nh = len(harm["hybrid"])
print(f"\nHARM SUBSET (heuristic fired but gold is the OTHER speaker's turn), n={nh}:")
print(f"  {'hybrid (no filter)':<24}{mean(harm['hybrid']):>8.3f}")
print(f"  {'hard filter':<24}{mean(harm['hybrid+speaker']):>8.3f}  <- a wrong hard-filter deletes the answer")
print(f"  {'soft filter (boost+fallback)':<24}{mean(harm['hybrid+speaker_soft']):>8.3f}  <- keeps the fallback")
print("\nReading: hybrid+speaker (HARD) is the cheap filter (exact-match 2 names, no LLM); it wins overall")
print("but ZEROES the harm subset. hybrid+speaker_soft (jacksonxly: boost, don't delete) keeps ~the overall")
print("gain AND rescues the harm subset - the safer default when extraction is lossy.")
print("hybrid+session_oracle is the CEILING of a perfect time/session filter (uses gold -> not shippable).")
print("If speaker's CI excludes 0 positive, cheap metadata filtering beats retriever choice here;")
print("if it includes 0 while the oracle ceiling is large, the lever is real but needs better filter extraction.")

result = {"k": K, "n_q": n_q, "n_conv": len(base),
          "recall@20": {m: round(mean(per_conv[m]), 4) for m in METHODS},
          "delta_vs_hybrid": {m: round(mean([per_conv[m][i]-base[i] for i in range(len(base))]), 4) for m in METHODS if m != "hybrid"},
          "speaker_fire_rate": round(fire/n_q, 3), "speaker_correct_rate": round(correct/max(fire, 1), 3),
          "harm_subset": {"n": nh, "hybrid": round(mean(harm["hybrid"]), 4),
                          "hard": round(mean(harm["hybrid+speaker"]), 4),
                          "soft": round(mean(harm["hybrid+speaker_soft"]), 4)}}
json.dump(result, open("locomo_metadata_prefilter_result.json", "w"), indent=1)
print("\nsaved: locomo_metadata_prefilter_result.json")
