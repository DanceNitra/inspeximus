"""A write the echo guard retires must not be reported to the caller as a write that landed.

Found by auditing 1.87.0 -- the release that turned the guard on by default -- on the same day it shipped.
`remember()` returns an id whether the record became current or was retired on arrival, so a legitimate
reversal looked like a success and left the store on the old value. Measured, each with a control against
echo_guard=False:

    A -> B -> A (third write TRUE)   store ends on B; the call returns an ordinary id
    LangGraph put/get round-trip      put({'theme':'dark'}) then get() returns {'theme':'light'}
    an oscillating status flag        2 of 6 writes dropped, final value inverted
    after revert()                    the reverted-away value can never be written again

Seven symptoms, one defect: a demoted write reported as a landed one. And the signal already existed --
`route()`, the other write path, returns {"intent": "echo", "action": "blocked", "note": ...}. It simply
was not on the path everyone uses.

`last_write` carries that verdict without changing remember()'s return type. It is reset at the start of
every write, because a verdict left over from an earlier call would be read as this call's, and a stale
signal is worse than none.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402


def _store(guard=True):
    st = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    st.echo_guard = guard
    return st


def _active(st, key="d"):
    return [r.get("object") for r in st.items if r.get("key") == key and r.get("status") == "active"]


def test_a_retired_echo_is_reported_as_blocked():
    """THE defect. Before this, the call was indistinguishable from one that landed."""
    st = _store()
    st.remember("branch is release-2", key="d", object="release-2")
    st.remember("branch is main", key="d", object="main")
    st.remember("branch is release-2 again", key="d", object="release-2")
    lw = st.last_write
    assert lw["blocked"] is True
    assert lw["status"] == "superseded"
    assert lw["policy"] == "echo_guard"
    assert "reaffirm=True" in lw["note"], "the note must name a remedy the caller can act on"


def test_a_write_that_lands_is_reported_as_landed():
    """CONTROL. If everything reported blocked, the field would carry no information."""
    st = _store()
    st.remember("branch is release-2", key="d", object="release-2")
    assert st.last_write["blocked"] is False
    st.remember("branch is main", key="d", object="main")
    assert st.last_write["blocked"] is False and st.last_write["status"] == "active"


def test_the_verdict_is_never_stale():
    """A blocked write followed by a good one must not leave the caller reading the old verdict."""
    st = _store()
    st.remember("branch is release-2", key="d", object="release-2")
    st.remember("branch is main", key="d", object="main")
    st.remember("branch is release-2 again", key="d", object="release-2")
    assert st.last_write["blocked"] is True
    st.remember("branch is develop", key="d", object="develop")
    assert st.last_write["blocked"] is False, "the previous call's verdict was still being reported"
    assert st.last_write["id"] != "", st.last_write


def test_the_id_the_verdict_names_is_the_record_just_written():
    st = _store()
    st.remember("branch is release-2", key="d", object="release-2")
    st.remember("branch is main", key="d", object="main")
    rid = st.remember("branch is release-2 again", key="d", object="release-2")
    assert st.last_write["id"] == rid
    rec = next(r for r in st.items if r["id"] == rid)
    assert rec["status"] == "superseded", "the verdict must describe what actually happened to it"


def test_reaffirm_lands_and_is_reported_as_landed():
    """The remedy the note names has to work, and has to report that it worked."""
    st = _store()
    st.remember("branch is release-2", key="d", object="release-2")
    st.remember("branch is main", key="d", object="main")
    st.remember("branch is release-2 again", key="d", object="release-2")
    st.remember("branch really is release-2", key="d", object="release-2", reaffirm=True)
    assert st.last_write["blocked"] is False
    assert _active(st) == ["release-2"], "the store must be able to follow the world back"


def test_an_unkeyed_write_still_reports_a_verdict():
    """A field callers rely on must be present on every write path, not only the keyed one."""
    st = _store()
    st.remember("an ordinary note with no key")
    assert st.last_write["blocked"] is False and st.last_write["id"]
