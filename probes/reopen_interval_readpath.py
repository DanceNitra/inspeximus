"""READ-PATH REOPEN probe (marintkael's lead, r/RAG 2026-07-16) — run against the REAL inspeximus store.

The confident wrong-merge is unattackable at WRITE time ("you cannot out-confidence your own confidence at the
moment you write"). The missing primitive lives on the READ path: inspeximus.observe() — a POST-write review
trigger (the mirror of the Fellegi-Sunter clerical-review band, which holds a POSSIBLE match BEFORE the write).
When independent evidence CONTRADICTS a high-confidence settled record, corroborate it (>= reopen_corroboration
independent observations, so a single benign restatement of the old value does NOT reopen) and REOPEN the
interval for steward review, surfacing the prior value.

Measured here on real Inspeximus (keyed supersession, no embedder needed — observe() matches on the object):
  (1) CONFIDENT WRONG-MERGE caught LATE, not never: a fuzzy match confidently superseded the true value with
      another entity's value. A baseline has no read path, so it never catches it. observe(true_value) x k
      reopens it once the contradiction corroborates.
  (2) VALUE-OBSCURING REVERT gets a key: observe(object=None) ('go back') reopens and surfaces the prior value.
  (3) FLOOD CONTROL (the honest falsifier): a single stray restatement of the old value (the OP's 'user repeats
      a preference they forgot they changed') must NOT reopen.
"""
import os, sys, json, random, tempfile
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # use the local edited source, not an installed pkg
from inspeximus import Inspeximus

SEEDS = int(os.environ.get("SEEDS", 8))
E = int(os.environ.get("E", 200))
OUT = Path(__file__).with_name("reopen_interval_readpath_result.json")


def run(seed):
    random.seed(seed)
    m = Inspeximus(os.path.join(tempfile.mkdtemp(), "m.json"))
    K = m.reopen_corroboration
    true = {}
    wrong, reverts, flood = [], [], []
    for e in range(E):
        key = f"e{e}/f"; tv = f"v{e}_true"; true[key] = tv
        m.remember(f"the {key} is {tv}", key=key, object=tv)
    for e in range(E):
        key = f"e{e}/f"; r = random.random()
        if r < 0.15:
            bad = f"v{random.randrange(E)}_true"
            if bad == true[key]:
                continue
            m.remember(f"the {key} is {bad}", key=key, object=bad, identity_confidence=0.92)  # confident WRONG merge
            wrong.append(key)
        elif r < 0.35:
            m.remember(f"the {key} is v{e}_corrected", key=key, object=f"v{e}_corrected")       # correction
            reverts.append(key)
        elif r < 0.55:
            m.remember(f"the {key} is v{e}_corrected", key=key, object=f"v{e}_corrected")       # correction
            flood.append(key)
    # (1) wrong-merge: k corroborated observations of the true value
    wm_caught = 0
    for key in wrong:
        res = None
        for _ in range(K):
            res = m.observe(f"the {key} is {true[key]}", key=key, object=true[key])
        wm_caught += 1 if res and res["reopened"] else 0
    # (2) value-obscuring revert: names nothing; must reopen and surface the prior (pre-correction) value
    rev_keyed = 0
    for key in reverts:
        res = m.observe("go back to the old one", key=key, object=None)
        rev_keyed += 1 if res and res["reopened"] and res["surfaced_prior"] == true[key] else 0
    # (3) flood: ONE stray restatement of the old value -> must NOT reopen
    false_reopen = 0
    for key in flood:
        res = m.observe(f"the {key} is {true[key]}", key=key, object=true[key])
        false_reopen += 1 if res and res["reopened"] else 0
    nw, nr, nf = max(1, len(wrong)), max(1, len(reverts)), max(1, len(flood))
    return {"seed": seed, "K": K, "wrong_merges": len(wrong), "reverts": len(reverts), "flood": len(flood),
            "wm_caught_reopen": round(wm_caught / nw, 3), "wm_caught_base": 0.0,
            "revert_keyed_reopen": round(rev_keyed / nr, 3), "revert_keyed_base": 0.0,
            "false_reopen_rate": round(false_reopen / nf, 3)}


def main():
    import os as _hb_os, sys as _hb_sys
    _hb_sys.path.insert(0, _hb_os.path.dirname(_hb_os.path.abspath(__file__)))
    import _heartbeat  # noqa: E402  (phase and elapsed time on stderr every 30 s)
    _heartbeat.start(__file__)
    rows = []
    for s in range(SEEDS):
        _heartbeat.phase("seed %d of %d" % (s + 1, SEEDS))
        rows.append(run(s))
    def mean(k): return round(sum(r[k] for r in rows) / len(rows), 3)
    agg = {k: mean(k) for k in ("wm_caught_base", "wm_caught_reopen", "revert_keyed_base",
                                "revert_keyed_reopen", "false_reopen_rate")}
    print("=== read-path reopen on the REAL inspeximus store (means over seeds) ===")
    print(f"  confident wrong-merge CAUGHT:   baseline {agg['wm_caught_base']:.0%}  ->  observe() {agg['wm_caught_reopen']:.0%}")
    print(f"  value-obscuring revert KEYED:   baseline {agg['revert_keyed_base']:.0%}  ->  observe() {agg['revert_keyed_reopen']:.0%}")
    print(f"  false-reopen on a single benign restatement (cost): {agg['false_reopen_rate']:.1%}")
    OUT.write_text(json.dumps({"ok": True, "store": "real inspeximus", "E": E, "agg": agg, "rows": rows}, indent=1),
                   encoding="utf-8")


main()
