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

What holds it -- and the description below is the CURRENT one; an earlier version of this docstring
described a `_RANK_QUANTUM` that no longer exists in the code:

  * the noise was removed at its SOURCE, in two places, because quantising the ranking score was tried
    and REVERTED. In a crowded store the target ranked first while being exactly tied with 58 competitors
    and 5.7e-10 above two more, so the noise and the smallest meaningful gap were the same size and no
    quantum could separate them. The two sources: BM25 sums its query terms in SORTED order (float
    addition is not associative and `qtok` is a set), and the decay age is quantised to whole seconds (an
    hours-long half-life has no sub-second meaning).
  * ties then resolve on a TOTAL key -- score, then WRITE POSITION -- so equal scores can never fall
    through to arrival order. Position, not `ts`: the wall clock here has ~15 ms granularity, so records
    written in a loop share a tick in one run and not in the next.

The same "`ts` is not a total order" defect survived in three OPT-IN levers that this file did not cover
(`tie_recent`, `rerank_by='recency'`, `resolve_conflicts`) and is now tested below.
"""
import collections
import hashlib
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import Inspeximus  # noqa: E402

_DIM = 64


def _embed(text):
    """A deterministic, zero-dependency bag-of-words hashing embedder, so `mode='semantic'` and
    `mode='hybrid'` actually RUN. Without one, `recall` falls back to lexical and a test parametrized
    over the four modes silently measures the same path four times.

    sha256, never Python's `hash()`: a seed-dependent embedder would inject the very noise the tests
    are looking for and every cross-seed assertion here would be measuring the fixture.
    """
    v = [0.0] * _DIM
    for w in (text or "").lower().split():
        v[int.from_bytes(hashlib.sha256(w.encode()).digest()[:8], "big") % _DIM] += 1.0
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


TEXTS = ["the capital of France is Paris", "photosynthesis converts light to chemical energy",
         "Paris hosted the 2024 Olympics", "the mitochondria is the powerhouse of the cell",
         "France borders Spain and Germany", "chlorophyll gives plants their green color",
         "the Eiffel Tower is in Paris", "cellular respiration happens in the mitochondria"]
QUERY = "what is in Paris France"
RUNS = 60          # at the measured 7% rotation rate, 60 runs miss it with probability ~0.013

# The WIDE fixture: 12 records of 40 tokens against a 45-token query, so each BM25 score is a sum of ~40
# addends. This is the only fixture here that can express the accumulation defect -- with the eight short
# TEXTS above, each score is a sum of two or three addends and CPython gives the same set order under
# every seed, so restoring set-iteration order in `_bm25_scores` changes nothing that can be observed.
# Measured on this fixture with the fix mutated out: 6 distinct BM25 score vectors across 6 hash seeds,
# max spread 2.66e-15, and 6 distinct top-k orders in mode='hybrid'.
WIDE_QUERY = " ".join(f"term{i}" for i in range(45))


def _fresh(embed=None):
    m = Inspeximus(path=None, embed=embed)
    for i, t in enumerate(TEXTS):
        m.remember(t, key=f"k{i}")
    return m


def _wide(embed=None):
    m = Inspeximus(path=None, embed=embed)
    for i in range(12):
        body = " ".join(f"term{(i * 7 + j) % 45}" for j in range(40))
        m.remember(f"record {i} " + body, key=f"k{i}")
    return m


def _orders(runs=RUNS, **kw):
    return collections.Counter(
        tuple(h["text"] for h in _fresh().recall(QUERY, k=5, **kw)) for _ in range(runs))


@pytest.mark.parametrize("mode", ["auto", "lexical", "semantic", "hybrid"])
@pytest.mark.parametrize("fixture", ["tie", "wide"])
def test_the_same_store_and_query_give_one_answer(mode, fixture):
    """The `tie` fixture is chosen so three records tie exactly -- that is where the defect lived, and a
    fixture without a tie could not fail. The `wide` fixture is the one whose BM25 scores are long enough
    sums to move at all.

    An EMBEDDER is supplied, and the mode actually reached is asserted. Without one,
    `recall(mode='semantic')` and `recall(mode='hybrid')` fall back to lexical -- so the earlier version
    of this test ran the identical code path four times and reported it as four modes covered.
    """
    build = _fresh if fixture == "tie" else _wide
    query = QUERY if fixture == "tie" else WIDE_QUERY
    orders = collections.Counter()
    reached = set()
    for _ in range(RUNS):
        m = build(embed=_embed)
        orders[tuple(h["text"] for h in m.recall(query, k=5, mode=mode))] += 1
        reached.add(m._last_mode)
    # ASSERT THE TARGET RESOLVES. 'auto' below the semantic_threshold is lexical BY DESIGN; the other
    # three must reach the channel they name or this test is measuring nothing it claims to.
    want = "lexical" if mode == "auto" else mode
    assert reached == {want}, f"mode={mode} reached {reached}, not {{'{want}'}}"
    assert len(orders) == 1, (
        f"mode={mode} fixture={fixture}: {len(orders)} distinct top-k orders over {RUNS} identical runs "
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


# The child of the cross-process test: one process = one PYTHONHASHSEED. It rebuilds the store for every
# run so nothing carries over, and prints one line per (fixture, mode) holding the distinct top-k orders
# that seed produced. Kept as source text rather than a helper module because it has to run under a
# DIFFERENT interpreter environment -- hash randomisation is per process, so an in-process loop is blind
# to it (a set built the same way iterates the same way all day inside one interpreter).
_SEED_CHILD = '''
import hashlib, sys
sys.path.insert(0, %(root)r)
from inspeximus import Inspeximus
DIM = 64
def embed(text):
    v = [0.0] * DIM
    for w in (text or "").lower().split():
        v[int.from_bytes(hashlib.sha256(w.encode()).digest()[:8], "big") %% DIM] += 1.0
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]
TEXTS = %(texts)r
def tie():
    m = Inspeximus(path=None, embed=embed)
    for i, t in enumerate(TEXTS):
        m.remember(t, key="k%%d" %% i)
    return m, %(query)r
def wide():
    m = Inspeximus(path=None, embed=embed)
    for i in range(12):
        m.remember("record %%d " %% i + " ".join("term%%d" %% ((i * 7 + j) %% 45) for j in range(40)),
                   key="k%%d" %% i)
    return m, " ".join("term%%d" %% i for i in range(45))
for name, build in (("tie", tie), ("wide", wide)):
    for mode in ("lexical", "semantic", "hybrid", "auto"):
        seen = set()
        for _ in range(%(runs)d):
            m, q = build()
            seen.add("|".join(h["text"] for h in m.recall(q, k=5, mode=mode)))
        print("%%s/%%s\\t%%d\\t%%s" %% (name, mode, len(seen), sorted(seen)[0]))
'''

SEEDS = ("0", "1", "2", "3", "4", "5")
SEED_RUNS = 120


def test_one_answer_over_120_runs_x_6_hash_seeds_in_every_mode():
    """THE headline measurement, and the reason it has to be a subprocess: the root cause was candidate
    order arriving through sets of record-id STRINGS, whose iteration depends on per-process hash
    randomisation. A single-process test cannot see that at all.

    720 observations per (fixture, mode); 8 combinations; must collapse to exactly one order each.
    Supersedes an earlier 4-seed, single-mode, no-embedder version of this test, which this one contains.
    """
    code = _SEED_CHILD % {"root": ROOT, "texts": TEXTS, "query": QUERY, "runs": SEED_RUNS}
    union = collections.defaultdict(set)
    for seed in SEEDS:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=900,
                           env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"})
        assert r.returncode == 0, r.stderr[-800:]
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        assert len(lines) == 8, f"seed {seed} reported {len(lines)} combinations, expected 8"
        for ln in lines:
            combo, n, first = ln.split("\t")
            assert int(n) == 1, f"seed {seed}, {combo}: {n} distinct orders within one process"
            union[combo].add(first)
    bad = {c: len(v) for c, v in union.items() if len(v) != 1}
    assert not bad, (
        f"the answer depends on PYTHONHASHSEED: {bad} "
        f"({SEED_RUNS} runs x {len(SEEDS)} seeds = {SEED_RUNS * len(SEEDS)} observations per combination)")


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


# ── the crowded-store control: determinism must not cost the memory you asked for ──────────────────
# This is the check that killed the previous fix. Quantising the ranking score made recall repeatable and
# then merged away the margin that surfaced the target, so "deterministic but cannot find your memory in
# a crowded store" shipped as an improvement. Any future change to the ranking has to clear BOTH bars, so
# the scenario lives in the test suite rather than only in adk_audit.py.
CROWD_QUERY = "quarterly revenue target"
TARGET_TEXT = "the quarterly revenue target is 4.2 million"
NOISE = "user {} says the quarterly revenue target is important"


def _crowded(target_first=False, embed=None):
    """adk_audit.py::sc_crowded_store, as a store: 60 other users' memories plus the one you asked for."""
    m = Inspeximus(path=None, embed=embed)
    if target_first:
        m.remember(TARGET_TEXT, key="target")
    for i in range(60):
        m.remember(NOISE.format(i), key=f"n{i}")
    if not target_first:
        m.remember(TARGET_TEXT, key="target")
    return m


