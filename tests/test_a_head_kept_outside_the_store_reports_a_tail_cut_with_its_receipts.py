"""A receipt chain cut at the tail, receipts included, is consistent to every check that reads the
store's directory. After each receipt the store writes {genesis, n_writes, writes_tip} to the config
home; verify_writes() compares the chain on disk against it. Controls: with INSPEXIMUS_HEADS=0 the same
cut is accepted (the head is what closes it); a fresh store at a reused path is not reported as a
rollback of the store that used to be there; a handle that has not seen a peer's write is not a
rollback either; a deliberate restore is accepted with reanchor_head(); a head that cannot be written
never fails a write; a chain rewritten past the head's tip is named."""
import json
import os
import sqlite3

import pytest

from inspeximus import Inspeximus, new_receipt_keypair


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "confighome"
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(h))
    monkeypatch.delenv("INSPEXIMUS_HEADS", raising=False)
    return h


def _store(tmp_path, sk, name="m.json"):
    return Inspeximus(str(tmp_path / name), receipts=True, receipt_key=sk)


def _cut_tail_with_receipts(path: str, n: int) -> None:
    """What an attacker with write access to the store's DIRECTORY does: delete the newest n records
    and their receipts, leaving a shorter chain that is internally consistent."""
    rp = path + ".receipts.json"
    rec = json.loads(open(rp, encoding="utf-8").read())
    victims = [r["memory_id"] for r in rec[-n:]]
    c = sqlite3.connect(path)
    for v in victims:
        c.execute("delete from records where id=?", (v,))
    c.commit()
    c.close()
    open(rp, "w", encoding="utf-8").write(json.dumps(rec[:-n]))


def test_a_tail_cut_with_its_receipts_is_reported_and_the_head_is_what_reports_it(tmp_path, home, monkeypatch):
    sk, pk = new_receipt_keypair()
    m = _store(tmp_path, sk)
    for i in range(5):
        m.remember(f"fact {i}: the limit is {50 + i}", key=f"fact::{i}")
    hp = m.head_path()
    assert hp and hp.startswith(str(home)) and not hp.startswith(str(tmp_path / "m.json"))
    assert json.loads(open(hp, encoding="utf-8").read())["n_writes"] == 5
    assert Inspeximus(m.path, receipts=True, receipt_key=sk).verify_writes(expected_pubkey=pk) == (True, [])
    _cut_tail_with_receipts(str(m.path), 2)
    ok, problems = Inspeximus(m.path, receipts=True, receipt_key=sk).verify_writes(expected_pubkey=pk)
    assert ok is False and any("shrank below the head kept outside the store: 3 < 5" in p for p in problems), problems
    # CONTROL: the head is the only thing that sees it
    monkeypatch.setenv("INSPEXIMUS_HEADS", "0")
    assert Inspeximus(m.path, receipts=True, receipt_key=sk).verify_writes(expected_pubkey=pk)[0] is True


def test_a_deliberate_restore_is_accepted_with_reanchor_head(tmp_path, home):
    sk, pk = new_receipt_keypair()
    m = _store(tmp_path, sk)
    for i in range(4):
        m.remember(f"fact {i}", key=f"fact::{i}")
    _cut_tail_with_receipts(str(m.path), 1)
    m2 = Inspeximus(m.path, receipts=True, receipt_key=sk)
    assert m2.verify_writes(expected_pubkey=pk)[0] is False
    res = m2.reanchor_head()
    assert res["before"]["n_writes"] == 4 and res["after"]["n_writes"] == 3
    assert m2.verify_writes(expected_pubkey=pk) == (True, [])


def test_a_fresh_store_at_a_reused_path_is_not_a_rollback(tmp_path, home):
    sk, pk = new_receipt_keypair()
    m = _store(tmp_path, sk)
    for i in range(6):
        m.remember(f"fact {i}", key=f"fact::{i}")
    path = str(m.path)
    del m
    os.remove(path)
    os.remove(path + ".receipts.json")
    sk2, pk2 = new_receipt_keypair()
    fresh = Inspeximus(path, receipts=True, receipt_key=sk2)
    fresh.remember("a new store, one record", key="k")
    assert fresh.verify_writes(expected_pubkey=pk2) == (True, []), "a different genesis: the old head does not apply"
    assert fresh.read_head()["n_writes"] == 1, "and the new store owns the head from its first write"


