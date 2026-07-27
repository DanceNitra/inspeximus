"""locomo_qa.py — END-TO-END LOCOMO QA accuracy for inspeximus (the number buyers open with).

Prior lab work measured retrieval RECALL@K (did the gold turn get retrieved). Buyers quote the END-TO-END
QA-ACCURACY that mem0 (66.9% LOCOMO, LLM-as-judge) and Zep (71.2% LongMemEval) publish. This harness produces
the comparable metric for inspeximus: ingest each conversation into inspeximus, for each question recall top-k, an LLM
answers from ONLY the recalled context, and an LLM judge scores the answer vs the gold answer.

Fair design: inspeximus vs a full-context ceiling vs a naive-recency baseline all answered+judged by the SAME model,
so the RELATIVE result is fair even when the absolute number is not directly comparable to mem0's gpt-4o judge.
Answerer/judge model is configurable: local (llama3.1:8b, free) for a pilot, gpt-4o for the mem0-comparable run
(needs OpenAI credit). Retrieval (inspeximus) is always free/local.

RUN (free pilot, local LLM):  python research/probes/locomo_qa.py --convs 1 --systems inspeximus,fullcontext,naive --model llama3.1:8b --base local
RUN (mem0-comparable):        python research/probes/locomo_qa.py --convs 10 --systems inspeximus --model gpt-4o --base openai
"""
import os, sys, json, argparse, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "inspeximus_pypi"))
from inspeximus import Inspeximus

DATA = os.path.join(HERE, "..", "..", "agora_output", "lab", "data", "locomo10.json")


def _key():
    for l in open(os.path.join(HERE, "..", "..", "server", ".env"), encoding="utf-8", errors="replace"):
        if l.startswith("OPENAI_API_KEY="):
            return l.split("=", 1)[1].strip()
    return "ollama"


