"""Data-subject rights over an agent's memory: access (GDPR Art. 15) and rectification (Art. 16).

Erasure (Art. 17) has lived in the core since 1.13 as `forget_subject` plus the erasure certificate.
This module adds the two rights that come before it in a real request, on the same resolver
`forget_subject` uses, so the set of records an access request returns is the set an erasure
request would remove: no second selector to disagree with the first.

    from inspeximus.subject_rights import export_subject, rectify

    pkg = export_subject(m, "crm/alice", ledger=led)      # every record about alice, with provenance
    rec = rectify(m, key="alice::phone", text="alice's phone is +200", actor="dpo", reason="DSAR-17",
                  subject="crm/alice", ledger=led)         # a keyed correction with a rights receipt

Both write a `rights` entry into the action ledger when one is passed, so an auditor reads the
request, the records it touched and the memory state at that moment from one chain. The export
carries a manifest hash over its own canonical content; the ledger entry records that hash, so an
export handed to a subject can be matched to the ledger later.

What this does not do: it does not find a subject by free text. A record is about a subject when its
source resolves to that subject, directly or through inherited taint, exactly as erasure decides it.
Records that mention a person only in their text, with a different or absent source, are not
returned, and the export says so in `scope`.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

__all__ = ["export_subject", "rectify", "RIGHTS_EVENTS"]

RIGHTS_EVENTS = ("export", "rectify")


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canon(obj)).hexdigest()


def export_subject(store, subject: str, ledger=None, allow_ambiguous: bool = False,
                   include_text: bool = True, actor: str | None = None, request_id: str | None = None) -> dict:
    """Everything the store holds about `subject`, for an Art. 15 access request.

    Returns a dict with the records (direct and inherited), each record's provenance and, when it is
    keyed, its correction history; the erasure tombstones already recorded for the subject; the
    ledger entries whose recall window contained one of these records (the actions taken while the
    subject's data was in play); and a `manifest_sha256` over the canonical content. With a ledger,
    one `rights:export` entry is appended carrying the manifest hash and the record count.

    Raises `AmbiguousSubject` when the subject collides with another under canonicalisation, the
    same refusal `forget_subject` makes, unless `allow_ambiguous=True`."""
    cand, ids, collisions = store._resolve_subject(subject, allow_ambiguous=allow_ambiguous, destructive=True)
    if collisions and not allow_ambiguous:
        raise store._ambiguous_error(subject, collisions, "export_subject")
    preview = store._erasure_preview(subject, cand, ids, collisions=collisions, allow_ambiguous=allow_ambiguous)
    by_id = {r["id"]: r for r in store.items}
    direct_ids = {row["id"] for row in preview.get("sample", []) if row["why"] == "direct"}
    records = []
    for rid in sorted(ids):
        r = by_id.get(rid)
        if r is None:
            continue
        row = {"id": rid, "key": r.get("key"), "status": r.get("status"), "mtype": r.get("mtype"),
               "ts": r.get("ts"), "source": store._raw_source(r) if hasattr(store, "_raw_source") else None,
               "why": "direct" if rid in direct_ids or _is_direct(store, r, cand) else "inherited"}
        if include_text:
            row["text"] = r.get("text")
            row["object"] = r.get("object")
        try:
            row["provenance"] = store.provenance(id=rid)
        except Exception as e:  # noqa: BLE001 - the export must not fail on one record's provenance
            row["provenance"] = {"error": f"{type(e).__name__}: {e}"}
        if r.get("key") and r.get("status") == "active":      # once per key, on the current record
            try:
                row["history"] = [{"id": h.get("id"), "status": h.get("status"), "ts": h.get("ts"),
                                   **({"text": h.get("text")} if include_text else {})}
                                  for h in store.history(r["key"])]
            except Exception:
                row["history"] = None
        records.append(row)

    tombstones = []
    try:
        rep = store.erasure_report()
        for t in (rep.get("tombstones") or rep.get("recent") or []):
            if isinstance(t, dict) and (t.get("subject") == subject or t.get("subject") in cand):
                tombstones.append(t)
    except Exception:
        pass

    actions = []
    if ledger is not None:
        idset = set(ids)
        for e in ledger.entries():
            ms = e.get("memory_state") or {}
            hit = sorted(idset & set(ms.get("recalled") or []))
            if hit:
                actions.append({"seq": e["seq"], "kind": e.get("kind", "action"), "action": e.get("action"),
                                "ts": e.get("ts"), "recalled_about_subject": hit})

    body = {
        "kind": "inspeximus.subject_export/1",
        "subject": subject,
        "resolved_as": sorted(cand),
        "generated_at": time.time(),
        "request_id": request_id,
        "records": records,
        "counts": {"records": len(records),
                   "direct": sum(1 for r in records if r["why"] == "direct"),
                   "inherited": sum(1 for r in records if r["why"] == "inherited"),
                   "tombstones": len(tombstones), "actions": len(actions)},
        "tombstones": tombstones,
        "actions": actions,
        "ambiguous_with": preview.get("ambiguous_with"),
        "scope": "Records whose source resolves to the subject, directly or through inherited taint, as "
                 "erasure resolves them. Records that mention the subject only in free text under another "
                 "source are not included. Stores registered as erasure targets are not read here.",
    }
    body["manifest_sha256"] = _sha({k: v for k, v in body.items() if k != "manifest_sha256"})
    if ledger is not None:
        entry = ledger.record("rights:export", inputs={"subject": subject, "request_id": request_id},
                              status="ok", actor=actor, kind="rights",
                              extra={"event": "export", "subject": subject, "request_id": request_id,
                                     "manifest_sha256": body["manifest_sha256"],
                                     "n_records": len(records), "record_ids": sorted(ids)})
        body["ledger_entry"] = {"seq": entry["seq"], "hash": entry["hash"]}
    return body


def _is_direct(store, r: dict, cand: set) -> bool:
    raw = store._raw_source(r) if hasattr(store, "_raw_source") else None
    if not raw:
        return False
    return type(store)._canon_source(raw) in cand or raw in cand


def rectify(store, key: str, text: str, actor: str, reason: str, ledger=None, subject: str | None = None,
            request_id: str | None = None, **remember_kwargs) -> dict:
    """An Art. 16 rectification: supersede the current value under `key` and record who asked for it
    and why. The correction itself is an ordinary keyed write, so every guard the store applies to a
    write applies here (echo guard, receipts, supersession). With a ledger, one `rights:rectify` entry
    binds the new record id, the retired record id and the memory receipt tail to the actor and reason.

    Returns {previous_id, new_id, key, receipt} and the ledger entry when one was written."""
    if not actor or not reason:
        raise ValueError("a rectification needs an actor (who asked or approved) and a reason")
    prev = None
    for r in store.items:
        if r.get("key") == key and r.get("status") == "active":
            prev = r
            break
    if subject is not None and "source" not in remember_kwargs:
        remember_kwargs["source"] = {"doc": subject}       # the shape erasure and attribution resolve on
    new_id = store.remember(text, key=key, **remember_kwargs)
    receipts = list(getattr(store, "_receipts", None) or [])
    out = {"key": key, "previous_id": prev["id"] if prev else None, "new_id": new_id,
           "memory_receipt": receipts[-1].get("hash") if receipts else None,
           "previous_status": None}
    for r in store.items:
        if prev and r.get("id") == prev["id"]:
            out["previous_status"] = r.get("status")
    if ledger is not None:
        entry = ledger.record("rights:rectify", inputs={"key": key, "reason": reason, "request_id": request_id},
                              status="ok", actor=actor, kind="rights",
                              extra={"event": "rectify", "subject": subject, "request_id": request_id,
                                     "key": key, "reason": reason, "previous_id": out["previous_id"],
                                     "new_id": new_id, "memory_receipt": out["memory_receipt"]})
        out["ledger_entry"] = {"seq": entry["seq"], "hash": entry["hash"]}
    return out
