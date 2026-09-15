"""The action ledger: what the agent did, bound to what it knew.

Every check here has a control that makes it fail: a rewritten action, a rewritten memory history,
a forged signature. A verifier that cannot fail has measured nothing.
"""
import json

import pytest

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger, verify_entries, verify_file, GENESIS

pytest.importorskip("cryptography")


def _store(tmp_path, signed=True):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk if signed else None)
    return m, sk, pk


def test_two_actions_around_a_correction_carry_two_digests_and_the_ids_recall_returned(tmp_path):
    m, sk, pk = _store(tmp_path)
    m.remember("staging db is db-3", key="staging-db")
    led = ActionLedger(m, actor="deploy")
    m.recall("staging db")
    with led.action("tool:deploy", inputs={"to": "db-3"}) as a:
        a.output("done")
    m.remember("staging db is db-7", key="staging-db")
    m.recall("staging db")
    with led.action("tool:deploy", inputs={"to": "db-7"}) as a:
        a.output("done")
    e = led.entries()
    assert len(e) == 2
    assert e[0]["memory_state"]["digest"] != e[1]["memory_state"]["digest"]
    assert e[0]["memory_state"]["recalled"] and e[1]["memory_state"]["recalled"]
    assert e[0]["memory_state"]["recalled"] != e[1]["memory_state"]["recalled"]
    assert e[0]["prev"] == GENESIS and e[1]["prev"] == e[0]["hash"]
    assert all(x["pubkey"] == pk for x in e)
    assert led.verify(expected_pubkey=pk) == (True, [])
    # content-free by default
    assert "inputs" not in e[0] and e[0]["inputs_sha256"]


def test_a_rewritten_action_fails_offline_verification(tmp_path):
    m, sk, pk = _store(tmp_path)
    led = ActionLedger(m)
    led.record("tool:a", inputs=1)
    led.record("tool:b", inputs=2)
    ok, _ = verify_file(led.path)
    assert ok
    data = json.loads(led.path.read_text(encoding="utf-8"))
    data[0]["action"] = "tool:nothing"
    led.path.write_text(json.dumps(data), encoding="utf-8")
    ok, problems = verify_file(led.path)
    assert not ok and any("seq 0" in p and "hash" in p for p in problems)


def test_a_rewritten_memory_history_is_caught_from_the_action_side(tmp_path):
    m, sk, pk = _store(tmp_path)
    m.remember("fact one", key="k")
    led = ActionLedger(m)
    led.record("tool:a")
    m.remember("fact two", key="k")
    led.record("tool:b")
    assert led.verify() == (True, [])
    # an operator drops the last memory receipt and re-opens the store with the same key
    rp = tmp_path / "mem.json.receipts.json"
    r = json.loads(rp.read_text(encoding="utf-8"))
    rp.write_text(json.dumps(r[:-1]), encoding="utf-8")
    m2 = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    led2 = ActionLedger(m2)
    ok, problems = led2.verify()
    assert not ok and any("last_receipt" in p for p in problems)
    # CONTROL: the offline check alone cannot see it; the binding to the store is what catches it
    assert verify_file(led2.path)[0]


def test_a_forged_signature_and_a_second_key_are_reported(tmp_path):
    m, sk, pk = _store(tmp_path)
    led = ActionLedger(m)
    led.record("tool:a")
    e = led.entries()
    e[0]["sig"] = "00" * 64
    assert any("signature does not verify" in p for p in verify_entries(e))
    # a second signer on the same chain
    sk2, pk2 = new_receipt_keypair()
    led2 = ActionLedger(m, signing_key=sk2)
    led2.record("tool:b")
    probs = verify_entries(led2.entries())
    assert any("different keys" in p for p in probs)
    assert any("unexpected key" in p for p in verify_entries(led2.entries(), expected_pubkey=pk))


def test_an_error_inside_the_block_is_recorded_and_re_raised(tmp_path):
    m, sk, pk = _store(tmp_path)
    led = ActionLedger(m)
    with pytest.raises(ValueError):
        with led.action("tool:boom", inputs={"x": 1}):
            raise ValueError("no")
    e = led.entries()[-1]
    assert e["status"] == "error" and e["error"].startswith("ValueError")
    assert e["output_sha256"] is None


def test_the_decorator_records_arguments_and_the_return_value(tmp_path):
    m, sk, pk = _store(tmp_path)
    led = ActionLedger(m, keep_content=True)

    @led.wrap("tool:add")
    def add(a, b=0):
        return a + b

    assert add(2, b=3) == 5
    e = led.entries()[-1]
    assert e["action"] == "tool:add" and e["inputs"] == {"args": [2], "kwargs": {"b": 3}} and e["output"] == 5


def test_an_unsigned_ledger_verifies_by_hash_and_signatures_are_required_once_one_exists(tmp_path):
    m, sk, pk = _store(tmp_path, signed=False)
    led = ActionLedger(m)
    led.record("tool:a")
    assert "sig" not in led.entries()[0]
    assert led.verify() == (True, [])
    led_signed = ActionLedger(m, signing_key=sk)
    led_signed.record("tool:b")
    ok, problems = led_signed.verify()
    assert not ok and any("seq 0: no signature" in p for p in problems)


def test_what_it_knew_resolves_the_recalled_ids_to_provenance(tmp_path):
    m, sk, pk = _store(tmp_path)
    m.remember("the limit is 50", key="limit")
    led = ActionLedger(m)
    m.recall("limit")
    led.record("tool:refund")
    knew = led.what_it_knew(0)
    assert knew["memory_state"]["recalled"]
    assert knew["recalled_now"] and "error" not in knew["recalled_now"][0]


def test_a_ledger_reloads_what_a_peer_appended(tmp_path):
    m, sk, pk = _store(tmp_path)
    a = ActionLedger(m)
    b = ActionLedger(m)
    a.record("tool:a")
    assert len(b) == 0
    b.reload()
    assert len(b) == 1 and b.verify() == (True, [])