@pytest.mark.parametrize("mode", ["auto", "lexical", "semantic", "hybrid"])
def test_a_crowded_store_still_returns_the_memory_you_asked_for(mode):
    """60 other users' memories all matching the query, plus the one that answers it. The target must come
    back FIRST -- not merely be present."""
    hits = _crowded(embed=_embed).recall(CROWD_QUERY, k=5, mode=mode)
    texts = [h["text"] for h in hits]
    assert texts and texts[0] == TARGET_TEXT, f"mode={mode}: target not first, got {texts}"


def test_the_crowded_store_target_wins_the_bm25_channel_on_a_real_margin():
    """...and it is a REAL margin, not a rounding artefact — which is the whole reason the score is no
    longer quantised for ranking.

    Measured here: BM25 gives the target 0.02852 against 0.02423 for every competitor, a margin of
    4.29e-3. The run-to-run accumulation noise, measured on the `wide` fixture with the sorted-terms fix
    mutated out, is 2.66e-15. The margin is ~12 orders of magnitude above the noise, so the BM25 channel
    separates this store on content and owes nothing to luck.
    """
    from inspeximus.core import _tokens
    m = _crowded()
    scores = m._bm25_scores(_tokens(CROWD_QUERY), list(m._items))
    ti = next(i for i, r in enumerate(m._items) if r["text"] == TARGET_TEXT)
    best_other = max(s for i, s in enumerate(scores) if i != ti)
    margin = scores[ti] - best_other
    assert margin > 1e-6, f"BM25 no longer separates the target: {scores[ti]!r} vs {best_other!r}"
    assert margin > 1e6 * 2.66e-15, f"margin {margin:.3g} is within reach of the accumulation noise"


