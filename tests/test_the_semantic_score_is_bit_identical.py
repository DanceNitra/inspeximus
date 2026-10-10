"""The semantic score is the same float it was before the speed-up, and a cached norm never outlives its vector (3.18).

On the crew store (13,628 records, bge-m3, no numpy in the `mcp` extra) a recall spent 82 % of its time in `_cosine`:
three generator sums over 1,024 dimensions for each of about 8,500 candidates, 2.5 s a recall. `_dot` and `_norm` now
use `sum(map(operator.mul, ...))`, and a record's norm is computed once per vector: 0.87 s a recall, the top 10
identical. Not `math.sumprod`: it rounds differently, so a near-tie could swap order. The tests below therefore check
equal SCORES, float for float, against the formula 3.17 used, on vectors built to be near-ties, on every Python version
the CI runs (the builtin `sum` of floats changed in 3.12, and the equality must hold within each).
"""
from __future__ import annotations

import math
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

DIM = 1024


def _ref_cosine(a, b):
    """The 3.17 `_cosine`, verbatim."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _near_ties(seed=1, n=40):
    """A query and n vectors whose cosines to it agree to about 9 digits: one base vector plus tiny perturbations.
    Components span 12 orders of magnitude, so a more precise dot product (`math.sumprod`) gives other floats."""
    rnd = random.Random(seed)
    q = [rnd.uniform(-1, 1) * 10 ** rnd.randint(-6, 6) for _ in range(DIM)]
    base = [rnd.uniform(-1, 1) * 10 ** rnd.randint(-6, 6) for _ in range(DIM)]
    vecs = []
    for i in range(n):
        v = list(base)
        j = rnd.randrange(DIM)
        v[j] += 1e-9 * (i + 1) * (abs(v[j]) or 1.0)
        vecs.append(v)
    return q, vecs


def test_the_fixture_holds_near_ties():
    q, vecs = _near_ties()
    s = sorted(_ref_cosine(q, v) for v in vecs)
    assert s[-1] - s[0] < 1e-6, "CONTROL: the vectors are near-ties"
    assert len(set(s)) > 1, "CONTROL: the near-ties are not all equal"


def test_the_fixture_tells_sumprod_from_sum():
    """CONTROL: `math.sumprod` would give other floats here, so the equality below can fail."""
    q, vecs = _near_ties()
    precise = getattr(math, "sumprod", None) or (lambda a, b: math.fsum(x * y for x, y in zip(a, b)))
    exact = [precise(q, v) for v in vecs]
    naive = [sum(x * y for x, y in zip(q, v)) for v in vecs]
    assert any(a != b for a, b in zip(exact, naive)), "CONTROL: a more precise dot product gives other floats here"


def test_the_cosine_equals_the_3_17_formula_float_for_float():
    q, vecs = _near_ties()
    for v in vecs:
        assert core._cosine(q, v) == _ref_cosine(q, v)
        assert core._dot(q, v) == sum(x * y for x, y in zip(q, v))
        assert core._norm(v) == (math.sqrt(sum(x * x for x in v)) or 1.0)


def _store(tmp_path, q, vecs):
    table = {"near tie record": q}
    for i, v in enumerate(vecs):
        table["near tie record %d" % i] = v
    m = Inspeximus(path=str(tmp_path / "s.json"), embed=lambda t: list(table.get(t, q)))
    for i in range(len(vecs)):
        m.remember("near tie record %d" % i)
    return m


def test_a_records_score_equals_the_3_17_formula_and_recall_keeps_the_order(tmp_path):
    q, vecs = _near_ties()
    m = _store(tmp_path, q, vecs)
    for r in m._items:
        assert m._rec_cos(q, r) == _ref_cosine(q, r["vec"])
        assert m._rec_cos(q, r, core._norm(q)) == _ref_cosine(q, r["vec"])
    got = [h["id"] for h in m.recall("near tie record", k=40)]
    real = Inspeximus._rec_cos
    Inspeximus._rec_cos = lambda self, a, r, na=None: _ref_cosine(a, r["vec"])
    try:
        want = [h["id"] for h in m.recall("near tie record", k=40)]
    finally:
        Inspeximus._rec_cos = real
    assert len(want) >= 10, "CONTROL: recall returns the near-ties"
    assert got == want, "the cached-norm score changed the order of near-ties"


def test_reembed_drops_the_old_norms(tmp_path):
    q, vecs = _near_ties()
    m = _store(tmp_path, q, vecs)
    for r in m._items:
        m._rec_cos(q, r)
    assert len(m._vnorm) == len(vecs), "CONTROL: every record's norm is cached"
    old = {r["id"]: r["vec"] for r in m._items}
    m.reembed(only_missing=False)
    assert all(r["vec"] is not old[r["id"]] for r in m._items), "CONTROL: reembed gave each record a new vector"
    stale = [rid for rid, (v, _n) in m._vnorm.items() if v is old.get(rid)]
    assert not stale, "reembed left the norms of %d replaced vectors in the cache" % len(stale)


def test_a_vector_replaced_behind_the_tracker_is_scored_with_its_own_norm(tmp_path):
    """A peer's row adopted by refresh, or a shelved vector: the record's `vec` changes without `_set_vec`."""
    q, vecs = _near_ties()
    m = _store(tmp_path, q, vecs)
    r = m._items[0]
    m._rec_cos(q, r)
    new = [x * 3.0 + 0.25 for x in r["vec"]]
    dict.__setitem__(r, "vec", new)                     # untracked: no edit fires
    assert m._rec_cos(q, r) == _ref_cosine(q, new), "a replaced vector was scored with the old vector's norm"


def test_an_in_place_edit_of_a_vector_drops_its_norm(tmp_path):
    q, vecs = _near_ties()
    m = _store(tmp_path, q, vecs)
    r = m._items[0]
    m._rec_cos(q, r)
    r["vec"][0] = r["vec"][0] * 40.0 + 3.0              # same list object, new norm
    assert m._rec_cos(q, r) == _ref_cosine(q, r["vec"]), "an in-place edit kept the vector's old norm"


def _norm_fixture():
    """Two vectors whose norm `math.sumprod` would round differently (found by search: seeds 63 and 113)."""
    out = []
    r = random.Random(63)
    out.append([r.uniform(-1, 1) for _ in range(DIM)])
    r = random.Random(113)
    out.append([r.uniform(-1, 1) * 10 ** r.randint(-6, 6) for _ in range(DIM)])
    return out


def test_the_norm_equals_the_3_17_formula_float_for_float():
    vecs = _norm_fixture()
    if hasattr(math, "sumprod"):
        assert all(math.sqrt(math.sumprod(v, v)) != math.sqrt(sum(x * x for x in v)) for v in vecs), \
            "CONTROL: a more precise sum of squares gives another norm for these vectors"
    for v in vecs:
        assert core._norm(v) == (math.sqrt(sum(x * x for x in v)) or 1.0)
        assert core._cosine(v, vecs[0]) == _ref_cosine(v, vecs[0])
