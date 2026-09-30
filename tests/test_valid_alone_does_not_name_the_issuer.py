"""`valid` from verify_erasure_certificate() said nothing about who issued the certificate.

Found by the launch-video crew on 2026-09-29, reading the verdict of a real run:

1. UNPINNED. With no `expected_pubkey`, every signature is checked against the `pubkey` the
   certificate itself carries. Anyone who holds any key can re-sign an honest certificate's
   tombstones with that key, swap `pubkey`, and the result verifies `valid: true`. The chain and
   the anchor do not cover the key, so nothing else notices. The docstring said the check "does NOT
   trust the operator", which is true only when the caller pins the key.
2. UNSIGNED. A certificate with no signature at all returns `valid: true` with an UNSIGNED note in
   `limits`, so a caller that reads only `valid` cannot tell it from a signed one.
3. NOT WITNESSED. The note that the anchor was checked against itself only was added by the
   `erasure-verify` command and not by the library function, so a library caller never saw it.

The fix keeps `valid` for what the chain proves, adds `require_signed=True` (and
`erasure-verify --require-signed`) for a caller who needs a signature, and makes `limits` name
every input that was not given.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus, new_source_keypair  # noqa: E402
from inspeximus.core import verify_erasure_certificate  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _erased(signed: bool):
    kw: dict = {"path": None, "receipts": True}
    pk = None
    if signed:
        sk, pk = new_source_keypair()
        kw["receipt_key"] = sk
    st = Inspeximus(**kw)
    st.remember("Jane prefers invoices to jane@example.test", key="invoice-email",
                source={"doc": "jane.example"})
    st.remember("Jane moved invoices to billing@example.test", key="invoice-email",
                source={"doc": "jane.example"})
    st.forget_subject("jane.example", request_id="req-1")
    return st.erasure_certificate(request_id="req-1"), [dict(r) for r in st.items], pk


def _resigned_by_another_key(cert: dict) -> dict:
    """What anyone with a key can do to an honest certificate, with no access to the issuer's key."""
    Ed25519PrivateKey = pytest.importorskip(
        "cryptography.hazmat.primitives.asymmetric.ed25519").Ed25519PrivateKey
    other_sk, other_pk = new_source_keypair()
    k = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(other_sk))
    forged = copy.deepcopy(cert)
    forged["pubkey"] = other_pk
    for t in forged["tombstones"]:
        t["sig"] = k.sign(bytes.fromhex(t["hash"])).hex()
        if "pubkey" in t:
            t["pubkey"] = other_pk
    return forged


def _limit(res: dict, name: str) -> bool:
    return any(x.startswith(name + ":") for x in res["limits"])


def test_the_fixture_has_signatures_to_forge():
    """Control: every test below relies on a signed chain, so the fixture must produce one."""
    pytest.importorskip("cryptography")
    cert, _, pk = _erased(signed=True)
    assert len(cert["tombstones"]) == 2
    assert all(t.get("sig") for t in cert["tombstones"])
    assert cert["pubkey"] == pk


def test_a_resigned_certificate_verifies_unpinned_and_says_so():
    pytest.importorskip("cryptography")
    cert, items, pk = _erased(signed=True)
    forged = _resigned_by_another_key(cert)
    assert forged["pubkey"] != pk
    res = verify_erasure_certificate(forged, store_items=items)
    # This is the behaviour the note exists for: without a pin, the forgery is indistinguishable.
    assert res["valid"] is True, res["problems"]
    assert _limit(res, "UNPINNED"), res["limits"]


def test_pinning_the_issuers_key_refuses_the_resigned_certificate():
    pytest.importorskip("cryptography")
    cert, items, pk = _erased(signed=True)
    res = verify_erasure_certificate(_resigned_by_another_key(cert), store_items=items,
                                     expected_pubkey=pk)
    assert res["valid"] is False
    assert any("unexpected key" in p for p in res["problems"]), res["problems"]
    honest = verify_erasure_certificate(cert, store_items=items, expected_pubkey=pk)
    assert honest["valid"] is True, honest["problems"]
    assert not _limit(honest, "UNPINNED"), honest["limits"]


def test_an_unsigned_certificate_is_valid_only_when_a_signature_is_not_required():
    cert, items, _ = _erased(signed=False)
    loose = verify_erasure_certificate(cert, store_items=items)
    assert loose["valid"] is True, loose["problems"]
    assert _limit(loose, "UNSIGNED"), loose["limits"]
    strict = verify_erasure_certificate(cert, store_items=items, require_signed=True)
    assert strict["valid"] is False
    assert any(p.startswith("UNSIGNED, and require_signed=True") for p in strict["problems"]), \
        strict["problems"]


def test_require_signed_does_not_refuse_a_signed_certificate():
    pytest.importorskip("cryptography")
    cert, items, pk = _erased(signed=True)
    res = verify_erasure_certificate(cert, store_items=items, expected_pubkey=pk, require_signed=True)
    assert res["valid"] is True, res["problems"]


def test_the_library_states_an_unwitnessed_anchor_itself():
    cert, items, _ = _erased(signed=False)
    res = verify_erasure_certificate(cert, store_items=items)
    assert _limit(res, "NOT WITNESSED"), res["limits"]
    witnessed = verify_erasure_certificate(cert, store_items=items, expected_anchor=cert["anchor"])
    assert witnessed["checks"]["anchor_witnessed"] is True
    assert not _limit(witnessed, "NOT WITNESSED"), witnessed["limits"]


def _cli(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env["PYTHONPATH"] = ROOT
    return subprocess.run([sys.executable, "-m", "inspeximus.cli", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_erasure_verify_require_signed_fails_an_unsigned_certificate(tmp_path):
    cert, _, _ = _erased(signed=False)
    p = tmp_path / "cert.json"
    p.write_text(json.dumps(cert), encoding="utf-8")
    loose = _cli("erasure-verify", str(p), cwd=tmp_path)
    assert loose.returncode == 0, loose.stdout + loose.stderr
    strict = _cli("erasure-verify", str(p), "--require-signed", cwd=tmp_path)
    assert strict.returncode == 1, strict.stdout + strict.stderr
    assert "require_signed=True" in strict.stdout
    # The NOT WITNESSED note now comes from the library; the command must print it exactly once.
    assert loose.stdout.count("NOT WITNESSED") == 1, loose.stdout


# ---- AUDIT-A, 2026-09-30: require_signed does not check WHOSE signature it is --------------------------

def test_require_signed_passes_a_certificate_the_attacker_signed_and_authorship_says_so():
    """The trap: an attacker's own key satisfies require_signed. `authorship` is the field that tells."""
    pytest.importorskip("cryptography")
    cert, items, pk = _erased(signed=True)
    forged = _resigned_by_another_key(cert)
    res = verify_erasure_certificate(forged, store_items=items, require_signed=True)
    assert res["valid"] is True, res["problems"]
    assert res["authorship"] == "self-asserted"
    pinned = verify_erasure_certificate(forged, store_items=items, require_signed=True, expected_pubkey=pk)
    assert pinned["valid"] is False
    assert pinned["authorship"] != "pinned"


def test_authorship_names_each_of_the_three_cases():
    pytest.importorskip("cryptography")
    cert, items, pk = _erased(signed=True)
    assert verify_erasure_certificate(cert, store_items=items, expected_pubkey=pk)["authorship"] == "pinned"
    assert verify_erasure_certificate(cert, store_items=items)["authorship"] == "self-asserted"
    unsigned, u_items, _ = _erased(signed=False)
    assert verify_erasure_certificate(unsigned, store_items=u_items)["authorship"] == "unsigned"
