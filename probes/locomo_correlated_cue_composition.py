"""Correlated-cue composition sweep — the frontier arm jacksonxly asked for (r/Rag 1ujwwu6): when two soft
cues are CORRELATED (not near-orthogonal like speaker x time), does the PRODUCT composition double-count the
shared evidence and flip below the SUM?

Setup (real corpus, one controlled variable — the honest way to isolate an interaction, not a rigged knob):
  - Store = real LoCoMo turns + real nomic embeddings + the SHIPPED mnemo hybrid ranking (same as
    locomo_composed_soft_filters.py). Questions with an exact resolvable speaker (cue A = the correct speaker,
    truthful, reused from the alias arm).
  - cue A(turn) = (turn.speaker == the question's named speaker).   [truthful]
  - cue B(turn) = a second binary predicate assigned DETERMINISTICALLY (seeded hash of turn+question+level, no
    RNG state) as a NOISY COPY of cue A at a controlled mixing level c in {0.0,0.25,0.5,0.75,1.0}:
        matchB = matchA        if hash01(turn,q,c) < c
                 else indep_bit(turn,q)   (independent Bernoulli(0.5))
    c=0 -> cue B independent of cue A (orthogonal, the earlier regime); c=1 -> cue B == cue A (fully redundant).
  We MEASURE and report the realized phi correlation between matchA and matchB over the pool at each level, so
  the x-axis is measured, not assumed. The whole range is swept (no cherry-picked operating point).

Arms (all via the SHIPPED prefer scoring; pref formula identical to mnemo.recall, verified by the sibling
probe's 0/1568 self-check):
  hybrid   : no cue                                            pref = 1
  single_A : cue A only                                        pref = 1 + T*G*matchA        [invariant in c]
  sum      : uncapped sum of the two cues                      pref = 1 + (T*matchA + T*matchB)*G
  product  : product of the two cues                           pref = (1+T*G*matchA)*(1+T*G*matchB)

Reported per level, split by whether the gold turn actually satisfies cue B:
  - B_truthful  (gold matches B): both cues point at gold -> product SHOULD help.
  - B_misleading (gold matches A but NOT B): cue B is wrong for this query; if cues are correlated, wrong
    turns that match BOTH get the full product boost and can outrank gold -> the double-count HURT.
Predictions this FALSIFIES if wrong: (1) single_A is identical across c (sanity: it ignores B). (2) at c=0
product ~ sum (orthogonal, matches the earlier arm). (3) as c->1, on the B_misleading subset, product's edge
over sum shrinks/inverts (double-counting).

WHAT IT ACTUALLY SHOWS (measured; NOT jacksonxly's correlation hypothesis): the product inherits the known
PRODUCT-OF-EXPERTS veto (Hinton 2002) / noisy-AND brittleness — a near-zero factor vetoes, so a gold turn
that misses EITHER binary cue collapses far below the additive sum or the trusted single cue. This is driven
by cue WRONGNESS, not correlation: the product-minus-sum gap on the B_misleading slice is already ~-0.42 at
c=0 (phi~0, orthogonal). Correlation only shrinks that slice (n 786 -> 74 as phi 0 -> 1), so UNCONDITIONALLY
product vs sum can even improve as correlation rises. CAVEAT (not a rigged claim): the conditional crater is
partly arithmetic (a gold matching only-A sits at pref 3.7 while a both-matching distractor sits at 3.7^2),
so the magnitude is a conditioned worst-case; the load-bearing content is the DIFFERENTIAL (single/sum/
product) and the trust dose-response, not the crater's size. Per-cue trust down-weighting (b06/b03 arms)
restores product to ~sum only WHILE the second cue is a genuine noisy secondary (full recovery at low c;
partial at c>=0.5). cue B + its phi are INJECTED (a controlled composition stress-test); corpus, embeddings,
gold, and cue A are real LoCoMo.

Reuses the warm embed cache + local nomic. Deterministic (seeded hashes; no Math.random/Date). MIT.
Run: LOCOMO_PATH=agora_output/lab/data/locomo10.json \
     LOCOMO_CACHE=agora_output/lab/data/locomo_confweighted_cache.json \
     python mnemo/probes/locomo_correlated_cue_composition.py"""
import json, re, ast, time, hashlib, os, urllib.request, math, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from mnemo import Mnemo, _PREFER_GAIN

DATA = os.environ.get("LOCOMO_PATH", "agora_output/lab/data/locomo10.json")
CACHE = os.environ.get("LOCOMO_CACHE", "agora_output/lab/data/locomo_confweighted_cache.json")
EMB_URL = "http://localhost:11434/api/embed"; K = 20; ANS = ("1", "2", "3", "4")
T = 0.9; G = _PREFER_GAIN
LEVELS = [0.0, 0.25, 0.5, 0.75, 1.0]
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
    print(f"warmup: {len(miss)} uncached", flush=True); nb = (len(miss)+batch-1)//batch
    for bi, i in enumerate(range(0, len(miss), batch)):
        ch = miss[i:i+batch]
        for c, v in zip(ch, _post({"model": "nomic-embed-text", "input": [c[:2000] for c in ch]})): _cache[_key(c)] = v
        if (bi+1) % flush_every == 0 or (bi+1) == nb: json.dump(_cache, open(CACHE, "w"))
