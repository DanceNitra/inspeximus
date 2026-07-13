"""Validate mnemo's SHIPPED soft `prefer` filter on LoCoMo — end-to-end through mnemo.recall(), not a
standalone reimplementation. Confirms the alias-strength-weighted soft filter beats BOTH no filter and a
hard `where` filter under imperfect (ambiguous) metadata extraction.

Setup: one Mnemo store per LoCoMo conversation (semantic hybrid mode; local nomic embedder via the warm
cache). Each dialogue turn is remembered with meta={"speaker": <name>}. For each question we pick the
speaker to filter on + an alias-strength trust:
  - EXACT: the speaker's name is literally in the question -> chosen = that speaker, alias_strength = 1.0
    (reliable; not error-injected).
  - AMBIGUOUS: no name in the question -> the extractor GUESSES (majority speaker of the top-10 plain
    recall) -> alias_strength = 0.0 (unreliable; this is where extraction actually fails).
Three retrievals, all via mnemo.recall:
  - no_filter:  recall(q)                              (plain hybrid)
  - hard_where: recall(q, where={"speaker": chosen})   (mnemo's existing HARD filter)
  - soft_prefer: recall(q, prefer={"speaker": chosen}, prefer_trust=alias_strength)  (the NEW feature)
Metric: recall@20 overall + on the harm subset (chosen speaker is wrong). Value/last_access are snapshotted
and restored around each method so recall's reinforcement can't bias the comparison. Reuses the warm cache.
Run: LOCOMO_PATH=agora_output/lab/data/locomo10.json \
     LOCOMO_CACHE=agora_output/lab/data/locomo_confweighted_cache.json \
     python mnemo/probes/locomo_soft_prefer_filter.py
"""
import json, re, ast, time, hashlib, os, urllib.request, collections, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from mnemo import Mnemo

DATA = os.environ.get("LOCOMO_PATH", "agora_output/lab/data/locomo10.json")
CACHE = os.environ.get("LOCOMO_CACHE", "agora_output/lab/data/locomo_confweighted_cache.json")
EMB_URL = "http://localhost:11434/api/embed"
K = 20; ANSWERABLE = ("1", "2", "3", "4"); TOPM = 10
_t0 = time.time()
_cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
def _key(t): return hashlib.sha1(t[:2000].encode("utf-8")).hexdigest()
def _post(p):
    r = urllib.request.urlopen(urllib.request.Request(
        EMB_URL, data=json.dumps(p).encode(), headers={"Content-Type": "application/json"}), timeout=120)
    return json.loads(r.read())["embeddings"]
def warmup(texts, batch=128, flush_every=10):
    miss, seen = [], set()
    for t in texts:
        k = _key(t)
        if k not in _cache and k not in seen: seen.add(k); miss.append(t)
    if not miss: print("warmup: all cached", flush=True); return
    print(f"warmup: {len(miss)} uncached / {len(texts)}", flush=True)
    nb = (len(miss)+batch-1)//batch
    for bi, i in enumerate(range(0, len(miss), batch)):
        ch = miss[i:i+batch]
        for c, v in zip(ch, _post({"model": "nomic-embed-text", "input": [c[:2000] for c in ch]})):
            _cache[_key(c)] = v
        if (bi+1) % flush_every == 0 or (bi+1) == nb:
            json.dump(_cache, open(CACHE, "w")); print(f"  warmup {bi+1}/{nb} (t+{time.time()-_t0:.0f}s)", flush=True)
def embed(t):
    v = _cache.get(_key(t))
    if v is None: v = _post({"model": "nomic-embed-text", "input": [t[:2000]]})[0]; _cache[_key(t)] = v
    return v
def gold_of(q, tset):
    e = q.get("evidence")
    try: ids = ast.literal_eval(e) if isinstance(e, str) else e
    except Exception: ids = []
    return [i for i in (ids or []) if i in tset]

D = json.load(open(DATA)); _all = []
for D0 in D:
    conv = D0["conversation"]; tset = set()
    for sk in [k for k in conv if re.fullmatch(r"session_\d+", k)]:
        for t in conv[sk]: _all.append(t["text"]); tset.add(t["dia_id"])
    for q in D0["qa"]:
        if str(q.get("category")) in ANSWERABLE and gold_of(q, tset): _all.append(q["question"])
warmup(_all)

