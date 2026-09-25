"""InspeximusMemoryProvider — a Hermes Agent memory provider backed by inspeximus.

Hermes Agent (NousResearch) lets an install choose ONE external memory provider, selected by name in
`memory.provider`. It ships eight of them, mem0 and supermemory among them, and the bundled directory
is closed to new entries. The route that stays open needs nobody's permission: a pip-installed
package publishes an entry point in the `hermes_agent.memory_providers` group, and Hermes discovers
it. That is what this module is, and it is why installing inspeximus INTO HERMES' OWN VENV is the
whole install. Not the PyPI package `hermes-agent`: that is 0.19.0, which reads providers from
directories only and never loads this one (measured 2026-09-25). See docs/INTEGRATIONS.md.

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
    inspeximus_forget_me  erase what the person speaking in THIS turn wrote, in a shared session

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
    {
        "name": "inspeximus_forget_me",
        "description": (
            "Erase everything this memory holds from the person speaking in the current turn, and "
            "record that the erasure happened. Use it when someone in a shared conversation asks "
            "you to forget what you know about them. It needs no subject: the store knows who "
            "wrote each memory. It refuses when the host did not say who is speaking, rather than "
            "guessing."
        ),
        "parameters": {"type": "object", "properties": {}},
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
            self._hermes_home = ""
            self._last_recalled = 0
            self._prefetch_query = ""
            self._author_id = ""

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
            self._hermes_home = str(kwargs.get("hermes_home") or "")
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

        def _recall_lines(self, query: str) -> List[str]:
            """Current-truth lines for a query, or [] for anything that goes wrong.

            Never raises. Everything that calls it sits on a turn boundary, and a memory provider
            that can end a turn with a traceback is worse than one that returns nothing.
            """
            if self._store is None or not (query or "").strip():
                return []
            try:
                self._store.refresh()                      # see what a peer process wrote (2.28.0)
                hits = self._store.recall(query, k=self._k)
            except Exception:                                # noqa: BLE001 - never break a turn
                return []
            return [str(h.get("text", "")).strip() for h in hits if str(h.get("text", "")).strip()]

        def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
            """Records the query and recalls NOTHING. Speculating here was measured, and it lost.

            The hook reads as an obvious win, and for a network-backed provider it is one. It is not
            one here, because of what the host actually queues. `run_agent.py` queues the text of the
            turn that just ENDED, and `turn_context.py` then prefetches the NEW user message at the
            start of the next turn. The two strings differ on every turn that is not a literal
            repeat, so a result recalled for the queued text can never answer the question asked.

            Serving it anyway is the one thing this store must not do: it would inject the previous
            question's memories as if they were this question's.

            So the only thing a speculative recall can produce is a second scan of the same store,
            running while the turn does its own. Measured over 40 turns per arm
            (`probes/what_a_turn_pays_for_recall_before_and_after_the_queue.py`), median per turn:

                records   as shipped   speculating   penalty
                    100        0.245         0.244     -0.4%
                  1,000        2.340         2.251     -3.8%
                 10,000       35.274        50.624    +43.5%

            The shape is the argument. Speculation is free where a recall is already cheap enough
            that nobody notices it, and it costs 15 ms where a recall is slow enough to matter. Any
            rule that keeps it only where it is safe keeps it only where it cannot help. Version
            2.27.3 shipped it and was slower than 2.27.2 in Hermes' own flow; this is that removal.

            Both columns come from one interleaved run, because the baseline moves between runs on
            a shared machine: an earlier pass measured the same penalty as +79.5% against a 30.1 ms
            baseline. The comparison holds inside a run; the absolute numbers do not travel.

            The query is kept so `recall_status` and a future host that queues the UPCOMING question
            have something to read. If Hermes ever passes the next query here, restore the thread and
            re-run the probe above: it still carries the speculating variant, and its `spec-hit` arm
            measured 0.003 ms against 35.274 ms.
            """
            self._prefetch_query = (query or "").strip()

        def prefetch(self, query: str, *, session_id: str = "") -> str:
            """Recall for the coming turn, with superseded values already excluded.

            Returns "" rather than a header with nothing under it: the base class treats "" as "no
            context", and an empty block still costs tokens while reading to the model as though
            memory had been consulted and had nothing, which is a different claim.
            """
            lines = self._recall_lines(query)
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
            """Where the record came from, carried on every write so erasure can reach it.

            `author` is present only when the host said who wrote the turn. A shared session is
            several people writing into one memory, and an erasure request comes from one of them.
            Without this field, "forget what you know about me" in a shared session can only be
            answered by subject text, and a subject the other participants also mention is then
            either over-erased or not erased at all.
            """
            src = {"doc": "hermes-agent::" + (self._session_id or "session")}
            if self._author_id:
                src["author"] = self._author_id
            return src

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
                if tool_name == "inspeximus_forget_me":
                    # Refuses without an author rather than erasing by session, because a session
                    # in a shared conversation holds several people's memories and this request
                    # came from one of them. Erasing the others' would be the wrong kind of wrong.
                    who = self._author_id
                    if not who:
                        return json.dumps({"ok": False, "error": (
                            "the host did not say who is speaking in this turn, so there is "
                            "nothing to scope the erasure to; use inspeximus_forget with a subject")})
                    out = self._store.forget(
                        where=lambda r: ((r.get("source") or {}).get("author") == who),
                        basis="the author asked, in a shared session, to be forgotten")
                    return json.dumps({"ok": True, "author": who, "result": out})
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

        # -- the two hooks that matter most to a store with a correction channel ---------

        def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
            """Hand the summariser the values that are CURRENT, just before the transcript goes.

            MEASURED, AND THE ARGUMENT THIS DOCSTRING USED TO MAKE DID NOT SURVIVE IT. The claim was
            that compaction is where a corrected value comes back, because the transcript holds both
            values and nothing marks one as retired. Run through Hermes' own summary prompt on
            deepseek-v4-flash, 11 scenarios x 2 repeats, judged by what a fresh reader of the summary
            answers (probes/does_the_pre_compress_block_stop_the_summariser_carrying_a_retired_value.py,
            2026-09-13):

                transcript alone, explicit "Correction:"      22 of 22 current
                transcript alone, plain restatement            22 of 22 current
                transcript + this block                        43 of 44 current, 1 empty answer
                transcript + a block naming the RETIRED value   5 of 44 current, 39 stale

            So on this model the summariser needs no help, and the block is not a correction. It is
            an authority: the reader believes it over the transcript, in both directions. With the
            block naming the wrong value, a plain restatement in the transcript lost 22 of 22.

            The hook stays, because with a true block it did no harm in 66 of 66 rows and this store
            keeps a retired value out of recall by key. What it must never do is speak from a store
            that is wrong, and the one path that can make it wrong is `on_memory_write` mirroring a
            host `replace` that carries a stale value. That is the risk to hold in mind, not the one
            the first version of this docstring described.

            Returns "" when nothing is known about what is being compressed, rather than a heading
            with nothing under it.

            Read against the eight providers Hermes bundles (checked 2026-09-13): one implements
            this hook, and it uses it in the other direction, harvesting the transcript into its own
            store and returning "" to the summary prompt. Returning text into that prompt needs a
            store that can say which of two values in the transcript is the retired one, which is
            what supersession by key is.
            """
            if self._store is None or not messages:
                return ""
            text = " ".join(str(m.get("content", "")) for m in messages[-12:])[-2000:]
            lines = self._recall_lines(text)
            if not lines:
                return ""
            # No "above" or "below": the host decides where this block lands in the summary prompt,
            # and a block that names its own position is wrong the first time that changes.
            return ("Durable memory says these values are current. Where the conversation being "
                    "summarized contains an earlier version of any of them, that version was "
                    "corrected and must not be carried forward:\n"
                    + "\n".join("- " + t for t in lines))

        #: Namespace for rows this mirror wrote. It is part of the key AND checked against the
        #: source on removal, so `remove` can never reach a record the agent stored through its own
        #: tools.
        MIRROR_KEY = "hermes-memory::"

        #: Content longer than this is skipped rather than truncated. A truncated fact is a WRONG
        #: fact, and a store whose pitch is that recall stays correct must not manufacture one.
        MIRROR_MAX_CHARS = 8000

        def on_memory_write(self, action: str, target: str, content: str,
                            metadata: Optional[Dict[str, Any]] = None) -> None:
            """Mirror a built-in memory-tool write, mapping its verb onto the correction channel.

            `target` is a DOCUMENT BUCKET, `memory` or `user`, not a per-fact identifier, so
            `replace` is a whole-document rewrite. Keying on the target reproduces exactly that: the
            new content retires the previous content of the same document, and the old version stays
            in the history where `revert` can reach it. Mirroring a replace as a plain append would
            leave this store recalling a value the built-in memory has already dropped, which is the
            divergence a user discovers months later through a stale recall.

            `remove` hard-deletes, because the built-in tool removed the content and a demoted row
            would still be readable with `include_superseded`. Only rows carrying this mirror's key
            AND this provider's source are matched.

            Failures are swallowed. This is a mirror, and a mirror that can break the write it
            mirrors is worse than no mirror.
            """
            if self._store is None:
                return
            body = (content or "").strip()
            key = self.MIRROR_KEY + (target or "memory")
            try:
                if action == "remove":
                    # Matched on the source PREFIX, not on this session's full source. The built-in
                    # memory is not session-scoped, so a removal in session B must still reach the
                    # row session A mirrored, and an exact-source match would silently miss it.
                    self._store.forget(
                        where=lambda r: (
                            r.get("key") == key
                            and str(((r.get("source") or {}).get("doc") or ""))
                            .startswith("hermes-agent::")
                        ),
                        basis="hermes built-in memory removed this content",
                    )
                    return
                if not body or len(body) > self.MIRROR_MAX_CHARS:
                    return
                self._store.remember(body, key=key if action == "replace" else None,
                                     mtype="fact", source=self._source())
            except Exception:                                # noqa: BLE001 - a mirror never raises
                pass

        # -- session identity and backup ------------------------------------------------

        def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
            """Read who wrote THIS turn, so the writes it causes are attributed to that person.

            The base class is explicit that a shared session carries several participants and a
            provider keying durable state on identity must read it per turn. Reading it once at
            `initialize` would attribute every later write to whoever opened the session.

            A host that does not pass the author (the build installed here, 0.21.1, does not; the
            2026-09-13 upstream head does) leaves the field empty, and `_source` then omits it
            rather than writing a placeholder that a later erasure would match by accident.
            """
            author = kwargs.get("author_id")
            self._author_id = str(author).strip() if author else ""

        def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "",
                              reset: bool = False, rewound: bool = False, **kwargs) -> None:
            """Rebind to the reassigned session, and drop anything recalled for the old one.

            `/resume`, `/branch`, `/reset` and compaction reassign the session id in the same
            process, with no teardown. Two things go wrong without this hook, and both are silent.
            Every write carries the session in its source, so later records would be attributed to
            the session the user left, and `inspeximus_forget` would then miss them. And a recall
            queued for the previous conversation would be served into the new one as though it had
            been recalled for it.

            An in-flight background recall is discarded rather than waited for: clearing the query
            it was queued under makes its publish check fail, so it cannot land in the new session.
            """
            self._session_id = new_session_id or ""
            self._last_recalled = 0
            self._prefetch_query = ""
            self._author_id = ""

        def backup_paths(self) -> List[str]:
            """Store files that `hermes backup` cannot find on its own.

            The default store lives under `hermes_home`, which the host already backs up, so the
            honest answer there is an empty list. A path the user configured points somewhere else,
            and that is the one a backup misses.

            Must work before `initialize()` and without a network call, per the base class, so this
            reads a configured path and never opens a store to find one.
            """
            path = self._explicit_path or getattr(self._store, "path", None)
            if not path:
                return []
            full = os.path.abspath(str(path))
            if self._hermes_home:
                home = os.path.abspath(self._hermes_home)
                if os.path.commonpath([full, home]) == home:
                    return []                            # the host already backs this up
            return [full]

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

    Hermes documents this as `register(ctx)` calling `ctx.register_memory_provider(provider)`, and
    that is what it does when a context with that method is passed. It ALSO returns the instance,
    because the host's loader (plugins/memory/__init__.py, read at 0.21.1) tries `loaded()` first
    and accepts a returned MemoryProvider, and the first version of this function relied on that
    fallback alone. A provider that satisfies only the undocumented path registers nothing the day
    the documented one becomes the only one.

    Returns None rather than raising when Hermes is absent, because a plugin that raises during
    registration takes the host down with it.
    """
    cls = provider_class()
    if cls is None:
        return None
    provider = cls()
    hook = getattr(ctx, "register_memory_provider", None)
    if callable(hook):
        try:
            hook(provider)
        except Exception:                                   # noqa: BLE001 - never take the host down
            pass
    return provider
