"""ONE command that asserts every determinism property inspeximus is sold on.

Determinism is the property this library is marketed on, and it was spread across a dozen files and a
dozen docstrings with no single suite that asserts all of it. So a regression could ship, and a marketing
sentence could drift away from behaviour, without anything turning red.

This file is the instrument, not the fix. Where a property does NOT hold it is recorded as a
`xfail(strict=True)` carrying the measured number, so the failure is written down rather than softened --
and when the property starts holding, strict xfail turns the suite RED and forces someone to delete the
marker. A conformance suite that is green before the fix has measured nothing.

That mechanism has now fired once, which is the reason this header reads differently from its first
revision. In 2.0.0 `reinforce` defaults to False, and ELEVEN of the strict xfails below became xpasses in
a single run -- all of P5 and all of P5b. They are ordinary assertions now, and each block carries a
CONTROL pinned to `reinforce=True` that must still reproduce the old number, because an assertion that
the defect is gone is worthless next to a harness that has merely stopped looking.

MEASURED BASELINE -- 2026-08-01, v1.89.0 @ ba7a3d4, CPython 3.12.10 on Windows, numpy present;
re-measured 2026-08-03 for 2.0.0 on the same box. The `FAILS` rows below are the 1.89.0 numbers, kept
because they are what the controls now assert; the `-> HOLDS` suffix is the 2.0.0 result.
(The CI matrix is 3.9 / 3.11 / 3.12; nothing here uses syntax past 3.8.)
Reproduce with `python -m pytest tests/test_determinism_conformance.py -q -rxX`.

  P1  run-to-run, one store instance, 60 repeats x 4 modes  ..... HOLDS   1 distinct order in every mode
  P2  across 6 PYTHONHASHSEED values (0-5) .................... HOLDS   1 distinct output over 6 seeds
  P3  across 6 separate processes, seed unset ................. HOLDS   1 distinct output over 6 processes
  P4  the DECLARED tie policy (core.py:5584, `(-score, -pos)`:
      equal relevance -> the more recent memory first)
        lexical, semantic ...................................... HOLDS   6-way exact tie -> newest first
        hybrid, auto ........................................... FAILS   identical content does not tie:
                                                                         relevances 1.000/0.985/0.984/
                                                                         0.969/0.968/0.953, top-1 is the
                                                                         OLDEST of the tied set
  P5  read purity -- a surface documented read-only leaves the
      ranking state (value, last_access, mtype, good, bad)
      byte-identical.  11-record store, ONE call.  MODE COVERAGE
      IS PART OF THE NUMBER: the surfaces that take a `mode` are
      measured in all four; `why_recalled` / `selection_integrity`
      / `admit` take none, so they are measured in both routings
      of the default `auto` (threshold 300 -> lexical, 1 -> hybrid):
        recall(reinforce=False), both routings .................. HOLDS   0/11 records mutated
        why_recalled(), both routings .......................... HOLDS   0/11
        selection_integrity(), both routings ................... HOLDS   0/11
        recall()  lexical ...................................... 1.89.0 4/11  -> 2.0.0 HOLDS 0/11
                  semantic ..................................... 1.89.0 4/11  -> 2.0.0 HOLDS 0/11
                  hybrid ....................................... 1.89.0 5/11  -> 2.0.0 HOLDS 0/11
                  auto -- THE SHIPPED DEFAULT PATH ............. 1.89.0 5/11  -> 2.0.0 HOLDS 0/11
        admit() on a duplicate (a no-write outcome) ............ 1.89.0 1/11  -> 2.0.0 HOLDS 0/11
                                                                 (was value 1.00 -> 1.25, both routings)
        mcp_server.token_report() (lexical routing) ............ 1.89.0 4/11  -> 2.0.0 HOLDS 0/11
        state_digest() after any of the above .................. UNCHANGED, always -- `value`/`good`/`bad`
                                                                 are outside the digest BY DESIGN
                                                                 (core.py:3594 docstring), so the digest
                                                                 cannot detect a read-purity violation and
                                                                 this suite reads the ranking state direct

      The default path mutates 5/11, not 4/11. An earlier revision of this file measured every P5 case at
      the default threshold, where an 11-record store routes auto -> LEXICAL (core.py:5390), and so
      reported the lexical figure under the heading "the shipped default". Hybrid touches MORE records
      because RRF admits a candidate when EITHER channel is non-empty (core.py:5433).
  P5b the consequence of P5: the answer to query N+1 depends on
      which queries ran before it.  8 permutations (the reversal
      + 7 rotations) x 8 queries = 64 answers per mode:
        reinforce=True  lexical ................................ 1.89.0   5/64 =   7.81%
                        semantic ............................... 1.89.0  10/64 =  15.62%
                        hybrid ................................. 1.89.0  64/64 = 100.00%
                        auto ................................... 1.89.0  64/64 = 100.00%
        reinforce=False  all four modes ........................ HOLDS    0/64 =   0.0000
                                                                 -- the 2.0.0 DEFAULT

      Read that hybrid row twice: under the 1.89.0 default, NO answer in hybrid mode survived a
      reordering of the same question set. It was the worst-hit mode because RRF ranks are coarse, so a
      value nudge crosses a rank boundary easily -- and `auto` routes there on any store past
      semantic_threshold, so it was the default path, not a corner. That row is why 2.0.0 flipped the
      default rather than documenting the behaviour. The reinforce=True numbers are still measured, by
      the controls, so this table stays falsifiable in both directions.

  P6  mode coverage, and what it means per property:
        P1, P2, P3, P4, P5b, recall() under P5 ... all four modes, and every call asserts the mode recall
                                                  ACTUALLY resolved to, so a silent lexical fallback
                                                  fails instead of passing as three extra green ticks
        why_recalled / selection_integrity /      no mode parameter exists -- both routings of the
        admit under P5 .......................... default `auto` instead (lexical and hybrid)
        mcp_server.token_report ................. lexical routing only (its store is built by the module
                                                  at the default threshold)
        P1's tie control ........................ lexical and semantic only. Duplicate-score counts at
                                                  k=6: lexical 4, semantic 2, hybrid 0, auto 0 -- a tie
                                                  is NOT EXPRESSIBLE in hybrid (see P4), so those two
                                                  modes assert stability in the absence of ties, which
                                                  is a weaker guarantee, stated rather than glossed

THE SAME MEASUREMENT WITHOUT NUMPY. numpy is optional, and the base CI leg installs only
`pytest cryptography pyyaml` -- so `center_embeddings` and the vectorised cosine are both out of the
path there. Measured in that environment, every property lands on the same verdict and only the
magnitudes move, which is why each number above is quoted with its environment rather than as a
constant of the library:

        P4 hybrid/auto relevances .... 1.000/0.984/0.968/0.952/0.938/0.923 (spread 0.077, was 0.047)
        P5 recall() / admit() ........ 4/11 and 1/11 at reinforce=True -- unchanged
        P5b lexical/semantic/hybrid/auto  5/64, 17/64, 60/64, 60/64 at reinforce=True -- every one still
                                          non-zero, which is why the P5b control asserts a FLOOR of 60
        P5b reinforce=False control .. 0/64 in every mode, in BOTH environments

mcp is absent there too, so the MCP surface test SKIPS rather than xfailing. Verified deliberately: a
`strict=True` xfail marker does not convert a skip into a failure, so the zero-dependency leg stays
green (exit 0) without weakening anything.

WHAT IS DELIBERATELY NOT ASSERTED HERE, and why.

  Permutation invariance -- "the same records inserted in a different order rank the same" -- is NOT a
  property of this library and is not a defect. `core.py:5584` sorts on `(-score, -insertion_position)`:
  an intentional newest-first tie-break, argued at length in the comment block at core.py:5506-5584 and
  the foundation of the measured `tie_recent` lever. A test demanding permutation invariance would
  require deleting a measured win to satisfy a property no benchmark rewards. What IS asserted is the
  DECLARED policy (P4) plus a positive control that insert order is load-bearing for ties at all -- so a
  future deletion of the tie-break cannot pass unnoticed.

  Run-to-run determinism (P1-P3) is already fixed and already covered by
  `tests/test_recall_is_deterministic.py`, which owns the mechanism-level assertions (BM25 term order,
  the sub-second decay factor). This file re-asserts the user-visible property across all four modes and
  does not duplicate the mechanism tests.

THE EMBEDDER. Semantic and hybrid need one, and `recall` falls back to lexical without a word of
complaint when it is missing -- so a four-mode parametrization over an embedder-less store would run
three vacuous copies of the lexical test. This file therefore ships a stdlib SHA-1 hashing embedder
(identical on every machine, every run and every process; the same construction the influence-gate probes
use) and every test asserts `_last_mode`, so a silent fallback fails instead of passing.
It is NOT a dense retriever -- it has no synonymy -- and no retrieval-quality number may be quoted from
it. Determinism is a property of the ranking machinery, not of the encoder, which is why a deterministic
stand-in is the right instrument here.
"""
from __future__ import annotations

