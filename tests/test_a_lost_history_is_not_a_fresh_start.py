"""A verifier's lost state is not an empty one (session E review, items 7 and 8, 2026-09-24).

7. ActionLedger read a ledger file it could not parse as an empty chain, and the next record() wrote a
   new one-entry chain over it. The history was replaced and verify() read the new file clean. Now the
   handle refuses to write and verify() reports the unreadable file.
8. The checkpoint witness read an unreadable state file as a first run, so every log went back to size
   0 and the witness would cosign a smaller tree that forks the one it had cosigned. Now it refuses.
   And Inspeximus.verify_inclusion() without `expected_root` checked a bundle against the root the
   bundle carries, so a one-leaf "tree" made from any text returned True. Now it returns False.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

import pytest

from inspeximus import Inspeximus
from inspeximus.actions import ActionLedger


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keys"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")


def _ledger(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"), receipts=True)
    m.remember("a", key="a", object="1")
    led = ActionLedger(m, actor="agent")
    for i in range(4):
        led.record("tool:x", inputs={"i": i}, status="ok")
    return m, led


def test_CONTROL_a_readable_ledger_verifies_and_records(tmp_path):
    m, led = _ledger(tmp_path)
    again = ActionLedger(m, actor="agent")
    assert again.verify()[0], again.verify()[1]
    again.record("tool:y", inputs={}, status="ok")
    assert len(json.loads(open(led.path, encoding="utf-8").read())) == 5


@pytest.mark.parametrize("garbage", ["{ not json", '{"entries": []}', "7"])
def test_an_unreadable_ledger_is_reported_and_never_overwritten(tmp_path, garbage):
    m, led = _ledger(tmp_path)
    with open(led.path, "w", encoding="utf-8") as fh:
        fh.write(garbage)
    again = ActionLedger(m, actor="agent")
    ok, problems = again.verify()
    assert not ok and problems[0].startswith("the ledger file could not be read"), problems
    with pytest.raises(RuntimeError, match="refusing to write over it"):
        again.record("tool:y", inputs={}, status="ok")
    assert open(led.path, encoding="utf-8").read() == garbage, "the file must be left as it was"


def test_verify_inclusion_needs_the_root_you_witnessed(tmp_path):
    leaf = "anything I like"
    root = hashlib.sha256(b"\x00" + leaf.encode()).hexdigest()
    forged = {"leaf": leaf, "index": 0, "tree_size": 1, "audit_path": [], "root": root}
    assert Inspeximus.verify_inclusion(forged, root) is True, "CONTROL: self-consistent when asked"
    assert Inspeximus.verify_inclusion(forged) is False
    assert Inspeximus.verify_inclusion(forged, "") is False


def test_verify_inclusion_still_proves_a_real_entry(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"), receipts=True)
    for i in range(5):
        m.remember(f"fact {i}", key=f"k{i}", object=str(i))
    witnessed = m.anchor()["writes_root"]
    b = m.inclusion_proof(2)
    assert Inspeximus.verify_inclusion(b, witnessed) is True
    assert Inspeximus.verify_inclusion(b) is False


def _witness(tmp_path):
    from inspeximus.witness_checkpoint import CheckpointWitness
    return CheckpointWitness(str(tmp_path / "state.json"), {}, "w", "00" * 32, "00" * 32)


def test_CONTROL_a_missing_witness_state_is_a_first_run(tmp_path):
    assert _witness(tmp_path).latest("any") == {"size": 0, "root": ""}


@pytest.mark.parametrize("garbage", ["{ not json", "[]", '{"logs": 5}'])
def test_an_unreadable_witness_state_refuses_instead_of_resetting(tmp_path, garbage):
    from inspeximus.witness_checkpoint import Refused
    w = _witness(tmp_path)
    good = {"kind": "inspeximus.witness-checkpoint/1",
            "logs": {"example.com/log": {"size": 7, "root": base64.b64encode(b"\x01" * 32).decode()}}}
    with open(w.state_path, "w", encoding="utf-8") as fh:
        json.dump(good, fh)
    assert w.latest("example.com/log")["size"] == 7, "CONTROL: a readable state is read"
    with open(w.state_path, "w", encoding="utf-8") as fh:
        fh.write(garbage)
    with pytest.raises(Refused) as exc:
        w.latest("example.com/log")
    assert exc.value.status == 500
    assert os.path.exists(w.state_path) and open(w.state_path, encoding="utf-8").read() == garbage