def embed(t):
    v = _cache.get(_key(t))
    if v is None: v = _post({"model": "nomic-embed-text", "input": [t[:2000]]})[0]; _cache[_key(t)] = v
    return v
def gold_of(q, tset):
    e = q.get("evidence")
    try: ids = ast.literal_eval(e) if isinstance(e, str) else e
    except Exception: ids = []
    return [i for i in (ids or []) if i in tset]
def hash01(*parts):
    h = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF
def toks(s): return set(re.findall(r"[a-z0-9]+", s.lower()))

D = json.load(open(DATA)); _all = []
for d0 in D:
    conv = d0["conversation"]; tset = set()
    for sk in [k for k in conv if re.fullmatch(r"session_\d+", k)]:
        for t in conv[sk]: _all.append(t["text"]); tset.add(t["dia_id"])
    for q in d0["qa"]:
        if str(q.get("category")) in ANS and gold_of(q, tset): _all.append(q["question"])
warmup(_all)

ARMS = ("hybrid", "single_A", "sum", "product", "product_b06", "product_b03")
# per level -> per arm -> recall lists, split by gold-B truthfulness; plus phi samples + marginals
stat = {c: {"B_truthful": {a: [] for a in ARMS}, "B_misleading": {a: [] for a in ARMS},
            "phi": [], "mB": [], "mA": []} for c in LEVELS}
single_A_by_level = {c: [] for c in LEVELS}   # invariance check
for ci, d0 in enumerate(D):
    conv = d0["conversation"]; sa = conv["speaker_a"]; sb = conv["speaker_b"]
    sa_tok, sb_tok = sa.split()[0].lower(), sb.split()[0].lower()
    turns = []
    for sk in sorted([k for k in conv if re.fullmatch(r"session_\d+", k)], key=lambda s: int(s.split("_")[1])):
        for t in conv[sk]: turns.append((t["dia_id"], t["text"], t["speaker"]))
    m = Mnemo(embed=embed); m.semantic_threshold = 1; dia2id = {}
    for dia, txt, spk in turns:
        dia2id[dia] = m.remember(txt, meta={"speaker": spk, "dia": dia})
    id2dia = {v: k for k, v in dia2id.items()}; turnset = set(dia2id); N = len(turns)
    rec_by_id = {r["id"]: r for r in m.items}
    base_val = {r["id"]: (r["value"], r["last_access"]) for r in m.items}
    def restore():
        for r in m.items: r["value"], r["last_access"] = base_val[r["id"]]
    def base_ranking(qtext):
        restore()
        return [(h["id"], h["score"]) for h in m.recall(qtext, k=N, mode="hybrid")]
    qs = [q for q in d0["qa"] if str(q.get("category")) in ANS and gold_of(q, turnset)]
    for q in qs:
        qt = q["question"]; qtok = toks(qt)
        has_a, has_b = sa_tok in qtok, sb_tok in qtok
        name = sa if (has_a and not has_b) else sb if (has_b and not has_a) else None
        if not name:
            continue                                   # need an exact single-speaker cue A (truthful)
        g = set(gold_of(q, turnset)); ng = len(g)
        base = base_ranking(qt)
        qid = q["question"]

        def matchA(rid):
            return rec_by_id[rid]["meta"]["speaker"] == name

        def dia_of(rid):
            return rec_by_id[rid]["meta"]["dia"]     # STABLE turn id (rid is a fresh uuid each run)

        for c in LEVELS:
            def matchB(rid):
                mA = matchA(rid); d = dia_of(rid)
                return mA if hash01("mix", d, qid, c) < c else (hash01("ind", d, qid) < 0.5)
            # realized correlation (phi) between matchA and matchB over the pool + marginals
            a = [1 if matchA(rid) else 0 for rid, _ in base]
            b = [1 if matchB(rid) else 0 for rid, _ in base]
            n = len(a); sA = sum(a); sB = sum(b); sAB = sum(x*y for x, y in zip(a, b))
            denom = (sA*(n-sA)*sB*(n-sB)) ** 0.5
            phi = (n*sAB - sA*sB) / denom if denom > 0 else 0.0
            stat[c]["phi"].append(phi); stat[c]["mA"].append(sA/n); stat[c]["mB"].append(sB/n)

            def topk(pref_fn):
                rr = sorted(base, key=lambda p: -(p[1] * pref_fn(p[0])))[:K]
                return set(id2dia[rid] for rid, _ in rr if rid in id2dia)
            arms = {
                "hybrid":   topk(lambda rid: 1.0),
                "single_A": topk(lambda rid: 1.0 + T*G*matchA(rid)),
                "sum":      topk(lambda rid: 1.0 + (T*matchA(rid) + T*matchB(rid))*G),
                "product":  topk(lambda rid: (1.0 + T*G*matchA(rid)) * (1.0 + T*G*matchB(rid))),
                # MITIGATION: down-weight the second cue's trust (the shipped per-cue prefer_trust knob).
                # If a misleading cue's damage in a product is trust-controllable, backing B off recovers it.
                "product_b06": topk(lambda rid: (1.0 + T*G*matchA(rid)) * (1.0 + 0.6*G*matchB(rid))),
                "product_b03": topk(lambda rid: (1.0 + T*G*matchA(rid)) * (1.0 + 0.3*G*matchB(rid))),
            }
            single_A_by_level[c].append(len(g & arms["single_A"])/ng)
            gold_B = all(matchB(dia2id[gi]) for gi in g)   # does the gold turn satisfy cue B?
            bucket = "B_truthful" if gold_B else "B_misleading"
            for aname in ARMS:
                stat[c][bucket][aname].append(len(g & arms[aname])/ng)
    print(f"  conv {ci}: {len(qs)} Q (t+{time.time()-_t0:.0f}s)", flush=True)
