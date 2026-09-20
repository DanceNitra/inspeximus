"""Row-level persistence for the store, on `sqlite3` from the standard library.

WHY. The JSON store is rewritten in full on every save. Measured 2026-09-06 on this project's live
coding store, 32,643 records and 21.5 MB: one write costs 0.5431 s and rewrites the whole file,
against 0.0341 s to write one row. The receipt cost has the same shape, 0.017 s at 500 records
rising to 0.283 s at 16,000, because the Merkle tree is rebuilt on every save too. One cause
underneath both: every event touches the whole store instead of one row.

CONCURRENCY IS THE OTHER HALF, AND IT DOES NOT LIVE IN THIS FILE. Writing rows is what makes two
writers able to share a store, but the merge that delivers it is in `Inspeximus._save`: this module
was measured alone and reported losing nothing, while the library around it still refused the second
writer. `probes/twelve_writers_and_the_one_that_stopped_writing.py` measures the product, which is
the number that means anything.

WHAT THIS DOES NOT CHANGE. `Inspeximus._items` stays an in-memory list of dicts, so the 44 call
sites in core.py that read it are untouched and the data model is identical. Only the disk format
moves. A record is stored whole, as JSON in one column, so nothing about its shape is encoded in a
schema that would then have to be migrated whenever a field is added.

WHY NOT A NEW DEPENDENCY. `sqlite3` ships with Python, so "one file, zero dependencies" survives
intact. It is still one file; it is a file that can write a row.

THE WRITE IS A DIFF, WHICH IS THE ENTIRE POINT. `snapshot()` records what was on disk at load.
`save()` compares the current list against it and issues only what actually changed. A hook that
appends one record performs one INSERT rather than serialising the whole store.
"""
import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id   TEXT PRIMARY KEY,
    ord  INTEGER NOT NULL,
    doc  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_ord ON records(ord);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS memory_events (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    type      TEXT NOT NULL,
    memory_id TEXT,
    agent     TEXT,
    tenant    TEXT,
    payload   TEXT NOT NULL
);
"""

#: The event table alone, for a store created before it existed. Additive: no row of `records`
#: changes, and a reader that does not know the table never touches it.
EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_events (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    type      TEXT NOT NULL,
    memory_id TEXT,
    agent     TEXT,
    tenant    TEXT,
    payload   TEXT NOT NULL
);
"""

#: What an automatic event carries about a record: ids, labels and status, never text or value.
#: A reader who may not read the record learns that something it cannot see changed, and nothing
#: else; the text is behind `recall`/`get`, under the grants that already guard them.
_EVENT_FIELDS = ("key", "status", "mtype")

#: Bumped when the BYTES a record turns into change, not when the schema does. A store written by an
#: older writer keeps those bytes until something rewrites the row, so a fix to the encoding does not
#: reach the rows already on disk: `doc_format` 1 escaped non-ASCII, which hid names with diacritics
#: from the residue scanner. `needs_rewrite()` reports a store that is behind, and the library takes
#: one full reconcile to bring it forward.
DOC_FORMAT = 2

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


#: How long a writer waits out a busy database. It must stay BELOW `core.LOCK_WAIT_S`: the
#: caller holds the inter-process lock across this wait, so a holder that can block longer
#: than a waiter is willing to wait makes the waiter give up on the lock and write
#: unprotected. It was 30 against a 20 s lock wait, which is the wrong way round.
BUSY_TIMEOUT_S = 10


