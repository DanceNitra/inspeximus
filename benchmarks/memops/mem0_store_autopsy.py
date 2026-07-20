"""Where do mem0's memories go? Autopsy before any mem0 number is recorded.

Facts so far: with 3 evidence-only sessions mem0 stored 20+ on-target memories (positive control
PASSED), but with the full 50-session long-context stream it holds only 20 memories total and the
arm scores 0.000. Either (i) mem0's own update logic is deleting/merging accumulated memories as
the stream grows, which is a real and citable property, or (ii) our harness is mis-calling it.

Two harness defects already found by reading the installed source:
  - `search`/`get_all` take **top_k**, not `limit` (mem0/memory/main.py:1261, 1415) — our `limit=`
    went into **kwargs and was ignored, so defaults applied.
  - the pilot ingested `sess[:6000]`, silently truncating long sessions.

This run persists the store, replays one UPDATE scenario, and reports the ADD/UPDATE/DELETE ledger
from mem0's own history DB so the answer is mem0's behaviour, not our inference about it.
"""
import contextlib
import io
import json
import os
import pathlib
import sqlite3
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = pathlib.Path(__file__).resolve().parent
import pilot  # noqa: E402

SCENARIO = sys.argv[1] if len(sys.argv) > 1 else "A01_update.json"


def main():
    lc = json.loads((HERE / "data_lc" / SCENARIO).read_text(encoding="utf-8"))
    ev = json.loads((HERE / "data" / SCENARIO).read_text(encoding="utf-8"))
    current = [op.get("new_value") for op in ev.get("operations", [])
               if op.get("validity") == "confirmed" and op.get("new_value")]
    current = current[-1] if current else ""
    sid = SCENARIO.rsplit(".", 1)[0]
    sessions = pilot.sessions_of(lc)

    from mem0 import Memory
    os.environ["OPENAI_API_KEY"] = pilot.ANSWER_KEY
    os.environ["OPENAI_BASE_URL"] = pilot.ANSWER_BASE
    d = tempfile.mkdtemp(prefix="memops_autopsy_")
    hist = os.path.join(d, "history.db")
    m = Memory.from_config({
        "llm": {"provider": "openai", "config": {"model": os.environ.get("MEMOPS_MEM0_MODEL", "glm-5.2"),
                                                 "temperature": 0, "openai_base_url": pilot.ANSWER_BASE,
                                                 "api_key": pilot.ANSWER_KEY}},
        "embedder": {"provider": "ollama", "config": {"model": "nomic-embed-text",
                                                      "ollama_base_url": "http://localhost:11434"}},
        "vector_store": {"provider": "qdrant", "config": {"path": os.path.join(d, "qd"),
                                                          "embedding_model_dims": 768, "on_disk": True}},
        "history_db_path": hist})

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        for s in sessions:
            try:
                m.add(s, user_id=sid)          # NO truncation this time
            except Exception:
                pass
    print(f"{SCENARIO}: {len(sessions)} sessions ingested, "
          f"{buf.getvalue().count('Error parsing extraction response')} parse errors")

    allm = m.get_all(filters={"user_id": sid}, top_k=10000) or {}
    allm = allm.get("results") if isinstance(allm, dict) else allm
    print(f"memories now in store: {len(allm or [])}")

    try:
        con = sqlite3.connect(hist)
        cols = [r[1] for r in con.execute("PRAGMA table_info(history)")]
        ev_col = "event" if "event" in cols else cols[-1]
        ledger = dict(con.execute(f"SELECT {ev_col}, COUNT(*) FROM history GROUP BY 1").fetchall())
        print("mem0's own operation ledger:", ledger)
    except Exception as e:
        print("history read failed:", e)

    q = f"What is my {ev.get('target_fact')}?"
    r = m.search(q, filters={"user_id": sid}, top_k=100) or {}
    hits = r.get("results") if isinstance(r, dict) else r
    texts = [h.get("memory", "") for h in (hits or [])]
    ctx = "\n".join(texts)
    print(f"\nsearch(top_k=100) -> {len(texts)} hits, {len(ctx)} chars, "
          f"current value {current!r} present: {current.lower() in ctx.lower()}")
    for t in texts[:8]:
        print("   *", t[:120])


if __name__ == "__main__":
    main()
