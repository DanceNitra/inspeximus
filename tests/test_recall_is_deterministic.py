"""Two identical stores, one query, one answer — every time, in every process.

The product's stated property is determinism. Recall did not have it. `scored.sort(key=lambda x: -x[0])`
ranked on the raw score, and the LEXICAL channel accumulates over an unordered collection, so the same
record scores differently between runs. Measured at full precision across 120 runs of one fixture: a
single record took **19 distinct score values with a spread of 5.7e-10**. Three records nominally tied at
0.564 therefore rotated, and a caller taking the top-1 of that tie got a different answer 7% of the time.

It was found by a probe nobody ran — `recall_reinforce_flag_probe.py`, which failed 4 of 20 standalone
runs and had never been executed by any test.

Three attempts, and the two failures are the instructive part:
  * `(ts, id)` as the tie-break made it WORSE (4/20 from 16/20): records written in the same clock tick
    share `ts`, so the tie fell through to `id`, which is random per store.
  * keying the position lookup on object identity did NOTHING, because `items` hands out copies. It
    measured 18/20 and read as progress while the lookup always missed.
  * quantising the score to 12 decimals was the right kind of fix at the wrong SIZE -- two orders of
    magnitude below the noise it was meant to absorb.

What holds it: the score is quantised to `_RANK_QUANTUM` places (measured, ~10,000x above the noise and
1000x finer than the score recall reports), then a TOTAL tie-break -- insertion position, then text -- so
equal scores can never fall through to arrival order.
"""
import collections
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import Inspeximus  # noqa: E402
from inspeximus.core import _RANK_QUANTUM  # noqa: E402

TEXTS = ["the capital of France is Paris", "photosynthesis converts light to chemical energy",
         "Paris hosted the 2024 Olympics", "the mitochondria is the powerhouse of the cell",
         "France borders Spain and Germany", "chlorophyll gives plants their green color",
         "the Eiffel Tower is in Paris", "cellular respiration happens in the mitochondria"]
QUERY = "what is in Paris France"
RUNS = 60          # at the measured 7% rotation rate, 60 runs miss it with probability ~0.013


def _fresh():
    m = Inspeximus(path=None)
    for i, t in enumerate(TEXTS):
        m.remember(t, key=f"k{i}")
    return m


def _orders(runs=RUNS, **kw):
    return collections.Counter(
        tuple(h["text"] for h in _fresh().recall(QUERY, k=5, **kw)) for _ in range(runs))


@pytest.mark.parametrize("mode", ["auto", "lexical", "semantic", "hybrid"])
def test_the_same_store_and_query_give_one_answer(mode):
    """The fixture is chosen so three records tie exactly -- that is where the defect lived, and a
    fixture without a tie could not fail."""
    orders = _orders(mode=mode)
    assert len(orders) == 1, (
        f"mode={mode}: {len(orders)} distinct top-k orders over {RUNS} identical runs "
        f"(counts {sorted(orders.values(), reverse=True)})")


def test_the_fixture_really_does_contain_a_tie():
    """Otherwise the test above passes over a case that could never have shown the bug."""
    scores = [h["score"] for h in _fresh().recall(QUERY, k=5)]
    assert len(scores) - len(set(scores)) >= 2, f"no tie in the fixture: {scores}"


def test_reinforcement_does_not_change_the_ranking():
    """The probe's own claim: `reinforce=False` differs in its SIDE EFFECT, not in what it returns. An
    earlier reading blamed the default path for the rotation; measured against itself, both paths were
    equally nondeterministic, so reinforcement was never the cause."""
    a = _orders(runs=20)
    b = _orders(runs=20, reinforce=False)
    assert len(a) == len(b) == 1
    assert list(a)[0] == list(b)[0]


