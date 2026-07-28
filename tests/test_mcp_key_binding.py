"""The SEVENTH of the class, on the surface the README names as the moat.

`verify_writes(expected_pubkey=...)` is the check that a receipt was signed by the key you EXPECT rather
than by A key. Both MCP tamper-evidence tools -- `verify_writes` and the auditor/DPO-facing
`governance_report` -- took no arguments at all and called the core bare, so no MCP caller could ever
pin the key. `governance_report` even RETURNS `proof.expected_pubkey`, which was therefore always null.

MEASURED before the fix (probes/audit_mcp_verify_writes_key.py, both controls green): a store whose
contents were rewritten and whose ENTIRE receipt chain was re-signed under a foreign key returned
ok=True with zero problems -- while serving a wire-transfer limit inflated from 50,000 to 5,000,000 EUR.
Pinned to the honest key, the same store reports "signed by an unexpected key" on every receipt.

Scope, honestly: this is NOT the operator-holds-the-key attack. anchor() already owns that one and says
so in its docstring, and it needs an externally witnessed anchor to defeat. This is the cheaper attack --
a party who can write the store file but does NOT have the honest key -- which `expected_pubkey` defeats
using nothing but the public half the owner already has. That is the third-party check the constructor
docstring advertises ("a third party can verify it with the public key only"), and the MCP surface could
not perform it.

The fix is a pin from INSPEXIMUS_RECEIPT_PUBKEY (not only an argument a caller must remember) plus an
in-band `limits` when a SIGNED chain is verified unpinned -- the same remedy already applied one surface
over in verify_erasure_certificate. The last test is the class guard: it fails if a future tamper-evidence
tool returns a clean verdict on a signed chain while neither pinning nor declaring the limit.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("mcp")
pytest.importorskip("cryptography")

from inspeximus import Inspeximus  # noqa: E402
from inspeximus.core import new_receipt_keypair  # noqa: E402

HONEST = [
    ("the wire transfer limit for tier-2 accounts is 50000 EUR per day", "limit", "50000"),
    ("the incident escalation contact is the on-call SRE, not the vendor", "escalation", "on-call SRE"),
]
INFLATED = "the wire transfer limit for tier-2 accounts is 5000000 EUR per day"


@pytest.fixture()
def store_dir():
    return tempfile.mkdtemp()


def _write(path, sk, pk, rows):
    """Build the store the MCP server will later OPEN. Signed stores are written by a library holder;
    the MCP server has no signing key of its own, which is exactly why it is the verifying party here."""
    for f in list(os.listdir(os.path.dirname(path))):
        os.remove(os.path.join(os.path.dirname(path), f))
    st = Inspeximus(path=path, receipts=True, receipt_key=sk, receipt_pubkey=pk)
    for text, key, obj in rows:
        st.remember(text, key=key, object=obj)
    st.flush()
    return st


def _load(monkeypatch, path, pin=None):
    monkeypatch.setenv("INSPEXIMUS_PATH", path)
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS", "1")
    if pin:
        monkeypatch.setenv("INSPEXIMUS_RECEIPT_PUBKEY", pin)
    else:
        monkeypatch.delenv("INSPEXIMUS_RECEIPT_PUBKEY", raising=False)
    return importlib.reload(importlib.import_module("inspeximus.mcp_server"))


# ── the control: an honest store must still pass, pinned and unpinned ─────────────────────────────────

def test_an_honest_signed_store_passes_when_pinned(monkeypatch, store_dir):
    path = os.path.join(store_dir, "mcp.json")
    sk, pk = new_receipt_keypair()
    _write(path, sk, pk, HONEST)
    res = _load(monkeypatch, path, pin=pk).verify_writes()
    assert res["ok"] is True, res["problems"]
    assert res["expected_pubkey"] == pk
    assert "limits" not in res, "a pinned, honest verdict has nothing to disclaim"


# ── the defect ────────────────────────────────────────────────────────────────────────────────────────

def test_a_rewritten_and_resigned_store_is_caught_when_pinned(monkeypatch, store_dir):
    """THE test. Same store path, contents changed, whole history re-signed under a foreign key."""
    path = os.path.join(store_dir, "mcp.json")
    _sk_honest, pk_honest = new_receipt_keypair()
    sk_bad, pk_bad = new_receipt_keypair()
    _write(path, sk_bad, pk_bad, [(INFLATED, "limit", "5000000")] + HONEST[1:])

    mod = _load(monkeypatch, path, pin=pk_honest)
    res = mod.verify_writes()
    assert res["ok"] is False, "a foreign-key re-signing verified clean"
    assert any("unexpected key" in p for p in res["problems"]), res["problems"]
    # and the tampered content really is what the store serves -- otherwise the test proves nothing
    assert any(INFLATED == r["text"] for r in mod._MEM.items)


def test_the_caller_can_pin_without_configuring_the_environment(monkeypatch, store_dir):
    path = os.path.join(store_dir, "mcp.json")
    _sk_honest, pk_honest = new_receipt_keypair()
    sk_bad, pk_bad = new_receipt_keypair()
    _write(path, sk_bad, pk_bad, HONEST)
    res = _load(monkeypatch, path).verify_writes(expected_pubkey=pk_honest)
    assert res["ok"] is False and any("unexpected key" in p for p in res["problems"])


def test_an_unpinned_verdict_on_a_signed_chain_declares_what_it_did_not_check(monkeypatch, store_dir):
    """Unpinned, ok=True is still true ABOUT THE CHAIN -- so it stays true and says what it omits."""
    path = os.path.join(store_dir, "mcp.json")
    sk, pk = new_receipt_keypair()
    _write(path, sk, pk, HONEST)
    res = _load(monkeypatch, path).verify_writes()
    assert res["ok"] is True
    assert res["expected_pubkey"] is None
    assert res.get("limits"), "a signed chain verified without a pin disclosed nothing"
    assert "UNPINNED" in res["limits"][0]
    assert "INSPEXIMUS_RECEIPT_PUBKEY" in res["limits"][0], "the advice must name a reachable remedy"


def test_an_unsigned_store_is_not_nagged_about_a_key_it_never_had(monkeypatch, store_dir):
    """The other control. Receipts without signatures already report themselves; no `limits` noise."""
    path = os.path.join(store_dir, "mcp.json")
    st = Inspeximus(path=path, receipts=True)
    st.remember(HONEST[0][0], key="limit", object="50000")
    st.flush()
    res = _load(monkeypatch, path).verify_writes()
    assert "limits" not in res, res.get("limits")


# ── the auditor-facing surface, one level up ──────────────────────────────────────────────────────────

def test_governance_report_can_finally_pin_the_key_it_reports(monkeypatch, store_dir):
    path = os.path.join(store_dir, "mcp.json")
    _sk_honest, pk_honest = new_receipt_keypair()
    sk_bad, pk_bad = new_receipt_keypair()
    _write(path, sk_bad, pk_bad, HONEST)
    rep = _load(monkeypatch, path, pin=pk_honest).governance_report()
    assert rep["proof"]["expected_pubkey"] == pk_honest, "the field existed but was always null over MCP"
    assert rep["proof"]["verified"] is False


def test_governance_report_declares_the_limit_when_unpinned(monkeypatch, store_dir):
    path = os.path.join(store_dir, "mcp.json")
    sk, pk = new_receipt_keypair()
    _write(path, sk, pk, HONEST)
    rep = _load(monkeypatch, path).governance_report()
    assert rep["proof"]["verified"] is True
    assert rep.get("limits") and "UNPINNED" in rep["limits"][0]


# ── the class guard ───────────────────────────────────────────────────────────────────────────────────

def test_no_tamper_evidence_tool_returns_a_clean_verdict_it_cannot_support(monkeypatch, store_dir):
    """Written for the EIGHTH instance, not the seventh.

    Over a SIGNED store with no pin configured, every MCP tool that reports a tamper-evidence verdict must
    either bind the key or say it did not. A new tool that returns a bare clean answer fails here.
    """
    path = os.path.join(store_dir, "mcp.json")
    sk, pk = new_receipt_keypair()
    _write(path, sk, pk, HONEST)
    mod = _load(monkeypatch, path)

    offenders = []
    for name in ("verify_writes", "governance_report"):
        res = mod.__dict__[name]()
        verdict = res.get("ok", res.get("proof", {}).get("verified"))
        if verdict is True and not res.get("limits") and not res.get("expected_pubkey"):
            offenders.append(name)
    assert not offenders, (
        f"{offenders} report a clean tamper-evidence verdict over a signed chain without pinning the "
        f"key or declaring that they did not -- the class this file exists for")
