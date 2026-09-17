"""A certificate trimmed of a tombstone, a certificate checked against a stranger's store, a ledger
whose records were rewritten under an intact chain, and a transcript check with no salt.

Found by a red team on 2026-09-17, on the run published at erasure.html and audit-trail.html:

1. Drop the LAST tombstone, set `anchor.tombstones_tip` to the new tail, fix `count` and the ids,
   put the record back under its original id: all checks OK, `VERDICT: PASS (1 erasure(s) attested)`,
   the record live in `list`. No key needed. `anchor.n_tombstones` and `tombstones_root` still said
   two and nothing compared them.
2. `--store other.json`, a store that never held the records, passed "absence checked".
3. A record's text rewritten in the store left the receipt chain untouched, so `actions verify`
   printed OK; only `verify_writes()` saw it, and the page attributed the detection to `verify`.
4. `matches()` on a ledger handed over without its salt minted a new salt and answered false for
   every transcript, the true one included.

Each test carries its control: the untrimmed certificate, the right store, the unedited record, the
present salt, all still PASS. Mutation entries in tools/mutations.json remove each new check and
require the test to fail.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest

from inspeximus import Inspeximus
from inspeximus.core import verify_erasure_certificate

cryptography = pytest.importorskip("cryptography")


def _signed_store(path):
    from inspeximus.core import Inspeximus as _I
    key = os.urandom(32).hex()
    m = _I(str(path), receipts=True, receipt_key=key)
    return m, key


def _erased(tmp_path):
    m, key = _signed_store(tmp_path / "mem.json")
    a1 = m.remember("Alice prefers email", source={"doc": "crm/alice"})
    a2 = m.remember("Alice phone is +100", key="alice::phone", source={"doc": "crm/alice"})
    m.remember("Bob phone is +300", key="bob::phone", source={"doc": "crm/bob"})
    m.forget_subject("crm/alice", request_id="DSAR-17")
    cert = m.erasure_certificate(request_id="DSAR-17")
    return m, key, cert, (a1, a2)


def _trim_last(cert):
    """The attack: drop the last tombstone and make the summary and the tip agree with the shorter chain."""
    c = copy.deepcopy(cert)
    dropped = c["tombstones"].pop()
    c["anchor"]["tombstones_tip"] = c["tombstones"][-1]["hash"]
    c["erased_memory_ids"] = [t["memory_id"] for t in c["tombstones"]]
    c["count"] = len(c["erased_memory_ids"])
    return c, dropped


def test_a_trimmed_certificate_fails_on_its_own_anchor(tmp_path):
    m, key, cert, _ = _erased(tmp_path)
    ok = verify_erasure_certificate(cert, store_items=list(m.items))
    assert ok["valid"] and ok["checks"]["anchor_consistent"] is True          # control

    trimmed, dropped = _trim_last(cert)
    res = verify_erasure_certificate(trimmed, store_items=list(m.items))
    assert res["checks"]["anchor_consistent"] is False
    assert res["valid"] is False
    assert any("n_tombstones" in p for p in res["problems"])
    assert any("tombstones_root" in p for p in res["problems"])


def test_a_trimmed_and_reanchored_certificate_fails_against_a_witnessed_anchor(tmp_path):
    """The trimmer who also rewrites the anchor's counts and roots gets past check 6. The anchor a
    witness holds does not move with the certificate."""
    m, key, cert, _ = _erased(tmp_path)
    witnessed = copy.deepcopy(cert["anchor"])           # what a witness saw at issue time

    trimmed, _ = _trim_last(cert)
    # rewrite the anchor wholesale so it is internally consistent with the shorter chain
    m2 = Inspeximus(str(tmp_path / "mem.json"), receipts=True)
    m2._tombstones = list(m2._tombstones)[:-1]
    trimmed["anchor"] = m2.anchor()
    res = verify_erasure_certificate(trimmed)
    assert res["checks"]["anchor_consistent"] is True    # the whole anchor was forged; check 6 passes
    assert res["valid"] is True                          # and without a witness nothing can tell

    res = verify_erasure_certificate(trimmed, expected_anchor=witnessed)
    assert res["checks"]["anchor_witnessed"] is False
    assert res["valid"] is False
    assert any("truncated" in p for p in res["problems"])

    ok = verify_erasure_certificate(cert, expected_anchor=witnessed)            # control
    assert ok["checks"]["anchor_witnessed"] is True and ok["valid"]


def test_absence_is_checked_against_the_store_the_certificate_names(tmp_path):
    m, key, cert, _ = _erased(tmp_path)
    ok = verify_erasure_certificate(cert, store_items=list(m.items), store_receipts=list(m._receipts))
    assert ok["checks"]["store_bound"] is True and ok["valid"]                  # control

    other, _ = _signed_store(tmp_path / "other.json")
    other.remember("unrelated", source={"doc": "x"})
    res = verify_erasure_certificate(cert, store_items=list(other.items), store_receipts=list(other._receipts))
    assert res["checks"]["store_absent"] is True                                # the ids are absent
    assert res["checks"]["store_bound"] is False                                # from the wrong store
    assert res["valid"] is False


# CI's test job runs the suite from the source tree without installing the package, so a child
# started in a temp directory has no `inspeximus` on its path unless the repo root is put there.
# Measured 2026-09-17: two of these tests passed locally (editable install) and failed on all
# three CI runners with `writer-key` exiting 1.
_ENV = {**os.environ, "PYTHONPATH": os.pathsep.join(
    p for p in (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.environ.get("PYTHONPATH", "")) if p)}


def _cli(args, cwd):
    return subprocess.run([sys.executable, "-m", "inspeximus.cli", *args], cwd=cwd, env=_ENV,
                          capture_output=True, text=True, encoding="utf-8")


def _ok(r):
    """A child that failed says why in the assertion, not only in a return code."""
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def test_bound_actions_verify_sees_a_record_rewritten_under_an_intact_chain(tmp_path):
    _ok(_cli(["writer-key", "--new", "--out", "key.txt"], tmp_path))
    store = ["--path", "mem.json", "--receipts", "--receipt-key-file", "key.txt"]
    _ok(_cli(store + ["remember", "Alice phone is +100", "--key", "alice::phone"], tmp_path))
    _ok(_cli(store + ["actions", "record", "tool:sms", "--input", '{"to": "+100"}', "--output",
                      '{"sent": true}', "--actor", "a"], tmp_path))
    before = _ok(_cli(["--path", "mem.json", "actions", "verify"], tmp_path))
    assert "store records against their write receipts" in before.stdout

    # rewrite the record's text on disk without touching the receipt chain
    m = Inspeximus(str(tmp_path / "mem.json"))
    rec = next(r for r in m.items if r.get("key") == "alice::phone")
    rec["text"] = "Alice phone is +200"
    m._save()

    after = _cli(["--path", "mem.json", "actions", "verify"], tmp_path)
    assert after.returncode == 1
    assert "OK action ledger" in after.stdout                # the chain itself is intact
    assert "FAIL store records against their write receipts" in after.stdout


def test_matches_refuses_without_the_salt_instead_of_answering_false(tmp_path):
    from inspeximus.actions import ActionLedger
    m = Inspeximus(str(tmp_path / "mem.json"))
    led = ActionLedger(m)
    led.record("tool:x", inputs={"a": 1}, output={"b": 2}, actor="t")
    assert led.matches(0, inputs={"a": 1}, output={"b": 2}) == {"seq": 0, "action": "tool:x",
                                                                "inputs": True, "output": True}
    led.salt_path.unlink()
    fresh = ActionLedger(Inspeximus(str(tmp_path / "mem.json")))
    with pytest.raises(FileNotFoundError):
        fresh.matches(0, inputs={"a": 1}, output={"b": 2})
    assert not led.salt_path.exists()                       # the read did not mint a new one


def test_a_bom_on_a_transcript_is_not_an_edit(tmp_path):
    _ok(_cli(["writer-key", "--new", "--out", "key.txt"], tmp_path))
    store = ["--path", "mem.json", "--receipts", "--receipt-key-file", "key.txt"]
    _ok(_cli(store + ["actions", "record", "tool:sms", "--input", '{"to": "+100"}', "--output",
                      '{"sent": true}', "--actor", "a"], tmp_path))
    (tmp_path / "in.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"to": "+100"}).encode())
    (tmp_path / "out.json").write_text(json.dumps({"sent": True}), encoding="utf-8")
    r = _cli(["--path", "mem.json", "actions", "matches", "0", "--inputs", "in.json", "--output", "out.json"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["inputs"] is True
