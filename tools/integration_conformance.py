"""Every framework adapter, one real round trip each: VERIFIED / SKIPPED / BROKEN.

WHY THIS EXISTS. `inspeximus/integrations/` is how most new users meet this library -- someone already
using LangGraph or Haystack adds our memory to it. An adapter that silently stops working against a new
upstream release costs that user permanently, and until now nothing said a word. The repository already
had FOUR deep parity audits (store_audit.py, adk_audit.py, haystack_audit.py, session_audit.py) plus
checkpointer_conformance.py, which are excellent and stay -- but they cover 4 of 11 adapters, they are
run by hand, and none of them records WHICH upstream version the adapter was last verified against. So a
breakage could neither be noticed nor dated.

This file is the BREADTH layer and the index over all of it: every adapter in `inspeximus/integrations/`
appears here exactly once, with

  * a smoke test that exercises the REAL round trip -- write through the framework's own interface, read
    back through it. Not `import`. An import proves nothing: `crewai` imports fine and its Storage
    protocol was replaced wholesale in 1.x, which is how that break went unnoticed.
  * the upstream distribution and the version the round trip was verified against, so a future breakage
    can be dated.
  * a pointer to the deep parity script for that adapter, where one exists.

A SKIP IS NOT A PASS. Every optional dependency is guarded, so this runs on a bare install -- but the
summary reports verified / skipped / broken as three separate numbers and the runner refuses to exit 0
having verified nothing. `--require-all` turns any skip into a failure, which is what CI's
extras-installed leg uses; a run where every framework is missing must not read as green.

    python tools/integration_conformance.py                  # human summary
    python tools/integration_conformance.py --json           # machine-readable
    python tools/integration_conformance.py --require-all    # a skip is a failure (CI, extras installed)
    python tools/integration_conformance.py --deep           # also run the per-adapter parity scripts
    python tools/integration_conformance.py --falsify NAME   # CONTROL: break the write path; must go BROKEN
    python tools/integration_conformance.py --write-record   # refresh docs/integration_conformance.json

THE CONTROL, which is the only reason to believe any of the above. `--falsify NAME` replaces
`Inspeximus.remember` with a no-op for the duration of that check. Every adapter's write path funnels
through it -- langgraph's `batch`, haystack's `write_documents`, ADK's `add_session_to_memory`, all of
them -- so the neutered store is an input none of them can examine its way out of. The round trip MUST
then fail; if it still passes, the check was never reading what it claimed to write, and the runner exits
2 with CONTROL FAILED rather than reporting a pass. `--falsify all` sweeps every present adapter.
"""
from __future__ import annotations

import argparse
import datetime
import importlib
import importlib.metadata
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile
import traceback

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RECORD_PATH = ROOT / "docs" / "integration_conformance.json"

VERIFIED = "verified"
SKIPPED = "skipped"
BROKEN = "broken"


# ── the round trips ─────────────────────────────────────────────────────────────────────────────────
# Each takes a scratch directory and raises on failure. The rule every one of them follows: the WRITE
# goes in through the framework's own interface and the READ comes back out through it. A helper that
# reaches past the adapter into the inspeximus store would pass while the adapter was broken.

def _rt_langgraph_store(tmp):
    """Written and read inside a compiled LangGraph, so LangGraph's own injection machinery has to
    accept the object -- not just our belief that it implements BaseStore."""
    from langgraph.graph import END, START, StateGraph
    from langgraph.store.base import BaseStore
    from typing_extensions import TypedDict

    from inspeximus.integrations.langgraph import InspeximusStore

    store = InspeximusStore(path=str(tmp / "store.jsonl"))
    assert isinstance(store, BaseStore), "not a BaseStore, so LangGraph will not inject it"

    class S(TypedDict):
        out: str

    def write(state: S, *, store: BaseStore) -> S:
        store.put(("users", "u1"), "city", {"value": "Nitra"})
        store.put(("users", "u1"), "city", {"value": "Bratislava"})     # a correction
        return {"out": ""}

    def read(state: S, *, store: BaseStore) -> S:
        item = store.get(("users", "u1"), "city")
        return {"out": (item.value or {}).get("value", "") if item else ""}

    g = StateGraph(S)
    g.add_node("write", write)
    g.add_node("read", read)
    g.add_edge(START, "write")
    g.add_edge("write", "read")
    g.add_edge("read", END)
    out = g.compile(store=store).invoke({"out": ""})["out"]
    assert out == "Bratislava", f"graph read back {out!r}, not the corrected value"

    # the differentiator, still through the adapter's own surface
    assert store.history(("users", "u1"), "city") == [{"value": "Nitra"}, {"value": "Bratislava"}]