def test_the_overlap_coefficient_cannot_separate_a_crowded_store():
    """The honest limit behind the test above, asserted so nobody re-derives it from a passing suite.

    `mode='lexical'` scores relevance as |q & t| / min(|q|, |t|), which SATURATES: every one of the 61
    records contains all three query tokens, so all 61 score exactly 1.0 and the composite score ties
    61 ways. The target comes back first in the ADK scenario purely because it is written LAST and the
    declared tie-break is newest-first. Write it FIRST and it lands at rank 60.

    That is a property of the overlap-coefficient channel, not of determinism -- it is identical before
    and after this branch, and BM25 (`mode='hybrid'`) ranks the target first from either write position.
    Changing it means changing the lexical relevance function, which needs its own retrieval benchmark,
    not a determinism fix. If someone does change it, this test fails and says so.
    """
    from inspeximus.core import _tokens
    m = _crowded()
    q = _tokens(CROWD_QUERY)
    sims = {len(q & m._rec_tokens(r)) / min(len(q), len(m._rec_tokens(r))) for r in m._items}
    assert sims == {1.0}, f"the overlap coefficient no longer saturates here: {sorted(sims)}"

    ranks = {}
    for first in (False, True):
        hits = _crowded(target_first=first).recall(CROWD_QUERY, k=61, mode="lexical")
        ranks[first] = next(i for i, h in enumerate(hits) if h["text"] == TARGET_TEXT)
    assert ranks == {False: 0, True: 60}, f"the lexical tie no longer resolves by write order: {ranks}"

    # ...while the BM25-bearing channel finds it from either end, which is what makes the limit tolerable.
    for first in (False, True):
        hits = _crowded(target_first=first, embed=_embed).recall(CROWD_QUERY, k=5, mode="hybrid")
        assert hits[0]["text"] == TARGET_TEXT, f"hybrid, target_first={first}: {[h['text'] for h in hits]}"


