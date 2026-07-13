"""COMPOSED soft filters on LoCoMo — jacksonxly's composition question (r/Rag 1ujwwu6, comment ov3wrze):

  "questions carrying both a temporal expression and a resolvable name are your highest-precision
   subset, and the open question is whether the two soft terms stack or crowd out the plain-hybrid
   floor when both fire. one weighted term per dimension in the fusion, capped, vs multiplied.
   if +0.254 and +0.084 even partially compose, that is a genuinely strong retriever built from parts."

Composes the two measured single arms IN THEIR WINNING CONFIGURATIONS:
  TIME  (locomo_temporal_parser_weight.py):  soft-prefer the rule-parser-resolved {year/month} window,
         flat trust 0.9 (the winning arm; parser-confidence weighting tied due to collinearity).
  ENTITY (locomo_alias_strength_weight.py):  soft-prefer turns by the speaker NAMED in the question,
         trust 0.9 * alias_strength. Composition uses the alias arm as it won: the term fires ONLY on
         exact-name questions (alias_strength=1); on ambiguous questions it backs off to 0 (disclosed —
         no guessed-speaker filter is ever applied here).

Arms (all on the SHIPPED mnemo hybrid scoring; pref term formula identical to mnemo.recall's
  `pref = 1 + trust * _PREFER_GAIN` — see SELF-CHECK below):
  hybrid       : no soft term (floor)
  time_soft    : shipped single-term arm, trust 0.9 on the resolved window
  alias_soft   : shipped single-term arm, trust 0.9 on the named speaker
  comp_capped  : ONE term, trusts summed per matching dimension and CAPPED at 1.0
                 pref = 1 + min(1.0, 0.9*[r in window] + 0.9*[r by speaker]) * GAIN
  comp_mult    : terms MULTIPLIED: pref = (1 + 0.9*GAIN*[window]) * (1 + 0.9*GAIN*[speaker])

METHOD (verifiable reconstruction): per query we take the shipped hybrid ranking ONCE with prefer=None
and k=|store| (hybrid scores the whole pool), then re-rank by score * pref(arm). For the two single
arms this is mathematically the shipped formula, and the probe ASSERTS it: on every signal-bearing
question the reconstructed single-arm gold-recall must equal a direct shipped
recall(prefer=..., prefer_trust=...) call's gold-recall (mismatches counted; run fails the self-check
if >1%). Composed arms are then the same scoring with a generalized pref term.

Subsets by signal presence: BOTH (resolved window AND exactly-one-name; the composition subset),
TIME-ONLY, ALIAS-ONLY. Metric recall@20; bootstrap CI on per-question deltas. Reuses the warm
embed cache + local nomic. MIT.
Run: LOCOMO_PATH=agora_output/lab/data/locomo10.json \
     LOCOMO_CACHE=agora_output/lab/data/locomo_confweighted_cache.json \
     python mnemo/probes/locomo_composed_soft_filters.py
"""
import json, re, ast, time, hashlib, os, urllib.request, collections, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from mnemo import Mnemo, _PREFER_GAIN

DATA = os.environ.get("LOCOMO_PATH", "agora_output/lab/data/locomo10.json")
CACHE = os.environ.get("LOCOMO_CACHE", "agora_output/lab/data/locomo_confweighted_cache.json")
EMB_URL = "http://localhost:11434/api/embed"; K = 20; ANS = ("1", "2", "3", "4")
T = 0.9; CAP = 1.0; G = _PREFER_GAIN
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

# — temporal rule parser (identical to locomo_temporal_parser_weight.py) —
MONTHS = {m: i+1 for i, m in enumerate(
    ["january","february","march","april","may","june","july","august","september","october","november","december"])}
MONW = "(" + "|".join(MONTHS) + ")"
RE_MY = re.compile(MONW + r"[,\s]+(\d{4})", re.I)
RE_YM = re.compile(r"(\d{4})[,\s]+" + MONW, re.I)
RE_MONTH = re.compile(r"\b" + MONW + r"\b", re.I)
RE_YEAR = re.compile(r"\b(20\d{2})\b")
def rule_window(qtext):
    m = RE_MY.search(qtext) or RE_YM.search(qtext)
    if m:
        g = m.groups()
        mon = next((MONTHS[x.lower()] for x in g if x and x.lower() in MONTHS), None)
        yr = next((int(x) for x in g if x and x.isdigit()), None)
    else:
        y = RE_YEAR.search(qtext); mo = RE_MONTH.search(qtext)
        yr = int(y.group(1)) if y else None; mon = MONTHS[mo.group(1).lower()] if mo else None
    w = {}
    if yr is not None: w["year"] = yr
    if mon is not None: w["month"] = mon
    return w or None
