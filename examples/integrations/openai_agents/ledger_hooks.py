"""Every tool call of an OpenAI Agents SDK run, as one signed entry in the inspeximus action ledger.

    from agents import Runner
    from inspeximus.actions import ActionLedger
    from ledger_hooks import InspeximusLedgerHooks

    hooks = InspeximusLedgerHooks(ActionLedger(store, actor="invoice-agent"), session="billing-desk")
    try:
        result = await Runner.run(agent, text, session=session, hooks=hooks)
    finally:
        hooks.flush()

The SDK's `RunHooks` is the seam, the way `inspeximus.integrations.langchain.InspeximusActionCallback`
uses LangChain's callbacks and the MCP server wraps its own tool boundary. Nothing in the agent changes
except the `hooks=` argument, and `failure_error_function=hooks.tool_error` on tools whose errors should
be recorded as errors (see below).

WHAT EACH ENTRY SAYS THE CALL WAS BASED ON
  memory_state     the ledger's own binding, captured when the tool STARTED: the store's state digest,
                   the tail of its signed receipt chain, and the ids the last recall on this store handle
                   returned. `ActionLedger.verify()` checks the receipt against the store.
  meta.based_on    the earlier ledger entries whose tool outputs were in the model's input when it asked
                   for this call, as {seq, hash} references (the latest `max_refs`; `context_outputs` is
                   the full count). verify_ledger.py resolves every one.
  model, meta.response_id
                   the model that asked for the call and the response it came in.
  inputs_sha256, output_sha256
                   salted digests of the call's arguments and of the output the model was given, so a
                   retained transcript can be checked against the entry (`ActionLedger.matches`).

WHAT THE SDK'S HOOKS DO NOT SHOW, AND HOW THIS COVERS IT. Measured on openai-agents 0.22.3.
  - There is no on_tool_error. A tool that raises is turned into an error string by the default
    failure_error_function, and on_tool_end receives that string as an ordinary result. Pass
    `failure_error_function=hooks.tool_error` and the entry records status "error" with the exception;
    the model still receives the SDK's default error message.
  - With failure_error_function=None the exception propagates: on_tool_start fired, on_tool_end never
    does, and Runner.run raises. flush() records the call with status "error" and meta.flushed. The SDK
    then saves nothing of that turn to the Session, so the ledger is the only record the call started.
  - on_handoff carries no call id. The SDK runs the first handoff the model asked for, so that one is
    matched, and it is written when its transfer message reaches the next model input.
  - A call refused by a tool input guardrail or at approval never reaches on_tool_start. The model's
    response named it (on_llm_end) and the refusal is in the next model input (on_llm_start), so it is
    recorded there with status "not_run" and the refusal as its output.
  - Hosted tools (web search, file search, code interpreter, image generation, hosted MCP) run on the
    provider's side and fire no hook. They are recorded from the model response that carried them,
    with the item as the input digest and meta.executed_by "provider".
  - Computer, shell, local shell and apply-patch tools are handed a plain RunContextWrapper with no call
    id and no arguments, so their entries carry the tool name, output and memory state and no inputs.
"""
from __future__ import annotations

import json
import time
from typing import Any

from agents import RunHooks
from agents.handoffs import Handoff
from agents.items import ItemHelpers
from agents.tool import default_tool_error_function
from agents.tool_context import ToolContext

#: Response items that are tool calls the PROVIDER executes. No local hook fires for any of them.
HOSTED_CALL_TYPES = ("web_search_call", "file_search_call", "code_interpreter_call",
                     "image_generation_call", "mcp_call")


def as_item(obj: Any) -> dict:
    """A response or input item as the plain dict the SDK stores in a Session.

    Pydantic items are dumped with exclude_unset, the same call `RunItem.to_input_item()` makes, so a
    digest taken here matches the item the session holds."""
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump(exclude_unset=True)
    return {"repr": repr(obj)}


def call_arguments(raw: Any) -> Any:
    """A function call's arguments as the model sent them: the parsed JSON, or the raw string when the
    model sent something that does not parse (the digest then covers exactly what it sent)."""
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _model_name(agent: Any) -> str | None:
    m = getattr(agent, "model", None)
    if isinstance(m, str):
        return m or None
    if m is None:
        return None                     # the run config's provider picks it; the agent does not say
    return str(getattr(m, "model", None) or type(m).__name__)


def _handoff_names(agent: Any) -> set:
    out = set()
    for h in getattr(agent, "handoffs", None) or []:
        out.add(h.tool_name if isinstance(h, Handoff) else Handoff.default_tool_name(h))
    return out


