"""Import a mem0 export into an inspeximus store, one receipt per record, source per user.

mem0's `Memory.get_all(filters={"user_id": ...}, top_k=..., show_expired=True)` returns
`{"results": [...]}` where each item carries `id`, `memory` (the text), `hash` (md5 of the text),
`created_at`, `updated_at`, and whichever of `user_id`, `agent_id`, `run_id`, `actor_id`, `role`,
`attributed_to`, `expiration_date` the payload held, plus `metadata` for anything else (read from
mem0 2.0.11, `Memory._get_all_from_vector_store`). Dump that dict to JSON and this module reads it;
mem0 itself is not imported, so the import runs with no mem0 dependency and no model. The export
recipe is the one mem0's maintainers give in discussion #5967; PR #6330 (open) proposes an
`export_session()` bundle in a different shape that this reader does not take yet.

What maps, and what does not:

- `memory`        -> `text`. Verbatim; mem0 already extracted it, nothing is re-extracted.
- `user_id`       -> `source={"doc": "mem0/user/<user_id>"}`, so `forget_subject("mem0/user/<id>")`
                     reaches every imported record that carried that user. `agent_id`, `run_id`,
                     `actor_id`, `role` and `attributed_to` are kept under `meta` and become tags,
                     never the subject: a subject is what a DSAR names, and a DSAR names a person.
- `created_at`    -> `valid_from` (event time), so `as_of()` answers as of mem0's own timestamp; the
                     write receipt's `ts` is the import time, which is when THIS store first held it.
- `id`, `hash`    -> `meta["mem0_id"]`, `meta["mem0_hash"]`, kept for tracing, not verified.
- `metadata`      -> `meta["mem0_metadata"]` verbatim. `metadata["key"]` is a convention of THIS
                     importer, not a mem0 field: when present it becomes the supersession key,
                     namespaced by the user (`<user_id>::<key>`) unless it already carries `::`, so
                     two users' `{"key": "phone"}` do not retire each other. mem0 has no
                     supersession key of its own, so without one every imported record is a
                     free-standing fact: a later correction retires nothing until you re-remember it
                     with a key.
- `expiration_date` -> a memory mem0 would no longer serve (its rule: the date is before today, UTC)
                     is skipped unless `include_expired`. A future expiry is kept as
                     `meta["mem0_expiration_date"]` and NOT enforced: inspeximus serves the record
                     until you forget it.
- `updated_at`    -> dropped. mem0 keeps an update history in its own SQLite table
                     (`history(memory_id)`), but `get_all()` exports the last value only, so there is
                     no history to import.

Identity and idempotence. Each imported item is identified by its `id`, or, when the export has none,
by `sha256(user_id, created_at, text)`. The store's live and superseded records are checked for the
identity, and so is a sidecar beside the store (`<store>.mem0-imported.json`) that keeps the sha256 of
every identity ever imported. The sidecar is what makes a re-import after an erasure safe: a
tombstone is content-free by design, so once `forget_subject` has removed a person's records the
store itself no longer knows their mem0 ids, and without the sidecar the same export would write the
person back. The sidecar holds hashes of opaque ids and no text.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date, datetime, timezone
from typing import Any


def _event_time(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _expired_for_mem0(value: Any, now: float) -> bool:
    """mem0 2.0.11, `_payload_is_expired`: `date.fromisoformat(str(expiration_date)) < today (UTC)`.
    A memory expiring today is still served today."""
    if not value:
        return False
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return False
    return d < datetime.fromtimestamp(now, tz=timezone.utc).date()


def identity_of(item: dict) -> str | None:
    mid = item.get("id")
    if isinstance(mid, str) and mid.strip():
        return mid.strip()
    text = item.get("memory")
    if not isinstance(text, str) or not text:
        return None
    return "sha256:" + hashlib.sha256(
        json.dumps([item.get("user_id"), item.get("created_at"), text]).encode("utf-8")).hexdigest()


def sidecar_path(store) -> str:
    return str(store.path) + ".mem0-imported.json"


def _read_sidecar(store) -> set[str]:
    p = sidecar_path(store)
    if not os.path.exists(p):
        return set()
    try:
        with open(p, encoding="utf-8") as fh:
            return set(json.load(fh).get("imported", []))
    except (OSError, ValueError):
        return set()


def _write_sidecar(store, hashes: set[str]) -> None:
    p = sidecar_path(store)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"format": "inspeximus-mem0-import/1", "imported": sorted(hashes)}, fh, indent=0)
    os.replace(tmp, p)


def _h(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def load_export(path: str) -> list[dict]:
    """A mem0 `get_all()` dict, a bare list of items, or one item per line."""
    with open(path, encoding="utf-8-sig") as fh:
        raw = fh.read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = [json.loads(ln) for ln in raw.splitlines() if ln.strip()]
    if isinstance(data, dict):
        data = data.get("results", data.get("memories", []))
    if not isinstance(data, list):
        raise ValueError("expected a mem0 get_all() dict, a list of memory items, or JSON lines")
    return data


def import_mem0(store, items: list[dict], include_expired: bool = False, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    in_store = {(r.get("meta") or {}).get("mem0_id") for r in store.items}
    ever = _read_sidecar(store)
    seen_in_export: set[str] = set()
    written, skipped = [], []
    for it in items:
        text = it.get("memory")
        ident = identity_of(it)
        if not isinstance(text, str) or not text:
            skipped.append({"id": it.get("id"), "why": "no memory text"})
            continue
        if ident in seen_in_export:
            skipped.append({"id": ident, "why": "duplicate id in export"})
            continue
        seen_in_export.add(ident)
        if ident in in_store:
            skipped.append({"id": ident, "why": "already imported"})
            continue
        if _h(ident) in ever:
            skipped.append({"id": ident, "why": "imported earlier and since erased"})
            continue
        if _expired_for_mem0(it.get("expiration_date"), now) and not include_expired:
            skipped.append({"id": ident, "why": "expired in mem0"})
            continue
        user = it.get("user_id")
        md = it.get("metadata") if isinstance(it.get("metadata"), dict) else {}
        meta = {"mem0_id": ident, "mem0_hash": it.get("hash"), "mem0_metadata": md or None,
                "mem0_expiration_date": it.get("expiration_date")}
        tags = []
        for k in ("agent_id", "run_id", "actor_id", "role", "attributed_to"):
            if it.get(k):
                meta["mem0_" + k] = it[k]
                tags.append(f"{k}:{it[k]}")
        key = md.get("key") if isinstance(md.get("key"), str) and md.get("key") else None
        if key and user and "::" not in key:
            key = f"{user}::{key}"
        rid = store.remember(
            text,
            key=key,
            tags=tags or None,
            source={"doc": f"mem0/user/{user}"} if user else None,
            valid_from=_event_time(it.get("created_at")),
            meta={k: v for k, v in meta.items() if v is not None},
        )
        written.append(rid)
        in_store.add(ident)
        ever.add(_h(ident))
    if written:
        _write_sidecar(store, ever)
    return {"written": written, "skipped": skipped,
            "without_subject": sum(1 for it in items
                                   if not it.get("user_id") and isinstance(it.get("memory"), str) and it.get("memory"))}
