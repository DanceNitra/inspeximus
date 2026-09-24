"""Every CrewAI tool call becomes one signed inspeximus action-ledger entry, bound to what it was based on.

This is the CrewAI counterpart of two recorders the repository already ships: the MCP server wraps each
tool in `ActionLedger.action("mcp:<tool>")`, and `inspeximus.integrations.langchain` records each tool
run from LangChain's callbacks as `tool:<name>`. CrewAI's equivalent seam is its tool-call hooks
(`crewai.hooks`), which fire around every tool execution on both of its agent loops, ReAct text and
native function calling. So:

    before_tool_call  ->  capture what the agent was holding: the store's state digest and receipt tail,
                          and the memory records CrewAI's recall put into THIS task's prompt
    after_tool_call   ->  append one entry: `tool:<name>`, inputs and output digested (salted SHA-256,
                          content-free), the captured memory state, and the earlier tool calls of the
                          same task by seq and hash

"What it was based on" needs one piece CrewAI does not hand a hook: which memories the agent saw. The
ledger's own `memory_state()` reads the store's last `recall()`, but CrewAI 1.x never calls it; its
`Memory.recall()` embeds the query and searches the StorageBackend by vector. `TrackedMemory` below is a
`crewai.memory.Memory` that reports every recall's matches, keyed by the task CrewAI says is running
(`crewai.context.get_current_task_id()`), and the recorder maps CrewAI record ids to the inspeximus
record ids that `ActionLedger.what_it_knew()` and `provenance()` resolve.

FAIL-CLOSED, as far as CrewAI lets a hook be. CrewAI swallows any exception a hook raises except
`HookAborted`, so a recorder that simply raised on a full disk would let the tool run unrecorded and
say nothing. Here a failure to capture the basis BLOCKS the tool (before hook). A failure to write the
entry after the tool ran raises `HookAborted`, and CrewAI 1.15.22 turns that into the observation
"Error executing tool: ...": the agent never sees the result of a call the ledger could not record.
From then on every tool call is blocked. What no hook can do is end the crew: it carries on to a final
answer, so the gap has to be reported by the run, which is what crew.py's cross-check does.

The hooks are process-wide. Pass `crew=` and the recorder ignores tool calls from any other crew
running in the same process.
"""
from __future__ import annotations

import contextlib
import json
import threading
import time
from typing import Any, Iterator

from crewai.context import get_current_task_id
from crewai.hooks import (HookAborted, register_after_tool_call_hook, register_before_tool_call_hook,
                          unregister_after_tool_call_hook, unregister_before_tool_call_hook)
from crewai.memory import Memory
from pydantic import PrivateAttr

#: The CrewAI build this recorder was written and tested against. See README.md.
TESTED_CREWAI = "1.15.22"
BLOCKED_PREFIX = "Tool execution blocked by hook"


class TrackedMemory(Memory):
    """`crewai.memory.Memory` that tells a listener what each recall returned.

    Nothing else changes: storage, scoring, the encoding pipeline and the recall flow are CrewAI's own.
    The listener sees the final `MemoryMatch` list, which is exactly what CrewAI formats into the
    "Relevant memories" block of the prompt, or returns from the "Search memory" tool."""

    _recall_listeners: list = PrivateAttr(default_factory=list)

    def add_recall_listener(self, fn) -> None:
        self._recall_listeners.append(fn)

    def recall(self, query, *args, **kwargs):
        matches = super().recall(query, *args, **kwargs)
        for fn in list(self._recall_listeners):
            fn(query, matches)
        return matches


def _plain(value: Any) -> Any:
    """A JSON-safe copy, taken at the moment of the call so a later in-place edit cannot change it."""
    try:
        return json.loads(json.dumps(value, sort_keys=True, default=str))
    except (TypeError, ValueError):
        return repr(value)


