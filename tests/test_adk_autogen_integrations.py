"""Google ADK and AutoGen — the last two framework adapters with uncovered functions.

Thirteen functions between them with no executed body line. Both are async protocols, and both carry an
erasure surface the rest of the package is sold on: ADK exposes `forget_subject_for(app_name, user_id)`,
so a data-subject request has to reach the records that user's sessions produced and nothing else.

Guarded per-block with `importorskip`.
"""
import asyncio
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


def _run(x):
    """These protocols are async; some methods return a coroutine and some do not."""
    return asyncio.run(x) if asyncio.iscoroutine(x) else x


# ── Google ADK: BaseMemoryService ───────────────────────────────────────────────────────────────────
pytest.importorskip("google.adk")
from inspeximus.integrations.google_adk import InspeximusMemoryService, register  # noqa: E402


def _svc(**kw):
    return InspeximusMemoryService(path=_path(), **kw)


def _texts(resp):
    """SearchMemoryResponse -> the strings in it, without depending on the exact field layout."""
    return str(getattr(resp, "memories", resp))


def _entry(text):
    """A real ADK MemoryEntry. `add_memory` reads `m.content.parts[].text` -- handing it plain strings
    stored empty text and every search came back `[]`, which looked like a broken adapter and was a
    broken fixture."""
    from google.adk.memory.memory_entry import MemoryEntry
    from google.genai import types as gt
    return MemoryEntry(content=gt.Content(role="user", parts=[gt.Part(text=text)]),
                       author="user", timestamp=None)


def test_adk_add_memory_then_search_finds_it():
    svc = _svc()
    _run(svc.add_memory(app_name="app", user_id="u1",
                        memories=[_entry("the deployment window is 02:00 UTC")]))
    resp = _run(svc.search_memory(app_name="app", user_id="u1", query="deployment window"))
    assert "02:00" in _texts(resp), _texts(resp)


def test_adk_search_does_not_leak_another_user():
    svc = _svc()
    _run(svc.add_memory(app_name="app", user_id="alice", memories=[_entry("alice api key is A-123")]))
    _run(svc.add_memory(app_name="app", user_id="bob", memories=[_entry("bob api key is B-456")]))

    got = _texts(_run(svc.search_memory(app_name="app", user_id="alice", query="api key")))
    assert "A-123" in got, got
    assert "B-456" not in got, f"one user's memory must not surface for another: {got}"


def test_adk_search_does_not_leak_another_app():
    svc = _svc()
    _run(svc.add_memory(app_name="app-one", user_id="u1", memories=[_entry("one secret is ONE")]))
    _run(svc.add_memory(app_name="app-two", user_id="u1", memories=[_entry("two secret is TWO")]))

    got = _texts(_run(svc.search_memory(app_name="app-one", user_id="u1", query="secret")))
    assert "ONE" in got and "TWO" not in got, got


def test_adk_forget_subject_for_erases_that_user_and_only_that_user():
    """The DSAR surface. If this over-reaches it deletes a third party's data; if it under-reaches the
    request was not honoured."""
    svc = _svc()
    _run(svc.add_memory(app_name="app", user_id="alice", memories=[_entry("alice lives at 12 Oak St")]))
    _run(svc.add_memory(app_name="app", user_id="bob", memories=[_entry("bob lives at 9 Elm St")]))

    res = svc.forget_subject_for(app_name="app", user_id="alice", request_id="DSAR-1")
    assert res, res

    alice = _texts(_run(svc.search_memory(app_name="app", user_id="alice", query="lives")))
    bob = _texts(_run(svc.search_memory(app_name="app", user_id="bob", query="lives")))
    assert "Oak St" not in alice, alice
    assert "Elm St" in bob, f"the other user's data must survive the DSAR: {bob}"


def test_adk_an_empty_query_returns_nothing_rather_than_everything():
    """Contract test, NOT a mutation kill, and the difference is worth writing down.

    Removing the `if not query` short-circuit is an EQUIVALENT mutant here: `recall("")` returns 0 hits
    (measured), so the guard changes nothing observable -- it is a cheap early return, not a boundary.
    Recording that is more useful than chasing a survivor that cannot be killed, or pretending it was."""
    svc = _svc()
    _run(svc.add_memory(app_name="app", user_id="u1", memories=[_entry("a private note about payroll")]))
    resp = _run(svc.search_memory(app_name="app", user_id="u1", query=""))
    assert not getattr(resp, "memories", []), _texts(resp)


def test_adk_from_uri_builds_a_service():
    svc = InspeximusMemoryService.from_uri("inspeximus://" + _path().replace("\\", "/"))
    assert isinstance(svc, InspeximusMemoryService)


def test_adk_register_is_idempotent():
    register()
    register()


def test_adk_serves_the_corrected_value():
    svc = _svc(extractor=lambda t: ("window", t.split()[-1]))
    _run(svc.add_memory(app_name="app", user_id="u1", memories=[_entry("the deployment window is 02:00")]))
    _run(svc.add_memory(app_name="app", user_id="u1", memories=[_entry("the deployment window is 04:00")]))

    got = _texts(_run(svc.search_memory(app_name="app", user_id="u1", query="deployment window")))
    assert "04:00" in got, got
    assert "02:00" not in got, f"the superseded value must not be returned as memory: {got}"


# ── AutoGen: the Memory protocol ────────────────────────────────────────────────────────────────────
def _mem_cls():
    pytest.importorskip("autogen_core")
    from inspeximus.integrations.autogen import InspeximusMemory
    return InspeximusMemory


def _content(text):
    from autogen_core.memory import MemoryContent
    return MemoryContent(content=text, mime_type="text/plain")


def test_autogen_add_then_query_finds_it():
    m = _mem_cls()(path=_path())
    _run(m.add(_content("the pager rotation is weekly")))
    res = _run(m.query("pager rotation"))
    assert "weekly" in str(res), res


def test_autogen_update_context_injects_memory_into_the_model_context():
    """`update_context` is how AutoGen actually gets memory in front of the model -- 13 body lines, and
    the only path that matters at inference time."""
    from autogen_core.model_context import BufferedChatCompletionContext
    from autogen_core.models import UserMessage

    m = _mem_cls()(path=_path())
    _run(m.add(_content("the incident commander is Dana")))

    ctx = BufferedChatCompletionContext(buffer_size=10)
    _run(ctx.add_message(UserMessage(content="who is the incident commander?", source="user")))
    _run(m.update_context(ctx))

    msgs = str(_run(ctx.get_messages()))
    assert "Dana" in msgs, f"the memory must reach the model context: {msgs}"


def test_autogen_clear_empties_it():
    m = _mem_cls()(path=_path())
    _run(m.add(_content("something worth forgetting")))
    assert "forgetting" in str(_run(m.query("forgetting")))
    _run(m.clear())
    assert "forgetting" not in str(_run(m.query("forgetting"))), "clear must actually clear"


def test_autogen_close_does_not_lose_what_was_written():
    p = _path()
    m = _mem_cls()(path=p)
    _run(m.add(_content("durable across close")))
    _run(m.close())

    from inspeximus import Inspeximus
    assert any("durable across close" in r.get("text", "") for r in Inspeximus(path=p).items), \
        "close() must flush, not discard"


def test_autogen_serves_the_corrected_value():
    m = _mem_cls()(path=_path(), extractor=lambda t: ("commander", t.split()[-1]))
    _run(m.add(_content("the incident commander is Dana")))
    _run(m.add(_content("the incident commander is Priya")))

    res = str(_run(m.query("incident commander")))
    assert "Priya" in res, res
    assert "Dana" not in res, f"the superseded value must not be returned: {res}"
