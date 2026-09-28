"""Archive tiering: captured mechanics leave the hot store for monthly segments beside it (AUDIT-B B-25).

A project store fills with the hook's own captures. Measured on a copy of a 71,772-record project store
(2026-09-28): 89.5 % of the rows were `ran: ...` command captures, and every prompt paid for all of them,
9.3 s for the UserPromptSubmit hook. With the command captures older than 7 days out of the store, the
same hook took 3.05 s and printed byte-identical output on five of five prompts. Nothing may be deleted,
so the old captures MOVE: into one segment file per month beside the store, each small enough to keep in a
git repository.

    from inspeximus.archive import plan, apply
    plan(store, older_than_days=7)          # what would move, per class and per segment; writes nothing
    apply(store, older_than_days=7)         # moves it; returns the segments written and the log entry
    store.recall("deploy", include_archive=True)   # the hot store and every segment, as one pool

WHAT MOVES is named, never guessed: `CLASSES` lists each class with the rule that selects it. The first
version names one, `cmd` (key prefix `cmd:`, the hook's command captures). Decisions, file states, digests,
session boundaries and anything else stay. A record also stays when a record that stays refers to it
(`links`, `derived_from`, `retires`), as a kept entry of the action ledger holds back what it refers to.

THE LOG. `<store>.archive.json` is an append-only, hash-chained list of entries. A `move` entry names the
segment, its sha256 after the write, and the sorted ids it received, so the hot store alone accounts for
every row that left it. The pattern is the one `actions.py` ships for the action ledger.

SEGMENTS are row stores named `<stem>.archive-YYYY-MM.<n><suffix>`, for example
`coding_memory.archive-2026-09.1.json`, holding the moved records verbatim and a manifest in their `meta`
table. A segment is closed at `SEGMENT_CAP_BYTES` of record documents and the next one takes `.2`.

ERASURE REACHES THE SEGMENTS. `forget`, `forget_subject`, `forget_pii` and `--scrub-secrets` select over the
hot rows and the archived rows as one store, rewrite each segment that held a match (an `amend-intent`,
the rewrite, then the `amend`; `recover` finishes one that stopped), and put the tombstones in the hot
store's one chain. A segment that is missing or does not match the log refuses the erasure before anything
is erased (`SegmentsUnreachable`): every segment for a subject, a PII sweep or a predicate, and only the
segments holding the ids for `forget(ids=...)`. The erasure certificate names each segment and whether the
erased ids were checked absent in it; `verify_erasure_certificate` re-checks that, and a segment it cannot
find is a named gap, never a pass.

NOT YET, stated so nobody relies on it: `verify_writes` does not read the log, so `apply` still refuses a
store with write receipts. Encrypted stores and the JSON pin are refused, because a segment is a plain
row store.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

__all__ = ["CLASSES", "SEGMENT_CAP_BYTES", "plan", "apply", "read_log", "verify_log", "listed_segments",
           "pooled", "git_work_tree", "ArchiveRefused"]

LOG_KIND = "inspeximus.archive_log/1"
SEGMENT_KIND = "inspeximus.archive_segment/1"
GENESIS = "0" * 64

#: Record documents per segment, in bytes of their JSON. 45 MB leaves headroom under GitHub's 50 MB warning
#: once the row store's own overhead is added.
SEGMENT_CAP_BYTES = 45 * 1024 * 1024

#: name -> (description, selector). A class is moved only when it is named here AND asked for.
CLASSES = {
    "cmd": ("the hook's command captures (key prefix cmd:)",
            lambda r: str(r.get("key") or "").startswith("cmd:")),
}

#: Fields through which a record refers to another record by id.
_REF_FIELDS = ("links", "derived_from", "retires")

GIT_WARNING = ("an erasure can rewrite these files but cannot reach git history or any clone or push of it; "
               "run `python -m inspeximus.claude_code --scrub-secrets` before committing a segment")


class ArchiveRefused(Exception):
    """The run was refused before anything was written. The message says why."""


# ── the log ──────────────────────────────────────────────────────────────────────────────────────────

def log_path(store_path) -> Path:
    p = Path(store_path)
    return p.with_name(p.name + ".archive.json")


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _entry_hash(entry: dict) -> str:
    return hashlib.sha256(_canon({k: v for k, v in entry.items() if k not in ("hash", "sig", "pubkey")})).hexdigest()


def read_log(store_path) -> list:
    """The log's entries, oldest first; an empty list when there is no log. A log that cannot be read
    raises: an unreadable log is not an empty one, because an empty log accounts for nothing."""
    p = log_path(store_path)
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or doc.get("kind") != LOG_KIND or not isinstance(doc.get("entries"), list):
        raise ValueError(f"{p} is not an inspeximus archive log")
    return doc["entries"]


def verify_log(entries: list, expected_pubkey: str | None = None) -> tuple:
    """Recompute every hash and link, and check every signature an entry carries. With
    `expected_pubkey`, an unsigned entry is a problem too. Returns (ok, problems)."""
    problems = []
    prev = GENESIS
    for i, e in enumerate(entries):
        if e.get("prev") != prev:
            problems.append(f"entry {i}: broken link (an earlier entry was altered or removed)")
        if e.get("hash") != _entry_hash(e):
            problems.append(f"entry {i}: hash does not match its content")
        why = _sig_problem(e, f"entry {i}", expected_pubkey)
        if why:
            problems.append(why)
        prev = e.get("hash")
    return not problems, problems


def _store_of(target):
    """The store handle behind `target` when it is one (it can sign), else None (a bare path)."""
    return target if hasattr(target, "_receipts") else None


def _path_of(target):
    return getattr(target, "path", None) or target


def _sign_fields(target, hash_hex: str) -> dict:
    """{sig, pubkey} over `hash_hex` with the store's receipt key, exactly as receipts and tombstones are
    signed: the configured signer, else the Ed25519 receipt key. {} when the store has neither, or when
    `target` is a bare path. Fails closed, as the write path does, when a signer returns nothing."""
    m = _store_of(target)
    if m is None:
        return {}
    signer = getattr(m, "_receipt_signer", None)
    if signer is not None:
        sig = signer(hash_hex)
        if not sig:
            raise RuntimeError("receipt signer returned no signature; refusing to write an unsigned archive "
                               "entry while a signer is configured")
        out = {"sig": sig}
        if getattr(m, "receipt_pubkey", None):
            out["pubkey"] = m.receipt_pubkey
        return out
    from . import core as _core
    sk = getattr(m, "_receipt_sk", None)
    if sk and _core._HAVE_ED:
        k = _core._Ed25519SK.from_private_bytes(bytes.fromhex(sk))
        return {"sig": k.sign(bytes.fromhex(hash_hex)).hex(), "pubkey": m.receipt_pubkey}
    return {}


def _sig_problem(obj: dict, what: str, expected_pubkey: str | None = None) -> str | None:
    if "sig" not in obj:
        return f"{what}: unsigned, but a signature was required" if expected_pubkey else None
    from . import core as _core
    if not _core._HAVE_ED:
        return f"{what}: signed, but cryptography is not installed to check it"
    try:
        _core._Ed25519PK.from_public_bytes(bytes.fromhex(obj.get("pubkey") or expected_pubkey or "")).verify(
            bytes.fromhex(obj["sig"]), bytes.fromhex(obj["hash"]))
    except Exception:                                                # noqa: BLE001
        return f"{what}: invalid signature"
    if expected_pubkey and obj.get("pubkey") and obj["pubkey"] != expected_pubkey:
        return f"{what}: signed by an unexpected key"
    return None


def _write_log(store_path, entries: list) -> None:
    p = log_path(store_path)
    tmp = p.with_name(p.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps({"kind": LOG_KIND, "entries": entries}, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)


def listed_segments(store_path) -> dict:
    """segment file name -> {"sha256", "ids"} as the log last recorded it: moves add ids, a committed
    amend (an erasure inside the segment) removes its ids and sets the new sha256."""
    out: dict = {}
    for e in read_log(store_path):
        if e.get("kind") == "move":
            seg = out.setdefault(e["segment"], {"sha256": None, "ids": []})
            seg["sha256"] = e["segment_sha256"]
            seg["ids"] = sorted(set(seg["ids"]) | set(e["ids"]))
        elif e.get("kind") == "amend" and e.get("segment") in out:
            seg = out[e["segment"]]
            seg["sha256"] = e["new_sha256"]
            seg["ids"] = sorted(set(seg["ids"]) - set(e["ids"]))
    return out


def _append(target, entry: dict) -> dict:
    """Append one entry to the log, hash-chained, and signed with the store's receipt key when `target`
    is a store that has one."""
    store_path = _path_of(target)
    entries = read_log(store_path)
    entry["prev"] = entries[-1]["hash"] if entries else GENESIS
    entry["hash"] = _entry_hash(entry)
    entry.update(_sign_fields(target, entry["hash"]))
    entries.append(entry)
    _write_log(store_path, entries)
    return entry


def _pending_intents(entries: list) -> list:
    """`amend-intent` entries with no `amend` that commits them."""
    done = {e.get("intent") for e in entries if e.get("kind") == "amend"}
    return [e for e in entries if e.get("kind") == "amend-intent" and e["hash"] not in done]


def _segment_rx(store_path):
    import re
    p = Path(store_path)
    return re.compile(re.escape(p.stem) + r"\.archive-\d{4}-\d{2}\.\d+" + re.escape(p.suffix) + r"$")


def segment_files(store_path) -> dict:
    """Every file beside the store that holds archived rows, whether or not the log names it.

    `listed`: in the log. `unlisted`: a segment the log does not name (a run that stopped after writing
    it); it holds copies of rows that are still in the hot store. `temps`: a segment being written or
    rewritten (`<segment>.tmp.<pid>`). An erasure has to account for all three."""
    import re
    p = Path(store_path)
    rx = _segment_rx(store_path)
    trx = re.compile(rx.pattern[:-1] + r"\.tmp\.\d+$")
    try:
        names = os.listdir(p.parent)
    except OSError:
        names = []
    listed = listed_segments(store_path) if log_path(store_path).exists() else {}
    return {"listed": listed,
            "unlisted": sorted(n for n in names if rx.match(n) and n not in listed),
            "temps": sorted(n for n in names if trx.match(n))}


def present(store_path) -> bool:
    """Whether this store has an archive at all: a log, or a segment or segment temp beside it."""
    if not store_path:
        return False
    if log_path(store_path).exists():
        return True
    f = segment_files(store_path)
    return bool(f["unlisted"] or f["temps"])


class SegmentsUnreachable(ValueError):
    """An erasure needs segments that are missing or do not match the log. Nothing was erased."""

    def __init__(self, problems: list):
        self.problems = problems
        super().__init__("the erasure was refused and nothing was erased, because these archive segments "
                         "cannot be checked: " + "; ".join(f"{p['segment']} ({p['state']})" for p in problems))


def check_segments(store_path, names=None) -> list:
    """[{segment, state}] for each listed segment (or each of `names`) that is missing or whose sha256
    differs from the log. Empty when every one is present and matches."""
    listed = listed_segments(store_path)
    out = []
    for name in (sorted(listed) if names is None else sorted(names)):
        sp = Path(store_path).with_name(name)
        if not sp.exists():
            out.append({"segment": name, "state": "missing"})
        elif _segment_sha256(sp) != listed[name]["sha256"]:
            out.append({"segment": name, "state": "altered"})
    return out


def recover(store_path) -> list:
    """Finish an erasure that stopped between its `amend-intent` and its `amend`. A segment still at its
    logged sha256 with the prepared temp beside it is replaced by the temp; a segment already at the
    intent's sha256 is committed. Anything else is left for `check_segments` to report. Returns what it
    did, as [(segment, action)]."""
    from .core import _StoreLock
    target, store_path = store_path, _path_of(store_path)
    done = []
    if not log_path(store_path).exists():
        return done
    with _StoreLock(store_path):
        _recover_locked(target, store_path, done)
    return done


def _recover_locked(target, store_path, done: list) -> None:
    for it in _pending_intents(read_log(store_path)):
        sp = Path(store_path).with_name(it["segment"])
        tmp = Path(store_path).with_name(it["temp"])
        cur = _segment_sha256(sp) if sp.exists() else None
        if cur == it["old_sha256"] and tmp.exists() and _segment_sha256(tmp) == it["new_sha256"]:
            os.replace(tmp, sp)
            cur = it["new_sha256"]
            done.append((it["segment"], "replaced"))
        if cur == it["new_sha256"]:
            _append(target, {"v": 1, "kind": "amend", "ts": time.time(), "segment": it["segment"],
                             "new_sha256": it["new_sha256"], "ids": it["ids"], "intent": it["hash"]})
            done.append((it["segment"], "committed"))


def sweep_temps(store_path) -> list:
    """Remove segment temps no pending amend refers to, under the store's lock; return their names.

    A temp is a segment being written: by a move (a copy of rows that are still in the hot store or
    already in a finished segment) or by an erasure (the segment without the erased rows). One that a
    stopped process left behind is a copy nothing accounts for, the shape of A-08's save temps, so an
    erasure or a run removes it rather than reporting a clean pass beside it."""
    from .core import _StoreLock
    if not store_path:
        return []
    swept = []
    with _StoreLock(store_path):
        keep = {it.get("temp") for it in _pending_intents(read_log(store_path))}             if log_path(store_path).exists() else set()
        for name in segment_files(store_path)["temps"]:
            if name in keep:
                continue
            try:
                os.unlink(Path(store_path).with_name(name))
                swept.append(name)
            except OSError:
                pass
    return swept


def active(store) -> bool:
    """Whether erasures on this store must reach an archive: it has one, and this call is not already
    inside a pooled read (where the archive rows are part of the selection)."""
    m = _base(store)
    return bool(getattr(m, "path", None)) and not getattr(m, "_archive_pooled", False) and present(m.path)


@contextlib.contextmanager
def erasure_pool(store):
    """The selection phase of an erasure that can match in any month (a subject, a PII sweep, a
    predicate): finish any interrupted amend, refuse unless EVERY segment is present and matches the log,
    then pool the segment rows in, so the selection, its ambiguity checks and its lineage closure see the
    hot rows and the archived rows as one store. Nothing is erased inside the block."""
    if not active(store):
        yield
        return
    m = _base(store)
    recover(m)
    sweep_temps(m.path)
    problems = check_segments(m.path)
    if problems:
        raise SegmentsUnreachable(problems)
    with pooled(store):
        yield


def locate(store, ids) -> dict:
    """{segment name: [ids]}: where the given ids sit in the archive. Listed segments are read from the
    log; an unlisted segment (a copy of rows still in the hot store) is read from disk, so an erasure
    reaches its copies too. Refuses (SegmentsUnreachable) when a listed segment holding one of the ids is
    missing or does not match the log: only those segments, so erasing by id needs only what it touches."""
    from . import sqlite_store as _rows
    m = _base(store)
    want = set(ids or ())
    if not want or not present(m.path):
        return {}
    recover(m)
    sweep_temps(m.path)
    files = segment_files(m.path)
    names = [n for n, seg in files["listed"].items() if want & set(seg["ids"])]
    problems = check_segments(m.path, names)
    if problems:
        raise SegmentsUnreachable(problems)
    out = {n: sorted(want & set(files["listed"][n]["ids"])) for n in names}
    tenant = getattr(store, "tenant", None) if store is not m else None
    for n in files["unlisted"]:
        got = sorted(r["id"] for r in _rows.load(Path(m.path).with_name(n))
                     if isinstance(r, dict) and r.get("id") in want
                     and (tenant is None or r.get("tenant") == tenant))
        if got:
            out[n] = got
    if tenant is not None:
        for n in names:
            rows = {r["id"]: r for r in _rows.load(Path(m.path).with_name(n)) if isinstance(r, dict)}
            out[n] = [i for i in out[n] if rows.get(i, {}).get("tenant") == tenant]
        out = {n: v for n, v in out.items() if v}
    return out


def prepare_erasure(store, by_segment: dict) -> list:
    """For each segment, write the rewritten segment beside it as a temp (without the erased rows) and
    log an `amend-intent` naming the ids, the old and the new sha256. Returns the prepared entries and
    the erased rows' text and object values, for the residue checks."""
    from . import sqlite_store as _rows
    from .core import _StoreLock
    m = _base(store)
    prepared = []
    with _StoreLock(m.path):
        _prepare_locked(m, by_segment, prepared, _rows)
    return prepared


