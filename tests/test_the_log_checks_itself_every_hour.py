"""The hourly self-check, and the corruptions it has to catch.

A published OK is worth nothing on its own: the question is whether this run can ever say FAILED.
So every test here breaks the published site in a different way and requires the verdict to change,
and each corruption ASSERTS that it changed the bytes before believing the result. That assertion is
not decoration. The first version of this acceptance replaced a substring that was not in the leaf,
compared two identical files, and reported that a tampered entry still verified: the control was
the defect, and it looked exactly like a finding about the code.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

from inspeximus import scitt
from inspeximus.core import new_receipt_keypair
from inspeximus.transparency import RegistrationPolicy, TransparencyService

pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
sys.path.insert(0, TOOLS)

import publish_static_log as publisher                                   # noqa: E402
import self_verify_log as selfcheck                                      # noqa: E402


@pytest.fixture()
def site(tmp_path):
    """A published log of six entries, exactly as the host publishes it."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk_hex, pub = new_receipt_keypair()
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(sk_hex))
    service = TransparencyService(str(tmp_path / "reg.log"), RegistrationPolicy("t"),
                                  sk.sign, lambda *_: True, service_pubkey=pub)
    for i in range(6):
        service.register(scitt.signed_statement(b"entry %d" % i, "urn:t:i", "s:%d" % i, sk.sign))
    out = str(tmp_path / "site")
    publisher.build(service, out, "file:///x", "t", "no witness")
    return out


def _copy(site, tmp_path, name):
    dest = str(tmp_path / name)
    shutil.copytree(site, dest)
    return dest


def test_a_healthy_site_verifies(site):
    out = selfcheck.run(site)
    assert out["verdict"] == "OK"
    assert out["entries"] == 7 and out["receipts_verified"] == 7   # six plus the policy entry
    assert out["problems"] == []


def test_a_tampered_leaf_is_caught(site, tmp_path):
    bad = _copy(site, tmp_path, "tampered")
    path = os.path.join(bad, "entries", "2.leaf.json")
    raw = open(path, "rb").read()
    changed = raw.replace(b'"issuer":"urn:t:i"', b'"issuer":"urn:t:X"')
    assert changed != raw, "the corruption changed nothing, so this test proves nothing"
    open(path, "wb").write(changed)
    out = selfcheck.run(bad)
    assert out["verdict"] == "FAILED"
    assert any("entry 2" in p for p in out["problems"])


def test_a_missing_receipt_is_caught(site, tmp_path):
    bad = _copy(site, tmp_path, "missing")
    os.remove(os.path.join(bad, "entries", "3.cose"))
    out = selfcheck.run(bad)
    assert out["verdict"] == "FAILED"
    assert out["entries_without_receipt"] == 1


def test_a_truncated_log_is_caught(site, tmp_path):
    """Removing the last line leaves a log consistent with itself, and the head then disagrees."""
    bad = _copy(site, tmp_path, "truncated")
    path = os.path.join(bad, "log.jsonl")
    lines = open(path, encoding="utf-8").read().splitlines()
    open(path, "w", encoding="utf-8", newline="\n").write("\n".join(lines[:-1]) + "\n")
    out = selfcheck.run(bad)
    assert out["verdict"] == "FAILED"
    assert any("claims" in p or "root" in p for p in out["problems"])


def test_a_rewritten_root_is_caught(site, tmp_path):
    bad = _copy(site, tmp_path, "rewritten")
    path = os.path.join(bad, "head.json")
    head = json.load(open(path, encoding="utf-8"))
    head["writes_tip"] = "ff" * 32
    json.dump(head, open(path, "w", encoding="utf-8", newline="\n"), indent=2, sort_keys=True)
    out = selfcheck.run(bad)
    assert out["verdict"] == "FAILED"


def test_a_run_that_cannot_read_the_site_publishes_that(tmp_path):
    """A missing verdict file and a bad verdict look the same from outside; only one is honest."""
    out_path = str(tmp_path / "verdict.json")
    code = selfcheck.main(["--site", str(tmp_path / "nothing-here"), "--out", out_path])
    assert code == 2
    written = json.load(open(out_path, encoding="utf-8"))
    assert written["verdict"] == "ERROR" and written["error"]


