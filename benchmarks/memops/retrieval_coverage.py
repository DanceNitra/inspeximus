"""Phase 2b diagnostic — is RETRIEVAL the bottleneck, not the integrity layer?

Zero LLM calls. For every probe in the pilot's 24 files we ask one question per arm:
does the context that arm hands the answerer actually CONTAIN the evidence turns?

If mnemo/naive coverage is far below session_rag's, then the pilot's P2/P3 null is
uninterpretable: supersession cannot correct a stale value the retriever never returned.
That is a harness property, not a product property, and it must be measured before any
conclusion about the integrity layer is recorded.

Reported per arm: evidence-turn coverage (recall of the gold evidence sentences) and the
character budget actually spent, since a bigger context trivially buys more coverage.
"""
import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = pathlib.Path(__file__).resolve().parent

import pilot  # noqa: E402  (reuse the exact ingestion/retrieval code the pilot ran)


def norm(s):
    return re.sub(r"\s+", " ", s.lower()).strip()


def evidence_sentences(lc):
    """Sentences of the EVIDENCE segments only (segments injected into the noise)."""
    out = []
    for seg in lc.get("conversations", []):
        if not seg.get("evidence_inserted"):
            continue
        for t in seg.get("dialogue", []):
            c = (t.get("content") or "").strip()
            if not c or t.get("role") != "user":
                continue
            for s in re.split(r"(?<=[.!?])\s+", c):
                s = norm(s)
                if len(s) > 25:
                    out.append(s)
    return out


def coverage(ctx, sents):
    if not sents:
        return None
    c = norm(ctx)
    return sum(1 for s in sents if s in c) / len(sents)


def main(files, topk_variants=(20,)):
    rows = []
    for fi, name in enumerate(files, 1):
        lc = json.loads((HERE / "data_lc" / name).read_text(encoding="utf-8"))
        ev_sents = evidence_sentences(lc)
        probes = [a for a in (lc.get("answer") or []) if a.get("question") and a.get("expected_answer")]
        stores = {"mnemo": pilot.build_mnemo(lc, True), "naive": pilot.build_mnemo(lc, False),
                  "session_rag": pilot.build_bm25_sessions(lc)}
        print(f"[{fi}/{len(files)}] {name:24} evidence_sents={len(ev_sents):3} probes={len(probes)}",
              flush=True)
        for a in probes:
            q = a["question"]
            ctxs = {"session_rag": pilot.recall_bm25(stores["session_rag"], q)}
            for k in topk_variants:
                pilot.TOPK = k
                ctxs[f"mnemo@{k}"] = pilot.recall_mnemo(stores["mnemo"], q, True)
                ctxs[f"naive@{k}"] = pilot.recall_mnemo(stores["naive"], q, False)
            pilot.TOPK = 20
            for arm, ctx in ctxs.items():
                ctx = ctx[:12000]      # the exact truncation the pilot applied
                rows.append({"file": name, "op": (lc.get("operation_type") or "").lower(),
                             "arm": arm, "cov": coverage(ctx, ev_sents), "chars": len(ctx)})
    (HERE / "retrieval_coverage.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")

    import collections
    by = collections.defaultdict(list)
    for r in rows:
        by[r["arm"]].append(r)
    print("\n" + "=" * 60)
    print(f"{'arm':14} {'n':>4} {'evid.coverage':>14} {'avg chars':>10}")
    for arm, rs in sorted(by.items()):
        cov = [r["cov"] for r in rs if r["cov"] is not None]
        print(f"{arm:14} {len(rs):>4} {sum(cov)/len(cov):14.3f} {sum(r['chars'] for r in rs)/len(rs):10.0f}")


if __name__ == "__main__":
    done = {r["file"] for r in json.loads((HERE / "pilot_raw_cheap.json").read_text(encoding="utf-8"))}
    ks = tuple(int(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1 else ["20"]))
    main(sorted(done), ks)
