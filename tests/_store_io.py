"""Read and write a store file from a test, whatever format it is in.

WHY THIS EXISTS. Dozens of tests here tamper with the store on disk on purpose: that is how you prove
`verify_writes` catches an edit, that a retired record cannot be smuggled back, or that dropping a
receipt does not launder a change. Every one of them opened the file and called `json.loads`, which
was correct while every store was JSON and stopped being correct the moment the library started
writing rows.

Pinning the old format in the tests instead would be worse than useless: the suite would then prove
the guarantees hold on a format users no longer get. So the tampering goes through here, and the
tests keep attacking the real file.
"""
import json
import os

from inspeximus import sqlite_store as ss


def load_store(path) -> list:
    """Every record in the store, in order, from either format."""
    path = str(path)
    if ss.looks_like_sqlite(path):
        return ss.load(path)
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return raw if isinstance(raw, list) else (raw.get("records") or [])


def receipt_files(path) -> list:
    """The receipt files a store has on disk: the sidecar, and the tail beside it in the snapshot-plus-tail format.

    A test that copies, rolls back or deletes the receipts handles all of them, whichever format the store is in."""
    base = str(path) + ".receipts"
    return [p for p in (base + ".json", base + ".tail.jsonl") if os.path.exists(p)]


def load_receipts(path) -> list:
    """Every receipt of the store at `path`, as a list, from either sidecar format (`<store>.receipts.json`, and the
    tail beside it when the store is in the snapshot-plus-tail format). The list is a copy: change it and call
    `save_receipts`. A sidecar that is not JSON, or is not a list of receipts, reads as an empty list."""
    from inspeximus import receipts_tail as rt
    from inspeximus.core import _GENESIS
    snap = str(path) + ".receipts.json"
    if not os.path.exists(snap):
        return []
    res = rt.read(snap, rt.tail_path(snap), _GENESIS)
    return list(res["entries"]) if isinstance(res["entries"], list) else []


def save_receipts(path, entries) -> None:
    """Write `entries` as the store's whole receipt chain, as the array every version reads, and remove the tail.

    This is what an attacker with the directory does: replace the chain. A handle that writes afterwards finds an
    array, and converts it again when the tail is switched on."""
    snap = str(path) + ".receipts.json"
    with open(snap, "w", encoding="utf-8") as fh:
        json.dump(list(entries), fh)
    tail = str(path) + ".receipts.tail.jsonl"
    if os.path.exists(tail):
        os.remove(tail)


def save_store(path, items) -> None:
    """Write the records back, keeping the format the file is already in.

    A store that does not exist yet is written as rows, which is what the library would do.
    """
    path = str(path)
    if not os.path.exists(path) or ss.looks_like_sqlite(path):
        # An out-of-band writer has no baseline, so this is a full reconcile by construction.
        ss.save(path, list(items), ss.snapshot(ss.load(path) if os.path.exists(path) else []))
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(list(items), fh, ensure_ascii=False)


def edit_store(path, fn):
    """Apply `fn(records)` to the store on disk, out of band. Returns what `fn` returned.

    `fn` mutates the list it is given, the way an attacker with file access would.
    """
    items = load_store(path)
    out = fn(items)
    save_store(path, items)
    return out


def store_text(path) -> str:
    """The store file's bytes as text, for the many tests that ask "is this string in the file".

    Decoded with `errors="replace"` on purpose: a row store is not a text file, and the question
    these tests ask is about the payload rather than the container. Reading it as strict UTF-8 raises
    on the SQLite header before it ever reaches the string the test cares about.
    """
    with open(str(path), "rb") as fh:
        return fh.read().decode("utf-8", "replace")
