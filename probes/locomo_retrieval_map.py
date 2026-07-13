"""A failure-map of cheap, self-hostable agent-memory retrieval on REAL multi-session memory
(LoCoMo: 10 conversations / 5882 turns / 1531 answerable questions). Rebuilt after an adversarial
red-team caught a config bug + demanded a strong-embedder arm + cluster-level statistics.

Arms (all self-hostable, vanilla):
  recency        - k most-recent turns, query-blind (what many agent frameworks ship as 'last-N memory')
  bm25           - zero-dependency lexical (k1=1.5, b=0.75)
  nomic          - nomic-embed-text WITH its required asymmetric prefixes (search_query:/search_document:)
  mxbai          - mxbai-embed-large (top-MTEB ~335M) with its retrieval query prompt  [the strong-embedder arm]
  hybrid_nomic   - RRF(bm25, nomic)
  hybrid_mxbai   - RRF(bm25, mxbai)

Metric: recall@k = fraction of a question's gold-evidence turns retrieved in top-k (k=5,10,20),
and full_recall@k (all gold turns in top-k). Broken out by LoCoMo category. We ALSO save per-question
recall@20 and run cluster-aware statistics (the 1531 questions are nested in only 10 conversations,
so we report a paired Wilcoxon, a per-conversation 10-way win-rate, and a conversation-level bootstrap
CI on the key deltas — not just 3-decimal point estimates).

Honest scope (baked into the output): recall@gold-turn under-credits a semantically-equivalent turn that
isn't the annotated gold one (slightly anti-embedder); LoCoMo is conversational self-narrative with high
question<->evidence lexical overlap (favorable to lexical); single benchmark; embedder-specific. This
REPRODUCES the BEIR (Thakur 2021) 'BM25 is a strong zero-shot baseline' pattern + RRF (Cormack 2009) on
agent-memory data, with a runnable receipt; it is not a new IR law. Embeddings cached on disk (model+prefix
keyed) so audit re-runs are deterministic and instant. EMBEDDER-ONLY (no LLM in the loop)."""
# RUN: pip install scipy ; install Ollama and `ollama pull nomic-embed-text mxbai-embed-large` (the only
# two non-stdlib pieces are scipy + a local Ollama embedding endpoint). Get the LoCoMo 10-conversation
# dataset (locomo10.json) from https://github.com/snap-research/locomo and point LOCOMO_PATH at it:
#   LOCOMO_PATH=/path/to/locomo10.json python locomo_retrieval_map.py
# Embeddings are cached to LOCOMO_CACHE so re-runs are deterministic and instant.
import json, re, ast, time, math, hashlib, os, urllib.request, collections, statistics as st, random
from scipy.stats import wilcoxon

DATA = os.environ.get("LOCOMO_PATH", "agora_output/lab/data/locomo10.json")
CACHE = os.environ.get("LOCOMO_CACHE", "locomo_embcache_v2.json")
EMB_URL = os.environ.get("OLLAMA_EMBED_URL", "http://localhost:11434/api/embed")
KS = [5, 10, 20]
CATNAME = {"1": "multi-hop", "2": "temporal", "3": "open-domain", "4": "single-hop"}
ANSWERABLE = ("1", "2", "3", "4")
# per-model retrieval prefixes (asymmetric query/document). Running nomic WITHOUT these was the red-team bug.
PREFIX = {
    "nomic-embed-text":   {"q": "search_query: ",  "d": "search_document: "},
    "mxbai-embed-large":  {"q": "Represent this sentence for searching relevant passages: ", "d": ""},
}
VEC_MODELS = {"nomic": "nomic-embed-text", "mxbai": "mxbai-embed-large"}

# ---------- model+prefix-aware embedding cache ----------
_cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
def _key(model, role, text): return hashlib.sha1(f"{model}|{role}|{text[:2000]}".encode("utf-8")).hexdigest()
def _post_raw(model, inputs):
    body = json.dumps({"model": model, "input": inputs}).encode()
    r = urllib.request.urlopen(urllib.request.Request(
        EMB_URL, data=body, headers={"Content-Type": "application/json"}), timeout=180)
    return json.loads(r.read())["embeddings"]
def _post(model, inputs, _depth=0):
    """Resilient batch embed: guards empty inputs, retries transient errors, and on a
    persistent error splits the batch and recurses (so one bad/slow item can't kill a 25-min run)."""
    inputs = [(s if (s and s.strip()) else " ") for s in inputs]
    for attempt in range(3):
        try:
            return _post_raw(model, inputs)
        except Exception as e:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1)); continue
            if len(inputs) > 1:                       # split-and-recurse
                mid = len(inputs) // 2
                return _post(model, inputs[:mid], _depth+1) + _post(model, inputs[mid:], _depth+1)
            print(f"    [warn] embed failed for one item ({model}): {str(e)[:80]} -> zero vector", flush=True)
            return [[0.0] * 768]                       # last-resort; dim fixed up by caller's cos (norm guard)