def test_the_order_is_the_same_under_a_different_hash_seed():
    """The root cause is candidate order arriving through sets of record-id STRINGS, whose iteration
    depends on per-process hash randomisation. A single-process test cannot see that at all."""
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from inspeximus import Inspeximus\n"
            "T = %r\n"
            "m = Inspeximus(path=None)\n"
            "[m.remember(t, key='k%%d' %% i) for i, t in enumerate(T)]\n"
            "print('|'.join(h['text'] for h in m.recall(%r, k=5)))\n" % (ROOT, TEXTS, QUERY))
    seen = set()
    for seed in ("0", "1", "2", "3"):
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300,
                           env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"})
        assert r.returncode == 0, r.stderr[-600:]
        seen.add(r.stdout.strip())
    assert len(seen) == 1, f"PYTHONHASHSEED changes the answer: {len(seen)} distinct orders"


def test_the_quantum_is_above_the_measured_noise_and_below_what_we_report():
    """Both bounds matter. Too fine and it absorbs nothing -- 12 places left 7% of runs rotating. Too
    coarse and genuinely different scores get merged into the tie-break."""
    assert _RANK_QUANTUM <= 9, "the quantum must stay far finer than any reported score"
    assert 10 ** -_RANK_QUANTUM > 5.7e-10 * 100, (
        "the quantum must sit well above the measured 5.7e-10 run-to-run spread")


def test_distinct_scores_are_still_ranked_by_score_not_by_the_tie_break():
    """The failure mode of the fix: quantise too coarsely and a real difference becomes a tie, so the
    ranking silently becomes insertion order."""
    m = Inspeximus(path=None)
    m.remember("the capital of France is Paris", key="a")
    m.remember("France borders Spain and Germany", key="b")
    hits = m.recall("capital of France", k=2)
    assert hits[0]["text"] == "the capital of France is Paris", [h["text"] for h in hits]
    assert hits[0]["score"] > hits[1]["score"], [h["score"] for h in hits]

    # Two wrong fixtures before this one, both mine, both worth the line. "France" scored IDENTICALLY
    # (1.6930, relevance 1.0) because the query contains every token of it. An unrelated document about
    # photosynthesis was filtered out below the relevance floor and never came back at all, so there was
    # no hits[1] to compare. A "clearly weaker" document has to be weaker to the SCORER and still strong
    # enough to be returned: 1.6930 vs 0.8470.


def test_tied_records_come_back_in_INSERTION_order_not_merely_a_stable_one():
    """Quantising the score alone already makes the answer repeatable, so a mutation that removes the
    tie-break survived every test above -- it had no teeth, only luck. Stability is not the property we
    want: arrival order is set-iteration order over random id strings, and "it happens not to move today"
    is exactly the accident this codebase keeps getting burned by.

    So the DECLARED order is asserted directly: among equal scores, older first."""
    m = Inspeximus(path=None)
    # Eight records that all match the query identically -> eight-way tie, ranked purely by the tie-break.
    for i in range(8):
        m.remember(f"alpha bravo charlie item {i}", key=f"t{i}")
    hits = m.recall("alpha bravo charlie", k=8)

    assert len({h["score"] for h in hits}) == 1, f"fixture must tie: {[h['score'] for h in hits]}"
    order = [h["text"] for h in hits]
    assert order == [f"alpha bravo charlie item {i}" for i in range(8)], order


def test_the_declared_tie_order_survives_a_different_hash_seed():
    """The same assertion in a fresh interpreter: arrival order is per-process, insertion order is not."""
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from inspeximus import Inspeximus\n"
            "m = Inspeximus(path=None)\n"
            "[m.remember('alpha bravo charlie item %%d' %% i, key='t%%d' %% i) for i in range(8)]\n"
            "print('|'.join(h['text'] for h in m.recall('alpha bravo charlie', k=8)))\n" % ROOT)
    want = "|".join(f"alpha bravo charlie item {i}" for i in range(8))
    for seed in ("0", "1", "2", "3"):
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300,
                           env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"})
        assert r.returncode == 0, r.stderr[-600:]
        assert r.stdout.strip() == want, f"seed {seed}: {r.stdout.strip()[:120]}"
