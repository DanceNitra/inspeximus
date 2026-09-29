"""A record KEY that names a person must not outlive that person's erasure in `memory_events`.

Measured on 3.15.4 (2026-09-29): two records under the key "jane-invoice-email", written with
source={"doc": "jane.example"}, then `forget_subject("jane.example", request_id="r1")`. Reading the store
file as bytes found the address and the domain 0 times and the key 5 times, all in `memory_events`: the
journal carries id, key, status and mtype for every `record.added`, `record.changed` and
`record.removed`, and nothing removed those rows or the key in them when the record went. A key is a
label the caller chose, and a caller can put a person in it.

The fix is in the row writer, so every removal path gets it, not only `forget_subject`: the journal rows
of a removed record lose `key` in the same transaction as the DELETE, keep id, type, status and mtype
(a reader tailing by seq sees no gap), and say `key_redacted`.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from inspeximus import Inspeximus
from inspeximus import sqlite_store

KEY = "jane-invoice-email"


def _mk(**kw):
    d = tempfile.mkdtemp(prefix="keyjournal-")
    p = os.path.join(d, "s.json")
    return d, p, Inspeximus(path=p, **kw)


def _bytes_of_dir(d) -> bytes:
    out = b""
    for name in sorted(os.listdir(d)):
        fp = os.path.join(d, name)
        if os.path.isfile(fp):
            with open(fp, "rb") as fh:
                out += fh.read()
    return out


def _journal(p):
    con = sqlite3.connect(p)
    try:
        return con.execute("SELECT seq, type, memory_id, payload FROM memory_events ORDER BY seq").fetchall()
    finally:
        con.close()


def _two_records(ix):
    ix.remember("Jane wants invoices at jane@jane.example", key=KEY, source={"doc": "jane.example"})
    ix.remember("Jane now wants invoices at billing@jane.example", key=KEY, source={"doc": "jane.example"})


def test_forget_subject_leaves_no_key_in_any_file_beside_the_store():
    d, p, ix = _mk()
    _two_records(ix)
    ix.flush()
    assert _bytes_of_dir(d).count(KEY.encode()) > 0, "the control: the key is on disk before the erasure"
    ix.forget_subject("jane.example", request_id="r1")
    ix.flush()
    blob = _bytes_of_dir(d)
    assert blob.count(KEY.encode()) == 0
    assert blob.count(b"jane.example") == 0


def test_a_key_that_is_not_personal_is_gone_from_the_journal_the_same_way():
    # The pre-fix count of "jane" was 0 for the key "invoice-email"; the label went nowhere either.
    d, p, ix = _mk()
    ix.remember("Jane wants invoices at jane@jane.example", key="invoice-email", source={"doc": "jane.example"})
    ix.flush()
    ix.forget_subject("jane.example", request_id="r1")
    ix.flush()
    assert _bytes_of_dir(d).count(b"invoice-email") == 0


def test_the_journal_rows_stay_with_id_type_status_and_a_redaction_mark():
    d, p, ix = _mk()
    _two_records(ix)
    ix.flush()
    before = [(s, t, m) for s, t, m, _ in _journal(p)]
    ids = ix.forget_subject("jane.example", request_id="r1")["ids"]
    ix.flush()
    rows = _journal(p)
    assert [(s, t, m) for s, t, m, _ in rows[:len(before)]] == before, "no earlier row moved or vanished"
    removed = [r for r in rows if r[1] == "record.removed"]
    assert sorted(r[2] for r in removed) == sorted(ids), "each removal is still an event"
    for _, _, mid, payload in rows:
        assert KEY not in payload
        assert '"key_redacted": true' in payload and '"status"' in payload


def test_forget_by_id_takes_the_key_out_too():
    d, p, ix = _mk()
    a = ix.remember("Jane wants invoices at jane@jane.example", key=KEY, source={"doc": "jane.example"})
    ix.flush()
    ix.forget(ids=[a])
    ix.flush()
    assert _bytes_of_dir(d).count(KEY.encode()) == 0


def test_a_handle_with_events_off_still_redacts_a_journal_another_handle_wrote():
    d, p, ix = _mk()
    _two_records(ix)
    ix.flush()
    quiet = Inspeximus(path=p, events=False)
    quiet.forget_subject("jane.example", request_id="r1")
    quiet.flush()
    assert _bytes_of_dir(d).count(KEY.encode()) == 0


def test_a_surviving_record_keeps_its_key_in_the_journal():
    d, p, ix = _mk()
    ix.remember("Jane wants invoices at jane@jane.example", key=KEY, source={"doc": "jane.example"})
    ix.remember("Bob wants invoices at bob@bob.example", key="bob-invoice-email", source={"doc": "bob.example"})
    ix.flush()
    ix.forget_subject("jane.example", request_id="r1")
    ix.flush()
    blob = _bytes_of_dir(d)
    assert blob.count(KEY.encode()) == 0
    assert blob.count(b"bob-invoice-email") >= 2, "the record and its journal row are untouched"
    payloads = [pl for _, t, _, pl in _journal(p) if t == "record.added" and "bob-invoice-email" in pl]
    assert payloads and "key_redacted" not in payloads[0]


def test_a_large_erasure_redacts_every_row_across_the_chunk_boundary():
    d, p, ix = _mk()
    for i in range(620):
        ix.remember("fact %d about jane" % i, key="jane-key-%d" % i, source={"doc": "jane.example"})
    ix.flush()
    assert ix.forget_subject("jane.example", request_id="r1")["erased"] == 620
    ix.flush()
    assert _bytes_of_dir(d).count(b"jane-key-") == 0


def test_the_full_diff_writer_redacts_a_removal_it_finds_by_comparison():
    # `forget()` names the ids it removed, so it takes the declared-ids writer. The other writer decides
    # what went by comparing the rows with its baseline, which is what a handle that did not make the
    # deletion itself does. Both must redact.
    d, p, ix = _mk()
    _two_records(ix)
    ix.flush()
    items = sqlite_store.load(p)
    before = sqlite_store.snapshot(items)
    res = sqlite_store.save(p, [], before, dirty=None, auto_events=True)
    assert res["removed"] == 2
    assert _bytes_of_dir(d).count(KEY.encode()) == 0
    assert all("key_redacted" in pl for _, _, _, pl in _journal(p))
