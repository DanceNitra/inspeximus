"""The absence proof must not report `valid` when it did not run.

`verify_erasure_certificate` performs four checks; the fourth is the one the product is sold on -- given
the store, every erased id is genuinely ABSENT from it, the "read the raw store" proof that soft-delete
systems fail. Its `store_path` branch had no test at all, and inverting its encryption-magic check
survived the whole suite.

Measured before the fix:

    correct plaintext path   valid=True   store_absent=True
    WRONG/missing path       valid=True   store_absent=None    <- typo the path, get a clean verdict
    ENCRYPTED store          valid=True   store_absent=None    <- proof skipped, still "valid"
    no store given at all    valid=True   store_absent=None

An auditor reads `valid`. A typo in the path silently downgraded the strongest check to "not performed"
while the verdict stayed clean -- the problem text was in `problems`, which `valid` ignored.

The distinction the code was missing: NOT asking for the absence proof is honest chain-only verification;
asking and not getting it is not. `valid` now requires `store_absent is True` whenever a store was
supplied, and stays True for a caller who supplied none.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core
from inspeximus import Inspeximus


@pytest.fixture()
def erased():
    """A store with one erased record, its certificate, and the path on disk."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "m.json")
    m = Inspeximus(path=p, receipts=True)
    rid = m.remember("alice lives at 12 Oak St")
    m.remember("an unrelated record that stays")
    m.forget(ids=[rid])
    m.flush()
    return m, m.erasure_certificate(), p, rid, d


# ── the branch that had no test ─────────────────────────────────────────────────────────────────────
def test_a_correct_store_path_verifies_and_performs_the_proof(erased):
    m, cert, p, _rid, _d = erased
    res = core.verify_erasure_certificate(cert, store_path=p)
    assert res["valid"] is True, res["problems"]
    assert res["checks"]["store_absent"] is True, "the proof must actually run, not just not-fail"


def test_an_unreadable_store_path_is_not_valid(erased):
    """The typo case. Requesting the proof and not getting it must not read as a clean audit."""
    m, cert, _p, _rid, d = erased
    res = core.verify_erasure_certificate(cert, store_path=os.path.join(d, "TYPO.json"))
    assert res["valid"] is False, res
    assert res["checks"]["store_absent"] is None
    assert any("REQUESTED but could not run" in x for x in res["problems"]), res["problems"]


def test_an_encrypted_store_is_not_valid_without_decrypted_items(erased):
    """The absence proof cannot read ciphertext. Saying so in `problems` while returning valid=True is the
    same failure as the typo: the caller asked and did not get it."""
    m, cert, _p, _rid, d = erased
    enc = os.path.join(d, "enc.json")
    with open(enc, "wb") as fh:
        fh.write(core._INSPEXIMUS_ENC_MAGIC + b"ciphertext-goes-here")
    res = core.verify_erasure_certificate(cert, store_path=enc)
    assert res["valid"] is False, res
    assert any("encrypted" in x for x in res["problems"]), res["problems"]


def test_supplying_no_store_stays_valid_as_chain_only_verification(erased):
    """The fix must not turn every chain-only verification into a failure -- that would be a gate that
    never passes, and callers would learn to ignore it."""
    m, cert, _p, _rid, _d = erased
    res = core.verify_erasure_certificate(cert)
    assert res["valid"] is True, res["problems"]
    assert res["checks"]["store_absent"] is None, "and it must be visible that the proof did not run"


def test_decrypted_store_items_satisfy_the_request(erased):
    m, cert, _p, _rid, _d = erased
    res = core.verify_erasure_certificate(cert, store_items=list(m.items))
    assert res["valid"] is True and res["checks"]["store_absent"] is True, res


# ── the proof still has to catch the thing it exists for ────────────────────────────────────────────
def test_an_erased_id_still_present_in_the_store_fails(erased):
    """The whole point. A soft-delete system passes the chain checks and fails here."""
    m, cert, _p, rid, _d = erased
    resurrected = list(m.items) + [{"id": rid, "text": "alice lives at 12 Oak St",
                                    "status": "active", "ts": 0.0}]
    res = core.verify_erasure_certificate(cert, store_items=resurrected)
    assert res["valid"] is False
    assert res["checks"]["store_absent"] is False
    assert any("STILL PRESENT" in x for x in res["problems"]), res["problems"]


def test_the_proof_reads_the_file_on_disk_not_the_live_store(erased):
    """`store_path` exists so an auditor can check the RAW file without trusting the running process. If a
    resurrected record is written straight into the JSON, the path branch must catch it."""
    import json

    m, cert, p, rid, _d = erased
    raw = json.loads(open(p, encoding="utf-8").read())
    rows = raw if isinstance(raw, list) else raw.get("items", raw.get("records"))
    assert isinstance(rows, list), f"unexpected store layout: {type(raw)}"
    rows.append({"id": rid, "text": "alice lives at 12 Oak St", "status": "active", "ts": 0.0})
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(raw, fh)

    res = core.verify_erasure_certificate(cert, store_path=p)
    assert res["valid"] is False, res
    assert res["checks"]["store_absent"] is False, res
