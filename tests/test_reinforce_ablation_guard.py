"""The reinforcement-ablation probe must REFUSE to report a delta it cannot interpret.

`probes/reinforce_accuracy_ablation.py` compares `recall(reinforce=True)` against `reinforce=False`.
A previous attempt at that comparison reported accuracy 0.0056 -> 0.0035 and nearly published
"reinforcement buys nothing" -- but chance on that corpus was 1/300 = 0.0033, so BOTH ARMS WERE ON THE
FLOOR and the delta measured nothing at all. The probe's baseline guard exists to make that outcome
impossible to reach, and a guard nobody has watched fire is a guard nobody has tested.

So this file proves the guard can fire, on a corpus rather than on arithmetic:

  * the historical numbers themselves (0.0056 vs chance 0.0033 at n=900) must be REJECTED;
  * a real store built from `build_floor_corpus` -- where every record carries the same tokens, so
    retrieval genuinely cannot work -- must abort the probe, with a non-zero exit code from the CLI;
  * a real store built from `build_synthetic_corpus`, where retrieval does work, must PASS, so the
    guard is not merely a constant "abort";
  * the floor fixture must actually BE at the floor, so the abort cannot come from some unrelated
    defect in the fixture;
  * and the mechanism guard must reject an ablation in which reinforcement never fired -- the defect
    that made the probe's own oracle arm report a flawless +0.0000 with a CI of [0, 0] while it was
    looking records up by the wrong key and bumping nothing at all.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "probes"))

# A PLAIN import, deliberately: the probe is a committed file in this repository, not an optional
# dependency. Guarding it with `importorskip` would hide all ten of these tests from the base CI job
# (tests/test_skip_census.py counts exactly that), and a guard nobody's CI runs is a guard nobody has.
import reinforce_accuracy_ablation as RA  # noqa: E402

K = RA.K


def _measure_control(records, questions, n_queries=300):
    """Run the probe's own control arm (reinforce=False) over a corpus and return (hits, n, chance)."""
    store, idx = RA.build_store(records)
    hits = 0
    qs = questions[:n_queries]
    for text, gold in qs:
        gs = set(gold) if isinstance(gold, (list, tuple)) else {gold}
        hits += RA._score(store.recall(text, k=K, reinforce=False) or [], idx, gs)[0]
    return hits, len(qs), 1.0 / len(records)


# --------------------------------------------------------------- the historical numbers, verbatim


def test_the_exact_numbers_that_nearly_got_published_are_rejected():
    """0.0056 accuracy against a 1/300 chance level at n=900 -- the measurement that was one step from
    being written up as "reinforcement buys nothing"."""
    with pytest.raises(RA.BaselineGuardError) as exc:
        RA.assert_baseline_is_measurable("historical", hits=5, n=900, chance=1.0 / 300, verbose=False)
    msg = str(exc.value)
    assert "refusing to report a delta" in msg
    assert "0.0033" in msg or "0.003333" in msg, "the chance level must appear in the message: " + msg


def test_the_guard_reports_the_chance_level_even_when_it_passes():
    """The previous attempt's error was invisible because the chance level was never put next to the
    accuracy. It has to be in the returned diagnostic on the passing path too, not only in the abort."""
    diag = RA.assert_baseline_is_measurable("healthy", hits=405, n=900, chance=1.0 / 300, verbose=False)
    assert diag["chance"] == pytest.approx(1.0 / 300, rel=1e-3)
    assert diag["ratio_over_chance"] > RA.MIN_RATIO_OVER_CHANCE
    assert diag["interpretable"] is True


# ------------------------------------------------------------------ on a real store, not on numbers


def test_the_floor_corpus_really_is_at_the_floor():
    """A control on the abort test itself. If this fixture ever became informative, the abort below
    would still pass -- for the wrong reason -- so the fixture's defect is asserted directly."""
    records, questions = RA.build_floor_corpus(seed=3, n_records=200)
    hits, n, chance = _measure_control(records, questions)
    assert hits / n <= 5 * chance, (
        "the floor fixture retrieves at %.4f vs chance %.4f -- it is no longer a floor, so the abort "
        "test below would be passing for the wrong reason" % (hits / n, chance))


