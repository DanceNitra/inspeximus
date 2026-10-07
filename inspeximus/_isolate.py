"""One bad record must not silence the prompt hook (3.16.5, AUDIT-A F-26, F-27, F-29).

The hook reads a store a repository can ship. Repairing one field at a time on load (`_normalise_loaded`) kept leaking: F-26
fixed `meta`, `text`, `tags`, `links`, `value` and `key`; F-27 the fields used as keys; F-29 found `meta.quarantined` as a
string and `value` as a 400-digit integer. Each is a record that is valid JSON and wrong for one line of the read, rank or render
path, and each cost the user every memory of the project, because the exception ended the whole recall.

The class fix is isolation per record, in the one place that has no knowledge of the fields. When a read raises, this module
finds the records that make it raise by halving the store (a record is "bad" when a read over that record alone raises), leaves
them out of one more read over the rest, and says so on stderr once. The first read is the only path that costs anything: a store
with no bad record never comes here.

What this does not do:
- It does not wrap the hook in a blanket `except`. If no single record explains the failure, the original exception is raised
  unchanged, and the hook reports it as before.
- It does not write. The bad records stay in the file for the user to inspect; the read leaves them out of this answer only.
"""
import copy
import sys

#: Reads over a subset of the store before the search gives up and the original exception is raised.
MAX_PROBES = 400
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


def say(path, count, ids, exc, what="read") -> None:
    """One stderr line per store and per process: how many records were left out, which, and the first fault."""
    key = (str(path), what)
    if key in _SAID:
        return
    _SAID.add(key)
    shown = ", ".join(str(i) for i in ids[:NAMED]) + (", ..." if len(ids) > NAMED else "")
    try:
        sys.stderr.write("[inspeximus] %d record(s) of %s could not be %s and are left out of this answer (%s). First fault: "
                         "%s: %s. The rest was served and the store is unchanged.%s"
                         % (count, path, what, shown, type(exc).__name__, str(exc)[:120].replace(chr(10), " "), chr(10)))
    except Exception:                                           # noqa: BLE001
        pass


def without_bad_records(m, fn, first):
    """`fn(handle)` over the store without the records that make it raise, or `first` re-raised when no single record does.

    `first` is the exception the full read raised. Records are told apart by identity, not by id: a corrupt record may have no
    usable id."""
    items = list(m._items)
    probes = [0]
    bad = []

    def fails(chunk):
        probes[0] += 1
        try:
            fn(_clone_over(m, chunk))
        except Exception as exc:                                # noqa: BLE001
            return exc
        return None

    def search(chunk):
        if probes[0] >= MAX_PROBES:
            return
        exc = fails(chunk)
        if exc is None:
            return
        if len(chunk) == 1:
            bad.append((chunk[0], exc))
            return
        mid = len(chunk) // 2
        before = len(bad)
        search(chunk[:mid])
        search(chunk[mid:])
        if len(bad) == before and probes[0] < MAX_PROBES:
            return                                               # a failure that needs two records at once: not isolable

    if len(items) > 1:                                          # the full read is the failure we were handed: split it
        mid = len(items) // 2
        search(items[:mid])
        search(items[mid:])
    if not bad:
        raise first
    gone = {id(r) for r, _ in bad}
    rest = [r for r in items if id(r) not in gone]
    result = fn(_clone_over(m, rest))
    ids = [(dict.get(r, "id") if isinstance(r, dict) else None) or "?" for r, _ in bad]
    say(getattr(m, "path", "the store"), len(bad), ids, bad[0][1])
    return result