# ── the same defect, three levers this file never covered ──────────────────────────────────────────
# `ts` is not a total order: the wall clock here has ~15 ms granularity, so 32 records written in a loop
# carry only 4 distinct timestamps spanning 2.3 ms, and WHICH records share a tick changes between runs.
# The main sort learned that and ranks ties by write position. Three opt-in levers had not, and each was
# reordering its pool on the bare timestamp. Measured on unmodified main, 60 identical runs in ONE process
# at a fixed PYTHONHASHSEED (so this is clock jitter, not hash randomisation):
#   rerank_by='recency'   31 distinct orders
#   resolve_conflicts     5 distinct orders, in every mode
#   tie_recent            3 distinct orders
LEVER_RUNS = 60


def _lever_store():
    """Two families that both match, and 20 records written in one loop so they share clock ticks."""
    m = Inspeximus(path=None, embed=_embed)
    for i in range(12):
        m.remember(f"record {i} " + " ".join(f"term{(i * 7 + j) % 45}" for j in range(40)), key=f"k{i}")
    for i in range(20):
        m.remember(f"the quarterly revenue target is important note {i}", key=f"q{i}")
    return m


@pytest.mark.parametrize("lever", [
    {"rerank_by": "recency"},
    {"tie_recent": 0.01},
    {"resolve_conflicts": True},
])
@pytest.mark.parametrize("mode", ["lexical", "hybrid"])
def test_the_opt_in_recency_levers_are_deterministic_too(lever, mode):
    """Each of these ordered its pool by `valid_from or ts` alone. The pool arrives in SCORE order, so
    when the clock happened to separate two records the sort moved them and when it did not the score
    order stood -- a different answer per run, with no set and no hash seed involved."""
    orders = collections.Counter()
    for _ in range(LEVER_RUNS):
        m = _lever_store()
        orders[tuple(h["text"] for h in m.recall(WIDE_QUERY, k=8, mode=mode, **lever))] += 1
    assert len(orders) == 1, (
        f"{lever} mode={mode}: {len(orders)} distinct top-k orders over {LEVER_RUNS} identical runs in "
        f"one process (counts {sorted(orders.values(), reverse=True)})")


def test_the_lever_fixture_really_does_share_clock_ticks():
    """Otherwise the test above passes over a case that could never have shown the bug: with one record
    per tick, `ts` is already a total order and the fix is unobservable."""
    ts = [r["ts"] for r in _lever_store()._items]
    assert len(set(ts)) < len(ts), f"every record got its own timestamp ({len(ts)} records, no tie)"
