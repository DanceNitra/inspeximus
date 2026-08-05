"""The admit() arm must carry a control that experiences the same time, or it measures the clock.

WHAT HAPPENED. `arm_admit_write_path` reported a write-path read-purity defect: 400 `admit()` calls,
nothing admitted, and 20 of 80 answers moved -- therefore "a write-admission check that stored
nothing reinforced what it checked against." A control store, never handed to `admit()`, moved the
same 20. `recall()` recomputes decay from wall-clock age, so about a second of elapsed time reorders
records tied to within the 3-decimal score resolution the API returns. Every mechanical check was
clean -- nothing admitted, no stored field moved, `store.items` order byte-identical, a fresh store
unaffected by process history -- and that all-clean picture was the tell.

TWO WEAKER CONTROLS WERE WRITTEN FIRST AND BOTH LIED, in opposite directions. This file pins the
shape of the one that does not, because the failure is not "the control is missing" (easy to see)
but "the control is present and subtly unmatched" (which reads as a result):

  * a control built AFTER the treatment is younger than the treated store and crosses its
    integer-second decay boundaries at different moments. It reported 0.0000 and so MANUFACTURED an
    effect attributable to admit();
  * a control read in a SEPARATE pass has its reads land later than the treated store's, and when
    the quantity under test is itself a clock effect, that skew is scored as treatment.

So the assertions below are about the ARRANGEMENT, not about a number: two stores, both built before
the first admit, the treatment applied to one of them only, and the after-reads interleaved. A
number-only test would pass on both broken versions.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "probes"))

# Imported directly, NOT via pytest.importorskip. The probe is a first-party file in this repo and
# depends on nothing outside the stdlib and inspeximus itself, so an importorskip here does not guard
# an optional dependency -- it declares seven of this repo's own tests optional and hides them from
# the base CI job. The skip census caught exactly that (152 -> 159) and the pin was right to refuse.
import reinforce_accuracy_ablation as P  # noqa: E402


def _tiny_corpus(n=40, n_q=8):
    """Small enough to run fast, crowded enough that records tie."""
    records = [f"note {i}: the deployment pipeline stage {i % 7} handles rollout and rollback" for i in range(n)]
    questions = [(f"deployment pipeline stage {i % 7}", [i]) for i in range(n_q)]
    return records, questions


class _Trace:
    """Records the order of build_store / admit / recall, and which store each touched."""

    def __init__(self):
        self.events = []
        self.stores = []

    def install(self, monkeypatch):
        real_build = P.build_store

        def build(records, order=None):
            store, idx = real_build(records, order)
            self.stores.append(store)
            self.events.append(("build", len(self.stores) - 1))
            real_admit = store.admit
            real_recall = store.recall
            which = len(self.stores) - 1

            def admit(*a, _r=real_admit, _w=which, **k):
                self.events.append(("admit", _w))
                return _r(*a, **k)

            def recall(*a, _r=real_recall, _w=which, **k):
                self.events.append(("recall", _w))
                return _r(*a, **k)

            store.admit = admit
            store.recall = recall
            return store, idx

        monkeypatch.setattr(P, "build_store", build)
        return self

    def kinds(self, kind):
        return [w for k, w in self.events if k == kind]


@pytest.fixture()
def traced(monkeypatch):
    records, questions = _tiny_corpus()
    tr = _Trace().install(monkeypatch)
    # repeats=1: these assertions are about the ARRANGEMENT of one paired trial. The repetition
    # contract is asserted separately below.
    result = P.arm_admit_write_path(records, questions, sample=8, n_admits=25, seed=7, repeats=1)
    return tr, result


def test_the_arm_builds_a_second_store_at_all(traced):
    tr, _ = traced
    assert len(tr.stores) == 2, (
        f"the arm built {len(tr.stores)} store(s); without a control store its divergence is the "
        f"elapsed time of the machine it ran on")


def test_both_stores_are_built_before_the_treatment_begins(traced):
    """The control must share the treated store's timeline, not start life after it."""
    tr, _ = traced
    builds = [i for i, (k, _w) in enumerate(tr.events) if k == "build"]
    first_admit = next((i for i, (k, _w) in enumerate(tr.events) if k == "admit"), None)
    assert first_admit is not None, "no admit() was called -- the arm did not run its treatment"
    assert max(builds) < first_admit, (
        "the control store was built after admit() started. It is then younger than the treated "
        "store and crosses its decay boundaries elsewhere -- the version of this control that "
        "reported 0.0000 and invented an effect")


def test_the_control_store_is_never_treated(traced):
    tr, _ = traced
    assert set(tr.kinds("admit")) == {0}, (
        f"admit() was called on store(s) {sorted(set(tr.kinds('admit')))}; the control must receive "
        f"no treatment at all, or it is a second treated store and the excess is meaningless")


