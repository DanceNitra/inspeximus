"""membench_recall_probe_v2.py — SCALE-UP + two new splits of the MemBench retrieval-only eval.

Extends membench_recall_probe.py (feasibility, 70 trajs) with:
  1. simple     n=200 (100 roles + 100 events)  - factual, ~165 msgs/traj
  2. highlevel  n=150 (50 movie/food/book)      - reflective, multi-evidence, ~13 msgs/traj
  3. noisy      n=100                            - SAME task as simple but the QUESTION is buried
                                                   in ~4 sentences of small-talk distractors
                                                   (query-robustness arm)
  4. knowledge_update n=100                      - a fact is stated then CORRECTED later in the
                                                   dialogue; the question asks the current value.

GROUND-TRUTH NOTES (fixture-construction honesty):
  - simple / highlevel / noisy use the benchmark's own annotation (target_step_id[i][0] = global
    flat evidence index; verified empirically in v1).
  - knowledge_update's annotations are UNRELIABLE (measured on 60 trajs: only ~35% of
    target_step_id point at a message containing the answer; evidence is often 1-2 messages
    later; the data also contains duplicated consecutive messages). For that split we therefore
    build GT OURSELVES, lexically: GT_new = messages containing the answer string (the corrected
    value), GT_old = messages containing any distractor choice string (the pre-correction value).
    Trajectories where the answer string never appears are dropped (~25%) — this restricts the
    split to lexically-recoverable updates and is OUR construction, not the benchmark's.
  - metrics: hit@k / full@k as v1; for knowledge_update additionally STALE-BEATS-FRESH rate =
    fraction of questions where the best-ranked stale (old-value) message outranks every
    fresh (corrected) message in the arm's ranking — the retrieval failure mode that answers
    the old value.

Arms: plain nomic cosine vs mnemo.recall(mode='semantic') with the same embedder (asymmetric
prefixes). No disk embedding cache: ~50k vectors would be a ~400MB JSON (probe-cache lesson);
nomic is deterministic and the GPU is idle, so we embed streaming per-trajectory instead.

HONEST SCOPE: retrieval-only (not comparable to the paper's LLM answer-accuracy tables);
FirstAgent (participation) perspective only; single embedder; INTERNAL numbers.

RUN: python mnemo/probes/membench_recall_probe_v2.py   (local Ollama, nomic-embed-text)
"""
import json, os, sys, math, time, urllib.request, tempfile

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "mnemo")))
from mnemo import Mnemo

DATA_DIR = os.environ.get("MEMBENCH_DATA", "agora_output/lab/data/membench")
EMB_URL = os.environ.get("OLLAMA_EMBED_URL", "http://localhost:11434/api/embed")
MODEL = "nomic-embed-text"
QP, DP = "search_query: ", "search_document: "
KS = (1, 5, 10)

def _post(inputs):
    inputs = [(s if (s and s.strip()) else " ") for s in inputs]
    body = json.dumps({"model": MODEL, "input": inputs}).encode()
    for attempt in range(3):
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                EMB_URL, data=body, headers={"Content-Type": "application/json"}), timeout=300)
            return json.loads(r.read())["embeddings"]
        except Exception:
            if attempt == 2:
                raise
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

def load_all():
    """-> list of (split, flat, question, gt_new_idx_set, gt_old_idx_set_or_None)"""
    items = []
    d = json.load(open(f"{DATA_DIR}/simple.json", encoding="utf-8"))
    for cat in ("roles", "events"):
        for t in d[cat][:100]:
            flat = flat_msgs(t, "user_message")
            gt = {a for a, _ in t["QA"]["target_step_id"] if a < len(flat)}
            if gt:
                items.append(("simple", flat, t["QA"]["question"], gt, None))
    d = json.load(open(f"{DATA_DIR}/highlevel.json", encoding="utf-8"))
    for cat in ("movie", "food", "book"):
        for t in d[cat][:50]:
            flat = flat_msgs(t, "user")
            gt = {a for a, _ in t["QA"]["target_step_id"] if a < len(flat)}
            if gt:
                items.append(("highlevel", flat, t["QA"]["question"], gt, None))
    d = json.load(open(f"{DATA_DIR}/noisy.json", encoding="utf-8"))
    for cat in ("roles", "events"):
        for t in d[cat][:50]:
            flat = flat_msgs(t, "user_message")
            gt = {a for a, _ in t["QA"]["target_step_id"] if a < len(flat)}
            if gt:
                items.append(("noisy", flat, t["QA"]["question"], gt, None))
    d = json.load(open(f"{DATA_DIR}/knowledge_update.json", encoding="utf-8"))
    dropped = 0
    for cat in ("roles", "events"):
        for t in d[cat][:50]:
            flat = flat_msgs(t, "user_message")
            qa = t["QA"]
            ans = qa["answer"].lower()
            gt_new = {i for i, m in enumerate(flat) if ans in m.lower()}
            if not gt_new:
                dropped += 1; continue     # lexically unrecoverable — our GT can't score it
            old_vals = [v.lower() for c, v in qa["choices"].items() if c != qa["ground_truth"]]
            gt_old = {i for i, m in enumerate(flat)
                      if i not in gt_new and any(v in m.lower() for v in old_vals)}
            items.append(("knowledge_update", flat, qa["question"], gt_new, gt_old or None))
    print(f"loaded {len(items)} questions "
          f"(knowledge_update dropped {dropped} lexically-unrecoverable)")
    return items

