"""Centring needs three distinct vectors, on both recall paths (3.21, AUDIT-A A-6).

With one vector, or several copies of one, every centred vector is zero, and semantic-only recall returned nothing,
even for a query equal to the stored vector. The numpy path always did this, and 3.21's 1b made the pure-Python path
do it too. Both paths now skip centring by the same rule, `_fewer_than_three_distinct`, and a store with three
distinct vectors still centres.
"""
import pytest

from inspeximus import core
from inspeximus.core import Inspeximus

VECS = {"alpha": [1.0, 0.2, 0.0, 0.1], "beta": [0.1, 1.0, 0.3, 0.0], "gamma": [0.0, 0.1, 1.0, 0.4]}


def _store(texts, vec_of):
    h = Inspeximus(embed=lambda t: list(vec_of(t)))
    ids = [h.remember(t) for t in texts]
    return h, ids


def _semantic(h, q, numpy, monkeypatch):
    with monkeypatch.context() as m:
        if not numpy:
            m.setattr(core, "_np", None)
        elif core._numpy() is None:
            pytest.skip("numpy is not installed")
        return [r["id"] for r in h.recall(q, k=10, mode="semantic")]


@pytest.mark.parametrize("numpy", [False, True], ids=["pure", "numpy"])
def test_a_store_of_one_vector_finds_it(numpy, monkeypatch):
    h, ids = _store(["only alpha"], lambda t: VECS["alpha"])
    assert _semantic(h, "alpha", numpy, monkeypatch) == ids


@pytest.mark.parametrize("numpy", [False, True], ids=["pure", "numpy"])
def test_a_store_of_identical_vectors_finds_them(numpy, monkeypatch):
    h, ids = _store(["copy one", "copy two", "copy three"], lambda t: VECS["alpha"])
    assert sorted(_semantic(h, "alpha", numpy, monkeypatch)) == sorted(ids)


@pytest.mark.parametrize("numpy", [False, True], ids=["pure", "numpy"])
def test_two_distinct_vectors_rank_the_query_equal_one_first(numpy, monkeypatch):
    h, ids = _store(["alpha", "beta"], lambda t: VECS[t] if t in VECS else VECS["alpha"])
    got = _semantic(h, "alpha", numpy, monkeypatch)
    assert got and got[0] == ids[0], got


@pytest.mark.parametrize("numpy", [False, True], ids=["pure", "numpy"])
def test_three_distinct_vectors_still_centre(numpy, monkeypatch):
    """CONTROL: the rule skips only the degenerate case."""
    h, ids = _store(["alpha", "beta", "gamma"], lambda t: VECS[t] if t in VECS else VECS["alpha"])
    got = _semantic(h, "alpha", numpy, monkeypatch)
    assert got and got[0] == ids[0], got
    if numpy:
        assert h._vec_mean is not None, "the numpy path did not centre three distinct vectors"
    else:
        assert h._center_pack() is not None, "the pure path did not centre three distinct vectors"


def test_the_rule_counts_distinct_vectors():
    a, b, c = VECS["alpha"], VECS["beta"], VECS["gamma"]
    assert core._fewer_than_three_distinct([a, list(a), a])
    assert core._fewer_than_three_distinct([a, b, list(b)])
    assert not core._fewer_than_three_distinct([a, b, c])
    assert not core._fewer_than_three_distinct([a, a, b, b, c])
