"""The inspector overview has to show that the store DROPPED writes, not just that records are superseded.

Part of the 1.87.0 audit. With the guard on by default, records get retired on arrival without anyone
opting in, so `memory_report()` -- the first surface an operator reads when writes seem to go missing --
is where the difference has to be visible. It was not. Measured on two stores of the same size:

    guard retired a restatement    memory_report() -> 165 chars
    an ordinary correction         memory_report() -> 165 chars, byte-identical

Both report `superseded: 1`. One of them silently refused a write the caller believed had landed. The
per-policy breakdown already existed in `supersession_report()`; nothing pointed an operator at it.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402


def _store(**kw):
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), **kw)


def _retired_store():
    st = _store()
    st.remember("v is A", key="k", object="A")
    st.remember("v is B", key="k", object="B")
    st.remember("v is A again", key="k", object="A")   # retired ON ARRIVAL by the guard
    return st


def _corrected_store():
    st = _store()
    st.remember("v is A", key="k", object="A")
    st.remember("v is B", key="k", object="B")
    st.remember("v is C", key="k", object="C")         # ordinary supersession, no policy refusal
    return st


def test_a_policy_retirement_is_visible_in_the_overview():
    rep = _retired_store().memory_report()
    assert rep["retired_on_arrival"] == 1
    assert rep["retired_by_policy"] == {"echo_guard": 1}


def test_an_ordinary_correction_is_not_counted_as_one():
    """CONTROL, and the reason the raw count cannot serve: both stores end with the SAME number of
    superseded records. If the new field were derived from that count it would report a refusal on the
    store that refused nothing -- the 'clean verdict about input never examined' this audit keeps finding."""
    rep = _corrected_store().memory_report()
    assert rep["superseded"] == _retired_store().memory_report()["superseded"] == 2, rep
    assert rep["retired_on_arrival"] == 0
    assert rep["retired_by_policy"] == {}


def test_the_two_stores_no_longer_produce_the_same_summary():
    """THE defect, stated as the comparison that used to hold."""
    assert _retired_store().memory_report() != _corrected_store().memory_report()


def test_a_store_with_the_guard_off_reports_nothing_retired():
    """CONTROL on the other axis: the same three writes, guard off, nothing refused."""
    st = _store(echo_guard=False)
    st.remember("v is A", key="k", object="A")
    st.remember("v is B", key="k", object="B")
    st.remember("v is A again", key="k", object="A")
    assert st.memory_report()["retired_on_arrival"] == 0


def test_the_overview_agrees_with_the_detailed_report():
    """The number must come from the same place an auditor would check it, not be recounted."""
    st = _retired_store()
    assert st.memory_report()["retired_by_policy"]["echo_guard"] == \
        st.supersession_report()["by_policy"]["echo_guard"]