def llm(base, model, prompt, max_tokens=60, temp=0.0):
    if base == "openai":
        url, key = "https://api.openai.com/v1/chat/completions", _key()
    else:
        url, key = "http://localhost:11434/v1/chat/completions", "ollama"
    body = json.dumps({"model": model, "temperature": temp, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    for a in range(3):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + key}), timeout=120)
            return json.loads(r.read())["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if a == 2:
                return f"__ERR__{e}"
            time.sleep(2)


def conv_turns(sample):
    """Flatten the LOCOMO conversation into (turn_id, speaker, text) in order. The session DATE is prepended to
    each turn's text — LOCOMO temporal questions (category 2) are unanswerable without it, and dropping it would
    unfairly tank every system on temporal QA."""
    conv = sample["conversation"]
    out = []
    for skey in sorted([k for k in conv if k.startswith("session_") and not k.endswith("date_time")],
                       key=lambda s: int(s.split("_")[1])):
        date = conv.get(skey + "_date_time", "")
        for t in conv[skey]:
            tid = t.get("dia_id") or t.get("id") or ""
            body = t.get("text", "") or t.get("clean_text", "")
            out.append((tid, t.get("speaker", ""), f"[{date}] {body}" if date else body))
    return out


_EMB_CACHE = {}


def nomic_embed(texts):
    """Batch-embed via local Ollama nomic-embed-text (free), cached. One call amortizes the ~2s fixed overhead."""
    todo = [t for t in texts if t and t not in _EMB_CACHE]
    for i in range(0, len(todo), 96):
        chunk = todo[i:i+96]
        body = json.dumps({"model": "nomic-embed-text", "input": chunk}).encode()
        try:
            r = urllib.request.urlopen(urllib.request.Request("http://localhost:11434/api/embed", data=body,
                headers={"Content-Type": "application/json"}), timeout=180)
            for t, e in zip(chunk, json.loads(r.read())["embeddings"]):
                _EMB_CACHE[t] = e
        except Exception:
            for t in chunk:
                _EMB_CACHE.setdefault(t, None)
    return [_EMB_CACHE.get(t) for t in texts]


def _embed_one(t):
    return nomic_embed([t])[0]


def build_inspeximus_store(turns):
    """Build the inspeximus store for a conversation ONCE (not per question). Batch-embeds all turns in one pass, then
    ingests them. This is the shippable recipe using only built-in inspeximus features (embed + meta speaker)."""
    texts = [f"{sp}: {tx}" for _i, sp, tx in turns if tx.strip()]
    nomic_embed(texts)                                               # ONE batched embed pass for the whole conv
    m = Inspeximus(path=None, embed=_embed_one)
    for tid, sp, tx in turns:
        if tx.strip():
            m.remember(f"{sp}: {tx}", key=tid or None, meta={"speaker": sp})
    return m


def recall_context(store, turns, question, k):
    """Recall the top-k context for a question from a prebuilt store — inspeximus's hybrid RRF + the SOFT speaker
    prefilter (prefer=), the two biggest measured LOCOMO levers."""
    speakers = {sp for _i, sp, _tx in turns if sp}
    named = [s for s in speakers if s and s.lower() in question.lower()]
    hits = store.recall(question, k=k, mode="hybrid",
                        prefer={"speaker": named} if named else None) or []
    return "\n".join(h.get("text", "") for h in hits)


def build_context(system, turns, question, k):
    """Backward-compatible single-shot context builder (rebuilds the inspeximus store each call — used only by the
    local/openai answerer paths). The --base claude path builds the store ONCE per conversation instead."""
    if system == "fullcontext":
        return "\n".join(f"{sp}: {tx}" for _i, sp, tx in turns)
    if system == "naive":
        return "\n".join(f"{sp}: {tx}" for _i, sp, tx in turns[-k:])
    return recall_context(build_inspeximus_store(turns), turns, question, k)


def judge(base, model, question, gold, pred):
    p = (f"Question: {question}\nGold answer: {gold}\nModel answer: {pred}\n\n"
         "Does the model answer match the gold answer in meaning (allow paraphrase, date/format differences)? "
         "Reply with exactly YES or NO.")
    r = llm(base, model, p, max_tokens=4).upper()
    return "YES" in r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--convs", type=int, default=1); ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--maxq", type=int, default=0, help="cap questions per conv (0=all)")
    ap.add_argument("--systems", default="inspeximus"); ap.add_argument("--model", default="llama3.1:8b")
    ap.add_argument("--base", default="local", choices=["local", "openai", "claude"])
    ap.add_argument("--score-claude", action="store_true", help="score locomo_qa_answers.json (Claude judged)")
    a = ap.parse_args()

    if a.score_claude:
        ans = json.load(open(os.path.join(HERE, "locomo_qa_answers.json"), encoding="utf-8"))
        agg = {}
        for r in ans:
            s = r["system"]; d = agg.setdefault(s, {"c": 0, "n": 0, "cat": {}})
            d["c"] += int(bool(r["correct"])); d["n"] += 1
            bc = d["cat"].setdefault(str(r.get("category", "?")), [0, 0])
            bc[0] += int(bool(r["correct"])); bc[1] += 1
        res = {s: {"accuracy": round(d["c"]/d["n"], 3) if d["n"] else None, "n": d["n"],
                   "by_category": {k: round(v[0]/v[1], 3) for k, v in d["cat"].items() if v[1]}}
               for s, d in agg.items()}
        json.dump({"judge": "claude-code", "results": res},
                  open(os.path.join(HERE, "locomo_qa_result.json"), "w"), indent=1)
        print(json.dumps(res, indent=1)); return

    data = json.load(open(DATA, encoding="utf-8"))[:a.convs]

    if a.base == "claude":
        # Claude-Code-as-the-LLM: dump (question, inspeximus-retrieved context, gold) per item; Claude answers
        # STRICTLY from the context (no world knowledge) + judges vs gold, writes locomo_qa_answers.json, and
        # score_claude() computes accuracy. Free, strong answerer, no cloud credit.
        batch = []
        syslist = [s.strip() for s in a.systems.split(",")]
        for ci, sample in enumerate(data):
            turns = conv_turns(sample); qa = sample["qa"]
            if a.maxq:
                qa = qa[:a.maxq]
            # BATCH + BUILD-ONCE: embed every turn AND every question of this conversation in one pass, and build
            # the inspeximus store a SINGLE time — not once per question (that was 250x rebuild = 150k wasted writes).
            nomic_embed([f"{sp}: {tx}" for _i, sp, tx in turns if tx.strip()] + [q["question"] for q in qa])
            mstore = build_inspeximus_store(turns) if "inspeximus" in syslist else None
            for qi, q in enumerate(qa):
                if not str(q.get("answer", "")).strip() or q.get("category") == 5:
                    continue
                for system in syslist:
                    ctx = (recall_context(mstore, turns, q["question"], a.k) if system == "inspeximus"
                           else build_context(system, turns, q["question"], a.k))
                    batch.append({"qid": f"c{ci}q{qi}", "system": system, "category": q.get("category"),
                                  "question": q["question"], "gold": str(q["answer"]).strip(),
                                  "context": ctx[:4000]})
        json.dump(batch, open(os.path.join(HERE, "locomo_qa_batch.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"DUMPED {len(batch)} items -> locomo_qa_batch.json (Claude: answer from context only, judge vs gold)")
        return
    print(f"LOCOMO QA end-to-end · convs={len(data)} k={a.k} answerer/judge={a.model} ({a.base})\n", flush=True)
    out = {s: {"correct": 0, "total": 0, "by_cat": {}} for s in a.systems.split(",")}
    for ci, sample in enumerate(data):
        turns = conv_turns(sample)
        qa = sample["qa"]
        if a.maxq:
            qa = qa[:a.maxq]
        for system in out:
            ctx = None
            for q in qa:
                cat = str(q.get("category", "?"))
                gold = str(q.get("answer", "")).strip()
                if not gold or q.get("category") == 5:      # cat 5 = adversarial/unanswerable, skip
                    continue
                context = build_context(system, turns, q["question"], a.k)
                ans = llm(a.base, a.model,
                          f"Answer the question using ONLY this conversation memory. Be brief.\n\n"
                          f"Memory:\n{context[:6000]}\n\nQuestion: {q['question']}\nAnswer:", max_tokens=60)
                ok = judge(a.base, a.model, q["question"], gold, ans)
                out[system]["correct"] += ok; out[system]["total"] += 1
                bc = out[system]["by_cat"].setdefault(cat, [0, 0]); bc[0] += ok; bc[1] += 1
            t = out[system]["total"]; c = out[system]["correct"]
            print(f"  conv{ci} {system:12} acc={c/t:.3f} ({c}/{t})" if t else f"  conv{ci} {system}: no q", flush=True)
    res = {}
    for s, d in out.items():
        acc = d["correct"] / d["total"] if d["total"] else None
        res[s] = {"accuracy": round(acc, 3) if acc is not None else None, "n": d["total"],
                  "by_category": {k: round(v[0]/v[1], 3) for k, v in d["by_cat"].items() if v[1]}}
    json.dump({"convs": len(data), "k": a.k, "model": a.model, "base": a.base, "results": res},
              open(os.path.join(HERE, "locomo_qa_result.json"), "w"), indent=1)
    print("\n" + json.dumps(res, indent=1))
    print("saved locomo_qa_result.json")


if __name__ == "__main__":
    main()
