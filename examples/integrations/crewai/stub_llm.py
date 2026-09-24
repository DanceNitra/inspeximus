"""A scripted CrewAI LLM and a hashing embedder: no network, no API key, the same output every run.

CrewAI calls its LLM for two different jobs, and the stub answers both:

  * the AGENT turn (ReAct text: `Action:` / `Action Input:` / `Final Answer:`), and
  * the MEMORY pipeline, which asks the LLM to extract memories from a task result, to pick a scope
    and categories for each one, to analyse a recall query, and to plan a consolidation. Those four
    are told apart by CrewAI's own system prompts, read from CrewAI's translation file rather than
    copied here, so a CrewAI release that rewords them makes the stub fail loudly instead of
    answering the wrong question.

The agent's decision is not canned. It reads the refund limit out of the "Relevant memories" block
CrewAI put into the prompt, and refunds at most that much; with no limit in memory it refunds
nothing. That is what makes "what the tool call was based on" a real question in this example: change
the memory and the tool call changes with it.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from crewai.llms.base_llm import BaseLLM
from crewai.utilities.i18n import I18N_DEFAULT

#: CrewAI's memory prompts, keyed by what the stub answers for them.
_MEMORY_PROMPTS = {
    "extract": I18N_DEFAULT.memory("extract_memories_system"),
    "save": I18N_DEFAULT.memory("save_system"),
    "query": I18N_DEFAULT.memory("query_system"),
    "consolidate": I18N_DEFAULT.memory("consolidation_system"),
}

_LIMIT = re.compile(r"refund(?:s)? (?:of )?up to (\d+(?:\.\d+)?) EUR", re.IGNORECASE)
_DAMAGED = re.compile(r"damaged item (?:is|costs|priced at) (\d+(?:\.\d+)?) EUR", re.IGNORECASE)


def _text(messages: Any) -> list[dict]:
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    out = []
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, list):          # multimodal parts: keep the text ones
            c = "\n".join(p.get("text", "") for p in c if isinstance(p, dict))
        out.append({"role": m.get("role"), "content": c or ""})
    return out


class StubLLM(BaseLLM):
    """Deterministic stand-in for a chat model. `calls` counts what it was asked, by kind."""

    llm_type: str = "stub"
    calls: dict = {}

    def supports_function_calling(self) -> bool:
        # False on purpose: CrewAI then drives the agent through its ReAct text loop and asks the
        # memory pipeline for plain JSON, both of which a stub can answer exactly.
        return False

    def supports_stop_words(self) -> bool:
        return True

    def get_context_window_size(self) -> int:
        return 32000

    def _count(self, kind: str) -> None:
        self.calls = {**self.calls, kind: self.calls.get(kind, 0) + 1}

    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None) -> str:
        msgs = _text(messages)
        system = next((m["content"] for m in msgs if m["role"] == "system"), "")
        for kind, prompt in _MEMORY_PROMPTS.items():
            if system.strip() == prompt.strip():
                self._count(kind)
                return json.dumps(getattr(self, "_" + kind)(msgs))
        self._count("agent")
        return self._agent_turn(msgs)

    # ── the memory pipeline ─────────────────────────────────────────────────────────────────────
    @staticmethod
    def _extract(msgs):
        body = msgs[-1]["content"]
        result = body.split("Result:", 1)[1] if "Result:" in body else body
        result = result.split("Extract memory statements", 1)[0]
        result = result.split("Final Answer:", 1)[-1].strip()
        return {"memories": [result] if result else []}

    @staticmethod
    def _save(msgs):
        body = msgs[-1]["content"].split("Existing scopes:", 1)[0].lower()
        cat = "policy" if "policy" in body else ("orders" if "order" in body else "notes")
        return {"suggested_scope": "/support", "categories": [cat], "importance": 0.7,
                "extracted_metadata": {"entities": [], "dates": [], "topics": [cat]}}

    @staticmethod
    def _query(msgs):
        q = msgs[-1]["content"].split("Query:", 1)[-1].split("Available scopes:", 1)[0].strip()
        return {"keywords": [], "suggested_scopes": [], "complexity": "simple",
                "recall_queries": [q], "time_filter": None}

    @staticmethod
    def _consolidate(msgs):
        return {"actions": [], "insert_new": True, "insert_reason": "stub: keep every record"}

    # ── the agent ───────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _agent_turn(msgs) -> str:
        prompt = "\n".join(m["content"] for m in msgs)
        # How far along is this task? CrewAI appends each tool result to the assistant turn that asked
        # for it, as "Observation: ...", so the count of those turns is the count of tools already run.
        seen = sum(1 for m in msgs if m["role"] == "assistant" and "\nObservation:" in m["content"])
        limit = _LIMIT.search(prompt)
        if seen <= 0:
            return ("Thought: I need the order before I decide anything.\n"
                    "Action: lookup_order\n"
                    'Action Input: {"order_id": "4711"}')
        damaged = _DAMAGED.search(prompt)
        if seen == 1 and limit and damaged:
            amount = min(float(damaged.group(1)), float(limit.group(1)))
            return ("Thought: The damaged item is within the refund limit I remember.\n"
                    "Action: issue_refund\n"
                    'Action Input: {"order_id": "4711", "amount_eur": %s, "reason": "damaged item"}'
                    % json.dumps(amount))
        if seen >= 2:
            return ("Thought: I now know the final answer\n"
                    "Final Answer: Refund issued for the damaged item on order 4711, within the "
                    "refund limit held in memory.")
        return ("Thought: I now know the final answer\n"
                "Final Answer: No refund limit is in memory, so I escalate order 4711 to a manager.")


def stub_embedder(texts: list[str], dims: int = 64) -> list[list[float]]:
    """Bag-of-words hashed into `dims` buckets, L2-normalised. Deterministic and good enough for
    two texts that share words to score above two that do not."""
    out = []
    for t in texts:
        v = [0.0] * dims
        for w in re.findall(r"[a-z0-9]+", (t or "").lower()):
            h = int.from_bytes(hashlib.sha256(w.encode()).digest()[:4], "big")
            v[h % dims] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out
