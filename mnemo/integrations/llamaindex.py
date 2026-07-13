"""MnemoMemoryBlock — a LlamaIndex long-term `BaseMemoryBlock` backed by mnemo (current-truth recall).

LlamaIndex agents attach long-term memory as `BaseMemoryBlock`s on a `Memory` object; the short-term buffer
flushes into each block, and before a turn the blocks return relevant content to inject. A block implements
async `_aget` (return what to inject) and `_aput` (store new messages).

    from llama_index.core.memory import Memory
    from mnemo.integrations.llamaindex import MnemoMemoryBlock
    memory = Memory.from_defaults(
        session_id="s1", token_limit=40000,
        memory_blocks=[MnemoMemoryBlock(name="mnemo", path="mem.json", k=5)],
    )

The honest differentiator (same as the AutoGen block, and unlike a plain vector block): `_aget` retrieves
through mnemo's `recall()`, which hides SUPERSEDED values by default — so once a fact is corrected (via a
keyed write), the block never injects the stale value back into the prompt. For that to bite you must write
facts with a supersession `key`; plain message text is stored append-only like any block.

Subclasses BaseMemoryBlock, so importing this module imports LlamaIndex (opt-in extra); `import mnemo` stays
zero-dependency.
"""
from __future__ import annotations
from typing import Any, List, Optional
from pydantic import Field, PrivateAttr
from llama_index.core.memory import BaseMemoryBlock
from llama_index.core.base.llms.types import ChatMessage


class MnemoMemoryBlock(BaseMemoryBlock[str]):
    """A persistent long-term memory block whose recall is supersession-filtered (current-truth)."""

    name: str = Field(default="MnemoMemory")
    description: Optional[str] = Field(
        default="Long-term memory with deterministic supersession: corrected facts are not re-injected.")
    k: int = Field(default=5, description="How many memories to inject.")
    _store: Any = PrivateAttr(default=None)

    def __init__(self, path: str | None = None, store: Any = None, extractor=None, **kwargs: Any):
        super().__init__(**kwargs)                      # only pydantic fields (name/description/priority/k)
        if store is None:
            from mnemo import Mnemo
            store = Mnemo(path=path)
        self._store = store
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
                self._store.remember(t)

    async def _aget(self, messages: Optional[List[ChatMessage]] = None, **block_kwargs: Any) -> str:
        query = self._text(messages[-1]) if messages else ""
        hits = self._store.recall(query, k=self.k) if query else []
        if not hits:
            return ""
        body = "\n".join(f"- {h['text']}" for h in hits)
        return "Relevant long-term memory (current-truth, superseded values omitted):\n" + body
