"""One bad record must not silence the prompt hook (3.16.5, AUDIT-A F-26, F-27, F-29, F-36).

The hook reads a store a repository can ship. Repairing one field at a time on load (`_normalise_loaded`) kept leaking: F-26
fixed `meta`, `text`, `tags`, `links`, `value` and `key`; F-27 the fields used as keys; F-29 found `meta.quarantined` as a
string and `value` as a 400-digit integer; F-36 the numbers. Each is a record that is valid JSON and wrong for one line of the
read, rank or render path, and each cost the user every memory of the project, because the exception ended the whole recall.

The class fix is isolation per record, in the one place that has no knowledge of the fields. When a read raises, this module
scans the store in fixed chunks of CHUNK records. A chunk over which the read passes is kept whole. A chunk over which it raises is
halved until single records are found (a record is "bad" when a read over that record alone raises). The read is repeated
without the bad records, and one stderr line says what was left out. A store with no bad record never comes here.

The work is bounded by TIME_S seconds, not by a count of reads: a store built to hold hundreds of bad records must not turn the
isolation off. When the time runs out, the chunks that were still failing are left out whole, and so are the chunks not yet
examined, and the stderr line says how many records that is. A good record is hidden only in a chunk that failed or was not
examined, never in one that passed.

Nothing is remembered between prompts (AUDIT-A F-37). A record that fails once is not proof for the next prompt, and a left-out
list that outlives the prompt hides good records for good when one run was starved of time. Each prompt decides for itself, within
its own time bound, and says so when the bound ran out.

What this does not do:
- It does not wrap the hook in a blanket `except`. If no single record explains the failure, the original exception is raised
  unchanged, and the hook reports it as before.
- It does not write the store. The bad records stay in the file for the user to inspect; the read leaves them out of this answer.
"""
import copy
import sys
import time

#: Records per first-pass chunk. A read over this many records costs milliseconds.
CHUNK = 32
#: Seconds the search may take before the failing chunks that are left are left out whole.
TIME_S = 1.5
#: Ids named on the stderr line.
NAMED = 5

_SAID = set()


def _clone_over(m, records):
    """A shallow copy of the handle that sees only `records`. The vector matrix is dropped because it is built from the item list,
    and recall observation is off because a probe must not look like a user's recall."""
    c = copy.copy(m)
    c._items = records
    c._mat, c._vec_rowof, c._vec_mean = None, {}, None
    c.observe_recall = False
    return c


def say(path, count, ids, exc, what="read", more="") -> None:
    """One stderr line per store and per process: how many records were left out, which, and the first fault."""
    key = (str(path), what)
    if key in _SAID:
        return
    _SAID.add(key)
    shown = ", ".join(str(i) for i in ids[:NAMED]) + (", ..." if len(ids) > NAMED else "")
    try:
        sys.stderr.write("[inspeximus] %d record(s) of %s could not be %s and are left out of this answer (%s). First fault: "
                         "%s: %s.%s The rest was served and the store is unchanged.%s"
                         % (count, path, what, shown, type(exc).__name__, str(exc)[:120].replace(chr(10), " "), more, chr(10)))
    except Exception:                                           # noqa: BLE001
        pass


# ── the search ────────────────────────────────────────────────────────────────────────────────────────────────────────

def without_bad_records(m, fn, first):
    """`fn(handle)` over the store without the records that make it raise, or `first` re-raised when no single record does.

    `first` is the exception the full read raised. Records are told apart by identity, not by id: a corrupt record may have no
    usable id."""
    items = list(m._items)
    path = getattr(m, "path", "the store")
    deadline = time.monotonic() + TIME_S
    bad = []                    # (record, exception): a read over this record alone raises
    failing = []                # chunks known to fail that the time did not allow to resolve
    unexamined = []             # records of chunks the time did not allow to probe

    def late():
        return time.monotonic() >= deadline

    def fails(chunk):
        try:
            fn(_clone_over(m, chunk))
        except MemoryError:
            raise                                               # the machine ran out of memory: that proves nothing about a record
        except Exception as exc:                                # noqa: BLE001
            return exc
        return None

    def resolve(chunk, exc):
        """`chunk` raises with `exc`: find the single records, or leave it out whole when the time is used up."""
        if len(chunk) == 1:
            bad.append((chunk[0], exc))
            return
        if late():
            failing.append((chunk, exc))
            return
        mid = len(chunk) // 2
        for half in (chunk[:mid], chunk[mid:]):
            if late():
                failing.append((half, exc))
                continue
            e = fails(half)
            if e is not None:
                resolve(half, e)

    for start in range(0, len(items), CHUNK):
        chunk = items[start:start + CHUNK]
        if late():
            unexamined.extend(chunk)
            continue
        exc = fails(chunk)
        if exc is not None:
            resolve(chunk, exc)

    left = [r for r, _ in bad] + [r for c, _ in failing for r in c] + unexamined
    if not left:
        raise first                                              # no record explains it, or two are needed at once
    gone = {id(r) for r in left}
    rest = [r for r in items if id(r) not in gone]
    try:
        result = fn(_clone_over(m, rest))
    except Exception:                                           # noqa: BLE001
        raise first from None
    ids = [(dict.get(r, "id") if isinstance(r, dict) else None) or "?" for r, _ in bad] or ["(not isolated)"]
    held = sum(len(c) for c, _ in failing)
    more = ""
    if held or unexamined:
        more = (" The %.1f s limit ran out: %d record(s) in %d failing chunk(s) and %d record(s) not yet examined were left out "
                "whole, and some of them are good." % (TIME_S, held, len(failing), len(unexamined)))
    exc0 = bad[0][1] if bad else (failing[0][1] if failing else first)
    say(path, len(left), ids, exc0, more=more)
    return result
