"""A refactor recorded in QUALIFIED form did not catch the code that resurrects it.

check_code hunts the recorded symbol as a whole identifier. That is exactly what its docstring promises,
and it is right for a bare name. But a refactor is naturally recorded qualified -- `Session.close_all`,
`mod.old_fn` -- and then the literal string never appears in the resurrecting code:

    recorded 'Session.close_all'  code 's = Session(); s.close_all()'          -> [] , "clean"
    recorded 'mod.old_fn'         code 'from mod import old_fn; old_fn(1)'     -> [] , "clean"

MEASURED in research/probes/audit_code_guard_recall.py: 2 misses in 11 realistic cases before the fix, 0
after. A miss is strictly worse than this guard's documented over-flagging: over-flagging is visible to the
caller, a miss is a clean verdict about code that calls a deleted function.

The fix is a SUBJECT CHECK, not a broader match. Hunting the tail unconditionally would make
`Session.close_all` flag every `.close_all()` on any object; instead the tail is hunted only when the
qualifier is itself present in the snippet as a whole identifier. `s.close_all()` is flagged in code that
mentions `Session` and ignored in code that does not.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402
from inspeximus.code_guard import check_code, deprecate_symbol, scan_lines  # noqa: E402


def _store(old, new="new_fn"):
    st = Inspeximus(path=None)
    deprecate_symbol(st, old, new, reason="renamed in the refactor")
    return st


def _syms(st, code):
    return sorted(h["symbol"] for h in check_code(st, code))


# ── the defect ────────────────────────────────────────────────────────────────────────────────────────

def test_a_method_rename_catches_the_instance_call():
    st = _store("Session.close_all", "Session.shutdown")
    assert _syms(st, "s = Session()\ns.close_all()") == ["Session.close_all"]


def test_a_module_qualified_rename_catches_the_imported_bare_call():
    st = _store("mod.old_fn")
    assert _syms(st, "from mod import old_fn\nx = old_fn(1)") == ["mod.old_fn"]


def test_the_fully_qualified_use_still_matches():
    """It always did; the fix must not trade one form for the other."""
    st = _store("mod.old_fn")
    assert _syms(st, "import mod\nx = mod.old_fn(1)") == ["mod.old_fn"]


def test_the_subject_check_reads_the_whole_snippet_not_the_line():
    """The qualifier is imported at the top and the call is far below."""
    st = _store("Session.close_all")
    code = "from db import Session\n" + "\n".join(f"# filler {i}" for i in range(12)) + "\ns.close_all()\n"
    assert _syms(st, code) == ["Session.close_all"]
    lines = scan_lines(st, code)
    assert len(lines) == 1 and lines[0]["line"] == 14, lines


# ── the controls: the fix must not become a blanket tail match ────────────────────────────────────────

def test_an_unrelated_object_is_not_flagged_when_the_qualifier_is_absent():
    """THE control. Without it this fix is just 'match the last segment', which flags everything."""
    st = _store("Session.close_all")
    assert _syms(st, "pool = ConnectionPool()\npool.close_all()") == []


def test_a_bare_name_is_unaffected():
    st = _store("old_fn")
    assert _syms(st, "x = old_fn(1)") == ["old_fn"]
    assert _syms(st, "x = obj.old_fn(1)") == ["old_fn"]


def test_whole_identifier_boundaries_still_hold_for_qualified_symbols():
    """`close_all` must not match `close_all_now` or `pre_close_all`, qualifier present or not."""
    st = _store("Session.close_all")
    assert _syms(st, "s = Session()\ns.close_all_now()") == []
    assert _syms(st, "s = Session()\ns.pre_close_all()") == []


def test_the_qualifier_itself_must_be_a_whole_identifier():
    """A snippet mentioning `MySessionFactory` does not mention `Session`."""
    st = _store("Session.close_all")
    assert _syms(st, "f = MySessionFactory()\nf.close_all()") == []


def test_occurrences_are_not_double_counted():
    """Full form and tail both match `mod.old_fn`; the count must stay one per textual occurrence."""
    st = _store("mod.old_fn")
    hits = check_code(st, "import mod\nx = mod.old_fn(1)\ny = mod.old_fn(2)")
    assert len(hits) == 1 and hits[0]["occurrences"] == 2, hits
