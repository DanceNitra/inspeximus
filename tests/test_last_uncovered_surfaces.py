"""The three remaining uncovered functions that are worth covering.

After the framework adapters, 34 public functions still had no executed body line. Sorting them by whether
a test could catch anything left exactly three:

  pydantic_ai check_conflict   6 body lines -- conflict detection is real logic, not a wrapper
  openai_agents pop_item       7 body lines -- destructive, and it had a defect once before
  openai_agents forget_subject 2 body lines -- a DSAR surface

The rest are one-line async delegates (`aput -> self.put`), abstract protocol methods, and process entry
points (`main`, `serve`) that only a subprocess could exercise. Those are named in HANDOFF as deliberately
uncovered, with the reason, rather than left to look like unfinished work.
"""
import asyncio
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


# ── pydantic_ai: check_conflict ─────────────────────────────────────────────────────────────────────
def _check_conflict(store):
    pytest.importorskip("pydantic_ai")
    from inspeximus.integrations.pydantic_ai import inspeximus_toolset
    ts = inspeximus_toolset(store=store)
    tools = getattr(ts, "tools", None) or getattr(ts, "_tools", None)
    fn = tools["check_conflict"] if hasattr(tools, "keys") else \
        next(t for t in tools if getattr(t, "name", "") == "check_conflict")
    return getattr(fn, "function", None) or getattr(fn, "func", None) or fn


def test_check_conflict_finds_a_contradicting_value():
    store = Inspeximus(path=_path())
    store.extractor = lambda t: ("deploy::window", t.split()[-1])
    store.remember("the deployment window is 02:00")

    got = _check_conflict(store)("the deployment window is 04:00")
    assert got, "a value change on a managed key must be reported as a conflict"
    assert any("02:00" in c for c in got), got


def test_a_pure_duplicate_is_not_a_conflict():
    """Documented explicitly: "A pure duplicate is not a conflict." Reporting one would make the tool
    fire on every reaffirmation and the agent would learn to ignore it."""
    store = Inspeximus(path=_path())
    store.extractor = lambda t: ("deploy::window", t.split()[-1])
    store.remember("the deployment window is 02:00")

    assert _check_conflict(store)("the deployment window is 02:00") == []


def test_an_unrelated_fact_is_not_a_conflict():
    store = Inspeximus(path=_path())
    store.remember("the deployment window is 02:00")
    assert _check_conflict(store)("the cafeteria serves soup on tuesdays") == []


def test_check_conflict_on_an_empty_store_is_empty():
    assert _check_conflict(Inspeximus(path=_path()))("anything at all") == []


# ── openai_agents: pop_item and forget_subject ──────────────────────────────────────────────────────
def _session(**kw):
    pytest.importorskip("agents")
    from inspeximus.integrations.openai_agents import InspeximusSession
    return InspeximusSession(session_id=kw.pop("session_id", "s1"), path=kw.pop("path", _path()), **kw)


def _msg(text, role="user"):
    return {"role": role, "content": text}


def test_pop_item_returns_the_LAST_item_and_removes_it():
    s = _session()
    asyncio.run(s.add_items([_msg("first"), _msg("second"), _msg("third")]))

    popped = asyncio.run(s.pop_item())
    assert popped["content"] == "third", popped

    left = [i["content"] for i in asyncio.run(s.get_items())]
    assert left == ["first", "second"], left


def test_pop_item_is_repeatable_down_to_empty_and_then_returns_none():
    s = _session()
    asyncio.run(s.add_items([_msg("only")]))
    assert asyncio.run(s.pop_item())["content"] == "only"
    assert asyncio.run(s.pop_item()) is None, "popping an empty session must return None, not raise"
    assert asyncio.run(s.get_items()) == []


def test_pop_item_does_not_reach_into_another_session():
    p = _path()
    a = _session(session_id="a", path=p)
    b = _session(session_id="b", path=p)
    asyncio.run(a.add_items([_msg("a-only")]))
    asyncio.run(b.add_items([_msg("b-only")]))

    assert asyncio.run(b.pop_item())["content"] == "b-only"
    assert [i["content"] for i in asyncio.run(a.get_items())] == ["a-only"], \
        "popping one session must not touch another"


def test_forget_subject_erases_this_sessions_turns_and_leaves_a_tombstone():
    p = _path()
    s = _session(session_id="dsar", path=p)
    other = _session(session_id="keeper", path=p)
    asyncio.run(s.add_items([_msg("alice lives at 12 Oak St")]))
    asyncio.run(other.add_items([_msg("bob lives at 9 Elm St")]))

    res = s.forget_subject(request_id="DSAR-1")
    assert res, res

    assert asyncio.run(s.get_items()) == [], "the session's turns must be gone"
    assert [i["content"] for i in asyncio.run(other.get_items())] == ["bob lives at 9 Elm St"], \
        "and another session's turns must survive"


def test_forget_subject_leaves_the_store_verifiable():
    """A hard delete must not read as tampering -- that is the whole point of the tombstone."""
    p = _path()
    store = Inspeximus(path=p, receipts=True)
    s = _session(session_id="dsar", store=store)
    asyncio.run(s.add_items([_msg("alice lives at 12 Oak St")]))
    assert store.verify_writes()[0] is True

    s.forget_subject(request_id="DSAR-1")
    ok, problems = store.verify_writes()
    assert ok is True, f"an authorised erasure must not look like an out-of-band edit: {problems}"


def test_check_conflict_auto_keys_so_a_reworded_value_change_is_still_caught():
    """The auto-keying step is what makes this more than a similarity check, and only a REWORDED value
    change can show it.

    Measured on "the deployment window is 02:00" already in the store:
        "the deployment window is 04:00"  -> 1 conflict with the key, 1 without   (lexically similar)
        "we moved the slot to 04:00"      -> 1 conflict with the key, 0 without   (nothing shared)

    My first test used the similar pair, so disabling the extractor lookup entirely still passed: the
    conflict was found by wording, not by the managed key.
    """
    store = Inspeximus(path=_path())
    store.extractor = lambda t: ("deploy::window", t.split()[-1])
    store.remember("the deployment window is 02:00")

    got = _check_conflict(store)("we moved the slot to 04:00")
    assert got, "a value change on a managed key must be caught even when the wording shares nothing"
    assert any("02:00" in c for c in got), got
