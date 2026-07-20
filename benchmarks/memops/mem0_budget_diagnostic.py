"""Why does the mem0 arm score 0.000 even with a strong extractor and zero parse errors?

Before recording ANY mem0 number, rule out the confound I already caught on our own arms: an
unequal context budget. mnemo/naive hand the answerer ~11.9k characters (k=150 sentences); mem0's
`limit=20` short memories may be a fraction of that. Scoring mem0 at a 5x smaller budget would be
the mirror image of the mistake in Appendix B — and this time it would flatter us.

Ingests one scenario ONCE (the expensive part), then measures, per retrieval limit:
  - characters of context actually produced
  - evidence-sentence coverage, computed exactly as retrieval_coverage.py does for our arms
so the mem0 arm can be run at a budget matched to the others.
"""
import contextlib
import io
import json
import os
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = pathlib.Path(__file__).resolve().parent
import pilot  # noqa: E402
import retrieval_coverage as rc  # noqa: E402

SCENARIO = sys.argv[1] if len(sys.argv) > 1 else "A01_reflect.json"
LIMITS = [20, 50, 100, 200, 400]


def main():
    lc = json.loads((HERE / "data_lc" / SCENARIO).read_text(encoding="utf-8"))
    probes = [a for a in (lc.get("answer") or []) if a.get("question")]
    ev_sents = rc.evidence_sentences(lc)
    sid = SCENARIO.rsplit(".", 1)[0]
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        m = pilot.build_mem0(lc, sid)
    print(f"{SCENARIO}: ingest done ({buf.getvalue().count('Error parsing extraction response')} parse errors), "
          f"{len(probes)} probes, {len(ev_sents)} evidence sentences")

    try:
        allm = m.get_all(filters={"user_id": sid}, limit=10000) or {}
        allm = allm.get("results") if isinstance(allm, dict) else allm
        print(f"mem0 stored {len(allm or [])} memories from 50 sessions "
              f"(mnemo stored ~3400 verbatim records from the same input)")
    except Exception as e:
        print("get_all failed:", e)

    print(f"\n{'limit':>6} {'avg chars':>10} {'evid.coverage':>14}")
    out = {}
    for lim in LIMITS:
        chars, covs = [], []
        for a in probes:
            try:
                r = m.search(a["question"], filters={"user_id": sid}, limit=lim) or {}
                hits = r.get("results") if isinstance(r, dict) else r
            except Exception:
                hits = []
            ctx = "\n".join(f"- {h.get('memory','')}" for h in (hits or []))[:12000]
            chars.append(len(ctx))
            c = rc.coverage(ctx, ev_sents)
            if c is not None:
                covs.append(c)
        avg_c = sum(chars) / len(chars)
        avg_cov = sum(covs) / len(covs) if covs else 0.0
        print(f"{lim:>6} {avg_c:10.0f} {avg_cov:14.3f}")
        out[lim] = {"avg_chars": avg_c, "coverage": avg_cov}
    print("\nreference (retrieval_coverage.py, same 24-file setting): "
          "mnemo@150 chars=11916 cov=0.142 | session_rag chars=11941 cov=0.305")
    print("NOTE: mem0 stores PARAPHRASED memories, so verbatim evidence-sentence coverage understates "
          "it by construction — read the char budget as the comparable quantity, coverage as a floor.")
    (HERE / f"mem0_budget_{sid}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