import collections
import hashlib
import importlib
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from inspeximus import Inspeximus  # noqa: E402

MODES = ("lexical", "semantic", "hybrid", "auto")
HASH_DIM = 96


# ── the deterministic fixture ────────────────────────────────────────────────────────────────────
def deterministic_embed(text: str) -> list:
    """token -> SHA-1 -> signed bucket, L2-normalised. No model, no download, no randomness: identical
    on every machine, every run and every process, which is the only reason a cross-process assertion
    about the LIBRARY is not really an assertion about the fixture."""
    vec = [0.0] * HASH_DIM
    for tok in re.findall(r"[a-z0-9]+", (text or "").lower()):
        h = hashlib.sha1(tok.encode()).digest()
        vec[h[0] % HASH_DIM] += 1.0 if h[1] & 1 else -1.0
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec] if norm else vec


# A corpus built to make reinforcement VISIBLE, not to flatter the ranker:
#   * rows 0-1 are a near-tie for "alpha decay in heavy nuclei"...
#   * ...and row 2 reaches only row 0, so asking about tunnelling first reinforces one side of that tie.
#   * rows 11-14 tie exactly for "alpha bravo charlie", which is where the tie-break lives.
CORPUS = [
    "quantum tunneling explains alpha decay in heavy nuclei",
    "alpha decay emits a helium nucleus from heavy nuclei",
    "quantum tunneling lets a particle cross a barrier",
    "the capital of France is Paris",
    "France borders Spain and Germany",
    "Paris hosted the 2024 Olympics",
    "photosynthesis converts light to chemical energy",
    "the mitochondria is the powerhouse of the cell",
    "chlorophyll gives plants their green color",
    "the Eiffel Tower is in Paris",
    "cellular respiration happens in the mitochondria",
    "alpha bravo charlie item one",
    "alpha bravo charlie item two",
    "alpha bravo charlie item three",
    "alpha bravo charlie item four",
]
# Many-term records with a heavily shared vocabulary: BM25 sums ~40 addends per record over a 45-term
# query, which is the shape that made float-addition order visible across hash seeds in the first place.
CORPUS += ["record%d " % i + " ".join("term%d" % ((i * 7 + j) % 45) for j in range(40)) for i in range(12)]

