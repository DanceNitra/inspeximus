"""MnemoStore — a LangGraph `BaseStore` backed by mnemo (with queryable value history).

LangGraph agents persist long-term state through a `BaseStore` (`put`/`get`/`search`/`delete` over
`(namespace, key)`), and LangMem sits on top of any BaseStore — so one adapter reaches both. A custom store
implements `batch`/`abatch`; the high-level accessors delegate to them.

The honest differentiator vs the built-in `InMemoryStore`: LangGraph's own store is last-write-wins with NO
history — a second `put` on the same key silently overwrites the first, and the old value is gone.
`MnemoStore` gives identical put/get/search/delete semantics BUT keeps the superseded values on mnemo's
bi-temporal supersession ledger, so you additionally get `history(namespace, key)` (every value the key has
held, in order), point-in-time reads, tamper-evident receipts, and `forget_subject` erasure — governance a
plain KV store can't offer. (mnemo's supersession itself is not the novelty here: BaseStore already overwrites
on same-key put; the novelty is that mnemo *keeps and can prove* what the value used to be.)

    from inspeximus.integrations.langgraph import MnemoStore
    store = MnemoStore(path="lg.json")
    store.put(("user", "42"), "timezone", {"tz": "UTC"})
    store.put(("user", "42"), "timezone", {"tz": "PST"})   # overwrites, like InMemoryStore
    store.get(("user", "42"), "timezone").value            # -> {"tz": "PST"}
    store.history(("user", "42"), "timezone")              # -> [{"tz": "UTC"}, {"tz": "PST"}]  (mnemo-only)

Importing this module imports LangGraph (it subclasses BaseStore) — it is an opt-in extra; `import inspeximus`
never pulls it in, so the core stays zero-dependency.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any

import base64
from langgraph.store.base import (
    BaseStore, Item, SearchItem, GetOp, PutOp, SearchOp, ListNamespacesOp,
)
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple

_b = lambda x: base64.b64encode(x).decode()      # bytes -> ascii for JSON meta
_ub = lambda s: base64.b64decode(s.encode())     # back to bytes


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts or 0, tz=timezone.utc)


class MnemoStore(BaseStore):
    """LangGraph BaseStore over a mnemo store; keeps queryable value history the built-in store discards."""

    def __init__(self, path: str | None = None, store: Any = None):
        if store is None:
            from inspeximus import Mnemo
            store = Mnemo(path=path)
        self.store = store

    @staticmethod
    def _mkey(namespace: tuple[str, ...], key: str) -> str:
        return "lg::" + "/".join(namespace) + "::" + key

    def _active(self, namespace, key):
        mk = self._mkey(namespace, key)
        rows = [r for r in self.store.items if r.get("status") == "active" and (r.get("meta") or {}).get("mkey") == mk]
        return rows[-1] if rows else None

    def _to_item(self, rec) -> Item:
        m = rec.get("meta") or {}
        return Item(value=m.get("value", {}), key=m.get("lg_key", ""),
                    namespace=tuple(m.get("lg_ns", ())), created_at=_dt(rec.get("ts", 0)),
                    updated_at=_dt(rec.get("ts", 0)))

    def batch(self, ops) -> list:
        results: list = []
        for op in ops:
            if isinstance(op, GetOp):
                rec = self._active(op.namespace, op.key)
                results.append(self._to_item(rec) if rec else None)
            elif isinstance(op, PutOp):
                mk = self._mkey(op.namespace, op.key)
                if op.value is None:                                   # LangGraph convention: value=None deletes
                    ids = [r["id"] for r in self.store.items
                           if r.get("status") == "active" and (r.get("meta") or {}).get("mkey") == mk]
                    if ids:
                        self.store.forget(ids=ids)
                else:
                    self.store.remember((op.key + " " + json.dumps(op.value, ensure_ascii=False, sort_keys=True))[:2000],
                                        key=mk, object=json.dumps(op.value, sort_keys=True),
                                        meta={"mkey": mk, "lg_ns": list(op.namespace), "lg_key": op.key,
                                              "value": op.value})
                results.append(None)
            elif isinstance(op, SearchOp):
                pref = "lg::" + "/".join(op.namespace_prefix)
                pool = [r for r in self.store.items if r.get("status") == "active"
                        and str((r.get("meta") or {}).get("mkey", "")).startswith(pref)]
                if op.query:
                    ranked = self.store.recall(op.query, k=op.limit + op.offset + 10)
                    order = {h["id"]: i for i, h in enumerate(ranked)}
                    pool = [r for r in pool if r["id"] in order]
                    pool.sort(key=lambda r: order[r["id"]])
                    scored = [(r, 1.0 / (1 + order[r["id"]])) for r in pool]
                else:
                    scored = [(r, None) for r in pool]
                page = scored[op.offset: op.offset + op.limit]
                results.append([SearchItem(namespace=tuple((r.get("meta") or {}).get("lg_ns", ())),
                                           key=(r.get("meta") or {}).get("lg_key", ""),
                                           value=(r.get("meta") or {}).get("value", {}),
                                           created_at=_dt(r.get("ts", 0)), updated_at=_dt(r.get("ts", 0)),
                                           score=score) for r, score in page])
            elif isinstance(op, ListNamespacesOp):
                seen = []
                for r in self.store.items:
                    if r.get("status") != "active":
                        continue
                    ns = tuple((r.get("meta") or {}).get("lg_ns", ()))
                    if ns and ns not in seen:
                        seen.append(ns)
                results.append(seen[op.offset: op.offset + op.limit])
            else:
                results.append(None)
        return results

    async def abatch(self, ops) -> list:
        return self.batch(ops)

    # ── mnemo-only bonus: the history a plain KV store discards ──
    def history(self, namespace: tuple[str, ...], key: str) -> list[dict]:
        """Every value this (namespace, key) has held, oldest-first — including superseded ones the built-in
        InMemoryStore would have overwritten and lost. Backed by mnemo's bi-temporal supersession ledger."""
        mk = self._mkey(namespace, key)
        rows = [r for r in self.store.items if (r.get("meta") or {}).get("mkey") == mk]
        rows.sort(key=lambda r: r.get("valid_from", r.get("ts", 0)))
        return [(r.get("meta") or {}).get("value") for r in rows]


