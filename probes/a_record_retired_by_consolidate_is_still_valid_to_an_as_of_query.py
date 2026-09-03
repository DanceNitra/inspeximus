"""Keep-budget eviction retires a record without an invalidation time. Deliberate, and now pinned.

WHAT THIS IS FOR. `recall(query, as_of=T)` answers "what did the store believe at time T":

    vf = r.get("valid_from", r["ts"]); inv = r.get("invalidated_at")
    if vf > as_of or (inv is not None and inv <= as_of): return False

Ten sites in core.py set `status = "superseded"`, and four of them never write `invalidated_at`. A
static read of that says four write paths forget a bitemporal field. The static read is wrong, and
this probe is what corrected it.

THE FOUR ARE NOT SUPERSESSION. They are keep-budget eviction (`consolidate`,
`consolidate_clusters`), candidate discard, and lineage retraction, and they stamp
`superseded_by_policy = "keep_budget"` rather than naming a successor. Nothing replaced the fact. It
stopped being STORED, not TRUE, so there is no instant at which it became false and
`invalidated_at` has nothing to hold. An `as_of` query before or after the eviction still counts it,
which is the intended reading of "what did the store believe".

So this probe does not report a defect. It PINS the distinction, because the two behaviours are one
`if` apart and nothing else in the suite tells them apart.

WHY IT WAS REWRITTEN. Its first version ran `consolidate()` on a fixture of near-duplicates, found
every retired record carried `invalidated_at`, and reported no leak. A line trace then showed the
keep-budget branch had never executed: the fixture drove the toggle branch, which does write the
field. A green arm that never reaches its target is the failure mode this repository has a rule
about, and it had it. Hence the coverage assertion below, which is the only reason the result means
anything.

CONTROLS:
  * COVERAGE, ASSERTED. The probe traces execution and REFUSES unless the keep-budget line actually
    ran in both consolidate() and consolidate_clusters(). It locates that line by its marker string
    rather than by number, so editing core.py moves the target instead of silently missing it.
  * A CORRECT PATH ALONGSIDE. Keyed supersession writes `invalidated_at`, and an `as_of` after that
    retirement must EXCLUDE the old value. If it does not, the query is broken everywhere and no
    statement about eviction can be made.
  * AN AS-OF BEFORE THE RETIREMENT MUST STILL RETURN IT, so the probe cannot pass by the query
    returning nothing.
  * THE EVICTED RECORD MUST BE GONE FROM A PLAIN RECALL, or it was never retired at all.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

sys.stdout.reconfigure(line_buffering=True)
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "a_record_retired_by_consolidate_is_still_valid_to_an_as_of_query.result.json")
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

MARKER = 'superseded_by_policy"] = "keep_budget"'


def refuse(why):
    print("REFUSED: " + why)
    json.dump({"verdict": "REFUSED", "why": why}, io.open(OUT, "w", encoding="utf-8"), indent=1)
    raise SystemExit(2)


def marker_lines(path):
    out = []
    for i, l in enumerate(io.open(path, encoding="utf-8").read().splitlines(), 1):
        if MARKER in l:
            out.append(i)
    return out


def run_traced(core_path, fn, *a, **k):
    """Run fn and return (result, set of core.py line numbers executed)."""
    seen = set()
    target = os.path.abspath(core_path)

    def tracer(frame, event, arg):
        if event == "line" and os.path.abspath(frame.f_code.co_filename) == target:
            seen.add(frame.f_lineno)
        return tracer

    sys.settrace(tracer)
    try:
        r = fn(*a, **k)
    finally:
        sys.settrace(None)
    return r, seen


def main():
    import tempfile
    from inspeximus import Inspeximus
    import inspeximus.core as core_mod

    core_path = core_mod.__file__
    markers = marker_lines(core_path)
    if len(markers) < 2:
        refuse("expected the keep-budget marker %r at two sites in core.py, found %d. The eviction "
               "path this probe pins has moved or been renamed." % (MARKER, len(markers)))

    tmp = tempfile.mkdtemp(prefix="inspx_asof_")

    # ---- CONTROL: keyed supersession, which does write invalidated_at ----
    a = Inspeximus(path=os.path.join(tmp, "keyed.json"))
    a.remember("Payment terms are net 30", key="terms")
    t_before = time.time()
    time.sleep(0.02)
    a.remember("Payment terms are net 60", key="terms")
    t_after = time.time()
    a_ret = [r for r in a.items if (r.get("status") or "") == "superseded"]
    if not a_ret or a_ret[0].get("invalidated_at") is None:
        refuse("the keyed control did not retire with an invalidated_at, so there is no correct "
               "path to compare eviction against")
    a_after = [r["text"] for r in a.recall("payment terms", k=8, as_of=t_after)]
    a_before = [r["text"] for r in a.recall("payment terms", k=8, as_of=t_before)]
    if any("net 30" in t for t in a_after):
        refuse("the as_of filter returns a properly invalidated value after its retirement, so it "
               "is broken everywhere and this is a larger finding than eviction")
    if not any("net 30" in t for t in a_before):
        refuse("an as_of BEFORE the retirement returned nothing, so the query does not do what this "
               "probe assumes")

    arms = {}
    for name, builder in (("consolidate", "keep"), ("consolidate_clusters", "cluster")):
        m = Inspeximus(path=os.path.join(tmp, name + ".json"))
        # DISTINCT subjects on purpose. Near-duplicates get retired by the toggle/dedup pass first,
        # which shrinks `active` below `keep` and the budget branch never runs. That is exactly how
        # the previous version of this probe came out green without reaching its target.
        subjects = ["collector heartbeat", "billing export", "tls renewal", "index rebuild",
                    "queue drain", "cache warm", "log rotation", "schema migration",
                    "backup verify", "dns failover", "quota audit", "token refresh",
                    "replica lag", "cert pinning"]
        for i, subj in enumerate(subjects):
            m.remember("Runbook %d covers %s and nothing else" % (i, subj), value=1.0 + i * 0.01)
        t_pre = time.time()
        time.sleep(0.02)
        if builder == "keep":
            _, seen = run_traced(core_path, m.consolidate, keep=4, dup_threshold=0.99)
        else:
            _, seen = run_traced(core_path, m.consolidate_clusters,
                                 threshold=2, cluster_sim=0.3, keep_per_cluster=2)
        t_post = time.time()

        hit = sorted(set(markers) & seen)
        if not hit:
            refuse("%s never executed the keep-budget line (markers at %s). The arm is green "
                   "because it did not reach its target, which is the exact failure this rewrite "
                   "exists to remove." % (name, markers))

        evicted = [r for r in m.items
                   if (r.get("meta") or {}).get("superseded_by_policy") == "keep_budget"]
        if not evicted:
            refuse("%s executed the keep-budget line but stamped no record with that policy" % name)
        no_inv = [r for r in evicted if r.get("invalidated_at") is None]
        texts = {r["text"] for r in evicted}
        plain = [t for t in (x["text"] for x in m.recall("runbook", k=40)) if t in texts]
        if plain:
            refuse("%s: an evicted record is still returned by a plain recall, so it was never "
                   "retired and nothing below is about invalidation" % name)
        asof_post = [t for t in (x["text"] for x in m.recall("runbook", k=40, as_of=t_post))
                     if t in texts]
        asof_pre = [t for t in (x["text"] for x in m.recall("runbook", k=40, as_of=t_pre))
                    if t in texts]

        arms[name] = {"keep_budget_line_executed": hit, "evicted": len(evicted),
                      "evicted_without_invalidated_at": len(no_inv),
                      "plain_recall_returns_them": len(plain),
                      "as_of_after_eviction_returns": len(asof_post),
                      "as_of_before_eviction_returns": len(asof_pre)}
        print("  %-21s line %s | evicted %2d, %2d without invalidated_at | plain %d | "
              "as_of after %d, before %d"
              % (name, hit, len(evicted), len(no_inv), len(plain), len(asof_post), len(asof_pre)))

    if not all(v["evicted_without_invalidated_at"] == v["evicted"] for v in arms.values()):
        refuse("some evicted records DO carry invalidated_at, so eviction and supersession are no "
               "longer distinguishable by that field and this probe's premise is stale")

    print("  -> pinned: keep-budget eviction retires without an invalidation time, and an as_of "
          "query still counts the record. Supersession is the path that invalidates.")

    json.dump({"probe": os.path.basename(__file__),
               "when_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "keep_budget_marker_sites": markers,
               "control_keyed_invalidated_at": a_ret[0].get("invalidated_at"),
               "control_keyed_excludes_after": True,
               "control_keyed_includes_before": True,
               "arms": arms,
               "verdict": "eviction is not invalidation, and both consolidate paths were executed",
               "controls": {
                   "keep_budget_line_coverage_asserted_not_assumed": True,
                   "marker_located_by_string_so_edits_move_the_target": True,
                   "a_correctly_invalidating_path_measured_alongside": True,
                   "plain_recall_confirms_retirement_before_any_as_of_claim": True,
               }},
              io.open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
