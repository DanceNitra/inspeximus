"""An erasure made before 3.15.2 can be brought to what 3.15.2 guarantees, in one command.

Before 3.15.2 an erasure removed the record, but its text could stay in a session digest written
before 3.15.2 (its entries carried the text), in a crashed save's temp file, and in a merge or
conversion backup beside the store. 3.15.2 reaches all three on every new erasure. For the erasures
already made, `erase_past_copies()` (CLI: `inspeximus erase-past-copies [--apply]`) finds them from the
tombstone chain, and with apply removes them; `erasure_certificate()` then names nothing it cannot vouch
for, apart from files nothing here accounts for, which it lists and does not touch.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus

MARK = "Alice Novak 7Q2-HOLLOWAY-ROAD"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _legacy_digest(m, entries, seq=9):
    """What close_session stored before 3.15.2: rendered text plus the entries' own text."""
    text = "SESSION DIGEST %d -- what changed in the last session (deterministic ledger diff, no LLM):\n" % seq
    text += "\n".join("  * " + e["text"] for e in entries)
    return m._stamp(text, key=Inspeximus.SESSION_DIGEST_KEY,
                    object=hashlib.sha256(text.encode()).hexdigest()[:16],
                    tags=["session-digest"], mtype="semantic", value=3.0,
                    meta={"kind": "session_digest", "session_seq": seq, "sid": "old",
                          "entries": entries, "considered": len(entries)})


def _holding(d):
    """Files in the store's directory whose bytes still hold the erased text."""
    return sorted(f for f in os.listdir(d)
                  if os.path.isfile(os.path.join(d, f)) and MARK.encode() in open(os.path.join(d, f), "rb").read())


@pytest.fixture
def erased_before(tmp_path, monkeypatch):
    """A store as an erasure BEFORE 3.15.2 left it: the record is gone and a tombstone says so, but a
    legacy digest and a merge backup still hold its text."""
    d = tmp_path / "store"
    d.mkdir()
    p = str(d / "s.json")
    m = Inspeximus(p, receipts=True)
    m.remember_decision(f"ship to {MARK}", because="customer asked", topic="alice-shipping")
    m.remember_decision("use Postgres for the ledger", because="joins", topic="db")
    alice = next(r for r in m.items if MARK in r["text"])
    _legacy_digest(m, [{"kind": "decision", "id": alice["id"], "text": alice["text"], "salience": 4.4}])
    m.flush()
    shutil.copy2(p, p + ".bak-merge-20260901-120000")          # what merge_store made before a merge
    shutil.copy2(p, p + ".old-copy")                            # a copy nothing here accounts for
    with monkeypatch.context() as mp:                           # forget as 3.15.1 did it
        mp.setattr(Inspeximus, "_digest_copies_of", lambda self, target: set())
        mp.setattr(Inspeximus, "_drop_merge_backups", lambda self: [])
        m.forget(ids=[alice["id"]], request_id="DSAR-OLD")
    m.flush()
    return p


def test_control_the_fixture_holds_what_an_older_erasure_left(erased_before):
    d = os.path.dirname(erased_before)
    held = _holding(d)
    assert "s.json.bak-merge-20260901-120000" in held and "s.json.old-copy" in held, held
    m = Inspeximus(erased_before, receipts=True)
    assert any(MARK in (r.get("text") or "") for r in m._items), "the legacy digest should still hold it"
    assert not any(r.get("text", "").startswith("ship to") for r in m._items), "the record itself is gone"


def test_a_dry_run_names_every_copy_and_removes_nothing(erased_before):
    d = os.path.dirname(erased_before)
    before = _holding(d)
    m = Inspeximus(erased_before, receipts=True)
    out = m.erase_past_copies()
    assert out["applied"] is False
    assert len(out["digests"]) == 1, out
    assert out["backups"] == ["s.json.bak-merge-20260901-120000"], out
    assert out["siblings_not_reached"] == ["s.json.old-copy"], out
    assert _holding(d) == before, "a dry run changed a file"


def test_apply_removes_every_copy_it_accounts_for_and_the_certificate_agrees(erased_before):
    d = os.path.dirname(erased_before)
    m = Inspeximus(erased_before, receipts=True)
    out = m.erase_past_copies(apply=True, request_id="CLEANUP-1")
    assert out["applied"] is True and out["forgot"] == 1, out
    assert _holding(d) == ["s.json.old-copy"], "only the copy nothing here accounts for may still hold it"
    assert any("Postgres" in (r.get("text") or "") for r in Inspeximus(erased_before, receipts=True).items)
    cert = Inspeximus(erased_before, receipts=True).erasure_certificate()
    assert cert["siblings_not_reached"], "the untouched copy must still be named"
    assert not any("bak-merge" in str(x) or "interrupted" in str(x).lower()
                   for x in cert["self_check"]["problems"]), cert["self_check"]
    ok, problems = Inspeximus(erased_before, receipts=True).verify_writes()
    assert ok, problems


def test_a_second_run_finds_nothing(erased_before):
    Inspeximus(erased_before, receipts=True).erase_past_copies(apply=True)
    out = Inspeximus(erased_before, receipts=True).erase_past_copies()
    assert out["digests"] == [] and out["backups"] == [], out


def test_the_cli_command(erased_before):
    def cli(*args):
        r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", erased_before, "--json",
                            "erase-past-copies", *args], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120,
                           env={**os.environ, "PYTHONPATH": ROOT, "PYTHONIOENCODING": "utf-8"})
        assert r.returncode == 0, (r.returncode, r.stdout[-500:], r.stderr[-800:])
        return json.loads(r.stdout)
    dry = cli()
    assert dry["applied"] is False and len(dry["digests"]) == 1 and dry["backups"], dry
    done = cli("--apply")
    assert done["applied"] is True and done["forgot"] == 1, done
    assert _holding(os.path.dirname(erased_before)) == ["s.json.old-copy"]


def test_apply_removes_a_backup_when_no_old_digest_is_left(tmp_path, monkeypatch):
    """The digest's own erasure also drops the merge backups (A-12), so the backup must go when there
    is no old digest to erase as well."""
    d = tmp_path / "store"
    d.mkdir()
    p = str(d / "s.json")
    m = Inspeximus(p, receipts=True)
    m.remember_decision(f"ship to {MARK}", because="customer asked", topic="alice-shipping")
    m.flush()
    shutil.copy2(p, p + ".bak-merge-20260901-120000")
    alice = next(r for r in m.items if MARK in r["text"])
    with monkeypatch.context() as mp:
        mp.setattr(Inspeximus, "_drop_merge_backups", lambda self: [])
        m.forget(ids=[alice["id"]])
    m.flush()
    if _holding(str(d)) != ["s.json.bak-merge-20260901-120000"]:
        pytest.fail(f"control: only the backup should hold the text, got {_holding(str(d))}")
    out = Inspeximus(p, receipts=True).erase_past_copies(apply=True)
    assert out["digests"] == [] and out["forgot"] == 0, out
    assert _holding(str(d)) == [], out
