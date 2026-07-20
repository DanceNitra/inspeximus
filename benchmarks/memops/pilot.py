"""Phase 2 — pilot: 5 memory arms on MemOps long-context data. Zero external spend (Ollama Cloud only).

Design note that decides everything (recorded because it is the load-bearing choice):
mnemo's supersession is KEYED. If we ingest raw conversation chunks with no keys, no key ever collides,
supersession never fires, and `mnemo` is `naive` BY CONSTRUCTION — the test would be rigged to a null.
So the mnemo arm uses its shipped deterministic `regex_extractor` to derive keys from user turns, which
is how the product is actually meant to consume raw text. The naive arm ingests the SAME turns with no
extractor, no echo_guard, no read-time resolver. Every other thing (granularity, retriever, k, answerer,
judge, prompts) is identical, so any delta is the integrity layer and nothing else.

Granularity: TURN level for the mnemo/naive pair (keys can only be derived per statement) and SESSION
level for the `session_rag` arm — which doubles as a measurement of what turn-level granularity costs us
inside our own harness. The paper reports session 0.845 vs turn 0.618, so absolute numbers here will sit
below their table; we are not comparing to their table (see PREREGISTRATION.md).
"""
import json
import os
import pathlib
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = pathlib.Path(__file__).resolve().parent
from mnemo.mnemo import Mnemo, regex_extractor  # noqa: E402

ANSWER_BASE, ANSWER_MODEL = "https://ollama.com/v1", "deepseek-v4-flash"
JUDGE_BASE, JUDGE_MODEL, JUDGE_KEY = "http://localhost:11434/v1", "glm-5.2:cloud", "local"
JUDGE_MAX_TOKENS = 16000
# TOPK is the retrieval depth of the turn-level arms. The first pilot ran k=20, which the
# retrieval_coverage.py diagnostic later showed spends only ~1.3k chars vs session_rag's ~11.9k —
# a 9x context-budget confound. k=150 matches the budget (both ~11.9k) and is the corrected setting.
TOPK = int(os.environ.get("MEMOPS_TOPK", "20"))
CONCURRENCY = 3          # ollama.com concurrent-request cap


def _key(path, name):
    for ln in pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        m = re.match(rf"^{name}\s*=\s*(.*)$", s)
        if m:
            return m.group(1).strip().strip('"')
    return None


ANSWER_KEY = os.environ.get("MEMOPS_API_KEY", "")   # OpenAI-compatible key for the answerer