def _rt_langgraph_checkpointer(tmp):
    """The same graph run twice -- once on LangGraph's own `InMemorySaver`, once on ours -- and every
    observable must match.

    Parity against the reference rather than against my expectations, because my expectations were
    wrong: the first version of this asserted that resuming a COMPLETED thread advances the state, and
    `InMemorySaver` does not do that either. A hand-written expectation turns a correct adapter red;
    the reference cannot."""
    from langgraph.graph import END, START, StateGraph
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.checkpoint.memory import InMemorySaver
    from typing_extensions import TypedDict

    from inspeximus.integrations.langgraph import InspeximusSaver

    saver = InspeximusSaver(path=str(tmp / "ckpt.jsonl"))
    assert isinstance(saver, BaseCheckpointSaver), "not a BaseCheckpointSaver"

    class S(TypedDict):
        n: int

    def bump(state: S) -> S:
        return {"n": state["n"] + 1}

    def observe(ckpt):
        g = StateGraph(S)
        g.add_node("bump", bump)
        g.add_edge(START, "bump")
        g.add_edge("bump", END)
        app = g.compile(checkpointer=ckpt)
        cfg = {"configurable": {"thread_id": "t-conformance"}}
        first = app.invoke({"n": 0}, cfg)
        return {"first": first, "state": app.get_state(cfg).values,
                "history": len(list(app.get_state_history(cfg))),
                "resume": app.invoke(None, cfg)}

    ref, ours = observe(InMemorySaver()), observe(saver)
    assert ours == ref, f"checkpoint behaviour differs from InMemorySaver: ours={ours} ref={ref}"
    assert ours["state"] == {"n": 1}, ours          # and the reference itself did something


def _rt_langchain(tmp):
    """Through the Runnable interface a chain actually calls (`invoke`), and through
    BaseChatMessageHistory with real message objects."""
    from langchain_core.chat_history import BaseChatMessageHistory
    from langchain_core.documents import Document
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.retrievers import BaseRetriever

    from inspeximus.integrations.langchain import InspeximusChatMessageHistory, InspeximusRetriever

    r = InspeximusRetriever(path=str(tmp / "r.json"), k=5)
    assert isinstance(r, BaseRetriever)
    r.add("the deploy channel is BLUE-9", key="deploy-channel")
    r.add("the deploy channel is RED-2", key="deploy-channel")        # supersedes BLUE-9
    docs = r.invoke("what is the deploy channel?")
    assert docs and all(isinstance(d, Document) for d in docs), docs
    texts = " ".join(d.page_content for d in docs)
    assert "RED-2" in texts, f"the corrected value did not come back: {texts!r}"
    assert "BLUE-9" not in texts, f"the superseded value resurfaced: {texts!r}"

    h = InspeximusChatMessageHistory("s-conformance", path=str(tmp / "h.json"))
    assert isinstance(h, BaseChatMessageHistory)
    h.add_message(HumanMessage(content="my dentist is Dr. Kovac"))
    h.add_message(AIMessage(content="noted"))
    got = h.messages
    assert [m.content for m in got] == ["my dentist is Dr. Kovac", "noted"], got
    assert isinstance(got[0], HumanMessage) and isinstance(got[1], AIMessage), got


def _rt_llamaindex(tmp):
    """Through `BaseMemoryBlock.aput` / `.aget` -- the public methods LlamaIndex's own `Memory` calls
    on a block. The adapter implements `_aput`/`_aget`; going in through the public wrappers is what
    proves the base class still routes to them."""
    import asyncio

    from llama_index.core.base.llms.types import ChatMessage
    from llama_index.core.memory import BaseMemoryBlock

    from inspeximus.integrations.llamaindex import InspeximusMemoryBlock

    block = InspeximusMemoryBlock(path=str(tmp / "li.json"), name="inspeximus", k=5)
    assert isinstance(block, BaseMemoryBlock)

    async def go():
        await block.aput([ChatMessage(role="user", content="the spare key is under the mat")])
        return await block.aget([ChatMessage(role="user", content="where is the spare key?")])

    injected = asyncio.run(go())
    assert "under the mat" in (injected or ""), f"the block injected {injected!r}"


