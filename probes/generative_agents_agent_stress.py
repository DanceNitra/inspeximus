"""Live-agent end-to-end arm for the Generative Agents replication: does GA's context contamination
actually make the AGENT answer wrong? Same LLM + same prompt (given every fair advantage, incl. an explicit
'use the most recent / "is now" supersedes' instruction); ONLY the retrieved context differs — GA's
top-k (stale + fresh) vs inspeximus's supersession-cleaned top-k (fresh only). Writes generative_agents_agent_result.json."""
import os, sys, json, time, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------------------------------------
# A PROBE IN A PUBLIC REPOSITORY MUST NOT READ ITS AUTHOR'S HOME DIRECTORY. Found while fixing
# DanceNitra/inspeximus#1, one file over from the one that was reported: the process environment
# comes first, a dotenv is a FALLBACK, missing files are skipped, and nothing is overwritten.
# The unconditional form this replaces crashed for every reader who was not the author.
# ---------------------------------------------------------------------------------------------
def _load_env_fallback():
    import os as _os
    _here = _os.path.dirname(_os.path.abspath(__file__))
    for _cand in (_os.environ.get("AGORA_ENV_FILE", ""),
                  _os.path.join(_here, "..", ".env"),
                  _os.path.join(_here, "..", "server", ".env"),
                  _os.path.join(_os.getcwd(), "server", ".env")):
        if not _cand or not _os.path.exists(_cand):
            continue
        try:
            with open(_cand, encoding="utf-8", errors="ignore") as _fh:
                for _line in _fh:
                    _line = _line.strip()
                    if _line and not _line.startswith("#") and "=" in _line:
                        _k, _v = _line.split("=", 1)
                        _os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
        except OSError:
            continue
        return _cand
    return None


_load_env_fallback()
sys.path.insert(0, os.path.dirname(__file__))
import generative_agents_retrieval_stress as G

BASE = os.environ["AGORA_API_BASE_URL"].rstrip("/")
KEY = os.environ["AGORA_API_KEY"]
MODEL = os.environ.get("AGORA_LLM_MODEL_CHEAP", "deepseek-v4-flash")
OUT = Path(__file__).with_name("generative_agents_agent_result.json")
K = 8
SCALE = 1000
SEEDS = 2

SYS = ("You answer using ONLY the provided facts. If two facts about the same thing conflict, use the MOST "
       "RECENT one — a fact phrased 'X is now Y' supersedes an earlier 'X is Y'. Reply with ONLY the current "
       "value, nothing else.")


def llm(context_texts, subj, _tries=4):
    usr = "Facts:\n" + "\n".join(context_texts) + f"\n\nQuestion: what is {subj}?\nAnswer:"
    body = json.dumps({"model": MODEL, "max_tokens": 512, "temperature": 0.0,
                       "messages": [{"role": "system", "content": SYS},
                                    {"role": "user", "content": usr}]}).encode()
    req = urllib.request.Request(BASE + "/chat/completions", data=body, headers={
        "Authorization": "Bearer " + KEY, "content-type": "application/json"})
    for a in range(_tries):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=90))
            return r["choices"][0]["message"]["content"] or ""
        except Exception as e:
            if a == _tries - 1:
                return f"__ERR__{e}"
            time.sleep(2 * (a + 1))


def correct(ans, new, old):
    a = ans.lower()
    return int(new.lower() in a and old.lower() not in a)   # says the fresh value, not the stale


def eval_cond(embed, contra, scale):
    import random
    hits = {"ga": 0, "inspeximus": 0, "n": 0, "errs": 0}
    for s in range(SEEDS):
        random.seed(20260716 + s)
        ga, st, updated = G.build(embed, n_facts=20, contra_frac=contra, scale_distractors=scale)
        jobs = []
        for subj, key, new, old in updated:
            qv = embed(f"what is {subj}?")
            ga_ctx = [m["text"] for m in G.ga_topk(ga, qv, K)]
            mn_ctx = [h["text"] for h in st.recall(f"what is {subj}?", k=K, mode="semantic")]
            jobs.append(("ga", ga_ctx, subj, new, old))
            jobs.append(("inspeximus", mn_ctx, subj, new, old))

        def work(job):
            arm, ctx, subj, new, old = job
            out = llm(ctx, subj)
            return arm, correct(out, new, old), out.startswith("__ERR__")

        with ThreadPoolExecutor(max_workers=8) as ex:
            for arm, ok, err in ex.map(work, jobs):
                hits[arm] += ok
                hits["errs"] += err
        hits["n"] += len(updated)
    n = hits["n"]
    return {"contra": contra, "scale": scale, "n_queries": n, "errors": hits["errs"],
            "ga_agent_acc": round(hits["ga"] / n, 3), "inspeximus_agent_acc": round(hits["inspeximus"] / n, 3)}


def main():
    embed = G.load_embed()
    warm = [f"{s} is {v}" for s, v in zip(G.SUBJECTS, G.VALUES)] + \
           [f"{s} is now {v}" for s, v in zip(G.SUBJECTS, G.NEW)] + \
           [f"what is {s}?" for s in G.SUBJECTS] + \
           [f"{G.FILLER[i % len(G.FILLER)]} (note {i})" for i in range(SCALE)]
    embed.warm(warm)
    res = []
    for contra in (0.2, 0.5, 0.9):
        r = eval_cond(embed, contra, SCALE)
        res.append(r)
        print(f"contra={contra:.0%}: AGENT acc  GA={r['ga_agent_acc']:.0%}  inspeximus={r['inspeximus_agent_acc']:.0%}  "
              f"(n={r['n_queries']}, errs={r['errors']})")
    OUT.write_text(json.dumps({"ok": True, "model": MODEL, "scale": SCALE, "k": K, "rows": res}, indent=1),
                   encoding="utf-8")


try:
    main()
except Exception as e:
    import traceback
    OUT.write_text(json.dumps({"ok": False, "error": repr(e), "tb": traceback.format_exc()}, indent=1),
                   encoding="utf-8")
