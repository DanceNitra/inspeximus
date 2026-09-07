"""Erasure has to remove the bytes, from the store and from any copy this library made itself.

WHY THIS EXISTS. The row store changed two things about deletion that nothing in the suite asked
about, and both were found by looking rather than by a failing test.

  1. SQLITE DOES NOT ZERO A DELETED ROW. By default it marks the row's pages free and leaves their
     content in the file until something reuses them, so an erased record stays readable with
     `strings`. Measured before `PRAGMA secure_delete=ON`: after `forget_subject`, a byte search for
     the erased text found it in the row store and did not find it in the JSON store, because the
     JSON path rewrites the whole file. The surface that sells this library is the one that proves an
     id is really gone.

  2. THE CONVERSION BACKUP IS A COPY WE MADE. Converting a JSON store to rows leaves the original
     beside it as `.pre-rows.bak` so the upgrade can be undone. That file holds every record,
     including the ones a subject later asks us to erase, and nothing in the erasure path could see
     it: measured, the erased text was gone from the store and the tombstone chain and still sat in
     the backup.

THE CONTROLS. Each arm asserts the text was in the file BEFORE the erasure, or "not found after"
would pass on a store that never held it. The backup arm has a second control: with no erasure the
backup must still be there, or the test cannot tell "erasure removed it" from "it is removed always".
"""
import glob
import json
import os
import tempfile

import pytest

from inspeximus import Inspeximus, sqlite_store as ss

SUBJECT_TEXT = "Ada Lovelace lives in Turin"


@pytest.fixture(autouse=True)
def _no_pinned_format(monkeypatch):
    monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT", raising=False)


def _fresh_store(as_rows, monkeypatch):
    if not as_rows:
        monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "memory.json")
    m = Inspeximus(path=p)
    m._save_min_s = 0
    m.remember(SUBJECT_TEXT, key="ada::city", mtype="fact", source={"doc": "ada-src"})
    m.remember("a filler record so the file is not a single row", key="f::1", mtype="fact",
               source={"doc": "other"})
    m.flush()
    assert ss.looks_like_sqlite(p) is as_rows, "the fixture is not in the format it claims"
    return m, p


def _holds(path):
    with open(path, "rb") as fh:
        return SUBJECT_TEXT.encode("utf-8") in fh.read()


@pytest.mark.parametrize("as_rows", [False, True], ids=["json", "rows"])
def test_the_bytes_leave_the_store_file(as_rows, monkeypatch):
    m, p = _fresh_store(as_rows, monkeypatch)
    assert _holds(p), "the control failed: the text was never in the file, so its absence proves nothing"
    m.forget_subject("ada-src", request_id="DSAR-1")
    m.flush()
    assert not _holds(p), (
        "the erased text is still readable in the store file. A deleted row whose bytes remain is "
        "exactly the soft-delete failure the erasure certificate exists to disprove.")


def _converted_store_with_a_backup():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "memory.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump([{"id": "a1", "text": SUBJECT_TEXT, "ts": 1.0, "status": "active",
                    "source": {"doc": "ada-src"}, "key": "ada::city", "mtype": "fact"},
                   {"id": "a2", "text": "someone else entirely", "ts": 2.0, "status": "active",
                    "source": {"doc": "other"}, "key": "o::x", "mtype": "fact"}], fh)
    m = Inspeximus(path=p)
    m._save_min_s = 0
    backup = p + ".pre-rows.bak"
    assert os.path.exists(backup), "the fixture did not convert, so there is no backup to test"
    assert _holds(backup)
    return m, p, backup


def test_an_erasure_removes_the_conversion_backup():
    m, p, backup = _converted_store_with_a_backup()
    m.forget_subject("ada-src", request_id="DSAR-1")
    m.flush()
    assert not os.path.exists(backup), (
        "the JSON copy this library made during the format conversion survived an erasure request, "
        "with the erased records still in it")
    left = [f for f in glob.glob(os.path.join(os.path.dirname(p), "*")) if _holds(f)]
    assert not left, "the erased text survives in %s" % [os.path.basename(f) for f in left]


def test_the_backup_survives_when_nothing_is_erased():
    """CONTROL. Without this, deleting the backup unconditionally would pass the test above."""
    _m, _p, backup = _converted_store_with_a_backup()
    assert os.path.exists(backup), "the rollback copy was removed without any erasure being requested"


DIACRITIC = "Zuzana Drahošová"


