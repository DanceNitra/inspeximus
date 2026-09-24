"""Memory partitions: a named scope per agent or per process, with a size cap and an expiry, closed when
the process ends.

The CNIL's exploratory note on agentic AI (20 July 2026) separates CONTEXT, which is deleted when the
process ends, from MEMORY, which persists and enriches itself, and recommends memory partitioned by
agent and by process with size limits and automatic expiry. inspeximus already isolates by tenant
(fail-closed) and by agent (grants); what it did not have is the process-shaped unit: a scope that
is opened for one workflow, capped, swept on a clock, and CLOSED with a disposition when the
workflow ends. This module is that unit, on top of the primitives that exist: a partition is a tag
on every record it holds, every erasure it performs is a `forget()` with a tombstone and a named
basis, and the registry beside the store says what each partition's rules are and when it closed.

    from inspeximus.partitions import Partitions

    parts = Partitions(store)
    ctx = parts.open("triage-2026-09-16", kind="context", max_age_days=1, max_records=200, agent="triage-bot")
    ctx.remember("customer asked about invoice 4471", key="ctx::invoice")      # tagged partition:triage-2026-09-16
    ctx.recall("invoice")                                                        # only this partition's records
    parts.sweep()                                                                # expiry and caps, tombstoned
    parts.close("triage-2026-09-16", actor="triage-bot", disposition="erased")  # the process ended; context goes
    parts.report()                                                               # per partition: counts, oldest age, closed, swept

Kinds: `context` (deleted at close by default), `process` (kept at close unless told otherwise),
`agent` (long-lived, capped and swept). A partition never crosses a tenant: it lives inside the store
handle it was opened on. What it does not do: it is not an isolation boundary against a caller who
holds the store, and a record written without going through the handle is not in any partition.
`report()` counts records that carry no partition tag, so an operator can see how much memory is
outside the scheme.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .core import __version__

__all__ = ["Partitions", "PartitionHandle", "KINDS", "TAG_PREFIX"]

KINDS = ("context", "process", "agent")
TAG_PREFIX = "partition:"
DISPOSITIONS = ("erased", "retained", "archived")


def _tag(name: str) -> str:
    return TAG_PREFIX + name


def _in(rec: dict, name: str) -> bool:
    return _tag(name) in (rec.get("tags") or [])


def _active(rec: dict) -> bool:
    return (rec.get("status") or "active") == "active"


class PartitionHandle:
    """Writes stamped with the partition tag, reads filtered to it. Everything else is the store's."""

    def __init__(self, parts: "Partitions", name: str):
        self._parts = parts
        self.name = name

    @property
    def store(self):
        return self._parts.store

    def remember(self, text: str, tags=None, **kw):
        p = self._parts._get(self.name)
        if p.get("closed_at"):
            raise ValueError(f"partition {self.name!r} is closed; open a new one for a new process")
        tags = list(tags or [])
        if _tag(self.name) not in tags:
            tags.append(_tag(self.name))
        cap = p.get("max_records")
        if cap:
            live = self._parts._records(self.name)
            if len(live) >= cap:
                if p.get("on_cap") == "refuse":
                    raise ValueError(f"partition {self.name!r} holds {len(live)} records, its cap; refusing the write")
                # evict the oldest, tombstoned and named, so the cap is a rule the store shows it applied
                oldest = sorted(live, key=lambda r: r.get("ts") or 0)[: len(live) - cap + 1]
                self.store.forget(ids=[r["id"] for r in oldest], basis=f"partition_cap:{self.name}")
                self._parts._note(self.name, "evicted", len(oldest))
        return self.store.remember(text, tags=tags, **kw)

    def recall(self, query: str, **kw):
        hits = self.store.recall(query, **kw)
        return [h for h in hits if _in(h, self.name)]

    def records(self) -> list[dict]:
        return self._parts._records(self.name)

    def close(self, actor: str, disposition: str | None = None, ledger=None) -> dict:
        return self._parts.close(self.name, actor=actor, disposition=disposition, ledger=ledger)