def parse_session_date(s):
    mo = RE_MONTH.search(s or ""); yr = RE_YEAR.search(s or "")
    return (int(yr.group(1)) if yr else None, MONTHS[mo.group(1).lower()] if mo else None)
_tokre = re.compile(r"[a-z0-9]+")
def toks(s): return set(_tokre.findall(s.lower()))

D = json.load(open(DATA)); _all = []
for d0 in D:
    conv = d0["conversation"]; tset = set()
    for sk in [k for k in conv if re.fullmatch(r"session_\d+", k)]:
        for t in conv[sk]: _all.append(t["text"]); tset.add(t["dia_id"])
    for q in d0["qa"]:
        if str(q.get("category")) in ANS and gold_of(q, tset): _all.append(q["question"])
warmup(_all)

ARMS = ("hybrid", "time_soft", "alias_soft", "comp_capped", "comp_sum", "comp_mult")
SUBSETS = ("both", "time_only", "alias_only")
per_q = {s: {a: [] for a in ARMS} for s in SUBSETS}
GOLD_JOINT = collections.Counter()   # both-subset: gold satisfies both / one / neither ("misleading")
n_check = n_mismatch = 0
for ci, d0 in enumerate(D):
    conv = d0["conversation"]; sa = conv["speaker_a"]; sb = conv["speaker_b"]
    sa_tok = sa.split()[0].lower(); sb_tok = sb.split()[0].lower()
    sess_date = {}
    for k in conv:
        mm = re.fullmatch(r"session_(\d+)_date_time", k)
        if mm: sess_date[int(mm.group(1))] = parse_session_date(conv[k])
    turns = []
    for sk in sorted([k for k in conv if re.fullmatch(r"session_\d+", k)], key=lambda s: int(s.split("_")[1])):
        snum = int(sk.split("_")[1]); yr, mon = sess_date.get(snum, (None, None))
        for t in conv[sk]: turns.append((t["dia_id"], t["text"], yr, mon, t["speaker"]))
    m = Mnemo(embed=embed); m.semantic_threshold = 1; dia2id = {}
    for dia, txt, yr, mon, spk in turns:
        dia2id[dia] = m.remember(txt, meta={"year": yr, "month": mon, "speaker": spk, "dia": dia})
    id2dia = {v: k for k, v in dia2id.items()}; turnset = set(dia2id); N = len(turns)
    rec_by_id = {r["id"]: r for r in m.items}
    base_val = {r["id"]: (r["value"], r["last_access"]) for r in m.items}
    def restore():
        for r in m.items: r["value"], r["last_access"] = base_val[r["id"]]
    def shipped(qtext, **kw):
        restore()
        return set(id2dia[h["id"]] for h in m.recall(qtext, k=K, mode="hybrid", **kw) if h["id"] in id2dia)
    def base_ranking(qtext):
        restore()
        return [(h["id"], h["score"]) for h in m.recall(qtext, k=N, mode="hybrid")]
    qs = [q for q in d0["qa"] if str(q.get("category")) in ANS and gold_of(q, turnset)]
    kept = 0
    for q in qs:
        qt = q["question"]; qtok = toks(qt)
        win = rule_window(qt)
        has_a, has_b = sa_tok in qtok, sb_tok in qtok
        name = sa if (has_a and not has_b) else sb if (has_b and not has_a) else None
        spk_cond = {"speaker": name} if name else None
        if win and spk_cond: sub = "both"
        elif win: sub = "time_only"
        elif spk_cond: sub = "alias_only"
        else: continue                                  # no composable signal -> out of scope
        kept += 1
        g = set(gold_of(q, turnset)); ng = len(g)
        base = base_ranking(qt)
        def match(rid, cond):
            return cond is not None and Mnemo._cond_match(rec_by_id[rid], cond)
        # DIAGNOSTIC (skeptic's "AND-tautology" check): on the BOTH subset, is the gold turn actually
        # inside BOTH conditions? Not by construction — the window can be wrong (event discussed in a
        # different session) and the named speaker can be the wrong filter (gold said by the other
        # speaker). This measures how often the joint filter is truthful vs misleading.
        if sub == "both":
            gm_all = [ (match(dia2id[gi], win), match(dia2id[gi], spk_cond)) for gi in g ]
            if all(a and b for a, b in gm_all): GOLD_JOINT["both"] += 1
            elif all(a or b for a, b in gm_all): GOLD_JOINT["one"] += 1
            else: GOLD_JOINT["misleading"] += 1
        def topk(pref_fn):
            rr = sorted(base, key=lambda p: -(p[1] * pref_fn(p[0])))[:K]
            return set(id2dia[rid] for rid, _ in rr if rid in id2dia)
        arms = {
            "hybrid":      topk(lambda rid: 1.0),
            "time_soft":   topk(lambda rid: 1.0 + T*G if match(rid, win) else 1.0),
            "alias_soft":  topk(lambda rid: 1.0 + T*G if match(rid, spk_cond) else 1.0),
            "comp_capped": topk(lambda rid: 1.0 + min(CAP, T*match(rid, win) + T*match(rid, spk_cond)) * G),
            "comp_sum":    topk(lambda rid: 1.0 + (T*match(rid, win) + T*match(rid, spk_cond)) * G),
            "comp_mult":   topk(lambda rid: (1.0 + T*G*match(rid, win)) * (1.0 + T*G*match(rid, spk_cond))),
        }
        # SELF-CHECK: reconstructed single arms must reproduce the SHIPPED prefer path's gold-recall
        if win is not None:
            n_check += 1
            if len(g & arms["time_soft"])/ng != len(g & shipped(qt, prefer=win, prefer_trust=T))/ng:
                n_mismatch += 1
        if spk_cond is not None:
            n_check += 1
            if len(g & arms["alias_soft"])/ng != len(g & shipped(qt, prefer=spk_cond, prefer_trust=T))/ng:
                n_mismatch += 1
        for a in ARMS: per_q[sub][a].append(len(g & arms[a])/ng)
    print(f"  conv {ci}: {kept} signal-bearing Q (t+{time.time()-_t0:.0f}s)", flush=True)
