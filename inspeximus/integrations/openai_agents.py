"""MnemoSession — a persistent `Session` backend for the OpenAI Agents SDK, backed by mnemo.

The OpenAI Agents SDK lets any object implement its `Session` protocol (get_items / add_items / pop_item /
clear_session, + a `session_id` attribute) and pass it as `Runner.run(..., session=...)`. This adapter is a
faithful, drop-in persistent backend (the same slot SQLiteSession/RedisSession fill), storing each
conversation item in a mnemo store so history survives process restarts.

    from agents import Agent, Runner
    from inspeximus.integrations.openai_agents import MnemoSession
    session = MnemoSession("user-42", path="sessions.json")
    Runner.run_sync(agent, "hi", session=session)

WHAT IT FAITHFULLY DOES (matches SQLiteSession semantics):
  - get_items(limit=None) -> all items oldest-first; limit -> the latest N, still oldest-first.
  - add_items(items)      -> append (no-op on []).
  - pop_item()            -> remove + return the most recent item, or None if empty.
  - clear_session()       -> delete every item for THIS session_id.
Items are stored VERBATIM in each memory's meta and returned unchanged; the memory `text` is only a short
searchable shadow. One store can hold many sessions (namespaced by session_id), like SQLite's session column.

HONEST DIFFERENTIATOR (do not overclaim). A `Session` is a VERBATIM turn log, so mnemo's supersession /
echo_guard (which key on FACTS) do NOT automatically "clean" replayed messages — for poison-resistant FACT
memory use mnemo's core `remember(key=…, object=…)` / `recall()` alongside this session. What this backend
DOES add for free over a plain SQLite session, from inspeximus's governance layer:
  - RIGHT-TO-ERASURE across a user's turns: `session.forget_subject()` (or `store.forget_subject(user)`)
    hard-deletes the user's items AND leaves a signed, content-free deletion tombstone, so an erasure is
    provable and does not read as tampering (see mnemo.forget_subject / verify_writes).
  - TAMPER-EVIDENT history: enable receipts on the store (`Mnemo(..., receipts=True, receipt_key=…)`) and
    `store.verify_writes()` proves the turn log was not edited out-of-band.
Zero extra dependencies; the OpenAI Agents SDK is matched structurally and never imported here.
"""
from __future__ import annotations
import json
from typing import Any


class MnemoSession:
    """Persistent OpenAI-Agents `Session` backed by a mnemo store (one store, many sessions)."""

    def __init__(self, session_id: str, path: str | None = None, store: Any = None, extractor=None):
        """Pass a `path` (a mnemo store is created/opened there) OR an existing mnemo `store` to share one
        store across sessions. `session_id` namespaces this conversation within the store. OPT-IN `extractor`
        (text -> (key, object)) auto-keys turns so the store supersedes corrected facts across the session;
        get_items() still replays the raw turn log (a Session is verbatim), but a fact recall over the store
        is then current-truth. See Mnemo.extractor."""
        self.session_id = str(session_id)
        if store is None:
            from inspeximus import Mnemo
            store = Mnemo(path=path)
        self.store = store
        if extractor is not None:
            self.store.extractor = extractor
        self._src = {"doc": "oai-session:" + self.session_id}   # canonical source -> enables forget_subject

    # ── internal: this session's items as (seq, record) sorted oldest-first ──
    def _rows(self):
        rows = [(int((r.get("meta") or {}).get("seq", 0)), r) for r in self.store.items
                if r.get("status") == "active" and (r.get("meta") or {}).get("oai_session") == self.session_id]
        rows.sort(key=lambda sr: sr[0])
        return rows

    def _next_seq(self):
        rows = self._rows()
        return (rows[-1][0] + 1) if rows else 0

    # ── the Session protocol (async, as the SDK expects) ──
    async def get_items(self, limit: int | None = None) -> list[dict]:
        rows = self._rows()
        if limit is not None:
            rows = rows[-int(limit):] if limit else []
        return [(r.get("meta") or {}).get("item") for _, r in rows]

    async def add_items(self, items: list[dict]) -> None:
        if not items:
            return
        seq = self._next_seq()
        for it in items:
            shadow = self._shadow(it)
            self.store.remember(shadow, source=dict(self._src),
                                meta={"oai_session": self.session_id, "seq": seq, "item": it})
            seq += 1

    async def pop_item(self) -> dict | None:
        rows = self._rows()
        if not rows:
            return None
        _, rec = rows[-1]
        item = (rec.get("meta") or {}).get("item")
        self.store.forget(ids=[rec["id"]])
        return item

    async def clear_session(self) -> None:
        ids = [r["id"] for _, r in self._rows()]
        if ids:
            self.store.forget(ids=ids)

    # ── governance bonus (mnemo-specific, honest) ──
    def forget_subject(self, request_id: str | None = None) -> dict:
        """Right-to-erasure for THIS session's user: hard-delete every turn of this session and leave a
        signed, content-free deletion tombstone (provable erasure that doesn't read as tampering)."""
        return self.store.forget_subject(self._src["doc"], request_id=request_id)

    @staticmethod
    def _shadow(item: Any) -> str:
        """A short, searchable text shadow of a conversation item; the verbatim item lives in meta['item']."""
        if isinstance(item, dict):
            c = item.get("content")
            if isinstance(c, str):
                return (item.get("role", "msg") + ": " + c)[:800]
        try:
            return json.dumps(item, ensure_ascii=False)[:800]
        except Exception:
            return str(item)[:800]
