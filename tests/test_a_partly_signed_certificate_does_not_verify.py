"""A certificate whose tombstone chain is signed in places does not verify, as a bundle's does not.

verifier-page review F7 (2026-09-24): with a fully signed certificate, anyone WITHOUT the key could
append an unsigned tombstone for an id that was never erased, recompute the unsigned anchor, and get
VALID "N erasure(s) attested" with only a PARTIALLY SIGNED note. verify_bundle() already fails a partly
signed chain; verify_erasure_certificate() now does too.

Also here: the CLI never crashes while printing a verdict. A problem that quotes a lone surrogate
killed print() on a UTF-8 console (review I1).
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from inspeximus import Inspeximus, verify_erasure_certificate  # noqa: E402
from inspeximus.core import _canon, _sha256_hex, sth_hash_of  # noqa: E402
from inspeximus.merkle import root as merkle_root  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GENESIS = "0" * 64


def _signed_certificate(tmp_path):
    key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()).hex()
    m = Inspeximus(path=str(tmp_path / "s.json"), receipts=True, receipt_key=key)
    for i in range(3):
        m.remember(f"subject {i} likes tea", source={"doc": f"subj:{i}"})
        m.forget_subject(f"subj:{i}", request_id=f"r{i}")
    cert = m.erasure_certificate()
    assert all(t.get("sig") for t in cert["tombstones"]), "CONTROL: the chain must start fully signed"
    return cert


def _reanchor(cert):
    """Everything in the anchor that needs no key: tip, count, Merkle root, sth_hash."""
    toms = cert["tombstones"]
    a = cert["anchor"]
    a["n_tombstones"] = len(toms)
    a["tombstones_tip"] = toms[-1]["hash"] if toms else _GENESIS
    a["tombstones_root"] = merkle_root([_canon(Inspeximus._chain_core(t, "tombstone")) for t in toms]).hex()
    a["sth_hash"] = sth_hash_of(a)
    return cert


def test_CONTROL_the_fully_signed_certificate_verifies(tmp_path):
    assert verify_erasure_certificate(_signed_certificate(tmp_path))["valid"] is True


def test_an_unsigned_tombstone_appended_without_the_key_does_not_verify(tmp_path):
    cert = copy.deepcopy(_signed_certificate(tmp_path))
    t = {"seq": len(cert["tombstones"]), "memory_id": "never-erased", "ts": 1.0, "request_id": None,
         "prev": cert["tombstones"][-1]["hash"]}
    t["hash"] = _sha256_hex(_canon(Inspeximus._tombstone_core(t)))
    cert["tombstones"].append(t)
    _reanchor(cert)
    cert["erased_memory_ids"] = sorted({x["memory_id"] for x in cert["tombstones"]})
    cert["count"] = len(cert["erased_memory_ids"])
    res = verify_erasure_certificate(cert)
    assert res["valid"] is False, res
    assert any("PARTIALLY SIGNED" in p and "never-erased" in p for p in res["problems"]), res["problems"]
    assert res["checks"]["signatures_valid"] is False


def test_an_entirely_unsigned_certificate_is_still_a_limit_not_a_failure(tmp_path):
    """No key at all is a documented limit (the chain proves integrity, not authorship). Only a chain
    signed in places fails."""
    m = Inspeximus(path=str(tmp_path / "u.json"), receipts=True)
    m.remember("x likes tea", source={"doc": "subj:x"})
    m.forget_subject("subj:x", request_id="r")
    res = verify_erasure_certificate(m.erasure_certificate())
    assert res["valid"] is True and res["checks"]["signatures_valid"] is None
    assert any(lim.startswith("UNSIGNED") for lim in res.get("limits", []))


def test_the_cli_prints_a_verdict_when_a_problem_quotes_a_lone_surrogate(tmp_path):
    cert = _signed_certificate(tmp_path)
    cert["count"] = "\ud800"
    path = tmp_path / "cert.json"
    path.write_text(json.dumps(cert), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": ROOT, "PYTHONIOENCODING": "utf-8", "INSPEXIMUS_NO_UPDATE_CHECK": "1"}
    p = subprocess.run([sys.executable, "-m", "inspeximus.cli", "erasure-verify", str(path)],
                       capture_output=True, cwd=str(tmp_path), env=env)
    out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    assert "Traceback" not in out, out[-800:]
    assert p.returncode == 1 and "FAIL" in out, out[-800:]
