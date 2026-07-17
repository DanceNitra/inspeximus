"""Control arm: ITERATIVE retrieval over the RAW (accumulate, no supersession) store, to isolate whether
supersession is the lever inside the iterative arm (vs iterative retrieval alone). Writes _control_rawiter.json."""
import os, sys, json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(__file__))
import run_cr_benchmark as R          # reuse iterative(), answer(), em(), config, _SYS
from memoryagentbench_cr import fact_lines
from mnemo import Mnemo
from huggingface_hub import hf_hub_download
import pandas as pd

N = int(sys.argv[1]) if len(sys.argv) > 1 else 50
ROWS = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["0", "1", "2", "3"])]
OUT = Path(__file__).with_name("_control_rawiter.json")


def eval_row(df, ridx):
    row = df.iloc[ridx]
    lines = fact_lines(row["context"])
    raw = Mnemo(None)                 # accumulate store: NO keyed supersession
    for ln in lines:
        raw.remember(ln)
    qs = list(row["questions"])[:N]
    golds = [list(g) if hasattr(g, "__len__") and not isinstance(g, str) else [g] for g in list(row["answers"])[:N]]

    def one(qg):
        q, gl = qg
        return R.em(R.iterative(raw, q), gl)      # iterative retrieval over the RAW store

    with ThreadPoolExecutor(max_workers=6) as ex:
        hits = sum(ex.map(one, zip(qs, golds)))
    return {"row": ridx, "facts": len(lines), "n": N, "raw_iterative": round(hits / N, 3)}


try:
    p = hf_hub_download('ai-hyz/MemoryAgentBench', 'data/Conflict_Resolution-00000-of-00001.parquet', repo_type='dataset')
    df = pd.read_parquet(p)
    res = [eval_row(df, r) for r in ROWS]
    OUT.write_text(json.dumps({"ok": True, "rows": res}, indent=2), encoding="utf-8")
except Exception as e:
    import traceback
    OUT.write_text(json.dumps({"ok": False, "error": repr(e), "tb": traceback.format_exc()}, indent=2), encoding="utf-8")
