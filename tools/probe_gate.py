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

import math
import statistics


class GateFailed(AssertionError):
    pass


def c4(n: int) -> float:
    """E[s]/sigma for n iid normal draws: sqrt(2/(n-1)) * Gamma(n/2)/Gamma((n-1)/2).

    The sample SD is a BIASED estimator of sigma, and at small n the bias is large and KNOWN --
    0.9400 at n=5, 0.9869 at n=20, 0.9896 at n=25 -- so dividing by it de-biases the estimate.
    Verified against 200,000 Monte-Carlo draws per n: measured E[s]/sigma = 0.9390 at n=5 where the
    closed form says 0.9400, and E[(s/c4)]/sigma = 0.9989.

    c4 IS NORMAL THEORY, AND OUR DATA IS NOT NORMAL, so the transfer was measured rather than assumed.
    Against a 400-seed pool of the identity-gate probe (values on a 0.025 grid, 13 distinct levels,
    skew 0.40) a corrected SD from five draws recovers 1.008x the pool's SD. On the gated arm -- skew
    1.13, four distinct levels, about as far from normal as our metrics get -- it recovers 0.971x, so
    roughly 3% of bias survives the correction at n=5. Both beat the range, which recovers 0.437x and
    0.489x of the pool range at the same n.

    This is the estimator the range should have been. Credit: jacksonxly.
    """
    if n < 2:
        return float("nan")
    return math.sqrt(2.0 / (n - 1)) * math.exp(math.lgamma(n / 2.0) - math.lgamma((n - 1) / 2.0))


def _excess_kurtosis(vals) -> float:
    """Sample excess kurtosis, moment form. Returns 0.0 when it is not defined."""
    n = len(vals)
    if n < 4:
        return 0.0
    m = sum(vals) / n
    m2 = sum((x - m) ** 2 for x in vals) / n
    if m2 <= 0:
        return 0.0
    m4 = sum((x - m) ** 4 for x in vals) / n
    return m4 / (m2 * m2) - 3.0


