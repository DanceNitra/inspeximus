"""MCP tool review, family: TAMPER-EVIDENCE, AUDIT AND COMPLIANCE REPORTS.

Part of audits/2026-09-24/mcp-tools-review.md. Tools: verify_writes, anchor, verify_consistency,
verify_cosigned_anchor, detect_split_view, witness, verify_witness, state_digest, governance_report,
admissibility_preconditions, audit_the_audits, audit_bundle, verify_audit_bundle, verify_attribution,
compliance_report, compliance_check, coverage.

Each test holds one tool to a sentence of its OWN description (the docstring an MCP client is shown) or
to the server's documented configuration (the module docstring's INSPEXIMUS_RECEIPT_PUBKEY promise), and
fails today. They are strict xfails: the day the tool is fixed, the test XPASSes and the marker has to
come off. Preconditions go through `pytest.fail`, so a setup that did not do what the test needs fails
loudly instead of passing as an expected failure. Every test runs on a throwaway store in tmp_path (see
tests/_mcp_review.py); INSPEXIMUS_KEY_HOME is pointed into tmp_path too, so the receipt-chain head the
library keeps outside the store directory does not land in the developer's ~/.config.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

pytest.importorskip("mcp")

from _mcp_review import call, load_server  # noqa: E402

from inspeximus import Inspeximus  # noqa: E402

XFAIL = dict(strict=True, raises=AssertionError)

HONEST = ("the wire transfer limit for tier-2 accounts is 50000 EUR per day", "limit", "50000")
INFLATED = ("the wire transfer limit for tier-2 accounts is 5000000 EUR per day", "limit", "5000000")


@pytest.fixture()
def server(monkeypatch, tmp_path):
    """Factory: `server(**env)` reloads the MCP server on tmp_path/store.json with only `env` set.

    Returned as a factory so a test can build the store file BEFORE the server opens it (a signed store is
    written by a library holder; the server has no receipt key of its own)."""
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))

    def _load(**env):
        return load_server(monkeypatch, tmp_path, **env)

    _load.store_path = tmp_path / "store.json"
    return _load


def _keypair():
    pytest.importorskip("cryptography")
    from inspeximus.core import new_receipt_keypair
    return new_receipt_keypair()


def _foreign_signed_store(path, text=INFLATED[0], key=INFLATED[1], obj=INFLATED[2], source="treasury/cfo"):
    """A store REWRITTEN and re-signed under a key the owner never held; returns the owner's public key.

    This is the attack the module docstring names for INSPEXIMUS_RECEIPT_PUBKEY: "a party who rewrites the
    store and re-signs it with a key of their own"."""
    _sk_honest, pk_honest = _keypair()
    sk_bad, pk_bad = _keypair()
    st = Inspeximus(path=str(path), receipts=True, receipt_key=sk_bad, receipt_pubkey=pk_bad)
    st.remember(text, key=key, object=obj, source={"doc": source})
    st.flush()
    return pk_honest


def _pinned_server_over_a_foreign_signed_store(server):
    pk_honest = _foreign_signed_store(server.store_path)
    mod = server(INSPEXIMUS_RECEIPT_PUBKEY=pk_honest)
    vw = call(mod, "verify_writes")
    # The pin is live and the chain really is foreign: the tool the pin was first wired into rejects it.
    if vw.is_error or vw.data.get("ok") is not False or vw.data.get("expected_pubkey") != pk_honest \
            or not any("unexpected key" in p for p in vw.data.get("problems", [])):
        pytest.fail(f"precondition: pinned verify_writes must reject the foreign-signed chain, got {vw}")
    return mod, pk_honest


def _disk_counts(store_path):
    """(records in the store file, receipts in the sidecar), read from disk rather than the handle."""
    from inspeximus import sqlite_store
    if sqlite_store.looks_like_sqlite(store_path):
        items = sqlite_store.load(store_path)
    else:
        raw = json.loads(open(store_path, encoding="utf-8").read())
        items = raw.get("items", raw) if isinstance(raw, dict) else raw
    receipts = json.loads(open(str(store_path) + ".receipts.json", encoding="utf-8").read())
    return len(items), len(receipts)


# ── INSPEXIMUS_RECEIPT_PUBKEY: "the tamper-evidence tools" ─────────────────────────────────────────────
@pytest.mark.xfail(reason="compliance_check: integrity_failed = 'the chain fails verify_writes'; with "
                          "INSPEXIMUS_RECEIPT_PUBKEY set it ignores the pin and passes a foreign-signed chain",
                   **XFAIL)
def test_compliance_check_reports_integrity_failed_for_a_chain_the_configured_pin_rejects(server):
    """compliance_check: "violations include ... integrity_failed (Art.12/15)" -- documented in the library as
    "the receipt/tombstone chain fails verify_writes". Module docstring: INSPEXIMUS_RECEIPT_PUBKEY is the key
    "the write receipts are expected to be signed by ... without it the tamper-evidence tools verify that
    receipts are signed by SOMEBODY".

    With the pin configured, verify_writes on the same server rejects the chain; the CI gate says ok.
    """
    mod, _pk = _pinned_server_over_a_foreign_signed_store(server)
    res = call(mod, "compliance_check")
    if res.is_error:
        pytest.fail(f"precondition: compliance_check must run, got {res.text}")
    codes = [v.get("code") for v in res.data["violations"]]
    assert res.data["ok"] is False and "integrity_failed" in codes, \
        f"the gate passed a chain the configured pin rejects: {res.data}"