json.dump(_cache, open(CACHE, "w"))

def mean(x): return sum(x)/len(x) if x else float("nan")
def boot(dl, it=6000, seed=17):
    r = random.Random(seed); n = len(dl)
    if not n: return float("nan"), float("nan")
    s = sorted(mean([dl[r.randrange(n)] for _ in range(n)]) for _ in range(it))
    return s[int(.025*it)], s[int(.975*it)]

# invariance self-check: single_A must NOT change across c (it ignores cue B)
inv = {c: round(mean(single_A_by_level[c]), 6) for c in LEVELS}
inv_ok = len(set(inv.values())) == 1
print(f"\nSELF-CHECK single_A invariant across c: {inv_ok}  {inv}")

print(f"\n=== Correlated-cue composition sweep (recall@{K}, T={T}, gain={G}) ===")
out = {"k": K, "trust": T, "gain": G, "levels": {}, "single_A_invariant": inv_ok, "single_A_by_level": inv}
for c in LEVELS:
    phi = mean(stat[c]["phi"]); mA = mean(stat[c]["mA"]); mB = mean(stat[c]["mB"])
    print(f"\n-- mix c={c}  (measured phi(A,B)={phi:+.3f}, marginals A={mA:.2f} B={mB:.2f}) --")
    lev = {"measured_phi": round(phi, 4), "marginal_A": round(mA, 4), "marginal_B": round(mB, 4), "subsets": {}}
    for bucket in ("B_truthful", "B_misleading"):
        nb = len(stat[c][bucket]["hybrid"])
        row = {a: round(mean(stat[c][bucket][a]), 4) for a in ARMS}
        d_ps = [stat[c][bucket]["product"][i] - stat[c][bucket]["sum"][i] for i in range(nb)]
        lo, hi = boot(d_ps)
        print(f"   {bucket:13} n={nb:4}  hybrid={row['hybrid']:.3f} single_A={row['single_A']:.3f} "
              f"sum={row['sum']:.3f} product={row['product']:.3f} (b.6={row['product_b06']:.3f} "
              f"b.3={row['product_b03']:.3f})  product-sum={mean(d_ps):+.3f} CI[{lo:+.3f},{hi:+.3f}]")
        lev["subsets"][bucket] = {"n": nb, "recall@20": row,
                                  "product_minus_sum": {"delta": round(mean(d_ps), 4), "ci95": [round(lo, 4), round(hi, 4)]}}
    # UNCONDITIONAL (both buckets pooled) — the honest aggregate: as correlation rises the misleading
    # subset shrinks, so unconditionally product vs sum can IMPROVE even though the conditional crater is
    # unchanged. Reporting this prevents cherry-picking the worst slice.
    allrow, dall = {}, {}
    for a in ARMS:
        pooled = stat[c]["B_truthful"][a] + stat[c]["B_misleading"][a]
        allrow[a] = round(mean(pooled), 4)
    d_all = ([stat[c]["B_truthful"]["product"][i] - stat[c]["B_truthful"]["sum"][i] for i in range(len(stat[c]["B_truthful"]["sum"]))]
             + [stat[c]["B_misleading"]["product"][i] - stat[c]["B_misleading"]["sum"][i] for i in range(len(stat[c]["B_misleading"]["sum"]))])
    lo_a, hi_a = boot(d_all)
    print(f"   {'UNCONDITIONAL':13} n={len(d_all):4}  single_A={allrow['single_A']:.3f} sum={allrow['sum']:.3f} "
          f"product={allrow['product']:.3f}   product-sum={mean(d_all):+.3f} CI[{lo_a:+.3f},{hi_a:+.3f}]")
    lev["unconditional"] = {"n": len(d_all), "recall@20": allrow,
                            "product_minus_sum": {"delta": round(mean(d_all), 4), "ci95": [round(lo_a, 4), round(hi_a, 4)]}}
    out["levels"][str(c)] = lev
json.dump(out, open("mnemo/probes/locomo_correlated_cue_composition_result.json", "w"), indent=1)
print("\nsaved: mnemo/probes/locomo_correlated_cue_composition_result.json")
