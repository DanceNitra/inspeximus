"""The echo/reaffirm policy panel — the numbers `route()`'s docstring states, re-measured on today's code.

`inspeximus/core.py` tells a reader, in the shipped docstring for `_supersede_by_key`:

    "safe" (default) ... Cost: a legitimate unmarked reaffirm is refused (measured 1.00 echo-blocked /
    0.00 reaffirm-honored).
    "trusting": treat as a reaffirm — always restores (0.00 echo-blocked / 1.00 honored).

Those numbers came from `echo_attack_probe_v2.py`, which cannot run in this repository: it imports a
sibling module that was never committed, and needs a MemBench fixture plus LLM-generated paraphrases. So
a claim printed in the source rested on evidence nobody could execute.

This probe measures the same two rates deterministically, with no dataset, no network and no LLM, because
the property is about OUR policy and not about a benchmark:

  ECHO      an unmarked restatement of a value that was already superseded (a stale or hostile replay).
            "echo-blocked" = it did NOT resurrect the old value.
  REAFFIRM  an unmarked restatement of that same old value which the user genuinely MEANS.
            "reaffirm-honored" = it DID restore the old value.

The two are byte-identical by construction. That is the whole point: no classifier can separate them from
text, so a policy can only choose which failure it accepts. A panel where `safe` scored anything other
than 1.00/0.00 would mean it had started guessing.

RUN:  python probes/echo_policy_panel.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402

KEY = "payout::wallet"
OLD, NEW = "0xAAA", "0xBBB"

#: Several surface forms of the same restatement, so a rate is over more than one phrasing.
RESTATEMENTS = [
    "the payout wallet is 0xAAA",
    "payout wallet: 0xAAA",
    "wallet for payouts is 0xAAA",
    "set the payout wallet to 0xAAA",
    "0xAAA is the payout wallet",
]

#: A preceding turn that shows change-awareness — what the `context` policy is allowed to rely on, and
#: what an attacker who can write two turns can forge.
AWARE_CONTEXT = "we switched the payout wallet to 0xBBB last week, but that was wrong"


def _fresh():
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"))
    m.echo_guard = True
    m.extractor = lambda t: (KEY, OLD if OLD.lower() in t.lower() else
                             (NEW if NEW.lower() in t.lower() else None))
    m.remember(f"the payout wallet is {OLD}", key=KEY, object=OLD)
    m.remember(f"the payout wallet is {NEW}", key=KEY, object=NEW)
    return m


def _current(m):
    active = [r for r in m.items if r.get("key") == KEY and r.get("status") == "active"]
    return active[0].get("object") if len(active) == 1 else [r.get("object") for r in active]


def _restored(policy, text, context=None):
    """Did this restatement put the OLD value back?"""
    m = _fresh()
    assert _current(m) == NEW, "fixture must start with the correction in force"
    m.route(text, policy=policy, context=context)
    return _current(m) == OLD


def _restored_default(text):
    """Route with NO policy argument at all, exactly as an ordinary caller would."""
    m = _fresh()
    m.route(text)
    return _current(m) == OLD


def main() -> int:
    rows = []
    for policy in ("safe", "context", "trusting"):
        # ECHO: no context at all -- a bare replay of a superseded value.
        echo_blocked = sum(0 if _restored(policy, t) else 1 for t in RESTATEMENTS) / len(RESTATEMENTS)
        # REAFFIRM: the user means it, and (for `context`) the preceding turn shows they know it changed.
        honored = sum(1 if _restored(policy, t, context=AWARE_CONTEXT) else 0
                      for t in RESTATEMENTS) / len(RESTATEMENTS)
        rows.append({"policy": policy, "echo_blocked": round(echo_blocked, 2),
                     "reaffirm_honored": round(honored, 2)})

    # The DEFAULT matters as much as the named policies: the docstring says "safe (default)", and every
    # caller who never passes `policy=` gets it. Measuring only the explicit policies leaves a flipped
    # default invisible -- a mutation of route()'s signature proved exactly that.
    default_blocked = sum(0 if _restored_default(t) else 1 for t in RESTATEMENTS) / len(RESTATEMENTS)
    rows.append({"policy": "(default)", "echo_blocked": round(default_blocked, 2),
                 "reaffirm_honored": None})

    print(f"{'policy':10s} {'echo-blocked':>14s} {'reaffirm-honored':>18s}")
    for r in rows:
        honored = "n/a" if r["reaffirm_honored"] is None else f"{r['reaffirm_honored']:.2f}"
        print(f"{r['policy']:10s} {r['echo_blocked']:14.2f} {honored:>18s}")

    by = {r["policy"]: r for r in rows}
    print()
    print("The docstring in inspeximus/core.py states, for the SAME two rates:")
    print("   safe      1.00 echo-blocked / 0.00 reaffirm-honored")
    print("   trusting  0.00 echo-blocked / 1.00 reaffirm-honored")

    problems = []
    if by["safe"]["echo_blocked"] != 1.00:
        problems.append(f"safe blocks {by['safe']['echo_blocked']:.2f} of echoes, the docstring says 1.00")
    if by["safe"]["reaffirm_honored"] != 0.00:
        problems.append(f"safe honors {by['safe']['reaffirm_honored']:.2f} of reaffirms, "
                        f"the docstring says 0.00")
    if by["trusting"]["reaffirm_honored"] != 1.00:
        problems.append(f"trusting honors {by['trusting']['reaffirm_honored']:.2f}, "
                        f"the docstring says 1.00")
    if by["(default)"]["echo_blocked"] != 1.00:
        problems.append(f"the DEFAULT policy blocks {by['(default)']['echo_blocked']:.2f} of echoes; the "
                        f"docstring says safe is the default, so it must match safe's 1.00")

    print()
    if problems:
        print("MISMATCH between the shipped docstring and today's behaviour:")
        for p in problems:
            print("   -", p)
    else:
        print("The shipped numbers reproduce on today's code.")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "echo_policy_panel_result.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"rows": rows, "problems": problems}, fh, indent=2)
    print(f"\nwrote {os.path.basename(out)}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
