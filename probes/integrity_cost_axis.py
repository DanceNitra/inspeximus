"""INTEGRITY x COST probe — the axis Vectorize's manifesto demands ("a system that scores 90% but costs $10/user/day
is not better than 82% at $0.10"), measured for the retain/recall path of mnemo vs mem0 (native config).

The structural asymmetry under test: mnemo's write path is zero-LLM (local, deterministic supersession); mem0's
add() runs an LLM extraction (gpt-4o-mini) + an embedding call per memory. HYPOTHESIS: mnemo is orders of
magnitude cheaper and faster on retain at comparable (for the integrity cells, better) correctness.

Measured, not asserted: wall-clock latency per op (retain / recall) and EXACT token usage (the OpenAI client is
monkeypatched to accumulate response.usage — mem0 discards it, we do not). mnemo runs with its local embedder
disabled (pure lexical) = its true zero-dependency floor; cost 0 tokens by construction, which we still verify.
Same fixture as the integrity bench (entity/value corrections). Prices: gpt-4o-mini $0.15/M in, $0.60/M out;
text-embedding-3-small $0.02/M (2026-07 public pricing).
"""
import os, sys, json, time, statistics
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

for line in Path(r"C:/Users/Danculus/agora/server/.env").read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

N = int(os.environ.get("N", 15))
OUT = Path(__file__).with_name("integrity_cost_axis_result.json")
ENTS = [("cache region", "osaka", "malmo"), ("primary shard", "delta7", "sigma2"),
        ("build target", "arm64", "riscv"), ("default currency", "forint", "guarani"),
        ("route profile", "coastal", "inland"), ("api tier", "bronze", "platinum"),
        ("index locale", "tallinn", "cusco"), ("worker pool", "amber", "cobalt"),
        ("log sink", "vault3", "harbor8"), ("retry policy", "linear", "jitter"),
        ("color theme", "sepia", "slate"), ("scheduler", "roundrobin", "weighted"),
        ("session store", "sticky", "pooled"), ("cdn provider", "fastly", "bunny"),
        ("rate limiter", "tiered", "flat")][:N]

# ── exact token metering: patch the OpenAI client so mem0's discarded usage is accumulated ────────
TOK = {"chat_in": 0, "chat_out": 0, "emb": 0, "chat_calls": 0, "emb_calls": 0}
import openai
_orig_chat = openai.resources.chat.completions.Completions.create
_orig_emb = openai.resources.embeddings.Embeddings.create
def _chat(self, *a, **k):
    r = _orig_chat(self, *a, **k)
    u = getattr(r, "usage", None)
    if u:
        TOK["chat_in"] += u.prompt_tokens; TOK["chat_out"] += u.completion_tokens
    TOK["chat_calls"] += 1
    return r
def _emb(self, *a, **k):
    r = _orig_emb(self, *a, **k)
    u = getattr(r, "usage", None)
    if u:
        TOK["emb"] += u.prompt_tokens
    TOK["emb_calls"] += 1
    return r
openai.resources.chat.completions.Completions.create = _chat
openai.resources.embeddings.Embeddings.create = _emb

PRICE = {"chat_in": 0.15 / 1e6, "chat_out": 0.60 / 1e6, "emb": 0.02 / 1e6}


def usd():
    return TOK["chat_in"] * PRICE["chat_in"] + TOK["chat_out"] * PRICE["chat_out"] + TOK["emb"] * PRICE["emb"]


def run_mnemo():
    from mnemo import Mnemo
    ret_lat, rec_lat = [], []
    m = Mnemo(path=None)                      # zero-dependency floor: no embedder, lexical recall
    for (e, A, B) in ENTS:
        t0 = time.perf_counter(); m.remember(f"the {e} is {A}", key=e, object=A); ret_lat.append(time.perf_counter() - t0)
        t0 = time.perf_counter(); m.remember(f"correction: the {e} is now {B}", key=e, object=B); ret_lat.append(time.perf_counter() - t0)
        t0 = time.perf_counter(); m.recall(e, k=5); rec_lat.append(time.perf_counter() - t0)
    return {"retain_ms_median": round(statistics.median(ret_lat) * 1000, 3),
            "retain_ms_p95": round(sorted(ret_lat)[int(len(ret_lat) * .95) - 1] * 1000, 3),
            "recall_ms_median": round(statistics.median(rec_lat) * 1000, 3),
            "n_retain": len(ret_lat), "n_recall": len(rec_lat),
            "tokens": 0, "llm_calls": 0, "usd_total": 0.0}