QUERIES = [
    "alpha decay in heavy nuclei",
    "quantum tunneling barrier",
    "what is in Paris France",
    "alpha bravo charlie",
    "how do plants make energy",
    "where does respiration happen",
    "the Eiffel Tower",
    "mitochondria of the cell",
]
BM25_QUERY = " ".join("term%d" % i for i in range(45))


def build_store(texts=None, threshold: int = 300):
    """`threshold` is what makes `auto` reachable: at the shipped 300 a small store routes auto->lexical,
    so an `auto` test at the default threshold silently re-runs the lexical test."""
    m = Inspeximus(path=None, embed=deterministic_embed)
    m.semantic_threshold = threshold
    for i, t in enumerate(CORPUS if texts is None else texts):
        m.remember(t, key="k%d" % i)
    return m


def expected_mode(mode: str) -> str:
    """What recall must resolve `mode` to on the fixtures here. `auto` is always driven with
    threshold=1 so it routes to the hybrid it exists to select."""
    return "hybrid" if mode == "auto" else mode


def threshold_for(mode: str) -> int:
    return 1 if mode == "auto" else 300


def answer(store, query, mode, k=5, **kw):
    hits = store.recall(query, k=k, mode=mode, **kw)
    assert store._last_mode == expected_mode(mode), (
        f"recall resolved mode={mode!r} to {store._last_mode!r} -- this test would have been a duplicate "
        f"of the lexical one")
    return tuple(h["text"] for h in hits)


def rank_state(store):
    """Exactly the fields that decide WHICH memory wins, sorted by id so the comparison is order-free."""
    return [(r.get("id"), r.get("value"), r.get("last_access"), r.get("mtype"),
             r.get("good"), r.get("bad")) for r in sorted(store._items, key=lambda x: x.get("id") or "")]


# ── fixture integrity: none of the above may be quietly wrong ────────────────────────────────────
def test_the_embedder_is_deterministic_across_processes():
    """Every cross-process claim in this file is about the LIBRARY only if the fixture is identical in
    every process. A hash-randomised embedder would make P2/P3 assertions about themselves."""
    a = deterministic_embed("the old lighthouse still guides ships")
    assert a == deterministic_embed("the old lighthouse still guides ships")
    assert len(a) == HASH_DIM
    assert abs(sum(x * x for x in a) - 1.0) < 1e-9, "unnormalised vectors make cosine ranking meaningless"
    assert a != deterministic_embed("a completely different sentence about budgets")

    # Compared against THIS process's vector, not merely child-against-child. `len(set(...)) == 1` is
    # also what three children that all imported a different embedder would report, and what three that
    # all printed "" would report.
    out = _run_child("print(repr(t.deterministic_embed('the old lighthouse still guides ships')))",
                     seeds=("0", "1", "2"))
    assert set(out.values()) == {repr(a)}, (
        f"the child processes do not compute the parent's vector: {sorted(set(out.values()))[:1]}")


@pytest.mark.parametrize("mode", MODES)
def test_every_mode_is_actually_reached(mode):
    """P6, asserted as its own test rather than only as a side condition: without an embedder recall
    falls back to lexical silently, and the whole four-mode parametrization becomes one test run four
    times. `answer()` raises if the resolved mode is not the requested one."""
    store = build_store(threshold=threshold_for(mode))
    assert store.embed is not None
    assert answer(store, QUERIES[0], mode, reinforce=False)
    assert store._last_mode == expected_mode(mode)


# ── P1: same store, same query, repeated in process ──────────────────────────────────────────────
@pytest.mark.parametrize("mode", MODES)
def test_p1_repeating_one_query_on_one_store_gives_one_answer(mode):
    """Repeated on the SAME store instance, which is the case a caller actually has -- and the case
    where reinforcement is live, so this is not a restatement of the fresh-store test in
    tests/test_recall_is_deterministic.py."""
    store = build_store(threshold=threshold_for(mode))
    orders = collections.Counter(answer(store, QUERIES[0], mode) for _ in range(60))
    assert len(orders) == 1, (
        f"mode={mode}: {len(orders)} distinct top-k orders over 60 identical calls "
        f"(counts {sorted(orders.values(), reverse=True)})")