def _connect(path):
    # A BUSY DATABASE IS ALREADY WAITED OUT, by `timeout=30` below: sqlite blocks the writer until
    # the lock frees or thirty seconds pass. A retry loop was added on top of that after a full-suite
    # run lost 9 of 96 records with eight concurrent writers -- a failure that never reproduced, in
    # four solo runs or three under sixteen processes of load. The loop was then refuted by its own
    # test: six attempts times a thirty-second wait is three minutes of blocking, which is the "a
    # retry turns a problem into a hang" failure the loop's own docstring warned about. One wait,
    # and it is this one.
    # "IS THE FILE THERE" IS ASKED BEFORE SQLITE CREATES IT. A store gets its format marker the
    # moment it is created, so an absent marker means one thing only: written before the marker
    # existed. Stamping it from the full-diff writer alone left a store created by a declared write
    # unmarked, every later open judged it out of date, and the upgrade path then emptied the diff
    # baseline -- which silently disabled DELETES. A GDPR erasure reported "erased 1" and the record
    # stayed in the file.
    _fresh = not os.path.exists(str(path))
    con = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_S, isolation_level=None)
    # NOT WAL, AND THE REASON IS THE FILE. WAL keeps recent writes in a `-wal` sidecar and folds
    # them back when the last connection closes, so it is only fast if a connection stays open --
    # and a held connection means the store cannot be renamed on Windows, which breaks the documented
    # rollback (`rename the backup over the store`) and any tool that moves the file. Measured, 300
    # open-write-close cycles: WAL 1.98 s, DELETE 1.13 s. The concurrency this store gains does not
    # come from WAL anyway; it comes from the merge in `Inspeximus._save`, which works whatever the
    # journal does.
    if _fresh:
        # SET ONCE, AT CREATION, because `journal_mode` is a property of the FILE and re-declaring it
        # on every connect is a write the store does not need.
        #
        # IT IS NOT A FIX FOR THE CONCURRENCY LOSS, and the first version of this comment said it
        # was. A full-suite run lost 9 of 96 records with eight concurrent writers, once; the story
        # written here was that changing a journal mode takes an exclusive lock which ignores the
        # busy timeout, so a second writer failed immediately. Two things refuted it: the failure it
        # cited was a thirty-second timeout misread as an immediate one, and the test built to prove
        # the fix passes with the fix reverted. The loss remains unexplained and unreproduced -- in
        # four solo runs, three under sixteen processes of load, and every full-suite run since.
        con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA synchronous=NORMAL")      # durable across a crash, not across a power cut
    # A DELETE HAS TO REMOVE THE BYTES, NOT ONLY THE ROW. By default sqlite marks a deleted row's
    # pages free and leaves their content in the file until something reuses them, so an erased
    # record stays readable with `strings`. That is precisely the failure this library's erasure
    # surfaces exist to disprove: measured before this pragma, `forget_subject` then a byte search
    # for the erased text found it in the row store and did NOT find it in the JSON store, because
    # the JSON path rewrites the whole file. `secure_delete` zeroes freed content instead.
    con.execute("PRAGMA secure_delete=ON")
    # RUN THE SCHEMA ONCE, NOT ON EVERY WRITE. `CREATE TABLE IF NOT EXISTS` is cheap to satisfy and
    # not free to parse: measured, 300 connections cost 0.55 s with the script and 0.07 s without it,
    # so a quarter of a small store's write time was spent re-declaring tables that already existed.
    if _fresh:
        con.executescript(SCHEMA)
    else:
        # ONE query answers both "is this a store" and "does it predate the event table". The
        # second table is created in place on an older store; that is the whole migration.
        _have = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('records','memory_events')")}
        if "records" not in _have:
            con.executescript(SCHEMA)
        elif "memory_events" not in _have:
            con.executescript(EVENTS_SCHEMA)
    if _fresh:
        con.execute("INSERT INTO meta(k, v) VALUES('doc_format', ?) "
                    "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(DOC_FORMAT),))
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


def _doc(rec, keep_vec: bool = True) -> str:
    """One record as the JSON text stored in its row.

    `allow_nan=False` for the same reason the whole-file writer has it: Python emits a bare `NaN` or
    `Infinity` literal that it can read back and every STRICT reader rejects, so the store quietly
    stops being valid JSON for `jq`, for a non-Python reader, and for the audit bundle, while
    `state_digest` and `verify_writes` both still report healthy. The row writer shipped without it,
    so a planted NaN reached the file on the new format and not on the old one.

    `ensure_ascii=False` IS A COMPLIANCE REQUIREMENT HERE, not a formatting preference. With the
    default, `Drahosova` keeps its diacritics as `š` escapes, so the name is not in the file as
    UTF-8 and a byte scan cannot find it. Measured: `erasure_residue.scan_residue` found the value in
    the JSON store and MISSED it in the row store, which means a residue report would state that a
    subject's data is not present while it is sitting in the file. The scanner reads arbitrary files
    and cannot parse every format, so the store is what has to hold the literal text.
    """
    # STRIP `vec` HERE RATHER THAN COPYING THE STORE TO STRIP IT. The caller used to build a whole
    # second list of dicts on every save just to drop one key -- 30,000 dict copies per write on this
    # project's store, 0.057 s of pure copying that the row path then threw away, because it only
    # serialises the ids that changed. Dropping it at the one place that serialises a record costs
    # nothing when the key is absent, which is the common case.
    # UNDERSCORE KEYS ARE A READER'S NOTES, NOT STORED STATE. `recall` tags the records it returns
    # with `_stale_derived`; once records declare their own edits, that tag counted as a change and
    # was written to disk, so a READ dirtied the store and `verify_writes` then reported memory and
    # disk disagreeing about a field neither of them should keep. Dropping them here lets a reader
    # annotate freely: the serialised row is unchanged, so the diff finds nothing to write.
    if (not keep_vec and "vec" in rec) or any(k[:1] == "_" for k in rec):
        rec = {k: v for k, v in rec.items()
               if k[:1] != "_" and (keep_vec or k != "vec")}
    return json.dumps(rec, sort_keys=True, default=str, allow_nan=False, ensure_ascii=False)


def doc_format(path) -> int:
    """The doc_format the store on disk was written with. 1 for a store that predates the marker."""
    if not os.path.exists(str(path)):
        return DOC_FORMAT
    con = _connect(path)
    try:
        row = con.execute("SELECT v FROM meta WHERE k='doc_format'").fetchone()
    except Exception:                                             # noqa: BLE001
        return 1
    finally:
        con.close()
    try:
        return int(row[0]) if row else 1
    except (TypeError, ValueError):
        return 1


def needs_rewrite(path) -> bool:
    """Whether every row has to be written again to reach the current encoding."""
    return looks_like_sqlite(path) and doc_format(path) < DOC_FORMAT


#: Read a record's field WITHOUT going through a subclass's accessor. Records in a live store are
#: `_TrackedDict`, which wraps nested containers on access so an edit declares itself -- useful to
#: the library, pure overhead to this module, which only ever reads ids. Measured: the save path
#: scans every record, so twenty writes to a 30,000-record store made 5.4 million wrapped lookups
#: and spent 2.1 s in them. This module reads through `dict` directly and mutates nothing.
_field = dict.get


def snapshot(items, keep_vec: bool = True) -> dict:
    """id -> serialised row, as it stands. The baseline `save` diffs against.

    `keep_vec` must match what the writer does, or every record with an embedding looks changed on
    every comparison and the diff degenerates into a full rewrite.
    """
    return {_field(r, "id"): _doc(r, keep_vec)
            for r in items if isinstance(r, dict) and _field(r, "id")}


def _event_row(kind, rec, ts):
    """A content-free event row for one record: (ts, type, memory_id, agent, tenant, payload)."""
    if not isinstance(rec, dict):
        return (ts, kind, None, None, None, "{}")
    payload = {k: rec.get(k) for k in _EVENT_FIELDS if rec.get(k) is not None}
    return (ts, kind, _field(rec, "id"), rec.get("owner_agent"), rec.get("tenant"),
            json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _insert_events(con, rows) -> list:
    """INSERT the event rows inside the caller's open transaction and return their seqs."""
    seqs = []
    for row in rows:
        cur = con.execute("INSERT INTO memory_events(ts, type, memory_id, agent, tenant, payload) "
                          "VALUES(?,?,?,?,?,?)", row)
        seqs.append(cur.lastrowid)
    return seqs


def events_since(path, since_seq: int = 0, limit: int = 100, event_type=None) -> list:
    """Events with seq > since_seq, oldest first, at most `limit`. [] for a store that has none."""
    if not os.path.exists(str(path)):
        return []
    con = _connect(path)
    try:
        q = "SELECT seq, ts, type, memory_id, agent, tenant, payload FROM memory_events WHERE seq>?"
        args = [int(since_seq)]
        if event_type:
            q += " AND type=?"
            args.append(str(event_type))
        q += " ORDER BY seq LIMIT ?"
        args.append(max(1, int(limit)))
        rows = con.execute(q, args).fetchall()
    finally:
        con.close()
    out = []
    for seq, ts, kind, mid, agent, tenant, payload in rows:
        try:
            pl = json.loads(payload)
        except Exception:
            pl = {}
        out.append({"seq": seq, "ts": ts, "type": kind, "memory_id": mid, "agent": agent,
                    "tenant": tenant, "payload": pl})
    return out


def events_tip(path) -> int:
    """The highest event seq on disk, 0 for none."""
    if not os.path.exists(str(path)):
        return 0
    con = _connect(path)
    try:
        row = con.execute("SELECT MAX(seq) FROM memory_events").fetchone()
    finally:
        con.close()
    return int(row[0] or 0)


def save(path, items, before: dict, dirty=None, rewrite_all: bool = False,
         keep_vec: bool = True, events=None, auto_events: bool = False) -> dict:
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
    if dirty is not None and not rewrite_all:
        return _save_known(path, items, before, set(dirty), keep_vec, events, auto_events)
    now = snapshot(items, keep_vec)
    order = {_field(r, "id"): i for i, r in enumerate(items)
             if isinstance(r, dict) and _field(r, "id")}
    added = [k for k in now if k not in before]
    # `rewrite_all` re-writes every row that already exists, which is how a store moves to a new
    # encoding. It deliberately does NOT touch `before`: `removed` is computed from it, and emptying
    # the baseline to force the rewrite is what made deletions vanish.
    changed = [k for k in now if k in before and (rewrite_all or now[k] != before[k])]
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
        # ONLY THE FULL DIFF MAY STAMP THIS. The marker says "every row in this file is in the
        # current encoding", and only a write that considered every row can honestly claim it.
        # Stamped from the declared-ids writer as well, it marked a store current after a single
        # row was rewritten: measured on this project's live store, which reported doc_format 2
        # with almost every row still escaped. A marker that can be set without checking its
        # subject is the same defect as a guard that never sees its target.
        con.execute("INSERT INTO meta(k, v) VALUES('doc_format', ?) "
                    "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(DOC_FORMAT),))
        # THE EVENTS RIDE IN THIS TRANSACTION, which is the one property that makes them worth
        # having: an event lands with its row or not at all, so a reader tailing `memory_events`
        # never sees a change that rolled back, and never misses one that committed.
        _ev = list(events or ())
        if auto_events:
            _ts = time.time()
            _by = {_field(r, "id"): r for r in items if isinstance(r, dict) and _field(r, "id")}
            _ev += [_event_row("record.added", _by.get(k), _ts) for k in added]
            _ev += [_event_row("record.changed", _by.get(k), _ts) for k in changed
                    if not rewrite_all or now[k] != before.get(k)]
            _ev += [_event_row("record.removed", _parse(before.get(k)), _ts) for k in removed]
        seqs = _insert_events(con, _ev) if _ev else []
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
            "removed": len(removed), "event_seqs": seqs}


def _parse(doc):
    try:
        return json.loads(doc) if doc else None
    except Exception:
        return None


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


def _save_known(path, items, before: dict, dirty: set, keep_vec: bool = True,
                events=None, auto_events: bool = False) -> dict:
    """The caller named what it touched, so serialise only those, plus anything that vanished."""
    order, live = {}, set()
    for i, r in enumerate(items):
        rid = _field(r, "id") if isinstance(r, dict) else None
        if rid:
            order[rid] = i
            live.add(rid)
    removed = [k for k in before if k not in live]
    now = dict(before)
    for k in removed:
        now.pop(k, None)

    touched, touched_recs = [], []
    for r in items:
        rid = _field(r, "id") if isinstance(r, dict) else None
        if rid in dirty:
            doc = _doc(r, keep_vec)
            if before.get(rid) != doc:
                touched.append((rid, order[rid], doc))
                touched_recs.append(r)
                now[rid] = doc

    _ev = list(events or ())
    if auto_events and (touched or removed):
        _ts = time.time()
        _ev += [_event_row("record.added" if _field(r, "id") not in before else "record.changed",
                           r, _ts) for r in touched_recs]
        _ev += [_event_row("record.removed", _parse(before.get(k)), _ts) for k in removed]
    if not touched and not removed and not _ev:
        return {"snapshot": now, "added": 0, "changed": 0, "removed": 0, "event_seqs": []}
    con = _connect(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        if removed:
            con.executemany("DELETE FROM records WHERE id=?", [(k,) for k in removed])
        if touched:
            con.executemany("INSERT INTO records(id, ord, doc) VALUES(?,?,?) "
                            "ON CONFLICT(id) DO UPDATE SET ord=excluded.ord, doc=excluded.doc",
                            touched)
        seqs = _insert_events(con, _ev) if _ev else []
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
            "changed": len([t for t in touched if t[0] in before]), "removed": len(removed),
            "event_seqs": seqs}