def main():
    items = load_all()
    scores, stale = {}, {}
    t0 = time.time()
    for n, (split, flat, q, gt_new, gt_old) in enumerate(items):
        dvecs = embed(flat, "d")
        qvec = embed([q], "q")[0]
        ranked = sorted(range(len(flat)), key=lambda i: -cos(qvec, dvecs[i]))

        doc_vec = {DP + t: v for t, v in zip(flat, dvecs)}
        def emb_fn(text):
            v = doc_vec.get(DP + text)
            return v if v is not None else embed([text], "q")[0]
        fd, p = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(p)
        m = Mnemo(path=p, embed=emb_fn)
        ids = [m.remember(t, mtype="episodic") for t in flat]
        idx_of = {mid: i for i, mid in enumerate(ids)}
        got = m.recall(q, k=len(flat), mode="semantic")
        mn_ranked = [idx_of[r["id"]] for r in got if r["id"] in idx_of]
        if os.path.exists(p): os.remove(p)

        for arm, rk in (("cosine", ranked), ("mnemo", mn_ranked)):
            for k in KS:
                top = set(rk[:k])
                scores.setdefault((split, arm, "hit", k), []).append(
                    1.0 if any(t in top for t in gt_new) else 0.0)
                scores.setdefault((split, arm, "full", k), []).append(
                    1.0 if all(t in top for t in gt_new) else 0.0)
            if gt_old:
                pos = {i: r for r, i in enumerate(rk)}
                best_new = min(pos.get(i, 10 ** 9) for i in gt_new)
                best_old = min(pos.get(i, 10 ** 9) for i in gt_old)
                stale.setdefault((split, arm), []).append(1.0 if best_old < best_new else 0.0)
        if (n + 1) % 50 == 0:
            print(f"  ... {n+1}/{len(items)} ({time.time()-t0:.0f}s)")

    def se(v):
        p = sum(v) / len(v)
        return math.sqrt(p * (1 - p) / len(v))
    out = {}
    for split in ("simple", "highlevel", "noisy", "knowledge_update"):
        n = len(scores.get((split, "cosine", "hit", 1), []))
        if not n: continue
        print(f"\n=== MEASURED {split} (n={n}) ===")
        for arm in ("cosine", "mnemo"):
            parts = []
            for k in KS:
                v = scores[(split, arm, "hit", k)]
                parts.append(f"hit@{k}={sum(v)/n:.3f}(se {se(v):.3f})")
                out[f"{split}|{arm}|hit@{k}"] = round(sum(v) / n, 4)
            for k in (5, 10):
                v = scores[(split, arm, "full", k)]
                parts.append(f"full@{k}={sum(v)/n:.3f}")
                out[f"{split}|{arm}|full@{k}"] = round(sum(v) / n, 4)
            if (split, arm) in stale:
                v = stale[(split, arm)]
                parts.append(f"STALE-BEATS-FRESH={sum(v)/len(v):.3f}(n={len(v)})")
                out[f"{split}|{arm}|stale_beats_fresh"] = round(sum(v) / len(v), 4)
            print(f"  {arm:7s} " + " ".join(parts))
    json.dump(out, open("mnemo/probes/membench_recall_probe_v2_result.json", "w"), indent=2)
    print(f"\ntotal {time.time()-t0:.0f}s -> mnemo/probes/membench_recall_probe_v2_result.json")

main()
