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
    path = os.path.join(d, "gone.sqlite")
    _sqlite_with(path, SECRET, then_delete=True)

    # Whether a deleted row's bytes linger is the STORAGE ENGINE's business: a build with secure_delete
    # on, or one that happens to reuse the page, reclaims them immediately. CI proved that -- this test
    # passed locally and found nothing on the runner. What we can assert is the CLASSIFICATION when
    # residue exists; manufacturing the residue would be testing sqlite, not us.
    with open(path, "rb") as fh:
        if SECRET.encode() not in fh.read():
            pytest.skip("this sqlite build reclaimed the page on delete; there is no residue to classify")

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
def test_a_skipped_directory_is_reported_and_is_not_a_clean_verdict(skip):
    """This test used to assert `ok is True` -- it PINNED the defect. A value sitting in `.git` produced
    "RESULT: clean" and exit 0, byte-identical to a genuinely clean scan, and `.git` is where a deleted
    store survives longest. The module already applies the right rule to a file that was too large to read
    ("a store is not clean because part of it was not looked at"); a pruned directory is the same claim at
    larger scale."""
    d = _dir()
    junk = os.path.join(d, skip)
    os.makedirs(junk)
    with open(os.path.join(junk, "x.txt"), "w", encoding="utf-8") as fh:
        fh.write(SECRET)

    rep = scan_residue(d, [SECRET])
    assert rep["ok"] is False, "not looked at is not clean"
    assert any(s["path"].endswith(skip) for s in rep["skipped"]), rep["skipped"]
    assert SECRET not in json.dumps(rep), "and it still must not echo the value"


@pytest.mark.parametrize("skip", [".git", "__pycache__", "node_modules"])
def test_the_caller_can_ask_for_those_directories_to_be_searched(skip):
    """`skip_dirs` was UNIONED with the default, so no caller could opt out -- there was no way to scan the
    one directory most likely to hold the residue. It now replaces."""
    d = _dir()
    junk = os.path.join(d, skip)
    os.makedirs(junk)
    with open(os.path.join(junk, "x.txt"), "w", encoding="utf-8") as fh:
        fh.write(SECRET)

    rep = scan_residue(d, [SECRET], skip_dirs=set())
    assert rep["ok"] is False
    assert any(f["kind"] == "PLAIN" for f in rep["findings"]), rep


def test_a_root_that_does_not_exist_is_not_clean():
    """A typo in a DSAR runbook used to return ok=True with zero files and no problems -- indistinguishable
    from an all-clear. Exactly the erasure-certificate defect (valid:True while the absence proof pointed at
    a path that was not there) in a second place."""
    rep = scan_residue(os.path.join(_dir(), "no-such-subdir"), [SECRET])
    assert rep["ok"] is False
    assert rep["problems"] and "not a directory" in rep["problems"][0]


def test_an_empty_but_real_directory_is_still_clean_with_a_caveat():
    """The other direction, deliberately. Failing an existing-but-empty root would cry wolf on the ordinary
    case, and a check that cries wolf gets switched off -- so it stays clean and says what it means."""
    rep = scan_residue(_dir(), [SECRET])
    assert rep["ok"] is True
    assert any("empty" in p for p in rep["problems"]), rep["problems"]


# ── the check wired into erasure itself, which is the only moment it can run ────────────────────────
def _planted(fragment_only: bool):
    """A store plus a stray file elsewhere in the deployment that still holds the value."""
    from inspeximus import Inspeximus

    d = _dir()
    m = Inspeximus(path=os.path.join(d, "m.json"), receipts=True)
    rid = m.remember(f"Alice contact is {SECRET}", source={"doc": "user-42"})
    m.remember("an unrelated record")
    m.flush()
    with open(os.path.join(d, "stray_backup.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"pii": SECRET}) if fragment_only else f"Alice contact is {SECRET}")
    return m, rid, d


def test_forget_can_prove_the_bytes_went_from_the_whole_deployment():
    """After forget() the value is gone with the row, so it can never be searched for afterwards. Running
    the check DURING erasure is the only moment it is possible at all."""
    m, rid, d = _planted(fragment_only=False)
    res = m.forget(ids=[rid], request_id="DSAR-1", verify_residue_in=d)

    assert res["forgotten"] == 1
    assert res["residue"]["ok"] is False, "a verbatim copy elsewhere must be found"
    assert [f["kind"] for f in res["residue"]["findings"]] == ["PLAIN"], res["residue"]["findings"]


def test_a_clean_deployment_reports_clean():
    from inspeximus import Inspeximus

    d = _dir()
    m = Inspeximus(path=os.path.join(d, "m.json"), receipts=True)
    rid = m.remember(f"Alice contact is {SECRET}")
    m.flush()
    res = m.forget(ids=[rid], verify_residue_in=d)
    assert res["residue"]["ok"] is True, res["residue"]["findings"]


def test_a_FRAGMENT_needs_naming_and_the_limit_is_pinned_here():
    """The honest limit. By default the search uses the record's own text, which catches verbatim copies
    -- backups, WAL files, logs that logged the whole row. A fragment (the email inside a sentence) is not
    matched by the full text, and only the caller knows which part was the sensitive one."""
    m, rid, d = _planted(fragment_only=True)

    default = m.forget(ids=[rid], verify_residue_in=d)["residue"]
    assert default["ok"] is True, "documented: the full text does not match a fragment"

    m2, rid2, d2 = _planted(fragment_only=True)
    named = m2.forget(ids=[rid2], verify_residue_in=d2, residue_values=[SECRET])["residue"]
    assert named["ok"] is False, "naming the fragment finds it"
    assert named["findings"][0]["kind"] == "PLAIN"


def test_the_erasure_result_never_carries_the_value():
    m, rid, d = _planted(fragment_only=True)
    res = m.forget(ids=[rid], verify_residue_in=d, residue_values=[SECRET])
    assert SECRET not in json.dumps(res), "the erasure result must not reintroduce what it erased"


def test_no_residue_check_runs_unless_asked():
    """It walks the filesystem, so it must stay opt-in: an erasure that silently scanned a directory
    would be a surprise, and on a large deployment an expensive one."""
    m, rid, _d = _planted(fragment_only=False)
    assert "residue" not in m.forget(ids=[rid])
