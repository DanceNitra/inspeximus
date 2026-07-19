"""mnemo_mcp_token_pack_probe.py — compact MCP recall + progressive disclosure.

Standard MCP/RAG context-economy practice (progressive disclosure / small-to-big retrieval), applied to mnemo's
MCP surface. Asserts:
  1. recall() returns a COMPACT projection (id/text/score/value/tags), NOT the full internal record.
  2. NO truncation by default (full text kept — so a corrected value can't be silently cut off).
  2b. truncation is OPT-IN via snippet_chars (then flagged `truncated`).
  3. recall(full=True) returns complete records.
  4. get(id) fetches the full record.
  5. neighbors(id, k) returns related memories (excludes self), bounded by k.
  6. k is hard-capped.
  7. token_report() gives a deterministic (no-LLM) COMPACT-vs-FULL-for-same-k payload estimate — the honest
     apples-to-apples baseline, NOT a whole-store strawman and NOT a measured token saving.

No LLM, zero extra deps beyond the MCP SDK.
"""
import os, json, tempfile, sys

os.environ["MNEMO_PATH"] = tempfile.mktemp(suffix=".json")
try:
    import mnemo.mnemo_mcp as M
except SystemExit:
    print("SKIP: MCP SDK not installed (pip install 'mcp[cli]')"); sys.exit(0)

FAILS = []
def check(name, cond):
    print(f"  [{'OK ' if cond else 'XXX'}] {name}")
    if not cond:
        FAILS.append(name)

mem = M._MEM
long_text = "Frankfurt was the stored region for the account. " * 20
mem.remember(long_text, key="region", object="frankfurt", tags=["geo"], value=2.0)
mem.remember("python is a programming language", tags=["lang"])
mem.remember("python has data-science libraries like numpy and pandas", tags=["lang"])
mem.remember("the deploy channel is blue-9", key="deploy", object="blue-9", tags=["ops"])

c = M.recall("what region", k=3)
check("1 compact projection keys", set(c[0].keys()) <= {"id", "text", "score", "value", "tags", "truncated"})
check("2 NO truncation by default (protects corrected value from being cut)",
      c[0].get("truncated") is None and c[0]["text"] == long_text)
ct = M.recall("what region", k=1, snippet_chars=50)
check("2b truncation is OPT-IN via snippet_chars", ct[0].get("truncated") is True and ct[0]["text"].endswith("…"))

f = M.recall("what region", k=1, full=True)
check("3 full=True returns richer records", len(f[0].keys()) > len(c[0].keys()))
check("3b compact is smaller than full (drops internal fields)", len(json.dumps(c[:1])) < len(json.dumps(f[:1], default=str)))

gid = c[0]["id"]
g = M.get(gid)
check("4 get(id) returns full untruncated text", len(g.get("text", "")) == len(long_text))
check("4b get(unknown) -> {}", M.get("nope") == {})

hits = M.recall("python programming", k=1)
nb = M.neighbors(hits[0]["id"], k=3)
check("5 neighbors finds related, excludes self", len(nb) >= 1 and all(x["id"] != hits[0]["id"] for x in nb))
check("5b neighbors(unknown) -> []", M.neighbors("nope") == [])

check("6 k hard-capped", len(M.recall("x", k=9999)) <= M._MAX_K)

tr = M.token_report("what region", k=2)
check("7 token_report honest same-k baseline (compact<=full, no whole-store strawman)",
      tr["full_records_tokens_est"] >= tr["compact_records_tokens_est"] >= 1
      and "SAME k" in tr["baseline"] and tr.get("compact_fraction") is not None)

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}  ({7 - len(set(FAILS))}/7 checks groups)")
sys.exit(1 if FAILS else 0)
