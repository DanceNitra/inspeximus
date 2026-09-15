"""LangChain integration for inspeximus — a supersession-filtered retriever and a chat-message history.

Two opt-in classes (importing this module imports langchain-core; `import inspeximus` stays zero-dependency):

    InspeximusRetriever          — a langchain_core BaseRetriever whose results come from inspeximus.recall(), so
                              SUPERSEDED facts are hidden by default: once a fact is corrected via a keyed
                              write, the retriever never returns the stale value into your chain/prompt.
    InspeximusChatMessageHistory — a BaseChatMessageHistory that persists a conversation in a inspeximus store
                              (per-session subject) with the same current-truth recall available.

    from inspeximus.integrations.langchain import InspeximusRetriever
    r = InspeximusRetriever(path="mem.json", k=5)
    r.store.remember("the deploy channel is BLUE-9", key="deploy-channel")   # keyed write -> supersedable
    docs = r.invoke("what is the deploy channel?")     # returns current value, never a superseded one

For semantic recall pass an embedder to the underlying store: InspeximusRetriever(embed=my_embed_fn). Without one,
recall is lexical (zero-dependency fallback).
"""
from __future__ import annotations
from typing import Any, List, Optional

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, message_to_dict, messages_from_dict

from inspeximus import Inspeximus

from .governance import ComplianceMixin
from .._surface import open_store          # one surface posture; see _surface.py


class InspeximusRetriever(BaseRetriever, ComplianceMixin):
    """A LangChain retriever backed by inspeximus. Its differentiator vs a plain vector retriever: recall() hides
    superseded values, so a corrected fact is never retrieved back into the prompt (write facts with a
    supersession `key=` for that to engage; plain text is stored append-only).

    Mixes in `ComplianceMixin`: the same retriever yields the EU AI Act evidence (compliance_report /
    compliance_check / retention / audit_bundle) with no extra wiring. Enable `receipts=True` on the store."""

    k: int = 5
    store: Any = None

    def __init__(self, path: str | None = None, store: Any = None, k: int = 5,
                 embed=None, extractor=None, **kwargs: Any):
        super().__init__(k=k, store=store if store is not None else open_store(path, embed=embed, resolve=False), **kwargs)
        if extractor is not None:
            self.store.extractor = extractor

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> List[Document]:
        self.store.refresh()                      # see what a peer process wrote (2.28.0)
        hits = self.store.recall(query, k=self.k) or []
        return [Document(page_content=h.get("text", ""),
                         metadata={"id": h.get("id"), "key": h.get("key"), **(h.get("meta") or {})})
                for h in hits]

    # convenience: write a (supersedable) fact straight through the retriever
    def add(self, text: str, key: Optional[str] = None, **kw: Any) -> None:
        # source= scopes this message to its session so forget_subject(session) can erase it (see crewai).
        kw.setdefault("source", {"doc": "lc::" + str(getattr(self, "_tag", None) or "retriever")})
        self.store.remember(text, key=key, **kw)