class Partitions:
    """The registry of partitions beside a store: `<store>.partitions.json`."""

    def __init__(self, store, path: str | os.PathLike | None = None):
        self.store = store
        spath = getattr(store, "path", None)
        if path is None:
            if spath is None:
                raise ValueError("the store has no path; pass path= for the partitions registry")
            spath = Path(spath)
            path = spath.parent / (spath.name + ".partitions.json")
        self.path = Path(path)
        self._reg: dict = {"v": 1, "partitions": {}}
        self._load()

    # ----------------------------------------------------------------- registry
    def _load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("partitions"), dict):
                    self._reg = data
            except (OSError, ValueError):
                pass

    def _save(self):
        tmp = self.path.with_name(self.path.name + ".tmp.%d" % os.getpid())
        tmp.write_text(json.dumps(self._reg, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def _get(self, name: str) -> dict:
        p = self._reg["partitions"].get(name)
        if p is None:
            raise KeyError(f"no partition named {name!r}")
        return p

    def _note(self, name: str, key: str, n: int):
        p = self._get(name)
        p[key] = int(p.get(key) or 0) + n
        self._save()

    def _records(self, name: str, held: bool = False) -> list[dict]:
        """The partition's ACTIVE records, or with `held=True` every record it still holds.

        A keyed write inside a partition supersedes the partition's earlier value, and the superseded
        record keeps its text and its partition tag. Closing and expiry walked the active records only,
        so a context partition's superseded records survived its close and expired ones survived the
        sweep, readable through history() (mcp-tools-review E1, E2). Erasure reaches every held record;
        the cap still counts active ones, which is the working set it bounds."""
        return [r for r in getattr(self.store, "items", []) if (held or _active(r)) and _in(r, name)]

    # ----------------------------------------------------------------- lifecycle
    def open(self, name: str, kind: str = "process", max_age_days: float | None = None,
             max_records: int | None = None, agent: str | None = None, on_cap: str = "evict_oldest",
             delete_at_close: bool | None = None, note: str | None = None) -> PartitionHandle:
        """Open (or reopen a handle to) a partition. `kind` is context, process or agent. `max_age_days`
        and `max_records` are enforced by `sweep()` and on write. `delete_at_close` defaults to True for
        a context partition and False otherwise."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if on_cap not in ("evict_oldest", "refuse"):
            raise ValueError("on_cap must be evict_oldest or refuse")
        if not name or "/" in name or "\\" in name or " " in name:
            raise ValueError("a partition name is a single token: letters, digits, dots, dashes, colons")
        if name in self._reg["partitions"]:
            p = self._reg["partitions"][name]
            if p.get("closed_at"):
                raise ValueError(f"partition {name!r} was closed at {p['closed_at']}; open a new name")
            return PartitionHandle(self, name)
        self._reg["partitions"][name] = {
            "name": name, "kind": kind, "agent": agent, "opened_at": time.time(),
            "max_age_days": float(max_age_days) if max_age_days is not None else None,
            "max_records": int(max_records) if max_records is not None else None,
            "on_cap": on_cap,
            "delete_at_close": (kind == "context") if delete_at_close is None else bool(delete_at_close),
            "note": note, "closed_at": None, "disposition": None, "evicted": 0, "expired": 0,
        }
        self._save()
        return PartitionHandle(self, name)

    def handle(self, name: str) -> PartitionHandle:
        self._get(name)
        return PartitionHandle(self, name)

    def sweep(self, now: float | None = None, ledger=None, actor: str | None = None) -> dict:
        """Apply every open partition's expiry and cap: records older than `max_age_days` and records
        beyond `max_records` (oldest first) are hard-deleted with a tombstone whose basis names the
        partition and the rule. Returns what was erased per partition. Nothing outside a partition is
        touched."""
        now = time.time() if now is None else now
        out = {"swept_at": now, "partitions": {}}
        for name, p in self._reg["partitions"].items():
            if p.get("closed_at"):
                continue
            live = sorted(self._records(name, held=True), key=lambda r: r.get("ts") or 0)
            expired = []
            if p.get("max_age_days") is not None:
                cutoff = now - p["max_age_days"] * 86400.0
                expired = [r for r in live if (r.get("ts") or 0) < cutoff]
            over = []
            cap = p.get("max_records")
            if cap:
                rest = [r for r in live if r not in expired and _active(r)]
                if len(rest) > cap:
                    over = rest[: len(rest) - cap]
            erased = 0
            if expired:
                self.store.forget(ids=[r["id"] for r in expired], basis=f"partition_expiry:{name}")
                p["expired"] = int(p.get("expired") or 0) + len(expired)
                erased += len(expired)
            if over:
                self.store.forget(ids=[r["id"] for r in over], basis=f"partition_cap:{name}")
                p["evicted"] = int(p.get("evicted") or 0) + len(over)
                erased += len(over)
            out["partitions"][name] = {"expired": len(expired), "evicted": len(over),
                                       "remaining": len(live) - erased}
        self._save()
        if ledger is not None and any(v["expired"] or v["evicted"] for v in out["partitions"].values()):
            ledger.record("partitions:sweep", status="ok", actor=actor, kind="retention",
                          extra={"event": "partition_sweep", "partitions": out["partitions"]})
        return out

    def close(self, name: str, actor: str, disposition: str | None = None, ledger=None,
              now: float | None = None) -> dict:
        """Close a partition when its process ends. A context partition erases its records (the CNIL's
        'context deleted at the end of the process'); a process or agent partition keeps them unless
        `disposition="erased"`. Every erasure is a tombstone with the basis `partition_close:<name>`.
        A ledger, when given, gets a lifecycle entry naming the partition and the disposition."""
        p = self._get(name)
        if p.get("closed_at"):
            raise ValueError(f"partition {name!r} is already closed")
        if not actor:
            raise ValueError("closing a partition needs an actor")
        now = time.time() if now is None else now
        if disposition is None:
            disposition = "erased" if p.get("delete_at_close") else "retained"
        if disposition not in DISPOSITIONS:
            raise ValueError(f"disposition must be one of {DISPOSITIONS}")
        live = self._records(name, held=True)
        erased = 0
        if disposition == "erased" and live:
            self.store.forget(ids=[r["id"] for r in live], basis=f"partition_close:{name}")
            erased = len(live)
        p["closed_at"] = now
        p["closed_by"] = actor
        p["disposition"] = disposition
        p["records_at_close"] = len(live)
        p["erased_at_close"] = erased
        self._save()
        entry = None
        if ledger is not None:
            entry = ledger.lifecycle("stop", actor=actor, note=f"partition {name} closed: {disposition}, "
                                                              f"{len(live)} records, {erased} erased")
        return {"partition": name, "disposition": disposition, "records": len(live), "erased": erased,
                "ledger_seq": entry["seq"] if entry else None}

    # ----------------------------------------------------------------- reading
    def report(self, now: float | None = None) -> dict:
        """Per partition: kind, agent, rules, live count, oldest age, how many are past expiry or over
        the cap right now (a sweep is due), closed state and disposition. Plus how many active records
        in the store carry no partition tag at all."""
        now = time.time() if now is None else now
        rows = []
        tagged = set()
        for name, p in self._reg["partitions"].items():
            live = self._records(name)
            held = self._records(name, held=True)
            tagged |= {r["id"] for r in live}
            ages = [(now - (r.get("ts") or now)) / 86400.0 for r in live]
            past = 0
            if p.get("max_age_days") is not None:
                # Every HELD record past expiry, superseded ones included: the sweep erases them too.
                past = sum(1 for r in held if (now - (r.get("ts") or now)) / 86400.0 > p["max_age_days"])
            over = max(0, len(live) - p["max_records"]) if p.get("max_records") else 0
            rows.append({
                "name": name, "kind": p.get("kind"), "agent": p.get("agent"),
                "max_age_days": p.get("max_age_days"), "max_records": p.get("max_records"),
                "records": len(live), "superseded_records": len(held) - len(live),
                "oldest_age_days": round(max(ages), 3) if ages else None,
                "past_expiry_now": past, "over_cap_now": over, "sweep_due": bool(past or over),
                "expired_total": p.get("expired", 0), "evicted_total": p.get("evicted", 0),
                "closed_at": p.get("closed_at"), "disposition": p.get("disposition"),
                "opened_at": p.get("opened_at"),
            })
        all_active = [r for r in getattr(self.store, "items", []) if _active(r)]
        untagged = sum(1 for r in all_active if r["id"] not in tagged
                       and not any(str(t).startswith(TAG_PREFIX) for t in (r.get("tags") or [])))
        return {
            "kind": "inspeximus.partitions_report/1",
            "inspeximus_version": __version__,
            "basis": "CNIL, exploratory note on agentic AI (20 July 2026): memory partitioned by agent and by "
                     "process, with size limits and automatic expiry; context deleted when the process ends. "
                     "GDPR Art. 5(1)(e) storage limitation.",
            "partitions": rows,
            "open": sum(1 for r in rows if not r["closed_at"]),
            "closed": sum(1 for r in rows if r["closed_at"]),
            "records_outside_any_partition": untagged,
            "sweep_due": any(r["sweep_due"] for r in rows),
        }