def _prepare_locked(m, by_segment: dict, prepared: list, _rows) -> None:
    listed = listed_segments(m.path)
    for name, ids in sorted(by_segment.items()):
        sp = Path(m.path).with_name(name)
        rows = [r for r in _rows.load(sp) if isinstance(r, dict) and r.get("id")]
        drop = set(ids)
        erased = [r for r in rows if r["id"] in drop]
        keep = [r for r in rows if r["id"] not in drop]
        tmp = sp.with_name(sp.name + ".tmp.%d" % os.getpid())
        if tmp.exists():
            tmp.unlink()
        keep_ids = sorted(r["id"] for r in keep)
        manifest = {"kind": SEGMENT_KIND, "hot_store": Path(m.path).name, "segment": name,
                    "month": name.split(".archive-")[1][:7], "count": len(keep_ids),
                    "ids_sha256": hashlib.sha256("\n".join(keep_ids).encode("utf-8")).hexdigest()}
        new_sha = _write_segment_file(tmp, keep, _signed_manifest(m, manifest),
                                      _receipt_copies(m, set(keep_ids), _segment_receipts(sp)))
        # Logged for an unlisted segment too, so a concurrent sweep keeps the temp (it keeps whatever a
        # pending intent names) and a crash before the commit is finished by `recover`.
        old_sha = listed[name]["sha256"] if name in listed else _segment_sha256(sp)
        entry = _append(m, {"v": 1, "kind": "amend-intent", "ts": time.time(), "segment": name,
                                 "ids": sorted(drop), "old_sha256": old_sha,
                                 "new_sha256": new_sha, "temp": tmp.name})
        prepared.append({"segment": name, "ids": sorted(drop), "temp": tmp.name, "intent": entry,
                         "values": [v for r in erased for v in (r.get("text"), r.get("object"))
                                    if isinstance(v, str) and v.strip()]})


