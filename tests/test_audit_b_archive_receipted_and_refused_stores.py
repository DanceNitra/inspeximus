"""AUDIT-B B-25: a store with write receipts can be archived and still verifies; the stores a plain
row-store segment cannot serve are refused before anything changes.

With receipts, every archived record keeps its evidence. The hot store's `verify_writes` checks an archived
record in its segment, the segment carries a signed manifest and copies of the records' receipts, and the
archive log's entries are signed with the store's receipt key. A segment that is missing or altered is a
named gap: `ok` is not true while it exists.

Encrypted stores and stores pinned to JSON are refused by plan, apply and the CLI alike, before anything
is read or written, with the reason and the way forward (a limit of 3.16.1: a plain segment from an
encrypted store would write its text in the clear).
"""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import inspeximus.core as core  # noqa: E402
from inspeximus import archive  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

DAY = 86400.0
T0 = 1790000000.0

pytest.importorskip("cryptography")


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)


@pytest.fixture
def signed(tmp_path, monkeypatch):
    sk, pk = core.new_receipt_keypair()
    p = str(tmp_path / "coding_memory.json")
    m = Inspeximus(p, receipts=True, receipt_key=sk)
    for i in range(10):
        monkeypatch.setattr(core.time, "time", lambda i=i: T0 - 40 * DAY + i)
        m.remember(f"ran: export number {i}", key=f"cmd:c{i}", tags=["bash"],
                   source={"doc": "hr/alice"} if i % 3 == 0 else None)
    m.flush()
    monkeypatch.setattr(core.time, "time", lambda: T0)
    handle = lambda: Inspeximus(p, receipts=True, receipt_key=sk)  # noqa: E731
    assert handle().verify_writes(expected_pubkey=pk) == (True, [])
    r = archive.apply(handle(), 7, now=T0)
    return p, pk, handle, str(tmp_path / r["written"][0]["file"])


def test_a_receipted_store_verifies_after_the_move_and_after_an_erasure(signed):
    p, pk, handle, seg = signed
    assert handle().verify_writes(expected_pubkey=pk) == (True, []), "archived records are checked in the segment"
    assert Inspeximus(seg).verify_writes(expected_pubkey=pk) == (True, []), "the segment verifies on its own"
    assert archive.verify_log(archive.read_log(p), pk) == (True, [])
    assert all("sig" in e for e in archive.read_log(p)), "every log entry is signed with the receipt key"
    handle().forget_subject("hr/alice")
    assert handle().verify_writes(expected_pubkey=pk)[0]
    assert Inspeximus(seg).verify_writes(expected_pubkey=pk)[0], "the rewritten segment is re-signed"


def test_an_edited_archived_record_fails_in_the_hot_store_and_in_the_segment(signed):
    p, pk, handle, seg = signed
    con = sqlite3.connect(seg)
    rid, doc = con.execute("SELECT id, doc FROM records LIMIT 1").fetchone()
    d = json.loads(doc)
    d["text"] = "rewritten after the move"
    con.execute("UPDATE records SET doc=? WHERE id=?", (json.dumps(d), rid))
    con.commit()
    con.close()
    ok, problems = handle().verify_writes(expected_pubkey=pk)
    assert not ok and any("altered" in x for x in problems)
    ok, problems = Inspeximus(seg).verify_writes(expected_pubkey=pk)
    assert not ok and any(rid in x and "differs from its receipt" in x for x in problems)


def test_a_row_and_its_receipt_copy_removed_together_fail_the_signed_manifest(signed):
    p, pk, handle, seg = signed
    con = sqlite3.connect(seg)
    rid = con.execute("SELECT id FROM records LIMIT 1").fetchone()[0]
    con.execute("DELETE FROM records WHERE id=?", (rid,))
    rc = json.loads(con.execute("SELECT v FROM meta WHERE k='archive_receipts'").fetchone()[0])
    con.execute("UPDATE meta SET v=? WHERE k='archive_receipts'",
                (json.dumps([r for r in rc if r.get("memory_id") != rid]),))
    con.commit()
    con.close()
    ok, problems = Inspeximus(seg).verify_writes(expected_pubkey=pk)
    assert not ok and any("do not match its manifest" in x for x in problems)