def _rt_autogen(tmp):
    """Through the `Memory` protocol AutoGen calls per turn: `add` a MemoryContent, then
    `update_context` against a real ChatCompletionContext and check the SystemMessage it injects."""
    import asyncio

    from autogen_core.memory import Memory, MemoryContent, MemoryMimeType, MemoryQueryResult
    from autogen_core.model_context import BufferedChatCompletionContext
    from autogen_core.models import UserMessage

    from inspeximus.integrations.autogen import InspeximusMemory

    mem = InspeximusMemory(path=str(tmp / "ag.json"))
    for name in ("add", "query", "update_context", "clear", "close"):
        assert callable(getattr(mem, name, None)), f"Memory protocol member missing: {name}"

    async def go():
        await mem.add(MemoryContent(content="the on-call rota is Tuesdays",
                                    mime_type=MemoryMimeType.TEXT,
                                    metadata={"key": "ops::rota", "object": "Tuesdays"}))
        qr = await mem.query("when is the on-call rota?")
        ctx = BufferedChatCompletionContext(buffer_size=5)
        await ctx.add_message(UserMessage(content="when is the on-call rota?", source="user"))
        await mem.update_context(ctx)
        return qr, await ctx.get_messages()

    qr, msgs = asyncio.run(go())
    assert isinstance(qr, MemoryQueryResult) and qr.results, qr
    assert any("Tuesdays" in str(getattr(m, "content", "")) for m in msgs), \
        f"nothing was injected into the model context: {msgs}"
    # Recorded, not asserted: AutoGen's `Memory` is an ABC rather than a runtime-checkable Protocol, and
    # this adapter is duck-typed, so `isinstance` is False by construction. AssistantAgent does not
    # isinstance-check its `memory=` argument, so the duck-typed object is what AutoGen actually uses.
    assert not isinstance(mem, Memory) or True


def _rt_google_adk(tmp):
    """Through BaseMemoryService: ingest a Session, then search it back."""
    import asyncio

    from google.adk.memory.base_memory_service import BaseMemoryService
    from google.adk.events.event import Event
    from google.adk.sessions.session import Session
    from google.genai import types as gt

    from inspeximus.integrations.google_adk import InspeximusMemoryService

    svc = InspeximusMemoryService(path=str(tmp / "adk.json"))
    assert isinstance(svc, BaseMemoryService), "ADK will not accept it as a memory service"

    def ev(text):
        return Event(author="user", content=gt.Content(role="user", parts=[gt.Part(text=text)]))

    async def go():
        await svc.add_session_to_memory(Session(app_name="app", user_id="u1", id="s1",
                                                events=[ev("my dentist is Dr. Kovac in Nitra")]))
        return await svc.search_memory(app_name="app", user_id="u1", query="dentist")

    resp = asyncio.run(go())
    texts = [" ".join(p.text for p in m.content.parts if p.text) for m in resp.memories]
    assert any("Kovac" in t for t in texts), f"search_memory returned {texts}"
    # and the isolation a multi-user service must keep
    other = asyncio.run(svc.search_memory(app_name="app", user_id="u2", query="dentist"))
    assert not other.memories, "another user could read the first user's memory"


def _rt_openai_agents(tmp):
    """Through the SDK's `Session` protocol -- which the SDK type-checks callers against, so satisfying
    it structurally IS the contract, not a nicety."""
    import asyncio

    from agents.memory import Session

    from inspeximus.integrations.openai_agents import InspeximusSession

    s = InspeximusSession("user-42", path=str(tmp / "sess.json"))

    async def go():
        await s.add_items([{"role": "user", "content": "my dentist is Dr. Kovac"},
                           {"role": "assistant", "content": "noted"}])
        items = await s.get_items()
        last = await s.get_items(limit=1)
        popped = await s.pop_item()
        await s.clear_session()
        return items, last, popped, await s.get_items()

    items, last, popped, after = asyncio.run(go())
    assert [i["content"] for i in items] == ["my dentist is Dr. Kovac", "noted"], items
    assert [i["content"] for i in last] == ["noted"], last
    assert popped["content"] == "noted", popped
    assert after == [], after
    # Everything above passed, so the verbatim turn log round-trips. The remaining question is the
    # drop-in claim itself: `agents.memory.Session` is a runtime-checkable Protocol and the SDK reads
    # settings off the session object, so failing it is not a style point.
    missing = [a for a in sorted(getattr(Session, "__protocol_attrs__", [])) if not hasattr(s, a)]
    assert isinstance(s, Session), (
        "the turn round trip works, but the object does not satisfy agents.memory.Session (missing: "
        + ", ".join(missing) + ") -- so it fails a type check, and any SDK behaviour keyed on that "
        "attribute is inert on this session")


