"""Tests A and B of ERASURE_REVERT_SPEC.md — governance axes MemOps never asks about.

Zero LLM judge calls anywhere: every metric is a string or byte check, so the judge instability that
qualifies the pilot cannot touch these numbers. The mem0 arm needs LLM ingestion and is therefore
opt-in (`--arms mnemo,naive,mem0`) so it can wait for the main mem0 run to release the cloud quota.

Read ERASURE_REVERT_SPEC.md first: predictions E1-E4 and R1-R3 are fixed there, including E1 which
predicts a TIE with the naive baseline and R3 which predicts a competitor CAN do what we do.
"""
import argparse
import collections
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("MEMOPS_TOPK", "150")
HERE = pathlib.Path(__file__).resolve().parent

import pilot  # noqa: E402
from mnemo.mnemo import Mnemo, regex_extractor  # noqa: E402


def norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def aliases(value, retained=()):
    """Every surface form of a value a store might have kept, MINUS anything legitimately retained.

    The literal string is not enough: a system that stores LLM-rewritten memories may hold
    'David Lam transferred to Vancouver' after 'David Lam, colleague in Strategic Analytics' was
    deleted by exact match. The first comma-segment (usually the entity itself) is the primary alias
    and is what a competent engineer would delete on.

    The `retained` subtraction is not cosmetic — without it the first run scored paraphrase_residue
    = 1.000 on A02_forget, because the forgotten value 'David Lam, colleague in Strategic Analytics
    department' yielded 'strategic analytics' as an alias, and that is the user's DEPARTMENT, a fact
    they explicitly kept. The metric was flagging correct behaviour as a leak.
    """
    v = value.strip()
    out = {norm(v), norm(v.split(",")[0])}
    for m in re.findall(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)+)\b", v):   # proper names
        out.add(norm(m))
    for m in re.findall(r"\b[A-Z]{2,}-[\w-]+\b", v):                  # ids like MCG-20250115-47
        out.add(norm(m))
    out = {a for a in out if len(a) > 3}
    keep = set()
    for r in retained:
        rn = norm(r)
        keep |= {rn, norm(r.split(",")[0])}
        for m in re.findall(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)+)\b", r):
            keep.add(norm(m))
    # drop any alias that IS a retained value, or is contained in one (substring collisions)
    return {a for a in out if a not in keep and not any(a in k for k in keep)}


def build_store(lc, keyed, path):
    m = Mnemo(path=path)
    if keyed:
        m.extractor = regex_extractor
        m.echo_guard = True
    for _si, role, sent in pilot.units_of(lc):
        try:
            m.remember(sent, tags=[role])
        except Exception:
            pass
    m._save(force=True)
    return m


def state_hash(m):
    return hashlib.sha256(json.dumps(
        sorted((r.get("text", ""), r.get("status", ""), r.get("key") or "") for r in m.items),
        ensure_ascii=False).encode()).hexdigest()


def ctx_of(m, q, keyed):
    return norm(pilot.recall_mnemo(m, q, keyed)[:12000])