json.dump(_cache, open(CACHE, "w"))

def mean(x): return sum(x)/len(x) if x else float("nan")
def boot(dl, it=10000, seed=17):
    r = random.Random(seed); n = len(dl)
    if not n: return float("nan"), float("nan")
    s = [mean([dl[r.randrange(n)] for _ in range(n)]) for _ in range(it)]
    s.sort(); return s[int(.025*it)], s[int(.975*it)]

print(f"\nSELF-CHECK: {n_mismatch}/{n_check} single-arm reconstructions diverged from the shipped prefer path")
if n_check and n_mismatch / n_check > 0.01:
    print("!! SELF-CHECK FAILED (>1%) — composed numbers are NOT trustworthy, do not report them"); sys.exit(2)
print(f"\n=== LoCoMo composed soft filters (recall@{K}) ===")
out = {"k": K, "self_check": {"checked": n_check, "mismatched": n_mismatch},
       "prefer_gain": G, "trust": T, "cap": CAP,
       "gold_joint_truthfulness_both_subset": dict(GOLD_JOINT), "subsets": {}}
print(f"gold-vs-joint-filter on BOTH subset: {dict(GOLD_JOINT)} (both=truthful joint, misleading=some gold outside both)")
for sub in SUBSETS:
    n = len(per_q[sub]["hybrid"])
    print(f"\n--- {sub.upper()} subset (n={n}) ---")
    if not n: continue
    row = {}
    for a in ARMS:
        r = mean(per_q[sub][a]); row[a] = round(r, 4)
        print(f"  {a:<12}{r:.3f}")
    out["subsets"][sub] = {"n": n, "recall@20": row}
    if sub == "both" and n:  # keep the conjunction-subset deltas as before
        for comp in ("comp_capped", "comp_sum", "comp_mult"):
            for single in ("time_soft", "alias_soft", "hybrid"):
                dl = [per_q[sub][comp][i] - per_q[sub][single][i] for i in range(n)]
                lo, hi = boot(dl)
                out["subsets"][sub][f"{comp}_vs_{single}"] = {
                    "delta": round(mean(dl), 4), "ci95": [round(lo, 4), round(hi, 4)]}
                print(f"  {comp} vs {single}: {mean(dl):+.3f}  CI[{lo:+.3f},{hi:+.3f}]")

