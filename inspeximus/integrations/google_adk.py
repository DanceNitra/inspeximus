"""InspeximusMemoryService — a Google ADK `BaseMemoryService` backed by inspeximus.

Google ADK agents get long-term memory from a `BaseMemoryService`: the runner calls
`add_session_to_memory(session)` to ingest a finished session's events, and the agent calls
`search_memory(app_name, user_id, query)` (exposed as `tool_context.search_memory`) to retrieve. This
adapter is a drop-in replacement for `InMemoryMemoryService`, backed by a inspeximus store so memory persists and
retrieval is value-ranked lexical+semantic instead of plain word overlap.

    from google.adk.runners import Runner
    from inspeximus.integrations.google_adk import InspeximusMemoryService
    runner = Runner(agent=agent, app_name="app", session_service=..., memory_service=InspeximusMemoryService(path="mem.json"))

Two honest extras over the built-in service:
  - Current-truth retrieval: `search_memory` goes through inspeximus's `recall()`, which hides SUPERSEDED values,
    so a corrected fact (written with a supersession `key`) is not returned. Plain event text is stored
    append-only like any service; the filtering bites when you key your facts.
  - Right-to-erasure per user: `forget_subject_for(app_name, user_id, request_id=…)` hard-deletes a user's
    memories across sessions and leaves a signed deletion tombstone (GDPR-style, provable). No built-in ADK
    service offers that.

Subclasses BaseMemoryService, so importing this module imports google-adk (opt-in extra); `import inspeximus`
stays zero-dependency.
"""
from __future__ import annotations
import hashlib
from typing import Any

from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types as _gt


_UNKNOWN_SESSION = "_unknown_session"


def _subject(app_name: str, user_id: str) -> str:
    return f"adk::{app_name}::{user_id}"


class InspeximusMemoryService(BaseMemoryService):
    """ADK BaseMemoryService over a inspeximus store (persistent, current-truth recall, per-user erasure)."""

    def __init__(self, path: str | None = None, store: Any = None, k: int = 10, extractor=None):
        if store is None:
            from inspeximus import Inspeximus
            store = Inspeximus(path=path)
        self.store = store
        self.k = int(k)
        # OPT-IN extractor (text -> (key, object)): auto-keys ingested event text so search_memory returns
        # current-truth (a corrected fact stops surfacing) without keying each write. See Inspeximus.extractor.
        if extractor is not None:
            self.store.extractor = extractor
        self._seen: set[str] | None = None

    # ── ingestion ──
    def _seen_events(self) -> set[str]:
        """Event identities already ingested, recovered from the store so it survives a restart.

        `add_session_to_memory` is documented as callable "multiple times during its lifetime", and the
        runner does exactly that, so ingestion has to be idempotent per event or a long session is stored
        once per turn. Built lazily and then kept in memory: the scan is O(records) and only pays once.
        """
        if self._seen is None:
            self._seen = {k for k in ((r.get("meta") or {}).get("adk_event") for r in self.store.items) if k}
        return self._seen

    def _ingest(self, app_name: str, user_id: str, session_id: str | None, event, fallback_id: str) -> None:
        content = getattr(event, "content", None)
        if not content or not getattr(content, "parts", None):
            return
        text = " ".join(p.text for p in content.parts if getattr(p, "text", None))
        if not text.strip():
            return
        # Prefer the event's own id; the caller supplies the fallback, because what makes two writes "the
        # same" differs: session events are identified by their position, a direct memory write by its text.
        eid = getattr(event, "id", None) or fallback_id
        ekey = f"{app_name}/{user_id}/{eid}"
        seen = self._seen_events()
        if ekey in seen:
            return
        self.store.remember(text, source={"doc": _subject(app_name, user_id)},
                            meta={"adk_app": app_name, "adk_user": user_id,
                                  "adk_author": getattr(event, "author", None),
                                  "adk_role": getattr(content, "role", None),
                                  "adk_session": session_id, "adk_event": ekey})
        seen.add(ekey)

    async def add_session_to_memory(self, session) -> None:
        for i, event in enumerate(session.events):
            self._ingest(session.app_name, session.user_id, session.id, event, f"{session.id}#{i}")

    async def add_events_to_memory(self, *, app_name: str, user_id: str, events,
                                   session_id: str | None = None, custom_metadata=None) -> None:
        """Incremental ingestion: `events` is a delta, never assumed to be the whole session."""
        _ = custom_metadata
        sid = session_id or _UNKNOWN_SESSION
        for i, event in enumerate(events):
            self._ingest(app_name, user_id, session_id, event, f"{sid}#{i}")

    async def add_memory(self, *, app_name: str, user_id: str, memories, custom_metadata=None) -> None:
        """Direct writes of already-formed memories, bypassing event extraction.

        A `MemoryEntry` carries no position in a conversation, so identity falls back to the text itself:
        writing the same memory twice stores it once.
        """
        _ = custom_metadata
        for m in memories:
            content = getattr(m, "content", None)
            parts = getattr(content, "parts", None) or []
            text = " ".join(p.text for p in parts if getattr(p, "text", None))
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            self._ingest(app_name, user_id, None, m, f"mem:{digest}")

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

    # ── the `adk web --memory_service_uri=` route ──
    @classmethod
    def from_uri(cls, uri: str, **kwargs) -> "InspeximusMemoryService":
        """Build the service from `inspeximus://<path>`, so the ADK CLI can construct it.

        `urlparse` splits a path the way a URL is split, not the way a filesystem is: on Windows
        `inspeximus://C:/x/mem.json` puts "C:" in netloc, and a POSIX absolute path lands entirely in
        path with the leading slash intact. Both have to rejoin into the path the user typed.
        """
        from urllib.parse import urlparse
        u = urlparse(uri)
        path = (u.netloc + u.path) if u.netloc else u.path
        return cls(path=path or None, **kwargs)

    # ── governance bonus (inspeximus-specific) ──
    def forget_subject_for(self, app_name: str, user_id: str, request_id: str | None = None) -> dict:
        """Right-to-erasure for one ADK user: hard-delete their memories across sessions and leave a signed,
        content-free deletion tombstone (verify_writes stays intact). Needs receipts enabled on the store for
        the signature; works either way for the erasure itself."""
        return self.store.forget_subject(_subject(app_name, user_id), request_id=request_id)


def register(scheme: str = "inspeximus") -> None:
    """Register the `inspeximus://` scheme with ADK's service registry.

    Call it from the `services.py` that `adk web` loads, and `--memory_service_uri=inspeximus://mem.json`
    then works without any Python glue.
    """
    from google.adk.cli.service_registry import get_service_registry
    get_service_registry().register_memory_service(scheme, InspeximusMemoryService.from_uri)