class CrewToolLedger:
    """Records CrewAI tool calls into an `inspeximus.actions.ActionLedger`.

        ledger = ActionLedger(store, actor="support-crew")
        recorder = CrewToolLedger(ledger, backend, memory)
        with recorder.installed():
            crew.kickoff()

    `backend` is the `InspeximusMemoryBackend` behind `memory`; it is how a CrewAI record id is turned
    into the inspeximus record id the ledger stores. `principal` is recorded on every entry as the person
    or account the crew acts for."""

    def __init__(self, ledger, backend, memory: TrackedMemory | None = None, principal: str | None = None,
                 crew=None):
        self.ledger = ledger
        self._crew_id = str(crew.id) if crew is not None else None
        self.backend = backend
        self.principal = principal
        self._lock = threading.Lock()
        self._local = threading.local()
        self._recalls: dict[str, list[dict]] = {}          # task id -> recall windows, in order
        self._task_calls: dict[str, list[dict]] = {}       # task id -> [{seq, hash}] already recorded
        self.failed: str | None = None
        self.recorded: list[dict] = []
        if memory is not None:
            memory.add_recall_listener(self.note_recall)

    # ── what the agent saw ──────────────────────────────────────────────────────────────────────
    def _inspeximus_ids(self, crewai_ids: list[str]) -> dict[str, str]:
        wanted = set(crewai_ids)
        out = {}
        for row in list(getattr(self.backend.store, "items", [])):
            crew = (row.get("meta") or {}).get("crew") or {}
            if (row.get("status") == "active" and crew.get("id") in wanted
                    and self.backend.TAG in (row.get("tags") or [])):
                out[crew["id"]] = row["id"]
        return out

    def note_recall(self, query: str, matches) -> None:
        """Called by `TrackedMemory.recall` with what it is about to return."""
        task = get_current_task_id() or "(no task)"
        crew_ids = [m.record.id for m in matches or []]
        mapped = self._inspeximus_ids(crew_ids)
        window = {"at": time.time(),
                  "memories": [{"id": mapped.get(m.record.id), "crewai_id": m.record.id,
                                "score": round(float(m.score), 6)} for m in matches or []]}
        with self._lock:
            self._recalls.setdefault(task, []).append(window)

    def _basis(self, task_id: str) -> tuple[list[dict], list[dict]]:
        with self._lock:
            windows = list(self._recalls.get(task_id, []))
            earlier = list(self._task_calls.get(task_id, []))
        seen, memories = set(), []
        for w in windows:
            for m in w["memories"]:
                if m["crewai_id"] not in seen:
                    seen.add(m["crewai_id"])
                    memories.append({**m, "at": w["at"]})
        return memories, earlier

    # ── the hooks ───────────────────────────────────────────────────────────────────────────────
    def _stack(self) -> list:
        if not hasattr(self._local, "stack"):
            self._local.stack = []
        return self._local.stack

    def _foreign(self, ctx) -> bool:
        if self._crew_id is None:
            return False
        c = getattr(ctx, "crew", None)
        return c is None or str(getattr(c, "id", "")) != self._crew_id

    def before(self, ctx) -> None:
        if self._foreign(ctx):
            return None
        if self.failed:
            raise HookAborted(f"the action ledger failed earlier ({self.failed}); no tool runs unrecorded",
                              source="inspeximus")
        try:
            task_id = str(ctx.task.id) if ctx.task is not None else "(no task)"
            memories, earlier = self._basis(task_id)
            state = self.ledger.memory_state()
            # The ledger reads the store's own recall window, which CrewAI never fills. Replace it with
            # what CrewAI's recall actually put in front of this agent for this task, and say so.
            state["recalled"] = [m["id"] for m in memories if m["id"]][:64]
            state["recalled_at"] = max((m["at"] for m in memories), default=None)
            state["recall_scope"] = "crewai Memory.recall, this task"
            state["recall_before_previous_entry"] = False
            self._stack().append({
                "tool": ctx.tool_name, "inputs": {"tool": ctx.tool_name, "args": _plain(ctx.tool_input)},
                "started": time.time(), "memory_state": state, "task_id": task_id,
                "based_on": {"memories": memories, "earlier_tool_calls": earlier},
            })
        except Exception as e:                       # noqa: BLE001 - fail closed, see module docstring
            self.failed = f"{type(e).__name__}: {e}"
            raise HookAborted(f"cannot capture what this tool call is based on: {self.failed}",
                              source="inspeximus") from e
        return None

    def after(self, ctx) -> None:
        if self._foreign(ctx):
            return None
        stack = self._stack()
        opened = None
        for i in range(len(stack) - 1, -1, -1):
            if stack[i]["tool"] == ctx.tool_name:
                opened = stack.pop(i)
                break
        if opened is None:
            if self.failed:
                return None                           # the before hook blocked it; nothing ran
            opened = {"tool": ctx.tool_name, "inputs": {"tool": ctx.tool_name, "args": _plain(ctx.tool_input)},
                      "started": None, "memory_state": None, "task_id": "(unknown)",
                      "based_on": {"memories": [], "earlier_tool_calls": [],
                                   "note": "no before_tool_call was seen for this call"}}
        result = ctx.tool_result
        blocked = isinstance(result, str) and result.startswith(BLOCKED_PREFIX)
        agent = ctx.agent
        llm = getattr(agent, "llm", None)
        meta = {"framework": "crewai", "crewai_tested": TESTED_CREWAI,
                "task_id": opened["task_id"],
                "task": (getattr(ctx.task, "name", None) or None) if ctx.task is not None else None,
                "based_on": opened["based_on"]}
        try:
            entry = self.ledger.record(
                "tool:" + str(ctx.tool_name), inputs=opened["inputs"], output=_plain(result),
                status="blocked" if blocked else "ok", started=opened["started"],
                actor=getattr(agent, "role", None) or self.ledger.actor,
                memory_state=opened["memory_state"], meta=meta,
                model=getattr(llm, "model", None) if llm is not None else None,
                principal=self.principal,
                session=str(ctx.crew.id) if getattr(ctx, "crew", None) is not None else None)
        except Exception as e:                       # noqa: BLE001 - fail closed, see module docstring
            self.failed = f"{type(e).__name__}: {e}"
            raise HookAborted(f"tool {ctx.tool_name} ran but the action ledger could not record it: "
                              f"{self.failed}", source="inspeximus") from e
        ref = {"seq": entry["seq"], "hash": entry["hash"]}
        with self._lock:
            self._task_calls.setdefault(opened["task_id"], []).append(ref)
            self.recorded.append(entry)
        return None

    @contextlib.contextmanager
    def installed(self) -> Iterator["CrewToolLedger"]:
        """Register the two hooks for the duration of the block. CrewAI's tool hooks are process-wide,
        so they are removed on the way out rather than left to record some other crew's calls."""
        register_before_tool_call_hook(self.before)
        register_after_tool_call_hook(self.after)
        try:
            yield self
        finally:
            unregister_before_tool_call_hook(self.before)
            unregister_after_tool_call_hook(self.after)