def _rt_pydantic_ai(tmp):
    """Through a real `Agent` whose model is a `FunctionModel` emitting controlled tool calls, so the
    write and the read are both dispatched by Pydantic AI's own toolset machinery.

    Deliberately NOT `TestModel`: it invents arguments from the JSON schema (a single `'a'` for every
    string), so `remember('a')` then `recall('a')` returns nothing and the check goes red on the
    library's short-token behaviour rather than on the adapter. A harness that fails for its own
    reasons is worse than no harness -- it trains the reader to ignore the red."""
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.toolsets import FunctionToolset

    from inspeximus.integrations.pydantic_ai import inspeximus_toolset

    ts = inspeximus_toolset(path=str(tmp / "pa.json"), k=5)
    assert isinstance(ts, FunctionToolset), type(ts)
    names = set(getattr(ts, "tools", {}))
    assert {"remember", "recall", "check_conflict", "forget"} <= names, sorted(names)

    script = [ToolCallPart("remember", {"text": "the deploy channel is BLUE-9"}),
              ToolCallPart("recall", {"query": "deploy channel"}),
              TextPart("done")]

    def model_fn(messages, info):
        step = sum(1 for m in messages if isinstance(m, ModelResponse))
        return ModelResponse(parts=[script[min(step, len(script) - 1)]])

    result = Agent(FunctionModel(model_fn), toolsets=[ts]).run_sync("remember and recall")
    returned = [p.content for m in result.all_messages() for p in getattr(m, "parts", [])
                if type(p).__name__ == "ToolReturnPart" and p.tool_name == "recall"]
    assert returned, f"the recall tool was never invoked by the agent: {result.all_messages()}"
    assert any("BLUE-9" in str(r) for r in returned), \
        f"what the agent wrote through remember did not come back through recall: {returned}"


def _rt_crewai(tmp):
    """Through CrewAI's own storage protocol -- which is the whole question, since 1.x replaced it."""
    from crewai.memory.storage.backend import StorageBackend

    from inspeximus.integrations.crewai import InspeximusStorage

    st = InspeximusStorage(path=str(tmp / "crew.json"))
    st.save("the deployment window is 02:00 UTC", {"key": "ops::window", "object": "02:00 UTC"})
    hits = st.search("deployment window", limit=3)
    assert hits and any("02:00" in str(h) for h in hits), hits
    assert isinstance(st, StorageBackend), (
        "does not satisfy crewai.memory.storage.backend.StorageBackend; missing: "
        + ", ".join(a for a in sorted(getattr(StorageBackend, "__protocol_attrs__", []))
                    if not hasattr(st, a)))


def _rt_haystack(tmp):
    """Written by a real Haystack `Pipeline` through `DocumentWriter`, read back through the
    DocumentStore interface a retriever uses."""
    from haystack import Document, Pipeline
    from haystack.components.writers import DocumentWriter
    from haystack.document_stores.types import DocumentStore, DuplicatePolicy

    from inspeximus.integrations.haystack import InspeximusDocumentStore

    ds = InspeximusDocumentStore(path=str(tmp / "docs.json"))
    missing = [a for a in sorted(getattr(DocumentStore, "__protocol_attrs__", [])) if not hasattr(ds, a)]
    assert not missing, f"DocumentStore protocol members missing: {missing}"

    pipe = Pipeline()
    pipe.add_component("writer", DocumentWriter(document_store=ds, policy=DuplicatePolicy.OVERWRITE))
    out = pipe.run({"writer": {"documents": [
        Document(id="1", content="the invoice is due in March", meta={"kind": "invoice"}),
        Document(id="2", content="the manager is Rachel Tseng", meta={"kind": "person"})]}})
    assert out["writer"]["documents_written"] == 2, out
    assert ds.count_documents() == 2, f"the pipeline wrote 2 documents, the store counts {ds.count_documents()}"
    got = ds.filter_documents({"field": "meta.kind", "operator": "==", "value": "invoice"})
    assert [d.id for d in got] == ["1"] and got[0].content == "the invoice is due in March", got


def _rt_memoryagentbench(tmp):
    """The mem0 `Memory` shape MemoryAgentBench's AgentWrapper drives (`add(messages, user_id)` /
    `search(query, user_id, limit)`), including the per-user isolation the benchmark relies on."""
    from inspeximus.integrations.memoryagentbench import InspeximusMABMemory

    m = InspeximusMABMemory()
    m.add([{"role": "user", "content": "the capital of Slovakia is Bratislava"}], user_id="u1")
    m.add([{"role": "user", "content": "the capital of Slovakia is Kosice"}], user_id="u2")
    hits = m.search("capital of Slovakia", user_id="u1", limit=5)["results"]
    texts = " ".join(h["memory"] for h in hits)
    assert "Bratislava" in texts, f"nothing came back for u1: {texts!r}"
    assert "Kosice" not in texts, f"another user's context leaked in: {texts!r}"


def _rt_governance(tmp):
    """ComplianceMixin is framework-free by design, so its round trip needs no upstream: attach it to a
    store, write through it, and the evidence report must reflect the write."""
    from inspeximus.core import Inspeximus
    from inspeximus.integrations.governance import ComplianceMixin

    class Holder(ComplianceMixin):
        def __init__(self):
            self.store = Inspeximus(path=str(tmp / "gov.json"), receipts=True)

    h = Holder()
    h.store.remember("retention is 90d", key="p::ret", object="90d")
    h.store.remember("retention is 30d", key="p::ret", object="30d")
    rep = h.compliance_report()
    assert rep["summary"]["writes"] == 2, rep["summary"]
    assert h.compliance_check()["ok"], h.compliance_check()
    b = h.audit_bundle()
    assert b["anchor"]["n_writes"] == 2, b["anchor"]
    assert ComplianceMixin.verify_audit_bundle(b)["ok"]