class InspeximusLedgerHooks(RunHooks):
    """RunHooks that write one ledger entry per tool call. Reuse one instance across the runs of a
    conversation: it keeps the call id to ledger entry map that `meta.based_on` is built from."""

    def __init__(self, ledger, principal: str | None = None, session: str | None = None,
                 max_refs: int = 32):
        self.ledger = ledger
        self.principal = principal          # the person or account the agent acts for
        self.session = session              # stamped on every entry; verify_ledger.py selects by it
        self.max_refs = int(max_refs)
        self._open: dict = {}               # started, not yet ended: call key -> what to record
        self._requested: dict = {}          # asked for by the model, not yet recorded: call id -> request
        self._handoffs: dict = {}           # agent name -> handoff call ids it asked for, in order
        self._context: dict = {}            # agent name -> refs of the tool outputs in its model input
        self._errors: dict = {}             # call id -> the exception tool_error saw
        self._anon = 0
        # Every call the ledger already holds, so a later run's model input (the session replays the
        # earlier turns) resolves to the entries that recorded those outputs.
        self._by_call: dict = {}
        for e in ledger.entries():
            cid = (e.get("meta") or {}).get("call_id")
            if cid:
                self._by_call[cid] = {"seq": e["seq"], "hash": e["hash"]}

    # ---------------------------------------------------------------- the model's side
    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        refs = []
        for it in input_items or []:
            d = as_item(it)
            t = d.get("type") or ""
            cid = d.get("call_id") if t.endswith("_output") else (d.get("id") if t in HOSTED_CALL_TYPES else None)
            if not cid:
                continue
            if t.endswith("_output") and cid in self._requested and cid not in self._open:
                if "handoff_to" in self._requested[cid]:
                    # a handoff that ran: recorded with the transfer message the model was given
                    self._record_request(cid, status="ok", output=d.get("output"))
                else:
                    # asked for, answered, never run: the SDK refused it before on_tool_start
                    self._record_request(cid, status="not_run", output=d.get("output"),
                                         note="the SDK answered this call without running the tool: a tool "
                                              "input guardrail or an approval refused it")
            ref = self._by_call.get(cid)
            if ref and ref not in refs:
                refs.append(ref)
        self._context[agent.name] = refs

    async def on_llm_end(self, context, agent, response) -> None:
        refs = self._context.pop(agent.name, [])
        handoffs = _handoff_names(agent)
        snapshot = None
        for it in getattr(response, "output", None) or []:
            d = as_item(it)
            t = d.get("type")
            if t != "function_call" and t not in HOSTED_CALL_TYPES:
                continue
            if snapshot is None:
                snapshot = self.ledger.memory_state()      # what memory held when the model decided
            req = {"agent": agent.name, "model": _model_name(agent), "response_id": response.response_id,
                   "based_on": refs[-self.max_refs:] if self.max_refs > 0 else [],
                   "context_outputs": len(refs), "memory_state": snapshot}
            if t in HOSTED_CALL_TYPES:
                self._record_hosted(d, req)
                continue
            req.update(name=d.get("name"), arguments=d.get("arguments"))
            self._requested[d.get("call_id")] = req
            if d.get("name") in handoffs:
                self._handoffs.setdefault(agent.name, []).append(d.get("call_id"))

    async def on_handoff(self, context, from_agent, to_agent) -> None:
        # The hook carries no call id. The SDK runs the first handoff the model asked for and refuses the
        # rest, so the first pending one is it. Written when its output reaches the next model input.
        pending = self._handoffs.get(from_agent.name) or []
        while pending:
            cid = pending.pop(0)
            if cid in self._requested:
                self._requested[cid]["handoff_to"] = to_agent.name
                return

    async def on_agent_end(self, context, agent, output) -> None:
        self.flush()

    # ---------------------------------------------------------------- the tool's side
    async def on_tool_start(self, context, agent, tool) -> None:
        if isinstance(context, ToolContext):
            key, name = context.tool_call_id, context.tool_name
            inputs = call_arguments(context.tool_arguments)
            meta = {"call_id": key, "tool": name, "agent": agent.name}
        else:
            self._anon += 1
            key, name, inputs = (getattr(tool, "name", "tool"), self._anon), getattr(tool, "name", "tool"), None
            meta = {"call_id": None, "tool": name, "agent": agent.name,
                    "note": "the SDK hands this tool family no call id and no arguments"}
        self._open[key] = {"action": "tool:" + str(name), "inputs": inputs, "meta": meta,
                           "started": time.time(), "memory_state": self.ledger.memory_state(),
                           "model": _model_name(agent)}

    async def on_tool_end(self, context, agent, tool, result) -> None:
        if isinstance(context, ToolContext):
            key = context.tool_call_id
            output = result
            call = getattr(context, "tool_call", None)
            if call is not None:
                try:            # the output exactly as the model was given it, the shape the session keeps
                    output = ItemHelpers.tool_call_output_item(call, result)["output"]
                except Exception:  # noqa: BLE001 - fall back to the raw result
                    output = result
        else:
            key = min((k for k in self._open if isinstance(k, tuple) and k[0] == getattr(tool, "name", "tool")),
                      default=None, key=lambda k: k[1])
            output = result
        op = self._open.pop(key, None)
        if op is None:
            return                              # started before these hooks were attached
        err = self._errors.pop(key, None)
        self._write(op, output=output, status="error" if err else "ok", error=err)

    def tool_error(self, context, error: Exception) -> str:
        """A `failure_error_function` that records the exception, then answers the model exactly as the
        SDK's default does. The SDK has no on_tool_error; without this a raised tool reads as success."""
        cid = getattr(context, "tool_call_id", None)
        if cid:
            self._errors[cid] = f"{type(error).__name__}: {error}"
        return default_tool_error_function(context, error)

    # ---------------------------------------------------------------- closing
    def flush(self, result: Any = None) -> list[dict]:
        """Record what the SDK never reported an end for. A tool that started and did not end (its
        exception propagated, or the run was cancelled) is an "error"; a call the model asked for that
        was neither run nor answered is "not_run". Calls waiting on approval in `result.interruptions`
        stay pending, since the resumed run executes them. Returns the entries written."""
        keep = set()
        for it in getattr(result, "interruptions", None) or []:
            raw = getattr(it, "raw_item", None)
            cid = raw.get("call_id") if isinstance(raw, dict) else getattr(raw, "call_id", None)
            if cid:
                keep.add(cid)
        written = []
        for key in list(self._open):
            op = self._open.pop(key)
            err = self._errors.pop(key, None) or "the tool started and the SDK reported no end: its exception " \
                                                 "propagated or the run was cancelled"
            op["meta"]["flushed"] = True
            written.append(self._write(op, output=None, status="error", error=err))
        for cid in list(self._requested):
            if cid in keep:
                continue
            if "handoff_to" in self._requested[cid]:
                written.append(self._record_request(cid, status="ok", output=None, flushed=True))
            else:
                written.append(self._record_request(cid, status="not_run", output=None, flushed=True,
                                                    note="the run ended before this call was run or answered"))
        return written

    # ---------------------------------------------------------------- writing
    def _write(self, op: dict, output: Any, status: str, error: str | None) -> dict:
        meta = dict(op["meta"])
        req = self._requested.pop(meta.get("call_id"), None) or {}
        model = req.get("model") or op.get("model")
        for k in ("response_id", "based_on", "context_outputs"):
            if k in req:
                meta[k] = req[k]
        e = self.ledger.record(op["action"], inputs=op["inputs"], output=output, status=status, error=error,
                               meta=meta, started=op["started"], memory_state=op["memory_state"],
                               model=model, principal=self.principal, session=self.session)
        self._remember(meta.get("call_id"), e)
        return e

    def _record_request(self, cid: str, status: str, output: Any, note: str | None = None,
                        flushed: bool = False) -> dict:
        req = self._requested.pop(cid)
        meta = {"call_id": cid, "tool": req.get("name"), "agent": req["agent"],
                "response_id": req["response_id"], "based_on": req["based_on"],
                "context_outputs": req["context_outputs"]}
        if "handoff_to" in req:
            meta["handoff_to"] = req["handoff_to"]
        if note:
            meta["note"] = note
        if flushed:
            meta["flushed"] = True          # written by flush(): the SDK reported no end for this call
        action = ("handoff:" if "handoff_to" in req else "tool:") + str(req.get("name"))
        e = self.ledger.record(action, inputs=call_arguments(req.get("arguments")),
                               output=output, status=status, meta=meta, memory_state=req["memory_state"],
                               model=req["model"], principal=self.principal, session=self.session)
        self._remember(cid, e)
        return e

    def _record_hosted(self, item: dict, req: dict) -> dict:
        iid = item.get("id")
        status = item.get("status")
        meta = {"call_id": iid, "tool": item.get("type"), "agent": req["agent"], "executed_by": "provider",
                "response_id": req["response_id"], "based_on": req["based_on"],
                "context_outputs": req["context_outputs"]}
        e = self.ledger.record("hosted:" + str(item.get("type")), inputs=item, output=None,
                               status="ok" if status in (None, "completed") else str(status), meta=meta,
                               memory_state=req["memory_state"], model=req["model"],
                               principal=self.principal, session=self.session)
        self._remember(iid, e)
        return e

    def _remember(self, cid: Any, entry: dict) -> None:
        if cid:
            self._by_call[cid] = {"seq": entry["seq"], "hash": entry["hash"]}

