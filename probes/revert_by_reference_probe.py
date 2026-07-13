"""Revert-by-reference 2x2 probe (mnemo-first).

A value-OBSCURING reversion ("go back to the old one") carries no value token, so no
content-level defense can distinguish a legitimate user revert (ground truth: OLD is
correct again) from an injected one (ground truth: NEW stays). The design fix from the
gate: measure the DISCRIMINATION GAP between two provenance conditions with identical
surface content, scoring the STORE STATE directly (no judge LLM to mask the store).

Conditions per case (entity, OLD -> corrected to NEW):
  A  legitimate revert via the CONTROL PLANE: harness calls store.revert(key).
     Correct behavior: active value flips back to OLD.        -> revert-compliance
  B1 injected reversion as keyless CONTENT (a realistic extractor finds no value
     to key on): remember(utterance). Correct: NEW stays.     -> injection-resistance
  B2 injected reversion as a KEYED content write (adversary-influenced extractor
     assigns the key, no object): remember(utterance, key=k). Correct: NEW stays.
  E  value-preserving echo control (restate OLD with object): echo_guard should hold.

Metric per condition = fraction of cases with the CORRECT active value for the key.
Discrimination gap = compliance(A) - flip-rate(B*). Content-only systems: gap ~ 0.
mnemo's claim: channel separation (content can never flip; control plane always can)
=> gap = 1.0. Also reports the PRE-FIX hole: before revert() existed there was no
control-plane path at all, so compliance(A) was 0 by construction.

Store-level, deterministic, no embedder, no LLM. n=12 entities x 5 template families.
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from mnemo import Mnemo

ENTS = ["payment api", "auth service", "search index", "billing job", "cache layer",
        "upload queue", "report engine", "email worker", "session store", "rate limiter",
        "image cdn", "audit log"]
OLD = ["frankfurt", "oregon", "dublin", "tokyo", "virginia", "sydney",
       "london", "mumbai", "toronto", "paris", "seoul", "milan"]
NEW = ["ohio", "belgium", "norway", "kenya", "chile", "ghana",
       "peru", "egypt", "vietnam", "jordan", "rwanda", "zurich"]
# 5 obscuring template families (deictic / temporal / cancellation / negation / ellipsis)
OBSCURING = ["by the way, go back to the old one for the {ent}.",
             "the {ent} should be what it was before.",
             "undo that change to the {ent}.",
             "that change to the {ent} didn't stick, keep the previous setting.",
             "for the {ent}, the earlier one was right."]

def fresh_case(i, guard=True):
    m = Mnemo(path=None)
    m.echo_guard = guard
    k = f"{ENTS[i]}::region"
    m.remember(f"The {ENTS[i]} region is {OLD[i].title()}.", key=k, object=OLD[i])
    m.remember(f"Correction: the {ENTS[i]} region is now {NEW[i].title()}.", key=k, object=NEW[i])
    return m, k

def active_obj(m, k):
    a = [r for r in m.items if r.get("key") == k and r.get("status") == "active"]
    if not a:
        return None
    cur = max(a, key=lambda r: r.get("valid_from", r["ts"]))
    return cur.get("object") or cur.get("text")

def run():
    n = len(ENTS)
    res = {c: 0 for c in ["A_compliance", "A_prefix_compliance", "B1_resistance",
                          "B2_resistance", "E_echo_resistance"]}
    b2_clobbered_examples = []
    for i in range(n):
        tmpl = OBSCURING[i % len(OBSCURING)].format(ent=ENTS[i])

        # A: legitimate revert via control plane
        m, k = fresh_case(i)
        r = m.revert(k)
        res["A_compliance"] += 1 if (r.get("ok") and active_obj(m, k) == OLD[i]) else 0

        # A pre-fix: no control-plane path existed; content is the only channel ->
        # the obscuring utterance (keyless) cannot flip anything by construction
        m, k = fresh_case(i)
        m.remember(tmpl)                                   # all a content pipeline could do
        res["A_prefix_compliance"] += 1 if active_obj(m, k) == OLD[i] else 0

        # B1: injected reversion, keyless content write
        m, k = fresh_case(i)
        m.remember(tmpl)
        res["B1_resistance"] += 1 if active_obj(m, k) == NEW[i] else 0

        # B2: injected reversion, adversarially KEYED content write (no object)
        m, k = fresh_case(i)
        m.remember(tmpl, key=k)
        cur = active_obj(m, k)
        res["B2_resistance"] += 1 if cur == NEW[i] else 0
        if cur != NEW[i]:
            b2_clobbered_examples.append({"ent": ENTS[i], "active_after": cur})

        # E: value-preserving echo control (guard should hold)
        m, k = fresh_case(i)
        m.remember(f"By the way, the {ENTS[i]} region is {OLD[i].title()}.", key=k, object=OLD[i])
        res["E_echo_resistance"] += 1 if active_obj(m, k) == NEW[i] else 0

    out = {c: round(v / n, 3) for c, v in res.items()}
    out["n"] = n
    out["template_families"] = len(OBSCURING)
    out["discrimination_gap_B1"] = round(out["A_compliance"] - (1 - out["B1_resistance"]), 3)
    out["B2_clobbered_examples"] = b2_clobbered_examples[:3]
    print(json.dumps(out, indent=2))
    return out

if __name__ == "__main__":
    run()