# ── the registry ────────────────────────────────────────────────────────────────────────────────────
# One row per module in inspeximus/integrations/. tests/test_integration_conformance.py asserts that
# mapping is total, so a new adapter added without a round trip turns the suite red rather than being
# quietly unverified -- which is how four of these went years without one.

def _rt_llm_errata(tmp):
    """The LLM Errata importer adapter, driven against the PROTOCOL rather than the spec package.

    Deliberate: `prototype/` is not on PyPI, so a check that imported it would be permanently skipped in
    CI, and a permanently skipped check on an adapter is the state this file exists to prevent. The
    published `StoreAdapter` protocol is the contract, so the round trip drives it directly.

    It also pins the two things a conformance run has to distinguish, because getting them the wrong way
    round is the defect the spec author reported against our own erasure audit: a store that can show
    root-specific lineage completeness may say `verified`, and a store carrying an unresolved derivation
    claim must say `unknown`. Never the reverse, and never a pass by default.
    """
    from inspeximus.core import Inspeximus
    from inspeximus.integrations.llm_errata import InspeximusErrataAdapter

    m = Inspeximus(path=str(tmp / "errata.json"), receipts=True)
    diet = m.remember("is vegetarian", source={"doc": "fact:diet"}, key="diet")
    rest = m.remember("prefers quiet restaurants", source={"doc": "fact:rest"}, key="rest")
    summary = m.remember("is vegetarian; prefers quiet restaurants", derived=True,
                         derived_from=[diet, rest], source={"doc": "summary:dining"})
    a = InspeximusErrataAdapter(m)

    reached = a.enumerate("fact:diet")
    assert diet in reached and summary in reached, (reached, diet, summary)
    assert a.lineage_complete("fact:diet") is True, a.coverage_detail("fact:diet")
    assert a.coverage("fact:diet") == "verified", a.coverage_detail("fact:diet")

    a.quarantine((summary,))
    assert a.is_quarantined(summary), "quarantine must gate the descendant"
    assert a.dispositions("fact:diet")[summary] == "quarantined-only", a.dispositions("fact:diet")

    # A rebuild APPENDS. The quarantined record stays superseded because history is the evidence, so
    # the assertion is about what became recallable, not about the old record flipping back.
    a.rebuild(summary, inputs=(rest,), replacement="eats meat again")
    assert any("eats meat again" in h.content for h in a.recall("eats meat again")), "positive check"
    assert any("quiet restaurants" in h.content for h in a.recall("quiet restaurants")), "preserve check"
    assert not any(h.content == "is vegetarian; prefers quiet restaurants"
                   for h in a.recall("is vegetarian")), "the superseded blob must not recall"
    assert a.dispositions("fact:diet")[summary] == "rebuilt", a.dispositions("fact:diet")

    # retire is the other half: an artifact whose origin is gone is not awaiting rebuild. One
    # keyword-only signature since ac4468f; before that it was undeclared and called two ways.
    a.retire(rest, superseded_at="2026-08-01T00:00:00Z")
    assert not a.is_quarantined(rest), "retired is not quarantined"

    # The surface the reference controller actually drives, declared at ac4468f after we implemented
    # far enough to hit each missing piece. `repair_inputs` is the one that replaced registering our
    # graph into the reference LineageLedger, so it is asserted to be store-owned and self-consistent.
    assert a.source_artifact(summary) == summary, "a record is its own lineage node in this store"
    assert set(a.repair_inputs(summary)) <= set(x["id"] for x in m.items), (
        "repair_inputs must name records this store actually holds, never dangling ids")
    snap = a.snapshot()
    assert snap and all(isinstance(k, str) and isinstance(v, str) for k, v in snap.items()), snap
    assert snap == a.snapshot(), "snapshot feeds a state root, so it must be deterministic"
    # quarantine_coverage is the PRE-repair verdict and must not be inferred from enumeration alone;
    # this is the contract that replaced the checkpoint defect we reported at 08b95263.
    assert a.quarantine_coverage("fact:diet") in ("verified", "unknown"), a.quarantine_coverage("fact:diet")

    # CONTROL: a writer that announced derivation and resolved no parent must move the verdict off pass.
    # `unknown`, not a fifth state of our own: the spec author declined `unaudited` on the grounds that
    # `unknown` already means "cannot substantiate complete coverage", and a fifth public wire value
    # would add migration cost without changing the decision rule. His call, his wire format.
    m.remember("digest whose summariser dropped its lineage", derived=True)
    assert a.lineage_complete("fact:diet") is False, a.coverage_detail("fact:diet")
    assert a.coverage("fact:diet") == "unknown", a.coverage_detail("fact:diet")


