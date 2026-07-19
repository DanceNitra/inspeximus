"""Probe: read-time conflict resolver (recall(resolve_conflicts=True)) — the un-keyed echo hole.

Write-time guards (keyed supersession, echo_guard) cannot reach an UN-KEYED re-assertion of a retired
value: it lands as an independent record and can out-rank the correction. This probe measures:
  1. the FAILURE EXISTS without the flag (un-keyed echo out-ranks the correction) — else the test is
     a demonstration, not a test;
  2. resolve_conflicts=True serves the correction, demotes the echo, and annotates `resolved_over`;
  3. value-birth semantics: a genuinely NEW value (honest update) still wins under the resolver;
  4. superseded-birth inheritance: after a KEYED correction, an un-keyed echo of the retired value is
     demoted (its birth = the retired row's birth);
  5. unrelated facts are untouched (no false clustering across subjects);
  6. determinism (two identical calls, identical order);
  7. the documented limit: a deliberate un-keyed reversal to an older value loses (use keys+reaffirm).
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from mnemo.mnemo import Mnemo  # noqa: E402

PASS, FAIL = 0, 0


def check(name, ok):
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(("PASS " if ok else "FAIL ") + name)


def build_echo_store(d, name):
    """V1 (old) -> V2 (correction, un-keyed) -> echo of V1 (newest). All un-keyed."""
    m = Mnemo(path=os.path.join(d, name))
    m.remember("The API rate limit is 100 rps")
    time.sleep(0.01)
    m.remember("The API rate limit is 500 rps")            # the correction
    time.sleep(0.01)
    m.remember("The API rate limit is 100 rps")            # stale echo, NEWEST write
    return m


def main():
    d = tempfile.mkdtemp()
    q = "api rate limit"

    # 1. the failure exists without the flag
    m = build_echo_store(d, "a.json")
    top_off = (m.recall(q, k=1) or [{}])[0].get("text", "")
    check("without resolver the echo wins top-1 (failure is real)", "100" in top_off)

    # 2. resolver serves the correction + annotates
    hits = m.recall(q, k=2, resolve_conflicts=True)
    check("resolver serves the correction top-1", "500" in hits[0].get("text", ""))
    check("winner carries resolved_over ids", len(hits[0].get("resolved_over") or []) >= 1)

    # 3. honest update still wins
    m2 = Mnemo(path=os.path.join(d, "b.json"))
    m2.remember("The API rate limit is 100 rps")
    time.sleep(0.01)
    m2.remember("The API rate limit is 900 rps")           # genuinely new value, newest birth
    h = m2.recall(q, k=1, resolve_conflicts=True)
    check("honest newest value wins under resolver", "900" in h[0].get("text", ""))

    # 4. keyed supersession + later un-keyed echo
    m3 = Mnemo(path=os.path.join(d, "c.json"))
    m3.remember("The API rate limit is 100 rps", key="rate")
    time.sleep(0.01)
    m3.remember("The API rate limit is 500 rps", key="rate")
    time.sleep(0.01)
    m3.remember("The API rate limit is 100 rps")           # un-keyed echo of the RETIRED value
    h = m3.recall(q, k=1, resolve_conflicts=True)
    check("echo of a keyed-superseded value is demoted", "500" in h[0].get("text", ""))

    # 5. related-but-distinct subjects untouched (shared words, different subjects -> below the 0.6 bar)
    m4 = Mnemo(path=os.path.join(d, "d.json"))
    m4.remember("The staging database host is db-stage.internal")
    time.sleep(0.01)
    m4.remember("The production database host is db-prod.internal")
    h = m4.recall("database host", k=2, resolve_conflicts=True)
    texts = " | ".join(x.get("text", "") for x in h)
    check("distinct subjects both survive", "db-stage" in texts and "db-prod" in texts)

    # 6. determinism
    r1 = [x["id"] for x in m.recall(q, k=5, resolve_conflicts=True)]
    r2 = [x["id"] for x in m.recall(q, k=5, resolve_conflicts=True)]
    check("deterministic across calls", r1 == r2)

    # 7. documented limit: un-keyed reversal loses (needs keys + reaffirm)
    m5 = Mnemo(path=os.path.join(d, "e.json"))
    m5.remember("The API rate limit is 100 rps")
    time.sleep(0.01)
    m5.remember("The API rate limit is 500 rps")
    time.sleep(0.01)
    m5.remember("The API rate limit is 100 rps")           # a HUMAN meant this as a real reversal
    h = m5.recall(q, k=1, resolve_conflicts=True)
    check("documented limit: un-keyed reversal reads as echo (500 stays)", "500" in h[0].get("text", ""))

    # default OFF is byte-identical legacy
    h_legacy = m.recall(q, k=1)
    check("default OFF unchanged (echo still wins without the flag)", "100" in h_legacy[0].get("text", ""))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
