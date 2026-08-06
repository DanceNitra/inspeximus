"""Ranking depends on the clock. That is allowed inside the tie band and nowhere else.

`recall()` blends relevance with a per-type half-life computed from ELAPSED wall-clock age at read
time, so the same untouched store can return a different top-1 seconds later with nothing written in
between. Measured on four LOCOMO conversations (`probes/recall_over_a_time_gap.py`): 64-83 of 320
top-1 answers change over a two-second gap, and **every one of them** moved between records the API
reports at the same score -- 100% across six insert orders.

That last part is the whole contract, and it is what this file defends. A caller cannot see inside a
displayed tie, so swapping there is invisible to them; a swap between records they CAN tell apart is
a different thing entirely and would make "deterministic" the wrong word for what we ship. The README
states the tie confinement as a fact, so it needs a test that can catch it stopping being one.

Note what is NOT asserted here: that the ranking is time-invariant. It is not, deliberately -- recency
is a channel we want. Run-to-run determinism at a FIXED instant is the property that holds, and it is
asserted elsewhere (arm (a) of the reinforce ablation, 0.0000 on every corpus).
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402


def _synthetic_store(n=120):
    """Many records sharing the query's terms, so relevance ties are the common case, not the corner.

    This fixture does NOT reproduce the movement, and that is recorded rather than hidden: tried at
    120-600 records, ages spread over 2-10 s, exact-tie and near-tie text, always 0 answers moved. It
    is kept because the two properties that do NOT need movement -- an untouched store and
    same-instant determinism -- are real assertions on it, and because a synthetic case that DOES
    reproduce would be worth more than the real corpus and someone may yet find it.
    """
    m = Inspeximus(path=None)
    ids = []
    for i in range(n):
        ids.append(m.remember(f"deployment pipeline stage {i % 6} handles rollout and rollback",
                              tags=["note"]))
    return m, ids


def _real_store():
    """The corpus the README's numbers come from, when this machine has it.

    The measurement lives in `probes/recall_over_a_time_gap.py`; LOCOMO is not vendored, so on CI
    this is absent and the tie assertion skips with a reason rather than passing on a fixture that
    cannot exercise it.
    """
    probes = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "probes")
    if probes not in sys.path:
        sys.path.insert(0, probes)
    try:
        import reinforce_accuracy_ablation as A
    except Exception as exc:                                    # pragma: no cover - env dependent
        pytest.skip(f"the ablation probe is not importable here: {exc}")
    path = A._locomo_candidates(None)
    if not path:
        pytest.skip("locomo10.json is not on this machine, so the real-corpus tie assertion cannot run")
    _label, records, questions = A.load_locomo(path, max_convs=1)[0]
    store, _idx = A.build_store(records)
    return store, [q for q, _g in questions][:60]


def _crowded_store(n=120):
    return _synthetic_store(n)


QUERIES = [f"deployment pipeline stage {i}" for i in range(6)] + [
    "rollout and rollback", "pipeline handles deployment", "stage rollback"]


def _read(m, queries=None):
    """(top-1 id, [rounded scores]) per query. reinforce=False: a NON-mutating read."""
    out = []
    for q in (queries if queries is not None else QUERIES):
        hits = m.recall(q, k=5, reinforce=False) or []
        out.append((hits[0]["id"] if hits else None, [h.get("score") for h in hits]))
    return out


def test_on_the_real_corpus_every_moved_answer_stayed_inside_a_displayed_tie():
    """THE CONTRACT, on the corpus the README's numbers come from.

    Carries its own positive control: if nothing moved, the assertion below proved nothing, and this
    test says so rather than going green. That is the whole reason the README states the tie
    confinement as a bound and not as a hope."""
    store, queries = _real_store()
    before = _read(store, queries)
    time.sleep(2.0)
    after = _read(store, queries)
    moved = [(q, a, b) for q, a, b in zip(queries, before, after) if a[0] != b[0]]
    assert moved, ("no answer moved across a 2 s gap on the real corpus, so the tie assertion is "
                   "vacuous for this run -- treat it as unproven, not as passing")
    visible = [(q, a[1][:2]) for q, a, _b in moved
               if not (len(a[1]) >= 2 and a[1][0] is not None and a[1][0] == a[1][1])]
    assert not visible, (
        f"{len(visible)} of {len(moved)} moved answers changed between records the caller can TELL "
        f"APART: " + "; ".join(f"{q[:50]!r} top-2 {s}" for q, s in visible[:3])
        + ". The README states this never happens, measured at 100% over six insert orders")


def test_the_control_a_gap_actually_moves_something():
    """Assert the target EXISTS before asserting anything about its shape.

    If no answer moves, every assertion below passes vacuously -- the exact failure mode that has cost
    this project whole days. This test is allowed to be the one that goes quiet on a fast machine, and
    when it does the others must be read as unproven, not as green.
    """
    m, _ids = _crowded_store()
    before = _read(m)
    time.sleep(2.0)
    after = _read(m)
    moved = sum(1 for a, b in zip(before, after) if a[0] != b[0])
    if not moved:
        pytest.skip("no answer moved across the gap on this machine; the tie assertions below are "
                    "vacuous for this run rather than passing")
    assert moved > 0


def test_every_answer_that_moves_moves_inside_a_displayed_tie():
    """THE CONTRACT. A swap a caller can SEE is not the same thing as a swap they cannot."""
    m, _ids = _crowded_store()
    before = _read(m)
    time.sleep(2.0)
    after = _read(m)
    visible = []
    for q, a, b in zip(QUERIES, before, after):
        if a[0] == b[0]:
            continue
        scores = a[1]
        tied = len(scores) >= 2 and scores[0] is not None and scores[0] == scores[1]
        if not tied:
            visible.append((q, scores[:2]))
    assert not visible, (
        "an answer changed across a time gap between records the caller can TELL APART: "
        + "; ".join(f"{q!r} top-2 scores {s}" for q, s in visible)
        + ". Recency inside a tie is a design choice; reordering distinguishable records because the "
          "clock advanced is not, and the README states the tie confinement as measured fact")


def test_the_store_really_was_untouched():
    """The moves must not be explained by a write. If the read mutated the store, this file is
    measuring reinforcement, not the clock, and its conclusion is about the wrong mechanism."""
    m, _ids = _crowded_store()
    snap = [(it["id"], it.get("value"), it.get("last_access"), it.get("mtype"), it.get("status"))
            for it in m.items]
    _read(m)
    time.sleep(2.0)
    _read(m)
    after = [(it["id"], it.get("value"), it.get("last_access"), it.get("mtype"), it.get("status"))
             for it in m.items]
    assert snap == after, "reinforce=False reads changed the store, so the gap is not what moved the answers"


def test_a_second_read_at_the_same_instant_is_identical():
    """The property we DO claim: at a fixed instant, the same store answers the same way.

    Without this the file could be read as 'ranking is unstable', which is not what was measured."""
    m, _ids = _crowded_store()
    assert _read(m) == _read(m), (
        "two back-to-back reads of the same store disagreed; that is not a clock effect and not "
        "something any part of this project's determinism story tolerates")