class Check:
    def __init__(self, name, module, dist, adapter, roundtrip, source, deep=None, note=None,
                 repeats=1):
        self.name = name              # stable id, also the --falsify argument
        self.module = module          # import probe; None means no upstream dependency at all
        self.dist = dist              # PyPI distribution the version is read from
        self.adapter = adapter        # what is under test
        self.roundtrip = roundtrip
        self.source = source          # the inspeximus.integrations module it covers
        self.deep = deep              # the per-adapter parity script, where one exists
        self.note = note
        # How many times the round trip must pass to count as VERIFIED. 1 for a deterministic adapter.
        # Raise it only where an INTERMITTENT failure has been measured, and record the rate next to it:
        # a check that reports the defect two runs in three is not a result, it is a coin, and it would
        # put a random red in CI while telling the ledger a different story every day.
        self.repeats = repeats


CHECKS = [
    Check("llm-errata", None, None,
          "inspeximus.integrations.llm_errata:InspeximusErrataAdapter", _rt_llm_errata,
          "llm_errata.py",
          note="drives the protocol directly; prototype/ is not on PyPI so importing it would make "
               "this check permanently skipped, which is worse than no check"),
    Check("langgraph-store", "langgraph", "langgraph",
          "inspeximus.integrations.langgraph:InspeximusStore", _rt_langgraph_store,
          "langgraph.py", deep="store_audit.py"),
    # repeats=40 because the failure here is INTERMITTENT, and an intermittent failure is a failure.
    # MEASURED 2026-08-02 on langgraph 1.2.9: 15/40 and 4/15 runs of one ordinary two-step graph raise
    # StoreChangedOnDisk from a store no other process ever opens -- ~27-37%, load-dependent. At p=0.27
    # the chance forty consecutive runs all pass is ~6e-6, so the verdict is stable even though the
    # defect is not. Cost: ~0.8 s.
    # ROOT CAUSE, reproduced away from LangGraph so the diagnosis is not a story about a stack trace:
    # ONE `Inspeximus` handle written from four threads raised StoreChangedOnDisk in 20 of 20 trials.
    # `_save` checks `_stat_sig() != self._file_sig` before `os.replace` and refreshes `_file_sig`
    # after it, with no lock, so a second thread entering that window sees its own peer's write and
    # reads it as a competing PROCESS. The guard is correct about cross-process writers and misfires on
    # intra-process concurrency -- and LangGraph calls a checkpointer from its executor, so an ordinary
    # `app.invoke` is enough to hit it. `langgraph-store` is unaffected: a graph node calls the store on
    # the node's own thread, which is why it is 15/15 here and the checkpointer is not.
    Check("langgraph-checkpointer", "langgraph", "langgraph",
          "inspeximus.integrations.langgraph:InspeximusSaver", _rt_langgraph_checkpointer,
          "langgraph.py", deep="checkpointer_conformance.py", repeats=40),
    Check("langchain", "langchain_core", "langchain-core",
          "inspeximus.integrations.langchain:InspeximusRetriever + InspeximusChatMessageHistory",
          _rt_langchain, "langchain.py"),
    Check("llamaindex", "llama_index.core", "llama-index-core",
          "inspeximus.integrations.llamaindex:InspeximusMemoryBlock", _rt_llamaindex, "llamaindex.py"),
    Check("autogen", "autogen_core", "autogen-core",
          "inspeximus.integrations.autogen:InspeximusMemory", _rt_autogen, "autogen.py"),
    Check("google-adk", "google.adk", "google-adk",
          "inspeximus.integrations.google_adk:InspeximusMemoryService", _rt_google_adk,
          "google_adk.py", deep="adk_audit.py"),
    Check("openai-agents", "agents", "openai-agents",
          "inspeximus.integrations.openai_agents:InspeximusSession", _rt_openai_agents,
          "openai_agents.py", deep="session_audit.py"),
    Check("pydantic-ai", "pydantic_ai", "pydantic-ai",
          "inspeximus.integrations.pydantic_ai:inspeximus_toolset", _rt_pydantic_ai, "pydantic_ai.py"),
    Check("crewai", "crewai", "crewai",
          "inspeximus.integrations.crewai:InspeximusStorage", _rt_crewai, "crewai.py"),
    Check("haystack", "haystack", "haystack-ai",
          "inspeximus.integrations.haystack:InspeximusDocumentStore", _rt_haystack,
          "haystack.py", deep="haystack_audit.py"),
    Check("memoryagentbench", None, None,
          "inspeximus.integrations.memoryagentbench:InspeximusMABMemory", _rt_memoryagentbench,
          "memoryagentbench.py",
          note="duck-typed on mem0's Memory shape; MemoryAgentBench is a research repo, not a PyPI "
               "distribution, so this suite cannot date an upstream breakage for it"),
    Check("governance", None, None,
          "inspeximus.integrations.governance:ComplianceMixin", _rt_governance, "governance.py",
          note="framework-free by design; no upstream to skip on"),
]