def warmup(model, role, texts, batch=64):
    pre = PREFIX[model]["q" if role == "q" else "d"]
    miss, seen = [], set()
    for t in texts:
        k = _key(model, role, t)
        if k not in _cache and k not in seen:
            seen.add(k); miss.append(t)
    print(f"  warmup {model}/{role}: {len(miss)} uncached", flush=True)
    t0 = time.time()
    for i in range(0, len(miss), batch):
        chunk = miss[i:i+batch]
        vecs = _post(model, [pre + c[:2000] for c in chunk])
        for c, v in zip(chunk, vecs):
            _cache[_key(model, role, c)] = v
        if (i // batch) % 8 == 0:
            json.dump(_cache, open(CACHE, "w")); print(f"    {min(i+batch,len(miss))}/{len(miss)} (t+{time.time()-t0:.0f}s)", flush=True)
    json.dump(_cache, open(CACHE, "w"))
def emb(model, role, text):
    k = _key(model, role, text); v = _cache.get(k)
    if v is None:
        v = _post(model, [PREFIX[model]["q" if role == "q" else "d"] + text[:2000]])[0]; _cache[k] = v
    return v
def cos(a, b):
    d = sum(x*y for x, y in zip(a, b)); na = sum(x*x for x in a) ** .5; nb = sum(x*x for x in b) ** .5
    return d / ((na*nb) or 1)

# ---------- zero-dependency BM25 ----------
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
    def scores(self, query):
        q = toks(query); out = [0.0]*self.N
        for i in range(self.N):
            c = self.tf[i]; dl = self.dl[i]; s = 0.0
            for t in q:
                f = c.get(t, 0)
                if f: s += self.idf.get(t, 0.0)*(f*(self.k1+1))/(f + self.k1*(1 - self.b + self.b*dl/(self.avgdl or 1)))
            out[i] = s
        return out

def gold_of(q, turnset):
    e = q.get("evidence")
    try: ids = ast.literal_eval(e) if isinstance(e, str) else e
    except Exception: ids = []
    return [i for i in (ids or []) if i in turnset]
def rrf(ra, rb, ids, c=60):
    da = {i: r for r, i in enumerate(ra)}; db = {i: r for r, i in enumerate(rb)}
    return sorted(ids, key=lambda i: -(1.0/(c+da[i]) + 1.0/(c+db[i])))

METHODS = ("recency", "bm25", "nomic", "mxbai", "hybrid_nomic", "hybrid_mxbai")
D = json.load(open(DATA))

# ---- warmup both embedders (turns as documents, questions as queries) ----
print("warmup embeddings...", flush=True)
all_doc, all_q = [], []
for D0 in D:
    conv = D0["conversation"]; tset = set()
    for sk in [k for k in conv if re.fullmatch(r"session_\d+", k)]:
        for t in conv[sk]: all_doc.append(t["text"]); tset.add(t["dia_id"])
    for q in D0["qa"]:
        if str(q.get("category")) in ANSWERABLE and gold_of(q, tset): all_q.append(q["question"])
for mk, mn in VEC_MODELS.items():
    warmup(mn, "d", all_doc); warmup(mn, "q", all_q)

# ---- run ----
rec = {m: {c: {k: [] for k in KS} for c in ANSWERABLE} for m in METHODS}
ful = {m: {c: {k: [] for k in KS} for c in ANSWERABLE} for m in METHODS}
per_q20 = {m: [] for m in METHODS}                  # per-question recall@20 (paired)
per_conv20 = {m: [] for m in METHODS}               # per-conversation mean recall@20 (10 clusters)
n_q = 0; n_turns = 0; t0 = time.time()
for ci, D0 in enumerate(D):
    conv = D0["conversation"]; order = []; text = {}
    for sk in sorted([k for k in conv if re.fullmatch(r"session_\d+", k)], key=lambda s: int(s.split("_")[1])):
        for t in conv[sk]: order.append(t["dia_id"]); text[t["dia_id"]] = t["text"]
    turnset = set(order); n_turns += len(order)
    bm = BM25([text[i] for i in order])
    nvec = {i: emb("nomic-embed-text", "d", text[i]) for i in order}
    xvec = {i: emb("mxbai-embed-large", "d", text[i]) for i in order}
    recency_rank = list(reversed(order))
    qs = [q for q in D0["qa"] if str(q.get("category")) in ANSWERABLE and gold_of(q, turnset)]
    conv_acc = {m: [] for m in METHODS}
    for q in qs:
        n_q += 1; cat = str(q["category"]); g = set(gold_of(q, turnset)); ng = len(g)
        bm_s = bm.scores(q["question"])
        bm_rank = [order[j] for j in sorted(range(len(order)), key=lambda j: -bm_s[j])]
        qn = emb("nomic-embed-text", "q", q["question"]); qx = emb("mxbai-embed-large", "q", q["question"])
        n_rank = sorted(order, key=lambda i: -cos(qn, nvec[i]))
        x_rank = sorted(order, key=lambda i: -cos(qx, xvec[i]))
        ranks = {"recency": recency_rank, "bm25": bm_rank, "nomic": n_rank, "mxbai": x_rank,
                 "hybrid_nomic": rrf(bm_rank, n_rank, order), "hybrid_mxbai": rrf(bm_rank, x_rank, order)}
        for m, rk in ranks.items():
            for k in KS:
                tk = set(rk[:k])
                r = len(g & tk)/ng
                rec[m][cat][k].append(r); ful[m][cat][k].append(1.0 if g.issubset(tk) else 0.0)
                if k == 20: per_q20[m].append(r); conv_acc[m].append(r)
    for m in METHODS: per_conv20[m].append(round(sum(conv_acc[m])/len(conv_acc[m]), 4))
    print(f"  conv {ci}: {len(order)} turns, {len(qs)} Q (t+{time.time()-t0:.0f}s)", flush=True)
json.dump(_cache, open(CACHE, "w"))

def agg(acc, m, cats, k):
    v = [x for c in cats for x in acc[m][c][k]]
    return round(sum(v)/len(v), 3) if v else 0.0

print(f"\n=== LoCoMo agent-memory retrieval map | n_turns={n_turns} n_Q={n_q} ===")
for metric, acc in (("recall@20", rec), ("full_recall@20", ful)):
    print(f"\n--- {metric} by category (mean over questions) ---")
    print("category".ljust(13) + "".join(f"{m:>14}" for m in METHODS))
    for c in ANSWERABLE:
        print(CATNAME[c].ljust(13) + "".join(f"{agg(acc,m,[c],20):>14.3f}" for m in METHODS))
    print("ALL".ljust(13) + "".join(f"{agg(acc,m,list(ANSWERABLE),20):>14.3f}" for m in METHODS))

# ---- cluster-aware statistics ----
print("\n=== statistics (recall@20; questions nested in 10 conversations) ===")
def overall(m): return round(sum(per_q20[m])/len(per_q20[m]), 4)
base = "bm25"
print(f"overall recall@20: " + " | ".join(f"{m} {overall(m)}" for m in METHODS))
print(f"\npaired Wilcoxon vs {base} (per-question, n={len(per_q20[base])}):")
for m in METHODS:
    if m == base: continue
    diff = [a-b for a, b in zip(per_q20[m], per_q20[base])]
    try:
        W, p = wilcoxon(diff, zero_method="zsplit"); d = round(sum(diff)/len(diff), 4)
        print(f"   {m:<14} mean_delta={d:+.4f}  p={p:.2e}  ({'differs' if p<0.05 else 'n.s.'})")
    except Exception as e:
        print(f"   {m:<14} wilcoxon-failed {e}")
print(f"\nper-conversation win-rate (of 10 convs, how often each beats {base} on mean recall@20):")
for m in METHODS:
    if m == base: continue
    wins = sum(1 for a, b in zip(per_conv20[m], per_conv20[base]) if a > b)
    print(f"   {m:<14} {wins}/10 convs beat {base}   (per-conv deltas: {[round(a-b,3) for a,b in zip(per_conv20[m],per_conv20[base])]})")
# conversation-level bootstrap CI on key deltas (resample the 10 conversations)
def boot_ci(m1, m2, iters=5000):
    d = [a-b for a, b in zip(per_conv20[m1], per_conv20[m2])]; n = len(d); idx = list(range(n))
    means = []
    for s in range(iters):
        rs = random.Random(s)
        means.append(sum(d[rs.choice(idx)] for _ in idx)/n)
    means.sort(); return round(means[int(.025*iters)], 4), round(means[int(.975*iters)], 4), round(sum(d)/n, 4)
print(f"\nconversation-level bootstrap 95% CI on mean recall@20 delta (resampling the 10 convs):")
for pair in [("mxbai", "bm25"), ("nomic", "bm25"), ("hybrid_mxbai", "bm25"), ("recency", "bm25")]:
    lo, hi, mean = boot_ci(*pair)
    sign = "CI excludes 0" if (lo > 0 or hi < 0) else "CI includes 0 (not sig at conv level)"
    print(f"   {pair[0]:<14} - bm25: mean {mean:+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]  -> {sign}")

result = {"n_turns": n_turns, "n_q": n_q, "ks": KS,
  "recall": {m: {c: {k: agg(rec, m, [c], k) for k in KS} for c in ANSWERABLE} for m in METHODS},
  "recall_overall": {m: {k: agg(rec, m, list(ANSWERABLE), k) for k in KS} for m in METHODS},
  "full_recall_overall": {m: {k: agg(ful, m, list(ANSWERABLE), k) for k in KS} for m in METHODS},
  "per_conv_recall20": per_conv20}
_out = os.environ.get("LOCOMO_OUT", "locomo_retrieval_map_result.json")
json.dump(result, open(_out, "w"), indent=1)
print(f"\nsaved -> {_out}")
print("Scope: recall@gold-turn under-credits semantically-equivalent non-gold turns (mildly anti-embedder); "
      "LoCoMo is high-lexical-overlap conversational memory (favors lexical); single benchmark; reproduces "
      "BEIR(2021)+RRF(2009). Cost: vector arms need an embedder + vectors stored; bm25/recency need only a text index.")
