"""Positive control for the mem0 arm BEFORE any mem0 number is recorded.

The smoke run returned accuracy 0.000 with 'Error parsing extraction response' lines in the log.
A zero produced by OUR model choice (deepseek-v4-flash emitting malformed JSON into mem0's
extraction prompt) is a strawman, not a measurement, and must never reach a comparison table.

This ingests only the EVIDENCE sessions of one scenario — the smallest input on which a working
memory system must succeed — and reports, per candidate LLM:
  - how many memories mem0 actually stored
  - how many extraction calls failed to parse
  - whether a search for the target fact returns the current value

Gate: an LLM qualifies for the mem0 arm only if it stores memories AND retrieves the target fact.
If no candidate passes, the mem0 arm is reported as NOT RUN, not as a score of zero.
"""
import io
import json
import contextlib
import os
import pathlib
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = pathlib.Path(__file__).resolve().parent
import pilot  # noqa: E402

SCENARIO = "A01_update.json"
CANDIDATES = [("https://ollama.com/v1", "deepseek-v4-flash"),
              ("https://ollama.com/v1", "glm-5.2")]


def evidence_sessions(lc):
    out = []
    for seg in lc.get("conversations", []):
        if not seg.get("evidence_inserted"):
            continue
        txt = "\n".join(f"{t.get('role')}: {(t.get('content') or '').strip()}"
                        for t in seg.get("dialogue", []) if (t.get("content") or "").strip())
        if txt:
            out.append(txt)
    return out


def try_llm(base, model, sessions, probe_q):
    from mem0 import Memory
    os.environ["OPENAI_API_KEY"] = pilot.ANSWER_KEY
    os.environ["OPENAI_BASE_URL"] = base
    d = tempfile.mkdtemp(prefix="memops_pc_")
    m = Memory.from_config({
        "llm": {"provider": "openai", "config": {"model": model, "temperature": 0,
                                                 "openai_base_url": base, "api_key": pilot.ANSWER_KEY}},
        "embedder": {"provider": "ollama", "config": {"model": "nomic-embed-text",
                                                      "ollama_base_url": "http://localhost:11434"}},
        "vector_store": {"provider": "qdrant", "config": {"path": os.path.join(d, "qd"),
                                                          "embedding_model_dims": 768, "on_disk": True}},
        "history_db_path": os.path.join(d, "history.db")})
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        for s in sessions:
            try:
                m.add(s[:6000], user_id="pc")
            except Exception:
                pass
    parse_errors = buf.getvalue().count("Error parsing extraction response")
    try:
        r = m.search(probe_q, filters={"user_id": "pc"}, limit=20) or {}
        hits = r.get("results") if isinstance(r, dict) else r
    except Exception as e:
        hits = []
        print("   search raised:", e)
    texts = [h.get("memory", "") for h in (hits or [])]
    return parse_errors, texts


def main():
    lc = json.loads((HERE / "data_lc" / SCENARIO).read_text(encoding="utf-8"))
    ev = json.loads((HERE / "data" / SCENARIO).read_text(encoding="utf-8"))
    sess = evidence_sessions(lc)
    target = ev.get("target_fact")
    current = [op.get("new_value") for op in ev.get("operations", [])
               if op.get("validity") == "confirmed" and op.get("new_value")]
    current = current[-1] if current else ""
    q = f"What is my {target}?"
    print(f"scenario={SCENARIO}  evidence sessions={len(sess)}  target={target!r}  current={current!r}\n")
    verdict = {}
    for base, model in CANDIDATES:
        print(f"--- {model} @ {base}")
        errs, texts = try_llm(base, model, sess, q)
        found = any(current.lower() in t.lower() for t in texts)
        print(f"    parse_errors={errs}  memories_returned={len(texts)}  current_value_retrieved={found}")
        for t in texts[:5]:
            print("      *", t[:110])
        verdict[model] = {"parse_errors": errs, "hits": len(texts), "current_retrieved": found,
                          "passes": bool(texts) and found}
        print()
    (HERE / "mem0_positive_control.json").write_text(json.dumps(verdict, indent=1), encoding="utf-8")
    ok = [k for k, v in verdict.items() if v["passes"]]
    print("GATE:", f"PASS with {ok}" if ok else "NO CANDIDATE PASSES -> mem0 arm reported NOT RUN")


if __name__ == "__main__":
    main()
