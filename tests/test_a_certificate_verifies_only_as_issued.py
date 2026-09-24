"""An erasure certificate verifies only in the shape the library issues it (session E review, item 5).

Three edits made a certificate that still read VALID, in Python and on the browser page:

  a. a tombstone signed with a second key, appended after the certificate's `pubkey` field was deleted,
     so each signature was checked against the key it named;
  b. `self_check.verified` set to false, the issuing store's own verify_writes() failing at issue time;
  c. `scope`, `scope_covers` and `scope_excludes` deleted, so the certificate no longer says what it
     does NOT certify.

Each is INVALID now. The page reaches the same verdict as Python on every case, and the untouched
certificate is the control that must stay VALID in both.
"""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from inspeximus import Inspeximus, verify_erasure_certificate  # noqa: E402
from inspeximus.core import _canon, _sha256_hex, sth_hash_of  # noqa: E402
from inspeximus.merkle import root as merkle_root  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_verifier_page import (GROUP, browser, documents, page, python_verdict, site,  # noqa: E402,F401
                                verify_many)

OTHER = Ed25519PrivateKey.generate()


def _pub(k) -> str:
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def _cert(tmp_path):
    key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()).hex()
    m = Inspeximus(path=str(tmp_path / "s.json"), receipts=True, receipt_key=key)
    for i in range(2):
        m.remember(f"subject {i} likes tea", source={"doc": f"subj:{i}"})
        m.forget_subject(f"subj:{i}", request_id=f"r{i}")
    return m.erasure_certificate()


def _other_key_tombstone(cert):
    c = copy.deepcopy(cert)
    t = {"seq": len(c["tombstones"]), "memory_id": "never-erased", "ts": 2.0, "request_id": "r1",
         "prev": c["tombstones"][-1]["hash"]}
    t["hash"] = _sha256_hex(_canon(Inspeximus._tombstone_core(t)))
    t["pubkey"], t["sig"] = _pub(OTHER), OTHER.sign(bytes.fromhex(t["hash"])).hex()
    c["tombstones"].append(t)
    c.pop("pubkey")
    a = c["anchor"]
    a["n_tombstones"] = len(c["tombstones"])
    a["tombstones_tip"] = t["hash"]
    a["tombstones_root"] = merkle_root(
        [_canon(Inspeximus._chain_core(x, "tombstone")) for x in c["tombstones"]]).hex()
    a["sth_hash"] = sth_hash_of(a)
    c["erased_memory_ids"] = sorted({x["memory_id"] for x in c["tombstones"]})
    c["count"] = len(c["erased_memory_ids"])
    return c


def _failed_self_check(cert):
    c = copy.deepcopy(cert)
    c["self_check"] = {"verified": False, "problems": ["receipt 3: invalid signature"]}
    return c


def _without(cert, *keys):
    c = copy.deepcopy(cert)
    for k in keys:
        c.pop(k)
    return c


def test_CONTROL_the_certificate_as_issued_verifies(tmp_path):
    r = verify_erasure_certificate(_cert(tmp_path))
    assert r["valid"] is True, r["problems"]
    assert r["checks"]["issuer_self_check"] is True and r["checks"]["scope_intact"] is True


def test_a_tombstone_signed_by_another_key_with_pubkey_removed_is_invalid(tmp_path):
    r = verify_erasure_certificate(_other_key_tombstone(_cert(tmp_path)))
    assert r["valid"] is False
    assert any(p.startswith("the tombstones are signed by 2 different keys") for p in r["problems"]), r


def test_a_failed_self_check_is_invalid(tmp_path):
    r = verify_erasure_certificate(_failed_self_check(_cert(tmp_path)))
    assert r["valid"] is False and r["checks"]["issuer_self_check"] is False
    assert any("failed its own write check" in p for p in r["problems"]), r


@pytest.mark.parametrize("keys", [("scope",), ("scope_covers",), ("scope_excludes",),
                                  ("scope", "scope_covers", "scope_excludes")])
def test_a_removed_scope_statement_is_invalid(tmp_path, keys):
    r = verify_erasure_certificate(_without(_cert(tmp_path), *keys))
    assert r["valid"] is False and r["checks"]["scope_intact"] is False, r
    assert any("is missing" in p for p in r["problems"]), r


@GROUP
def test_the_page_agrees_on_every_case(page, tmp_path):
    cert = _cert(tmp_path)
    cases = {"as issued": cert, "other key": _other_key_tombstone(cert),
             "failed self_check": _failed_self_check(cert),
             "no scope": _without(cert, "scope"), "no scope_covers": _without(cert, "scope_covers"),
             "no scope at all": _without(cert, "scope", "scope_covers", "scope_excludes")}
    texts = {k: json.dumps(v, ensure_ascii=False, indent=2) for k, v in cases.items()}
    got = dict(zip(texts, verify_many(page, list(texts.values()))))
    want = {k: ("VALID" if k == "as issued" else "INVALID") for k in texts}
    seen = {k: (python_verdict(t), got[k]["verdict"]) for k, t in texts.items()}
    assert seen == {k: (v, v) for k, v in want.items()}, seen
