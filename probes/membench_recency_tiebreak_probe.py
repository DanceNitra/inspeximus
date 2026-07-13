"""membench_recency_tiebreak_probe.py — measure the recency-tiebreak lever on MemBench.

v2 finding: on knowledge_update questions the STALE (pre-correction) value outranks the fresh
one at rank 1 in 32.7% of cases, identically for plain cosine and mnemo semantic recall
(free-text SRO supersession never triggers). Hypothesized lever: a small position (recency)
bonus in the recall score.

FAIRNESS DESIGN (method-skeptic baked in): a recency bonus trivially helps update questions
BY CONSTRUCTION (the correction always comes after the original mention). The real question is
the TRADEOFF: how much stale-beats-fresh reduction do we buy per point of hit@k lost on
NON-update splits (simple/highlevel/noisy), where late position carries no evidence signal.
A lever is only shippable if it collapses stale-beats-fresh at ~zero cost elsewhere.

Two-stage:
  A (online, embeds once): per question dump {split, sims (raw + centered cosine), gt_new,
    gt_old, n} to membench_recency_stage_a.json.
  B (offline, instant): sweep
      linear:   score = sim + lam * (pos/N)                    lam in LAMS
      near-tie: recency reorder only among msgs with sim >= top_sim - eps    eps in EPSS
    reporting stale-beats-fresh (knowledge_update) and hit@1/5 deltas (control splits).

GT note: knowledge_update GT is OUR lexical construction (benchmark annotations measured ~35%
reliable — see v2 header). INTERNAL numbers.

RUN: python -u mnemo/probes/membench_recency_tiebreak_probe.py    (local Ollama nomic)
Re-runs skip stage A if the stage-A dump exists.
"""
import json, os, sys, time, math, urllib.request

sys.stdout.reconfigure(errors="replace")

DATA_DIR = os.environ.get("MEMBENCH_DATA", "agora_output/lab/data/membench")
STAGE_A = "mnemo/probes/membench_recency_stage_a.json"
EMB_URL = os.environ.get("OLLAMA_EMBED_URL", "http://localhost:11434/api/embed")
MODEL = "nomic-embed-text"
QP, DP = "search_query: ", "search_document: "
N_KU, N_CTRL = 50, 50          # per category
LAMS = (0.0, 0.005, 0.01, 0.02, 0.05, 0.1)
EPSS = (0.005, 0.01, 0.02, 0.05)

def _post(inputs):
    inputs = [(s if (s and s.strip()) else " ") for s in inputs]
    body = json.dumps({"model": MODEL, "input": inputs}).encode()
    for attempt in range(3):
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                EMB_URL, data=body, headers={"Content-Type": "application/json"}), timeout=300)
            return json.loads(r.read())["embeddings"]
        except Exception:
            if attempt == 2: raise
            time.sleep(2)

def embed(texts, role):
    pref = QP if role == "q" else DP
    out = []
    for i in range(0, len(texts), 64):
        out.extend(_post([pref + t for t in texts[i:i + 64]]))
    return out

def cos(a, b):
    num = sum(x * y for x, y in zip(a, b))
    return num / ((sum(x * x for x in a) ** 0.5) * (sum(x * x for x in b) ** 0.5) + 1e-12)

def flat_msgs(t, key):
    return [m[key] for s in t["message_list"] for m in s]

def load_questions():
    items = []
    d = json.load(open(f"{DATA_DIR}/knowledge_update.json", encoding="utf-8"))
    for cat in ("roles", "events"):
        for t in d[cat][:N_KU]:
            flat = flat_msgs(t, "user_message")
            qa = t["QA"]; ans = qa["answer"].lower()
            gt_new = sorted(i for i, m in enumerate(flat) if ans in m.lower())
            if not gt_new: continue
            old_vals = [v.lower() for c, v in qa["choices"].items() if c != qa["ground_truth"]]
            gt_old = sorted(i for i, m in enumerate(flat)
                            if i not in set(gt_new) and any(v in m.lower() for v in old_vals))
            items.append(("knowledge_update", flat, qa["question"], gt_new, gt_old))
    for split, fname, cats, key in (
            ("simple", "simple.json", ("roles", "events"), "user_message"),
            ("highlevel", "highlevel.json", ("movie", "food", "book"), "user"),
            ("noisy", "noisy.json", ("roles", "events"), "user_message")):
        d = json.load(open(f"{DATA_DIR}/{fname}", encoding="utf-8"))
        per = N_CTRL // len(cats) if split == "highlevel" else N_CTRL // len(cats)
        for cat in cats:
            for t in d[cat][:per]:
                flat = flat_msgs(t, key)
                gt = sorted({a for a, _ in t["QA"]["target_step_id"] if a < len(flat)})
                if gt:
                    items.append((split, flat, t["QA"]["question"], gt, []))
    return items