@pytest.mark.parametrize("mode", ["lexical", "semantic"])
def test_p1_the_fixture_contains_a_tie_so_the_test_could_have_failed(mode):
    """The rotation defect lived in ties. A fixture without one runs green over a case that could never
    have shown it, so the tie is asserted rather than assumed -- once per mode, because "the lexical
    fixture ties" says nothing about the semantic one.

    hybrid and auto are absent on purpose and NOT by oversight: RRF gives exactly-equivalent records
    distinct fused scores, so a tie is not expressible there at all. That is itself a measured finding
    and it has its own test -- see the hybrid/auto xfail on
    test_p4_equal_relevance_returns_the_more_recent_memory_first. Listing them here would only produce a
    control that always fails for a reason unrelated to P1."""
    store = build_store(threshold=threshold_for(mode))
    hits = store.recall("alpha bravo charlie", k=6, mode=mode, reinforce=False)
    assert store._last_mode == mode, (mode, store._last_mode)
    scores = [h["score"] for h in hits]
    assert len(scores) - len(set(scores)) >= 2, f"mode={mode}: no tie in the fixture: {scores}"


# ── P2 / P3: across hash seeds, across processes ─────────────────────────────────────────────────
_CHILD = """
import os, sys
sys.path.insert(0, @@ROOT@@)
sys.path.insert(0, @@HERE@@)
import test_determinism_conformance as t
@@BODY@@
"""


def _run_child(body: str, seeds=None, repeats: int = 0) -> dict:
    """Run `body` in a fresh interpreter. With `seeds`, PYTHONHASHSEED is pinned per run; with
    `repeats`, it is REMOVED so each process randomises its own -- the two halves of P2 and P3."""
    code = (_CHILD.replace("@@ROOT@@", repr(ROOT)).replace("@@HERE@@", repr(HERE))
            .replace("@@BODY@@", body))
    out = {}
    runs = [("seed=" + s, s) for s in (seeds or [])] + [("run%d" % i, None) for i in range(repeats)]
    for label, seed in runs:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        # PYTHONOPTIMIZE compiles `assert` OUT. The only thing stopping P2/P3 from being four copies of
        # the lexical answer is the child's `_last_mode` check, so under `PYTHONOPTIMIZE=1` this test
        # would go green having measured one channel four times and called it four. The child raises
        # explicitly as well (see _ANSWER_BODY) -- belt and braces, because either alone is one
        # environment variable away from silence.
        env.pop("PYTHONOPTIMIZE", None)
        if seed is None:
            env.pop("PYTHONHASHSEED", None)      # let the child randomise; that is the point of P3
        else:
            env["PYTHONHASHSEED"] = seed
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           timeout=600, env=env, cwd=ROOT)
        assert r.returncode == 0, f"{label}: child failed\n{r.stderr[-1500:]}"
        out[label] = r.stdout.strip()
    return out


_ANSWER_BODY = """
from inspeximus.core import _tokens
lines = []
for mode in t.MODES:
    store = t.build_store(threshold=t.threshold_for(mode))
    for q in list(t.QUERIES) + [t.BM25_QUERY]:
        hits = store.recall(q, k=6, mode=mode)
        if store._last_mode != t.expected_mode(mode):
            raise AssertionError("resolved %r, not %r" % (store._last_mode, t.expected_mode(mode)))
        lines.append(mode + "|" + "|".join(h["text"] for h in hits))
        lines.append("scores|" + "|".join(repr(h["score"]) for h in hits))
    # RAW, full-precision channel scores. recall() rounds to three places on the way out, and the
    # divergence class this whole test exists for is ~1e-10 -- invisible at three places. BM25 covers
    # the lexical channel; the cosines cover semantic and hybrid, which otherwise had no raw readout at
    # all and so could have diverged in exactly the way P2 was built to catch, silently.
    qvec = store._qvec(t.BM25_QUERY, store.embed_query)
    lines.append("bm25|" + ";".join(repr(x) for x in store._bm25_scores(
        _tokens(t.BM25_QUERY), list(store._items))))
    lines.append("sims|" + ";".join(
        repr(store._similarity(t.BM25_QUERY, r, qvec)) for r in store._items))
print("\\n".join(lines))
"""


def test_p2_the_answer_is_the_same_under_six_hash_seeds():
    """Hash randomisation is per PROCESS, so no in-process loop can see this. The fixture carries the
    BM25 query on purpose: 45 query terms against records of 40 tokens is ~40 float addends per score,
    where summation order is visible, and the raw `_bm25_scores` vector is printed at full precision
    because recall rounds the score to three places on the way out -- a test reading only the rounded
    score cannot see 1e-10 of divergence."""
    out = _run_child(_ANSWER_BODY, seeds=("0", "1", "2", "3", "4", "5"))
    assert len(set(out.values())) == 1, _diff(out)


def test_p3_the_answer_is_the_same_in_six_separate_processes():
    """PYTHONHASHSEED unset, so every child randomises independently -- process identity, not a pinned
    seed, is the variable here."""
    out = _run_child(_ANSWER_BODY, repeats=6)
    assert len(set(out.values())) == 1, _diff(out)


def _diff(out: dict) -> str:
    groups = collections.defaultdict(list)
    for label, text in out.items():
        groups[text].append(label)
    variants = list(groups)
    if len(variants) < 2:
        # Only reachable if the caller's assertion was wrong about its own input; an IndexError here
        # would replace a real failure message with a crash inside the reporting code.
        return f"{len(variants)} variant(s) over {len(out)} processes -- nothing to diff"
    a, b = variants[0].splitlines(), variants[1].splitlines()
    first = next((f"line {i}:\n  A {x[:180]}\n  B {y[:180]}"
                  for i, (x, y) in enumerate(zip(a, b)) if x != y), "(same lines, different length)")
    return (f"{len(variants)} distinct answers across {len(out)} processes "
            f"{[groups[v] for v in variants]}\n{first}")