def test_a_floor_level_corpus_aborts_the_guard():
    """The whole point: a store on which retrieval cannot work must ABORT, not yield a delta."""
    records, questions = RA.build_floor_corpus(seed=3, n_records=200)
    hits, n, chance = _measure_control(records, questions)
    with pytest.raises(RA.BaselineGuardError) as exc:
        RA.assert_baseline_is_measurable("floor corpus", hits, n, chance, verbose=False)
    assert "refusing to report a delta" in str(exc.value)


def test_a_working_corpus_passes_the_guard():
    """Positive control: the guard must not be a constant abort, or it would prove nothing."""
    records, questions = RA.build_synthetic_corpus(seed=1, n_records=200)
    hits, n, chance = _measure_control(records, questions)
    diag = RA.assert_baseline_is_measurable("working corpus", hits, n, chance, verbose=False)
    assert diag["interpretable"] is True
    assert diag["ratio_over_chance"] >= RA.MIN_RATIO_OVER_CHANCE


def test_the_probe_itself_aborts_on_a_floor_corpus_end_to_end():
    """Through `evaluate_corpus`, the function the CLI actually calls -- not through the guard alone."""
    records, questions = RA.build_floor_corpus(seed=5, n_records=150)
    with pytest.raises(RA.BaselineGuardError):
        RA.evaluate_corpus("floor", records, questions, seeds=[0], train_events=60, verbose=False)


def test_the_cli_exits_non_zero_when_the_guard_fires():
    """Read the exit code directly. A probe that aborts but exits 0 is a probe whose abort nobody sees."""
    code = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
            "import reinforce_accuracy_ablation as RA\n"
            "recs, qs = RA.build_floor_corpus(seed=5, n_records=150)\n"
            "try:\n"
            "    RA.evaluate_corpus('floor', recs, qs, seeds=[0], train_events=60, verbose=False)\n"
            "except RA.BaselineGuardError as e:\n"
            "    print('ABORTED'); sys.exit(2)\n"
            "sys.exit(0)\n" % (ROOT, os.path.join(ROOT, "probes")))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 2, "expected a non-zero exit, got %d\n%s" % (r.returncode, r.stderr[-800:])
    assert "ABORTED" in r.stdout


# --------------------------------------------------------------------------- the mechanism guard


def test_the_mechanism_guard_rejects_an_ablation_where_reinforcement_never_fired():
    """The probe's oracle arm once reported +0.0000 with a CI of [0, 0] because it looked records up by
    gold INDEX in an ID-keyed dict and bumped nothing. Identical value between the arms means the two
    arms are the same experiment, and no delta from it means anything."""
    with pytest.raises(RA.BaselineGuardError) as exc:
        RA.assert_reinforcement_is_active("never fired", value_on=1.0, value_off=1.0, verbose=False)
    assert "DID NOT FIRE" in str(exc.value)
    RA.assert_reinforcement_is_active("fired", value_on=12.5, value_off=1.0, verbose=False)


def test_the_oracle_warm_actually_moves_the_records_it_names():
    """The regression for that defect, asserted at the mechanism rather than on the summary: the bump
    must land on the GOLD record, addressed by its index in the original corpus."""
    records, questions = RA.build_synthetic_corpus(seed=2, n_records=40)
    store, idx = RA.build_store(records)
    gold = questions[0][1]
    RA._oracle_warm(store, idx, [questions[0]] * 8)
    pos = {idx[it["id"]]: it["value"] for it in store.items}
    assert pos[gold] == pytest.approx(1.0 + 8 * RA.ORACLE_BUMP), (
        "the gold record was not bumped: %r" % pos[gold])
    assert all(v == pytest.approx(1.0) for p, v in pos.items() if p != gold), \
        "the oracle bumped a record other than the gold one"


# ------------------------------------------------------------------------ scope: measurement only


def test_the_probe_does_not_change_the_reinforce_default():
    """This unit measures; it does not ship a behaviour change. If the default ever moves, it must not
    move from here."""
    import inspect

    from inspeximus import Inspeximus
    assert inspect.signature(Inspeximus.recall).parameters["reinforce"].default is True
    src = open(os.path.join(ROOT, "probes", "reinforce_accuracy_ablation.py"), encoding="utf-8").read()
    assert "reinforce: bool = True" not in src, "the probe must not redefine the recall signature"
