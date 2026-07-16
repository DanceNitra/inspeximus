"""cost-per-op: a SHARED INSTRUMENT (not a scoreboard) for the write/read cost of an agent-memory system,
decomposed by phase so the EXTRACTION BOUNDARY is explicit.

Vectorize's manifesto elevated cost-per-operation to a first-class axis. A fair cost comparison of memory
systems has a trap: some systems (mem0, Zep, Letta) run an LLM at add() to EXTRACT structured facts from raw
text; others (mnemo) take an already-structured (key, value) write and do the extraction UPSTREAM in the
caller. Comparing "cost per add" without naming where extraction is paid rewards the system that skips it and
hides the cost in the caller. So this tool reports, per phase:

  EXTRACT  — raw utterance -> structured fact(s). 'in-store' if the system's add() does it (LLM calls counted
             here); 'upstream/caller' if the store assumes structured input (the cost is real but paid before
             the store sees it, and this tool flags it rather than scoring it 0).
  RETAIN   — persist the fact.
  RECALL   — retrieve for a query.

Metric is LLM-CALLS and TOKENS per phase (the backend-independent, un-gameable quantities). It deliberately
does NOT headline dollars (a repricing across tokenizers is a modeled estimate) or latency (a local op vs a
network round-trip is not a like-for-like number) — report those only with their assumptions, downstream.

Add a system by writing a CostAdapter (below). Runnable on any OpenAI-compatible backend; mnemo runs free/local.
"""
import os, sys, json, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── universal LLM/embedding call meter: patch the OpenAI client so any adapter's calls are counted ──────────
TOK = {"chat_calls": 0, "chat_in": 0, "chat_out": 0, "emb_calls": 0}
try:
    import openai
    _oc = openai.resources.chat.completions.Completions.create
    _oe = openai.resources.embeddings.Embeddings.create
    def _c(self, *a, **k):
        r = _oc(self, *a, **k); u = getattr(r, "usage", None)
        TOK["chat_calls"] += 1
        if u: TOK["chat_in"] += u.prompt_tokens; TOK["chat_out"] += u.completion_tokens
        return r
    def _e(self, *a, **k):
        r = _oe(self, *a, **k); TOK["emb_calls"] += 1
        return r
    openai.resources.chat.completions.Completions.create = _c
    openai.resources.embeddings.Embeddings.create = _e
except Exception:
    pass


def _snap(): return dict(TOK)
def _delta(a, b): return {k: b[k] - a[k] for k in a}
def _calls(d): return d["chat_calls"] + d["emb_calls"]


class CostAdapter:
    """4 hooks. Return where EXTRACT is paid so the boundary is explicit, not scored 0."""
    name = "abstract"
    extract_site = "in-store"           # or "upstream/caller"
    def reset(self, case): ...          # fresh isolated store for one case id
    def extract(self, text): ...        # raw utterance -> structured fact (may be a no-op for structured-input stores)
    def retain(self, fact): ...         # persist
    def recall(self, query): ...        # retrieve


class MnemoAdapter(CostAdapter):
    name = "mnemo"
    extract_site = "upstream/caller"    # mnemo takes an already-structured (key, value) write; extraction is the caller's
    def __init__(self):
        from mnemo import Mnemo
        self._M = Mnemo
    def reset(self, case):
        self.m = self._M(path=None)
    def extract(self, text):
        return text                     # no-op: the caller already holds (key, value); zero LLM in the store
    def retain(self, fact):
        self.m.remember(fact["text"], key=fact["key"], object=fact["object"])
    def recall(self, query):
        return self.m.recall(query, k=5)


class Mem0Adapter(CostAdapter):
    name = "mem0"
    extract_site = "in-store"           # mem0's add() runs an LLM extractor + embedder
    def __init__(self):
        import tempfile
        from mem0 import Memory
        key = os.environ.get("AGORA_API_KEY", "")
        self._vdir = tempfile.mkdtemp(prefix="cpo_")
        self.mem = Memory.from_config({
            "llm": {"provider": "openai", "config": {"model": os.environ.get("MEM0_LLM", "deepseek-v4-flash"),
                    "temperature": 0.1, "openai_base_url": os.environ.get("MEM0_BASE", "https://ollama.com/v1"),
                    "api_key": key}},
            "embedder": {"provider": "ollama", "config": {"model": "nomic-embed-text",
                         "ollama_base_url": "http://localhost:11434", "embedding_dims": 768}},
            "vector_store": {"provider": "qdrant", "config": {"path": self._vdir, "collection_name": "cpo",
                             "embedding_model_dims": 768}}})
    def reset(self, case):
        self._uid = f"case{case}"
    def extract(self, text):
        return text                     # mem0 does extraction inside add(); measured in RETAIN below
    def retain(self, fact):
        self.mem.add(fact["text"], user_id=self._uid)
    def recall(self, query):
        return self.mem.search(query, filters={"user_id": self._uid}, top_k=5)


ADAPTERS = {"mnemo": MnemoAdapter, "mem0": Mem0Adapter}
FIXTURE = [("cache region", "osaka"), ("primary shard", "delta7"), ("build target", "arm64"),
           ("default currency", "forint"), ("route profile", "coastal"), ("api tier", "bronze"),
           ("index locale", "tallinn"), ("worker pool", "amber"), ("log sink", "vault3"), ("retry policy", "linear")]


def measure(ad, n):
    per = {"extract": [], "retain": [], "recall": []}
    for i, (subj, val) in enumerate(FIXTURE[:n]):
        ad.reset(i)
        text = f"the {subj} is {val}"
        s = _snap(); fact = ad.extract(text); per["extract"].append(_delta(s, _snap()))
        fact = {"text": text, "key": subj, "object": val} if not isinstance(fact, dict) else fact
        s = _snap(); ad.retain(fact); per["retain"].append(_delta(s, _snap()))
        s = _snap(); ad.recall(subj); per["recall"].append(_delta(s, _snap()))
    def agg(phase):
        rows = per[phase]; nn = max(1, len(rows))
        return {"llm_calls_per_op": round(sum(_calls(r) for r in rows) / nn, 2),
                "tokens_per_op": round(sum(r["chat_in"] + r["chat_out"] for r in rows) / nn, 1)}
    return {"extract": {**agg("extract"), "site": ad.extract_site}, "retain": agg("retain"), "recall": agg("recall")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", default="mnemo")
    ap.add_argument("--n", type=int, default=10)
    a = ap.parse_args()
    if os.path.exists(r"C:/Users/Danculus/agora/server/.env"):
        for line in open(r"C:/Users/Danculus/agora/server/.env", encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    out = {}
    for s in [x.strip() for x in a.systems.split(",") if x.strip()]:
        if s not in ADAPTERS:
            print(f"no adapter for '{s}' — write a CostAdapter and PR it."); continue
        print(f"measuring {s} ...", flush=True)
        r = measure(ADAPTERS[s](), a.n); out[s] = r
        print(f"  {s}: extract={r['extract']['llm_calls_per_op']} calls [{r['extract']['site']}] | "
              f"retain={r['retain']['llm_calls_per_op']} calls/{r['retain']['tokens_per_op']} tok | "
              f"recall={r['recall']['llm_calls_per_op']} calls/{r['recall']['tokens_per_op']} tok")
    json.dump({"metric": "LLM-calls & tokens per op, by phase; extract 'site' shows where extraction is paid",
               "n": a.n, "systems": out},
              open(os.path.join(os.path.dirname(__file__), "cost_per_op_result.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