@pytest.mark.xfail(reason="compliance_report: INSPEXIMUS_RECEIPT_PUBKEY is not the default pin, so "
                          "summary.integrity_verified is True over a foreign-signed chain", **XFAIL)
def test_compliance_report_integrity_verdict_is_bound_to_the_configured_pin(server):
    """compliance_report: "EU AI Act AGENT-MEMORY compliance EVIDENCE ... with LIVE counts from this store and
    an honest per-control status". Module docstring: INSPEXIMUS_RECEIPT_PUBKEY pins "the tamper-evidence
    tools"; without it they "verify that receipts are signed by SOMEBODY".
    """
    mod, _pk = _pinned_server_over_a_foreign_signed_store(server)
    res = call(mod, "compliance_report")
    if res.is_error:
        pytest.fail(f"precondition: compliance_report must run, got {res.text}")
    assert res.data["summary"]["integrity_verified"] is False, \
        "the evidence report certifies chain integrity for a chain the configured pin rejects"


@pytest.mark.xfail(reason="audit_bundle: INSPEXIMUS_RECEIPT_PUBKEY is not the default pin, so the exported "
                          "governance.proof says verified=True (expected_pubkey null) over a foreign-signed chain",
                   **XFAIL)
def test_audit_bundle_governance_verdict_is_bound_to_the_configured_pin(server):
    """audit_bundle: "Export a portable, CONTENT-FREE audit bundle of this store's whole write + erasure
    history (EU AI Act Art. 12/19)". The bundle carries `governance.proof.verified`; module docstring:
    INSPEXIMUS_RECEIPT_PUBKEY pins "the tamper-evidence tools".
    """
    mod, _pk = _pinned_server_over_a_foreign_signed_store(server)
    res = call(mod, "audit_bundle")
    if res.is_error:
        pytest.fail(f"precondition: audit_bundle must run, got {res.text}")
    proof = res.data["governance"]["proof"]
    assert proof["verified"] is False, \
        f"the exported bundle states verified=True under expected_pubkey={proof['expected_pubkey']!r}"


@pytest.mark.xfail(reason="verify_attribution: a TAMPER-EVIDENCE tool that never consults "
                          "INSPEXIMUS_RECEIPT_PUBKEY; ok=True over a relabel re-signed under a foreign key", **XFAIL)
def test_verify_attribution_is_bound_to_the_configured_pin(server):
    """verify_attribution: "TAMPER-EVIDENCE for the attribution / poison-defense layer: are k, the influence
    budget, the influence gate, and the slash ledger internally consistent and unedited?" Module docstring:
    INSPEXIMUS_RECEIPT_PUBKEY pins "the tamper-evidence tools"; without it a party who "rewrites the store
    and re-signs it with a key of their own" passes.
    """
    mod, _pk = _pinned_server_over_a_foreign_signed_store(server)
    res = call(mod, "verify_attribution")
    if res.is_error:
        pytest.fail(f"precondition: verify_attribution must run, got {res.text}")
    assert res.data["ok"] is False, \
        f"attribution committed under a foreign key verified clean with the pin configured: {res.data}"


# ── verify_consistency ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="verify_consistency: 'nothing was rewritten, rolled back'; on a running server a "
                          "store rolled back on disk reports consistent=True (the refresh merge re-adds "
                          "the rolled-back entries in memory)", **XFAIL)
def test_verify_consistency_on_a_running_server_catches_a_rollback_on_disk(server):
    """verify_consistency: "confirm the store is a consistent forward-extension of the witnessed anchor
    (nothing was rewritten, rolled back, or re-signed away)".

    The store file and its receipt sidecar are put back to an earlier state while the server runs, as an
    operator restoring an old copy would. A freshly started server reports "write log shrank"; the running
    one answers from the chain it still holds in memory.
    """
    mod = server(INSPEXIMUS_RECEIPTS="1")
    call(mod, "remember", text="the region is frankfurt", key="svc::region", object="frankfurt")
    mod._MEM.flush()
    sp = server.store_path
    earlier = {p: open(p, "rb").read() for p in (str(sp), str(sp) + ".receipts.json")}
    call(mod, "remember", text="the limit is 50000", key="limit", object="50000")
    call(mod, "remember", text="the escalation contact is the on-call SRE", key="esc", object="sre")
    mod._MEM.flush()
    prior = call(mod, "anchor").data
    if prior.get("n_writes") != 3 or _disk_counts(sp) != (3, 3):
        pytest.fail(f"precondition: three receipted writes before the anchor, got {prior} / {_disk_counts(sp)}")

    for p, b in earlier.items():                               # the rollback, on disk
        with open(p, "wb") as fh:
            fh.write(b)
    st = os.stat(sp)
    os.utime(sp, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    if _disk_counts(sp) != (1, 1):
        pytest.fail(f"precondition: the store on disk must be rolled back to 1 write, got {_disk_counts(sp)}")

    res = call(mod, "verify_consistency", prior_anchor=prior)
    if res.is_error:
        pytest.fail(f"precondition: verify_consistency must run, got {res.text}")
    if _disk_counts(sp) != (1, 1):
        pytest.fail("precondition: the store must still be rolled back when the verdict is given")
    assert res.data["consistent"] is False, \
        f"a store rolled back from 3 writes to 1 on disk was reported consistent with the 3-write anchor: {res.data}"


# ── admissibility_preconditions ─────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="admissibility_preconditions: receipt_chain_covers_records is described as 'if "
                          "receipts are enabled and records exist, the chain is not empty'; that exact shape "
                          "reports applicable=false, holds=true, ok=true", **XFAIL)
