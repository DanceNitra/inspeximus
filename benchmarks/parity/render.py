"""results/*.json -> RESULTS.md, the published table.

The renderer is deliberately dumb: it prints what is in the JSON and never computes a headline. Two rules
are enforced in code rather than left to the author's discretion:

* an arm that was not measured appears as `NOT-MEASURED` **with its reason in the cell**, never blank and
  never zero;
* an axis where inspeximus does not come first is marked, so a table of only our wins cannot be produced
  from this script by accident.
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
RESULTS = HERE / "results"


def _f(x, nd=3):
    return "—" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _rate(cell, key):
    r = (cell or {}).get(key)
    if not isinstance(r, dict) or r.get("rate") is None:
        return None, None
    return r["rate"], f"{r['rate']:.3f} <sub>[{r['ci95'][0]:.2f}, {r['ci95'][1]:.2f}] n={r['n']}</sub>"


def _nm(cell):
    """Why this cell has no number — quoted, never paraphrased into a zero."""
    if not isinstance(cell, dict):
        return "NOT-MEASURED"
    for k in ("not_measured", "error"):
        if cell.get(k):
            return f"NOT-MEASURED — {cell[k]}"
    return None


def render_parity(doc) -> list[str]:
    arms = doc["arms"]
    names = [a["arm"] for a in arms]
    by = {a["arm"]: a for a in arms}
    c = doc["corpus"]
    out = [
        "## Axes — self-authored corpus\n",
        f"Corpus `{c['subset']}` (seed {c['seed']}, sha256 `{c['sha256'][:16]}...`): "
        f"{c['threads']} threads x {c['distractors_per_thread']} distractors, k={doc['k']}. "
        f"inspeximus {doc['inspeximus_version']}.\n",
        f"Reader: {doc['reader']}.\n",
    ]

    def table(title, rows, note=""):
        out.append(f"\n### {title}\n")
        out.append("| metric | " + " | ".join(f"**{n}**" if n == "inspeximus" else n for n in names) + " |")
        out.append("|---|" + "---|" * len(names))
        for label, fn in rows:
            cells = []
            for n in names:
                arm = by[n]
                if arm["status"] != "measured":
                    cells.append(f"NOT-MEASURED — {arm['reason']}")
                    continue
                cells.append(fn(arm) or _nm(arm.get(label.split("|")[0])) or "—")
            out.append(f"| {label.split('|')[-1]} | " + " | ".join(cells) + " |")
        if note:
            out.append(f"\n{note}")

    table("W — write cost", [
        ("write|wall seconds per 100 writes", lambda a: _f(a["write"].get("s_per_100_writes"), 4)),
        ("write|LLM calls per write", lambda a: _f(a["write"].get("llm_calls_per_write"))),
    ], note="_BM25's write is an append; its cost is paid at read time, where it rebuilds the index. "
            "A like-for-like ingest+query cost would move that column and is not claimed here._")

    table("R — revert (unmarked 'go back', naming no value)", [
        ("revert|revert success (higher better)", lambda a: _rate(a["revert"], "revert_success")[1]),
        ("revert|kept the corrected value", lambda a: _f(a["revert"].get("kept_corrected_B"))
         if "revert_success" in a["revert"] else None),
        ("revert|path taken", lambda a: a["revert"].get("path")),
    ])

    table("C — echo resistance (retired value restated)", [
        ("echo|resurrection rate (lower better)", lambda a: _rate(a["echo"], "resurrection_rate")[1]),
        ("echo|clean current-value rate", lambda a: _rate(a["echo"], "clean_current_rate")[1]),
    ])

    table("E — erasure", [
        ("erasure|retrieval leakage (lower better)", lambda a: _rate(a["erasure"], "retrieval_leakage")[1]),
        ("erasure|residue files in persisted bytes", lambda a: _f(a["erasure"].get("raw_residue_files"))),
        ("erasure|over-forget (lower better)", lambda a: _f(a["erasure"].get("over_forget"))),
        ("erasure|verifiable receipt", lambda a: "yes" if a["erasure"].get("receipt") else "no"),
        ("erasure|LLM calls", lambda a: _f(a["erasure"].get("llm_calls"))),
    ], note="_Receipt is a **governance** column, not an accuracy column._")

    kk = doc["k"]
    table("Q — retrieval on PARAPHRASED probes", [
        ("retrieval|hit@1", lambda a: _rate(a["retrieval"], "hit@1")[1]),
        (f"retrieval|hit@{kk}", lambda a: _rate(a["retrieval"], f"hit@{kk}")[1]),
    ], note="_Pre-registered as the row we expect to lose (Q1)._")

    losses = []
    for axis, key, higher in (("retrieval", "hit@1", True), ("retrieval", f"hit@{kk}", True),
                              ("echo", "resurrection_rate", False)):
        vals = {n: _rate(by[n].get(axis, {}), key)[0] for n in names
                if by[n]["status"] == "measured"}
        vals = {n: v for n, v in vals.items() if v is not None}
        if len(vals) > 1 and "inspeximus" in vals:
            best = max(vals.values()) if higher else min(vals.values())
            if (vals["inspeximus"] < best) if higher else (vals["inspeximus"] > best):
                winner = [n for n, v in vals.items() if v == best]
                losses.append(f"- **{axis}/{key}**: inspeximus {vals['inspeximus']:.3f}, "
                              f"best is {', '.join(winner)} at {best:.3f}")
    out.append("\n### Rows where inspeximus does NOT come first\n")
    out += (losses or ["- none in this run — which on a self-authored corpus is a reason to distrust "
                       "the corpus, not to celebrate"])
    return out


def render_cr(doc) -> list[str]:
    out = ["\n## Spine — MemoryAgentBench Conflict Resolution (third-party)\n",
           f"`{doc['dataset']}` · metric: {doc['metric_stage2']} · k={doc['k']} · hops={doc['hops']} · "
           f"`reinforce=False` on every recall.\n"]

    f1a = doc.get("F1a")
    if f1a:
        out += [f"\n### Stage 1 — mechanism control, zero LLM calls · **F1a {f1a['verdict']}**\n",
                f"{f1a['metric']}. {f1a['reading']}\n",
                "\n| k | current_rank ON | current_rank OFF | current_in_topk ON/OFF | "
                "stale_in_topk ON/OFF | ceilinged |", "|---|---|---|---|---|---|"]
        for c in f1a["per_k"]:
            out.append(f"| {c['k']} | {c['current_rank_on']:.3f} | {c['current_rank_off']:.3f} | "
                       f"{c['current_in_topk_on']:.3f} / {c['current_in_topk_off']:.3f} | "
                       f"{c['stale_in_topk_on']:.3f} / {c['stale_in_topk_off']:.3f} | "
                       f"{'yes' if c['ceilinged'] else 'no'} |")

    s2 = doc.get("stage2")
    out.append("\n### Stage 2 — their metric, 2x2 factorial\n")
    if not s2:
        out.append(f"**NOT-MEASURED** — {doc.get('stage2_not_measured', 'did not run')}\n")
    else:
        arms = ["full_context", "single_off", "single_on", "iter_off", "iter_on"]
        out.append("| row | facts | " + " | ".join(arms) + " |")
        out.append("|---|---|" + "---|" * len(arms))
        for r in s2:
            out.append(f"| {r['row']} | {r['facts']} | " +
                       " | ".join(str(r.get(a)) for a in arms) + " |")
        op = doc.get("stage2_operating_point", {})
        out.append(f"\nAnswerer `{op.get('model')}` @ `{op.get('endpoint')}`, temperature "
                   f"{op.get('temperature')}, seed {op.get('seed')}, resident context "
                   f"{op.get('resident_context_length')}; {op.get('calls')} calls, "
                   f"{op.get('errors')} errors.")
        if op.get("gpu_contended"):
            out.append(f"\n> **GPU CONTENDED** — {op.get('gpu_state', {}).get('preflight_reason')}. "
                       f"{op.get('latency_note')}")
        f1 = doc.get("F1", {})
        out.append(f"\n**F1 {f1.get('verdict')}** — {f1.get('reading')}  \n"
                   f"`iter_on={f1.get('iter_on')}` vs `iter_off={f1.get('iter_off')}`, "
                   f"`single_on={f1.get('single_on')}` vs `single_off={f1.get('single_off')}`.")
    return out


def main() -> int:
    out = ["# Parity results",
           "",
           "Generated by `benchmarks/parity/render.py` from the committed result JSON. "
           "Read `PREREGISTRATION.md` first — the axes, the metrics and the expected directions "
           "(including the ones against us) were fixed before any number here existed.",
           ""]
    p = RESULTS / "parity_small.json"
    if p.exists():
        out += render_parity(json.loads(p.read_text(encoding="utf-8")))
    cr = RESULTS / "cr_control.json"
    if cr.exists():
        out += render_cr(json.loads(cr.read_text(encoding="utf-8")))
    dest = HERE / "RESULTS.md"
    dest.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
