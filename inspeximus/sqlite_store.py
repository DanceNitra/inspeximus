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
import base64
import hashlib
import hmac
import json
import math
import os
import sqlite3
import struct
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

#: Where a row keeps its embedding (3.17): base64 of little-endian IEEE 754 half floats, under a key no
#: release before 3.17 reads. A JSON list of float32 values cost 186 MB for 13,498 vectors of 1,024
#: dimensions on a real store; this encoding is 37 MB, and on 8 real queries the top 10 was identical
#: to float32's on all 8 (measured 2026-10-07, agora_output/strategy/builder_crew_store_recall.md).
#:
#: A NEW KEY IS THE FORMAT GATE. An older release finds no `vec`, so it ranks those records lexically,
#: and it keeps `vec16` as an unknown field on every row it rewrites: it cannot misread the vector and
#: cannot drop it. `doc_format` is not bumped, because a bump makes the first save a full rewrite, and
#: a handle that does not persist vectors would write every row without its vector.
#:
#: Rows written before 3.17 keep their JSON list, which still reads. A row is re-encoded when it is next
#: written; `Inspeximus.compact_vectors()` re-encodes them all at once.
#:
#: THE RECIPE TRAVELS WITH THE VECTOR. The text is `<tag>:<base64>`, where the tag is `recipe_tag(embed_id,
#: dim)`. A vector made under another embed recipe is then recognised and not ranked: the case is a
#: release before 3.17 that re-embeds under a new model and fails on one row, which keeps the old model's
#: vector (AUDIT-A, vec16 review). The tag lives inside the string and not in a field of its own, because
#: an older release keeps every field it does not know: a separate field would survive that release
#: writing a list under the new model, and would then mislabel it. Base64 has no `:`, so the split is exact.
VEC_KEY = "vec16"

