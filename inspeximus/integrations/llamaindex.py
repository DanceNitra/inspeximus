"""InspeximusMemoryBlock — a LlamaIndex long-term `BaseMemoryBlock` backed by inspeximus (current-truth recall).

LlamaIndex agents attach long-term memory as `BaseMemoryBlock`s on a `Memory` object; the short-term buffer
flushes into each block, and before a turn the blocks return relevant content to inject. A block implements
async `_aget` (return what to inject) and `_aput` (store new messages).

    from llama_index.core.memory import Memory
    from inspeximus.integrations.llamaindex import InspeximusMemoryBlock
    memory = Memory.from_defaults(
        session_id="s1", token_limit=40000,
        memory_blocks=[InspeximusMemoryBlock(name="inspeximus", path="mem.json", k=5)],
    )

The honest differentiator (same as the AutoGen block, and unlike a plain vector block): `_aget` retrieves
through inspeximus's `recall()`, which hides SUPERSEDED values by default — so once a fact is corrected (via a
keyed write), the block never injects the stale value back into the prompt. For that to bite you must write
facts with a supersession `key`; plain message text is stored append-only like any block.

Subclasses BaseMemoryBlock, so importing this module imports LlamaIndex (opt-in extra); `import inspeximus` stays
zero-dependency.
"""
from __future__ import annotations
from typing import Any, List, Optional

try:                       # pydantic is pulled in BY llama-index-core, so a bare ImportError here named the
    from pydantic import Field, PrivateAttr          # wrong package: the user installed pydantic, then hit
    from llama_index.core.memory import BaseMemoryBlock              # the next missing import.
    from llama_index.core.base.llms.types import ChatMessage
except ImportError as e:
    raise ImportError('the inspeximus LlamaIndex adapter needs LlamaIndex: '
                      'pip install "inspeximus[llamaindex]"') from e

from .governance import ComplianceMixin


class InspeximusMemoryBlock(BaseMemoryBlock[str], ComplianceMixin):
    """A persistent long-term memory block whose recall is supersession-filtered (current-truth).

    Mixes in `ComplianceMixin`: the same memory block yields the EU AI Act evidence (compliance_report /
    compliance_check / retention / audit_bundle) with no extra wiring. Enable `receipts=True` on the store.
    The mixin carries no annotations, so it cannot shadow this class's `store` property."""

    name: str = Field(default="InspeximusMemory")
    description: Optional[str] = Field(
        default="Long-term memory with deterministic supersession: corrected facts are not re-injected.")
    k: int = Field(default=5, description="How many memories to inject.")
    _store: Any = PrivateAttr(default=None)
    _subject: str = PrivateAttr(default="llamaindex")

    def __init__(self, path: str | None = None, store: Any = None, extractor=None,
                 subject: str = "llamaindex", **kwargs: Any):
        super().__init__(**kwargs)                      # only pydantic fields (name/description/priority/k)
        if store is None:
            from inspeximus import Inspeximus
            from .._surface import open_store
            store = open_store(path, resolve=False)
        self._store = store
        # The subject these messages are attributable to, so forget_subject() can erase them.
        # Pass a per-user or per-session value; the default groups every message under one id.
        self._subject = str(subject)
        # OPT-IN extractor (text -> (key, object)): auto-keys _aput'd messages so _aget injects current-truth.
        if extractor is not None:
            self._store.extractor = extractor

    @property
    def store(self):
        return self._store

    @staticmethod
    def _text(msg: ChatMessage) -> str:
        c = getattr(msg, "content", None)
        return c if isinstance(c, str) else (str(c) if c is not None else "")

    async def _aput(self, messages: List[ChatMessage]) -> None:
        for m in messages:
            t = self._text(m).strip()
            if t:
                # source= is what makes this message erasable by subject; see the langgraph adapter for
                # why the separator is "::" and not "/" (a path-shaped subject collides by construction).
                self._store.remember(t, source={"doc": "llamaindex::" + self._subject})

    async def _aget(self, messages: Optional[List[ChatMessage]] = None, **block_kwargs: Any) -> str:
        query = self._text(messages[-1]) if messages else ""
        hits = self._store.recall(query, k=self.k) if query else []
        if not hits:
            return ""
        body = "\n".join(f"- {h['text']}" for h in hits)
        return "Relevant long-term memory (current-truth, superseded values omitted):\n" + body
