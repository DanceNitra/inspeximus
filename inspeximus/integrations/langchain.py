"""LangChain integration for mnemo — a supersession-filtered retriever and a chat-message history.

Two opt-in classes (importing this module imports langchain-core; `import inspeximus` stays zero-dependency):

    MnemoRetriever          — a langchain_core BaseRetriever whose results come from inspeximus.recall(), so
                              SUPERSEDED facts are hidden by default: once a fact is corrected via a keyed
                              write, the retriever never returns the stale value into your chain/prompt.
    MnemoChatMessageHistory — a BaseChatMessageHistory that persists a conversation in a mnemo store
                              (per-session subject) with the same current-truth recall available.

    from inspeximus.integrations.langchain import MnemoRetriever
    r = MnemoRetriever(path="mem.json", k=5)
    r.store.remember("the deploy channel is BLUE-9", key="deploy-channel")   # keyed write -> supersedable
    docs = r.invoke("what is the deploy channel?")     # returns current value, never a superseded one

For semantic recall pass an embedder to the underlying store: MnemoRetriever(embed=my_embed_fn). Without one,
recall is lexical (zero-dependency fallback).
"""
from __future__ import annotations
from typing import Any, List, Optional

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, message_to_dict, messages_from_dict

from inspeximus import Mnemo


class MnemoRetriever(BaseRetriever):
    """A LangChain retriever backed by mnemo. Its differentiator vs a plain vector retriever: recall() hides
    superseded values, so a corrected fact is never retrieved back into the prompt (write facts with a
    supersession `key=` for that to engage; plain text is stored append-only)."""

    k: int = 5
    store: Any = None

    def __init__(self, path: str | None = None, store: Any = None, k: int = 5,
                 embed=None, extractor=None, **kwargs: Any):
        super().__init__(k=k, store=store if store is not None else Mnemo(path=path, embed=embed), **kwargs)
        if extractor is not None:
            self.store.extractor = extractor

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> List[Document]:
        hits = self.store.recall(query, k=self.k) or []
        return [Document(page_content=h.get("text", ""),
                         metadata={"id": h.get("id"), "key": h.get("key"), **(h.get("meta") or {})})
                for h in hits]

    # convenience: write a (supersedable) fact straight through the retriever
    def add(self, text: str, key: Optional[str] = None, **kw: Any) -> None:
        self.store.remember(text, key=key, **kw)


class MnemoChatMessageHistory(BaseChatMessageHistory):
    """A conversation history persisted in a mnemo store, scoped per session_id. Messages are appended;
    current-truth recall over the same store is available via `.store.recall(...)`."""

    def __init__(self, session_id: str, path: str | None = None, store: Any = None, embed=None):
        self.session_id = session_id
        self.store = store if store is not None else Mnemo(path=path, embed=embed)
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
        self.store.remember(_json.dumps(message_to_dict(message)), tags=[self._tag])

    def clear(self) -> None:
        for r in list(getattr(self.store, "items", [])):
            if self._tag in (r.get("tags") or []):
                r["status"] = "deleted"
