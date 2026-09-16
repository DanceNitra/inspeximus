"""RFC 3161 timestamps in the action ledger. Controls: the entry stamps its own prev; a token moved to
another position fails; a rejected PKIStatus is refused, never stored; a token whose bytes changed
fails; the rotation keeps the entry inside the archive it dates. The TSA is a fake here (a DER
TimeStampResp with status granted); the real one is `openssl ts -verify`."""
import base64
import json

import pytest

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger, verify_file, _entry_hash, _sign
from inspeximus.timestamp import TimestampError, read_status

pytest.importorskip("cryptography")


def _resp(status: int) -> bytes:
    """A minimal DER TimeStampResp: SEQUENCE { SEQUENCE { INTEGER status } }, enough for read_status."""
    inner = bytes([0x30, 0x03, 0x02, 0x01, status])
    return bytes([0x30, len(inner)]) + inner


def _granted(url, digest):
    return {"token": _resp(0) + digest, "status": read_status(_resp(0))}


def _rejected(url, digest):
    return {"token": _resp(2), "status": read_status(_resp(2))}


def test_the_entry_stamps_the_previous_hash_and_a_rejection_is_refused(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    led = ActionLedger(m, actor="agent")
    led.record("tool:a")
    prev = led.entries()[-1]["hash"]
    e = led.timestamp_tail("https://tsa.example/tsr", stamp_fn=_granted)
    assert e["kind"] == "timestamp" and e["stamped_hash"] == prev and e["prev"] == prev
    assert e["pki_status"] == "granted" and base64.b64decode(e["token_b64"]).endswith(bytes.fromhex(prev))
    assert led.verify(expected_pubkey=pk) == (True, [])
    assert led.timestamps()[0]["binds_previous_entry"] is True
    with pytest.raises(TimestampError):
        led.timestamp_tail("https://tsa.example/tsr", stamp_fn=_rejected)
    assert len(led) == 2                                              # nothing stored for the rejection
    assert led.oversight_report()["actions"] == 1                     # a timestamp is not an action


def test_a_moved_or_altered_token_fails(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    led = ActionLedger(m, actor="agent")
    led.record("tool:a")
    led.timestamp_tail("https://tsa.example/tsr", stamp_fn=_granted)
    led.record("tool:b")
    data = json.loads(led.path.read_text(encoding="utf-8"))
    # move the timestamp after tool:b, re-linking and re-signing everything with the key
    data[1], data[2] = data[2], data[1]
    prev = "0" * 64
    for i, x in enumerate(data):
        x["seq"], x["prev"] = i, prev
        for k in ("hash", "sig", "pubkey"):
            x.pop(k, None)
        x["hash"] = _entry_hash(x)
        x["sig"], x["pubkey"] = _sign(sk, x["hash"])
        prev = x["hash"]
    led.path.write_text(json.dumps(data), encoding="utf-8")
    ok, problems = verify_file(led.path, expected_pubkey=pk)
    assert not ok and any("not the previous entry" in p for p in problems)
    # a token whose bytes changed (re-signed by the key holder) fails on its own digest
    led.reload()
    data = json.loads(led.path.read_text(encoding="utf-8"))
    data[1], data[2] = data[2], data[1]                               # back in order
    data[1]["token_b64"] = base64.b64encode(_resp(0) + b"\x00" * 32).decode()
    prev = "0" * 64
    for i, x in enumerate(data):
        x["seq"], x["prev"] = i, prev
        for k in ("hash", "sig", "pubkey"):
            x.pop(k, None)
        x["hash"] = _entry_hash(x)
        x["sig"], x["pubkey"] = _sign(sk, x["hash"])
        prev = x["hash"]
    led.path.write_text(json.dumps(data), encoding="utf-8")
    ok, problems = verify_file(led.path, expected_pubkey=pk)
    assert not ok and any("token_sha256" in p for p in problems)


def test_a_timestamp_on_an_empty_live_file_stamps_the_archived_tail(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    led = ActionLedger(m, actor="agent")
    led.record("tool:a")
    led.record("tool:b")
    import time
    led.archive(before_ts=time.time() + 1, actor="ops")
    assert len(led) == 0 and led.archived
    e = led.timestamp_tail("https://tsa.example/tsr", stamp_fn=_granted)
    assert e["stamped_hash"] == led.archived["archived_tail_hash"] and e["seq"] == 2
    assert led.verify(expected_pubkey=pk) == (True, [])
