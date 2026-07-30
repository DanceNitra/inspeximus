"""memory_report's documented cost, made checkable.

`memory_report` is an MCP tool a model can call mid-conversation, and its docstring said only "sampled at
400 for cost" -- a bound was applied, but what it costs was never stated. MEASURED (no embedder, median of
3): 1.76 s at n=2,000 and 10.19 s at n=8,000, and a profile at n=4,000 put essentially the entire run
inside those 400 recalls (1.6M _lexsim calls). The sample caps the number of QUERIES; each one still scores
every active record, so the redundancy estimate is O(400 x n).

Both docstrings now say so. This file keeps that statement true, by pinning its SHAPE rather than its
seconds -- a wall-clock assertion would be flaky on a loaded machine and would say nothing about why.

Structural, not chronometric:
  - the number of full recalls is the sample cap, and does not grow with the store,
  - the counts callers usually want cost ZERO recalls, which is the actionable half of the warning.
"""
import pytest

from inspeximus.core import Inspeximus


SAMPLE_CAP = 400


def _store(tmp_path, n, name="s.json"):
    m = Inspeximus(str(tmp_path / name))
    for i in range(n):
        m.remember(f"record {i} alpha beta gamma deploy salary", tags=["a"], source={"doc": f"d{i % 7}"})
    m.flush()
    return m


def _count_recalls(monkeypatch, m):
    calls = []
    real = Inspeximus.recall
    monkeypatch.setattr(Inspeximus, "recall", lambda self, *a, **k: (calls.append(1), real(self, *a, **k))[1])
    return calls


@pytest.mark.parametrize("n,expected", [(50, 50), (600, SAMPLE_CAP)],
                         ids=["under-the-cap", "over-the-cap"])
def test_the_number_of_full_recalls_is_the_sample_cap_not_the_store_size(tmp_path, monkeypatch, n, expected):
    """O(400 x n), not O(n^2): the query count stops growing, which is what the sample is for."""
    m = _store(tmp_path, n)
    calls = _count_recalls(monkeypatch, m)
    m.memory_report()
    assert len(calls) == expected, (
        f"{n} active records produced {len(calls)} full recalls, not {expected}. The documented cost is "
        f"O({SAMPLE_CAP} x n); if the query count tracks the store, it is quadratic and the docstring lies.")


def test_the_cheap_counts_really_are_cheap(tmp_path, monkeypatch):
    """The actionable half of the warning: everything except the duplicate estimate is a single pass.

    If this ever needed a recall, "if that is all you need, this tool is the expensive way to get it"
    would be false advice.
    """
    m = _store(tmp_path, 600, "cheap.json")
    calls = _count_recalls(monkeypatch, m)

    active = [r for r in m._tenant_rows() if r.get("status") == "active"]
    assert len(active) == 600
    assert len(calls) == 0, "counting active records cost a recall"


def test_control_the_instrument_sees_a_recall_when_one_happens(tmp_path, monkeypatch):
    """Without this, both assertions above are satisfied by a counter that never increments."""
    m = _store(tmp_path, 10, "ctl.json")
    calls = _count_recalls(monkeypatch, m)
    m.recall("alpha", k=2)
    assert len(calls) == 1


def test_the_report_still_returns_what_it_promises(tmp_path):
    """Documenting a cost must not have changed the answer."""
    m = _store(tmp_path, 40, "shape.json")
    rep = m.memory_report()
    for field in ("active", "superseded", "by_type", "redundant_frac", "sampled"):
        assert field in rep, (field, sorted(rep))
    assert rep["active"] == 40
    assert rep["sampled"] == 40
