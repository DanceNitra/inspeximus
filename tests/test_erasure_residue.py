"""The residue check: did the bytes actually go, and is the finding the kind you think it is?

Built after turning the same instrument on a competitor and on ourselves. mem0 2.0.11 with a local qdrant
came out CLEAN in the sense that matters -- after delete() and reset() no live row anywhere held the
value; it survived only as unreclaimed bytes in the vector store's sqlite. Reporting that as retention
would have been a false accusation, so the three outcomes are kept apart by construction:

  LIVE         a table still holds it -> the system retained it
  UNRECLAIMED  in the bytes, in no row -> the storage engine has not reclaimed the page (VACUUM/compact)
  PLAIN        a JSON/JSONL/log/backup still contains it -> nothing reclaims this on its own

Two properties matter as much as the detection and are tested as such: the report never echoes the value
it was given (it is a secret by construction), and a file that could not be read is REPORTED rather than
skipped, because "clean" must not mean "we did not look".
"""
import json
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus.erasure_residue import scan_residue

SECRET = "alice-probe-9f3c@example.com"


def _sqlite_with(path, value, then_delete=False, vacuum=False):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE notes(txt TEXT)")
    con.execute("INSERT INTO notes VALUES(?)", (f"prefix {value} suffix",))
    con.commit()
    if then_delete:
        con.execute("DELETE FROM notes")
        con.commit()
        if vacuum:
            con.execute("VACUUM")
    con.close()


def _dir():
    return tempfile.mkdtemp()


def test_a_live_row_is_reported_as_retention():
    d = _dir()
    _sqlite_with(os.path.join(d, "live.sqlite"), SECRET)
    rep = scan_residue(d, [SECRET])
    assert rep["ok"] is False
    live = [f for f in rep["findings"] if f["kind"] == "LIVE"]
    assert live and live[0]["table"] == "notes" and live[0]["rows"] == 1, rep["findings"]
    assert any("LIVE row" in p for p in rep["problems"])


def test_a_deleted_row_is_reported_as_UNRECLAIMED_not_as_retention():
    """The distinction the whole file exists for. Calling this retention is a false accusation."""
    d = _dir()
    _sqlite_with(os.path.join(d, "gone.sqlite"), SECRET, then_delete=True)
    rep = scan_residue(d, [SECRET])
    kinds = {f["kind"] for f in rep["findings"]}
    assert kinds == {"UNRECLAIMED"}, rep["findings"]
    assert any("NOT a vendor defect" in p for p in rep["problems"])


def test_a_vacuumed_store_is_clean():
    """And the remedy the report names must actually work, or the advice is noise."""
    d = _dir()
    _sqlite_with(os.path.join(d, "vacuumed.sqlite"), SECRET, then_delete=True, vacuum=True)
    rep = scan_residue(d, [SECRET])
    assert rep["ok"] is True, rep["findings"]


def test_a_plain_file_is_its_own_kind():
    d = _dir()
    with open(os.path.join(d, "trace.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"text": SECRET}))
    rep = scan_residue(d, [SECRET])
    assert [f["kind"] for f in rep["findings"]] == ["PLAIN"], rep["findings"]


def test_a_clean_directory_is_clean():
    assert scan_residue(_dir(), [SECRET])["ok"] is True


def test_the_report_never_echoes_the_value():
    """It is a secret by construction. A tool that hunts for one and then prints it into a log or a
    ticket is itself the leak."""
    d = _dir()
    _sqlite_with(os.path.join(d, "live.sqlite"), SECRET)
    with open(os.path.join(d, "trace.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(SECRET)
    rep = scan_residue(d, [SECRET])
    assert SECRET not in json.dumps(rep), "the value must never appear in the report"
    assert all(len(f["fingerprint"]) == 12 for f in rep["findings"])


def test_an_empty_search_is_not_a_clean_result():
    """A check that passes when asked nothing is a check that always passes."""
    rep = scan_residue(_dir(), [])
    assert rep["ok"] is False
    assert any("empty search" in p for p in rep["problems"])


def test_an_unreadable_or_oversized_file_is_reported_not_skipped():
    """"Clean" must never mean "we did not look at that part."""
    d = _dir()
    big = os.path.join(d, "huge.bin")
    with open(big, "wb") as fh:
        fh.write(b"\0" * (2 * 1024 * 1024))
    rep = scan_residue(d, [SECRET], max_file_mb=0.5)
    assert rep["ok"] is False, "an unexamined file must not read as clean"
    assert rep["skipped"] and "larger than" in rep["skipped"][0]["why"]
    assert any("not looked at" in p for p in rep["problems"])


def test_our_own_store_leaves_no_residue_after_forget():
    """The instrument turned on ourselves, which is the only version of this claim worth making."""
    from inspeximus import Inspeximus

    d = _dir()
    p = os.path.join(d, "m.json")
    m = Inspeximus(path=p, receipts=True)
    rid = m.remember(f"Alice's contact address is {SECRET}", source={"doc": "user-42"})
    m.remember("an unrelated record that must survive")
    m.flush()
    assert scan_residue(d, [SECRET])["ok"] is False, "precondition: it must be there before we erase it"

    m.forget(ids=[rid])
    m.flush()
    rep = scan_residue(d, [SECRET])
    assert rep["ok"] is True, rep["findings"]


def test_it_finds_residue_in_nested_directories():
    d = _dir()
    nested = os.path.join(d, "vector_store", "collection", "default")
    os.makedirs(nested)
    with open(os.path.join(nested, "payload.json"), "w", encoding="utf-8") as fh:
        fh.write(SECRET)
    rep = scan_residue(d, [SECRET])
    assert rep["findings"], "a store keeps its data in subdirectories; a shallow scan proves nothing"


@pytest.mark.parametrize("skip", [".git", "__pycache__", "node_modules"])
def test_noise_directories_are_skipped(skip):
    d = _dir()
    junk = os.path.join(d, skip)
    os.makedirs(junk)
    with open(os.path.join(junk, "x.txt"), "w", encoding="utf-8") as fh:
        fh.write(SECRET)
    assert scan_residue(d, [SECRET])["ok"] is True