def sd_rel_error(n: int, values=None) -> float:
    """The bias-corrected SD's OWN relative standard error: sqrt(1 - c4(n)^2) / c4(n).

    De-biasing fixes the direction of the error, not its size, and this is the number that says so:
    0.363 at n=5, 0.239 at n=10, 0.163 at n=20, 0.145 at n=25 (closed form; Monte-Carlo agrees to
    three decimals). So a bias-corrected SD from five trials is unbiased and still lands anywhere
    between 0.55x and 1.49x the truth, 10th to 90th percentile. That residual is what the trial floor
    buys, and it is why correcting the estimator does not remove the floor.

    THIS FIGURE IS NORMAL THEORY AND IT UNDERSTATES ON HEAVY TAILS. Var(s) ~ sigma^2 (2 + kappa)/(4n),
    so the truth scales by roughly sqrt(1 + kappa/2) in the excess kurtosis. Measured against 40,000
    subsamples per cell, quoted-over-actual:

        pool (excess kurtosis)      n=5     n=20        so the printed +/- is
        ungated  (-0.26)           1.04x   1.07x        conservative
        normal   ( 0.00)           0.98x   0.99x        right
        gated    (+0.52)           0.84x   0.87x        TOO TIGHT
        lognormal(+~4)             0.58x   0.40x        far too tight

    Too tight is the direction that flatters, which is the whole failure this module exists to stop, so
    passing `values` applies the inflation sqrt(1 + g2/2) from the sample's own excess kurtosis, floored
    at 1.0 so the figure can only ever widen.

    IT IS A PARTIAL FIX AND MUST NOT BE READ AS A CURE. Measured with the same 40,000 subsamples, the
    correction moves quoted-over-actual from 0.84x to 0.90x on the gated arm and 0.58x to 0.66x on the
    lognormal: better, still short. The reason is exactly the reason the range failed. Sample kurtosis is
    a FOURTH-moment statistic, even more extremum-sensitive than a range, and a small sample from a heavy
    tail usually contains no tail point, so it looks light-tailed. The detector fails in the same regime
    as the thing it detects. Two other candidates were measured and are worse: a bootstrap SE of the
    estimate reports 0.69x-0.89x of the truth across these arms (0.47x on the lognormal), and a raw
    normal constant with no inflation is the 0.84x/0.58x row above.

    So: a NON-positive g2 does not clear a sample as normal, it only fails to convict. The honest reading
    of this number is a floor, and the only instrument that reliably widens it is more trials.
    """
    c = c4(n)
    if not (c == c) or c <= 0:                       # NaN guard: n < 2 has no spread to speak of
        return float("inf")
    rel = math.sqrt(max(0.0, 1.0 - c * c)) / c
    if values is not None:
        g2 = _excess_kurtosis(list(values))
        rel *= max(1.0, math.sqrt(max(0.0, 1.0 + g2 / 2.0)))
    return rel


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

    # 2c ── a SPREAD needs far more trials than a mean does, AND a better estimator than the range
    def spread(self, label, values, min_trials: int = 20):
        """A spread quoted from five runs is run-to-run noise wearing a result's clothes.

        Measured on our own data: five trials gave 0.4067-0.4200 (spread 0.0133); the same operating point
        over ~25 trials gave 0.380-0.420 -- 0.04, three times as wide. The mean had long since settled.
        Credit to jacksonxly: "spread converges a lot slower than the mean", which is the reason a post
        about measurement discipline shipped an under-sampled number.

        THE FLOOR WAS THE SYMPTOM; THE RANGE WAS THE DEFECT. A range is an extremum statistic, so its
        expectation only GROWS with n and a small sample can only ever understate it -- which is why an
        under-sampled spread does not read as noisy, it reads as tight. Measured over 200,000 draws of
        five: the range understates its own n=25 expectation in 95.7% of runs. The sample SD has no such
        shape; its small-sample bias is a known constant (c4, 0.9400 at n=5) and dividing it out leaves an
        estimator that is too low in 52.8% of runs -- a coin flip, not a direction. So this check now
        reports the bias-corrected SD, and keeps the range only as a descriptive figure that must never be
        compared between runs of different n.

        The sharpest way to see it, subsampling our own 400-seed pool (true SD 0.0569, true range 0.300):

            n            5       10       20       25       50
            E[s/c4]   0.0574   0.0572   0.0569   0.0570   0.0570   <- settled by five
            E[range]  0.131    0.170    0.203    0.213    0.242    <- still climbing at fifty

        The corrected SD estimates a parameter. The range estimates the sample size.

        The floor survives the fix because it answers the other half. De-biasing corrects the DIRECTION of
        the error, not its SIZE: a corrected SD from five trials still lands between 0.55x and 1.49x the
        truth (10th-90th pct on normals; 0.57x-1.46x measured on the pool above), which `sd_rel_error`
        states outright -- +/-36% at n=5 against +/-16% at n=20. Precision is bought with trials and
        cannot be bought with algebra.

        A BOOTSTRAP INTERVAL WAS THE OTHER CANDIDATE AND IT IS NOT USED HERE, but the honest reason is
        narrower than the one first written down, and the prior art is older than the measurement.
        Schenker 1985 (JASA 80:360-361) already showed that percentile and bias-corrected intervals for a
        normal VARIANCE under-cover badly at small n; that paper is why Efron built BCa in 1987. Our own
        run reproduces it rather than discovers it: at n=5, nominal 95%, percentile covers 62.1% on the
        pool above and 64.0% on a normal control, and BCa does not rescue it (59.7% and 65.1%) -- which
        was Schenker's point.

        What was written down first and is WRONG: "five points cannot be resampled into a tail they never
        sampled" as the mechanism. Measured here, a plain chi-square interval built from those same five
        points covers 94.2% on normals and 96.4% on the pool above. The support is not the binding
        constraint; the non-pivotality of s is. A studentized interval also beats percentile at n=5, and
        we do not quote a figure for it because two of our own implementations disagree by ten points on
        the identical question (83.4% and 73.5% on normals), which is not a number anyone should publish.

        One further caveat on our own numbers: a coverage figure like 62.1% carries pool-to-pool
        variation, not just binomial error, so it should not be read to three digits.
        """
        vals = list(values or [])
        ok = len(vals) >= min_trials
        n = len(vals)
        rng = (max(vals) - min(vals)) if vals else 0.0
        sd_hat = (statistics.stdev(vals) / c4(n)) if n >= 2 else 0.0
        # pass the values: the closed form is NORMAL theory and runs 0.84x too tight on our own skewed
        # arm, which is the flattering direction. See sd_rel_error -- the inflation is partial, not a cure.
        rel = sd_rel_error(n, vals if n >= 4 else None)
        return self._add(f"SPREAD: {label}", ok,
                         f"{len(vals)} trial(s), sd {sd_hat:.4f} +/-{rel * 100:.0f}% (bias-corrected; the "
                         f"+/- is this estimator's OWN uncertainty at n={n}, not the measurement's, and it "
                         f"is a FLOOR -- normal theory plus a partial kurtosis inflation, measured to run "
                         f"0.90x of the truth on a skewed arm and 0.66x on a heavy-tailed one), "
                         f"observed range {rng:.4f} (descriptive only -- an extremum grows with n and is "
                         f"not comparable across different n). A spread needs >= {min_trials} trials: the "
                         f"mean converges quickly and dispersion does not, so a spread from a handful of "
                         f"runs understates itself and reads like a tight result.")

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
