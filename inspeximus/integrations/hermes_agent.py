"""InspeximusMemoryProvider — a Hermes Agent memory provider backed by inspeximus.

Hermes Agent (NousResearch) lets an install choose ONE external memory provider, selected by name in
`memory.provider`. It ships eight of them, mem0 and supermemory among them, and the bundled directory
is closed to new entries. The route that stays open needs nobody's permission: a pip-installed
package publishes an entry point in the `hermes_agent.memory_providers` group, and Hermes discovers
it. That is what this module is, and it is why `pip install inspeximus` is the whole install.

    # ~/.hermes config
    memory:
      provider: inspeximus

WHY THIS ADAPTER IS NOT INCIDENTAL. Hermes calls `prefetch(query)` before each turn and injects what
it returns into the model's context. Every other provider answers that question with retrieval. This
one answers it with retrieval MINUS what a later correction retired: `recall()` hides superseded and
poisoned records by default, so a value the user corrected three sessions ago cannot come back as
context. The agent's own tools then make the correction channel explicit rather than implicit, which
is the thing a retrieval-only memory cannot offer at all:

    inspeximus_remember   store a fact, optionally under a stable key
    inspeximus_correct    supersede that key; the old value stops being recalled
    inspeximus_revert     put the previous value back, by key
    inspeximus_forget     erase a subject and leave a receipt saying so

KEYS ARE THE WHOLE MECHANISM. `key="user::timezone"` makes a later write for that key RETIRE the
earlier one deterministically, with no model call and no similarity threshold. Without a key a write
is an ordinary appended fact, which is what every other provider gives you.

Zero-dependency core: importing this module never imports Hermes. The `MemoryProvider` base class is
imported lazily inside `_base()`, so `pip install inspeximus` alone is enough and the class only
materialises inside a Hermes process where `agent.memory_provider` is importable.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .._surface import open_store

#: The name a user selects in `memory.provider`, and the entry-point name in pyproject.toml. The two
#: MUST agree: Hermes activates a provider BY NAME, so a mismatch is a provider that can be
#: discovered and never selected.
PROVIDER_NAME = "inspeximus"

_TOOLS = [
    {
        "name": "inspeximus_remember",
        "description": (
            "Store a durable fact. Pass a stable `key` for anything that can later change, such as "
            "user::timezone or project::database. A later write to the same key retires this value, "
            "so the correction is deterministic instead of hoping retrieval prefers the newer text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The fact, as one sentence."},
                "key": {"type": "string", "description": "Stable identity of the fact, optional."},
                "mtype": {"type": "string",
                          "description": "fact | decision | preference | task. Defaults to fact."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "inspeximus_correct",
        "description": (
            "Correct a fact stored under a key. The previous value stops being recalled from now on, "
            "and stays in the history so the change can be audited or reverted."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "The key whose value is now wrong."},
                "text": {"type": "string", "description": "The corrected fact."},
            },
            "required": ["key", "text"],
        },
    },
    {
        "name": "inspeximus_revert",
        "description": (
            "Undo the most recent correction for a key and make the previous value current again. "
            "Use it when a correction turned out to be the mistake."
        ),
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "The key to roll back."}},
            "required": ["key"],
        },
    },
    {
        "name": "inspeximus_forget",
        "description": (
            "Erase everything stored about a subject and record that the erasure happened. Use it "
            "when the user asks you to forget something about them."
        ),
        "parameters": {
            "type": "object",
            "properties": {"subject": {"type": "string",
                                       "description": "The person or thing to erase."}},
            "required": ["subject"],
        },
    },
]


def _base():
    """The Hermes ABC, imported lazily so this module is importable without Hermes installed.

    Returns None when Hermes is absent, and `register()` then refuses rather than raising: a plugin
    that explodes on import takes the host down with it, and discovery imports nothing by contract.
    """
    try:
        from agent.memory_provider import MemoryProvider   # type: ignore
        return MemoryProvider
    except Exception:                                       # noqa: BLE001 - absence is not an error
        return None


def _make_class(base):
    """Build the provider class against whatever base Hermes gave us.

    Defined inside a function rather than at module scope because the base class only exists inside
    a Hermes process. At module scope this file would either import Hermes eagerly, which breaks the
    zero-dependency promise, or subclass `object` and silently fail the host's isinstance check.
    """

    class InspeximusMemoryProvider(base):                   # type: ignore[misc,valid-type]
        """Hermes Agent memory provider whose recall excludes what a correction retired."""

        def __init__(self, path: str | None = None, store: Any = None, k: int = 5):
            self._explicit_path = path
            self._store = store
            self._k = k
            self._session_id = ""
            self._last_recalled = 0

        @property
        def name(self) -> str:
            return PROVIDER_NAME

        def is_available(self) -> bool:
            """Importable and able to open a store. NO network call, per the base class contract.

            inspeximus needs no key and no server, so the honest answer is whether the library
            imports. Returning True on a broken install would make Hermes activate a provider that
            then fails on the first turn, which is worse than not offering it.
            """
            try:
                import inspeximus                            # noqa: F401
                return True
            except Exception:                                # noqa: BLE001
                return False

        def initialize(self, session_id: str, **kwargs) -> None:
            """Open the store under the profile Hermes gives us.

            `hermes_home` is profile-scoped and the base class is explicit that a provider must not
            hardcode ~/.hermes: two profiles are two memories, and writing both into one file is the
            bug that isolation exists to prevent.
            """
            self._session_id = session_id or ""
            if self._store is None:
                path = self._explicit_path
                if not path:
                    home = kwargs.get("hermes_home") or os.path.expanduser("~/.hermes")
                    path = os.path.join(home, "inspeximus", "memory.json")
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                self._store = open_store(path)

        # -- what the agent reads ------------------------------------------------------

        def system_prompt_block(self) -> str:
            return ("You have a durable memory with a correction channel. Store anything that should "
                    "outlive this session with inspeximus_remember, and give a stable key to anything "
                    "that can change. When the user corrects such a fact, call inspeximus_correct with "
                    "that key rather than storing a second, competing version.")

        def prefetch(self, query: str, *, session_id: str = "") -> str:
            """Recall for the coming turn, with superseded values already excluded.

            Returns "" rather than a header with nothing under it: the base class treats "" as "no
            context", and an empty block still costs tokens and still reads to the model as though
            memory had been consulted and had nothing, which is a different claim.
            """
            if self._store is None or not (query or "").strip():
                self._last_recalled = 0
                return ""
            try:
                hits = self._store.recall(query, k=self._k)
            except Exception:                                # noqa: BLE001 - never break a turn
                self._last_recalled = 0
                return ""
            lines = [str(h.get("text", "")).strip() for h in hits if str(h.get("text", "")).strip()]
            self._last_recalled = len(lines)
            if not lines:
                return ""
            return "What you remember:\n" + "\n".join("- " + t for t in lines)

        def recall_status(self):
            """Reflects the LAST prefetch only, which the base class requires by name."""
            if not self._last_recalled:
                return None
            try:
                from agent.memory_provider import RecallStatus   # type: ignore
            except Exception:                                    # noqa: BLE001
                return None
            return RecallStatus(provider_label="inspeximus", count=self._last_recalled)

        # -- what the agent writes -----------------------------------------------------

        def _source(self) -> Dict[str, str]:
            """Where the record came from, carried on every write so erasure can reach it."""
            return {"doc": "hermes-agent::" + (self._session_id or "session")}

        def get_tool_schemas(self) -> List[Dict[str, Any]]:
            return list(_TOOLS)

        def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
            """Dispatch one tool. Returns a JSON string, which the base class requires."""
            if self._store is None:
                return json.dumps({"ok": False, "error": "the store is not initialized"})
            try:
                if tool_name == "inspeximus_remember":
                    # source= is what makes this record reachable by forget_subject() later. Without
                    # it, this provider offers an inspeximus_forget tool that cannot see its own
                    # writes, which is worse than offering none.
                    rid = self._store.remember(args["text"], key=args.get("key"),
                                               mtype=args.get("mtype") or "fact",
                                               source=self._source())
                    return json.dumps({"ok": True, "id": rid, "key": args.get("key")})
                if tool_name == "inspeximus_correct":
                    rid = self._store.remember(args["text"], key=args["key"], mtype="fact",
                                               source=self._source())
                    return json.dumps({"ok": True, "id": rid, "key": args["key"],
                                       "note": "the previous value for this key is no longer recalled"})
                if tool_name == "inspeximus_revert":
                    out = self._store.revert(args["key"])
                    return json.dumps({"ok": bool(out), "key": args["key"], "result": out})
                if tool_name == "inspeximus_forget":
                    out = self._store.forget_subject(args["subject"])
                    return json.dumps({"ok": True, "subject": args["subject"], "result": out})
            except KeyError as e:
                return json.dumps({"ok": False, "error": "missing argument %s" % e})
            except Exception as e:                           # noqa: BLE001
                return json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
            return json.dumps({"ok": False, "error": "unknown tool %s" % tool_name})

        def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                      messages=None, turn_author=None) -> None:
            """Deliberately does nothing, and that is the design rather than an omission.

            Providers that persist every turn automatically need a model call to decide what was
            worth keeping, and that is the cost this store exists to avoid. Writes here are explicit:
            the agent calls a tool when a fact is worth keeping, so what the store holds is what
            something decided to keep, not a transcript.
            """

        # -- config ---------------------------------------------------------------------

        def get_config_schema(self) -> List[Dict[str, Any]]:
            return [{
                "key": "path",
                "label": "Store file",
                "type": "string",
                "required": False,
                "description": ("Where the memory lives. Empty means "
                                "<hermes_home>/inspeximus/memory.json, which keeps profiles apart."),
            }]

        def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
            path = os.path.join(hermes_home, "inspeximus", "config.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"path": (values or {}).get("path") or ""}, fh, indent=2)

        def shutdown(self) -> None:
            try:
                if self._store is not None:
                    self._store.flush()
            except Exception:                                # noqa: BLE001
                pass

    return InspeximusMemoryProvider


def provider_class():
    """The provider class, or None when Hermes is not importable."""
    base = _base()
    return None if base is None else _make_class(base)


def register(ctx=None):
    """Entry point for the `hermes_agent.memory_providers` group.

    Hermes passes a context object; it is accepted and unused so a later Hermes that passes more
    does not break this signature. Returns the provider INSTANCE, and returns None rather than
    raising when Hermes is absent, because a plugin that raises during registration takes the host
    down with it.
    """
    cls = provider_class()
    return None if cls is None else cls()