# ---------------------------------------------------------------- Test A: erasure
def test_a(files, arm):
    keyed = arm == "mnemo"
    rows = []
    for name in files:
        lc = json.loads((HERE / "data_lc" / name).read_text(encoding="utf-8"))
        ev = json.loads((HERE / "data" / name).read_text(encoding="utf-8"))
        ops = [o for o in ev.get("operations", []) if o.get("validity") == "confirmed"]
        forgets = [o for o in ops if o.get("type") == "forget" and o.get("old_value")]
        if not forgets:
            continue
        target = forgets[0]["old_value"]
        primary = norm(target.split(",")[0])
        retained = [o["new_value"] for o in ops
                    if o.get("type") == "remember" and o.get("new_value")
                    and primary not in norm(o["new_value"])]
        al = aliases(target, retained)
        probes = [a["question"] for a in (lc.get("answer") or []) if a.get("question")]

        d = tempfile.mkdtemp(prefix="erasure_")
        p = os.path.join(d, "store.jsonl")
        m = build_store(lc, keyed, p)
        before = sum(1 for r in m.items if primary in norm(r.get("text", "")))

        # over-forgetting must be a BEFORE/AFTER difference, not an absolute reachability count.
        # The first run asked "What is my <value>?" once, after deletion, and scored 0.50-0.75 —
        # but that mostly measured how well a templated query retrieves at all. Measuring the same
        # query on the same store before and after cancels the query's weakness out: only a fact
        # that WAS reachable and then stopped being reachable counts as over-forgotten.
        reachable_before = [v for v in retained if norm(v) in ctx_of(m, f"What is my {v}?", keyed)]

        # the shared deletion path: hard-delete every record carrying the value (spec rule 3)
        res = m.forget(where=lambda r: primary in norm(r.get("text", "")))
        steps, llm_calls = 1, 0

        leak = sum(1 for q in probes if primary in ctx_of(m, q, keyed)) / max(1, len(probes))
        para = sum(1 for q in probes if any(a in ctx_of(m, q, keyed) for a in al - {primary})) \
            / max(1, len(probes))
        still = sum(1 for v in reachable_before if norm(v) in ctx_of(m, f"What is my {v}?", keyed))
        over = (1 - still / len(reachable_before)) if reachable_before else None
        raw = 0
        for f in pathlib.Path(d).rglob("*"):
            if f.is_file():
                try:
                    if primary in norm(f.read_text(encoding="utf-8", errors="replace")):
                        raw += 1
                except Exception:
                    pass
        h1 = state_hash(m)

        # determinism: same ingest + same delete on a fresh store must land byte-identical
        d2 = tempfile.mkdtemp(prefix="erasure_rep_")
        m2 = build_store(lc, keyed, os.path.join(d2, "store.jsonl"))
        m2.forget(where=lambda r: primary in norm(r.get("text", "")))
        det = state_hash(m2) == h1
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(d2, ignore_errors=True)

        rows.append({"file": name, "arm": arm, "target": target, "records_hit": before,
                     "deleted": res.get("forgotten", 0), "retrieval_leakage": leak,
                     "paraphrase_residue": para, "over_forget": over, "raw_residue_files": raw,
                     "deterministic": det, "steps": steps, "llm_calls": llm_calls,
                     "retained_reachable_before": len(reachable_before), "retained_total": len(retained),
                     "receipt": bool(getattr(m, "tombstones", None))})
        print(f"  {name:22} deleted={res.get('forgotten',0):3}/{before:3} leak={leak:.3f} "
              f"para={para:.3f} overFgt={('%.3f' % over) if over is not None else ' n/a '} "
              f"(kept {len(reachable_before)} reachable) raw_files={raw} det={det}", flush=True)
    return rows