def commit_erasure(store, prepared: list) -> list:
    """Replace each segment by its prepared temp and log the `amend`. An unlisted segment has no log
    entry: it is rewritten and stays unlisted. Returns [{segment, state}]."""
    from .core import _StoreLock
    m = _base(store)
    states = []
    with _StoreLock(m.path):
        _commit_locked(m, prepared, states)
    return states


def _commit_locked(m, prepared: list, states: list) -> None:
    for p in prepared:
        sp = Path(m.path).with_name(p["segment"])
        os.replace(Path(m.path).with_name(p["temp"]), sp)
        _append(m, {"v": 1, "kind": "amend", "ts": time.time(), "segment": p["segment"],
                         "new_sha256": p["intent"]["new_sha256"], "ids": p["ids"],
                         "intent": p["intent"]["hash"]})
        states.append({"segment": p["segment"], "state": "rewritten", "erased": len(p["ids"])})


# ── selection ────────────────────────────────────────────────────────────────────────────────────────

def _logged_ids(listed: dict) -> set:
    return {i for seg in listed.values() for i in seg["ids"]}


def _classify(r: dict, classes) -> str | None:
    for name in classes:
        if CLASSES[name][1](r):
            return name
    return None


def _select(items: list, cutoff: float, classes, logged=frozenset()) -> tuple:
    """(moving, held_back) lists of records. Held back: selected by class and age, but referred to by a
    record that stays. Iterated to a fixed point, because holding one back can hold back what it names.
    Ids the log already lists are never selected again: they belong to a segment."""
    cand = {r["id"]: r for r in items
            if r["id"] not in logged and isinstance(r.get("ts"), (int, float)) and r["ts"] < cutoff
            and _classify(r, classes)}
    held: dict = {}
    changed = True
    while changed:
        changed = False
        for r in items:
            if r["id"] in cand:
                continue
            for f in _REF_FIELDS:
                for ref in (r.get(f) or ()):
                    if isinstance(ref, str) and ref in cand:
                        held[ref] = cand.pop(ref)
                        changed = True
    moving = [r for r in items if r["id"] in cand]
    return moving, list(held.values())