def test_a_log_entry_edited_after_signing_fails(signed):
    p, pk, handle, seg = signed
    entries = archive.read_log(p)
    entries[0]["count"] += 1
    entries[0]["hash"] = archive._entry_hash(entries[0])          # a consistent hash, but not the key's
    assert not archive.verify_log(entries, pk)[0]


def test_a_missing_segment_is_a_named_gap_not_a_pass(signed):
    p, pk, handle, seg = signed
    os.rename(seg, seg + ".moved-away")
    ok, problems = handle().verify_writes(expected_pubkey=pk)
    assert not ok and any("missing: they were not verified" in x for x in problems)


def test_a_segment_without_receipts_says_it_is_accounting_only(tmp_path, monkeypatch):
    p = str(tmp_path / "coding_memory.json")
    m = Inspeximus(p)
    monkeypatch.setattr(core.time, "time", lambda: T0 - 40 * DAY)
    m.remember("ran: ls", key="cmd:a", tags=["bash"])
    m.flush()
    monkeypatch.setattr(core.time, "time", lambda: T0)
    r = archive.apply(Inspeximus(p), 7, now=T0)
    ok, problems = Inspeximus(str(tmp_path / r["written"][0]["file"])).verify_writes()
    assert not ok and any("accounting, not tamper evidence" in x for x in problems)


def _files(d):
    return {n: open(os.path.join(d, n), "rb").read() for n in sorted(os.listdir(d))}


def _old_capture(m, monkeypatch):
    monkeypatch.setattr(core.time, "time", lambda: T0 - 40 * DAY)
    m.remember("ran: ls -la", key="cmd:a", tags=["bash"])
    m.flush()
    monkeypatch.setattr(core.time, "time", lambda: T0)


def test_an_encrypted_store_is_refused_before_anything_changes(tmp_path, monkeypatch):
    enc = tmp_path / "enc"
    enc.mkdir()
    p = str(enc / "coding_memory.json")
    _old_capture(Inspeximus(p, encrypt_passphrase="correct horse"), monkeypatch)
    before = _files(enc)
    for call in (archive.plan, archive.apply):
        with pytest.raises(archive.ArchiveRefused, match="in the clear. An encrypted store stays hot"):
            call(Inspeximus(p, encrypt_passphrase="correct horse"), 7, now=T0)
    assert _files(enc) == before
    plain = tmp_path / "plain"
    plain.mkdir()
    q = str(plain / "coding_memory.json")
    _old_capture(Inspeximus(q), monkeypatch)
    assert archive.apply(Inspeximus(q), 7, now=T0)["applied"], "the control: the same fixture as a row store"


def test_a_json_pinned_store_is_refused_by_plan_apply_and_the_cli(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    st = proj / ".inspeximus"
    st.mkdir(parents=True)
    p = str(st / "coding_memory.json")
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    _old_capture(Inspeximus(p), monkeypatch)
    before = _files(st)
    for call in (archive.plan, archive.apply):
        with pytest.raises(archive.ArchiveRefused, match="Convert it first"):
            call(Inspeximus(p), 7, now=T0)
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_") and k != "PYTHONPATH"}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home), INSPEXIMUS_NO_UPDATE_CHECK="1",
               PYTHONPATH=ROOT, INSPEXIMUS_STORE_FORMAT="json")
    for extra in ([], ["--apply"]):
        run = subprocess.run([sys.executable, "-m", "inspeximus.claude_code", "--archive", "--older-than", "7",
                              *extra], capture_output=True, text=True, encoding="utf-8", cwd=str(proj), env=env,
                             timeout=120)
        assert run.returncode == 2 and "Convert it first" in run.stdout, run.stdout + run.stderr
    assert _files(st) == before, "nothing on disk changed"
    monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT")
    assert archive.apply(Inspeximus(p), 7, now=T0)["applied"], "the control: unpinned, the same store archives"