# ── P4: the DECLARED tie policy ──────────────────────────────────────────────────────────────────
# Same TOKENS, different raw text -> an exact tie in every channel, and still tellable apart in the
# result. Ballast is not decoration: with only identical vectors in the store, `center_embeddings`
# subtracts their mean and zeroes every row, so semantic recall returns nothing at all and the test
# would assert over an empty list.
TIED = ["alpha bravo charlie delta",
        "Alpha, bravo charlie delta!",
        "ALPHA bravo charlie delta.",
        "alpha  bravo   charlie  delta",
        "(alpha) bravo charlie delta",
        "alpha; bravo, charlie: delta"]
BALLAST = ["the capital of France is Paris",
           "photosynthesis converts light to chemical energy",
           "the mitochondria is the powerhouse of the cell",
           "chlorophyll gives plants their green color"]
TIE_QUERY = "alpha bravo charlie delta"


def _tie_store(order, threshold):
    m = Inspeximus(path=None, embed=deterministic_embed)
    m.semantic_threshold = threshold
    for i, t in enumerate(BALLAST):
        m.remember(t, key="b%d" % i)
    for i in order:
        m.remember(TIED[i], key="t%d" % i)
    return m


def _tie_order(order, mode):
    store = _tie_store(order, threshold_for(mode))
    hits = store.recall(TIE_QUERY, k=len(TIED), mode=mode, reinforce=False)
    assert store._last_mode == expected_mode(mode), (mode, store._last_mode)
    # Checked BEFORE indexing: if a BALLAST record reaches the top-6 the comprehension below dies with
    # `ValueError: 'the capital of France is Paris' is not in list`, which says nothing about the tie.
    intruders = [h["text"] for h in hits if h["text"] not in TIED]
    assert not intruders, f"mode={mode}: the tie fixture lost records to {intruders}"
    return hits, [TIED.index(h["text"]) for h in hits]


@pytest.mark.parametrize("mode", [
    "lexical",
    "semantic",
    pytest.param("hybrid", marks=pytest.mark.xfail(strict=True, reason=(
        "MEASURED 2026-08-01: six records with identical tokens do not tie in hybrid. RRF fuses two "
        "STABLE rank orders over the candidate pool, so pool position becomes the score: relevances "
        "1.000/0.985/0.984/0.969/0.968/0.953 with numpy (spread 0.047), 1.000/0.984/0.968/0.952/0.938/"
        "0.923 without it (spread 0.077), and in both the top-1 of an exactly-equivalent set is the "
        "OLDEST of them. The declared newest-first tie-break at core.py:5584 never sees a tie and "
        "cannot apply."))),
    pytest.param("auto", marks=pytest.mark.xfail(strict=True, reason=(
        "auto routes to hybrid once the store passes semantic_threshold, and inherits hybrid's result: "
        "identical content, distinct fused relevances, oldest-first at k=1."))),
])
def test_p4_equal_relevance_returns_the_more_recent_memory_first(mode):
    """The DECLARED policy, quoted from core.py:5584 -- `scored.sort(key=(-score, -insertion_position))`,
    i.e. equal relevance -> the more recent memory first. Not permutation invariance: that is
    deliberately false here (see the module docstring), and asserting it would demand deleting the
    tie-break the measured `tie_recent` lever is built on.

    The fixture-integrity assertion comes FIRST and is load-bearing. Without it, a mode where identical
    content produces DISTINCT relevances would run this test over a case with no tie in it and report a
    policy it never exercised -- which is exactly what hybrid does."""
    hits, order = _tie_order(range(len(TIED)), mode)
    assert len(hits) == len(TIED), f"the tie fixture lost records: {order}"

    rels = [h["relevance"] for h in hits]
    assert len(set(rels)) == 1, (
        f"mode={mode}: identical content did not produce equal relevance, so the declared tie policy is "
        f"unreachable here -- relevances {rels}")
    scores = [h["score"] for h in hits]
    assert len(set(scores)) == 1, f"mode={mode}: equal relevance but unequal score: {scores}"

    assert order == list(reversed(range(len(TIED)))), (
        f"mode={mode}: equal relevance must return newest first (core.py:5584); got insertion indices "
        f"{order}")


@pytest.mark.parametrize("mode", MODES)
def test_p4_control_insert_order_is_load_bearing_in_every_mode(mode):
    """The positive control for P4, and it holds in all four modes including the two that xfail above.

    It asserts only that insert order DECIDES the order of equivalent records -- reversing the inserts
    mirrors the result -- without pinning the direction, which is what P4 does. Kept separate on purpose:
    if the tie-break were ever deleted "to make recall permutation-invariant", ties would fall through to
    arrival order and this control would still pass, while P4 would fail. Neither test alone is enough;
    that is why there are two."""
    n = len(TIED)
    _, fwd = _tie_order(range(n), mode)
    _, rev = _tie_order(reversed(range(n)), mode)
    assert sorted(fwd) == sorted(rev) == list(range(n))
    assert rev == [n - 1 - i for i in fwd], (
        f"mode={mode}: reversing the insert order did not mirror the result -- insert order is not what "
        f"orders equivalent records here. fwd={fwd} rev={rev}")