def _month(ts: float) -> str:
    return time.strftime("%Y-%m", time.gmtime(ts))


def _segment_name(store_path, month: str, n: int) -> str:
    p = Path(store_path)
    return f"{p.stem}.archive-{month}.{n}{p.suffix}"


def _doc_bytes(r: dict) -> int:
    return len(json.dumps(r, ensure_ascii=False, default=str).encode("utf-8"))


def _layout(store_path, moving: list, cap_bytes: int, existing: set) -> list:
    """[(segment name, [records])], month by month, each under cap_bytes of record documents. A segment
    number already used by an earlier run is never reused."""
    by_month: dict = {}
    for r in moving:
        by_month.setdefault(_month(r["ts"]), []).append(r)
    out = []
    for month in sorted(by_month):
        n = 1
        while _segment_name(store_path, month, n) in existing:
            n += 1
        cur, size = [], 0
        for r in sorted(by_month[month], key=lambda x: (x["ts"], x["id"])):
            b = _doc_bytes(r)
            if cur and size + b > cap_bytes:
                out.append((_segment_name(store_path, month, n), cur))
                n += 1
                while _segment_name(store_path, month, n) in existing:
                    n += 1
                cur, size = [], 0
            cur.append(r)
            size += b
        if cur:
            out.append((_segment_name(store_path, month, n), cur))
    return out


