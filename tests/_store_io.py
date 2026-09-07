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
