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

    from mnemo.integrations.langgraph import MnemoStore
    store = MnemoStore(path="lg.json")
    store.put(("user", "42"), "timezone", {"tz": "UTC"})
    store.put(("user", "42"), "timezone", {"tz": "PST"})   # overwrites, like InMemoryStore
    store.get(("user", "42"), "timezone").value            # -> {"tz": "PST"}
    store.history(("user", "42"), "timezone")              # -> [{"tz": "UTC"}, {"tz": "PST"}]  (mnemo-only)

Importing this module imports LangGraph (it subclasses BaseStore) — it is an opt-in extra; `import mnemo`
never pulls it in, so the core stays zero-dependency.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any

from langgraph.store.base import (
    BaseStore, Item, SearchItem, GetOp, PutOp, SearchOp, ListNamespacesOp,
)


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts or 0, tz=timezone.utc)


class MnemoStore(BaseStore):
    """LangGraph BaseStore over a mnemo store; keeps queryable value history the built-in store discards."""

    def __init__(self, path: str | None = None, store: Any = None):
        if store is None:
            from mnemo import Mnemo
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