def stage_a():
    if os.path.exists(STAGE_A):
        print(f"stage A dump exists -> {STAGE_A} (delete to re-embed)")
        return json.load(open(STAGE_A))
    items = load_questions()
    print(f"stage A: {len(items)} questions, ~{sum(len(f) for _, f, _, _, _ in items)} msgs to embed")
    rows, t0 = [], time.time()
    for n, (split, flat, q, gt_new, gt_old) in enumerate(items):
        dvecs = embed(flat, "d")
        qvec = embed([q], "q")[0]
        sims_raw = [cos(qvec, v) for v in dvecs]
        dim = len(qvec)
        mean = [sum(v[j] for v in dvecs) / len(dvecs) for j in range(dim)]
        qc = [q_ - m_ for q_, m_ in zip(qvec, mean)]
        sims_cent = [cos(qc, [x - m_ for x, m_ in zip(v, mean)]) for v in dvecs]
        rows.append({"split": split, "n": len(flat), "gt_new": gt_new, "gt_old": gt_old,
                     "sims_raw": [round(s, 6) for s in sims_raw],
                     "sims_cent": [round(s, 6) for s in sims_cent]})
        if (n + 1) % 20 == 0:
            print(f"  ... {n+1}/{len(items)} ({time.time()-t0:.0f}s)", flush=True)
            json.dump(rows, open(STAGE_A, "w"))
    json.dump(rows, open(STAGE_A, "w"))
    print(f"stage A done in {time.time()-t0:.0f}s")
    return rows

def rank_linear(sims, lam):
    n = len(sims)
    return sorted(range(n), key=lambda i: -(sims[i] + lam * (i / max(1, n - 1))))

def rank_neartie(sims, eps):
    n = len(sims)
    base = sorted(range(n), key=lambda i: -sims[i])
    top = sims[base[0]]
    tied = sorted([i for i in base if sims[i] >= top - eps], key=lambda i: -i)  # latest first
    rest = [i for i in base if sims[i] < top - eps]
    return tied + rest

def evaluate(rows, ranker, simkey):
    """-> (stale_beats_fresh_rate, n_sbf, {split: {hit@1, hit@5}})"""
    sbf, per = [], {}
    for r in rows:
        rk = ranker(r[simkey])
        pos = {i: p for p, i in enumerate(rk)}
        gt = r["gt_new"]
        top1, top5 = set(rk[:1]), set(rk[:5])
        d = per.setdefault(r["split"], {"h1": [], "h5": []})
        d["h1"].append(1.0 if any(t in top1 for t in gt) else 0.0)
        d["h5"].append(1.0 if any(t in top5 for t in gt) else 0.0)
        if r["split"] == "knowledge_update" and r["gt_old"]:
            bn = min(pos[i] for i in gt)
            bo = min(pos[i] for i in r["gt_old"])
            sbf.append(1.0 if bo < bn else 0.0)
    rate = sum(sbf) / len(sbf) if sbf else float("nan")
    return rate, len(sbf), {s: {k: sum(v) / len(v) for k, v in d.items()} for s, d in per.items()}

def main():
    rows = stage_a()
    for simkey in ("sims_raw", "sims_cent"):
        print(f"\n================ {simkey} ================")
        base_rate, nsbf, base_per = evaluate(rows, lambda s: rank_linear(s, 0.0), simkey)
        print(f"baseline: STALE-BEATS-FRESH={base_rate:.3f} (n={nsbf}) | " +
              " ".join(f"{s}:h1={d['h1']:.3f},h5={d['h5']:.3f}" for s, d in sorted(base_per.items())))
        print("-- linear recency bonus: score = sim + lam*(pos/N)")
        for lam in LAMS[1:]:
            rate, _, per = evaluate(rows, lambda s: rank_linear(s, lam), simkey)
            costs = " ".join(f"{s}:dh1={per[s]['h1']-base_per[s]['h1']:+.3f},"
                             f"dh5={per[s]['h5']-base_per[s]['h5']:+.3f}"
                             for s in ("simple", "highlevel", "noisy"))
            print(f"  lam={lam:<6} SBF={rate:.3f} ({base_rate:.3f}->) | ctrl {costs}")
        print("-- near-tie recency reorder (only within eps of top sim)")
        for eps in EPSS:
            rate, _, per = evaluate(rows, lambda s: rank_neartie(s, eps), simkey)
            costs = " ".join(f"{s}:dh1={per[s]['h1']-base_per[s]['h1']:+.3f},"
                             f"dh5={per[s]['h5']-base_per[s]['h5']:+.3f}"
                             for s in ("simple", "highlevel", "noisy"))
            print(f"  eps={eps:<6} SBF={rate:.3f} ({base_rate:.3f}->) | ctrl {costs}")

main()