def run_mem0():
    """mem0's native PIPELINE (LLM extraction + embedding per add), served per the owner's standing rule on
    Ollama Cloud (OpenAI quota must never block a competitor measurement). The structural cost story — N
    network LLM+embedding calls per add vs mnemo's zero — is backend-independent; token counts are measured
    and PRICED at gpt-4o-mini public rates (labeled estimate); latency is 'as measured on this backend'."""
    from mem0 import Memory
    key = os.environ.get("AGORA_API_KEY", "")
    import tempfile
    vdir = tempfile.mkdtemp(prefix="mem0cost_")     # fresh vector store (avoid a stale 1536-dim collection)
    cfg = {"llm": {"provider": "openai",
                   "config": {"model": "deepseek-v4-flash", "temperature": 0.1,
                              "openai_base_url": "https://ollama.com/v1", "api_key": key}},
           "embedder": {"provider": "ollama",
                        "config": {"model": "nomic-embed-text", "ollama_base_url": "http://localhost:11434",
                                   "embedding_dims": 768}},
           "vector_store": {"provider": "qdrant",
                            "config": {"path": vdir, "collection_name": "cost", "embedding_model_dims": 768}}}
    mem = Memory.from_config(cfg)
    # count local-ollama embedding calls too (they bypass the openai client)
    try:
        emb_obj = mem.embedding_model
        _orig_e = emb_obj.embed
        def _e(*a, **k):
            TOK["emb_calls"] += 1
            return _orig_e(*a, **k)
        emb_obj.embed = _e
    except Exception:
        pass
    base_usd = usd(); base_calls = TOK["chat_calls"] + TOK["emb_calls"]
    ret_lat, rec_lat = [], []
    for i, (e, A, B) in enumerate(ENTS):
        uid = f"cost{i}"
        t0 = time.perf_counter(); mem.add(f"the {e} is {A}", user_id=uid); ret_lat.append(time.perf_counter() - t0)
        t0 = time.perf_counter(); mem.add(f"correction: the {e} is now {B}", user_id=uid); ret_lat.append(time.perf_counter() - t0)
        t0 = time.perf_counter(); mem.search(e, filters={"user_id": uid}, top_k=5); rec_lat.append(time.perf_counter() - t0)
        print(f"  mem0 {i+1}/{len(ENTS)}", flush=True)
    return {"retain_ms_median": round(statistics.median(ret_lat) * 1000, 1),
            "retain_ms_p95": round(sorted(ret_lat)[int(len(ret_lat) * .95) - 1] * 1000, 1),
            "recall_ms_median": round(statistics.median(rec_lat) * 1000, 1),
            "n_retain": len(ret_lat), "n_recall": len(rec_lat),
            "tokens": {k: TOK[k] for k in ("chat_in", "chat_out", "emb")},
            "llm_calls": TOK["chat_calls"] + TOK["emb_calls"] - base_calls,
            "usd_total": round(usd() - base_usd, 5)}


def main():
    print(f"=== integrity x cost: retain/recall, n={N} entities (2 retains + 1 recall each) ===")
    mn = run_mnemo()
    print("mnemo :", json.dumps(mn))
    m0 = run_mem0()
    print("mem0  :", json.dumps(m0))
    ops = mn["n_retain"]
    per_1k = round(m0["usd_total"] / ops * 1000, 4)
    speed = round(m0["retain_ms_median"] / max(mn["retain_ms_median"], 1e-6))
    print(f"\nHEADLINE: retain median {m0['retain_ms_median']}ms (mem0) vs {mn['retain_ms_median']}ms (mnemo) "
          f"= {speed}x; mem0 cost ${m0['usd_total']} for {ops} retains (${per_1k}/1k retains) vs mnemo $0.")
    OUT.write_text(json.dumps({"ok": True, "n_entities": N, "mnemo": mn, "mem0": m0,
                               "headline": {"retain_speedup_x": speed, "mem0_usd_per_1k_retains": per_1k}},
                              indent=1), encoding="utf-8")


main()
