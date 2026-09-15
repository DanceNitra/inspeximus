"""The seven mutations a red-team ran against the 2.29.0 action ledger. Four passed verify() then.

Each one is now a failing control. The fixes: an unsigned chain fails whenever the caller names the
expected key; the memory binding checks the receipt's POSITION, not its membership; memory_state is
captured before the block runs; a recall older than the previous entry is not re-attributed; digests
are salted so a low-entropy input cannot be dictionary-attacked from the ledger alone; a store with
receipts off cannot bind and says so.
"""
import json
import hashlib

import pytest

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger, verify_file, verify_entries, _entry_hash, _sign

pytest.importorskip("cryptography")


def _store(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    m.remember("the limit is 50", key="limit")
    led = ActionLedger(m, actor="agent")
    led.record("tool:a", inputs={"phone": "+100"})
    m.remember("the limit is 60", key="limit")
    led.record("tool:b", inputs={"phone": "+100"})
    return m, led, sk, pk


def _resign(entries, sk):
    prev = "0" * 64
    for i, e in enumerate(entries):
        e["seq"] = i
        e["prev"] = prev
        e.pop("hash", None); e.pop("sig", None); e.pop("pubkey", None)
        e["hash"] = _entry_hash(e)
        e["sig"], e["pubkey"] = _sign(sk, e["hash"])
        prev = e["hash"]
    return entries


def test_an_unsigned_chain_fails_when_the_expected_key_is_named(tmp_path):
    m, led, sk, pk = _store(tmp_path)
    data = json.loads(led.path.read_text(encoding="utf-8"))
    for e in data:
        e.pop("sig"); e.pop("pubkey")
        e.pop("hash"); e["hash"] = _entry_hash(e)
    # re-link prev
    prev = "0" * 64
    for e in data:
        e["prev"] = prev; e.pop("hash"); e["hash"] = _entry_hash(e); prev = e["hash"]
    led.path.write_text(json.dumps(data), encoding="utf-8")
    ok, problems = verify_file(led.path, expected_pubkey=pk)
    assert not ok and any("no signature" in p for p in problems)
    # CONTROL: with no key named, an unsigned chain is still a valid hash chain
    assert verify_file(led.path)[0]


def test_a_receipt_from_earlier_in_the_chain_does_not_satisfy_the_binding(tmp_path):
    m, led, sk, pk = _store(tmp_path)
    data = json.loads(led.path.read_text(encoding="utf-8"))
    data[1]["memory_state"]["last_receipt"] = data[0]["memory_state"]["last_receipt"]
    led.path.write_text(json.dumps(_resign(data, sk)), encoding="utf-8")
    led.reload()
    assert verify_file(led.path, expected_pubkey=pk)[0]           # a consistent re-sign passes offline
    ok, problems = led.verify(expected_pubkey=pk)
    assert not ok and any("position" in p for p in problems)


def test_the_binding_may_not_move_backwards(tmp_path):
    m, led, sk, pk = _store(tmp_path)
    data = json.loads(led.path.read_text(encoding="utf-8"))
    data[0]["memory_state"], data[1]["memory_state"] = data[1]["memory_state"], data[0]["memory_state"]
    led.path.write_text(json.dumps(_resign(data, sk)), encoding="utf-8")
    led.reload()
    ok, problems = led.verify(expected_pubkey=pk)
    assert not ok and any("earlier in the receipt chain" in p for p in problems)


def test_memory_state_is_captured_before_the_block_runs(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    m.remember("v1", key="k")
    led = ActionLedger(m)
    before = m.state_digest()
    with led.action("tool:writes") as a:
        m.remember("v2", key="k")             # the action itself changes the store
        a.output("done")
    e = led.entries()[-1]
    assert e["memory_state"]["digest"] == before
    assert e["memory_state"]["digest"] != m.state_digest()
    # CONTROL: a plain record() captures now, which is after
    led.record("tool:plain")
    assert led.entries()[-1]["memory_state"]["digest"] == m.state_digest()


def test_a_recall_older_than_the_previous_entry_is_not_re_attributed(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    m.remember("the limit is 50", key="limit")
    led = ActionLedger(m)
    m.recall("limit")
    led.record("tool:a")
    led.record("tool:b")                       # no recall in between
    a, b = led.entries()
    assert a["memory_state"]["recalled"] and a["memory_state"]["recall_before_previous_entry"] is False
    assert b["memory_state"]["recalled"] == [] and b["memory_state"]["recall_before_previous_entry"] is True
    assert b["memory_state"]["recall_scope"] == "this handle"
    m.recall("limit")
    led.record("tool:c")
    assert led.entries()[-1]["memory_state"]["recalled"]


def test_input_digests_are_salted_and_the_salt_is_not_in_the_ledger(tmp_path):
    m, led, sk, pk = _store(tmp_path)
    e = led.entries()[0]
    plain = hashlib.sha256(json.dumps({"phone": "+100"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert e["inputs_sha256"] != plain                               # a dictionary of plain digests misses
    assert led.salt_path.exists()
    text = led.path.read_text(encoding="utf-8")
    assert led.salt_path.read_text(encoding="utf-8").strip() not in text
    # the same ledger digests the same input the same way, so the operator can still match a known input
    led.record("tool:c", inputs={"phone": "+100"})
    assert led.entries()[-1]["inputs_sha256"] == e["inputs_sha256"]


def test_a_store_with_receipts_off_cannot_bind_and_verify_says_so(tmp_path):
    m = Inspeximus(str(tmp_path / "plain.json"))
    m.remember("x", key="k")
    led = ActionLedger(m)
    led.record("tool:a")
    ok, problems = led.verify()
    assert not ok and any("no memory binding" in p for p in problems)
    # CONTROL: a receipted store with an empty chain at recording time is a state, not a gap
    sk, pk = new_receipt_keypair()
    m2 = Inspeximus(str(tmp_path / "rec.json"), receipts=True, receipt_key=sk)
    led2 = ActionLedger(m2)
    led2.record("tool:a")
    assert led2.verify(expected_pubkey=pk) == (True, [])


def test_a_forged_tail_signed_with_the_store_key_still_passes_and_that_limit_is_stated(tmp_path):
    """The one the ledger cannot catch: an operator who holds the receipt key can rewrite both chains
    consistently. That is the witness's job (anchor + co-signature), not this file's. The test pins the
    limit so a future change to the wording of verify() does not claim more."""
    m, led, sk, pk = _store(tmp_path)
    data = json.loads(led.path.read_text(encoding="utf-8"))
    data.pop()
    led.path.write_text(json.dumps(_resign(data, sk)), encoding="utf-8")
    led.reload()
    assert led.verify(expected_pubkey=pk) == (True, [])
    assert "operator" in ActionLedger.verify.__doc__.lower() or "witness" in ActionLedger.verify.__doc__.lower()
