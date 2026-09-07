#!/usr/bin/env python3
"""Migrate mem0 memories into inspeximus — with correction-chain reconstruction.

Reads mem0's own operation ledger (SQLite `history.db`) plus the live memory
export, and rebuilds each correction chain as an inspeximus supersession key,
so a correction mem0 recorded survives migration as a first-class supersession
— not as two unrelated memories.

mem0 ledger schema, verified against the mem0 OSS source (audit clone,
2026-09-07; `mem0/memory/storage.py` `_create_history_table`):

    history(id, memory_id, old_memory, new_memory, event, created_at,
            updated_at, is_deleted, actor_id, role)  -- event in {ADD, UPDATE, DELETE}

mem0's update writer (`mem0/memory/main.py` `_update_memory`) records
`add_history(memory_id, prev_value, data, "UPDATE", created_at=..., updated_at=...)`,
so events sharing one `memory_id` are a real correction chain: ADD -> UPDATE* -> DELETE.

Honest scope, stated rather than hidden:
  - Supersession keys are reconstructed as `mem0:{memory_id}`. mem0 has no key
    concept, so "same memory_id was updated" is the only faithful mapping.
  - mem0 memories are LLM-extracted facts; they import as raw text. Extraction
    internals (lemmatized BM25 text, entity graph) are not carried.
  - mem0 ran with the default in-memory history DB (`:memory:`)? Then the ledger
    is gone. Chains are unrecoverable; memories import unkeyed and the report
    says so explicitly instead of guessing.
  - mtype defaults to "semantic" and value defaults to 2.0 for every imported
    record. mem0 has no faithful mapping to either; both are CLI-settable.
  - The optional live parity check needs mem0ai installed and a live Memory
    instance. Without it the tool verifies end-state reconstruction only.

Usage:
    python migrate_mem0.py --current current.json --store migrated.json \
        [--history history.db] [--value 2.0] [--mtype semantic] \
        [--report report.json] [--force]

    current.json = the JSON returned by
        mem0_memory.get_all(filters={"user_id": u}, top_k=1_000_000)
    (a bare list or a {"results": [...]} wrapper are both accepted).

Safety: refuses to write into an existing non-empty store without --force.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CHAIN_TAG = "mem0-migration"
UNKEYED_TAG = "mem0-unkeyed"


def read_history(db_path: str) -> tuple[list[dict], list[str]]:
    """Read mem0's history ledger read-only. Returns (events, notes)."""
    notes: list[str] = []
    p = Path(db_path)
    if not p.exists():
        return [], [f"history DB not found at {db_path}: chains unrecoverable"]
    con = sqlite3.connect(f"file:{p.resolve().as_posix()}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(history)")}
        required = {"memory_id", "old_memory", "new_memory", "event", "created_at"}
        if not required <= cols:
            notes.append(f"history table missing required columns "
                         f"{sorted(required - cols)}; found {sorted(cols)} — importing unkeyed")
            return [], notes
        rows = con.execute(
            "SELECT memory_id, old_memory, new_memory, event, created_at, updated_at, "
            "actor_id, role FROM history ORDER BY created_at, id").fetchall()
    finally:
        con.close()
    return [{"memory_id": r[0], "old_memory": r[1], "new_memory": r[2],
             "event": (r[3] or "").upper(), "created_at": r[4], "updated_at": r[5],
             "actor_id": r[6], "role": r[7]} for r in rows], notes