#: The most numbers a stored vector may hold, the cap an embedder's answer already has (F-17, 3.16.4).
#: AUDIT-A F-40: a `vec16` of 5,000,000 half floats (a 10 MB string) decoded to a 160 MB list, and 40 such
#: rows made a store take 72 s to open, before the hook's per-record isolation could act. The length of
#: the text is checked before it is decoded, so a refused vector costs nothing.
MAX_VEC_LEN = 16384
_MAX_VEC16_CHARS = 4 * ((2 * MAX_VEC_LEN + 2) // 3)


def recipe_tag(embed_id, dim) -> str:
    """Eight hex characters naming the embed recipe and the dimension a vector was made with."""
    return hashlib.sha256(("%s|%d" % (embed_id or "", int(dim))).encode("utf-8")).hexdigest()[:8]


def encode_vec(vec):
    """`vec` as base64 float16, or None when a value does not fit a half float (the caller keeps the list)."""
    try:
        return base64.b64encode(struct.pack("<%de" % len(vec), *vec)).decode("ascii")
    except (struct.error, OverflowError, TypeError, ValueError):
        return None


def decode_vec(text):
    """The list `encode_vec` wrote, or None for text that is not one: bad base64, an odd byte count, or
    a value that is not finite. A record whose vector does not decode is ranked lexically, not refused."""
    if not isinstance(text, str) or not text or len(text) > _MAX_VEC16_CHARS:
        return None
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        return None
    if not raw or len(raw) % 2:
        return None
    vals = struct.unpack("<%de" % (len(raw) // 2), raw)
    # ONE PASS IN C, NOT A PYTHON LOOP OVER EVERY VALUE (3.17). A sum of finite half floats is finite (at most
    # 16,384 x 65,504), and a NaN or an infinity anywhere makes it non-finite, inf + -inf included. The loop it
    # replaces cost about a second on every open of a 13,494-vector store, more than the decode itself.
    if not math.isfinite(sum(vals)):
        return None
    return list(vals)


def _decode_row(rec) -> None:
    """Turn a stored `vec16` back into the `vec` list the library ranks with, in place. A `vec` list
    already on the row wins: only a release before 3.17 writes one beside `vec16`, and it wrote it
    later. Replaces one key with one key, so the caller's "normalisation added no key" test still
    holds and the stored text stays the save baseline."""
    enc = rec.pop(VEC_KEY, None)
    if enc is None:
        return
    have = rec.get("vec")
    if isinstance(have, list) and have:
        return
    tag = None
    if isinstance(enc, str) and ":" in enc[:9]:
        tag, _, enc = enc.partition(":")
    vec = decode_vec(enc)
    if vec is not None:
        rec["vec"] = vec
        if tag:
            rec["vec_recipe"] = tag
    elif "vec" not in rec:
        rec["vec"] = None

MAGIC = b"SQLite format 3\x00"


def _bump_generation(con) -> int:
    """Increment the store's write generation inside the caller's transaction and return it (A-37).

    A row store is written in place and its size stays page-aligned, so (mtime_ns, size) did not move
    for a peer's write in the same clock tick, of any length, and a reader's refresh served the value
    it already had. The generation moves with every committed write, and only with one."""
    con.execute("INSERT INTO meta(k, v) VALUES('generation', '1') "
                "ON CONFLICT(k) DO UPDATE SET v=CAST(CAST(v AS INTEGER) + 1 AS TEXT)")
    return int(con.execute("SELECT v FROM meta WHERE k='generation'").fetchone()[0])


def generation(path):
    """The store's write generation, or None when the file has none (written before 3.15.4) or cannot
    be read. None is never equal to a recorded generation; callers fall back to the stat signature."""
    try:
        con = _connect(path)
        try:
            row = con.execute("SELECT v FROM meta WHERE k='generation'").fetchone()
        finally:
            con.close()
        return int(row[0]) if row and row[0] is not None else None
    except Exception:                                       # noqa: BLE001
        return None


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
# `INSPEXIMUS_BUSY_TIMEOUT_S` overrides it (3.5.2) for an operator who has measured a longer
# foreign hold and for tests that need a short one; the constraint above still applies, and
# `Inspeximus._save_rows_retrying` multiplies it by the retry count.
# 3.18: the environment may raise it and never lower it (a project could make every write fail fast under contention);
# the user's config `store.busy_timeout_s` sets any value.
try:
    from . import _envpolicy as _ep
    BUSY_TIMEOUT_S = _ep.at_least("INSPEXIMUS_BUSY_TIMEOUT_S", BUSY_TIMEOUT_S, float)
except Exception:                                               # noqa: BLE001 -- the constant stands
    pass


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
    return load_with_docs(path)[0]


def load_with_docs(path):
    """`load`, plus the stored text of each returned record, in the same order: ([records], [texts]).

    The texts are what `snapshot_from_docs` reuses as the save baseline, so opening a store does not
    serialise every record again (AUDIT-B B-08)."""
    if not os.path.exists(str(path)):
        return [], []
    con = _connect(path)
    try:
        rows = con.execute("SELECT doc FROM records ORDER BY ord").fetchall()
    finally:
        con.close()
    out, docs = [], []
    for (doc,) in rows:
        try:
            rec = json.loads(doc)
            if isinstance(rec, dict) and VEC_KEY in rec:
                _decode_row(rec)
            out.append(rec)
        except Exception:
            # ONE UNREADABLE ROW MUST NOT COST THE STORE. The JSON path fails whole-file: a single
            # bad byte makes every record unreachable, and this project lost its coding store to
            # exactly that three times in ten days. Here the blast radius is one record.
            continue
        docs.append(doc)
    return out, docs


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
    # THE VECTOR IS WRITTEN AS FLOAT16 (3.17), see VEC_KEY. The round trip is exact from the second
    # write on, so the stored text of an unchanged row serialises back to the same bytes.
    # The recipe tag goes INTO the vec16 text and never stays a field of its own, see VEC_KEY.
    _enc = None
    if keep_vec:
        _v = _field(rec, "vec")          # dict.get: a tracked record counts every `get` it serves
        if isinstance(_v, list) and _v:
            _enc = encode_vec(_v)
    if _enc is not None:
        _tag = _field(rec, "vec_recipe")
        rec = {k: v for k, v in rec.items() if k not in ("vec", "vec_recipe")}
        rec[VEC_KEY] = ("%s:%s" % (_tag, _enc)) if isinstance(_tag, str) and _tag else _enc
    elif "vec_recipe" in rec:
        rec = {k: v for k, v in rec.items() if k != "vec_recipe"}
    return json.dumps(rec, sort_keys=True, default=str, allow_nan=False, ensure_ascii=False)


def slack(path) -> dict:
    """The free pages inside the file: space a row that shrank or was deleted left behind (3.17).

    SQLite does not give that space back to the file system by itself. Measured on a 13,489-record store:
    after `compact_vectors` rewrote every vector as float16, the file stayed at 254 MB with 140 MB of it
    free pages. `secure_delete` has zeroed them, so they hold no erased content; they are only size."""
    con = _connect(path)
    try:
        ps = con.execute("PRAGMA page_size").fetchone()[0]
        pages = con.execute("PRAGMA page_count").fetchone()[0]
        free = con.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        con.close()
    return {"file_bytes": os.path.getsize(str(path)), "free_pages": free, "free_bytes": free * ps,
            "used_bytes": (pages - free) * ps}


def vacuum(path, wait_s: float = 2.0) -> dict:
    """Rewrite the file without its free pages (3.17). Waits at most `wait_s` for readers to finish, then
    gives up and reports the slack instead: a VACUUM needs the file to itself, and a reader holding it is
    never interrupted. `secure_delete` stays on for the rewrite, as it is for every connection."""
    before = slack(path)
    con = sqlite3.connect(str(path), timeout=wait_s, isolation_level=None)
    try:
        con.execute("PRAGMA secure_delete=ON")
        con.execute("VACUUM")
    except sqlite3.OperationalError as e:
        return {"vacuumed": False, "reason": str(e), "slack_bytes": before["free_bytes"],
                "file_bytes": before["file_bytes"]}
    finally:
        con.close()
    after = slack(path)
    return {"vacuumed": True, "freed_bytes": before["file_bytes"] - after["file_bytes"],
            "file_bytes": after["file_bytes"], "slack_bytes": after["free_bytes"]}


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


def _same(now_doc, before_doc) -> bool:
    """Does the baseline already hold `now_doc`?

    The baseline holds a row's STORED text when `snapshot_from_docs` reused it, where it used to hold
    this writer's serialisation of the loaded record. For every row this writer stored the two are the
    same bytes. For a row another writer stored with different key order or escapes they are not, and
    comparing bytes would call an unchanged record changed, rewrite it and emit an event for it. So a
    byte mismatch is settled by serialising the stored text the way the old baseline was built: same
    answer as before, and the cost falls only on rows whose bytes differ (AUDIT-B B-08)."""
    if now_doc == before_doc:
        return True
    if before_doc is None:
        return False
    try:
        return _doc(json.loads(before_doc)) == now_doc
    except Exception:
        return False


def snapshot_from_docs(items, docs, reuse, keep_vec: bool = True) -> dict:
    """`snapshot`, reusing the stored text of every record whose `reuse` flag is set.

    `snapshot` serialised every record at every open: 1.27 s of a 5.51 s open on a 67,165-record
    store, measured 2026-09-27, paid by opens that never save. A record that normalisation did not
    touch serialises back to exactly the text it was read from when the store is in the current
    encoding, which is what the doc_format marker certifies: measured on two real stores, 78,048 of
    78,048 such rows. A record whose `vec` this writer would drop, or that carries an underscore key,
    is serialised as before (AUDIT-B B-08).
    """
    out = {}
    for r, doc, ok in zip(items, docs, reuse):
        if not isinstance(r, dict):
            continue
        rid = _field(r, "id")
        if not rid:
            continue
        if ok and (keep_vec or "vec" not in r) and not any(k[:1] == "_" for k in r):
            out[rid] = doc
        else:
            out[rid] = _doc(r, keep_vec)
    return out


def _event_row(kind, rec, ts):
    """A content-free event row for one record: (ts, type, memory_id, agent, tenant, payload)."""
    if not isinstance(rec, dict):
        return (ts, kind, None, None, None, "{}")
    payload = {k: rec.get(k) for k in _EVENT_FIELDS if rec.get(k) is not None}
    return (ts, kind, _field(rec, "id"), rec.get("owner_agent"), rec.get("tenant"),
            json.dumps(payload, ensure_ascii=False, sort_keys=True))


#: What replaces a record's key in the journal once the record is gone.
_KEY_REDACTED = "key_redacted"


#: What a journal row's `agent` or `tenant` column holds once the record it describes is gone: this prefix
#: and an HMAC of the id under the store's event salt (`pseudonym`).
PSEUDONYM_PREFIX = "pseud:"


def _event_salt_path(path) -> str:
    """Where the event salt for the store at `path` lives: the key home, as for the receipt key and the
    chain head (`INSPEXIMUS_KEY_HOME`, else APPDATA, else XDG_CONFIG_HOME, else ~/.config), under
    `inspeximus/salts/`, named by a hash of the store's absolute path."""
    from ._keyhome import key_home
    home = key_home(path)                        # 3.16.4, F-13
    tag = hashlib.sha256(os.path.abspath(str(path)).encode("utf-8", "replace")).hexdigest()[:16]
    return os.path.join(home, "inspeximus", "salts", tag + ".salt")


def _event_salt(path, create: bool) -> "bytes | None":
    """The store's event salt, minted on first use when `create`. None when there is none and `create` is
    False, or when the key home resolves inside the store's own directory: a salt kept beside the store
    lets whoever holds the store test a guessed id against the pseudonym, which is what the salt is for.
    """
    sp = _event_salt_path(path)
    try:
        from .core import _guard_key_location
        _guard_key_location(os.path.dirname(sp), path)
    except ValueError:
        return None
    try:
        with open(sp, "r", encoding="ascii") as fh:
            return bytes.fromhex(fh.read().strip())
    except FileNotFoundError:
        if not create:
            return None
    except (OSError, ValueError):
        return None
    try:
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        fd = os.open(sp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(os.urandom(32).hex())
    except FileExistsError:
        pass                                   # another process minted it first: read theirs
    except OSError:
        return None
    try:
        with open(sp, "r", encoding="ascii") as fh:
            return bytes.fromhex(fh.read().strip())
    except (OSError, ValueError):
        return None


def pseudonym(path, value, create: bool = False) -> "str | None":
    """The value that replaces `value` in the `agent` and `tenant` columns of a removed record's journal
    rows. None when `value` is empty or the store has no salt (and `create` is False). A value that is
    already a pseudonym is returned unchanged."""
    if not value:
        return None
    value = str(value)
    if value.startswith(PSEUDONYM_PREFIX):
        return value
    salt = _event_salt(path, create)
    if salt is None:
        return None
    return PSEUDONYM_PREFIX + hmac.new(salt, value.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _redact_removed_keys(con, ids, path=None) -> int:
    """Take the KEY out of every journal row of a record that was removed, in the caller's transaction.

    A key is a label the caller chose, and a caller can put a person in it ("jane-invoice-email"). The
    record's row is deleted and its tombstone is content-free, but the journal kept `key` in the payload
    of the record's `record.added`, `record.changed` and `record.removed` rows: measured on 3.15.4, after
    `forget_subject("jane.example")` the file no longer held the address or the domain, and still held the
    key five times, all in `memory_events`. The rows stay, with the id, the type and the status, so a
    reader tailing by `seq` sees no gap; only the label goes, and `key_redacted` says it did. With
    `secure_delete` on, the old bytes are zeroed by this UPDATE, not left in a free page.
    """
    ids = [i for i in ids if i]
    n = 0
    memo = {}                                  # one salt read and one HMAC per distinct id, not per row
    for at in range(0, len(ids), 500):
        chunk = ids[at:at + 500]
        marks = ",".join("?" * len(chunk))
        rows = con.execute("SELECT seq, payload, agent, tenant FROM memory_events WHERE memory_id IN (%s)"
                           % marks, chunk).fetchall()
        upd = []
        for seq, payload, agent, tenant in rows:
            pl = _parse(payload)
            moved = False
            if isinstance(pl, dict) and "key" in pl:
                pl.pop("key")
                pl[_KEY_REDACTED] = True
                moved = True
            # THE AGENT AND TENANT IDS GO TOO (3.16.2). A caller names a tenant or an agent, and can put a
            # person in it: measured by AUDIT-A on 3.15.8, `for_tenant("jane-tenant-77")` was still in the
            # store file twice after `forget_subject`, in this record's `record.added` and `record.removed`
            # rows. Each is replaced by an HMAC under a salt kept in the key home, so a tenant handle still
            # finds its own events (`poll_events` compares both forms) and nobody holding only the store
            # can recover or confirm the id. Without a salt (the key home sits inside the store's
            # directory) the columns are cleared: the id goes, and only the tenant filter is lost.
            new_agent, new_tenant = agent, tenant
            if path is not None:
                if agent and not str(agent).startswith(PSEUDONYM_PREFIX):
                    new_agent = memo[agent] if agent in memo else memo.setdefault(agent, pseudonym(path, agent, True))
                if tenant and not str(tenant).startswith(PSEUDONYM_PREFIX):
                    new_tenant = memo[tenant] if tenant in memo else memo.setdefault(tenant, pseudonym(path, tenant, True))
            if moved or (new_agent, new_tenant) != (agent, tenant):
                upd.append((json.dumps(pl, ensure_ascii=False, sort_keys=True) if isinstance(pl, dict) else payload,
                            new_agent, new_tenant, seq))
        if upd:
            con.executemany("UPDATE memory_events SET payload=?, agent=?, tenant=? WHERE seq=?", upd)
            n += len(upd)
    return n


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
    changed = [k for k in now if k in before and (rewrite_all or not _same(now[k], before[k]))]
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
        # SETS for the membership test. `added` and `changed` are lists, and asking a list is a scan,
        # so a save that adds or rewrites every row (a JSON-to-rows migration, the rewrite_all after
        # an encoding upgrade) was O(rows^2): 0.34 s at 8,000 rows, about 3x per doubling (AUDIT-B B-15).
        _written = set(added) | set(changed)
        stale_order = [(order[k], k) for k in now if k not in _written and k in order]
        if stale_order:
            # ONLY THE ROWS WHOSE ORDER MOVED. `ord<>?` below already made every other row a no-op, but
            # each was still a statement: 67,165 of them and 1.18 s per full reconcile on a copy of a
            # real hook store, paid at every session boundary. One read of the stored order, in this
            # transaction, selects the same rows the WHERE clause would have changed (AUDIT-B B-09).
            _on_disk = dict(con.execute("SELECT id, ord FROM records").fetchall())
            stale_order = [(o, k) for o, k in stale_order if _on_disk.get(k) != o]
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
                    if not rewrite_all or not _same(now[k], before.get(k))]
            _ev += [_event_row("record.removed", _parse(before.get(k)), _ts) for k in removed]
        seqs = _insert_events(con, _ev) if _ev else []
        if removed:
            _redact_removed_keys(con, removed, path)       # whatever wrote the earlier rows, and even with events off
        gen = _bump_generation(con)                  # in this transaction: it commits or rolls back with it
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
            "removed": len(removed), "event_seqs": seqs, "generation": gen}


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
            if not _same(doc, before.get(rid)):
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
        if removed:
            _redact_removed_keys(con, removed, path)       # whatever wrote the earlier rows, and even with events off
        gen = _bump_generation(con)                  # in this transaction: it commits or rolls back with it
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
            "event_seqs": seqs, "generation": gen}