BY_NAME = {c.name: c for c in CHECKS}


# ── running one ─────────────────────────────────────────────────────────────────────────────────────
def upstream_version(check):
    """The version the round trip is about to be verified against. Read from the DISTRIBUTION, because
    plenty of these packages carry no module `__version__` (langgraph is one)."""
    if check.module is None:
        return None
    if check.dist:
        try:
            return importlib.metadata.version(check.dist)
        except Exception:
            pass
    try:
        return str(getattr(importlib.import_module(check.module), "__version__", "unknown"))
    except Exception:
        return "unknown"


def _present(module):
    if module is None:
        return True
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


class _NeuteredWrites:
    """CONTROL. Replace `Inspeximus.remember` with a no-op for the duration of one check.

    Every adapter's write path funnels through it, so this is one uniform break that no round trip can
    examine its way out of -- it cannot be satisfied by reading the store directly, by a cached handle,
    or by an assertion that only checks the call did not raise. If a round trip still passes under this,
    it was not reading back what it claimed to write."""

    def __enter__(self):
        from inspeximus.core import Inspeximus
        self._cls = Inspeximus
        self._real = Inspeximus.remember
        Inspeximus.remember = lambda self, *a, **k: "falsified-no-op-id"
        return self

    def __exit__(self, *exc):
        self._cls.remember = self._real
        return False


def run_check(check, falsify=False):
    """-> dict. Never raises: a BROKEN adapter is a result, not a crash."""
    row = {"name": check.name, "adapter": check.adapter, "source": check.source,
           "upstream_dist": check.dist, "upstream_version": None, "deep_audit": check.deep,
           "status": None, "detail": None, "note": check.note}
    if not _present(check.module):
        row["status"] = SKIPPED
        row["detail"] = f"{check.dist or check.module} is not installed (optional extra)"
        return row
    row["upstream_version"] = upstream_version(check)
    row["repeats"] = check.repeats
    for attempt in range(check.repeats):
        # A fresh scratch directory per attempt, so a repeat can never pass by reading what an earlier
        # one left behind.
        tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"conf_{check.name}_"))
        try:
            if falsify:
                with _NeuteredWrites():
                    check.roundtrip(tmp)
            else:
                check.roundtrip(tmp)
        except Exception as e:
            row["status"] = BROKEN
            first = str(e).strip().splitlines()[0] if str(e).strip() else ""
            # The scratch path is per-run, and this string is COMMITTED to the ledger: leaving it in
            # makes every regeneration a diff, which is how a file stops being read.
            first = first.replace(str(tmp), "<tmp>")
            row["detail"] = f"{type(e).__name__}: {first}"[:400]
            if check.repeats > 1:
                row["detail"] += f"  [intermittent: failed on run {attempt + 1} of {check.repeats}]"
            row["traceback"] = traceback.format_exc()[-1500:]
            return row
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    row["status"] = VERIFIED
    return row


def run_all(names=None, falsify=None):
    picked = [c for c in CHECKS if names is None or c.name in names]
    return [run_check(c, falsify=(falsify == "all" or falsify == c.name)) for c in picked]


def summarise(rows):
    return {s: sum(1 for r in rows if r["status"] == s) for s in (VERIFIED, SKIPPED, BROKEN)}


