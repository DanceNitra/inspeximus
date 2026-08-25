"""When a corrected-away value is said again, does it win? Ours must not. Cognee's does, by design.

WHY THIS EXISTS. Cognee shipped deterministic no-LLM supersession (PR #4084, 2026-07-28) and a
SHA-256-chained audit ledger (PR #4476, 2026-08-14), and renamed its API to remember / recall /
improve / forget. Our generic "we are deterministic" pitch stopped separating us that week. What
still separates us is narrower, and their own source states it:

    cognee/modules/graph/utils/temporal_conflict_resolver.py:76
    winners[key] = max(members, key=lambda i: _recency_key(i, edges[i][3]))

Recency wins. A value a correction retired, said again later, becomes the most recent and takes the
key back. Their open issue #4030 is that behaviour reported in the wild.

Before claiming we are different, this measures it on us, because a pitch without a receipt is the
thing this repo keeps catching itself doing.

WHAT THE FIRST VERSION OF THIS PROBE GOT WRONG, kept because it is the reason to run one. I assumed
a keyed re-statement of the retired value SHOULD win, as a deliberate re-assert, and wrote the arm to
require it. Both of those arms failed, and the product was right rather than the probe: a plain
keyed restatement does NOT revive a retired value, and the deliberate path back is a named argument,
`remember(..., reaffirm=True)`. core.py says so where the guard lives -- "the guard cannot
un-supersede on its own". I was one commit from publishing a claim that was backwards about our own
mechanism, in a section written to sharpen it.

So the contract measured here is:

  1. a correction wins;
  2. RESTATING the retired value under the same key does not take the key back -- this is the arm
     Cognee's recency rule fails, and the one the pitch rests on;
  3. `reaffirm=True` does take it back, so arm 2 is a guard rather than a frozen store;
  4. AND THE LIMIT: the guard is KEYED. The same sentence written with NO key is a separate fact,
     outside the guard, and it can outrank the current value. Re-ingesting a stale document without
     keys has exactly that shape. Measured here so it is a documented limit rather than a surprise.

stdlib only, no network, no LLM. Seconds.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from inspeximus import Inspeximus  # noqa: E402

KEY = "staging-db"
OLD = "The staging database is db-3.internal"
NEW = "The staging database is db-7.internal"
Q = "which staging database"


def top(m) -> str:
    hits = m.recall(Q)
    return hits[0]["text"] if hits else ""


def main() -> int:
    root = tempfile.mkdtemp(prefix="restate_")
    v: dict = {}

    # --- 1. the baseline the README already claims -------------------------------------
    m = Inspeximus(os.path.join(root, "a.json"))
    m.remember(OLD, key=KEY)
    m.remember(NEW, key=KEY)
    v["a_correction_wins"] = top(m) == NEW

    # --- 2. THE ONE THAT SEPARATES US from a recency rule. The retired value is written AGAIN,
    # under the same key, and arrives last. Cognee's resolver takes the key back here; ours does
    # not, because the guard remembers that this exact value was retired FOR this key.
    m.remember(OLD, key=KEY)
    after_restate = top(m)
    v["a_keyed_restatement_does_NOT_revive_it"] = after_restate == NEW

    # --- 3. and the deliberate path back exists, or arm 2 would just be a store that cannot
    # change its mind. core.py: "A genuine reversal back to a superseded value needs
    # remember(..., reaffirm=True) to bypass the guard (the guard cannot un-supersede on its own)."
    m.remember(OLD, key=KEY, reaffirm=True)
    after_reaffirm = top(m)
    v["reaffirm_True_IS_the_way_back"] = after_reaffirm == OLD

    # --- 4. THE LIMIT, measured rather than left for a user to discover. The guard is KEYED. The
    # same sentence written with NO key is a separate fact, outside the guard, and it can outrank
    # the current value. Re-ingesting a stale document without keys is exactly this shape.
    m4 = Inspeximus(os.path.join(root, "d.json"))
    m4.remember(OLD, key=KEY)
    m4.remember(NEW, key=KEY)
    m4.remember(OLD)                      # no key: not a correction, a new record
    unkeyed = top(m4)
    v["DOCUMENTED_LIMIT_an_unkeyed_echo_is_not_guarded"] = unkeyed == OLD

    # --- controls ----------------------------------------------------------------------
    # Arm 2 must not pass by the store simply freezing: arm 3 proves it can still change.
    v["CONTROL_the_store_can_still_change_its_mind"] = after_restate != after_reaffirm
    # And the keyed and unkeyed paths must genuinely differ, or arm 2 measured nothing.
    v["CONTROL_keyed_and_unkeyed_differ"] = after_restate != unkeyed

    for k, ok in v.items():
        print(f"  {'YES' if ok else 'no '}  {k}")
    print(f"\n  after a keyed restatement of the retired value : {after_restate!r}")
    print(f"  after remember(..., reaffirm=True)             : {after_reaffirm!r}")
    print(f"  after an UNKEYED echo (the documented limit)   : {unkeyed!r}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "does_a_restatement_take_the_key_back.result.json")
    json.dump({"probe": os.path.basename(__file__), "verdicts": v,
               "after_restatement": after_restate, "after_reaffirm": after_reaffirm,
               "after_unkeyed_echo": unkeyed},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nwrote {out}")
    return 0 if all(v.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
