"""PRE-FLIGHT GATE for any probe whose number is going to be reported.

Ported into inspeximus from the research harness, because the probes that produce our published numbers
live here. Each check is written from a specific error that re-reading the code would not have caught:

  CONTROL       the unmanipulated arm must reproduce a known baseline
  MANIPULATION  two-sided: what you meant to change changed, AND nothing else did (incl. record COUNT)
  SPREAD        a range needs >= 20 trials; the mean converges fast and the range does not
  CAN FAIL      feed it something it MUST reject
  DENOMINATOR   how many units were actually scored

The two-sided manipulation check and the trial floor came from an outside review (jacksonxly, 2026-07-27),
after we published an under-sampled spread in a post about measurement discipline and nearly confirmed a
hypothesis with a patch that had destroyed the arm it was meant to isolate.
"""

from __future__ import annotations


class GateFailed(AssertionError):
    pass


class ProbeGate:
    def __init__(self, name: str, operating_point: dict):
        if not operating_point:
            raise GateFailed("state the operating point BEFORE measuring, not after")
        self.name = name
        self.op = operating_point
        self.checks: list[tuple[str, bool, str]] = []

    def _add(self, label, ok, detail=""):
        self.checks.append((label, bool(ok), detail))
        return bool(ok)

    # 1 ── the control must reproduce something already known
    def control(self, label, value, expect_range):
        lo, hi = min(expect_range), max(expect_range)
        pad = 0.02 * max(abs(lo), abs(hi), 1e-9)
        ok = (lo - pad) <= value <= (hi + pad)
        return self._add(f"CONTROL: {label}", ok,
                         f"measured {value}, baseline {lo}..{hi} (+/-2%). "
                         "If the control does not reproduce the baseline, the manipulation says nothing.")

    # 2 ── the manipulation must be verified to have actually happened
    def manipulation_landed(self, label, verify):
        try:
            ok = bool(verify())
        except Exception as e:
            ok, label = False, f"{label} [{type(e).__name__}: {e}]"
        return self._add(f"LANDED: {label}", ok,
                         "Assert the change took effect in the object under test. A patch that silently "
                         "did nothing -- or destroyed the arm -- produces a clean-looking confirmation.")

    # 2b ── ...and NOTHING ELSE may have changed. Two-sided, because one-sided is how the id patch passed.
    def manipulation(self, label, before, after, expect_changed, ignore=(), key_fn=None):
        """Diff EVERY key across two record sets: the fields you meant to move must have moved, and no
        others may have.

        `manipulation_landed` above asks only "did my change take effect". The uuid4 patch DID take
        effect -- every record really did get the id I wrote -- it just landed wider than intended, and
        `f"{i:032x}"[:10]` is "0000000000" for every small i, so the arm collapsed and the spread went to
        zero. A one-sided check calls that a success. Credit: jacksonxly, who pointed out that the
        all-keys diff we already had between two BUILDS is the instrument, and that it only needed
        pointing before-and-after the patch instead.

        Cardinality is part of the diff. With every id identical the store deduplicated, so the honest
        first symptom was 101,874 records becoming a handful -- a field-by-field comparison that silently
        zipped the two lists would have missed it entirely.

        `expect_changed` is the set of keys the manipulation is allowed to touch. Anything else that moved
        is reported by name and count, which is exactly what "landed wider than intended" looks like.
        """
        before, after = list(before or []), list(after or [])
        detail = []
        if len(before) != len(after):
            return self._add(f"MANIPULATION: {label}", False,
                             f"record count changed {len(before)} -> {len(after)}: the manipulation "
                             f"changed how many records exist, which no field-level diff would show. "
                             f"(All ids identical is the classic cause -- the store deduplicates.)")
        if not before:
            return self._add(f"MANIPULATION: {label}", False,
                             "nothing to diff: an empty comparison is not a clean one")

        pairs = (zip(sorted(before, key=key_fn), sorted(after, key=key_fn)) if key_fn
                 else zip(before, after))          # positional by default: the id itself may be the subject
        keys = sorted({k for r in before + after for k in r})
        moved = {k: 0 for k in keys}
        for b, a in pairs:
            for k in keys:
                if b.get(k) != a.get(k):
                    moved[k] += 1

        expected, ignored = set(expect_changed), set(ignore)
        did_not_move = sorted(k for k in expected if moved.get(k, 0) == 0)
        unexpected = sorted(k for k, n in moved.items()
                            if n and k not in expected and k not in ignored)
        if did_not_move:
            detail.append(f"expected to change but did NOT: {did_not_move}")
        if unexpected:
            detail.append("changed but was NOT part of the manipulation: "
                          + ", ".join(f"{k} ({moved[k]}/{len(before)})" for k in unexpected))
        ok = not did_not_move and not unexpected
        return self._add(f"MANIPULATION: {label}", ok,
                         "; ".join(detail) or
                         f"exactly {sorted(k for k in expected if moved.get(k))} moved, "
                         f"across {len(before)} records; no other key differs")

    # 2c ── a SPREAD needs far more trials than a mean does
    def spread(self, label, values, min_trials: int = 20):
        """A spread quoted from five runs is run-to-run noise wearing a result's clothes.

        Measured on our own data: five trials gave 0.4067-0.4200 (spread 0.0133); the same operating point
        over ~25 trials gave 0.380-0.420 -- 0.04, three times as wide. The mean had long since settled.
        Credit again to jacksonxly: "spread converges a lot slower than the mean", which is the reason a
        post about measurement discipline shipped an under-sampled number.
        """
        vals = list(values or [])
        ok = len(vals) >= min_trials
        rng = (max(vals) - min(vals)) if vals else 0.0
        return self._add(f"SPREAD: {label}", ok,
                         f"{len(vals)} trial(s), observed spread {rng:.4f}. A spread needs >= {min_trials} "
                         f"trials: the mean converges quickly and the RANGE does not, so a range from a "
                         f"handful of runs understates itself and reads like a tight result.")

    # 3 ── the measurement must be able to come out the other way
    def can_fail(self, label, negative_control):
        try:
            ok = bool(negative_control())
        except Exception as e:
            ok, label = False, f"{label} [{type(e).__name__}: {e}]"
        return self._add(f"CAN FAIL: {label}", ok,
                         "Feed it something it MUST reject. A check that cannot fail is a demonstration.")

    # 4 ── the denominator must be real and stated
    def denominator(self, n_scored: int, n_total: int, min_frac: float = 0.5):
        ok = n_scored > 0 and (n_scored / max(n_total, 1)) >= min_frac
        return self._add("DENOMINATOR", ok,
                         f"{n_scored}/{n_total} units scored. An empty or tiny denominator must not read "
                         "as a pass, and the number is meaningless without it.")

    def report(self, result: dict, strict: bool = True) -> dict:
        failed = [(l, d) for l, ok, d in self.checks if not ok]
        lines = [f"PROBE GATE: {self.name}", f"  operating point: {self.op}"]
        for label, ok, _ in self.checks:
            lines.append(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        if failed:
            lines.append("  --> NOT REPORTABLE:")
            lines += [f"      {l}\n        {d}" for l, d in failed]
        print("\n".join(lines))
        if failed and strict:
            raise GateFailed(f"{len(failed)} gate check(s) failed; the number is not reportable")
        return {"gate": self.name, "operating_point": self.op, "result": result,
                "checks": [{"label": l, "passed": ok} for l, ok, _ in self.checks]}


def selftest():
    """Every check must be able to fail, or this file is theatre."""
    g = ProbeGate("t", {"k": 1})
    assert not g.control("c", 0.10, (0.40, 0.42)), "control must fail when it misses the baseline"
    assert g.control("c", 0.41, (0.40, 0.42))
    assert not g.manipulation_landed("m", lambda: False)
    assert not g.manipulation_landed("m", lambda: 1 / 0)          # an exception is a FAIL, not a skip
    assert not g.can_fail("n", lambda: False)
    assert not g.denominator(0, 150), "an empty denominator must not read as a pass"
    assert not g.denominator(10, 150)
    assert g.denominator(150, 150)

    try:
        ProbeGate("no operating point", {})
    except GateFailed:
        pass
    else:
        raise AssertionError("an unstated operating point must be refused")

    g2 = ProbeGate("t2", {"k": 1})
    g2.control("c", 0.10, (0.40, 0.42))
    try:
        g2.report({"x": 1})
    except GateFailed:
        print("selftest OK - every check can fail, and a failed gate blocks the number")
    else:
        raise AssertionError("report() must refuse to pass a failed gate")


if __name__ == "__main__":
    selftest()