def test_admissibility_receipt_invariant_fires_when_receipts_are_enabled_and_the_chain_is_empty(server):
    """admissibility_preconditions: "receipt_chain_covers_records   if receipts are enabled and records exist,
    the chain is not empty" ... "the third is the same shape found in our own 450-record store, which had
    receipts enabled, an empty chain".
    """
    st = Inspeximus(path=str(server.store_path))
    st.remember("the region is frankfurt", key="svc::region", object="frankfurt")
    st.remember("a second note")
    st.flush()
    del st
    mod = server(INSPEXIMUS_RECEIPTS="1")
    if not (mod._MEM.receipts_enabled and not mod._MEM._receipts and len(mod._MEM.items) == 2):
        pytest.fail("precondition: receipts enabled, two records, an empty chain")
    vw = call(mod, "verify_writes").data
    if not any("chain is EMPTY" in p for p in vw.get("problems", [])):
        pytest.fail(f"precondition: verify_writes on the same server must see the empty chain, got {vw}")

    res = call(mod, "admissibility_preconditions")
    if res.is_error:
        pytest.fail(f"precondition: admissibility_preconditions must run, got {res.text}")
    inv = next(p for p in res.data["preconditions"] if p["id"] == "receipt_chain_covers_records")
    assert inv["applicable"] is True and inv["holds"] is False and res.data["ok"] is False, \
        f"receipts enabled + records + empty chain did not fail the invariant: {inv}, ok={res.data['ok']}"


# ── audit_the_audits ────────────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="audit_the_audits: 'Corrupts a temporary COPY'; the copies of the store (content "
                          "included) are left in the system temp dir and survive a later erasure", **XFAIL)
def test_audit_the_audits_leaves_no_copy_of_the_store_behind(server, monkeypatch, tmp_path):
    """audit_the_audits: "Corrupts a temporary COPY (never your store) in ways each surface claims to detect".

    The copies are made with tempfile.mkdtemp() and never removed, so every call leaves full copies of the
    store file -- record text included -- in the system temp directory, where a later forget_subject does
    not reach them.
    """
    systmp = tmp_path / "systmp"
    systmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(systmp))
    marker = "alice ssn 078-05-1120"
    mod = server(INSPEXIMUS_RECEIPTS="1")
    call(mod, "remember", text=f"{marker} is on file", source="crm/alice")
    call(mod, "remember", text="the region is frankfurt", key="svc::region", object="frankfurt")
    mod._MEM.flush()

    res = call(mod, "audit_the_audits")
    if res.is_error or not res.data.get("probes"):
        pytest.fail(f"precondition: audit_the_audits must run its probes, got {res.text[:300]}")
    er = call(mod, "forget_subject", subject="crm/alice")
    if er.is_error or any(marker in (r.get("text") or "") for r in mod._MEM.items):
        pytest.fail(f"precondition: the subject must be erased from the live store, got {er.text[:300]}")

    left = []
    for root, _dirs, files in os.walk(systmp):
        for f in files:
            p = os.path.join(root, f)
            try:
                if marker.encode() in open(p, "rb").read():
                    left.append(os.path.relpath(p, systmp))
            except OSError:
                pass
    assert not left, f"{len(left)} file(s) in the temp dir still hold the erased text, e.g. {left[:3]}"


# ── anchor ──────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="anchor: 'emit a SIGNED HEAD COMMITMENT'; the MCP anchor carries no signature "
                          "and the tool has no way to request one", **XFAIL)
def test_anchor_returns_a_signed_head_commitment(server):
    """anchor: "emit a SIGNED HEAD COMMITMENT -- a compact, externally-publishable snapshot {n_writes,
    writes_tip, n_tombstones, tombstones_tip, ts} that hash-commits to the ENTIRE write + erasure history".
    """
    mod = server(INSPEXIMUS_RECEIPTS="1")
    call(mod, "remember", text="the region is frankfurt", key="svc::region", object="frankfurt")
    res = call(mod, "anchor")
    if res.is_error or res.data.get("n_writes") != 1 or not res.data.get("sth_hash"):
        pytest.fail(f"precondition: an anchor over one receipted write, got {res}")
    assert any("sig" in k for k in res.data), f"the head commitment carries no signature: {sorted(res.data)}"
