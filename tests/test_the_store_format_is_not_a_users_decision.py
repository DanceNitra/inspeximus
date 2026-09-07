"""The library picks the storage format. The user is never asked, and never loses data over it.

WHY THIS EXISTS. The row store shipped reachable only by a manual migration, so the format was chosen
by the bytes already in the file: a new store came out as JSON, and the faster, concurrency-safe path
went only to whoever knew the row store existed and converted by hand. "SQLite or JSON?" is not a
question a memory library gets to ask its users.

WHAT THESE PIN. A new store is written as rows and its FIRST record survives a reopen. An existing
JSON store is converted on open, keeps every record and its order, and leaves the original beside it
so the conversion can be undone. An encrypted store is never converted. And an operator who must keep
the old format has one switch that works.

THE FIRST-RECORD TEST IS NOT PARANOIA. It failed when it was written: the save path seeded its
on-disk baseline from the records in memory, so every record looked already-written and the first
write did nothing. `remember()` returned an id, `flush()` succeeded, and the store read back empty.
"""
import json
import os
import tempfile

import pytest

from inspeximus import Inspeximus, sqlite_store as ss


@pytest.fixture(autouse=True)
def _no_pinned_format(monkeypatch):
    monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT", raising=False)


def _path(name="memory.json"):
    return os.path.join(tempfile.mkdtemp(), name)


def test_a_new_store_is_written_as_rows_without_being_asked():
    p = _path()
    m = Inspeximus(path=p)
    m._save_min_s = 0
    m.remember("the first thing anyone stores", key="k", mtype="fact")
    m.flush()
    assert ss.looks_like_sqlite(p), "a new store came out as JSON, so the user must still know to convert"


def test_the_very_first_record_survives_a_reopen():
    """The control that matters. A format nobody chose is worthless if it drops the opening write."""
    p = _path()
    m = Inspeximus(path=p)
    m._save_min_s = 0
    m.remember("the first thing anyone stores", key="k", mtype="fact")
    m.flush()
    back = Inspeximus(path=p)._items
    assert len(back) == 1, "the first record never reached disk: read back %d of 1" % len(back)
    assert back[0]["text"] == "the first thing anyone stores"


def test_an_existing_json_store_is_converted_on_open():
    p = _path("old.json")
    items = [{"id": "a%02d" % i, "text": "record %d" % i, "ts": float(i), "status": "active"}
             for i in range(40)]
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(items, fh)
    m = Inspeximus(path=p)
    assert ss.looks_like_sqlite(p), "an existing JSON store was left on the slow path"
    assert [r["id"] for r in m._items] == [r["id"] for r in items], \
        "the conversion changed the records or their order"


def test_the_conversion_can_be_undone():
    """A row store cannot be read by 2.26.1 or earlier, so the original has to still be there."""
    p = _path("old.json")
    items = [{"id": "a1", "text": "one", "ts": 1.0, "status": "active"}]
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(items, fh)
    Inspeximus(path=p)
    backup = p + ".pre-rows.bak"
    assert os.path.exists(backup), "the conversion left no way back"
    with open(backup, encoding="utf-8") as fh:
        assert json.load(fh) == items, "the backup is not the store we started with"
    os.replace(backup, p)
    assert not ss.looks_like_sqlite(p), "restoring the backup did not restore the old format"
    with open(p, encoding="utf-8") as fh:
        assert json.load(fh) == items, "the restored store is not readable as plain JSON"
    # Rolling back the FILE without rolling back the library converts it again, which is correct: this
    # version reads rows. The rollback that matters is file plus version together, and what it needs is
    # exactly this: the original bytes, under a name the operator can find.


def test_a_pinned_format_is_honoured_in_both_directions(monkeypatch):
    """The escape hatch for someone whose other tooling reads the file."""
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    p = _path()
    m = Inspeximus(path=p)
    m._save_min_s = 0
    m.remember("x", key="k", mtype="fact")
    m.flush()
    assert not ss.looks_like_sqlite(p), "a pinned JSON store was written as rows"

    q = _path("existing.json")
    with open(q, "w", encoding="utf-8") as fh:
        json.dump([{"id": "a1", "text": "one", "ts": 1.0, "status": "active"}], fh)
    Inspeximus(path=q)
    assert not ss.looks_like_sqlite(q), "a pinned store was converted anyway"
    assert not os.path.exists(q + ".pre-rows.bak")


def test_an_encrypted_store_is_never_converted():
    p = _path("enc.json")
    m = Inspeximus(path=p, encrypt_passphrase="a passphrase for the test")
    m._save_min_s = 0
    m.remember("secret", key="k", mtype="fact")
    m.flush()
    assert not ss.looks_like_sqlite(p), "an encrypted store was written as rows"
    back = Inspeximus(path=p, encrypt_passphrase="a passphrase for the test")
    assert len(back._items) == 1