# ── git ──────────────────────────────────────────────────────────────────────────────────────────────

def git_work_tree(path) -> str | None:
    """The root of the git work tree holding `path`, or None. Found by walking up to a `.git` entry, so
    it needs no git executable."""
    p = Path(path).resolve()
    for d in [p] + list(p.parents):
        if (d / ".git").exists():
            return str(d)
    return None


def _git_ignored(path) -> bool | None:
    """Whether git would ignore `path`; None when git cannot say (no executable, not a work tree)."""
    try:
        r = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=str(Path(path).parent),
                           capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: True, 1: False}.get(r.returncode)


# ── plan and apply ───────────────────────────────────────────────────────────────────────────────────

def _base(store):
    """The store object that owns the rows: a tenant view's parent, or the store itself."""
    try:
        return object.__getattribute__(store, "_parent")
    except AttributeError:
        return store


def _refuse_unsupported(m) -> None:
    """Refuse, before anything is read or written, the stores a plain row-store segment cannot serve.

    A LIMIT OF 3.16.1, NOT A GAP: the owner asked to keep everything, and a refusal keeps everything,
    only without the speedup. Plain segments from an encrypted store would write its text in the clear."""
    if not getattr(m, "path", None):
        raise ArchiveRefused("this store has no file; there is nothing to archive beside")
    if getattr(m, "_encrypted", False):
        raise ArchiveRefused("the store is encrypted at rest, and archive segments are plain row stores, so "
                             "archiving would write its text in the clear. An encrypted store stays hot")
    if not m._rows_available():
        raise ArchiveRefused("the store is pinned to JSON (INSPEXIMUS_STORE_FORMAT=json), and archive segments "
                             "are row stores. Convert it first: unset INSPEXIMUS_STORE_FORMAT and open the "
                             "store once, which converts it to a row store")


def plan(store, older_than_days: float, classes=("cmd",), now: float | None = None,
         cap_bytes: int = SEGMENT_CAP_BYTES) -> dict:
    """What `apply` would do, and nothing else: no file is written or read beyond the store and its log."""
    m = _base(store)
    _refuse_unsupported(m)
    unknown = [c for c in classes if c not in CLASSES]
    if unknown:
        raise ValueError(f"unknown class(es) {unknown}; known: {sorted(CLASSES)}")
    now = time.time() if now is None else float(now)
    cutoff = now - float(older_than_days) * 86400.0
    items = list(m._items)
    listed = listed_segments(m.path) if m.path else {}
    moving, held = _select(items, cutoff, classes, _logged_ids(listed))
    existing = set(listed)
    layout = _layout(m.path, moving, cap_bytes, existing) if m.path else []
    per_class: dict = {}
    for r in moving:
        per_class[_classify(r, classes)] = per_class.get(_classify(r, classes), 0) + 1
    hot_bytes = sum(_doc_bytes(r) for r in items)
    moved_bytes = sum(_doc_bytes(r) for r in moving)
    seg_dir = Path(m.path).parent if m.path else None
    repo = git_work_tree(seg_dir) if seg_dir else None
    return {
        "store": str(m.path) if m.path else None,
        "classes": {c: CLASSES[c][0] for c in classes},
        "cutoff_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff)),
        "moving": len(moving), "moving_per_class": per_class, "held_back_by_reference": len(held),
        "hot_rows_before": len(items), "hot_rows_after": len(items) - len(moving),
        "hot_record_bytes_before": hot_bytes, "hot_record_bytes_after": hot_bytes - moved_bytes,
        "hot_file_bytes_before": os.path.getsize(m.path) if m.path and os.path.exists(m.path) else 0,
        "segments": [{"file": name, "records": len(recs), "record_bytes": sum(_doc_bytes(r) for r in recs)}
                     for name, recs in layout],
        "git_work_tree": repo,
    }


def _segment_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _signed_manifest(m, manifest: dict) -> dict:
    """The manifest with its hash and, when the store has a receipt key, its signature. Deleting a record
    and its receipt copy from a segment changes the id count and sha256 the signature covers."""
    manifest = dict(manifest)
    manifest["hash"] = _entry_hash(manifest)
    manifest.update(_sign_fields(m, manifest["hash"]))
    return manifest


def _receipt_copies(m, ids: set, prior=None) -> list:
    """Every write receipt of the given ids: from the hot store's chain, or from a segment's own copies."""
    src = prior if prior is not None else list(getattr(m, "_receipts", None) or [])
    return [copy.deepcopy(r) for r in src if isinstance(r, dict) and r.get("memory_id") in ids]


def _meta(path, key):
    from . import sqlite_store as _rows
    con = _rows._connect(path)
    try:
        row = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    finally:
        con.close()
    return json.loads(row[0]) if row else None


def _segment_receipts(path) -> list:
    return _meta(path, "archive_receipts") or []


def _write_segment_file(path: Path, records: list, manifest: dict, receipts=None) -> str:
    """Write a new row store at `path` holding `records` verbatim, the manifest, and the write receipts of
    those records (the hot chain's copies, as a list: chain positions live in the hot store); return
    its sha256."""
    from . import sqlite_store as _rows
    from .core import Inspeximus
    seg = Inspeximus(path=str(path), receipts=False)
    seg._items = [copy.deepcopy(dict(r)) for r in records]
    seg._save(force=True)
    seg.flush()
    con = _rows._connect(path)
    try:
        con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('archive_manifest', ?)",
                    (json.dumps(manifest, sort_keys=True),))
        if receipts:
            con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('archive_receipts', ?)",
                        (json.dumps(receipts, sort_keys=True),))
        con.commit()
    finally:
        con.close()
    del seg
    return _segment_sha256(path)


