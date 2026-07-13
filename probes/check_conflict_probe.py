"""check_conflict_probe.py — write-time conflict detection that separates a contradiction from a duplicate.

The whole point (and the thing a cosine-similarity gate CANNOT do): a restated identical fact is a
duplicate and must NOT flag; a contradicted value is a conflict and MUST flag — even though the
contradiction is often MORE embedding-similar to the original than a rephrase (AUROC ~0.59). check_conflict
keys on value/negation clash (and managed-key value change), not on similarity, so it gets this right, and
takes a pluggable `incompatible` judge for the semantic case the deterministic default can't reach.

Pre-registered checks:
  A keyed value change flagged   key set, new object differs -> kind=keyed_value_change
  B keyed same value NOT flagged re-affirming the same object on a key is not a conflict
  C numeric update flagged        "retry limit is 5" then "retry limit is 12" -> clash
  D negation flip flagged         "server is up" then "server is not up" -> clash
  E DUPLICATE NOT flagged         restating an identical fact is a dup, not a conflict (the crux)
  F unrelated NOT flagged         a fact with no similarity to anything active -> clean
  G semantic miss + pluggable fix "lives in Berlin" vs "Munich": default misses; an LLM-style judge catches
  H read-only                     check_conflict does not write (store size unchanged)
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mnemo import Mnemo


def main():
    ok = {}

    # A / B — managed-key value change
    m = Mnemo(path=None)
    m.remember("prod region is frankfurt", key="cfg::region", object="frankfurt")
    ok["A keyed value change flagged"] = any(c["kind"] == "keyed_value_change"
        for c in m.check_conflict("prod region is ohio", key="cfg::region", object="ohio"))
    ok["B keyed same value NOT flagged"] = m.check_conflict("prod region is frankfurt",
        key="cfg::region", object="frankfurt") == []

    # C numeric update
    m = Mnemo(path=None)
    m.remember("the retry limit is 5 attempts")
    ok["C numeric update flagged"] = len(m.check_conflict("the retry limit is 12 attempts")) >= 1

    # D negation flip
    m = Mnemo(path=None)
    m.remember("the staging server is up")
    ok["D negation flip flagged"] = len(m.check_conflict("the staging server is not up")) >= 1

    # E DUPLICATE must NOT flag (the crux: a similarity gate would; clash-signal does not)
    m = Mnemo(path=None)
    m.remember("cats are mammals")
    ok["E duplicate NOT flagged"] = m.check_conflict("cats are mammals") == []

    # F unrelated -> clean
    m = Mnemo(path=None)
    m.remember("the retry limit is 5 attempts")
    ok["F unrelated NOT flagged"] = m.check_conflict("the sky is blue today") == []

    # G semantic contradiction: deterministic default misses; pluggable judge catches
    m = Mnemo(path=None)
    m.remember("alice lives in berlin")
    default_miss = m.check_conflict("alice lives in munich") == []
    # a toy "semantic" judge: same subject+relation, different city token
    cities = {"berlin", "munich", "paris", "ohio", "frankfurt"}
    def judge(a, b):
        ca = {w for w in a.lower().split() if w in cities}
        cb = {w for w in b.lower().split() if w in cities}
        return bool(ca) and bool(cb) and ca != cb
    judge_catch = len(m.check_conflict("alice lives in munich", incompatible=judge)) >= 1
    ok["G semantic: default misses, judge catches"] = default_miss and judge_catch

    # H read-only
    m = Mnemo(path=None)
    m.remember("the retry limit is 5 attempts")
    n0 = len(m.items)
    m.check_conflict("the retry limit is 12 attempts")
    ok["H read-only (no write)"] = len(m.items) == n0

    print("=" * 66)
    print("check_conflict — write-time contradiction-vs-duplicate detection")
    print("=" * 66)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("-" * 66)
    print("RECEIPT:", "VALID — all checks hold" if all(ok.values()) else "INVALID — do not ship")
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
