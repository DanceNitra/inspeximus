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
