"""Iterative multi-hop retrieval + supersession on MemoryAgentBench CR. The single-shot ceiling is the
retrieval hit-rate (answer-fact entities aren't in the question). Iterative: recall -> LLM names the next
entity -> recall again (2 hops) -> answer. Compares base_full vs mnemo single-shot vs mnemo iterative,
their metric (substring_exact_match). Writes _cr_iter.json."""
import os, sys, json, time, string, re, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
from memoryagentbench_cr import fact_lines, consolidate_with_mnemo, _SYS
from mnemo import Mnemo
from huggingface_hub import hf_hub_download
import pandas as pd

# LLM backend: any OpenAI-compatible /chat/completions endpoint. Configure via env:
#   LLM_BASE_URL (e.g. https://api.openai.com/v1), LLM_API_KEY, LLM_MODEL.
# Fallback (our setup): load Ollama Cloud creds from an agora server/.env if LLM_* are unset.
if not os.environ.get("LLM_BASE_URL"):
    _envf = os.environ.get("AGORA_ENV_FILE", r"C:/Users/Danculus/agora/server/.env")
    if os.path.exists(_envf):
        for line in Path(_envf).read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
BASE = (os.environ.get("LLM_BASE_URL") or os.environ["AGORA_API_BASE_URL"]).rstrip("/")
KEY = os.environ.get("LLM_API_KEY") or os.environ["AGORA_API_KEY"]
MODEL = os.environ.get("LLM_MODEL") or os.environ.get("AGORA_LLM_MODEL_CHEAP", "deepseek-v4-flash")
OUT = Path(__file__).with_name("results_cr_sweep.json")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 50
ROWS = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["0", "1", "2", "3"])]
K = 15
HOPS = 2
FULLCTX_CHAR_CAP = 450000        # ~112k tokens; above this, full-context does not fit -> N/A

_FSYS = ("You are decomposing a multi-hop question into retrieval steps. Given the question and the facts "
         "retrieved so far, name the SINGLE most useful next thing to look up to make progress (an entity or "
         "a relation). Reply with ONLY a short search phrase, no explanation.")


def _post(messages, mt=512):
    body = json.dumps({"model": MODEL, "max_tokens": mt, "temperature": 0.0, "messages": messages}).encode()
    req = urllib.request.Request(BASE + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + KEY, "content-type": "application/json"})
    for a in range(4):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=120))
            return r["choices"][0]["message"]["content"] or ""
        except Exception as e:
            if a == 3:
                return f"__ERR__{e}"
            time.sleep(2 * (a + 1))


def answer(facts, q):
    return _post([{"role": "system", "content": _SYS},
                  {"role": "user", "content": "Facts:\n" + "\n".join(facts) + f"\n\nQuestion: {q}\nAnswer:"}])


def followup(facts, q):
    fs = "\n".join(list(facts)[:40])
    return _post([{"role": "system", "content": _FSYS},
                  {"role": "user", "content": f"Question: {q}\nFacts so far:\n{fs}\n\nNext to look up:"}], mt=256)


def iterative(store, q):
    facts = {h["text"] for h in store.recall(q, k=K, mode="lexical")}
    for _ in range(HOPS):
        fq = followup(facts, q)
        if fq and not fq.startswith("__ERR__"):
            facts |= {h["text"] for h in store.recall(fq, k=K, mode="lexical")}
    return answer(sorted(facts), q)


def norm(t):
    t = t.lower(); t = ''.join(c for c in t if c not in string.punctuation)
    t = re.sub(r'\b(a|an|the)\b', ' ', t); return ' '.join(t.split())


def em(pred, golds):
    p = norm(pred); return int(any(norm(g) and norm(g) in p for g in golds))


def eval_row(df, ridx):
    row = df.iloc[ridx]; lines = fact_lines(row["context"]); cons, st = consolidate_with_mnemo(lines)
    fits = len("\n".join(lines)) <= FULLCTX_CHAR_CAP           # can full-context even fit the model window?
    qs = list(row["questions"])[:N]
    golds = [list(g) if hasattr(g, "__len__") and not isinstance(g, str) else [g] for g in list(row["answers"])[:N]]

    def one(qg):
        q, gl = qg
        base = em(answer(lines, q), gl) if fits else None
        single = em(answer([h["text"] for h in st.recall(q, k=K, mode="lexical")], q), gl)
        itr = em(iterative(st, q), gl)
        return base, single, itr

    with ThreadPoolExecutor(max_workers=6) as ex:
        rs = list(ex.map(one, zip(qs, golds)))
    s = sum(r[1] for r in rs); it = sum(r[2] for r in rs)
    out = {"row": ridx, "facts": len(lines), "n": N,
           "base_full": (round(sum(r[0] for r in rs) / N, 3) if fits else "N/A (context exceeds window)"),
           "mnemo_single": round(s / N, 3), "mnemo_iterative": round(it / N, 3)}
    return out


def main():
    p = hf_hub_download('ai-hyz/MemoryAgentBench', 'data/Conflict_Resolution-00000-of-00001.parquet', repo_type='dataset')
    df = pd.read_parquet(p)
    res = [eval_row(df, r) for r in ROWS]
    OUT.write_text(json.dumps({"ok": True, "model": MODEL, "hops": HOPS, "k": K, "rows": res}, indent=2), encoding="utf-8")


try:
    main()
except Exception as e:
    import traceback
    OUT.write_text(json.dumps({"ok": False, "error": repr(e), "tb": traceback.format_exc()}, indent=2), encoding="utf-8")
