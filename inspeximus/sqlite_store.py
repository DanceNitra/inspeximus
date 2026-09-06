"""Row-level persistence for the store, on `sqlite3` from the standard library.

WHY. The JSON store is rewritten in full on every save. Measured 2026-09-06 on this project's live
coding store, 32,538 records and 20.3 MB: one write costs 0.35 s and rewrites the whole file, and
12 concurrent writers lost a whole worker's output in 8 of 8 trials. The same workload on sqlite3
costs 0.001 s and lost nothing in 8 of 8. The receipt cost has the same shape, 0.017 s at 500
records rising to 0.283 s at 16,000, because the Merkle tree is rebuilt on every save too. One
cause underneath all three: every event touches the whole store instead of one row.

WHAT THIS DOES NOT CHANGE. `Inspeximus._items` stays an in-memory list of dicts, so the 44 call
sites in core.py that read it are untouched and the data model is identical. Only the disk format
moves. A record is stored whole, as JSON in one column, so nothing about its shape is encoded in a
schema that would then have to be migrated whenever a field is added.

WHY NOT A NEW DEPENDENCY. `sqlite3` ships with Python, so "one file, zero dependencies" survives
intact. It is still one file; it is a file that can write a row.

THE WRITE IS A DIFF, WHICH IS THE ENTIRE POINT. `snapshot()` records what was on disk at load.
`save()` compares the current list against it and issues only what actually changed. A hook that
appends one record performs one INSERT rather than serialising 32,538.
"""
import json
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id   TEXT PRIMARY KEY,
    ord  INTEGER NOT NULL,
    doc  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_ord ON records(ord);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

MAGIC = b"SQLite format 3\x00"


def looks_like_sqlite(path) -> bool:
    """Read the file header rather than trust the extension.

    A store's name is the caller's choice and says nothing about its contents; a store that was
    migrated in place keeps its old name. The 16-byte header is the only honest answer.
    """
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == MAGIC
    except Exception:
        return False


def _connect(path):
    con = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")        # real concurrency, not a lock file
    con.execute("PRAGMA synchronous=NORMAL")      # durable across a crash, not across a power cut
    con.executescript(SCHEMA)
    return con


def load(path):
    """Every record, in the order it was written. Returns [] for a store that does not exist yet."""
    if not os.path.exists(str(path)):
        return []
    con = _connect(path)
    try:
        rows = con.execute("SELECT doc FROM records ORDER BY ord").fetchall()
    finally:
        con.close()
    out = []
    for (doc,) in rows:
        try:
            out.append(json.loads(doc))
        except Exception:
            # ONE UNREADABLE ROW MUST NOT COST THE STORE. The JSON path fails whole-file: a single
            # bad byte makes every record unreachable, and this project lost its coding store to
            # exactly that three times in ten days. Here the blast radius is one record.
            continue
    return out


def snapshot(items) -> dict:
    """id -> serialised row, as it stands. The baseline `save` diffs against."""
    return {r["id"]: json.dumps(r, sort_keys=True, default=str)
            for r in items if isinstance(r, dict) and r.get("id")}


def save(path, items, before: dict, dirty=None) -> dict:
    """Write only what changed since `before`. Returns the new snapshot and what it did.

    `dirty` IS THE DIFFERENCE BETWEEN FAST AND POINTLESS. Without it this has to serialise every
    record to find out which ones moved, and that comparison costs more than the write it saves:
    measured on 32,539 records, the full diff took 0.393 s while the INSERT it produced took
    0.007 s. Ninety-eight percent of the work was deciding what to write. Passing the ids the
    caller already knows it touched turns a whole-store scan into one row.

    A caller that does not know passes nothing and gets the correct, slow answer. That is the right
    default: a wrong diff loses data, a slow diff only costs time.

    The counts are returned rather than logged, because a caller that cannot tell an append from a
    rewrite cannot tell this is working.
    """
    if dirty is not None:
        return _save_known(path, items, before, set(dirty))
    now = snapshot(items)
    order = {r["id"]: i for i, r in enumerate(items) if isinstance(r, dict) and r.get("id")}
    added = [k for k in now if k not in before]
    changed = [k for k in now if k in before and now[k] != before[k]]
    removed = [k for k in before if k not in now]

    con = _connect(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        if removed:
            con.executemany("DELETE FROM records WHERE id=?", [(k,) for k in removed])
        if added or changed:
            con.executemany("INSERT INTO records(id, ord, doc) VALUES(?,?,?) "
                            "ON CONFLICT(id) DO UPDATE SET ord=excluded.ord, doc=excluded.doc",
                            [(k, order.get(k, 0), now[k]) for k in added + changed])
        # Reordering without a content change still has to land, or a store reopened after a
        # consolidation comes back in the wrong order and `history()` reads backwards.
        stale_order = [(order[k], k) for k in now
                       if k not in added and k not in changed and k in order]
        if stale_order:
            con.executemany("UPDATE records SET ord=? WHERE id=? AND ord<>?",
                            [(o, k, o) for o, k in stale_order])
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        con.close()
        raise
    con.close()
    return {"snapshot": now, "added": len(added), "changed": len(changed),
            "removed": len(removed)}


def migrate_from_json(json_path, db_path) -> dict:
    """Copy a JSON store into a SQLite one, and refuse unless the count survives.

    A migration that loses records silently is worse than no migration, and this store has been
    corrupted before, so the check is part of the operation rather than a step someone remembers.
    """
    with open(str(json_path), encoding="utf-8") as fh:
        raw = json.load(fh)
    items = raw if isinstance(raw, list) else (raw.get("records") or [])
    res = save(db_path, items, {})
    back = load(db_path)
    if len(back) != len(items):
        raise RuntimeError("migration lost records: %d in, %d out" % (len(items), len(back)))
    ids_in = [r.get("id") for r in items if isinstance(r, dict)]
    ids_out = [r.get("id") for r in back]
    if ids_in != ids_out:
        raise RuntimeError("migration changed record order or identity")
    return {"records": len(back), "written": res["added"]}


def _save_known(path, items, before: dict, dirty: set) -> dict:
    """The caller named what it touched, so serialise only those, plus anything that vanished."""
    order, live = {}, set()
    for i, r in enumerate(items):
        if isinstance(r, dict) and r.get("id"):
            order[r["id"]] = i
            live.add(r["id"])
    removed = [k for k in before if k not in live]
    now = dict(before)
    for k in removed:
        now.pop(k, None)

    touched = []
    for r in items:
        if isinstance(r, dict) and r.get("id") in dirty:
            doc = json.dumps(r, sort_keys=True, default=str)
            if before.get(r["id"]) != doc:
                touched.append((r["id"], order[r["id"]], doc))
                now[r["id"]] = doc

    con = _connect(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        if removed:
            con.executemany("DELETE FROM records WHERE id=?", [(k,) for k in removed])
        if touched:
            con.executemany("INSERT INTO records(id, ord, doc) VALUES(?,?,?) "
                            "ON CONFLICT(id) DO UPDATE SET ord=excluded.ord, doc=excluded.doc",
                            touched)
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        con.close()
        raise
    con.close()
    return {"snapshot": now, "added": len([t for t in touched if t[0] not in before]),
            "changed": len([t for t in touched if t[0] in before]), "removed": len(removed)}