def _write_segment(seg_path: Path, records: list, manifest: dict, hot_ids=frozenset(), receipts=None) -> str:
    """Write one segment holding `records` verbatim, then its manifest. Returns its sha256.

    A segment the log does not name is left by a run that stopped before its log entry. It is reused
    when it holds exactly these ids, and replaced when every row it holds is still in the hot store (a
    stale copy, for example one an erasure has since rewritten). Anything else is refused, as
    `actions.py` treats an archive file it finds in place."""
    from . import sqlite_store as _rows
    ids = sorted(r["id"] for r in records)
    if seg_path.exists():
        have = sorted(r.get("id") for r in _rows.load(seg_path) if isinstance(r, dict))
        if have == ids:
            return _segment_sha256(seg_path)
        if not set(have) <= set(hot_ids):
            raise ArchiveRefused(f"{seg_path.name} already exists with records the hot store does not hold; "
                                 f"refusing to overwrite a segment the log does not name")
    tmp = seg_path.with_name(seg_path.name + ".tmp.%d" % os.getpid())
    if tmp.exists():
        tmp.unlink()
    _write_segment_file(tmp, records, manifest, receipts)
    os.replace(tmp, seg_path)
    return _segment_sha256(seg_path)


def apply(store, older_than_days: float, classes=("cmd",), now: float | None = None,
          allow_git_tracked: bool = False, cap_bytes: int = SEGMENT_CAP_BYTES) -> dict:
    """Move the selected records into segments, append one `move` entry per segment, and save the hot
    store without them. Refused, with nothing written, when the store is not supported, the log does not
    verify, or the segments would land in a git work tree without `allow_git_tracked`."""
    from .core import _StoreLock
    m = _base(store)
    _refuse_unsupported(m)
    recover(m)
    sweep_temps(m.path)
    ok, problems = verify_log(read_log(m.path))
    if not ok:
        raise ArchiveRefused(f"the archive log does not verify: {problems[0]}")
    p = plan(m, older_than_days, classes, now, cap_bytes)
    repo = p["git_work_tree"]
    if repo and not allow_git_tracked:
        raise ArchiveRefused(f"the segments would be written inside the git work tree {repo}; {GIT_WARNING}. "
                             f"Pass allow_git_tracked=True (CLI --allow-git-tracked) to do it deliberately")
    now = time.time() if now is None else float(now)
    cutoff = now - float(older_than_days) * 86400.0
    written = []
    with _StoreLock(m.path):
        entries = read_log(m.path)
        listed = listed_segments(m.path)
        logged = _logged_ids(listed)
        # AN EARLIER RUN THAT STOPPED between its log entry and the hot store's save left rows that the
        # log gives to a segment and the hot store still holds. They are finished here, once the segment
        # they went to is present and matches the log, and never selected again (so never copied twice).
        stranded = {r["id"] for r in m._items if r["id"] in logged}
        for name, seg in listed.items():
            if stranded & set(seg["ids"]):
                sp = Path(m.path).with_name(name)
                if not sp.exists() or _segment_sha256(sp) != seg["sha256"]:
                    raise ArchiveRefused(f"{name} holds rows the hot store still has, and it is missing or "
                                         f"does not match the log; nothing was moved")
        moving, _held = _select(list(m._items), cutoff, classes, logged)
        if not moving and not stranded:
            return {**p, "applied": False, "written": []}
        layout = _layout(m.path, moving, cap_bytes, set(listed))
        moved_ids: set = set(stranded)
        for name, recs in layout:
            seg_path = Path(m.path).with_name(name)
            ids = sorted(r["id"] for r in recs)
            ids_sha = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
            manifest = {"kind": SEGMENT_KIND, "hot_store": Path(m.path).name, "segment": name,
                        "month": name.split(".archive-")[1][:7], "count": len(ids), "ids_sha256": ids_sha}
            sha = _write_segment(seg_path, recs, _signed_manifest(m, manifest), {r["id"] for r in m._items},
                                 _receipt_copies(m, set(ids)))
            entry = {"v": 1, "kind": "move", "ts": now, "classes": list(classes),
                     "cutoff_ts": cutoff, "segment": name, "segment_sha256": sha,
                     "count": len(ids), "ids": ids, "ids_sha256": ids_sha,
                     "prev": entries[-1]["hash"] if entries else GENESIS}
            entry["hash"] = _entry_hash(entry)
            entry.update(_sign_fields(m, entry["hash"]))
            entries.append(entry)
            written.append({"file": name, "records": len(ids), "sha256": sha, "entry_hash": entry["hash"]})
            moved_ids.update(ids)
        _write_log(m.path, entries)
        m._items = [r for r in m._items if r["id"] not in moved_ids]
        m._dirty = True
    m._save(force=True)
    m.flush()
    vacuumed = _vacuum(m)
    ignored = None
    if repo:
        ignored = all(_git_ignored(Path(m.path).with_name(w["file"])) for w in written) if written else None
    return {**p, "applied": True, "written": written, "vacuumed": vacuumed,
            "hot_file_bytes_after": os.path.getsize(m.path),
            "git_warning": GIT_WARNING if repo else None, "gitignore_excludes_segments": ignored}