METHODS = ("no_filter", "hard_where", "soft_prefer")
per_conv = {m: [] for m in METHODS}; harm = {m: [] for m in METHODS}
n_q = fex = fam = wex = wam = 0; t0 = time.time()
for ci, D0 in enumerate(D):
    conv = D0["conversation"]; sa = conv["speaker_a"]; sb = conv["speaker_b"]
    turns = []
    for sk in sorted([k for k in conv if re.fullmatch(r"session_\d+", k)], key=lambda s: int(s.split("_")[1])):
        for t in conv[sk]: turns.append((t["dia_id"], t["text"], t["speaker"]))
    m = Mnemo(embed=embed); m.semantic_threshold = 1
    dia2id = {}
    for dia, txt, sp in turns:
        mid = m.remember(txt, meta={"speaker": sp, "dia": dia}); dia2id[dia] = mid
    id2dia = {v: k for k, v in dia2id.items()}
    turnset = set(dia2id)
    base_val = {r["id"]: (r["value"], r["last_access"]) for r in m.items}
    def restore():
        for r in m.items: r["value"], r["last_access"] = base_val[r["id"]]
    def rec_dias(**kw):
        restore()
        return set(id2dia[h["id"]] for h in m.recall(q["question"], k=K, mode="hybrid", **kw) if h["id"] in id2dia)
    qs = [q for q in D0["qa"] if str(q.get("category")) in ANSWERABLE and gold_of(q, turnset)]
    acc = {mm: [] for mm in METHODS}
    for q in qs:
        n_q += 1; g = set(gold_of(q, turnset)); ng = len(g)
        qn = q["question"].lower(); na = sa.lower() in qn; nb = sb.lower() in qn
        # baseline (also used to derive the ambiguous-guess speaker)
        restore(); base_hits = m.recall(q["question"], k=K, mode="hybrid")
        base_dias = set(id2dia[h["id"]] for h in base_hits if h["id"] in id2dia)
        if na ^ nb:
            chosen = sa if na else sb; alias = 1.0; fex += 1
        else:
            top = [h for h in base_hits[:TOPM] if h["id"] in id2dia]
            cnt = collections.Counter((next(x for x in m.items if x["id"] == h["id"])["meta"]["speaker"]) for h in top)
            chosen = cnt.most_common(1)[0][0] if cnt else sa; alias = 0.0; fam += 1
        gold_spk = set()
        for gi in g:
            gid = dia2id.get(gi)
            if gid: gold_spk.add(next(x for x in m.items if x["id"] == gid)["meta"]["speaker"])
        wrong = not all(s == chosen for s in gold_spk) if gold_spk else False
        if na ^ nb: wex += wrong
        else: wam += wrong
        nf = base_dias
        hw = rec_dias(where={"speaker": chosen})
        sp = rec_dias(prefer={"speaker": chosen}, prefer_trust=alias)
        r_nf = len(g & nf)/ng; r_hw = len(g & hw)/ng; r_sp = len(g & sp)/ng
        acc["no_filter"].append(r_nf); acc["hard_where"].append(r_hw); acc["soft_prefer"].append(r_sp)
        if wrong:
            harm["no_filter"].append(r_nf); harm["hard_where"].append(r_hw); harm["soft_prefer"].append(r_sp)
    for mm in METHODS: per_conv[mm].append(sum(acc[mm])/len(acc[mm]))
    print(f"  conv {ci}: {len(turns)} turns, {len(qs)} Q (t+{time.time()-t0:.0f}s)", flush=True)
json.dump(_cache, open(CACHE, "w"))

def mean(x): return sum(x)/len(x) if x else float("nan")
def boot(dl, it=10000, seed=17):
    r = random.Random(seed); n = len(dl); s = [mean([dl[r.randrange(n)] for _ in range(n)]) for _ in range(it)]
    s.sort(); return s[int(.025*it)], s[int(.975*it)]
base = per_conv["no_filter"]
print(f"\n=== mnemo SHIPPED soft `prefer` filter on LoCoMo (recall@{K}, n_q={n_q}, 10 conv) ===")
print(f"exact-name firings {fex} (wrong {wex}, {100*wex//max(fex,1)}%); ambiguous-guess firings {fam} "
      f"(wrong {wam}, {100*wam//max(fam,1)}%); harm subset n={len(harm['no_filter'])}")
print(f"\n{'method':<14}{'recall@20':>10}{'delta':>9}{'wins':>7}")
for mm in METHODS:
    r = mean(per_conv[mm])
    if mm == "no_filter": print(f"{mm:<14}{r:>10.3f}{'--':>9}{'--':>7}")
    else:
        dl = [per_conv[mm][i]-base[i] for i in range(len(base))]; lo, hi = boot(dl)
        print(f"{mm:<14}{r:>10.3f}{mean(dl):>+9.3f}{sum(1 for d in dl if d>0):>5}/10  CI[{lo:+.3f},{hi:+.3f}]")
print(f"\nHARM SUBSET recall@{K} (chosen speaker wrong; baseline=no_filter on this subset):")
for mm in METHODS: print(f"  {mm:<14}{mean(harm[mm]):.3f}  n={len(harm[mm])}")
out = {"k": K, "n_q": n_q, "fire_exact": fex, "wrong_exact": wex, "fire_ambiguous": fam, "wrong_ambiguous": wam,
       "harm_n": len(harm["no_filter"]),
       "recall@20": {mm: round(mean(per_conv[mm]), 4) for mm in METHODS},
       "harm_subset": {mm: {"mean": round(mean(harm[mm]), 4) if harm[mm] else None, "n": len(harm[mm])} for mm in METHODS}}
json.dump(out, open("mnemo/probes/locomo_soft_prefer_filter_result.json", "w"), indent=1)
print("\nsaved: mnemo/probes/locomo_soft_prefer_filter_result.json")