# FULL COMPOSABLE SET (jacksonxly's "deployment number"): every question where AT LEAST one cue fires,
# with the missing dimension seeded at 1.0 (multiplicatively neutral — a lone strong cue is never
# vetoed). This is the exact query-weighted distribution: concatenate the per-query recall values across
# both + time_only + alias_only (NOT a weighted average of the subset means — same result, but exact and
# bootstrappable). Contrast with the BOTH-subset (conjunction) number; they answer different questions.
full = {a: per_q["both"][a] + per_q["time_only"][a] + per_q["alias_only"][a] for a in ARMS}
nfull = len(full["hybrid"])
print(f"\n--- FULL COMPOSABLE SET (n={nfull}: both {len(per_q['both']['hybrid'])} + "
      f"time_only {len(per_q['time_only']['hybrid'])} + alias_only {len(per_q['alias_only']['hybrid'])}) ---")
full_row = {}
for a in ARMS:
    full_row[a] = round(mean(full[a]), 4)
    print(f"  {a:<12}{mean(full[a]):.3f}")
full_out = {"n": nfull, "recall@20": full_row, "missing_dim_seed": 1.0}
for comp in ("comp_mult", "comp_capped", "comp_sum"):
    dl = [full[comp][i] - full["hybrid"][i] for i in range(nfull)]
    lo, hi = boot(dl)
    full_out[f"{comp}_vs_hybrid"] = {"delta": round(mean(dl), 4), "ci95": [round(lo, 4), round(hi, 4)]}
    print(f"  {comp} vs hybrid: {mean(dl):+.3f}  CI[{lo:+.3f},{hi:+.3f}]")
out["full_composable_set"] = full_out

# SINGLE-CUE-ONLY REGIME (the seed's regime — jacksonxly: "there the whole game is what a missing
# dimension multiplies by"). Questions where EXACTLY ONE cue fires (time_only + alias_only, n=1202). Here
# the non-firing dimension is seeded at 1.0, so comp_mult == the lone firing cue EXACTLY (no sub-1.0 veto);
# the delta vs hybrid is the value the lone cue keeps precisely because the seed is graceful. This is the
# clean demonstration that seed=1.0 preserves a lone strong cue, vs a sub-1.0 seed which would drag it down.
sc = {a: per_q["time_only"][a] + per_q["alias_only"][a] for a in ARMS}
nsc = len(sc["hybrid"])
print(f"\n--- SINGLE-CUE-ONLY (seed's regime, n={nsc}: time_only {len(per_q['time_only']['hybrid'])} + "
      f"alias_only {len(per_q['alias_only']['hybrid'])}) ---")
sc_row = {a: round(mean(sc[a]), 4) for a in ARMS}
for a in ARMS: print(f"  {a:<12}{mean(sc[a]):.3f}")
# sanity: on a single-cue subset all three composed arms reduce to the SAME single-term formula
# (1 + T*G), so comp_mult == comp_sum == comp_capped per query. If this fails, the seed logic is wrong.
sc_invariant = all(abs(sc["comp_mult"][i] - sc["comp_sum"][i]) < 1e-12 and
                   abs(sc["comp_mult"][i] - sc["comp_capped"][i]) < 1e-12 for i in range(nsc))
print(f"  single-cue invariant (comp_mult==comp_sum==comp_capped): {sc_invariant}")
if not sc_invariant:
    print("!! SINGLE-CUE INVARIANT FAILED — seed/composition logic is wrong, do not report"); sys.exit(3)
dl = [sc["comp_mult"][i] - sc["hybrid"][i] for i in range(nsc)]
lo, hi = boot(dl)
sc_out = {"n": nsc, "recall@20": sc_row, "missing_dim_seed": 1.0,
          "comp_mult_vs_hybrid": {"delta": round(mean(dl), 4), "ci95": [round(lo, 4), round(hi, 4)]},
          "note": "comp_mult == the lone firing cue by construction (missing dim x 1.0, no veto)"}
print(f"  comp_mult vs hybrid: {mean(dl):+.3f}  CI[{lo:+.3f},{hi:+.3f}]  "
      f"(comp_mult == lone cue: seed=1.0 preserves it, no veto)")
out["single_cue_only"] = sc_out
json.dump(out, open("mnemo/probes/locomo_composed_soft_filters_result.json", "w"), indent=1)
print("\nsaved: mnemo/probes/locomo_composed_soft_filters_result.json")
