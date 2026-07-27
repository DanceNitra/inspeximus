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


def test_bm25_sums_its_terms_in_a_fixed_order():
    """The first source, tested at the mechanism. `qtok` is a SET, and float addition is not associative,
    so summing the per-term contributions in set-iteration order gave a different total per process.

    Asserted on `_bm25_scores` directly rather than through `recall`, because recall rounds the score to
    three places on the way out -- a test reading THAT cannot see 1e-10 of noise, which is exactly why an
    earlier version of this file let the mutation survive."""
    # ACROSS PROCESSES. Within one interpreter a set built the same way iterates the same way every time,
    # so a 50-iteration in-process loop could not see this at all -- it ran green while the mutation that
    # restores set order survived. Hash randomisation is per PROCESS, so the test has to be too.
    # MANY terms, and records that match most of them. With eight short tokens CPython gives the same set
    # order under every seed and each record sums only two or three addends, so the test ran green while
    # the mutation restoring set order survived -- a fixture too small to express the bug. At 45 tokens
    # the iteration order differs under all five seeds and each score is a sum of ~40 addends, where
    # float addition is order-sensitive.
    code = "\n".join([
        "import sys; sys.path.insert(0, %r)" % ROOT,
        "from inspeximus import Inspeximus",
        "m = Inspeximus(path=None)",
        "for i in range(12):",
        "    body = ' '.join('term%d' % ((i * 7 + j) % 45) for j in range(40))",
        "    m.remember('record %d ' % i + body, key='k%d' % i)",
        "q = {'term%d' % i for i in range(45)}",
        "print(';'.join(repr(x) for x in m._bm25_scores(q, list(m._items))))",
    ])
    seen = set()
    for seed in ("0", "1", "2", "3", "4"):
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300,
                           env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"})
        assert r.returncode == 0, r.stderr[-600:]
        seen.add(r.stdout.strip())
    assert len(seen) == 1, (
        f"BM25 produced {len(seen)} distinct score vectors across hash seeds -- the term summation order "
        f"is not fixed")


def test_the_decay_factor_ignores_sub_second_time():
    """The second source. Half-lives here are hours to days, so sub-second resolution carries no meaning
    -- but it carried noise: `now` and `last_access` are wall clocks, so two runs produced decay factors
    differing at ~1e-10, which propagated into the score."""
    m = _fresh()
    rec = m._items[0]
    base = rec.get("last_access") or rec.get("ts")
    vals = {m._effective_value(rec, base + frac) for frac in (0.0, 0.017, 0.4, 0.83, 0.999)}
    assert len(vals) == 1, f"the decay factor moves within one second: {sorted(vals)}"

    # ...and still decays across real time, or the fix would have removed the feature.
    assert m._effective_value(rec, base + 86400.0) < m._effective_value(rec, base)


def test_the_score_reported_is_stable_too():
    """The score itself must be bit-identical between runs. Quantising the RANKING score was tried first
    and reverted: in a crowded store the target ranks first while being exactly tied with 58 competitors
    and 5.7e-10 above two more, so the noise and the smallest meaningful gap were the same size and no
    quantum could separate them. Both sources were removed instead -- sorted BM25 terms, and a decay age
    quantised to whole seconds (an hours-long half-life has no sub-second meaning)."""
    scores = collections.defaultdict(set)
    for _ in range(40):
        for h in _fresh().recall(QUERY, k=5):
            scores[h["text"]].add(h["score"])
    varying = {t: sorted(v) for t, v in scores.items() if len(v) > 1}
    assert not varying, f"the score still moves between runs: {varying}"


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


def test_tied_records_come_back_NEWEST_first():
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
    # Newest first: the policy the store already holds everywhere else -- when two memories are equally
    # relevant, the newer one is the better answer. It used to arrive by accident, through sub-second
    # differences in the decay factor, and that accident was what surfaced the target in a crowded store.
    assert order == [f"alpha bravo charlie item {i}" for i in range(7, -1, -1)], order


def test_the_declared_tie_order_survives_a_different_hash_seed():
    """The same assertion in a fresh interpreter: arrival order is per-process, insertion order is not."""
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from inspeximus import Inspeximus\n"
            "m = Inspeximus(path=None)\n"
            "[m.remember('alpha bravo charlie item %%d' %% i, key='t%%d' %% i) for i in range(8)]\n"
            "print('|'.join(h['text'] for h in m.recall('alpha bravo charlie', k=8)))\n" % ROOT)
    want = "|".join(f"alpha bravo charlie item {i}" for i in range(7, -1, -1))
    for seed in ("0", "1", "2", "3"):
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300,
                           env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"})
        assert r.returncode == 0, r.stderr[-600:]
        assert r.stdout.strip() == want, f"seed {seed}: {r.stdout.strip()[:120]}"
