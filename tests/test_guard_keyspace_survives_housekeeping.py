"""The code guard disarmed itself under the configuration the docs recommend for production.

check_code() only considers deprecation records whose status is "active" -- correct, since re-deprecating a
symbol supersedes the earlier record. But `status` is not the code guard's to own: capacity eviction and the
consolidate() keep-budget retire records by value and by similarity, and neither knows a refactor record
from a note.

MEASURED before the fix (research/probes/audit_code_guard_disarm.py):

    30 recorded deprecations, capacity=8, then TEN ORDINARY WRITES  -> 2 left, check_code() went quiet
    30 recorded deprecations, consolidate(keep=5)                   -> 5 left, check_code() went quiet

In both, check_code() returned [] -- "clean" -- for a snippet calling a function the refactor deleted. That
is the failure the tool exists to prevent, and docs/API.md recommends `capacity=N` under "Run bounded in
production", so a store disarmed its own guard simply by filling up. No maintenance call was needed.

The fix carves the guard keyspace out of both policies (core._GUARD_KEYSPACES), the same way the eviction
docstring already carves out superseded history: these are bookkeeping a guard's correctness rests on, not
part of the recall working set those policies exist to bound.

CONTROLS: ordinary records must still be evicted and still be demoted by the budget. A carve-out that
quietly disabled eviction would pass every assertion about the guard while breaking the bounded store.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402
from inspeximus.code_guard import _PREFIX, check_code, deprecate_symbol  # noqa: E402
from inspeximus.core import _GUARD_KEYSPACES  # noqa: E402

PAIRS = [(f"old_api_fn_{i}", f"new_api_fn_{i}") for i in range(30)]
SNIPPET = "result = old_api_fn_0(payload)\nvalue = old_api_fn_7(config)\n"


def _store(**kw):
    st = Inspeximus(path=None, **kw)
    for old, new in PAIRS:
        deprecate_symbol(st, old, new, reason="renamed in the 2026-07 refactor")
    return st


def _flagged(st):
    return sorted(h["symbol"] for h in check_code(st, SNIPPET))


def _ordinary_active(st):
    return [r for r in st.items
            if r.get("status") == "active" and not (r.get("key") or "").startswith(_PREFIX)]


def test_the_guard_still_fires_before_any_housekeeping():
    """The control the whole file rests on."""
    assert _flagged(_store()) == ["old_api_fn_0", "old_api_fn_7"]


def test_capacity_eviction_does_not_disarm_the_guard():
    """THE defect: no maintenance call, just ordinary writes against the documented production config."""
    st = _store(capacity=8)
    for i in range(10):
        st.remember(f"unrelated operational note number {i}", key=f"note{i}", object=str(i))
    assert _flagged(st) == ["old_api_fn_0", "old_api_fn_7"], "capacity eviction silenced the code guard"


def test_the_keep_budget_does_not_disarm_the_guard():
    st = _store()
    st.consolidate(keep=5)
    assert _flagged(st) == ["old_api_fn_0", "old_api_fn_7"], "the keep-budget silenced the code guard"


def test_capacity_still_bounds_ORDINARY_memories():
    """CONTROL. The carve-out must not turn the bounded store into an unbounded one."""
    st = Inspeximus(path=None, capacity=8)
    for i in range(20):
        st.remember(f"unrelated operational note number {i}", key=f"note{i}", object=str(i))
    assert len(_ordinary_active(st)) <= 8, "capacity no longer bounds the working set"


def test_deprecations_do_not_consume_the_capacity_budget():
    """A store holding 30 refactor records must still be allowed its full 8 real memories."""
    st = _store(capacity=8)
    for i in range(8):
        st.remember(f"unrelated operational note number {i}", key=f"note{i}", object=str(i))
    assert len(_ordinary_active(st)) == 8, "deprecations crowded real memories out of the budget"


def test_the_keep_budget_still_demotes_ORDINARY_memories():
    """CONTROL, the other policy."""
    st = Inspeximus(path=None)
    for i in range(20):
        st.remember(f"distinct operational subject {i} with its own wording", key=f"n{i}", object=str(i))
    st.consolidate(keep=6)
    assert len(_ordinary_active(st)) <= 6, "the keep-budget stopped bounding ordinary records"


def test_re_deprecating_a_symbol_still_supersedes_the_old_record():
    """The carve-out must not protect a record from the guard's OWN supersession."""
    st = Inspeximus(path=None)
    deprecate_symbol(st, "old_fn", "new_fn_v1", reason="first refactor")
    deprecate_symbol(st, "old_fn", "new_fn_v2", reason="second refactor")
    hits = check_code(st, "x = old_fn(1)")
    assert len(hits) == 1 and hits[0]["replacement"] == "new_fn_v2", hits


def test_the_code_guard_keyspace_is_registered_in_core():
    """The two modules are deliberately decoupled -- so the agreement is asserted, not imported.

    If code_guard ever renames its prefix, the carve-out above silently stops applying and every other
    test here still passes, because they all go through code_guard's own constant.
    """
    assert _PREFIX in _GUARD_KEYSPACES, (
        f"code_guard._PREFIX is {_PREFIX!r} but core._GUARD_KEYSPACES holds {_GUARD_KEYSPACES!r}; "
        f"housekeeping would evict deprecation records again")
