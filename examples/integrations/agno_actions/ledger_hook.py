"""One agno tool hook that writes every tool call into the inspeximus action ledger.

agno runs each tool call through the agent's `tool_hooks` chain, including a call answered from
agno's tool cache. So one hook on the agent covers every tool it has, and each call becomes one
signed, hash-chained ledger entry:

    action         "tool:<name>"
    inputs_sha256  salted digest of the arguments the tool ran with
    output_sha256  salted digest of the result, as the text agno puts into the tool message
    memory_state   what the call was based on: the store's state digest, the tail of its write-receipt
                   chain, and the ids the store's last recall returned. Captured before the tool runs.
    model          the model id that issued the call
    session        agno's session_id
    principal      agno's user_id
    meta           the agno run_id and the tool_call_id of the model's call

The ledger keeps digests and never the arguments or results themselves. Whoever keeps the transcript
(agno's own RunOutput.tools) can later prove it with `ActionLedger.matches()`. See verify.py.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Optional

from inspeximus.actions import ActionLedger


def tool_output_text(result: Any) -> Optional[str]:
    """The text agno turns a tool's return value into (agno/models/base.py, run_function_call):
    a ToolResult's `content`, else `str(result)`. The output digest is taken over this text, so it
    compares against `ToolExecution.result` without any conversion. A generator is streamed to the
    model after the hook returns, so its output cannot be digested here and is recorded as None."""
    if result is None:
        return None
    if hasattr(result, "__next__") or hasattr(result, "__anext__"):
        return None
    content = getattr(result, "content", None)
    if type(result).__name__ == "ToolResult" and isinstance(content, str):
        return content
    return result if isinstance(result, str) else str(result)


def _tool_call_id(run_context: Any, name: str, arguments: dict, taken: set) -> Optional[str]:
    """The id of the model's tool call this execution answers, read from the assistant message that
    issued it. agno does not hand the id to a tool hook, but it does hand over the run's messages.
    The first call with this name and these arguments that no earlier entry has claimed wins, so two
    identical calls in one message still get their own ids, in order."""
    for msg in reversed(list(getattr(run_context, "messages", None) or [])):
        calls = getattr(msg, "tool_calls", None)
        if getattr(msg, "role", None) != "assistant" or not calls:
            continue
        fallback = None
        for call in calls:
            cid = call.get("id")
            fn = call.get("function") or {}
            if not cid or cid in taken or fn.get("name") != name:
                continue
            try:
                sent = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                sent = None
            if sent == arguments:
                return cid
            fallback = fallback or cid        # agno dropped an injected argument the model sent
        return fallback
    return None


def ledger_hook(ledger: ActionLedger) -> Callable[..., Any]:
    """An agno tool hook that records every tool call into `ledger`.

        agent = Agent(model=..., tools=[...], tool_hooks=[ledger_hook(ledger)])

    A tool that raises is recorded with status "error" and the exception is re-raised, so agno
    reports the failure to the model exactly as it would without the hook.

    The same hook serves `agent.run()` and `agent.arun()`. agno skips a coroutine hook in a sync run,
    so the hook is a plain function; in an async run agno hands it an async `function_call`, and the
    hook then returns a coroutine that awaits the tool inside the ledger entry, which agno awaits."""
    claimed: dict = {}            # run_id -> the tool_call_ids already recorded; the last 64 runs

    def record_tool_call(function_name: str, function_call: Callable[..., Any], arguments: dict,
                         agent: Any = None, run_context: Any = None) -> Any:
        args = dict(arguments or {})
        taken = claimed.setdefault(getattr(run_context, "run_id", None), set())
        while len(claimed) > 64:
            claimed.pop(next(iter(claimed)))
        call_id = _tool_call_id(run_context, function_name, args, taken)
        if call_id:
            taken.add(call_id)
        entry = dict(action="tool:" + function_name, inputs=args,
                     meta={"framework": "agno", "run_id": getattr(run_context, "run_id", None),
                           "tool_call_id": call_id},
                     model=getattr(getattr(agent, "model", None), "id", None),
                     principal=getattr(run_context, "user_id", None),
                     session=getattr(run_context, "session_id", None))

        if inspect.iscoroutinefunction(function_call):
            async def recorded() -> Any:
                with ledger.action(**entry) as act:
                    result = await function_call(**args)
                    act.output(tool_output_text(result))
                return result
            return recorded()

        with ledger.action(**entry) as act:
            result = function_call(**args)
            act.output(tool_output_text(result))
        return result

    return record_tool_call