class MnemoSaver(BaseCheckpointSaver):
    """LangGraph `BaseCheckpointSaver` backed by mnemo — the THREAD-STATE half of LangGraph memory (MnemoStore is
    the long-term half). Persists checkpoints + pending writes so a graph can resume, with the SAME contract as
    SqliteSaver/PostgresSaver, but in a single zero-dependency mnemo JSON file (no DB, no server). Checkpoints and
    writes are stored as mnemo records (payloads serialized via LangGraph's own serde, base64 in meta), tagged
    `_langgraph` so they never pollute normal recall.

        from inspeximus.integrations.langgraph import MnemoSaver
        graph = builder.compile(checkpointer=MnemoSaver(path="threads.json"))

    Async methods delegate to the sync ones (a local file store has no real I/O concurrency to exploit); fine for
    single-process agents. Importing this module imports LangGraph — opt-in; `import inspeximus` never pulls it in."""

    def __init__(self, path: str | None = None, store: Any = None, serde: Any = None):
        super().__init__(serde=serde)
        if store is None:
            from inspeximus import Mnemo
            store = Mnemo(path=path)
        self.store = store

    @staticmethod
    def _cfg(config):
        c = (config or {}).get("configurable", {}) or {}
        return c.get("thread_id", ""), c.get("checkpoint_ns", ""), c.get("checkpoint_id")

    def _ckpts(self, thread=None, ns=None):
        rows = [r for r in self.store.items if (r.get("meta") or {}).get("kind") == "lg_checkpoint"]
        if thread is not None:
            rows = [r for r in rows if r["meta"]["thread"] == thread and r["meta"]["ns"] == (ns or "")]
        rows.sort(key=lambda r: r.get("ts", 0))
        return rows

    def _tuple(self, rec) -> CheckpointTuple:
        m = rec["meta"]
        checkpoint = self.serde.loads_typed((m["ctype"], _ub(m["cblob"])))
        metadata = self.serde.loads_typed((m["mtype"], _ub(m["mblob"])))
        cid, thread, ns = m["cid"], m["thread"], m["ns"]
        writes = []
        for w in self.store.items:
            wm = w.get("meta") or {}
            if (wm.get("kind") == "lg_write" and wm.get("thread") == thread
                    and wm.get("ns") == ns and wm.get("cid") == cid):
                writes.append((wm["task_id"], wm["channel"], self.serde.loads_typed((wm["wtype"], _ub(wm["wblob"])))))
        cur = {"configurable": {"thread_id": thread, "checkpoint_ns": ns, "checkpoint_id": cid}}
        parent = m.get("parent")
        parent_cfg = ({"configurable": {"thread_id": thread, "checkpoint_ns": ns, "checkpoint_id": parent}}
                      if parent else None)
        return CheckpointTuple(cur, checkpoint, metadata, parent_cfg, writes or None)

    def put(self, config, checkpoint, metadata, new_versions):
        thread, ns, _ = self._cfg(config)
        cid = checkpoint["id"]
        parent = (config or {}).get("configurable", {}).get("checkpoint_id")
        ctype, cblob = self.serde.dumps_typed(checkpoint)
        mtype, mblob = self.serde.dumps_typed(dict(metadata))
        self.store.remember(
            f"lg checkpoint {thread}/{ns}/{cid}", key=f"lgckpt::{thread}::{ns}::{cid}", tags=["_langgraph"],
            meta={"kind": "lg_checkpoint", "thread": thread, "ns": ns, "cid": cid, "parent": parent,
                  "ctype": ctype, "cblob": _b(cblob), "mtype": mtype, "mblob": _b(mblob)})
        self.store._save()
        return {"configurable": {"thread_id": thread, "checkpoint_ns": ns, "checkpoint_id": cid}}

    def put_writes(self, config, writes, task_id, task_path=""):
        thread, ns, cid = self._cfg(config)
        for idx, (channel, value) in enumerate(writes):
            wtype, wblob = self.serde.dumps_typed(value)
            self.store.remember(
                f"lg write {thread}/{ns}/{cid}/{task_id}/{idx}",
                key=f"lgwrite::{thread}::{ns}::{cid}::{task_id}::{idx}", tags=["_langgraph"],
                meta={"kind": "lg_write", "thread": thread, "ns": ns, "cid": cid, "task_id": task_id,
                      "task_path": task_path, "idx": idx, "channel": channel, "wtype": wtype, "wblob": _b(wblob)})
        self.store._save()

    def get_tuple(self, config):
        thread, ns, cid = self._cfg(config)
        rows = self._ckpts(thread, ns)
        if not rows:
            return None
        rec = next((r for r in rows if r["meta"]["cid"] == cid), None) if cid else rows[-1]
        return self._tuple(rec) if rec else None

    def list(self, config, *, filter=None, before=None, limit=None):
        thread = (config or {}).get("configurable", {}).get("thread_id")
        ns = (config or {}).get("configurable", {}).get("checkpoint_ns", "") if config else None
        rows = self._ckpts(thread, ns) if thread is not None else self._ckpts()
        rows = list(reversed(rows))                                  # newest-first
        before_id = before["configurable"]["checkpoint_id"] if before else None
        seen_before = before_id is None
        n = 0
        for rec in rows:
            if before_id and not seen_before:
                if rec["meta"]["cid"] == before_id:
                    seen_before = True
                continue
            t = self._tuple(rec)
            if filter and not all((t.metadata or {}).get(k) == v for k, v in filter.items()):
                continue
            yield t
            n += 1
            if limit and n >= limit:
                break

    def delete_thread(self, thread_id):
        ids = [r["id"] for r in self.store.items
               if (r.get("meta") or {}).get("kind") in ("lg_checkpoint", "lg_write")
               and (r.get("meta") or {}).get("thread") == thread_id]
        if ids:
            self.store.forget(ids=ids)
            self.store._save()

    # async delegates (local file store: no real concurrency to exploit)
    async def aput(self, *a, **k):
        return self.put(*a, **k)

    async def aput_writes(self, *a, **k):
        return self.put_writes(*a, **k)

    async def aget_tuple(self, *a, **k):
        return self.get_tuple(*a, **k)

    async def alist(self, config, **k):
        for t in self.list(config, **k):
            yield t

    async def adelete_thread(self, thread_id):
        return self.delete_thread(thread_id)
