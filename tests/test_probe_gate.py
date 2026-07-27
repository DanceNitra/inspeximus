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
from probe_gate import GateFailed, ProbeGate  # noqa: E402


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