@pytest.mark.parametrize("as_rows", [False, True], ids=["json", "rows"])
def test_a_name_with_diacritics_is_in_the_file_as_itself(as_rows, monkeypatch):
    """A residue scan reads bytes, so the store has to hold the text as text.

    `json.dumps` escapes non-ASCII by default, and the row writer shipped with that default: the name
    went to disk as `Draho\u0161ov\u00e1`, the UTF-8 bytes of the name were not in the file, and
    `scan_residue` reported nothing found. A residue report is the artefact that tells a regulator a
    subject's data is gone, so a scanner that cannot see the value is worse than no scanner. Measured
    on both formats: the JSON store found it, the row store did not.
    """
    from inspeximus.erasure_residue import scan_residue
    if not as_rows:
        monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "memory.json")
    m = Inspeximus(path=p)
    m._save_min_s = 0
    m.remember(DIACRITIC + " lives in Košice", key="z", source={"doc": "z"})
    m.flush()
    assert ss.looks_like_sqlite(p) is as_rows

    with open(p, "rb") as fh:
        assert DIACRITIC.encode("utf-8") in fh.read(), (
            "the name is not in the store file as UTF-8, so a byte scan cannot find it")
    assert scan_residue(d, [DIACRITIC]).get("findings"), (
        "scan_residue reported no residue for a value that is in the store")
    assert DIACRITIC in Inspeximus(path=p)._items[0]["text"], "the record did not survive the trip"


def test_a_store_written_by_an_older_row_writer_is_brought_forward():
    """CONTROL on the upgrade path: fixing the encoding does not reach rows already on disk.

    A row keeps its bytes until something rewrites it, so a store written before the fix stays
    invisible to the scanner. The library asks for one full reconcile when the marker is behind.
    """
    d = tempfile.mkdtemp()
    p = os.path.join(d, "memory.json")
    rec = {"id": "a1", "text": DIACRITIC + " lives in Košice", "ts": 1.0, "status": "active"}
    # Write the way the OLD writer did: escaped, and no format marker.
    import json as _json
    import sqlite3
    # Build it escaped from the start. Writing the literal first and updating it afterwards leaves
    # the literal bytes in the file's free pages, so the fixture would not be in the state it claims.
    ss.save(p, [], {})
    con = sqlite3.connect(p)
    con.execute("INSERT INTO records(id, ord, doc) VALUES(?,?,?)",
                ("a1", 0, _json.dumps(rec, sort_keys=True, default=str)))
    con.execute("DELETE FROM meta WHERE k='doc_format'")
    con.commit()
    con.close()
    assert ss.needs_rewrite(p), "the fixture is not in the old format, so this proves nothing"
    with open(p, "rb") as fh:
        assert DIACRITIC.encode("utf-8") not in fh.read(), "the fixture is not actually escaped"

    m = Inspeximus(path=p)
    m._save_min_s = 0
    m.flush()
    assert not ss.needs_rewrite(p), "the store was not brought forward"
    with open(p, "rb") as fh:
        assert DIACRITIC.encode("utf-8") in fh.read(), (
            "the rewrite did not put the literal text on disk")


def test_the_certificate_says_what_happened_to_the_rollback_copy(monkeypatch):
    """Removing the copy is right. Removing it without saying so is what an auditor cannot accept."""
    monkeypatch.delenv("INSPEXIMUS_KEEP_CONVERSION_BACKUP", raising=False)
    m, _p, backup = _converted_store_with_a_backup()
    assert os.path.exists(backup), "the fixture never produced a conversion backup, so it tests nothing"

    m.forget_subject("ada-src", request_id="DSAR-1")
    assert not os.path.exists(backup), "the erased records are still in the copy the library made"

    cert = m.erasure_certificate()
    assert cert["conversion_backup"]["state"] == "removed", cert["conversion_backup"]
    assert cert["conversion_backup"]["erasure_reached_it"] is True
    assert backup in cert["conversion_backup"]["path"], "the certificate does not name the file"


def test_an_operator_can_keep_the_rollback_and_the_certificate_declares_it(monkeypatch):
    """The opt-out is an honest scope reduction: the file stays, and the certificate says so."""
    monkeypatch.setenv("INSPEXIMUS_KEEP_CONVERSION_BACKUP", "1")
    m, _p, backup = _converted_store_with_a_backup()
    assert os.path.exists(backup), "the fixture never produced a conversion backup"

    m.forget_subject("ada-src", request_id="DSAR-2")
    assert os.path.exists(backup), "the opt-out did not keep the rollback copy"

    cert = m.erasure_certificate()
    assert cert["conversion_backup"]["state"] == "kept", cert["conversion_backup"]
    assert cert["conversion_backup"]["erasure_reached_it"] is False, (
        "the certificate claims the erasure reached a file that still holds the records")