# ── P5: read purity ──────────────────────────────────────────────────────────────────────────────
PURITY_CORPUS = CORPUS[:11]
PURITY_QUERY = "what is in Paris France"


# The routing axis. `why_recalled`, `selection_integrity` and `admit` all call recall internally with
# the DEFAULT mode='auto' and take no mode argument, so the only way to exercise them in more than one
# channel is the threshold that decides what 'auto' resolves to. Both resolutions are covered.
# This matters: at threshold=300 an 11-record store routes auto -> LEXICAL (core.py:5390), so a suite
# that only ever built stores at the default threshold would measure the lexical channel and report it
# as the shipped default. It did, for one revision, and the numbers differ (4/11 vs 5/11).
ROUTES = [("lexical", 300), ("hybrid", 1)]


def _purity_delta(call, mode="lexical", threshold=None) -> tuple:
    """Returns (records_mutated, total, digest_changed) for one call against a fresh store.

    `mode` is the channel the call is REQUIRED to have actually used -- asserted after the call, so a
    surface that silently fell back to lexical cannot be reported as a measurement of hybrid. `threshold`
    is what makes that reachable for the surfaces that take no mode argument: they always ask for `auto`,
    and only semantic_threshold decides what `auto` becomes. Pass it explicitly (the ROUTES axis) rather
    than deriving it from `mode` -- `threshold_for('hybrid')` is 300, which routes an internal `auto` to
    LEXICAL, and this assertion caught exactly that mistake being made here."""
    store = build_store(PURITY_CORPUS,
                        threshold=threshold_for(mode) if threshold is None else threshold)
    before, digest_before = rank_state(store), store.state_digest()
    call(store)
    assert store._last_mode == expected_mode(mode), (
        f"the call resolved to {store._last_mode!r}, not the {expected_mode(mode)!r} this measurement "
        f"claims to be about")
    after, digest_after = rank_state(store), store.state_digest()
    assert [r[0] for r in before] == [r[0] for r in after], "a read added or removed a record"
    return sum(a != b for a, b in zip(before, after)), len(before), digest_before != digest_after


@pytest.mark.parametrize("route,threshold", ROUTES)
@pytest.mark.parametrize("name,call", [
    ("recall(reinforce=False)", lambda s: s.recall(PURITY_QUERY, k=5, reinforce=False)),
    ("why_recalled", lambda s: s.why_recalled(PURITY_QUERY)),
    ("selection_integrity", lambda s: s.selection_integrity(PURITY_QUERY, k=5)),
])
def test_p5_the_pure_read_surfaces_leave_the_ranking_state_untouched(name, call, route, threshold):
    """These three already pass reinforce=False internally, and this pins that they keep doing it. An
    inspector that changes the state it measures is the failure `why_recalled` was fixed for.

    Run in both routings, because "pure in the lexical channel" is not the claim the docs make."""
    moved, total, digest_changed = _purity_delta(call, mode=route, threshold=threshold)
    assert not digest_changed, f"{name} ({route}) changed state_digest()"
    assert moved == 0, f"{name} ({route}) mutated {moved}/{total} records' ranking state"


@pytest.mark.parametrize("route,threshold", ROUTES)
def test_p5_the_read_purity_probe_can_actually_see_a_mutation(route, threshold):
    """The control. Every assertion above reports zero, and a probe that reads the wrong FIELDS would
    report zero for exactly the same reason. Hand it a known write and require it to say so -- and
    require the exact count, so a probe that reported "everything moved" would fail too."""
    moved, total, _ = _purity_delta(
        lambda s: s.credit([s.recall(PURITY_QUERY, k=1, reinforce=False)[0]["id"]], True),
        mode=route, threshold=threshold)
    assert (moved, total) == (1, len(PURITY_CORPUS)), (
        f"the probe should see exactly the one record credit() wrote, out of {len(PURITY_CORPUS)}; "
        f"got {moved}/{total}")


@pytest.mark.parametrize("route,threshold", ROUTES)
def test_p5_state_digest_is_blind_to_the_ranking_state_by_design(route, threshold):
    """Why this suite reads `value`/`last_access` directly instead of diffing `state_digest()`.

    The digest covers id/status/ts/key/tenant/content-hash and deliberately EXCLUDES `value` and
    `good`/`bad` (core.py:3594) -- because `recall()` bumps them, so a digest covering them would change
    on every read and no witness could ever match. The consequence is that the digest cannot detect a
    ranking-state change, and a conformance test built on it would be green no matter what recall did.

    Demonstrated with `credit()` -- an OUTRIGHT, undisputed write -- rather than with recall's own
    reinforcement. If it used recall it would be asserting that the defect is still present, and would
    turn red the day A1 fixes it: a second failure for a fix, in a test whose subject is the digest."""
    moved, total, digest_changed = _purity_delta(
        lambda s: s.credit([s.recall(PURITY_QUERY, k=1, reinforce=False)[0]["id"]], True),
        mode=route, threshold=threshold)
    assert moved == 1, f"the fixture did not write anything, so blindness proves nothing: {moved}/{total}"
    assert not digest_changed, (
        "state_digest() now moves when only `value`/`good`/`bad` change; it can no longer serve as a "
        "stable witness, and this suite's justification for reading the ranking state directly needs "
        "rewriting")


