"""MnemoMemoryService — a Google ADK `BaseMemoryService` backed by mnemo.

Google ADK agents get long-term memory from a `BaseMemoryService`: the runner calls
`add_session_to_memory(session)` to ingest a finished session's events, and the agent calls
`search_memory(app_name, user_id, query)` (exposed as `tool_context.search_memory`) to retrieve. This
adapter is a drop-in replacement for `InMemoryMemoryService`, backed by a mnemo store so memory persists and
retrieval is value-ranked lexical+semantic instead of plain word overlap.

    from google.adk.runners import Runner
    from mnemo.integrations.google_adk import MnemoMemoryService
    runner = Runner(agent=agent, app_name="app", session_service=..., memory_service=MnemoMemoryService(path="mem.json"))

Two honest extras over the built-in service:
  - Current-truth retrieval: `search_memory` goes through mnemo's `recall()`, which hides SUPERSEDED values,
    so a corrected fact (written with a supersession `key`) is not returned. Plain event text is stored
    append-only like any service; the filtering bites when you key your facts.
  - Right-to-erasure per user: `forget_subject_for(app_name, user_id, request_id=…)` hard-deletes a user's
    memories across sessions and leaves a signed deletion tombstone (GDPR-style, provable). No built-in ADK
    service offers that.

Subclasses BaseMemoryService, so importing this module imports google-adk (opt-in extra); `import mnemo`
stays zero-dependency.
"""
from __future__ import annotations
from typing import Any

from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types as _gt


def _subject(app_name: str, user_id: str) -> str:
    return f"adk::{app_name}::{user_id}"


class MnemoMemoryService(BaseMemoryService):
    """ADK BaseMemoryService over a mnemo store (persistent, current-truth recall, per-user erasure)."""

    def __init__(self, path: str | None = None, store: Any = None, k: int = 10, extractor=None):
        if store is None:
            from mnemo import Mnemo
            store = Mnemo(path=path)
        self.store = store
        self.k = int(k)
        # OPT-IN extractor (text -> (key, object)): auto-keys ingested event text so search_memory returns
        # current-truth (a corrected fact stops surfacing) without keying each write. See Mnemo.extractor.
        if extractor is not None:
            self.store.extractor = extractor

    async def add_session_to_memory(self, session) -> None:
        subj = _subject(session.app_name, session.user_id)
        for ev in session.events:
            content = getattr(ev, "content", None)
            if not content or not getattr(content, "parts", None):
                continue
            text = " ".join(p.text for p in content.parts if getattr(p, "text", None))
            if not text.strip():
                continue
            self.store.remember(text, source={"doc": subj},
                                meta={"adk_app": session.app_name, "adk_user": session.user_id,
                                      "adk_author": getattr(ev, "author", None),
                                      "adk_role": getattr(content, "role", None),
                                      "adk_session": session.id})

    async def search_memory(self, *, app_name: str, user_id: str, query: str) -> SearchMemoryResponse:
        resp = SearchMemoryResponse()
        if not query:
            return resp
        by_id = {r["id"]: r for r in self.store.items}      # recall() hits omit meta; look up the full record
        n = 0
        for h in self.store.recall(query, k=self.k + 20):
            rec = by_id.get(h["id"])
            m = (rec.get("meta") or {}) if rec else {}
            if m.get("adk_app") != app_name or m.get("adk_user") != user_id:
                continue
            resp.memories.append(MemoryEntry(
                content=_gt.Content(role=m.get("adk_role") or "user",
                                    parts=[_gt.Part(text=h["text"])]),
                author=m.get("adk_author"), timestamp=None))
            n += 1
            if n >= self.k:
                break
        return resp

    # ── governance bonus (mnemo-specific) ──
    def forget_subject_for(self, app_name: str, user_id: str, request_id: str | None = None) -> dict:
        """Right-to-erasure for one ADK user: hard-delete their memories across sessions and leave a signed,
        content-free deletion tombstone (verify_writes stays intact). Needs receipts enabled on the store for
        the signature; works either way for the erasure itself."""
        return self.store.forget_subject(_subject(app_name, user_id), request_id=request_id)