def test_the_exit_code_carries_the_verdict(site, tmp_path):
    ok_path = str(tmp_path / "ok.json")
    assert selfcheck.main(["--site", site, "--out", ok_path]) == 0
    assert json.load(open(ok_path, encoding="utf-8"))["verdict"] == "OK"

    bad = _copy(site, tmp_path, "for-exit-code")
    os.remove(os.path.join(bad, "entries", "1.cose"))
    bad_path = str(tmp_path / "failed.json")
    assert selfcheck.main(["--site", bad, "--out", bad_path]) == 1
    assert json.load(open(bad_path, encoding="utf-8"))["verdict"] == "FAILED"


def test_the_verdict_says_what_it_cannot_prove(site, tmp_path):
    out_path = str(tmp_path / "v.json")
    selfcheck.main(["--site", site, "--out", out_path])
    scope = json.load(open(out_path, encoding="utf-8"))["scope"]
    assert "operator checking the operator" in scope
    assert "witness" in scope, "the file must point at what independence actually comes from"


# -- the inclusion proof a reader can check with a hash function and a list ------------------------
def test_every_entry_publishes_an_audit_path_that_verifies_offline(site):
    """A document that names a log root is only honest if the reader can check that the root CONTAINS
    the document. The COSE receipt already carries this proof; the JSON file exists so checking it
    needs no COSE parser and no question asked of the server."""
    import json as _json

    from inspeximus import merkle

    head = _json.load(open(os.path.join(site, "head.json"), encoding="utf-8"))
    root = bytes.fromhex(head["writes_tip"])
    proofs = sorted(f for f in os.listdir(os.path.join(site, "entries")) if f.endswith(".proof.json"))
    assert len(proofs) == head["n_writes"], "every entry needs a proof, not most of them"

    for name in proofs:
        p = _json.load(open(os.path.join(site, "entries", name), encoding="utf-8"))
        leaf = open(os.path.join(site, p["leaf"]), "rb").read()
        assert p["root"] == head["writes_tip"]
        assert p["tree_size"] == head["n_writes"]
        assert merkle.leaf_hash(leaf).hex() == p["leaf_hash"]
        assert merkle.verify_inclusion(leaf, p["index"], p["tree_size"],
                                       [bytes.fromhex(h) for h in p["audit_path"]], root), name


def test_CONTROL_a_tampered_leaf_fails_its_own_audit_path(site):
    """Without this the test above cannot tell a working proof from arithmetic that accepts anything."""
    import json as _json

    from inspeximus import merkle

    root = bytes.fromhex(_json.load(open(os.path.join(site, "head.json"), encoding="utf-8"))["writes_tip"])
    p = _json.load(open(os.path.join(site, "entries", "2.proof.json"), encoding="utf-8"))
    leaf = bytearray(open(os.path.join(site, p["leaf"]), "rb").read())
    leaf[20] ^= 0x01
    assert bytes(leaf) != open(os.path.join(site, p["leaf"]), "rb").read()
    assert not merkle.verify_inclusion(bytes(leaf), p["index"], p["tree_size"],
                                       [bytes.fromhex(h) for h in p["audit_path"]], root)


def test_CONTROL_a_proof_read_against_the_wrong_index_fails(site):
    import json as _json

    from inspeximus import merkle

    root = bytes.fromhex(_json.load(open(os.path.join(site, "head.json"), encoding="utf-8"))["writes_tip"])
    p = _json.load(open(os.path.join(site, "entries", "2.proof.json"), encoding="utf-8"))
    leaf = open(os.path.join(site, p["leaf"]), "rb").read()
    assert not merkle.verify_inclusion(leaf, p["index"] + 1, p["tree_size"],
                                       [bytes.fromhex(h) for h in p["audit_path"]], root)


def test_the_proof_says_what_it_does_not_prove(site):
    import json as _json
    p = _json.load(open(os.path.join(site, "entries", "0.proof.json"), encoding="utf-8"))
    assert "does not prove the entry is true" in p["scope"]
    assert "witness" in p["scope"]