def _vacuum(m) -> bool:
    """Give the moved rows' pages back to the file system. The row store runs with `secure_delete`, so the
    freed pages are already zeroed; without this the file keeps its size. Skipped, and reported, when
    another connection holds the database busy."""
    import sqlite3
    from . import sqlite_store as _rows
    from .core import _StoreLock
    try:
        with _StoreLock(m.path):
            con = _rows._connect(m.path)
            try:
                con.execute("VACUUM")
            finally:
                con.close()
            m._file_sig = m._stat_sig()
        return True
    except sqlite3.OperationalError:
        return False


# ── reads ────────────────────────────────────────────────────────────────────────────────────────────

def _segment_records(store_path) -> list:
    from . import sqlite_store as _rows
    from .core import Inspeximus
    out = []
    for name in listed_segments(store_path):
        seg_path = Path(store_path).with_name(name)
        if not seg_path.exists():
            continue                      # a missing segment is reported by the verifiers, not by recall
        for r in _rows.load(seg_path):
            if isinstance(r, dict) and r.get("id"):
                Inspeximus._normalise_loaded(r)
                out.append(r)
    return out


def segment_rows(store) -> list:
    """Every row in the listed segments (a copy per call), tenant-scoped for a tenant view."""
    m = _base(store)
    rows = _segment_records(m.path) if getattr(m, "path", None) else []
    tenant = getattr(store, "tenant", None) if store is not m else None
    return [r for r in rows if tenant is None or r.get("tenant") == tenant]


@contextlib.contextmanager
def pooled(store):
    """The hot store's rows plus every listed segment's, for the duration of one read, on the hot
    instance itself: the same configuration, tenant rules and keys decide what the read returns. Nothing
    pooled can reach the hot file, because saving is suspended for the block, and the pooled rows are
    removed again by identity, so a reload during the block is kept."""
    m = _base(store)
    extra = _segment_records(m.path) if getattr(m, "path", None) else []
    if not extra:
        yield m
        return
    have = {r["id"] for r in m._items}
    extra = [r for r in extra if r["id"] not in have]       # a row present in both is read from the hot store
    marks = {id(r) for r in extra}
    # ORDER. Recall orders equal scores by recency, so the pooled rows rank as they did before the move.
    # The one exception, measured: rows with equal scores AND an identical `ts` fall back to their
    # position in the list, and that position is not kept across the move. Merging the rows back by
    # `ts` was tried and changed nothing in either case, so they are appended.
    prev_flag = getattr(m, "_archive_pooled", False)
    m._archive_pooled = True
    m._items = list(m._items) + extra
    try:
        yield m
    finally:
        m._items = [r for r in m._items if id(r) not in marks]
        m._archive_pooled = prev_flag


# ── certificates ─────────────────────────────────────────────────────────────────────────────────────

GIT_HISTORY = "in a git work tree: history not reachable"


def certificate_block(store, erased_ids) -> dict | None:
    """What an erasure certificate says about the archive: every listed segment with its state and
    whether the erased ids were checked absent there, the log's hash, and every problem. None when the
    store has no archive.

    Problems (each makes the certificate unverified): the log does not verify; an erasure's amend was
    not completed; an erased id was moved into a segment and no amend removed it; an erased id is still
    in a segment; a segment is missing or altered; a segment the log does not name, or a segment temp,
    sits beside the store; a segment that held an erased id is in a git work tree, whose history an
    erasure cannot reach."""
    from . import sqlite_store as _rows
    m = _base(store)
    if not getattr(m, "path", None) or not present(m.path):
        return None
    problems = []
    entries = read_log(m.path) if log_path(m.path).exists() else []
    ok, lp = verify_log(entries)
    problems += [f"archive log: {x}" for x in lp]
    for it in _pending_intents(entries):
        problems.append(f"an erasure in {it['segment']} was not completed: its amend-intent has no amend")
    erased = set(erased_ids)
    ever: dict = {}
    for e in entries:
        if e.get("kind") == "move":
            ever.setdefault(e["segment"], set()).update(e["ids"])
    amended = {i for e in entries if e.get("kind") == "amend" for i in e["ids"]}
    for i in sorted((erased & set().union(*ever.values())) - amended) if ever else []:
        problems.append(f"erased id {i} was moved to an archive segment and no amend removed it there")
    listed = listed_segments(m.path) if entries else {}
    segs = []
    for name in sorted(listed):
        sp = Path(m.path).with_name(name)
        state = "missing" if not sp.exists() else (
            "present" if _segment_sha256(sp) == listed[name]["sha256"] else "altered")
        entry = {"segment": name, "sha256": listed[name]["sha256"], "state": state}
        if state == "present":
            held = {r.get("id") for r in _rows.load(sp) if isinstance(r, dict)}
            leaked = sorted(erased & held)
            entry["erased_ids"] = "absent (checked)" if not leaked else f"{len(leaked)} PRESENT"
            if leaked:
                problems.append(f"{len(leaked)} erased id(s) are still in {name}")
        else:
            entry["erased_ids"] = f"not checked (segment {state})"
            problems.append(f"{name} is {state}, so the erased ids were not checked there")
        repo = git_work_tree(sp.parent)
        if repo:
            entry["git_work_tree"] = repo
            if erased & ever.get(name, set()):
                entry["git"] = GIT_HISTORY
                problems.append(f"{name} held an erased record and is {GIT_HISTORY} ({repo}): a commit or a "
                                f"clone can still hold it")
        segs.append(entry)
    files = segment_files(m.path)
    for n in files["unlisted"]:
        problems.append(f"{n} is an archive segment the log does not name, a copy the erasure accounted for "
                        f"only if it was rewritten in this call")
    for n in files["temps"]:
        problems.append(f"{n} is a segment temp beside the store, a copy no erasure has checked")
    lp_path = log_path(m.path)
    return {"log_sha256": _segment_sha256(lp_path) if lp_path.exists() else None, "segments": segs,
            "unlisted": files["unlisted"], "temps": files["temps"], "problems": problems}


