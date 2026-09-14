"""A handle that opens a store while a peer is creating it must not replace the peer's file.

THE WINDOW. `sqlite3.connect` creates the file before the first commit writes the SQLite header. A
second handle opening in that window sees a file that exists and does not look like rows, reads it
as a JSON store with nothing in it, and until 2.27.6 converted that nothing to rows and put the
result over the peer's file with `os.replace`, outside any lock. The peer had already been told its
first record was stored. CI reported it as `w7:r0` and `w1:r0`;
`probes/is_the_first_write_of_a_fresh_handle_the_one_that_goes_missing.py` measured 3 of 1,600
first writes lost on ext4 when the file did not exist at start, 0 of 1,600 when it did.

THE TEST. The window is reproduced without timing: the file is created empty, the second handle's
read of it is hooked so that the peer creates the real row store right after that read, and the
second handle then carries on into its migration. On 2.27.5 the peer's record is gone. On 2.27.6
the migration takes the store lock, reads the header again, finds a row store, and loads it.
"""
from __future__ import annotations

import os
import pathlib

import pytest

from inspeximus import Inspeximus


def _open_in_the_creation_window(tmp_path, monkeypatch):
    """The late handle reads the empty file, asks "is this rows?" and gets no; the peer's commit
    lands between that answer and whatever the late handle does with it."""
    from inspeximus import sqlite_store
    db = tmp_path / "s.json"
    peer = Inspeximus(path=str(db))            # opened on nothing: the creator-to-be
    db.write_bytes(b"")                        # what sqlite3.connect leaves before the first commit
    orig_read = pathlib.Path.read_bytes
    orig_looks = sqlite_store.looks_like_sqlite
    state = {"read": False, "fired": False}

    def read_marks(self):
        if self == db:
            state["read"] = True
        return orig_read(self)

    def header_check_then_the_peer_commits(path):
        answer = orig_looks(path)
        if state["read"] and not state["fired"] and pathlib.Path(path) == db:
            state["fired"] = True
            os.remove(db)                      # the peer's connect has not committed yet ...
            peer.remember("peer r0", mtype="fact")   # ... and now it has: header, schema, row
        return answer

    monkeypatch.setattr(pathlib.Path, "read_bytes", read_marks)
    monkeypatch.setattr(sqlite_store, "looks_like_sqlite", header_check_then_the_peer_commits)
    late = Inspeximus(path=str(db))
    monkeypatch.setattr(pathlib.Path, "read_bytes", orig_read)
    monkeypatch.setattr(sqlite_store, "looks_like_sqlite", orig_looks)
    assert state["fired"], "the header check never ran after the read; the test measured nothing"
    return db, peer, late


def test_the_peers_first_record_survives_a_late_opener(tmp_path, monkeypatch):
    db, peer, late = _open_in_the_creation_window(tmp_path, monkeypatch)
    kept = {r.get("text") for r in Inspeximus(path=str(db)).items}
    assert "peer r0" in kept, "the late opener replaced the peer's freshly created store"


def test_the_late_opener_still_writes_its_own_record(tmp_path, monkeypatch):
    db, peer, late = _open_in_the_creation_window(tmp_path, monkeypatch)
    late.remember("late r0", mtype="fact")
    kept = {r.get("text") for r in Inspeximus(path=str(db)).items}
    assert {"peer r0", "late r0"} <= kept


def test_control_a_real_json_store_is_still_migrated(tmp_path):
    db = tmp_path / "s.json"
    db.write_text("[]", encoding="utf-8")
    m = Inspeximus(path=str(db))
    m.remember("only", mtype="fact")
    from inspeximus import sqlite_store
    assert sqlite_store.looks_like_sqlite(db), "the control did not migrate; the fixture is wrong"
    assert (tmp_path / "s.json.pre-rows.bak").exists()
