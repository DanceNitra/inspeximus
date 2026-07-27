"""`memory_report()` sampled the OLDEST 400 records and called it a sample.

Duplication accumulates in the tail, so the same 1000-record store reported `redundant_frac` **1.0, 0.245
or 0.99** depending only on the order the records went in — a spread of 0.755 on a number the CHANGELOG
sells as "the surface that proves a store did NOT accumulate 800 copies of a fact". It disclosed
`sampled: 400` but never "the oldest 400", which is the part that made it wrong rather than approximate.

A seeded random sample of the same size costs the same and tracks the truth:

    true 0.600 -> 0.562 / 0.613     true 0.200 -> 0.215 / 0.185     true 0.000 -> 0.000 / 0.000
    (ordered / shuffled inserts)

Seeded, not merely random: a number offered as evidence must not move between runs.
"""
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402

DUPE = "the quarterly revenue target is 4.2 million"
#: Deliberately varied. An earlier fixture drew six words from a twenty-word vocabulary and its "distinct"
#: records collided with each other constantly -- the metric was right and the fixture was wrong, which
#: looked exactly like a denominator bug for half an hour.
TOPICS = ["the deploy window is {i} on Tuesdays", "invoice {i} was issued in March",
          "customer {i} prefers email over phone", "server rack {i} runs at 22 degrees",
          "the {i}th sprint retro moved to Thursday", "budget line {i} covers travel",
          "vendor {i} renewed for two years", "dataset {i} has 40 thousand rows"]


def _store(n_dupes, n_distinct, shuffle=False):
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"))
    rows = [DUPE] * n_dupes + [TOPICS[i % len(TOPICS)].format(i=i) for i in range(n_distinct)]
    if shuffle:
        random.Random(7).shuffle(rows)
    for t in rows:
        m.remember(t)
    return m


def test_the_answer_does_not_depend_on_insertion_order():
    """THE defect. Same content, same counts, three orders, three different answers."""
    fracs = [_store(600, 400, shuffle=s).memory_report()["redundant_frac"] for s in (False, True)]
    assert max(fracs) - min(fracs) <= 0.15, f"insertion order still moves the estimate: {fracs}"


def test_the_estimate_tracks_the_true_duplicate_fraction():
    """Order-independence alone could be achieved by returning a constant."""
    for n_dupes, n_distinct in ((600, 400), (200, 800)):
        true = n_dupes / (n_dupes + n_distinct)
        got = _store(n_dupes, n_distinct).memory_report()["redundant_frac"]
        assert abs(got - true) <= 0.12, f"true {true:.3f}, reported {got:.3f}"


def test_a_store_with_no_duplicates_reports_none():
    """The direction that matters commercially: this surface exists to say 'clean', so it must be able to."""
    assert _store(0, 1000).memory_report()["redundant_frac"] == 0.0


def test_the_number_is_reproducible():
    """Seeded. Evidence that moves between runs is not evidence — and an unseeded sample would have passed
    every test above."""
    m = _store(600, 400)
    assert len({m.memory_report()["redundant_frac"] for _ in range(5)}) == 1


def test_the_sample_is_not_the_head_of_the_store():
    """Pinned directly, because that is what the defect WAS: the estimate must not equal what you get by
    looking only at the oldest records."""
    m = _store(600, 400)                       # 600 duplicates first, then 400 distinct
    head_only = [r for r in m.items if r.get("status") == "active"][:400]
    assert all(r["text"] == DUPE for r in head_only), "fixture: the head must be all duplicates"
    assert m.memory_report()["redundant_frac"] < 0.9, \
        "the estimate still reflects only the oldest records"


def test_small_stores_are_not_sampled_at_all():
    """Below the cap every record is examined, and the fix must not have introduced sampling there."""
    m = _store(10, 10)
    assert m.memory_report()["sampled"] == 20
