"""A scripted agno model: no API key, no network, the same tool calls on every run.

It stands in for a real model so the example and its tests run offline. It is not a mock of the
ledger path: agno parses its tool calls, runs the tools through the agent's tool hooks and feeds the
results back, exactly as it does for OpenAIChat or Claude. Only the decision of what to call is
scripted:

    1. after the user's message:        recall_memory(query=<the user's message>)
    2. after recall_memory's result:    run_migration(database=<the host the recalled fact names>)
    3. after run_migration's result:    a final answer

The database argument in step 2 is read out of the recall result, so the action really does depend
on what memory returned. Swap in any agno model to run the same agent against a real one.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterator, List

from agno.models.base import Model
from agno.models.response import ModelResponse

_HOST = re.compile(r"staging database is ([A-Za-z0-9.-]+[A-Za-z0-9])")


@dataclass
class ScriptedModel(Model):
    id: str = "scripted-stub"
    name: str = "ScriptedModel"
    provider: str = "stub"

    # --- the script -----------------------------------------------------------------------------
    def _call(self, name: str, arguments: dict) -> ModelResponse:
        # Unique like a provider's ids, so two agents writing into one transcript cannot collide.
        return ModelResponse(role="assistant", content="", tool_calls=[{
            "id": "call_" + uuid.uuid4().hex[:16], "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}])

    def _next(self, messages: List[Any]) -> ModelResponse:
        last_user = max(i for i, m in enumerate(messages) if m.role == "user")
        tool_msgs = [m for m in messages[last_user + 1:] if m.role == "tool"]
        if not tool_msgs:
            return self._call("recall_memory", {"query": str(messages[last_user].content)})
        last = tool_msgs[-1]
        if last.tool_name == "recall_memory":
            found = _HOST.search(str(last.content or ""))
            if found is None:
                return ModelResponse(role="assistant", content="I do not know which staging database to use.")
            return self._call("run_migration", {"database": found.group(1)})
        return ModelResponse(role="assistant", content=f"Done: {last.content}")

    # --- agno's Model interface -----------------------------------------------------------------
    def invoke(self, messages: List[Any], **kwargs: Any) -> ModelResponse:
        return self._next(messages)

    async def ainvoke(self, messages: List[Any], **kwargs: Any) -> ModelResponse:
        return self._next(messages)

    def invoke_stream(self, messages: List[Any], **kwargs: Any) -> Iterator[ModelResponse]:
        yield self._next(messages)

    async def ainvoke_stream(self, messages: List[Any], **kwargs: Any) -> AsyncIterator[ModelResponse]:
        yield self._next(messages)

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response
