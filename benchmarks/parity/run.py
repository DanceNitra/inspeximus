"""The parity harness: one corpus, one reader, several memory systems, five axes.

    python benchmarks/parity/run.py --subset small
    python benchmarks/parity/run.py --subset small --arms inspeximus,naive,bm25,mem0

Reads `corpus.py`, runs every requested arm through every axis, and writes `results/parity_<subset>.json`.
`render.py` turns that into the published table.

Rules this file enforces rather than assumes:

* **A competitor scoring 0.000 is OUR bug until proven otherwise.** No arm is scored until its positive
  control passes; an arm that fails it is recorded `NOT-MEASURED` with the reason, never as a zero.
* **No cell without its operating point.** Corpus id + digest, k, arm version, and (for LLM arms) the model
  and endpoint travel with every result.
* **We measure the code that RUNS.** The repo root is forced onto `sys.path[0]`, and the resolved
  `inspeximus.__file__` is recorded, so a number can never silently describe a pip-installed copy.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

import adapters  # noqa: E402
import corpus as corpus_mod  # noqa: E402


# ---------------------------------------------------------------- the one reader
def top_value(surface, a: str, b: str) -> str:
    """The single verdict function every arm is read by (PREREGISTRATION.md, "The reader").

    Models a consumer that reads the top facts it is handed. A store that surfaces the retired value
    alongside the current one scores `unclear` — a read-contract difference, not a failure, reported in
    its own column and never folded into another arm's win.
    """
    al, bl = a.lower(), b.lower()
    for item in surface:
        t = (item or "").lower()
        ina, inb = al in t, bl in t
        if ina and inb:
            return "unclear"
        if ina:
            return "A"
        if inb:
            return "B"
    return "unclear"


def wilson(k: int, n: int, z: float = 1.96):
    if n <= 0:
        return [0.0, 0.0]
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return [round((c - h) / d, 3), round((c + h) / d, 3)]


def _rate(k: int, n: int):
    return {"rate": round(k / n, 3) if n else None, "k": k, "n": n, "ci95": wilson(k, n)}


# ---------------------------------------------------------------- axes
def axis_write_cost(arm, threads, k):
    """W — wall seconds and LLM calls to ingest the corpus. The moat, measured rather than asserted."""
    t0 = time.time()
    calls0, writes = arm.llm_calls, 0
    for th in threads:
        s = arm.open("w_" + th["id"])
        for d in th["distractors"]:
            arm.write(s, d["text"])
            writes += 1
        for r in th["retained"]:
            arm.write(s, r["text"])
            writes += 1
        arm.write(s, th["secret_text"])
        writes += 1
    dt = time.time() - t0
    return {"writes": writes, "wall_s": round(dt, 3),
            "s_per_100_writes": round(100 * dt / writes, 4) if writes else None,
            "llm_calls": arm.llm_calls - calls0,
            "llm_calls_per_write": round((arm.llm_calls - calls0) / writes, 3) if writes else None}


def _setup_corrected(arm, th, ns, k):
    """Assert A, correct to B, and CHECK THE CORRECTION TOOK before running any attack on it.

    This precondition is not ceremony. Without it the `naive` keep-all arm scored **revert_success
    1.00** — with no revert channel at all — because its ranker put the original, cleanly-worded
    sentence above the wordier "correction: ..." line, so the read surface was serving the old value
    the whole time and the "revert" changed nothing. A store that never applied the correction cannot
    be scored on undoing it; the case is invalid, not a win.

    Returns (session, pre_verdict). The caller keeps only cases where pre_verdict == "B".
    """
    s = arm.open(ns)
    arm.write(s, th["assert_a"], key=th["key"], object=th["value_a"])
    arm.write(s, th["correct_b"], key=th["key"], object=th["value_b"])
    pre = top_value(arm.read(s, th["read_query"], k=k), th["value_a"], th["value_b"])
    return s, pre


def _invalid(pre_counts, n, axis):
    return {"not_measured": (
        f"correction never took effect in this arm's read surface, so the {axis} attack has nothing to "
        f"act on: 0 of {n} cases were valid (pre-attack verdict was "
        + ", ".join(f"{v}x{c}" for v, c in sorted(pre_counts.items())) + "; a valid case needs 'B')"),
        "valid_cases": 0, "pre_attack_verdicts": dict(pre_counts)}


def axis_revert(arm, threads, k):
    """R — assert A, correct to B, then an UNMARKED revert naming no value. Does the read surface
    return to A? Each arm takes its own best exposed path (`route()` where one exists, otherwise the
    utterance is stored as a fact, which is what that system actually does)."""
    import collections
    verdicts, paths, pre_counts = [], [], collections.Counter()
    for th in threads:
        s, pre = _setup_corrected(arm, th, "r_" + th["id"], k)
        pre_counts[pre] += 1
        if pre != "B":
            continue
        p = arm.route(s, th["revert_utterance"], key=th["key"])
        paths.append(p.get("path"))
        verdicts.append(top_value(arm.read(s, th["read_query"], k=k), th["value_a"], th["value_b"]))
    if not verdicts:
        return _invalid(pre_counts, len(threads), "revert")
    n = len(verdicts)
    return {"revert_success": _rate(verdicts.count("A"), n),
            "kept_corrected_B": verdicts.count("B"), "unclear": verdicts.count("unclear"),
            "valid_cases": n, "of_threads": len(threads),
            "pre_attack_verdicts": dict(pre_counts), "path": paths[0] if paths else None}


def axis_echo(arm, threads, k):
    """C — assert A, correct to B, then RESTATE the retired A. Resurrection (A becomes current) is the
    attack and is lower-better; `clean_current` is answer clarity, a separate question."""
    import collections
    verdicts, pre_counts = [], collections.Counter()
    for th in threads:
        s, pre = _setup_corrected(arm, th, "e_" + th["id"], k)
        pre_counts[pre] += 1
        if pre != "B":
            continue
        arm.route(s, th["echo_utterance"], key=th["key"], object=th["value_a"])
        verdicts.append(top_value(arm.read(s, th["read_query"], k=k), th["value_a"], th["value_b"]))
    if not verdicts:
        return _invalid(pre_counts, len(threads), "echo")
    n = len(verdicts)
    return {"resurrection_rate": _rate(verdicts.count("A"), n),
            "clean_current_rate": _rate(verdicts.count("B"), n),
            "unclear": verdicts.count("unclear"), "valid_cases": n, "of_threads": len(threads),
            "pre_attack_verdicts": dict(pre_counts)}


def axis_erasure(arm, threads, k):
    """E — ingest, delete one value by the arm's best path, then measure what is left: in the read
    surface, in the persisted bytes, and in the facts that were supposed to SURVIVE."""
    rows = []
    for th in threads:
        s = arm.open("x_" + th["id"])
        for d in th["distractors"]:
            arm.write(s, d["text"])
        for r in th["retained"]:
            arm.write(s, r["text"])
        arm.write(s, th["secret_text"])

        # over-forgetting must be a BEFORE/AFTER difference: only a fact that WAS reachable and then
        # stopped being reachable counts. An absolute post-deletion count mostly measures how well a
        # templated query retrieves at all.
        reach_before = [r for r in th["retained"]
                        if r["value"].lower() in " ".join(arm.read(s, r["question"], k=k)).lower()]
        try:
            res = arm.erase(s, th["secret"])
        except adapters.Unavailable as e:
            rows.append({"thread": th["id"], "unsupported": str(e)})
            continue
        leak = th["secret"].lower() in " ".join(arm.read(s, th["secret_question"], k=k)).lower()
        still = sum(1 for r in reach_before
                    if r["value"].lower() in " ".join(arm.read(s, r["question"], k=k)).lower())
        raw = 0
        d = arm.data_dir(s)
        if d:
            for f in pathlib.Path(d).rglob("*"):
                if f.is_file():
                    try:
                        if th["secret"].lower() in f.read_text(encoding="utf-8",
                                                               errors="replace").lower():
                            raw += 1
                    except Exception:                                # noqa: BLE001
                        pass
        rows.append({"thread": th["id"], "leak": int(leak), "raw_residue_files": raw,
                     "retained_before": len(reach_before), "retained_after": still,
                     "deleted": res.get("deleted"), "steps": res.get("steps"),
                     "llm_calls": res.get("llm_calls"), "receipt": bool(res.get("receipt")),
                     "path": res.get("path")})
    ok = [r for r in rows if "unsupported" not in r]
    if not ok:
        return {"not_measured": rows[0].get("unsupported") if rows else "no threads"}
    n = len(ok)
    before = sum(r["retained_before"] for r in ok)
    return {"retrieval_leakage": _rate(sum(r["leak"] for r in ok), n),
            "raw_residue_files": round(sum(r["raw_residue_files"] for r in ok) / n, 3),
            "over_forget": round(1 - sum(r["retained_after"] for r in ok) / before, 3) if before else None,
            "over_forget_denominator": before,
            "steps": ok[0]["steps"], "llm_calls": sum(r["llm_calls"] or 0 for r in ok),
            "receipt": ok[0]["receipt"], "path": ok[0]["path"]}


def axis_retrieval(arm, threads, k):
    """Q — hit@1 / hit@k on PARAPHRASED probes. Pre-registered as the row we expect to lose."""
    h1 = hk = n = 0
    for th in threads:
        s = arm.open("q_" + th["id"])
        for d in th["distractors"]:
            arm.write(s, d["text"])
        for p in th["probes"]:
            hits = arm.read(s, p["question"], k=k)
            g = p["gold"].lower()
            h1 += int(bool(hits) and g in (hits[0] or "").lower())
            hk += int(any(g in (x or "").lower() for x in hits))
            n += 1
    return {"hit@1": _rate(h1, n), f"hit@{k}": _rate(hk, n), "k": k}


AXES = {"write": axis_write_cost, "revert": axis_revert, "echo": axis_echo,
        "erasure": axis_erasure, "retrieval": axis_retrieval}


# ---------------------------------------------------------------- driver
def run_arm(name: str, threads, k: int, cfg: dict) -> dict:
    try:
        arm = adapters.build_arm(name, **cfg)
    except Exception as e:                                           # noqa: BLE001
        return {"arm": name, "status": "NOT-MEASURED",
                "reason": f"could not construct: {type(e).__name__}: {e}"[:300]}
    pc = arm.positive_control()
    if not pc["passes"]:
        arm.close()
        return {"arm": name, "status": "NOT-MEASURED",
                "reason": f"positive control failed: {pc['reason']}", "positive_control": pc,
                "note": "A zero from a competitor is our bug until the positive control says otherwise; "
                        "this arm is reported unmeasured rather than scored."}
    out = {"arm": name, "status": "measured", "positive_control": pc, "info": arm.info()}
    for axis, fn in AXES.items():
        t0 = time.time()
        try:
            out[axis] = fn(arm, threads, k)
        except adapters.Unavailable as e:
            out[axis] = {"not_measured": str(e)[:300]}
        except Exception as e:                                       # noqa: BLE001
            out[axis] = {"error": f"{type(e).__name__}: {e}"[:300]}
        out.setdefault("axis_wall_s", {})[axis] = round(time.time() - t0, 2)
        print(f"    {name:10} {axis:10} {out['axis_wall_s'][axis]:7.2f}s  "
              f"{json.dumps(out[axis], default=str)[:150]}", flush=True)
    arm.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subset", default="small", choices=sorted(corpus_mod.SUBSETS))
    ap.add_argument("--arms", default=",".join(adapters.LOCAL_ARMS))
    ap.add_argument("-k", type=int, default=6)
    ap.add_argument("--threads", type=int, default=0, help="cap threads (0 = whole subset)")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    c = corpus_mod.load(a.subset)
    threads = c["items"][: a.threads] if a.threads else c["items"]
    import inspeximus
    print(f"corpus={a.subset} threads={len(threads)} k={a.k} "
          f"digest={corpus_mod.digest(c)[:16]}...\ninspeximus from {inspeximus.__file__}\n")

    results = {"corpus": {"subset": a.subset, "seed": c["seed"], "threads": len(threads),
                          "distractors_per_thread": c["distractors_per_thread"],
                          "sha256": corpus_mod.digest(c)},
               "k": a.k, "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "inspeximus_path": inspeximus.__file__,
               "inspeximus_version": getattr(inspeximus, "__version__", "?"),
               "reader": "top_value(): first retrieved item mentioning either candidate wins; "
                         "both in one item, or neither anywhere, is `unclear`",
               "arms": []}
    for name in [x.strip() for x in a.arms.split(",") if x.strip()]:
        print(f"  arm {name}")
        results["arms"].append(run_arm(name, threads, a.k, {}))
    measured = [r for r in results["arms"] if r["status"] == "measured"]
    results["summary"] = {"measured": [r["arm"] for r in measured],
                          "not_measured": {r["arm"]: r["reason"]
                                           for r in results["arms"] if r["status"] != "measured"}}
    p = pathlib.Path(a.out) if a.out else HERE / "results" / f"parity_{a.subset}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {p}")
    print("measured:", results["summary"]["measured"])
    for k_, v in results["summary"]["not_measured"].items():
        print(f"NOT-MEASURED {k_}: {v}")
    return 0 if measured else 1


if __name__ == "__main__":
    raise SystemExit(main())
