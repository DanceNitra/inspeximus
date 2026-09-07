"""The absence proof must read the store it is handed, whatever format that store is in.

WHY THIS EXISTS. `verify_erasure_certificate` is the strongest thing this library does: it opens the
store file itself and shows a supposedly-erased id is really not in it. Soft-delete systems fail
exactly there. When the row format shipped, that function still read the raw bytes and called
`json.loads` on them, so a migrated store produced `cannot read store` and the proof silently
downgraded to "not performed". The certificate came back without its absence evidence and nothing in
the return said the strongest check had stopped running.

THE CONTROL IS THE POINT. Asserting "no cannot-read-store problem" proves only that the read stopped
throwing. So each arm plants an erased record BACK into the store and requires the verifier to catch
it. If that arm passes on JSON and fails on SQLite, the row store is not really being read; if it
fails on both, the absence proof is not wired to anything at all.
"""
import json
import os
import tempfile

from inspeximus import Inspeximus, sqlite_store as ss
from inspeximus.core import verify_erasure_certificate


def _store_with_an_erasure(as_rows):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "memory.json")
    # A new store is written as rows now, so the JSON arm has to ask for the old format explicitly.
    # That arm is not obsolete: every store written before this version is JSON, and the absence proof
    # has to keep working on both for as long as those files exist.
    prev = os.environ.get("INSPEXIMUS_STORE_FORMAT")
    if as_rows:
        os.environ.pop("INSPEXIMUS_STORE_FORMAT", None)
    else:
        os.environ["INSPEXIMUS_STORE_FORMAT"] = "json"
    try:
        return _build(path, as_rows)
    finally:
        if prev is None:
            os.environ.pop("INSPEXIMUS_STORE_FORMAT", None)
        else:
            os.environ["INSPEXIMUS_STORE_FORMAT"] = prev


def _build(path, as_rows):
    m = Inspeximus(path=path)
    m._save_min_s = 0
    m.remember("Ada Lovelace lives in Turin", key="ada::city", mtype="fact",
               source={"doc": "ada-src"})
    m.remember("a record about someone else", key="other::x", mtype="fact",
               source={"doc": "other-src"})
    m.flush()
    assert ss.looks_like_sqlite(path) is as_rows, "the fixture is not in the format it claims"
    m.forget_subject("ada-src", request_id="DSAR-1")
    m.flush()
    cert = m.erasure_certificate(request_id="DSAR-1")
    return m, path, cert


def _erased_ids(cert):
    ids = cert.get("erased_memory_ids") or cert.get("erased_ids") or []
    return [i for i in ids]


def _put_back(path, rec):
    """Re-insert an erased record, the way a broken soft-delete would leave it."""
    if ss.looks_like_sqlite(path):
        items = ss.load(path)
        items.append(rec)
        ss.save(path, items, ss.snapshot([r for r in items if r is not rec]), dirty=[rec["id"]])
    else:
        with open(path, encoding="utf-8") as fh:
            items = json.load(fh)
        items.append(rec)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(items, fh)


def _run(as_rows):
    m, path, cert = _store_with_an_erasure(as_rows)
    clean = verify_erasure_certificate(cert, store_path=path)
    unreadable = [p for p in (clean.get("problems") or []) if "cannot read store" in str(p)]
    ids = _erased_ids(cert)
    assert ids, "the fixture erased nothing, so both arms below would pass vacuously"

    _put_back(path, {"id": ids[0], "text": "Ada Lovelace lives in Turin", "ts": 1.0,
                     "status": "active"})
    leaked = verify_erasure_certificate(cert, store_path=path)
    return {"unreadable": unreadable,
            "absent_when_clean": clean.get("checks", {}).get("store_absent"),
            "absent_when_planted": leaked.get("checks", {}).get("store_absent")}


def test_a_row_store_is_readable_by_the_absence_proof():
    r = _run(True)
    assert not r["unreadable"], "the absence proof could not read a row store: %s" % r["unreadable"]
    assert r["absent_when_clean"] is True, "a clean row store did not pass the absence proof"


def test_a_planted_record_is_caught_in_a_row_store():
    """CONTROL. Without this, 'no error' is indistinguishable from 'the check never ran'."""
    assert _run(True)["absent_when_planted"] is False, (
        "an erased id put back into a row store was NOT detected, so the absence proof is reading "
        "nothing even though it stopped erroring")


def test_the_json_store_behaves_identically():
    """The other half of the control: the two formats must give the same verdicts."""
    j, s = _run(False), _run(True)
    assert (j["absent_when_clean"], j["absent_when_planted"]) == (True, False)
    assert (j["absent_when_clean"], j["absent_when_planted"]) == \
           (s["absent_when_clean"], s["absent_when_planted"]), \
        "the two store formats disagree about the same erasure: json=%r sqlite=%r" % (j, s)