def _to_epoch(stamp) -> float | None:
    if stamp in (None, ""):
        return None
    if isinstance(stamp, (int, float)):
        return float(stamp)
    try:
        d = _dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def load_current(path: str) -> tuple[list[dict], list[str]]:
    """Load the mem0 get_all export. Accepts a bare list or {"results": [...]}."""
    notes: list[str] = []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("results", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError(f"{path}: expected a list or a 'results' object")
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            notes.append(f"current[{i}] is not an object; skipped")
            continue
        text = (item.get("memory") or item.get("data") or item.get("text") or "").strip()
        if not text:
            notes.append(f"current[{i}] has no memory text; skipped")
            continue
        out.append({"id": item.get("id"), "text": text,
                    "user_id": item.get("user_id"), "created_at": item.get("created_at")})
    return out, notes


def _remember(store, text: str, key: str | None, value: float, mtype: str,
              uid: str | None, valid_from: float | None, ev: dict) -> str:
    meta = {"migrated_from": "mem0", "mem0_id": ev.get("memory_id"),
            "mem0_event": ev.get("event"), "mem0_actor_id": ev.get("actor_id"),
            "mem0_role": ev.get("role")}
    kw: dict = {"text": text, "tags": [CHAIN_TAG if key else UNKEYED_TAG],
                "value": value, "mtype": mtype, "meta": meta,
                "object": text if key else None,
                "user_id": uid, "valid_from": valid_from}
    if key:
        kw["key"] = key
    return store.remember(**{k: v for k, v in kw.items() if v is not None})


def _uid_for(ev: dict, by_id: dict) -> str | None:
    tgt = by_id.get(ev.get("memory_id"))
    return (tgt or {}).get("user_id")


def migrate(events: list[dict], current: list[dict], store_path: str, *,
            value: float = 2.0, mtype: str = "semantic", force: bool = False) -> dict:
    """Replay mem0's ledger into inspeximus, then reconcile against the live export."""
    target = Path(store_path)
    if target.exists() and target.stat().st_size > 0 and not force:
        raise SystemExit(f"refusing to overwrite existing store {target} (use --force)")
    if target.exists() and force:
        target.unlink()

    from inspeximus import Inspeximus
    store = Inspeximus(store_path)

    by_id = {c["id"]: c for c in current if c.get("id")}
    chains: dict[str, list[dict]] = {}
    for ev in events:
        chains.setdefault(ev["memory_id"], []).append(ev)

    report = {"store": str(target), "events": len(events), "chains": len(chains),
              "live_memories": len(current), "chains_imported": 0,
              "superseded_events": 0, "noop_events": 0, "deleted_events": 0,
              "reconciled": 0, "chain_end_missing": 0, "unkeyed": 0,
              "reconcile": [], "notes": [], "limits": []}

    for mid in sorted(chains, key=lambda m: (_to_epoch(chains[m][0]["created_at"]) or 0.0, m)):
        key = f"mem0:{mid}"
        cur_text: str | None = None
        xid: str | None = None
        for ev in chains[mid]:
            ts = _to_epoch(ev["updated_at"] or ev["created_at"])
            uid = _uid_for(ev, by_id)
            if ev["event"] == "ADD":
                text = (ev["new_memory"] or ev["old_memory"] or "").strip()
                if not text:
                    continue
                xid = _remember(store, text, key, value, mtype, uid, ts, ev)
                cur_text = text
            elif ev["event"] == "UPDATE":
                text = (ev["new_memory"] or "").strip()
                if not text:
                    report["notes"].append(f"chain {mid}: UPDATE with empty new_memory skipped")
                    continue
                if text == cur_text:
                    report["noop_events"] += 1
                    continue
                xid = _remember(store, text, key, value, mtype, uid, ts, ev)
                report["superseded_events"] += 1
                cur_text = text
            elif ev["event"] == "DELETE":
                if xid:
                    store.forget(ids=[xid])
                report["deleted_events"] += 1
                cur_text, xid = None, None
            else:
                report["notes"].append(f"chain {mid}: unknown event {ev['event']!r} skipped")
        tgt = by_id.get(mid)
        if tgt:
            if cur_text != tgt["text"]:
                _remember(store, tgt["text"], key, value, mtype, tgt.get("user_id"),
                          _to_epoch(tgt.get("created_at")), {"memory_id": mid})
                report["reconciled"] += 1
                report["reconcile"].append({"memory_id": mid, "replayed": cur_text, "live": tgt["text"]})
            report["chains_imported"] += 1
        else:
            report["chain_end_missing"] += 1
            report["notes"].append(f"chain {mid}: ends deleted or absent from the live export; not active")

    keyed = set(chains)
    for c in current:
        if c.get("id") and c["id"] not in keyed:
            _remember(store, c["text"], None, value, mtype, c.get("user_id"),
                      _to_epoch(c.get("created_at")), {"memory_id": c["id"]})
            report["unkeyed"] += 1

    if not events:
        report["limits"].append(
            "no history ledger available: every memory imported unkeyed — "
            "mem0 correction chains could not be reconstructed")
    report["limits"].append(
        "mem0 memories import as raw text; extraction internals (lemmatized "
        "BM25 text, entity graph) are not carried across")
    try:
        report["integrity"] = store.verify_writes()
    except Exception as e:
        report["integrity"] = {"skipped": str(e)}
    return report


def parity_check(store, mem0_memory, queries: list[str], user_id: str, top_k: int = 5) -> list[dict]:
    """Optional live parity check. Requires mem0ai installed and a live mem0 Memory."""
    import re as _re

    def norm(s: str) -> str:
        return _re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()

    stop = {"the", "a", "an", "is", "was", "at", "of", "and", "to", "in", "on", "for",
            "their", "his", "her", "its", "with", "that", "this", "my", "your", "our"}
    def tokens(s: str) -> set:
        return {w for w in norm(s).split() if w not in stop and len(w) > 2}
    rows = []
    for q in queries:
        try:
            r = mem0_memory.search(q, filters={"user_id": user_id}, top_k=top_k)
            m0 = ((r.get("results") or [{}])[0].get("memory") or "").strip()
        except Exception as e:
            m0 = f"<mem0 error: {e}>"
        hits = store.recall(q, k=top_k, user_id=user_id)
        ix = (hits[0].get("text") if hits and isinstance(hits[0], dict) else "").strip()
        m0t, ixt = tokens(m0), tokens(ix)
        overlap = len(m0t & ixt) / len(m0t) if m0t else 0.0
        rows.append({"query": q, "mem0_top1": m0, "inspeximus_top1": ix,
                     "exact_match": bool(norm(m0)) and norm(m0) == norm(ix),
                     "keyphrase_overlap": round(overlap, 2),
                     "keyphrase_match": overlap >= 0.5,
                     "note": "exact match is strict; keyphrase_match answers "
                             "'same fact, different wording' (both stores word facts differently)"})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Migrate mem0 -> inspeximus (chain-preserving)")
    ap.add_argument("--current", required=True, help="JSON export of mem0 get_all()")
    ap.add_argument("--store", required=True, help="inspeximus store file to create")
    ap.add_argument("--history", help="mem0 history.db path (SQLite ledger)")
    ap.add_argument("--value", type=float, default=2.0)
    ap.add_argument("--mtype", default="semantic", choices=["episodic", "semantic", "procedural"])
    ap.add_argument("--report", help="write the migration report JSON here")
    ap.add_argument("--force", action="store_true", help="allow writing an existing store")
    a = ap.parse_args()

    if a.history:
        events, notes = read_history(a.history)
    else:
        events, notes = [], ["--history not given: importing unkeyed"]
    current, cnotes = load_current(a.current)
    report = migrate(events, current, a.store, value=a.value, mtype=a.mtype, force=a.force)
    report["notes"] = notes + cnotes + report["notes"]
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if a.report:
        Path(a.report).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                  encoding="utf-8")


if __name__ == "__main__":
    main()


