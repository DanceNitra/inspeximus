"""identity_gate_supersession_probe.py — does gating supersession on identity-resolution confidence prevent a
CONFIDENT-BUT-WRONG authoritative ledger? (marintkael's point, r/... thread 2026-07-15.)

Prior art, credited (NOT novel): Fellegi & Sunter, "A Theory for Record Linkage", JASA 1969 — the three-zone
rule (match / non-match / POSSIBLE MATCH -> clerical review); MDM match/merge stewardship (auto-merge above a
threshold, route the intermediate band to a steward queue, e.g. Informatica/Semarchy). The contribution here is
only the PORT into an agent-memory write path + the measured prevention vs an ungated baseline. In agent memory
nobody gates supersession on identity confidence: mem0/Zep-Graphiti/Letta all auto-commit an ungated update.

THE SCENARIO (deterministic, no LLM, no embedder). E entities each hold an authoritative value. A stream of
corrections arrives. The upstream identity resolver is NOISY: with prob p_miss it attaches a correction to the
WRONG entity, and it reports a confidence that is (imperfectly) lower when it is wrong. Two write policies:
  UNGATED  : supersede on whatever key the resolver returned (inspeximus default / mem0-style auto-commit)
  GATED    : remember(..., identity_confidence=c); c < fork_below forks a candidate instead of superseding
METRIC = authoritative-ledger CORRUPTION rate: fraction of entities whose authoritative value is now WRONG
(a correction landed on the wrong record). Lower is better. Also report the review-queue cost (candidates).

RUN:  python inspeximus/probes/identity_gate_supersession_probe.py
"""
import os
import sys
import json
import random
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from inspeximus import Inspeximus, __version__                          # noqa: E402

E = 40            # entities
ROUNDS = 6        # corrections per entity (a stream)
P_MISS = 0.20     # resolver attaches to the WRONG entity this often
FORK_BELOW = 0.7
SEED = 11


def resolver_confidence(is_correct, rng):
    """A confidence that separates right from wrong imperfectly (AUROC ~0.8): correct hits skew high,
    misresolutions skew low, with overlap. This is the signal a real entity resolver already produces."""
    if is_correct:
        return min(0.99, max(0.0, rng.gauss(0.85, 0.12)))
    return min(0.99, max(0.0, rng.gauss(0.45, 0.18)))


def run(policy, rng):
    # mkstemp CREATES the file, empty, and an empty file is not valid JSON -- the store refuses to open it
    # rather than silently overwriting what might be someone's data. So this probe crashed on line 1 and
    # nothing noticed, because it is one of the 48 probes no doc cites and no test runs.
    p = os.path.join(tempfile.mkdtemp(prefix="idg_"), "store.json")
    m = Inspeximus(path=p)
    m.fork_below = FORK_BELOW
    truth = {}                                               # entity -> the value it SHOULD hold
    for e in range(E):
        v = f"v{e}-0"
        m.remember(f"entity {e} value is {v}", key=f"ent::{e}", object=v)
        truth[e] = v
    n_candidates = 0
    for r in range(1, ROUNDS + 1):
        for e in range(E):
            new_v = f"v{e}-{r}"
            miss = rng.random() < P_MISS
            target = rng.randrange(E) if miss else e         # noisy identity resolution
            if miss and target == e:                         # a "miss" that accidentally hit right isn't a miss
                miss = False
            conf = resolver_confidence(not miss, rng)
            if policy == "ungated":
                m.remember(f"correction: entity {e} value is {new_v}", key=f"ent::{target}", object=new_v)
                if not miss:
                    truth[e] = new_v                         # a correct write updates the ground truth
                # a miss silently corrupts entity `target`; truth[target] is now out of sync (we do NOT update it)
            else:  # gated
                res = m.remember(f"correction: entity {e} value is {new_v}", key=f"ent::{target}",
                                 object=new_v, identity_confidence=conf)
                rec = next(x for x in m.items if x["id"] == res)
                if rec.get("status") == "candidate":
                    n_candidates += 1
                elif not miss:
                    truth[e] = new_v
                # a miss that passed the gate (high conf but wrong) still corrupts; that's the residual
    # measure authoritative-ledger corruption: entities whose active value != truth
    corrupt = 0
    for e in range(E):
        act = next((x for x in m.items if x.get("key") == f"ent::{e}" and x.get("status") == "active"), None)
        if act is None or act.get("object") != truth[e]:
            corrupt += 1
    for suf in ("", ".receipts.json"):
        try: os.remove(p + suf)
        except OSError: pass
    return corrupt / E, n_candidates


#: 25, not 5. Five seeds were labelled "a CI-ish spread", and a spread from five draws is run-to-run noise
#: wearing a result's clothes: the mean settles quickly, the RANGE does not. On normal draws five trials
#: capture 2.33 sigma of range on average and twenty-five capture 3.94 -- 69% wider -- while the mean's
#: standard error only halves. Credit: jacksonxly, who pointed out that spread converges far more slowly
#: than the mean, after we published an under-sampled one in a post about measurement discipline.
SEEDS = 25


def main():
    print(f"=== IDENTITY-GATE SUPERSESSION PROBE (inspeximus {__version__}, E={E}, rounds={ROUNDS}, "
          f"p_miss={P_MISS}, fork_below={FORK_BELOW}) ===")
    print("authoritative-ledger corruption rate = entities whose current value is WRONG (lower better)\n")
    ung, gat = [], []
    ncand = 0
    for s in range(SEEDS):
        rng = random.Random(SEED + s)
        u, _ = run("ungated", rng)
        rng = random.Random(SEED + s)
        g, nc = run("gated", rng)
        ung.append(u); gat.append(g); ncand += nc
    import statistics
    mu_u, mu_g = statistics.mean(ung), statistics.mean(gat)
    # n is printed WITH the range, always: "0.025-0.225 over 25 seeds" is a claim, "0.025-0.225" is not,
    # and the next person to quote it cannot otherwise know whether it was sampled enough. At 5 seeds this
    # same probe reported an ungated range of 0.100 where 25 show 0.200 -- the mean did not move.
    print(f"UNGATED (auto-commit, mem0/inspeximus-default): corruption {mu_u:.3f}  "
          f"range {min(ung):.3f}-{max(ung):.3f} over seeds={SEEDS}  "
          f"(per-seed {[round(x,3) for x in ung]})")
    print(f"GATED   (identity_confidence < {FORK_BELOW} -> candidate): corruption {mu_g:.3f}  "
          f"range {min(gat):.3f}-{max(gat):.3f} over seeds={SEEDS}  "
          f"(per-seed {[round(x,3) for x in gat]})")
    print(f"review-queue cost: {ncand/SEEDS:.0f} candidates/run forked for steward reconciliation")
    print(f"\n=> the gate cuts confident-wrong authoritative writes {mu_u:.3f} -> {mu_g:.3f} "
          f"({(1-mu_g/mu_u)*100:.0f}% reduction) at the cost of a review queue.")
    print("Residual gated corruption = misresolutions that scored ABOVE the threshold (the gate is only as good "
          "as the confidence signal; Fellegi-Sunter's clerical-review zone, not a proof).")
    json.dump({"inspeximus": __version__, "E": E, "rounds": ROUNDS, "p_miss": P_MISS, "fork_below": FORK_BELOW,
               "ungated_corruption": round(mu_u, 3), "gated_corruption": round(mu_g, 3),
               "candidates_per_run": round(ncand / 5, 1)},
              open(os.path.join(os.path.dirname(__file__), "identity_gate_supersession_result.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
