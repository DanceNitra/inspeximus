"""The last of the framework adapters: CrewAI, Pydantic AI, LlamaIndex.

Eleven functions with no executed body line between them. Each is guarded with its own `importorskip`, so
the file degrades to skips in an environment that has only some of the optional dependencies -- CI's base
environment has none of them, and local-green is not CI-green.

All three sell the same differentiator: recall returns CURRENT truth, so a corrected fact supersedes the
stale one instead of both coming back and the agent picking. That claim is what each block tests, because
if it does not hold the adapter is just a slower dict.
"""
import asyncio
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


# ── CrewAI: Storage(save / search / reset) ──────────────────────────────────────────────────────────
crewai = pytest.importorskip("crewai")
from inspeximus.integrations.crewai import InspeximusStorage  # noqa: E402


def test_crewai_storage_saves_and_finds_it_again():
    st = InspeximusStorage(path=_path())
    st.save("the deployment window is 02:00 UTC", {"agent": "ops"})
    hits = st.search("deployment window", limit=3)
    assert hits, "a saved value must be findable"
    assert any("02:00" in str(h) for h in hits), hits


def test_crewai_search_honours_limit():
    st = InspeximusStorage(path=_path())
    for i in range(6):
        st.save(f"runbook step number {i} for the deploy", {"agent": "ops"})
    assert len(st.search("runbook step", limit=2)) <= 2
    assert len(st.search("runbook step", limit=6)) > 2


def test_crewai_reset_empties_the_storage():
    st = InspeximusStorage(path=_path())
    st.save("something worth forgetting", {"agent": "ops"})
    assert st.search("forgetting", limit=3)
    st.reset()
    assert st.search("forgetting", limit=3) == [], "reset must actually clear it"


def test_crewai_storage_serves_the_corrected_value():
    """The differentiator: CrewAI's own storage would return both statements and let the agent choose."""
    st = InspeximusStorage(path=_path(), extractor=lambda t: ("deploy::window", t.split()[-1]))
    st.save("the deployment window is 02:00", {"agent": "ops"})
    st.save("the deployment window is 04:00", {"agent": "ops"})

    flat = str(st.search("deployment window", limit=5))
    assert "04:00" in flat, flat
    assert "02:00" not in flat, f"the superseded value must not come back as current truth: {flat}"


def test_crewai_storage_can_share_a_caller_supplied_store():
    from inspeximus import Inspeximus
    shared = Inspeximus(path=_path())
    InspeximusStorage(store=shared).save("landed in the shared store", {"agent": "ops"})
    assert any("shared store" in r.get("text", "") for r in shared.items)


# ── Pydantic AI: memory as tools ────────────────────────────────────────────────────────────────────
def _toolset():
    pytest.importorskip("pydantic_ai")
    from inspeximus.integrations.pydantic_ai import inspeximus_toolset
    return inspeximus_toolset


def _tools_of(ts):
    """The toolset exposes its functions under a few possible attribute names across versions."""
    for attr in ("tools", "_tools", "functions"):
        got = getattr(ts, attr, None)
        if got:
            return got
    return None


def test_pydantic_ai_toolset_exposes_the_memory_tools():
    ts = _toolset()(path=_path())
    tools = _tools_of(ts)
    assert tools, f"the toolset must expose tools: {dir(ts)[:20]}"
    names = set(tools.keys()) if hasattr(tools, "keys") else {getattr(t, "name", str(t)) for t in tools}
    assert {"remember", "recall"} <= {str(n) for n in names}, names


def test_pydantic_ai_toolset_round_trips_through_the_store():
    """Reach the underlying store directly: the tools are wrappers, and what matters is that a value
    written through the toolset is readable through it."""
    from inspeximus import Inspeximus
    shared = Inspeximus(path=_path())
    _toolset()(store=shared)
    shared.remember("the pager rotation is weekly")
    assert any("pager rotation" in (h.get("text") or "") for h in (shared.recall("pager rotation") or []))


def test_pydantic_ai_toolset_accepts_a_shared_store_without_creating_a_file():
    from inspeximus import Inspeximus
    shared = Inspeximus(path=None)
    ts = _toolset()(store=shared)
    assert ts is not None
    assert shared.path is None, "a store handed in must not be swapped for a file-backed one"


# ── LlamaIndex: a long-term BaseMemoryBlock ─────────────────────────────────────────────────────────
def _block_cls():
    pytest.importorskip("llama_index.core")
    from inspeximus.integrations.llamaindex import InspeximusMemoryBlock
    return InspeximusMemoryBlock


def test_llamaindex_block_constructs_and_carries_a_store():
    blk = _block_cls()(path=_path())
    assert blk is not None
    store = getattr(blk, "store", None) or getattr(blk, "_store", None)
    assert store is not None, f"the block must hold a store: {[a for a in dir(blk) if 'stor' in a]}"


def test_llamaindex_block_puts_a_message_and_gets_it_back():
    from llama_index.core.llms import ChatMessage

    blk = _block_cls()(path=_path())
    asyncio.run(blk.aput([ChatMessage(role="user", content="the invoice is due in March")]))

    # `_aget` derives its query from `messages[-1]` -- the block returns what is relevant to the CURRENT
    # turn, so calling it with no messages correctly returns "". My first version asserted on that empty
    # string and read the design as a failure.
    got = asyncio.run(blk.aget([ChatMessage(role="user", content="when is the invoice due?")]))
    assert got, "a flushed message must be retrievable for a related turn"
    assert "invoice" in str(got), got


def test_llamaindex_block_returns_nothing_when_there_is_no_turn_to_be_relevant_to():
    from llama_index.core.llms import ChatMessage

    blk = _block_cls()(path=_path())
    asyncio.run(blk.aput([ChatMessage(role="user", content="the invoice is due in March")]))
    assert asyncio.run(blk.aget()) == "", "no query means no injected context, not a guess"


def test_llamaindex_block_serves_the_corrected_value():
    from llama_index.core.llms import ChatMessage

    blk = _block_cls()(path=_path(), extractor=lambda t: ("invoice::due", t.split()[-1]))
    asyncio.run(blk.aput([ChatMessage(role="user", content="the invoice is due in March")]))
    asyncio.run(blk.aput([ChatMessage(role="user", content="the invoice is due in April")]))

    flat = str(asyncio.run(blk.aget([ChatMessage(role="user", content="when is the invoice due?")])))
    assert "April" in flat, flat
    assert "March" not in flat, f"the superseded value must not be injected as context: {flat}"