@pytest.mark.parametrize("mode,measured", [
    ("lexical", "4/11"),
    ("semantic", "4/11"),
    ("hybrid", "5/11"),
    ("auto", "5/11"),
])
def test_p5_recall_is_a_read(mode, measured):
    """BOTH halves of the public property -- the digest AND the ranking vector -- are asserted, even
    though only the second can currently fail. Stating it in full is the point: if the digest is ever
    widened to cover `value`, this test keeps meaning what it says instead of quietly narrowing.

    Parametrized over all four channels because the number is NOT the same in each (4/11 lexical and
    semantic, 5/11 hybrid and auto) and the default path is the 5.

    `measured` is the pre-2.0.0 baseline, when `reinforce` defaulted to True and this test was a strict
    xfail carrying that number. It is kept in the id so a run can be compared against what the defect
    used to cost, and it is now the CONTROL's expectation rather than this test's."""
    moved, total, digest_changed = _purity_delta(lambda s: s.recall(PURITY_QUERY, k=5, mode=mode),
                                                 mode=mode)
    assert not digest_changed, f"recall(mode={mode}) changed state_digest()"
    assert moved == 0, (
        f"recall(mode={mode}) mutated {moved}/{total} records' ranking state (baseline {measured})")


@pytest.mark.parametrize("mode,measured", [
    ("lexical", 4), ("semantic", 4), ("hybrid", 5), ("auto", 5),
])
def test_p5_control_the_purity_harness_still_sees_a_mutating_read(mode, measured):
    """The control for the four assertions above, and the reason they are not vacuous.

    Passing `reinforce=True` explicitly restores the pre-2.0.0 behaviour, and `_purity_delta` must
    still report exactly the number the strict xfails used to carry. Without this, a `_purity_delta`
    that stopped observing anything -- a rank_state() that returned a constant, a corpus that stopped
    reaching top-k -- would make every P5 test above pass while measuring nothing at all."""
    moved, total, _ = _purity_delta(
        lambda s: s.recall(PURITY_QUERY, k=5, mode=mode, reinforce=True), mode=mode)
    # A FLOOR, not the exact count, for the same reason the P5b control uses one: the number is
    # environment-dependent. `measured` is Windows/3.12 with numpy; the base CI leg has no numpy and the
    # semantic channel moves 5 there, not 4. Pinning equality made this fail on 3.11 for the wrong
    # reason -- the harness was working perfectly. 3 is below every environment we have measured and
    # far above the 0 a blind harness reports, which is the only thing this control has to separate.
    assert moved >= 3, (
        f"mode={mode}: the harness saw {moved}/{total} records move on a reinforce=True read "
        f"(baseline {measured}/11 on Windows+numpy, 5/11 on the no-numpy leg). Below 3 means either the "
        f"harness has stopped observing the ranking state or reinforcement itself has changed -- in both "
        f"cases the purity assertions above are no longer evidence of anything")


@pytest.mark.parametrize("route,threshold", ROUTES)
def test_p5_a_refused_admission_writes_nothing(route, threshold):
    store = build_store(PURITY_CORPUS, threshold=threshold)
    before, digest_before = rank_state(store), store.state_digest()
    values_before = {r[0]: r[1] for r in before}
    res = store.admit(PURITY_CORPUS[0])
    assert res["admitted"] is False and res["reason"] == "duplicate", res
    assert store._last_mode == route, (route, store._last_mode)
    assert len(store._items) == len(before), "a refused admission appended a record"
    assert store.state_digest() == digest_before, "a refused admission changed state_digest()"
    after = rank_state(store)
    moved = [(r[0], values_before[r[0]], r[1]) for a, r in zip(before, after) if a != r]
    # The DELTA, not just the count: the xfail reason quotes 1.00 -> 1.25, and a count-only assertion
    # would keep reporting 1/11 while that number silently changed at core.py:5732.
    assert not moved, (
        f"a refused admission mutated {len(moved)}/{len(before)} records' ranking state; "
        + "; ".join(f"{i}: value {v0} -> {v1}" for i, v0, v1 in moved))