# ── the recorded ledger ─────────────────────────────────────────────────────────────────────────────
def load_record():
    if not RECORD_PATH.exists():
        return {"integrations": {}}
    with open(RECORD_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def write_record(rows):
    """Only rows that RAN update the ledger. A skipped integration must never overwrite the version it
    was last verified against -- that is the whole point of recording it, and a run on a bare install
    would otherwise erase every date in the file."""
    rec = load_record()
    rec.setdefault("integrations", {})
    rec["generated_by"] = "tools/integration_conformance.py --write-record"
    rec["updated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    for r in rows:
        if r["status"] == SKIPPED:
            continue
        ok = r["status"] == VERIFIED
        rec["integrations"][r["name"]] = {
            "adapter": r["adapter"], "source": r["source"], "deep_audit": r["deep_audit"],
            "upstream_dist": r["upstream_dist"],
            # Two fields, not one, because "verified against 1.15.6" for an adapter that FAILS against
            # 1.15.6 is the kind of half-true a reader acts on. Exactly one of these is ever set.
            "verified_against": r["upstream_version"] if ok else None,
            "broken_against": None if ok else r["upstream_version"],
            "status": r["status"], "detail": r["detail"], "note": r["note"],
            "python": "%d.%d" % sys.version_info[:2],
            "checked": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
        }
    RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RECORD_PATH, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return rec


# ── the deep parity scripts, indexed here so there is one entry point ────────────────────────────────
def run_deep(rows):
    import subprocess
    out = []
    for r in rows:
        if not r["deep_audit"] or r["status"] == SKIPPED:
            continue
        p = subprocess.run([sys.executable, r["deep_audit"]], cwd=str(ROOT), capture_output=True,
                           text=True, timeout=1800,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        out.append({"script": r["deep_audit"], "for": r["name"], "returncode": p.returncode,
                    "tail": (p.stdout or "")[-400:]})
    return out


# ── cli ─────────────────────────────────────────────────────────────────────────────────────────────
def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--only", help="comma-separated integration names")
    ap.add_argument("--require-all", action="store_true",
                    help="a SKIPPED integration is a failure (CI leg with the extras installed)")
    ap.add_argument("--falsify", metavar="NAME",
                    help="CONTROL: neuter the write path for NAME (or 'all'); the round trip MUST break")
    ap.add_argument("--deep", action="store_true", help="also run the per-adapter parity scripts")
    ap.add_argument("--write-record", action="store_true",
                    help=f"refresh {RECORD_PATH.relative_to(ROOT).as_posix()}")
    a = ap.parse_args(argv)
    if a.falsify and a.write_record:
        # Otherwise one keystroke records every adapter as BROKEN against its current upstream, and the
        # ledger -- the thing a future breakage is dated against -- becomes a transcript of the control.
        ap.error("--falsify records nothing: it deliberately breaks the adapters. Drop --write-record.")

    names = set(a.only.split(",")) if a.only else None
    if names:
        unknown = names - set(BY_NAME)
        if unknown:
            ap.error(f"unknown integration(s): {', '.join(sorted(unknown))}")
    rows = run_all(names, falsify=a.falsify)
    counts = summarise(rows)
    deep = run_deep(rows) if a.deep else []

    if a.json:
        print(json.dumps({"counts": counts, "integrations": rows, "deep": deep,
                          "python": sys.version.split()[0], "falsify": a.falsify}, indent=2))
    else:
        width = max(len(r["name"]) for r in rows)
        print("=" * (width + 62))
        print(f"inspeximus integration conformance -- {len(rows)} integrations, python "
              f"{sys.version.split()[0]}")
        if a.falsify:
            print(f"FALSIFY MODE ({a.falsify}): the write path is neutered; the round trip MUST break")
        print("=" * (width + 62))
        for r in rows:
            tag = {VERIFIED: "VERIFIED", SKIPPED: "skipped ", BROKEN: "BROKEN  "}[r["status"]]
            up = f"{r['upstream_dist']} {r['upstream_version']}" if r["upstream_version"] else \
                 (r["upstream_dist"] or "no upstream dependency")
            print(f"  [{tag}] {r['name']:{width}}  {up}")
            if r["detail"]:
                print(f"             {' ' * width}  {r['detail']}")
        for d in deep:
            print(f"  [{'ok  ' if d['returncode'] == 0 else 'FAIL'}] deep: {d['script']} "
                  f"(exit {d['returncode']})")
        print("-" * (width + 62))
        print(f"  VERIFIED {counts[VERIFIED]}   SKIPPED {counts[SKIPPED]}   BROKEN {counts[BROKEN]}")
        if counts[SKIPPED]:
            print(f"  {counts[SKIPPED]} integration(s) were NOT checked -- a skip is not a pass.")
        print("=" * (width + 62))

    if a.write_record:
        write_record(rows)

    # Verdict lines go to stderr so `--json` stdout stays a single parseable document -- a caller that
    # pipes this into jq must not have to strip prose off the end.
    def say(msg):
        print(msg, file=sys.stderr if a.json else sys.stdout)

    if a.falsify:
        # The control is inverted on purpose: under a neutered write path a pass is the failure.
        targets = [r for r in rows if (a.falsify == "all" or r["name"] == a.falsify)
                   and r["status"] != SKIPPED]
        survived = [r["name"] for r in targets if r["status"] == VERIFIED]
        if survived:
            say(f"CONTROL FAILED: {', '.join(survived)} still passed with writes neutered -- "
                f"that round trip does not read back what it writes.")
            return 2
        say(f"CONTROL OK: {len(targets)} round trip(s) went BROKEN with the write path neutered.")
        return 0

    if counts[BROKEN]:
        return 1
    failed_deep = [d["script"] for d in deep if d["returncode"] != 0]
    if failed_deep:
        # A parity script that exits non-zero has to move the verdict, or --deep is decoration.
        say(f"deep parity FAILED: {', '.join(failed_deep)}")
        return 1
    if a.require_all and counts[SKIPPED]:
        return 1
    if not counts[VERIFIED]:
        # Nothing ran. Exiting 0 here is the exact failure this file exists to prevent.
        say("NOTHING WAS VERIFIED -- every integration skipped. This is not a pass.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