def verify_certificate_block(cert: dict, store_path, erased: set) -> tuple:
    """(levels, problems) for a certificate's archive block, re-checked against the segments beside
    `store_path`: each named segment either has the erased ids checked absent, or says why not."""
    from . import sqlite_store as _rows
    arch = cert.get("archive")
    if not arch:
        return [], []
    levels, problems = [], []
    for seg in arch.get("segments") or []:
        sp = Path(store_path).with_name(seg["segment"])
        if not sp.exists():
            levels.append({"segment": seg["segment"], "content": "not checked (segment absent)"})
            problems.append(f"segment {seg['segment']} is not present beside the store, so the erased ids "
                            f"were not checked there")
            continue
        held = {r.get("id") for r in _rows.load(sp) if isinstance(r, dict)}
        leaked = sorted(erased & held)
        levels.append({"segment": seg["segment"], "content": "absent (checked)" if not leaked else "PRESENT"})
        if leaked:
            problems.append(f"{len(leaked)} erased id(s) STILL PRESENT in segment {seg['segment']}: {leaked[:5]}")
    return levels, problems


# ── verification ─────────────────────────────────────────────────────────────────────────────────────

def is_segment(path) -> bool:
    """Whether `path` is an archive segment: a row store with an archive manifest."""
    from . import sqlite_store as _rows
    try:
        return bool(path) and _rows.looks_like_sqlite(path) and _meta(path, "archive_manifest") is not None
    except Exception:                                                # noqa: BLE001
        return False


def verify_segment(path, expected_pubkey: str | None = None) -> tuple:
    """Verify one segment on its own. Returns (ok, problems).

    The manifest's hash and signature, its id count and id sha256 against the rows, each receipt copy's
    hash and signature, and each row against its latest receipt (the committed fields). A segment of a
    store without receipts verifies its manifest only and says so: that is accounting, not tamper
    evidence. Chain positions are checked against the hot store by its own verify_writes."""
    from . import core as _core
    from . import sqlite_store as _rows
    problems = []
    man = _meta(path, "archive_manifest")
    if not isinstance(man, dict):
        return False, [f"{Path(path).name} carries no archive manifest"]
    if man.get("hash") != _entry_hash(man):
        problems.append("the segment manifest does not match its hash")
    why = _sig_problem(man, "the segment manifest", expected_pubkey)
    if why:
        problems.append(why)
    rows = [r for r in _rows.load(path) if isinstance(r, dict) and r.get("id")]
    ids = sorted(r["id"] for r in rows)
    if man.get("count") != len(ids) or man.get("ids_sha256") != hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest():
        problems.append(f"the segment holds {len(ids)} record(s) and they do not match its manifest "
                        f"({man.get('count')}): a row was added or removed after it was written")
    receipts = _meta(path, "archive_receipts") or []
    latest: dict = {}
    for i, r in enumerate(receipts):
        if r.get("hash") != _core._sha256_hex(_core._canon(_core.Inspeximus._chain_core(r, "write"))):
            problems.append(f"receipt copy {i}: hash does not match its content")
        why = _sig_problem(r, f"receipt copy {i}", expected_pubkey)
        if why:
            problems.append(why)
        mid = r.get("memory_id")
        if mid not in latest or r.get("seq", 0) >= latest[mid].get("seq", 0):
            latest[mid] = r
    if receipts:
        for rec in rows:
            _core.Inspeximus._normalise_loaded(rec)
            rc = (latest.get(rec["id"]) or {}).get("commit") or {}
            if not rc:
                problems.append(f"memory {rec['id']}: no receipt copy in the segment")
                continue
            cc = _core.Inspeximus._recompute_commit(rec)
            bad = [k for k in _core._COMMIT_BINDING_FIELDS if k in rc and rc.get(k) != cc.get(k)]
            if bad:
                problems.append(f"memory {rec['id']}: differs from its receipt in {', '.join(bad)}")
    else:
        # As the hot store's verify_writes does for a store without receipts: nothing here is verified.
        problems.append("the segment carries no write receipts (its store keeps none): its manifest gives "
                        "accounting, not tamper evidence")
    return not problems, problems


def receipt_lookup(store):
    """For verify_writes: a function memory_id -> (row, gap). The row of an archived record, read from its
    segment when the segment is present and matches the log; or a gap (segment, state) when it is not.
    (None, None) for an id the log does not list."""
    from . import core as _core
    from . import sqlite_store as _rows
    m = _base(store)
    listed = listed_segments(m.path)
    where = {i: n for n, seg in listed.items() for i in seg["ids"]}
    cache: dict = {}

    def get(mid):
        n = where.get(mid)
        if n is None:
            return None, None
        if n not in cache:
            sp = Path(m.path).with_name(n)
            if not sp.exists():
                cache[n] = ("gap", "missing")
            elif _segment_sha256(sp) != listed[n]["sha256"]:
                cache[n] = ("gap", "altered")
            else:
                rows = {}
                for r in _rows.load(sp):
                    if isinstance(r, dict) and r.get("id"):
                        _core.Inspeximus._normalise_loaded(r)
                        rows[r["id"]] = r
                cache[n] = ("rows", rows)
        kind, val = cache[n]
        if kind == "gap":
            return None, (n, val)
        return val.get(mid), None
    return get