def test_p5_the_mcp_sizing_report_does_not_reorder_the_store(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "mcp.json"))
    # EVERY env var the module reads at import, not the three that came to mind. inspeximus is dogfooded
    # over MCP on the maintainer's box, so `INSPEXIMUS_EMBED_URL` and friends are exactly the ones likely
    # to be set -- and a reload that picked them up would build a NETWORK embedder and issue HTTP from a
    # unit test, on the developer's machine only. A default-posture test cannot inherit a posture.
    for var in ("INSPEXIMUS_RECEIPTS", "INSPEXIMUS_ECHO_GUARD", "INSPEXIMUS_EMBED_URL",
                "INSPEXIMUS_EMBED_MODEL", "INSPEXIMUS_EMBED_KEY", "INSPEXIMUS_NOMIC_PREFIX",
                "INSPEXIMUS_MAX_K", "INSPEXIMUS_SNIPPET_CHARS", "INSPEXIMUS_READ_RESOLVER",
                "INSPEXIMUS_RECEIPT_PUBKEY"):
        monkeypatch.delenv(var, raising=False)
    import inspeximus.mcp_server as mcp_server
    mcp_server = importlib.reload(mcp_server)
    for i, text in enumerate(PURITY_CORPUS):
        mcp_server._MEM.remember(text, key="k%d" % i)

    before, digest_before = rank_state(mcp_server._MEM), mcp_server._MEM.state_digest()
    fn = getattr(mcp_server.token_report, "fn", mcp_server.token_report)   # unwrap the @mcp.tool()
    report = fn(PURITY_QUERY, k=5)
    assert report["k"] > 0, f"the report found nothing, so it exercised nothing: {report}"
    assert mcp_server._MEM.state_digest() == digest_before, "token_report changed state_digest()"
    moved = sum(a != b for a, b in zip(before, rank_state(mcp_server._MEM)))
    assert moved == 0, f"token_report mutated {moved}/{len(before)} records' ranking state"


# ── P5b: the consequence -- the answer depends on which queries ran first ────────────────────────
def _permutations(queries):
    """The reversal plus every rotation: deterministic, so the failure rate below is a number that can
    be quoted, not a sample from an RNG whose shuffle could change between interpreters."""
    return [list(reversed(queries))] + [queries[r:] + queries[:r] for r in range(1, len(queries))]


# 8 permutations x 8 queries. Asserted as a denominator everywhere below, because `changed == 0` is also
# what an empty sweep reports.
SWEEP_SIZE = len(_permutations(list(QUERIES))) * len(QUERIES)


def _order_sensitivity(mode, **kw):
    """Fraction of (permutation, query) pairs whose answer differs from the canonical-order answer.
    Every permutation starts from a FRESH store, so the only variable is the order of the questions."""
    base_store = build_store(threshold=threshold_for(mode))
    base = {q: answer(base_store, q, mode, **kw) for q in QUERIES}
    changed = total = 0
    for perm in _permutations(list(QUERIES)):
        store = build_store(threshold=threshold_for(mode))
        got = {q: answer(store, q, mode, **kw) for q in perm}
        for q in QUERIES:
            total += 1
            changed += (got[q] != base[q])
    return changed, total


@pytest.mark.parametrize("mode,measured", [
    ("lexical", "5/64 = 7.81%"),
    ("semantic", "10/64 = 15.62%"),
    ("hybrid", "64/64 = 100.00%"),
    ("auto", "64/64 = 100.00%"),
])
def test_p5b_asking_the_same_questions_in_a_different_order_gives_the_same_answers(mode, measured):
    """The user-visible consequence of P5: because a read is a write, the answer to question N+1 depends
    on which questions were asked before it. `measured` is carried in the id so the number a run reports
    can be compared against the baseline without reading the docstring."""
    changed, total = _order_sensitivity(mode)
    assert (changed, total) == (0, SWEEP_SIZE), (
        f"mode={mode}: {changed}/{total} answers changed when the question order changed "
        f"(baseline {measured})")


@pytest.mark.parametrize("mode", MODES)
def test_p5b_control_reinforce_false_is_exactly_order_independent(mode):
    """The control, and the reason P5b is attributed to reinforcement rather than to a second unknown
    defect: the identical sweep with reinforce=False is exactly 0/64 in every mode.

    The DENOMINATOR is asserted with the numerator. `changed == 0` alone is satisfied by a sweep that
    compared nothing at all -- if `_permutations` ever returned [], this control would pass on an empty
    measurement and read as proof of order-independence."""
    changed, total = _order_sensitivity(mode, reinforce=False)
    assert (changed, total) == (0, SWEEP_SIZE), (
        f"mode={mode}: {changed}/{total} answers changed with reinforcement OFF "
        f"(expected 0/{SWEEP_SIZE})")


def test_p5b_control_the_sweep_can_detect_a_changed_answer():
    """...and the sweep itself must be able to report non-zero, or both arms above are vacuous.

    It calls `_order_sensitivity` -- the SAME function, on its known non-zero arm -- rather than
    comparing two hand-made tuples. The earlier version of this control compared a k=5 answer against a
    k=3 answer, which is a fact about slicing and not about the sweep: stubbing `_order_sensitivity` to
    `return 0, 64` left it green, so the control certified a function it never called."""
    changed, total = _order_sensitivity("hybrid", reinforce=True)
    assert total == SWEEP_SIZE, f"the sweep compared {total} answers, not {SWEEP_SIZE}"
    # A FLOOR, not the exact 64: the same sweep measures 64/64 with numpy and 60/64 without it (the
    # base CI leg has no numpy), and a control pinned to one environment's number is a control that
    # fails for the wrong reason on the other. 60 is far above anything a broken sweep reports, and
    # `return 0, 64` -- the stub that survived the previous version of this control -- fails it.
    assert changed >= 60, (
        f"the reinforce-ON hybrid sweep is the known non-zero arm (64/64 with numpy, 60/64 without); it "
        f"reported {changed}/{total}. Either the sweep has stopped measuring, or the defect it measures "
        f"is gone -- and if it is gone, the four xfails above are now xpasses and the whole P5b block "
        f"needs re-baselining")