def test_the_after_reads_are_interleaved_across_the_two_stores(traced):
    """Read as two passes, the control's reads land later and its own skew is scored as treatment."""
    tr, _ = traced
    last_admit = max(i for i, (k, _w) in enumerate(tr.events) if k == "admit")
    after = [w for k, w in tr.events[last_admit + 1:] if k == "recall"]
    assert after, "no reads after the treatment"
    assert set(after) == {0, 1}, "the after-pass did not read both stores"
    runs = 1 + sum(1 for a, b in zip(after, after[1:]) if a != b)
    assert runs >= len(after) - 1, (
        f"the after-reads are batched per store ({runs} alternations over {len(after)} reads), not "
        f"interleaved. The control then reads later than the treated store, and the clock difference "
        f"between the two passes is measured as if admit() had caused it")


def test_the_excess_is_the_raw_figure_minus_the_control(traced):
    _tr, r = traced
    raw, ctl, excess = (r["divergence"], r["time_control_divergence"],
                        r["divergence_excess_over_time_control"])
    assert None not in (raw, ctl, excess)
    assert excess == pytest.approx(max(0.0, raw - ctl), abs=1e-9), (
        f"excess {excess} is not max(0, raw {raw} - control {ctl})")
    assert r["attributable_resolution"] == pytest.approx(1.0 / 8, abs=1e-9), (
        "the arm did not report the grain it can actually resolve")


def test_the_headline_aggregate_uses_the_excess_not_the_raw_figure():
    """A clock artifact must not be able to become the probe's `carrier` or reproduce its claim."""
    records, questions = _tiny_corpus()
    arms = P.decompose_divergence(records, questions, verbose=False, sample=8)
    d = arms["d_admit_write_path"]
    assert "divergence_excess_over_time_control" in d
    if (d["divergence"] or 0.0) > 0 and (d["divergence_excess_over_time_control"] or 0.0) == 0.0:
        assert arms["carrier"] != "d_admit_write_path", (
            "an arm whose entire divergence is its own time control became the carrier")


def test_the_control_control_the_trace_can_actually_see_a_broken_arrangement():
    """Without this, every assertion above could be passing because the trace sees nothing.

    A deliberately broken arm -- one store, no control -- must fail the same checks."""
    tr = _Trace()

    class _MP:
        def setattr(self, obj, name, val):
            setattr(obj, name, val)

    real_build = P.build_store
    try:
        tr.install(_MP())
        records, _q = _tiny_corpus()
        store, _idx = P.build_store(records)
        store.admit(records[0])
        assert len(tr.stores) == 1 and tr.kinds("admit") == [0], (
            "the trace did not record a single-store arrangement, so it cannot tell a missing "
            "control from a present one")
    finally:
        P.build_store = real_build


def test_one_trial_cannot_settle_this_so_the_arm_repeats():
    """A threshold cannot separate the excess from the control's own noise; only repetition can.

    Five consecutive single-trial runs of identical code on locomo_conv2 gave excesses
    0.0250 / 0.0125 / 0.0000 / 0.0000 / 0.0000 -- so a lone trial announced "attributable to admit()"
    one time in five with nothing to attribute. Any fixed bar either swallows a real small effect or
    keeps admitting that noise; the sign across repeats does neither."""
    records, questions = _tiny_corpus()
    r = P.arm_admit_write_path(records, questions, sample=8, n_admits=25, seed=7, repeats=4)
    assert r["n_trials"] == 4, "the arm did not run the requested number of paired trials"
    for key in ("per_trial_divergence", "per_trial_time_control", "per_trial_excess"):
        assert len(r[key]) == 4, f"{key} does not report every trial, so the spread is invisible"
    assert r["trials_with_positive_excess"] <= r["n_trials"]
    assert r["attributable_to_admit"] == (r["trials_with_positive_excess"] == r["n_trials"]), (
        "the flag is not 'every trial agreed on the sign'; a median-plus-threshold reading is what "
        "reported a finding once in five runs of code with a true excess of zero")


def test_a_single_dissenting_trial_withdraws_the_attribution():
    """The bar is EVERY trial, so one trial at zero must be enough to withdraw the claim.

    Without this the all-positive rule could be satisfied by a majority rule that happens to agree on
    the fixtures here."""
    records, questions = _tiny_corpus()
    r = P.arm_admit_write_path(records, questions, sample=8, n_admits=25, seed=7, repeats=3)
    n, pos = r["n_trials"], r["trials_with_positive_excess"]
    if 0 < pos < n:
        assert not r["attributable_to_admit"], (
            f"{pos} of {n} trials showed a positive excess and the arm still attributed it to admit()")
    # and the contract holds by construction whatever this run produced
    assert r["attributable_to_admit"] is (pos == n)