def call(base, model, key, prompt, max_tokens, retries=3):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0}
    for a in range(retries):
        try:
            req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {key}"})
            r = json.load(urllib.request.urlopen(req, timeout=300))
            c = ((r.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if c.strip():
                return c.strip()
        except Exception:
            pass
        time.sleep(2 + 3 * a)
    return ""


ANSWER_PROMPT = """You are an assistant with a memory of this user's past conversations.

RETRIEVED MEMORIES (may be incomplete, may contain outdated statements the user later corrected):
{context}

USER'S QUESTION:
{question}

Answer using ONLY the memories above. If the user corrected a value, use the CURRENT one. If the user
asked you to forget something, do not reveal it. Answer directly and concisely."""

JUDGE_PROMPT = """You grade a memory system's answer against a gold operation trace. Be strict and literal.

EVIDENCE CONVERSATION (the only ground truth):
{conversation}

GOLD OPERATION TRACE:
{trace}

QUESTION ASKED:
{question}

EXPECTED ANSWER:
{expected}

THE SYSTEM'S ANSWER TO GRADE:
{response}

Grade these fields. Reply ONLY with one JSON object, no prose:
{{"answer_score": 0 or 1, "stale_value": 0 or 1 or null, "leakage": 0 or 1 or null, "over_forget": 0 or 1 or null}}

- answer_score: 1 if the system's answer semantically satisfies the expected answer (the core requested
  value / state / choice is correct). 0 otherwise.
- stale_value: for an Update operation, 1 if the system's answer presents an OLD, superseded value as the
  CURRENT one. Decide by the final state of the operation chain, not by whether the value was ever valid:
  in a chain A->B->A->C the current value is C, so asserting A "is what is on file now" is stale_value=1
  EVEN THOUGH A was legitimately current earlier. Merely RECOUNTING an old value as history ("you
  initially took A, then switched to C") is NOT stale — that is 0. null if this is not an Update case.
- leakage: for a Forget operation, 1 if the answer reveals a value the user asked to forget, in a context
  where the expected answer does not name it. 0 otherwise. null if not a Forget case.
- over_forget: for a Forget operation, 1 if the answer wrongly treats an UNRELATED retained fact as
  deleted/unavailable. 0 otherwise. null if not a Forget case."""


def turns_of(scenario):
    """Every dialogue turn, in order, with its segment index."""
    out = []
    for seg in scenario.get("conversations", []):
        for t in seg.get("dialogue", []):
            c = (t.get("content") or "").strip()
            if c:
                out.append((seg.get("segment_index", 0), t.get("role", "user"), c))
    return out


def sessions_of(scenario):
    out = []
    for seg in scenario.get("conversations", []):
        txt = "\n".join(f"{t.get('role')}: {(t.get('content') or '').strip()}"
                        for t in seg.get("dialogue", []) if (t.get("content") or "").strip())
        if txt:
            out.append(txt)
    return out


def units_of(scenario):
    """Ingestion units, IDENTICAL for the mnemo and naive arms.

    Two harness bugs found in the first dry run and fixed here (both mine, not the product's):
      1. prefixing the text with "user: " made regex_extractor return None on everything — it matches from
         the start of the string, so the role prefix shadowed the subject. Role is now a TAG, not text.
      2. turns are multi-sentence, and the extractor matches a whole string; sentence-splitting is what
         lets a key be derived at all.
    Deliberately NOT done: stripping conversational discourse markers ("So my current title is ...") to
    make keys collide more often. That would be tuning the instrument to flatter our own product.
    """
    out = []
    for si, role, content in turns_of(scenario):
        for s in re.split(r"(?<=[.!?])\s+", content):
            s = s.strip()
            if s:
                out.append((si, role, s))
    return out


def build_mnemo(scenario, keyed: bool):
    m = Mnemo(path=None)
    if keyed:
        m.extractor = regex_extractor
        m.echo_guard = True
    for _si, role, sent in units_of(scenario):
        try:
            m.remember(sent, tags=[role])
        except Exception:
            pass
    return m


def recall_mnemo(m, q, keyed):
    hits = m.recall(q, k=TOPK, mode="lexical", reinforce=False,
                    resolve_conflicts=keyed) or []
    return "\n".join(f"- {h.get('text','')}" for h in hits)


def build_mem0(scenario, scenario_id):
    """mem0 with its OWN LLM extraction pipeline, pointed at Ollama Cloud (zero external spend).
    This is the arm that makes the head-to-head internally valid: same data, same answerer, same judge,
    so the only difference is verbatim-keyed storage (mnemo) vs LLM-extracted storage (mem0)."""
    import tempfile
    from mem0 import Memory
    # mem0's extractor model is deliberately the STRONGER of the two we can run for free
    # (mem0_positive_control.py: glm-5.2 -> 20 memories / 0 parse errors; deepseek-v4-flash -> 5 / 1).
    # Handing the competitor the weaker extractor would manufacture a strawman zero.
    mem0_model = os.environ.get("MEMOPS_MEM0_MODEL", "glm-5.2")
    os.environ["OPENAI_API_KEY"] = ANSWER_KEY          # mem0 reads the OpenAI-compatible key from env
    os.environ["OPENAI_BASE_URL"] = ANSWER_BASE
    d = tempfile.mkdtemp(prefix="memops_mem0_")
    m = Memory.from_config({
        "llm": {"provider": "openai", "config": {"model": mem0_model, "temperature": 0,
                                                 "openai_base_url": ANSWER_BASE, "api_key": ANSWER_KEY}},
        "embedder": {"provider": "ollama", "config": {"model": "nomic-embed-text",
                                                      "ollama_base_url": "http://localhost:11434"}},
        "vector_store": {"provider": "qdrant", "config": {"path": os.path.join(d, "qd"),
                                                          "embedding_model_dims": 768, "on_disk": True}},
        "history_db_path": os.path.join(d, "history.db")})
    # ingest at SESSION granularity — mem0's own extractor is designed to digest chunks, and this is the
    # granularity the MemOps paper found works best for retrieval-based memory (0.845 vs 0.618 turn-level)
    # NO truncation. `sess[:6000]` (the first version) cut long sessions off before the injected
    # evidence and left mem0 with 20 memories instead of 262 — it scored 0.000 for a defect of ours,
    # not of its own. Its history ledger over a full ingest is pure ADD, so it deletes nothing.
    for i, sess in enumerate(sessions_of(scenario)):
        try:
            m.add(sess, user_id=scenario_id)
        except Exception:
            pass
    return m


def recall_mem0(m, q, scenario_id):
    try:
        # mem0's parameter is top_k, not limit (mem0/memory/main.py:1415) — `limit=` silently fell
        # into **kwargs and the default applied. top_k=100 yields ~24k chars, which the shared
        # ctx[:12000] truncation then trims to the SAME budget the other arms get.
        r = m.search(q, filters={"user_id": scenario_id}, top_k=100) or {}
        hits = r.get("results") if isinstance(r, dict) else r
        return "\n".join(f"- {h.get('memory','')}" for h in (hits or []))
    except Exception:
        return ""


def build_bm25_sessions(scenario):
    from rank_bm25 import BM25Okapi
    docs = sessions_of(scenario)
    return BM25Okapi([d.lower().split() for d in docs]), docs


def recall_bm25(idx, q):
    bm, docs = idx
    scores = bm.get_scores(q.lower().split())
    top = sorted(range(len(docs)), key=lambda i: -scores[i])[:3]
    return "\n\n".join(docs[i][:4000] for i in top)


def judge(conv, trace, q, exp, resp):
    raw = call(JUDGE_BASE, JUDGE_MODEL, JUDGE_KEY,
               JUDGE_PROMPT.format(conversation=conv, trace=trace, question=q, expected=exp, response=resp),
               JUDGE_MAX_TOKENS)
    m = re.search(r"\{[^{}]*\}", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


TAG = "run"


def run(files, arms):
    results = []
    for fi, name in enumerate(files, 1):
        lc = json.loads((HERE / "data_lc" / name).read_text(encoding="utf-8"))
        ev = json.loads((HERE / "data" / name).read_text(encoding="utf-8"))
        op = (ev.get("operation_type") or "").lower()
        conv = json.dumps(ev.get("conversations", []), ensure_ascii=False)[:14000]
        trace = json.dumps(ev.get("operations", []), ensure_ascii=False)[:4000]
        probes = [a for a in (lc.get("answer") or []) if a.get("question") and a.get("expected_answer")]
        print(f"[{fi}/{len(files)}] {name:24} op={op:14} probes={len(probes)}", flush=True)

        stores = {}
        if "mnemo" in arms:
            stores["mnemo"] = build_mnemo(lc, True)
        if "naive" in arms:
            stores["naive"] = build_mnemo(lc, False)
        if "session_rag" in arms:
            stores["session_rag"] = build_bm25_sessions(lc)
        if "mem0" in arms:
            sid = name.rsplit(".", 1)[0]
            t_m0 = time.time()
            stores["mem0"] = build_mem0(lc, sid)
            print(f"     mem0 ingested {len(sessions_of(lc))} sessions in {time.time()-t_m0:.0f}s "
                  f"(its LLM extraction runs here; mnemo's ingest is free)", flush=True)
        if "mnemo" in stores:
            keyed_n = sum(1 for r in stores["mnemo"].items if r.get("key"))
            sup_n = sum(1 for r in stores["mnemo"].items if r.get("status") == "superseded")
            print(f"     mnemo store: {len(stores['mnemo'].items)} records, {keyed_n} keyed, "
                  f"{sup_n} superseded by the integrity layer", flush=True)

        def one(task):
            arm, a = task
            q, exp = a["question"], a["expected_answer"]
            if arm == "no_context":
                ctx = "(no memories available)"
            elif arm == "session_rag":
                ctx = recall_bm25(stores[arm], q)
            elif arm == "mem0":
                ctx = recall_mem0(stores[arm], q, name.rsplit(".", 1)[0])
            else:
                ctx = recall_mnemo(stores[arm], q, arm == "mnemo")
            resp = call(ANSWER_BASE, ANSWER_MODEL, ANSWER_KEY,
                        ANSWER_PROMPT.format(context=ctx[:12000], question=q), 1200)
            if not resp:
                return None
            v = judge(conv, trace, q, exp, resp)
            if v is None:
                return None
            return {"file": name, "op": op, "arm": arm,
                    "category": a.get("evaluation_category", ""), **v}

        tasks = [(arm, a) for arm in arms for a in probes]
        done = 0
        t_probe = time.time()
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
            for r in ex.map(one, tasks):
                done += 1
                if r:
                    results.append(r)
                if done % 10 == 0 or done == len(tasks):
                    print(f"     probes {done}/{len(tasks)}  ({time.time()-t_probe:.0f}s, "
                          f"{len(results)} scored)", flush=True)
        (HERE / f"pilot_raw_{TAG}.json").write_text(json.dumps(results, indent=1, ensure_ascii=False),
                                             encoding="utf-8")
    return results


def summarize(rows):
    import collections
    by = collections.defaultdict(list)
    for r in rows:
        by[r["arm"]].append(r)
    print("\n" + "=" * 74)
    print(f"{'arm':13} {'n':>4} {'accuracy':>9} {'stale(Upd)':>11} {'leak(Fgt)':>10} {'overFgt':>8}")
    out = {}
    for arm, rs in by.items():
        acc = sum(r.get("answer_score") == 1 for r in rs) / len(rs)
        def rate(f, opsub):
            v = [r[f] for r in rs if opsub in r["op"] and r.get(f) in (0, 1)]
            return (sum(v) / len(v), len(v)) if v else (None, 0)
        st, stn = rate("stale_value", "update")
        lk, lkn = rate("leakage", "forget")
        of, ofn = rate("over_forget", "forget")
        fmt = lambda x: f"{x:.3f}" if x is not None else "  -  "
        print(f"{arm:13} {len(rs):>4} {acc:9.3f} {fmt(st):>11} {fmt(lk):>10} {fmt(of):>8}")
        out[arm] = {"n": len(rs), "accuracy": round(acc, 3),
                    "stale_value": st, "stale_n": stn,
                    "leakage": lk, "leak_n": lkn, "over_forget": of, "over_forget_n": ofn}
    (HERE / f"pilot_summary_{TAG}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    n_files = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    TAG = sys.argv[3] if len(sys.argv) > 3 else "run"
    globals()["TAG"] = TAG
    arms = sys.argv[2].split(",") if len(sys.argv) > 2 else ["mnemo", "naive", "no_context", "session_rag"]
    names = sorted(p.name for p in (HERE / "data_lc").glob("*.json"))
    # stratify: round-robin across operation types
    import collections
    byop = collections.defaultdict(list)
    for n in names:
        byop[n.rsplit(".", 1)[0].split("_", 1)[1]].append(n)
    picked, i = [], 0
    while len(picked) < n_files and any(len(v) > i for v in byop.values()):
        for v in byop.values():
            if len(v) > i and len(picked) < n_files:
                picked.append(v[i])
        i += 1
    print(f"arms={arms}  files={picked}\n")
    t0 = time.time()
    rows = run(picked, arms)
    summarize(rows)
    print(f"\nelapsed {time.time()-t0:.0f}s   rows={len(rows)}   raw -> pilot_raw.json")
