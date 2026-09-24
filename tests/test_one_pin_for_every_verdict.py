"""The other side of the fixes for X2, I4, I5, I7, E4 and E11 in audits/2026-09-24/mcp-tools-review.md.

The review's reproducers (tests/test_mcp_review_*.py) show each tool now REJECTS what it used to pass. A fix
that rejects everything passes those too, so this file holds the controls: an honest store still passes
every verdict the pin now reaches, the receipt pin does not leak onto the action ledger (which a different
key signs), and each new library argument does what its docstring says when called directly.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402
from inspeximus.erasure_residue import scan_residue  # noqa: E402

HONEST = ("the wire transfer limit for tier-2 accounts is 50000 EUR per day", "limit", "50000")


def _keypair():
    pytest.importorskip("cryptography")
    from inspeximus.core import new_receipt_keypair
    return new_receipt_keypair()


def _signed_store(path, sk, pk):
    st = Inspeximus(path=str(path), receipts=True, receipt_key=sk, receipt_pubkey=pk)
    st.remember(HONEST[0], key=HONEST[1], object=HONEST[2], source={"doc": "treasury/cfo"})
    st.remember("alice lives at 5 elm st", source={"doc": "crm/alice"})
    st.forget_subject("crm/alice", request_id="DSAR-1", basis="gdpr_art17")
    st.flush()
    return st


# ── X2: the configured pin, on an HONEST store ───────────────────────────────────────────────────────
def _pinned_server(monkeypatch, tmp_path, **env):
    pytest.importorskip("mcp")
    from _mcp_review import call, load_server
    sk, pk = _keypair()
    _signed_store(tmp_path / "store.json", sk, pk)
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_RECEIPTS="1", INSPEXIMUS_RECEIPT_PUBKEY=pk, **env)
    vw = call(mod, "verify_writes").data
    assert vw["ok"] is True and vw["expected_pubkey"] == pk, f"control: the honest chain verifies pinned: {vw}"
    return mod, call


def _memory_chain_verdicts(doc):
    found = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "memory_chain_verified":
                    found.append(v)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(doc)
    return found


def test_every_pinned_verdict_still_passes_an_honest_store(monkeypatch, tmp_path):
    """Pinned to the key that really signed the store, none of the eight tools may fail it."""
    mod, call = _pinned_server(monkeypatch, tmp_path)
    got = {
        "compliance_check": call(mod, "compliance_check").data,
        "compliance_report": call(mod, "compliance_report").data,
        "audit_bundle": call(mod, "audit_bundle").data,
        "verify_attribution": call(mod, "verify_attribution").data,
        "erasure_certificate": call(mod, "erasure_certificate", request_id="DSAR-1").data,
    }
    assert "integrity_failed" not in [v["code"] for v in got["compliance_check"]["violations"]], got["compliance_check"]
    assert got["compliance_report"]["summary"]["integrity_verified"] is True
    assert "limits" not in got["compliance_report"], "a pinned report must not say it is unpinned"
    assert got["audit_bundle"]["governance"]["proof"]["verified"] is True
    assert got["verify_attribution"]["chain_ok"] is True and got["verify_attribution"]["problems"] == []
    assert got["erasure_certificate"]["self_check"]["verified"] is True
    for tool, args in (("technical_documentation", {}), ("deployer_report", {}),
                       ("registration_export", {"section": "C"})):
        verdicts = _memory_chain_verdicts(call(mod, tool, **args).data)
        assert verdicts and all(v is True for v in verdicts), f"{tool}: {verdicts}"


def test_the_receipt_pin_does_not_reach_the_action_ledger(monkeypatch, tmp_path):
    """The action ledger is signed with the writer key, not the receipt key (review L2's fix note). Pinning
    it to INSPEXIMUS_RECEIPT_PUBKEY would fail every honest ledger, so only a key the caller passes pins it."""
    w_sk, _w_pk = _keypair()
    mod, call = _pinned_server(monkeypatch, tmp_path, INSPEXIMUS_ACTIONS="1", INSPEXIMUS_WRITER_KEY=w_sk)
    call(mod, "where_am_i")
    call(mod, "memory_report")
    led = call(mod, "actions_verify").data
    assert led["ok"] is True, f"control: the writer-signed ledger verifies on its own: {led}"
    doc = call(mod, "technical_documentation").data
    ev = json.dumps(doc)
    assert '"action_ledger_verified": true' in ev, "the receipt pin was applied to the writer-signed ledger"
    dep = call(mod, "deployer_report").data
    assert '"action_ledger_verified": true' in json.dumps(dep)


def test_an_unpinned_compliance_report_says_so(monkeypatch, tmp_path):
    pytest.importorskip("mcp")
    from _mcp_review import call, load_server
    sk, pk = _keypair()
    _signed_store(tmp_path / "store.json", sk, pk)
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_RECEIPTS="1")
    rep = call(mod, "compliance_report").data
    assert any("UNPINNED" in x for x in rep.get("limits", [])), rep.get("limits")


# ── the library arguments behind them ───────────────────────────────────────────────────────────────
def test_verify_attribution_binds_to_the_expected_key(tmp_path):
    sk, pk = _keypair()
    _other_sk, other_pk = _keypair()
    st = _signed_store(tmp_path / "s.json", sk, pk)
    assert st.verify_attribution(expected_pubkey=pk)["chain_ok"] is True
    bad = st.verify_attribution(expected_pubkey=other_pk)
    assert bad["ok"] is False and bad["chain_ok"] is False
    assert any("unexpected key" in p for p in bad["problems"])
    unsigned = Inspeximus(path=str(tmp_path / "u.json"), receipts=True)
    unsigned.remember("a note", source={"doc": "x"})
    r = unsigned.verify_attribution(expected_pubkey=pk)
    assert r["chain_ok"] is False and any("unsigned" in p for p in r["problems"])
    assert unsigned.verify_attribution()["chain_ok"] is True, "unpinned, an unsigned chain is unchanged"


def test_compliance_check_binds_to_the_expected_key(tmp_path):
    from inspeximus.compliance import compliance_check
    sk, pk = _keypair()
    _other_sk, other_pk = _keypair()
    st = _signed_store(tmp_path / "s.json", sk, pk)
    assert compliance_check(st, expected_pubkey=pk)["ok"] is True
    codes = [v["code"] for v in compliance_check(st, expected_pubkey=other_pk)["violations"]]
    assert "integrity_failed" in codes


def test_the_ledger_key_defaults_to_the_memory_key_for_library_callers(tmp_path):
    """One key passed once still pins both chains, as it did before `ledger_pubkey` existed."""
    from inspeximus.deployer import deployer_report
    from inspeximus.technical_documentation import annex_iv, registration_export

    from inspeximus.actions import ActionLedger

    class Ledger(ActionLedger):
        def verify(self, expected_pubkey=None, **kw):
            self.keys.append(expected_pubkey)
            return super().verify(expected_pubkey=expected_pubkey, **kw)

    def ledger():
        led = Ledger(st)
        led.keys = []
        return led

    st = Inspeximus(path=str(tmp_path / "s.json"), receipts=True)
    st.remember("a note")
    for build in (lambda led, **k: annex_iv(st, led, **k), lambda led, **k: deployer_report(st, led, **k),
                  lambda led, **k: registration_export(st, led, section="C", **k)):
        same, split = ledger(), ledger()
        build(same, expected_pubkey="aa" * 32)
        build(split, expected_pubkey="aa" * 32, ledger_pubkey=None)
        assert same.keys and set(same.keys) == {"aa" * 32}, same.keys
        assert split.keys and set(split.keys) == {None}, split.keys


# ── I4 ──────────────────────────────────────────────────────────────────────────────────────────────
def test_verify_consistency_reads_the_chain_on_disk_as_well(tmp_path):
    path = tmp_path / "s.json"
    st = Inspeximus(path=str(path), receipts=True)
    st.remember("one", key="a", object="1")
    st.flush()
    side = str(path) + ".receipts.json"
    earlier = open(side, "rb").read()
    st.remember("two", key="b", object="2")
    st.remember("three", key="c", object="3")
    st.flush()
    a = st.anchor()
    st.remember("four", key="d", object="4")
    assert st.verify_consistency(a) == (True, []), "control: growth after the anchor stays consistent"
    with open(side, "wb") as fh:                                   # the handle keeps its 4; disk has 1
        fh.write(earlier)
    ok, problems = st.verify_consistency(a)
    assert ok is False and any(p.startswith("on disk: write log shrank") for p in problems), problems
    os.remove(side)
    ok, problems = st.verify_consistency(a)
    assert ok is False and any("on disk: write log shrank: 0" in p for p in problems), problems
    with open(side, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    ok, problems = st.verify_consistency(a)
    assert ok is False and any("could not be read" in p for p in problems), problems


def test_verify_consistency_on_a_store_with_no_file_is_unchanged():
    m = Inspeximus(receipts=True)
    for i in range(3):
        m.remember(f"f{i}")
    a = m.anchor()
    m.remember("f3")
    assert m.verify_consistency(a) == (True, [])


# ── I5 ──────────────────────────────────────────────────────────────────────────────────────────────
def test_audit_the_audits_leaves_nothing_in_the_temp_dir_or_the_key_home(monkeypatch, tmp_path):
    systmp = tmp_path / "systmp"
    systmp.mkdir()
    home = tmp_path / "key_home"
    monkeypatch.setattr(tempfile, "tempdir", str(systmp))
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(home))
    st = Inspeximus(path=str(tmp_path / "store" / "s.json"), receipts=True, embed=False)
    st.remember("alice ssn 078-05-1120 is on file", source={"doc": "crm/alice"})
    st.remember("the region is frankfurt", key="svc::region", object="frankfurt")
    st.flush()
    heads_before = set(os.listdir(home / "inspeximus" / "heads"))
    out = st.audit_the_audits()
    assert out["probes"], "control: the audit ran its probes"
    # What may remain is the store LOCK of each path the audit wrote: content-free by design (opened, never
    # written) and held for the life of the process, so it is the lock's behaviour, not a copy of the store.
    left = [n for n in os.listdir(systmp)
            if not (n.startswith("inspeximus-") and n.endswith(".lock") and os.path.getsize(systmp / n) == 0)]
    assert left == [], f"left in the temp dir: {left}"
    assert set(os.listdir(home / "inspeximus" / "heads")) == heads_before, "copies' heads left in the key home"
    assert not any("could not all be removed" in x for x in out["limits"])


# ── I7 ──────────────────────────────────────────────────────────────────────────────────────────────
def test_a_configured_deployment_takes_the_receipt_invariant(tmp_path):
    path = str(tmp_path / "s.json")
    st = Inspeximus(path=path, embed=False)
    st.remember("a", key="a", object="a")
    st.flush()
    opened = Inspeximus(path=path, embed=False, receipts=True)

    def inv(**kw):
        return next(p for p in opened.admissibility_preconditions(**kw)["preconditions"]
                    if p["id"] == "receipt_chain_covers_records")
    assert (inv()["applicable"], inv()["holds"]) == (False, True), "a reader's flag alone still does not apply it"
    got = inv(receipts_configured=True)
    assert got["applicable"] is True and got["holds"] is False and got["receipts_configured"] is True
    plain = Inspeximus(path=path, embed=False)
    p = next(p for p in plain.admissibility_preconditions(receipts_configured=True)["preconditions"]
             if p["id"] == "receipt_chain_covers_records")
    assert p["applicable"] is False, "configured on a handle with receipts off does not apply"


# ── E4 / E11 ────────────────────────────────────────────────────────────────────────────────────────
def test_following_symlinks_enters_the_directory_and_can_come_back_clean(tmp_path):
    root = tmp_path / "scan"
    root.mkdir()
    target = tmp_path / "volume"
    target.mkdir()
    (target / "notes.txt").write_text("nothing here")
    try:
        os.symlink(target, root / "data", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks on this platform")
    unfollowed = scan_residue(str(root), ["alice@example.com"])
    assert unfollowed["ok"] is False and [s["path"] for s in unfollowed["skipped"]] == ["data"]
    followed = scan_residue(str(root), ["alice@example.com"], follow_symlinks=True)
    assert followed["ok"] is True and followed["checked_files"] == 1 and followed["skipped"] == []


def test_an_unlistable_directory_is_named_in_skipped(tmp_path, monkeypatch):
    root = tmp_path / "scan"
    (root / "locked").mkdir(parents=True)
    (root / "readme.txt").write_text("nothing to see")
    real = os.scandir

    def scandir(path="."):
        if os.fspath(path).rstrip(os.sep).endswith("locked"):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    r = scan_residue(str(root), ["alice@example.com"])
    monkeypatch.setattr(os, "scandir", real)
    assert r["ok"] is False
    assert r["skipped"] == [{"path": "locked", "why": "directory could not be listed (PermissionError)"}]