# ---------------------------------------------------------------- Test B: revert
def test_b(files, arm):
    keyed = arm == "mnemo"
    rows = []
    import stale_diagnostic as sd
    for name in files:
        lc = json.loads((HERE / "data_lc" / name).read_text(encoding="utf-8"))
        ev = json.loads((HERE / "data" / name).read_text(encoding="utf-8"))
        chains = {}
        for o in ev.get("operations", []):
            if o.get("validity") == "confirmed" and o.get("new_value"):
                chains.setdefault(o.get("chain_id") or "c", []).append(o)
        for cid, ops in chains.items():
            ops.sort(key=lambda o: o.get("chain_step", 0))
            if len(ops) < 2:
                continue
            gold_prev = norm(ops[-2]["new_value"])
            d = tempfile.mkdtemp(prefix="revert_")
            m = build_store(lc, keyed, os.path.join(d, "store.jsonl"))
            # what the store can do UNAIDED: does it hold the predecessor, and can one call restore it?
            keys = [r.get("key") for r in m.items
                    if r.get("key") and norm(ops[-1]["new_value"]) in norm(r.get("text", ""))]
            recoverable = any(norm(r.get("text", "")) and gold_prev in norm(r.get("text", ""))
                              and r.get("status") == "superseded" for r in m.items)
            exact, steps, needs_ext = 0, 0, True
            if keyed and keys:
                try:
                    out = m.revert(keys[0])
                    steps = 1
                    needs_ext = False
                    cur = [r for r in m.items if r.get("key") == keys[0] and r.get("status") != "superseded"]
                    exact = int(any(gold_prev in norm(r.get("text", "")) for r in cur))
                except Exception as e:
                    out = {"error": str(e)}
            shutil.rmtree(d, ignore_errors=True)
            rows.append({"file": name, "arm": arm, "chain": cid, "gold_prev": ops[-2]["new_value"],
                         "revert_exact": exact, "predecessor_recoverable": recoverable,
                         "needs_external_knowledge": needs_ext, "steps": steps, "llm_calls": 0})
            print(f"  {name:22} chain={cid:14} exact={exact} recoverable={recoverable} "
                  f"steps={steps} needs_ext={needs_ext}", flush=True)
    return rows


def _unit(job):
    """One (test, arm, scenario) work unit — the parallel granularity.

    Scenarios are completely independent: each builds its own store in its own temp directory and
    shares nothing. Running them in a serial for-loop pinned the whole probe to one core of twelve
    and turned a five-minute job into fifty.
    """
    test, arm, name = job
    return f"{test}_{arm}", (test_a([name], arm) if test == "A" else test_b([name], arm))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="mnemo,naive")
    ap.add_argument("--tests", default="A,B")
    ap.add_argument("--workers", type=int, default=min(12, (os.cpu_count() or 4) - 2))
    a = ap.parse_args()
    done = sorted({r["file"] for r in json.loads((HERE / "pilot_raw_k150.json").read_text(encoding="utf-8"))})
    forget_files = [f for f in done if f.endswith("_forget.json")]
    update_files = [f for f in done if f.endswith("_update.json")]

    jobs = []
    for arm in a.arms.split(","):
        if "A" in a.tests:
            jobs += [("A", arm, f) for f in forget_files]
        if "B" in a.tests:
            jobs += [("B", arm, f) for f in update_files]

    out = collections.defaultdict(list)
    print(f"{len(jobs)} independent scenario-units on {a.workers} workers "
          f"({os.cpu_count()} logical cores available)\n", flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for i, (key, rows) in enumerate(ex.map(_unit, jobs), 1):
            out[key] += rows
            print(f"  [{i}/{len(jobs)}] {key} done ({time.time()-t0:.0f}s elapsed)", flush=True)
    out = dict(out)
    (HERE / "erasure_revert_result.json").write_text(json.dumps(out, indent=1, ensure_ascii=False),
                                                     encoding="utf-8")
    print("\n" + "=" * 70)
    for k, rows in out.items():
        if not rows:
            continue
        n = len(rows)
        if k.startswith("A_"):
            f = lambda key: sum(r[key] for r in rows) / n
            ov = [r['over_forget'] for r in rows if r['over_forget'] is not None]
            print(f"{k:12} n={n}  leak={f('retrieval_leakage'):.3f}  para={f('paraphrase_residue'):.3f}  "
                  f"overFgt={(sum(ov)/len(ov) if ov else float('nan')):.3f}  "
                  f"raw_files={f('raw_residue_files'):.2f}  "
                  f"det={sum(r['deterministic'] for r in rows)}/{n}")
        else:
            print(f"{k:12} n={n}  revert_exact={sum(r['revert_exact'] for r in rows)}/{n}  "
                  f"recoverable={sum(r['predecessor_recoverable'] for r in rows)}/{n}  "
                  f"needs_ext={sum(r['needs_external_knowledge'] for r in rows)}/{n}")


if __name__ == "__main__":
    main()
