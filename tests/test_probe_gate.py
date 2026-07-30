"""The gate that decides whether a measured number may be reported.

Every check here exists because of a specific error that re-reading the code did not catch. Two of them
came from an outside reviewer after we published an under-sampled spread in a post about measurement
discipline:

  * `manipulation_landed` asks "did my change take effect?" -- and the patch that destroyed the arm
    (`f"{i:032x}"`, whose first ten hex chars are "0000000000" for every small i, so every record got the
    SAME id) HAD taken effect. It passed. The arm collapsed 0.40 -> 0.0133 and the spread went to zero,
    which reads exactly like a confirmed hypothesis.
  * A spread was quoted from five trials. At the same operating point over 25 trials it was three times
    wider. The mean had long since settled.

So the gate is itself gated: every check below is shown to FAIL on the thing it was written to catch.
A gate that cannot refuse is a ceremony.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from probe_gate import GateFailed, ProbeGate, c4, sd_rel_error  # noqa: E402


def _gate(name="t"):
    return ProbeGate(name, operating_point={"k": 150, "arms": "keyed"})


def test_the_operating_point_must_be_stated_before_measuring():
    with pytest.raises(GateFailed):
        ProbeGate("no op", operating_point={})


# ── the two-sided manipulation check ────────────────────────────────────────────────────────────────
BEFORE = [{"id": f"u{i}", "text": f"fact {i}", "key": f"k{i % 5}", "status": "active"} for i in range(40)]


def test_an_honest_manipulation_passes():
    after = [dict(r, id=f"{i:010x}") for i, r in enumerate(BEFORE)]
    g = _gate()
    assert g.manipulation("only the id moves", BEFORE, after, expect_changed=["id"]) is True


def test_a_manipulation_that_landed_wider_than_intended_fails():
    """THE case. The ids ARE what I wrote, so the one-sided check passes; a second field moved with them."""
    after = [dict(r, id=f"{i:010x}", status="candidate") for i, r in enumerate(BEFORE)]
    g = _gate()
    assert g.manipulation_landed("ids are mine", lambda: True) is True, "one-sided sees nothing wrong"
    assert g.manipulation("only the id moves", BEFORE, after, expect_changed=["id"]) is False
    assert any("status" in d for _, ok, d in g.checks if not ok)


def test_a_collapse_in_RECORD_COUNT_fails_first():
    """Cardinality is part of the diff, not a field in it. With every id identical the store deduplicates,
    so a field-by-field comparison that zips the two lists compares one record against one and finds
    nothing wrong -- the count is the first symptom and the one a field diff cannot show."""
    after = [dict(BEFORE[0], id="0000000000")]
    g = _gate()
    assert g.manipulation("only the id moves", BEFORE, after, expect_changed=["id"]) is False
    assert any("record count changed 40 -> 1" in d for _, ok, d in g.checks if not ok)


def test_a_manipulation_that_did_not_happen_fails():
    """The other direction: the patch silently did nothing."""
    g = _gate()
    assert g.manipulation("id should move", BEFORE, [dict(r) for r in BEFORE],
                          expect_changed=["id"]) is False
    assert any("did NOT" in d for _, ok, d in g.checks if not ok)


def test_an_empty_comparison_is_not_a_clean_one():
    g = _gate()
    assert g.manipulation("nothing", [], [], expect_changed=["id"]) is False


def test_ignored_keys_do_not_fail_the_check():
    """Timestamps move on every build. Declaring them is different from not looking."""
    after = [dict(r, id=f"{i:010x}", ts=i) for i, r in enumerate(BEFORE)]
    g = _gate()
    assert g.manipulation("id moves; ts is wall-clock", BEFORE, after,
                          expect_changed=["id"], ignore=["ts"]) is True


# ── the trial floor ─────────────────────────────────────────────────────────────────────────────────
def test_a_spread_from_five_trials_is_refused():
    g = _gate()
    assert g.spread("keyed arm", [0.4067, 0.4100, 0.4133, 0.4167, 0.4200]) is False


def test_a_spread_from_twenty_trials_is_accepted():
    g = _gate()
    assert g.spread("keyed arm", [0.38 + 0.002 * i for i in range(20)]) is True


def test_the_floor_is_stated_in_the_failure():
    g = _gate()
    g.spread("keyed arm", [0.40, 0.41])
    detail = next(d for _, ok, d in g.checks if not ok)
    assert "2 trial(s)" in detail and ">= 20" in detail


# ── the estimator, not just the floor ───────────────────────────────────────────────────────────────
# The floor above was the symptom. The RANGE was the defect: an extremum statistic whose expectation
# only grows with n, so a small sample can only understate it -- which is the direction that flatters a
# result, and the reason an under-sampled spread reads as tight rather than as noisy. Credit: jacksonxly.
def test_c4_matches_the_closed_form_at_the_points_the_docstring_quotes():
    """If these drift, every corrected SD the gate reports is quietly wrong."""
    assert round(c4(5), 4) == 0.9400
    assert round(c4(20), 4) == 0.9869
    assert round(c4(25), 4) == 0.9896
    assert all(c4(n) < c4(n + 1) for n in range(2, 60)), "the bias must shrink monotonically with n"
    assert c4(500) > 0.999, "and vanish in the limit"
    assert sd_rel_error(1) == float("inf"), "one trial has no spread, and must not report a finite one"


def test_the_correction_de_biases_and_the_range_structurally_cannot():
    """THE falsification control for this change.

    Draw many samples of five from a known pool. The corrected SD must recover the pool's sigma; the raw
    SD must sit ~6% low; and the range must sit far below its own large-n expectation. Delete the `/c4(n)`
    from `spread()` and the middle assertion is what goes red.
    """
    import random
    import statistics as st

    rnd = random.Random(20260730)
    DRAWS, SIGMA = 20000, 1.0
    raw, corrected, ranges = [], [], []
    for _ in range(DRAWS):
        x = [rnd.gauss(0.0, SIGMA) for _ in range(5)]
        raw.append(st.stdev(x))
        corrected.append(st.stdev(x) / c4(5))
        ranges.append(max(x) - min(x))

    assert abs(st.mean(raw) / SIGMA - 0.94) < 0.02, "the raw SD is biased low at n=5, by the c4 constant"
    assert abs(st.mean(corrected) / SIGMA - 1.00) < 0.02, "dividing by c4 must remove that bias"

    # the asymmetry that motivated the change: the range errs in ONE direction, the corrected SD in both
    e_range_25 = 3.93 * SIGMA                       # E[range]/sigma at n=25, measured over 200k draws
    understates = sum(1 for v in ranges if v < e_range_25) / DRAWS
    assert understates > 0.90, "a 5-sample range almost always understates the 25-sample one"
    low = sum(1 for v in corrected if v < SIGMA) / DRAWS
    assert 0.45 < low < 0.60, "a corrected SD is too low about half the time -- a coin flip, not a bias"


def test_the_gate_ITSELF_reports_the_corrected_sd_not_the_raw_one():
    """The assertions above are about the CONSTANT; this one is about the CALL SITE, and only this one
    fails when `/ c4(n)` is deleted from `spread()`.

    Written after the first version of the control passed a mutation that removed the correction: it
    re-implemented `stdev(x)/c4(5)` inside the test and compared that to itself, so it could not see the
    shipping code at all. A guard that recomputes the answer is checking arithmetic, not the object.
    """
    import re
    import statistics as st

    vals = [0.40, 0.41, 0.42, 0.43, 0.44]
    g = _gate()
    g.spread("five", vals)
    detail = next(d for l, _, d in g.checks if l.startswith("SPREAD"))
    reported = float(re.search(r"sd (\d+\.\d+)", detail).group(1))

    raw, corrected = st.stdev(vals), st.stdev(vals) / c4(5)
    assert round(corrected, 4) != round(raw, 4), "the fixture must be able to tell the two apart"
    assert abs(reported - round(corrected, 4)) < 1e-9, (
        f"the gate reported sd={reported}; corrected is {corrected:.4f} and the raw sample SD is "
        f"{raw:.4f}. Reporting the raw one puts every published spread {100 * (1 - c4(5)):.0f}% low "
        f"at n=5.")


def test_de_biasing_does_not_remove_the_need_for_trials():
    """The correction fixes the DIRECTION of the error, not its SIZE, so the floor survives it."""
    assert sd_rel_error(5) > 0.35, "a corrected SD from five trials still carries +/-36% of its own"
    assert sd_rel_error(20) < 0.17, "twenty buys +/-16% -- which is what the floor is actually for"
    assert sd_rel_error(5) > 2 * sd_rel_error(20)


def test_the_uncertainty_widens_on_a_heavy_tailed_sample_and_never_shrinks():
    """The closed form is NORMAL theory. Measured over 40,000 subsamples it runs 0.84x of the truth on
    our own positive-kurtosis arm and 0.58x on a lognormal, which is the flattering direction and the
    exact failure this module exists to stop. Passing the values applies sqrt(1 + g2/2), floored at 1.

    The floor is load-bearing in both directions: a light-tailed sample must not SHRINK the figure, or a
    uniform ramp would print a tighter +/- than normal theory allows.
    """
    import re

    n = 24
    ramp = [0.30 + 0.01 * i for i in range(n)]                 # platykurtic: g2 well below 0
    heavy = [0.30] * (n - 2) + [0.90, -0.30]                   # two outliers: g2 well above 0

    base = sd_rel_error(n)
    assert sd_rel_error(n, ramp) == base, "a light-tailed sample must not shrink the stated uncertainty"
    assert sd_rel_error(n, heavy) > base * 1.5, (
        f"heavy tails must widen it: got {sd_rel_error(n, heavy):.3f} against a normal-theory {base:.3f}")

    g = _gate()
    g.spread("heavy", heavy)
    detail = next(d for l, _, d in g.checks if l.startswith("SPREAD"))
    assert "FLOOR" in detail, "the figure must be labelled a floor, not a measurement of the truth"

    # Assert the EXACT figure the widened estimator gives, not merely "bigger than the plain one". The
    # first version of this line compared the printed (rounded) percentage against int(base*100), which
    # truncates: normal theory at n=24 is 0.1487, printed as 15%, tested against a threshold of 14. So
    # 15 > 14 passed with the inflation removed entirely, and the mutation that deletes the whole fix
    # SURVIVED. A threshold loose enough to admit the absence of the thing it guards is not a guard.
    reported = int(re.search(r"\+/-(\d+)%", detail).group(1))
    assert reported == round(sd_rel_error(n, heavy) * 100), (
        f"the gate printed +/-{reported}%; the widened estimator says "
        f"{round(sd_rel_error(n, heavy) * 100)}% and plain normal theory says {round(base * 100)}%")
    assert reported > round(base * 100) + 5, "and the two must be far enough apart to tell apart"


def test_the_uncertainty_is_documented_as_a_floor_not_a_cure():
    """The kurtosis correction is PARTIAL and saying so is part of the fix. Measured: it moves
    quoted-over-actual from 0.84x to 0.90x on the skewed arm and 0.58x to 0.66x on a lognormal. It cannot
    do better, because sample kurtosis is a fourth-moment statistic and a small sample from a heavy tail
    usually contains no tail point -- the detector fails in the same regime as the thing it detects.
    A docstring that claimed a cure would be the more dangerous artifact.
    """
    doc = sd_rel_error.__doc__
    assert "PARTIAL" in doc and "not be read as a cure" in doc.lower()
    assert "0.90x" in doc and "0.66x" in doc, "the residual shortfall must be stated as a number"
    assert "fourth-moment" in doc.lower() or "FOURTH-moment" in doc, "state WHY it cannot be cured"


def test_the_reported_spread_carries_the_estimator_and_its_uncertainty():
    """A number with no stated uncertainty invites the next reader to treat it as exact."""
    g = _gate()
    g.spread("keyed arm", [0.38 + 0.002 * i for i in range(20)])
    detail = next(d for l, _, d in g.checks if l.startswith("SPREAD"))
    assert "bias-corrected" in detail and "+/-16%" in detail
    assert "descriptive only" in detail, "the raw range must be marked as not comparable across n"

    g2 = _gate()
    g2.spread("five", [0.40, 0.41, 0.42, 0.43, 0.44])
    d2 = next(d for l, _, d in g2.checks if l.startswith("SPREAD"))
    assert "+/-36%" in d2, "and the stated uncertainty must move with n, not be a fixed string"


# ── the report refuses to hand back a number when a check failed ────────────────────────────────────
def test_report_raises_while_any_check_failed():
    g = _gate()
    g.spread("under-sampled", [0.40, 0.41])
    with pytest.raises(GateFailed):
        g.report({"value": 0.405})


def test_report_returns_the_number_when_every_check_passed():
    g = _gate()
    g.spread("fine", [0.38 + 0.002 * i for i in range(25)])
    g.denominator(n_scored=150, n_total=150)
    out = g.report({"value": 0.40})
    assert out["result"]["value"] == 0.40
    assert all(c["passed"] for c in out["checks"])


def test_the_denominator_refuses_an_empty_one():
    g = _gate()
    assert g.denominator(n_scored=0, n_total=150) is False


def test_a_check_that_raises_counts_as_a_failure_not_an_error():
    """A probe crashing inside its own gate must not abort the run and lose the other verdicts."""
    g = _gate()
    assert g.can_fail("boom", lambda: (_ for _ in ()).throw(RuntimeError("x"))) is False


# ── the one probe that reports a run-to-run spread ──────────────────────────────────────────────────
@pytest.fixture(scope="module")
def seeded_probe_run():
    """Run the probe ONCE for both tests below. It takes ~50s, and a file that runs it twice spends a
    minute of suite time to assert two different things about the same output."""
    import subprocess

    r = subprocess.run([sys.executable, os.path.join(ROOT, "probes",
                                                     "identity_gate_supersession_probe.py")],
                       cwd=ROOT, capture_output=True, text=True, timeout=600,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": ROOT})
    assert r.returncode == 0, f"the probe does not run: {r.stderr[-1200:]}"
    return r


def test_the_seeded_probe_declares_enough_seeds_and_actually_runs(seeded_probe_run):
    """`identity_gate_supersession_probe.py` computed a spread from FIVE seeds, with the comment
    "5 seeds for a CI-ish spread". Re-measured at 25, on our own data:

        seeds   ungated mean   ungated range   gated mean   gated range
          5        0.135          0.100          0.010        0.025
         25       0.131          0.200          0.015        0.075

    The means do not move; the ranges double and triple. The range approaches the truth from BELOW
    always, so an under-sampled spread does not look noisy -- it looks tight, which is the direction that
    flatters a result.

    It was also CRASHING on its first line: `tempfile.mkstemp` creates the file, empty, and the store
    correctly refuses to open a file it cannot parse. Nobody noticed, because it is one of 48 probes (of
    101) that no doc cites and no test runs. This is the smallest honest guard: this probe, specifically.
    A repo-wide scanner was measured first and would have matched the WORD "variance" in five docstrings
    and the real thing in none -- a check that cannot fail.
    """
    import re

    path = os.path.join(ROOT, "probes", "identity_gate_supersession_probe.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"^SEEDS\s*=\s*(\d+)", src, re.M)
    assert m, "the probe must DECLARE its trial count, not bury it in a range() literal"
    assert int(m.group(1)) >= 20, (
        f"SEEDS={m.group(1)}: a spread needs >= 20 trials. At 5 this probe reported a range of 0.100 "
        f"where 25 trials show 0.200.")

    r = seeded_probe_run
    assert "per-seed" in r.stdout


def test_the_saved_artifact_agrees_with_the_line_that_was_printed(seeded_probe_run):
    """The probe printed 65 candidates/run and SAVED 326.0, because `ncand` was divided by the literal 5
    -- the seed count from before it was raised to 25. Nobody reads a JSON file next to a correct stdout.

    326 is not merely wrong; it is impossible. A run makes E*ROUNDS = 240 corrections, so it cannot fork
    more than 240 candidates, and the bound is checkable without knowing the right answer. Both halves are
    asserted here: the artifact must agree with the printed line, and it must respect the physical ceiling.
    """
    import json
    import re

    r = seeded_probe_run
    m = re.search(r"review-queue cost: (\d+) candidates/run", r.stdout)
    assert m, f"the probe no longer prints the review-queue cost:\n{r.stdout[-600:]}"
    printed = float(m.group(1))

    art = json.load(open(os.path.join(ROOT, "probes", "identity_gate_supersession_result.json")))
    saved = art["candidates_per_run"]
    ceiling = art["E"] * art["rounds"]
    assert saved <= ceiling, (
        f"{saved} candidates/run exceeds the {ceiling} corrections a run makes: a stale denominator")
    assert abs(saved - printed) <= 0.5, f"artifact says {saved}, stdout said {printed}"
    assert art.get("seeds") == 25, "the artifact must carry the trial count it was measured over"


def test_the_probe_reports_n_alongside_the_range(seeded_probe_run):
    """"0.38-0.42 over 25 trials" is a claim; "0.38-0.42" is not. If the count is not printed next to the
    numbers, the next person to quote it cannot know it was sampled enough."""
    r = seeded_probe_run
    # EVERY line reporting a range must carry the count. Looking for "seeds=25" anywhere in stdout let a
    # mutation strip it from one of the two arms and still pass, and the reader of THAT line would have
    # had a range with no n -- which is the entire failure this guards against.
    range_lines = [ln for ln in r.stdout.splitlines() if "range " in ln]
    assert len(range_lines) >= 2, f"expected a range on both arms:\n{r.stdout[:500]}"
    for ln in range_lines:
        assert "seeds=25" in ln.replace(" ", ""), f"a range with no trial count: {ln}"
