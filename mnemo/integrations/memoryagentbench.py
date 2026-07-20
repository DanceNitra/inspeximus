"""MemoryAgentBench (ICLR 2026, HUST-AI-HYZ) integration for mnemo.

MemoryAgentBench evaluates a memory system by driving it through incremental multi-turn interactions and, per
query, retrieving from memory and letting a fixed LLM answerer produce the answer (scored by exact/substring
match). Its `AgentWrapper` (agent.py) already has a mem0 path that calls `self.memory.add(messages, user_id)`
to ingest and `self.memory.search(query, user_id, limit)` to retrieve. `MnemoMABMemory` exposes that SAME
interface backed by mnemo, so mnemo drops into the mem0 code path and is compared APPLES-TO-APPLES (identical
answerer; only the memory backend differs).

The point of running mnemo here is the **Conflict Resolution** competency (fact_mh / fact_sh): the context
updates facts, and the question asks for the CURRENT value. mnemo answers it with DETERMINISTIC supersession —
a keyed write retires the old value with no LLM and no similarity threshold — so `search` returns current-truth
and the retired value doesn't resurface. An optional regex extractor keys free-text facts automatically so
supersession fires without the benchmark supplying keys.

    # in MemoryAgentBench/agent.py:
    #   elif self._is_agent_type("mnemo"):
    #       from mnemo.integrations.memoryagentbench import MnemoMABMemory
    #       self.memory = MnemoMABMemory()          # then reuse the mem0 handler unchanged
    #       self.retrieve_num = agent_config['retrieve_num']; self.client = self._create_oai_client()

Zero-dependency: matches the mem0 interface structurally, never imports mem0 or MemoryAgentBench.
"""
from __future__ import annotations
from typing import Any, Dict, List


def _text_of(messages) -> str:
    """MAB passes mem0 a [{'role','content'}, ...] list; the fact/context lives in the user turn(s)."""
    if isinstance(messages, str):
        return messages
    parts = []
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                parts.append(c)
    return "\n".join(parts) if parts else str(messages)


class MnemoMABMemory:
    """A mem0.Memory-compatible shim backed by mnemo (add / search), scoped per user_id via mnemo tenants.

    add(messages, user_id)                -> ingest (deterministic supersession via the extractor)
    search(query, user_id, limit)         -> {"results": [{"memory": <text>}, ...]}  (current-truth first)
    """

    def __init__(self, path: str | None = None, embed=None, use_extractor: bool = True):
        from mnemo import Mnemo
        self._Mnemo = Mnemo
        self._path = path
        self._embed = embed
        self._use_extractor = use_extractor
        self._stores: Dict[str, Any] = {}

    def _store(self, user_id: str):
        uid = user_id or "default"
        m = self._stores.get(uid)
        if m is None:
            # hard per-tenant isolation so one context's facts never leak into another's retrieval
            m = self._Mnemo(path=None, embed=self._embed, tenant=uid)
            m.echo_guard = True
            if self._use_extractor:
                try:
                    from mnemo import regex_extractor
                    m.extractor = regex_extractor
                except Exception:
                    pass
            self._stores[uid] = m
        return m

    def add(self, messages, user_id: str = "default", **_: Any):
        m = self._store(user_id)
        text = _text_of(messages).strip()
        # Each line is a candidate fact; a keyed (subject,relation) write supersedes the stale value.
        for line in [ln.strip() for ln in text.split("\n") if ln.strip()]:
            try:
                m.remember(line)
            except Exception:
                pass
        return {"results": []}

    def search(self, query: str, user_id: str = "default", limit: int = 10, **_: Any) -> Dict[str, List[dict]]:
        m = self._store(user_id)
        hits = m.recall(query, k=limit) or []
        return {"results": [{"memory": h.get("text", "")} for h in hits]}

    # convenience for a non-mem0 wiring / tests
    def reset(self):
        self._stores.clear()
