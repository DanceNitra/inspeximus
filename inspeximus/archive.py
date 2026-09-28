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

WHAT THIS VERSION DOES NOT DO YET, stated so nobody relies on it: erasure (`forget`, `forget_subject`,
`forget_pii`, the certificate and the residue scans) does not yet reach segments, and `verify_writes` does
not yet read the log. Both are the next step of B-25 and ship in the same release as this module, which is
why `apply` refuses a store with write receipts and nothing here is on by default.
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


def verify_log(entries: list) -> tuple:
    """Recompute every hash and link. Returns (ok, problems)."""
    problems = []
    prev = GENESIS
    for i, e in enumerate(entries):
        if e.get("prev") != prev:
            problems.append(f"entry {i}: broken link (an earlier entry was altered or removed)")
        if e.get("hash") != _entry_hash(e):
            problems.append(f"entry {i}: hash does not match its content")
        prev = e.get("hash")
    return not problems, problems


def _write_log(store_path, entries: list) -> None:
    p = log_path(store_path)
    tmp = p.with_name(p.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps({"kind": LOG_KIND, "entries": entries}, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)


def listed_segments(store_path) -> dict:
    """segment file name -> {"sha256", "ids"} as the log last recorded it."""
    out: dict = {}
    for e in read_log(store_path):
        if e.get("kind") == "move":
            seg = out.setdefault(e["segment"], {"sha256": None, "ids": []})
            seg["sha256"] = e["segment_sha256"]
            seg["ids"] = sorted(set(seg["ids"]) | set(e["ids"]))
    return out


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
    if not getattr(m, "path", None):
        raise ArchiveRefused("this store has no file; there is nothing to archive beside")
    if getattr(m, "_encrypted", False):
        raise ArchiveRefused("the store is encrypted at rest; a segment would be written in plain text")
    if not m._rows_available():
        raise ArchiveRefused("the store is not a row store (INSPEXIMUS_STORE_FORMAT=json pins JSON); "
                             "segments are row stores")
    if getattr(m, "receipts_enabled", False):
        raise ArchiveRefused("the store keeps write receipts, and verify_writes does not yet account for "
                             "archived records; archiving a receipted store ships with the erasure step")


def plan(store, older_than_days: float, classes=("cmd",), now: float | None = None,
         cap_bytes: int = SEGMENT_CAP_BYTES) -> dict:
    """What `apply` would do, and nothing else: no file is written or read beyond the store and its log."""
    m = _base(store)
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


def _write_segment(seg_path: Path, records: list, manifest: dict) -> str:
    """Write one segment as a row store holding `records` verbatim, then its manifest. Returns its sha256.

    A segment left by a run that crashed before its log entry is reused when it holds exactly these ids,
    and refused otherwise, as `actions.py` treats an archive file it finds in place."""
    from . import sqlite_store as _rows
    from .core import Inspeximus
    ids = sorted(r["id"] for r in records)
    if seg_path.exists():
        have = sorted(r.get("id") for r in _rows.load(seg_path) if isinstance(r, dict))
        if have != ids:
            raise ArchiveRefused(f"{seg_path.name} already exists with other records; refusing to overwrite a "
                                 f"segment the log does not name")
        return _segment_sha256(seg_path)
    tmp = seg_path.with_name(seg_path.name + ".tmp.%d" % os.getpid())
    if tmp.exists():
        tmp.unlink()
    seg = Inspeximus(path=str(tmp), receipts=False)
    seg._items = [copy.deepcopy(dict(r)) for r in records]
    seg._save(force=True)
    seg.flush()
    con = _rows._connect(tmp)
    try:
        con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('archive_manifest', ?)",
                    (json.dumps(manifest, sort_keys=True),))
        con.commit()
    finally:
        con.close()
    del seg
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
            sha = _write_segment(seg_path, recs, manifest)
            entry = {"v": 1, "kind": "move", "ts": now, "classes": list(classes),
                     "cutoff_ts": cutoff, "segment": name, "segment_sha256": sha,
                     "count": len(ids), "ids": ids, "ids_sha256": ids_sha,
                     "prev": entries[-1]["hash"] if entries else GENESIS}
            entry["hash"] = _entry_hash(entry)
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
