"""MnemoMemory — an AutoGen (autogen-agentchat) `Memory` backed by mnemo.

AutoGen agents accept any object implementing the `Memory` protocol (add / query / update_context / clear /
close) via `AssistantAgent(..., memory=[m])`. Unlike a verbatim conversation `Session`, AutoGen `Memory` is
a FACT/context store: before each turn, `update_context` retrieves relevant memories and injects them as a
SystemMessage. That is exactly where mnemo's value is REAL, not incidental — `recall()` hides superseded /
poisoned facts by default, so the agent is grounded on CURRENT-TRUTH context, not on a stale value a later
correction already retired.

    from autogen_agentchat.agents import AssistantAgent
    from inspeximus.integrations.autogen import MnemoMemory
    mem = MnemoMemory(path="mem.json")
    agent = AssistantAgent("assistant", model_client=..., memory=[mem])

Zero-dependency core: `import inspeximus` never imports AutoGen. The AutoGen types are imported LAZILY inside the
methods, so `pip install agora-mnemo` alone is enough — you only need AutoGen installed to actually USE the
adapter (which you already have, since you're wiring it into an AutoGen agent).

KEYED SUPERSESSION (the differentiator): pass a stable `key` in a memory's metadata to make later writes for
the same key RETIRE the older value deterministically — e.g. `MemoryContent(content="tz is UTC",
metadata={"key": "user::timezone", "object": "UTC"})`. A later `key="user::timezone", object="PST"` supersedes
it, and `query`/`update_context` then surface only PST. Without a key, entries are plain appended facts.
"""
from __future__ import annotations
from typing import Any


class MnemoMemory:
    """AutoGen `Memory` backed by a mnemo store; injects supersession-filtered current-truth context."""

    def __init__(self, path: str | None = None, store: Any = None, k: int = 5, source: str | None = None,
                 extractor=None):
        if store is None:
            from inspeximus import Mnemo
            store = Mnemo(path=path)
        self.store = store
        self.k = int(k)
        self._source = source   # optional canonical source tag (enables forget_subject on this memory's writes)
        # OPT-IN: plug a text -> (key, object) extractor so free-text messages auto-key and the current-truth
        # (supersession-filtered) recall fires without the caller keying each add(). See Mnemo.extractor.
        if extractor is not None:
            self.store.extractor = extractor

    async def add(self, content: Any, cancellation_token: Any = None) -> None:
        """Store one MemoryContent. metadata may carry {key, object, source} to drive keyed supersession."""
        md = dict(getattr(content, "metadata", None) or {})
        text = getattr(content, "content", None)
        text = text if isinstance(text, str) else str(text)
        src = md.get("source") or self._source
        self.store.remember(text, key=md.get("key"), object=md.get("object"),
                            source=({"doc": src} if src else None),
                            meta={"mime_type": str(getattr(content, "mime_type", "text/plain"))})

    async def query(self, query: Any, cancellation_token: Any = None, **kwargs) -> Any:
        """Retrieve current-truth memories relevant to `query` (superseded values are hidden by recall)."""
        from autogen_core.memory import MemoryContent, MemoryMimeType, MemoryQueryResult
        q = getattr(query, "content", query)
        q = q if isinstance(q, str) else str(q)
        hits = self.store.recall(q, k=self.k)
        results = [MemoryContent(content=h["text"], mime_type=MemoryMimeType.TEXT,
                                 metadata={"id": h["id"]}) for h in hits]
        return MemoryQueryResult(results=results)

    async def update_context(self, model_context: Any) -> Any:
        """Inject current-truth memories relevant to the LAST message as a SystemMessage (AutoGen contract)."""
        from autogen_core.memory import MemoryQueryResult, UpdateContextResult
        from autogen_core.models import SystemMessage
        msgs = await model_context.get_messages()
        last = ""
        if msgs:
            c = getattr(msgs[-1], "content", "")
            last = c if isinstance(c, str) else str(c)
        qr = await self.query(last)
        if qr.results:
            block = "\n".join(f"- {m.content}" for m in qr.results)
            await model_context.add_message(
                SystemMessage(content="Relevant memory (current-truth; superseded values omitted):\n" + block))
        return UpdateContextResult(memories=qr)

    async def clear(self) -> None:
        ids = [r["id"] for r in self.store.items if r.get("status") == "active"]
        if ids:
            self.store.forget(ids=ids)

    async def close(self) -> None:
        return None