def test_a_handle_that_has_not_seen_a_peers_write_is_not_a_rollback(tmp_path, home):
    sk, pk = new_receipt_keypair()
    a = _store(tmp_path, sk)
    for i in range(3):
        a.remember(f"fact {i}", key=f"fact::{i}")
    b = Inspeximus(a.path, receipts=True, receipt_key=sk)
    b.remember("peer write", key="peer")
    assert b.read_head()["n_writes"] == 4
    assert a.verify_writes(expected_pubkey=pk) == (True, []), "compared against the receipts on disk, not this handle's copy"


def test_a_chain_rewritten_past_the_heads_tip_is_named(tmp_path, home):
    sk, pk = new_receipt_keypair()
    m = _store(tmp_path, sk)
    for i in range(3):
        m.remember(f"fact {i}", key=f"fact::{i}")
    # the attacker who holds the sidecar and (somehow) a key of their own: cut the tail and grow a
    # different chain to the same length, so the count matches and only the tip differs
    _cut_tail_with_receipts(str(m.path), 1)
    sk2, _ = new_receipt_keypair()
    hp = m.head_path()
    head = json.loads(open(hp, encoding="utf-8").read())
    m2 = Inspeximus(m.path, receipts=True, receipt_key=sk2)
    m2._receipts_path.write_text(open(m2._receipts_path, encoding="utf-8").read(), encoding="utf-8")
    m2.remember("a different third record", key="fact::2")
    open(hp, "w", encoding="utf-8").write(json.dumps(head))          # the attacker cannot reach the head
    ok, problems = Inspeximus(m.path, receipts=True, receipt_key=sk).verify_writes()
    assert ok is False and any("diverges from the head kept outside the store at receipt 3" in p for p in problems), problems


def test_a_head_that_cannot_be_written_never_fails_a_write(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where a directory is needed", encoding="utf-8")
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(blocked))
    monkeypatch.delenv("INSPEXIMUS_HEADS", raising=False)
    sk, pk = new_receipt_keypair()
    m = _store(tmp_path, sk)
    m.remember("still written", key="k")
    assert m.read_head() is None
    assert m.head_error and "blocked" in m.head_error
    assert m.verify_writes(expected_pubkey=pk) == (True, []), "no head, no head check; everything else as before"


def test_heads_off_writes_nothing(tmp_path, home, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_HEADS", "0")
    sk, pk = new_receipt_keypair()
    m = _store(tmp_path, sk)
    m.remember("x", key="k")
    assert m.head_path() is None and not (home / "inspeximus" / "heads").exists()


def test_the_agents_next_write_does_not_lower_the_head_after_a_cut(tmp_path, home):
    """The red team's bypass on the first version: cut the tail, then wait for the agent to write
    once; the head was rewritten with the shorter chain and the cut vanished. Now the head keeps
    the larger count, the write goes ahead, and the regrown chain of equal length is named at the
    receipt where it diverges."""
    sk, pk = new_receipt_keypair()
    m = _store(tmp_path, sk)
    for i in range(5):
        m.remember(f"fact {i}", key=f"fact::{i}")
    _cut_tail_with_receipts(str(m.path), 2)
    agent = Inspeximus(m.path, receipts=True, receipt_key=sk)
    agent.remember("the agent carries on", key="fact::3")
    assert agent.read_head()["n_writes"] == 5, "not lowered"
    assert agent.head_error and "head not advanced" in agent.head_error
    ok, problems = Inspeximus(m.path, receipts=True, receipt_key=sk).verify_writes(expected_pubkey=pk)
    assert ok is False and any("shrank below the head kept outside the store: 4 < 5" in p for p in problems), problems
    agent.remember("and again", key="fact::4")
    ok, problems = Inspeximus(m.path, receipts=True, receipt_key=sk).verify_writes(expected_pubkey=pk)
    assert ok is False and any("diverges from the head kept outside the store at receipt 5" in p for p in problems), problems
    agent.remember("a sixth", key="fact::5")
    ok, problems = Inspeximus(m.path, receipts=True, receipt_key=sk).verify_writes(expected_pubkey=pk)
    assert ok is False and any("diverges" in p for p in problems), "still named once the chain is longer than the head"
    # CONTROL: an honest chain that simply grows past the head advances it
    n = Inspeximus(str(tmp_path / "honest.json"), receipts=True, receipt_key=sk)
    for i in range(3):
        n.remember(f"h {i}", key=f"h::{i}")
    assert n.read_head()["n_writes"] == 3 and n.head_error is None
