"""retrieval_recall_locomo.py — the CLEAN, judge-free, FREE inspeximus LOCOMO number.

Not end-to-end QA (that needs an LLM answerer + judge, which is judge-dependent and not comparable across systems).
This measures the memory system's ACTUAL job, deterministically and for free: given a question, does inspeximus's
top-k recall retrieve the gold EVIDENCE turn(s) LOCOMO labels as supporting the answer? No LLM, no credit, fully
reproducible. Uses the shipped tuned recipe (semantic embedder + hybrid RRF + soft speaker prefilter).

  recall@k (any)  = fraction of questions where >=1 gold-evidence turn is in the top-k
  recall@k (all)  = fraction where ALL gold-evidence turns are in the top-k (harder; needed for multi-hop)

RUN:  python research/probes/retrieval_recall_locomo.py --k 25
"""
import os, sys, json, argparse, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "inspeximus_pypi"))
import locomo_qa as LQ

DATA = os.path.join(HERE, "..", "..", "agora_output", "lab", "data", "locomo10.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=25); ap.add_argument("--convs", type=int, default=10)
    a = ap.parse_args()
    data = json.load(open(DATA, encoding="utf-8"))[:a.convs]
    print(f"LOCOMO RETRIEVAL-RECALL (LLM-free, tuned recipe) k={a.k} convs={len(data)}\n", flush=True)
    tot = collections.Counter(); any_hit = collections.Counter(); all_hit = collections.Counter()
    cat_tot = collections.Counter(); cat_any = collections.Counter()
    for ci, sample in enumerate(data):
        turns = LQ.conv_turns(sample)
        # batch-embed the whole conv (turns + questions) once, build the store once
        qa = [q for q in sample["qa"] if q.get("evidence") and q.get("category") != 5]
        LQ.nomic_embed([f"{sp}: {tx}" for _i, sp, tx in turns if tx.strip()] + [q["question"] for q in qa])
        store = LQ.build_inspeximus_store(turns)
        text2id = {f"{sp}: {tx}": tid for tid, sp, tx in turns if tx.strip()}   # recall hits carry text, not key
        speakers = {sp for _i, sp, _tx in turns if sp}
        for q in qa:
            gold = set(str(e) for e in (q.get("evidence") or []))
            if not gold:
                continue
            named = [s for s in speakers if s and s.lower() in q["question"].lower()]
            hits = store.recall(q["question"], k=a.k, mode="hybrid",
                                prefer={"speaker": named} if named else None) or []
            got = set(text2id.get(h.get("text", "")) for h in hits) - {None}
            cat = q.get("category")
            tot["all"] += 1; cat_tot[cat] += 1
            if gold & got:
                any_hit["all"] += 1; cat_any[cat] += 1
            if gold <= got:
                all_hit["all"] += 1
        print(f"  conv{ci}: {tot['all']} q processed (any-hit {any_hit['all']})", flush=True)
    n = tot["all"]
    res = {"k": a.k, "n": n,
           "recall_any": round(any_hit["all"] / n, 3) if n else None,
           "recall_all": round(all_hit["all"] / n, 3) if n else None,
           "by_category_any": {str(c): round(cat_any[c] / cat_tot[c], 3) for c in sorted(cat_tot) if cat_tot[c]},
           "by_category_n": {str(c): cat_tot[c] for c in sorted(cat_tot)}}
    json.dump(res, open(os.path.join(HERE, "retrieval_recall_locomo_result.json"), "w"), indent=1)
    print("\n" + json.dumps(res, indent=1))
    print("\nsaved retrieval_recall_locomo_result.json")


if __name__ == "__main__":
    main()