class InspeximusChatMessageHistory(BaseChatMessageHistory, ComplianceMixin):
    """A conversation history persisted in a inspeximus store, scoped per session_id. Messages are appended;
    current-truth recall over the same store is available via `.store.recall(...)`.

    Mixes in `ComplianceMixin`: the same history object yields the EU AI Act evidence (compliance_report /
    compliance_check / retention / audit_bundle) with no extra wiring. Enable `receipts=True` on the store."""

    def __init__(self, session_id: str, path: str | None = None, store: Any = None, embed=None):
        self.session_id = session_id
        self.store = store if store is not None else open_store(path, embed=embed, resolve=False)
        self._tag = f"lc-chat:{session_id}"

    @property
    def messages(self) -> List[BaseMessage]:
        self.store.refresh()                      # see what a peer process wrote (2.28.0)
        rows = self.store.recall(self._tag, k=1000, where={"tags": {"$contains": self._tag}}) \
            if False else [r for r in getattr(self.store, "items", []) if self._tag in (r.get("tags") or [])]
        rows = sorted(rows, key=lambda r: r.get("ts", 0))
        import json as _json
        out = []
        for r in rows:
            try:
                out.extend(messages_from_dict([_json.loads(r["text"])]))
            except Exception:
                pass
        return out

    def add_message(self, message: BaseMessage) -> None:
        import json as _json
        # source= scopes the message to its session, so forget_subject(session tag) erases it.
        self.store.remember(_json.dumps(message_to_dict(message)), tags=[self._tag],
                            source={"doc": "lc::" + str(self._tag)})

    def clear(self) -> None:
        """Erase this session's messages — for real.

        This used to set `status="deleted"` on the in-memory records and stop there: no forget(), no
        tombstone, no save. `.messages` filters by TAG and never looked at status, so the history still
        returned them; and a reload brought every record back `active` with the content still on disk. A
        user-visible "clear" that leaves the data is the wrong way for this to fail."""
        ids = [r["id"] for r in list(getattr(self.store, "items", []))
               if self._tag in (r.get("tags") or [])]
        if ids:
            self.store.forget(ids=ids, basis="chat_history_clear")


# ---------------------------------------------------------------------------------------------------
# The action ledger as a LangChain callback: every tool call, model call and chain error becomes one
# signed entry in <store>.actions.json, carrying the store's state digest and the ids the last recall
# returned. LangChain closed two requests for a compliance callback (#35357, #35691) as not planned;
# this is that handler, on top of inspeximus's own chain rather than a new one.
from langchain_core.callbacks import BaseCallbackHandler  # noqa: E402


class InspeximusActionCallback(BaseCallbackHandler):
    """Record LangChain tool and LLM calls into an inspeximus ActionLedger.

        from inspeximus import Inspeximus
        from inspeximus.actions import ActionLedger
        from inspeximus.integrations.langchain import InspeximusActionCallback

        m = Inspeximus("memory.json", receipts=True)
        cb = InspeximusActionCallback(ActionLedger(m, actor="support-agent"))
        agent.invoke(inputs, config={"callbacks": [cb]})

    Content-free by default (arguments and outputs are digested). Each run id is one entry; a start
    without an end is recorded at the next end or error, so an interrupted run still leaves a row."""

    def __init__(self, ledger, record_llm: bool = True, record_tools: bool = True):
        self.ledger = ledger
        self.record_llm = record_llm
        self.record_tools = record_tools
        self._open: dict = {}

    # tools
    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        if self.record_tools:
            name = (serialized or {}).get("name") or "tool"
            self._open[str(run_id)] = ("tool:" + str(name), input_str, __import__("time").time())

    def on_tool_end(self, output, *, run_id, **kwargs):
        self._close(run_id, output, "ok", None)

    def on_tool_error(self, error, *, run_id, **kwargs):
        self._close(run_id, None, "error", error)

    # models
    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        if self.record_llm:
            name = (serialized or {}).get("name") or "llm"
            self._open[str(run_id)] = ("llm:" + str(name), list(prompts), __import__("time").time())

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        if self.record_llm:
            name = (serialized or {}).get("name") or "chat_model"
            flat = [[getattr(x, "content", str(x)) for x in batch] for batch in messages]
            self._open[str(run_id)] = ("llm:" + str(name), flat, __import__("time").time())

    def on_llm_end(self, response, *, run_id, **kwargs):
        try:
            out = [[g.text for g in batch] for batch in response.generations]
        except Exception:
            out = str(response)
        self._close(run_id, out, "ok", None)

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._close(run_id, None, "error", error)

    # chains: only errors are recorded, a chain start is not an action
    def on_chain_error(self, error, *, run_id, **kwargs):
        self.ledger.record("chain:error", status="error", error=f"{type(error).__name__}: {error}")

    def _close(self, run_id, output, status, error):
        opened = self._open.pop(str(run_id), None)
        if opened is None:
            return
        action, inputs, started = opened
        self.ledger.record(action, inputs=inputs, output=output, status=status,
                           error=None if error is None else f"{type(error).__name__}: {error}",
                           started=started)
