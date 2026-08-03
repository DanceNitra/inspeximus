# -*- coding: utf-8 -*-
"""Measure whether the deterministic, zero-LLM keyer binds a real conversational correction chain.

Two numbers, both required, because either alone is meaningless:

  BIND RATE       -- of the correction chains in fixture.py, how many collapse to ONE active record
                     holding the FINAL value. This is the product promise: corrections stick.
  FALSE-BIND RATE -- of the unrelated pairs, how many got keyed together anyway. A keyer that binds
                     everything scores a perfect bind rate and silently destroys unrelated records, so
                     without this number the bind rate cannot fail.

Run:  python benchmarks/chain_binding/probe.py
Exit: 0 iff the negative control is clean (0 false binds). A false bind is data loss, not a tuning miss.
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

from fixture import CHAINS, KNOWN_UNFIXED, NEGATIVES, PROSE     # noqa: E402
from inspeximus.core import Inspeximus, regex_extractor   # noqa: E402


def _store():
    m = Inspeximus(path=None)
    m.extractor = regex_extractor
    return m


def keys_of(turns):
    out = []
    for t in turns:
        ex = regex_extractor(t)
        out.append(ex[0] if ex else None)
    return out


def chain_result(turns, final_value):
    """(keys_agree, store_collapsed, active_texts). store_collapsed is the product-level truth:
    exactly one active record and it carries the final value."""
    ks = keys_of(turns)
    agree = bool(ks[0]) and all(k == ks[0] for k in ks)
    m = _store()
    for t in turns:
        m.remember(t)
    active = [r for r in m.items if r.get("status") == "active"]
    collapsed = len(active) == 1 and final_value.lower() in (active[0].get("text") or "").lower()
    return agree, collapsed, ks, [r.get("text") for r in active]


def negative_result(a, b):
    """(false_bound_by_key, false_bound_in_store, key_a, key_b)."""
    ka = regex_extractor(a)
    kb = regex_extractor(b)
    ka = ka[0] if ka else None
    kb = kb[0] if kb else None
    by_key = bool(ka) and ka == kb
    m = _store()
    m.remember(a)
    m.remember(b)
    in_store = sum(1 for r in m.items if r.get("status") == "active") < 2
    return by_key, in_store, ka, kb


def main():
    print("=" * 100)
    print("CORRECTION CHAINS  (must bind: one active record holding the final value)")
    print("=" * 100)
    bound = collapsed_n = 0
    unsolved = []
    for cid, shape, turns, final in CHAINS:
        agree, collapsed, ks, active = chain_result(turns, final)
        bound += agree
        collapsed_n += collapsed
        mark = "BIND  " if collapsed else "MISS  "
        print(f"{mark} {cid:16} keys={ks}")
        if not collapsed:
            unsolved.append((cid, shape))
            print(f"{'':7}{'':16} shape: {shape}")
            print(f"{'':7}{'':16} active after ingest ({len(active)}): {active}")
    n = len(CHAINS)

    print()
    print("=" * 100)
    print("NEGATIVE CONTROL  (must NOT bind: two active records, distinct or absent keys)")
    print("=" * 100)
    false_key = false_store = 0
    for pid, why, a, b in NEGATIVES:
        by_key, in_store, ka, kb = negative_result(a, b)
        bad = by_key or in_store
        false_key += by_key
        false_store += in_store
        print(f"{'FALSE ' if bad else 'ok    '} {pid:24} {ka!r} | {kb!r}")
        if bad:
            print(f"{'':7}{'':24} {why}")
            print(f"{'':7}{'':24} A: {a}")
            print(f"{'':7}{'':24} B: {b}")
    nn = len(NEGATIVES)

    print()
    print("=" * 100)
    print("KNOWN UNFIXED  (pre-existing, unchanged by this work, reported so it stays visible)")
    print("=" * 100)
    ku_bad = 0
    for pid, why, a, b in KNOWN_UNFIXED:
        by_key, in_store, ka, kb = negative_result(a, b)
        ku_bad += bool(by_key or in_store)
        print(f"{'BINDS ' if (by_key or in_store) else 'ok    '} {pid:28} {ka!r} | {kb!r}")
        print(f"{'':7}{'':28} {why}")

    print()
    print("=" * 100)
    print("ANTI-GREED CONTROL  (non-declarative prose: keying must stay rare, supersession must stay 0)")
    print("=" * 100)
    keyed = [(s, regex_extractor(s)) for s in PROSE]
    keyed = [(s, ex) for s, ex in keyed if ex]
    mp = _store()
    for s in PROSE:
        mp.remember(s)
    sup = [r for r in mp.items if r.get("status") != "active"]
    for s, ex in keyed:
        print(f"  keyed  {ex[0]!r:34} <- {s}")
    for r in sup:
        print(f"  RETIRED {r.get('text')!r}")
    print(f"  keyed {len(keyed)}/{len(PROSE)} ({len(keyed) / len(PROSE):.1%})   "
          f"spurious supersessions {len(sup)}")

    # Turn-level detail: of every turn that is NOT the first in its chain, how many landed on the key the
    # chain's first turn established? A chain can be 2 of 3 bound; the all-or-nothing headline hides that.
    corr_turns = corr_bound = 0
    for _cid, _shape, turns, _final in CHAINS:
        k0 = keys_of(turns)
        for k in k0[1:]:
            corr_turns += 1
            corr_bound += bool(k0[0]) and k == k0[0]

    print()
    print("=" * 100)
    print(f"BIND RATE        {collapsed_n}/{n}   ({collapsed_n / n:.1%})   "
          f"[key agreement alone: {bound}/{n}]")
    print(f"  correction turns bound to the chain key: {corr_bound}/{corr_turns} "
          f"({corr_bound / corr_turns:.1%})")
    print(f"FALSE-BIND RATE  {false_store}/{nn}  ({false_store / nn:.1%})  "
          f"[key collision alone: {false_key}/{nn}]")
    print(f"PROSE            keyed {len(keyed)}/{len(PROSE)} ({len(keyed) / len(PROSE):.1%}), "
          f"spurious supersessions {len(sup)}")
    print(f"KNOWN UNFIXED    {ku_bad}/{len(KNOWN_UNFIXED)} still bind (pre-existing bare-copula path, "
          f"identical before and after)")
    if unsolved:
        print("\nUNSOLVED CHAIN SHAPES:")
        for cid, shape in unsolved:
            print(f"  - {cid}: {shape}")
    print("=" * 100)
    return 1 if (false_store or false_key or sup) else 0


if __name__ == "__main__":
    sys.exit(main())
