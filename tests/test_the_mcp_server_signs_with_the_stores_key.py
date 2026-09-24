"""The MCP server signs what it appends with the store's own receipt key, and one ledger key signs the ledger.

X1 and X3 in the MCP tool review of 2026-09-24 (audits/2026-09-24/mcp-tools-review.md on review-mcp-tools):

  X1  open_store() was called with no receipt key and nothing could supply one, so on a store the library
      signed, one `remember`, `forget` or `retention(apply=True)` through the server appended an UNSIGNED
      receipt or tombstone and verify_writes failed from then on.
  X3  twenty-two tools wrote the action ledger through a second, keyless ActionLedger, so on a server
      that signs its ledger each of their entries went in unsigned and actions_verify failed from then on.

The review's own reproducers (tests/test_mcp_review_*.py: W7, E6, L1, L3, L4, S5) cover the fixed paths.
These cover what the fix added and what it must not change: the unsigned control, each place the key is
found, the keys the server refuses at startup rather than breaking a chain with, the ledger keeping the
key it is already signed with, and an unreadable ledger refusing BEFORE a tool changes anything.
"""
import json

import pytest

pytest.importorskip("mcp")
pytest.importorskip("cryptography")

from _mcp_review import call, load_server  # noqa: E402
from _store_io import load_store, save_store  # noqa: E402

from inspeximus import Inspeximus, receipt_key_for, verify_erasure_certificate  # noqa: E402
from inspeximus.core import new_receipt_keypair  # noqa: E402


def _library_store(tmp_path, **kw):
    """Two records and one erasure written by the LIBRARY at tmp_path/store.json, with receipts."""
    lib = Inspeximus(path=str(tmp_path / "store.json"), receipts=True, **kw)
    lib.remember("alice lives at 5 elm st", source={"doc": "crm/alice"})
    lib.remember("bob lives at 9 oak st", source={"doc": "crm/bob"})
    lib.forget_subject("crm/alice", request_id="DSAR-1", basis="gdpr_art17")
    lib.flush()
    return lib


def _chain(tmp_path):
    out = []
    for suffix in (".receipts.json", ".tombstones.json"):
        p = tmp_path / ("store.json" + suffix)
        if p.exists():
            out += json.loads(p.read_text(encoding="utf-8"))
    return out


def _ok(res, what):
    if res.is_error or not isinstance(res.data, dict) or "error" in res.data:
        pytest.fail(f"precondition: {what} failed: {res.text[:300] if res.is_error else res.data}")
    return res.data


# ── the control: nothing about an unsigned server on an unsigned store changes ────────────────────────
def test_an_unsigned_server_on_an_unsigned_store_still_works(monkeypatch, tmp_path):
    """No key anywhere: every write, erasure and ledger entry goes in unsigned, as before, and both
    verifiers stay ok. A fully unsigned chain is the documented default, not a problem."""
    _library_store(tmp_path)
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_ACTIONS="1", INSPEXIMUS_ACTOR="agent")
    assert mod._SIGNING == {"key": None, "pubkey": None, "source": None, "note": None}
    assert call(mod, "where_am_i").data["receipt_signing"]["signed"] is False

    rid = _ok(call(mod, "remember", text="carol lives at 1 pine st", source="crm/carol"), "remember")["id"]
    _ok(call(mod, "forget", ids=[rid], basis="gdpr_art17", request_id="DSAR-2"), "forget")
    _ok(call(mod, "retention", max_age_days=0, pii_only=False, apply=True, basis="policy"), "retention")
    _ok(call(mod, "record_incident", title="wrong refund", severity="serious", actor="bob"), "record_incident")
    _ok(call(mod, "export_subject", subject="crm/bob"), "export_subject")

    vw = call(mod, "verify_writes").data
    assert vw["ok"], vw["problems"]
    chain = _chain(tmp_path)
    assert len(chain) >= 5 and not any("sig" in e for e in chain), "an unsigned store must stay unsigned"
    av = call(mod, "actions_verify").data
    assert av["ok"] and av["entries"] >= 7, av
    ledger = json.loads((tmp_path / "store.json.actions.json").read_text(encoding="utf-8"))
    assert not any("sig" in e for e in ledger), "no key was given, so no ledger entry is signed"


# ── where the key comes from ──────────────────────────────────────────────────────────────────────────
def test_the_key_file_signs_writes_erasures_and_the_certificate(monkeypatch, tmp_path, tmp_path_factory):
    """INSPEXIMUS_RECEIPT_KEY_FILE, the CLI's variable, read the way the CLI reads it. A third party's
    verify_erasure_certificate on an MCP erasure pins the key and passes (E6 found it PARTIALLY SIGNED),
    and an out-of-band declaration is signed too."""
    sk, pk = new_receipt_keypair()
    _library_store(tmp_path, receipt_key=sk)
    kf = tmp_path_factory.mktemp("secrets") / "receipt.key"
    kf.write_text(sk + "\n", encoding="utf-8")
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_RECEIPT_KEY_FILE=str(kf), INSPEXIMUS_RECEIPT_PUBKEY=pk)
    assert call(mod, "where_am_i").data["receipt_signing"] == {
        "signed": True, "pubkey": pk, "key_source": "INSPEXIMUS_RECEIPT_KEY_FILE", "note": None}

    rid = _ok(call(mod, "remember", text="dave lives at 3 ash st", source="crm/dave"), "remember")["id"]
    _ok(call(mod, "forget_subject", subject="crm/dave", request_id="DSAR-3", basis="gdpr_art17"),
        "forget_subject")
    cert = _ok(call(mod, "erasure_certificate", request_id="DSAR-3"), "erasure_certificate")
    third_party = verify_erasure_certificate(cert, expected_pubkey=pk)
    assert third_party["valid"], third_party

    # a record removed behind the store's back, then declared: the declaration is signed as well
    bob = next(r["id"] for r in mod._MEM.items if "bob" in r["text"])
    save_store(tmp_path / "store.json", [r for r in load_store(tmp_path / "store.json") if r["id"] != bob])
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_RECEIPT_KEY_FILE=str(kf), INSPEXIMUS_RECEIPT_PUBKEY=pk)
    _ok(call(mod, "declare_out_of_band_deletion", memory_id=bob, actor="ops", reason="raw delete"), "declare")

    assert all(e.get("sig") and e.get("pubkey") == pk for e in _chain(tmp_path))
    vw = call(mod, "verify_writes").data
    assert vw["ok"], vw["problems"]
    assert rid not in [r["id"] for r in mod._MEM.items]


def test_a_store_keyed_through_receipt_key_for_is_signed_with_no_configuration(monkeypatch, tmp_path,
                                                                                tmp_path_factory):
    """The library's documented recipe, `receipt_key=receipt_key_for(path)`, keeps the key in the key home.
    The server finds it there by the store's path, so the same store needs no key variable at all."""
    home = tmp_path_factory.mktemp("keyhome")
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(home))
    monkeypatch.delenv("INSPEXIMUS_RECEIPT_KEY", raising=False)
    sk = receipt_key_for(str(tmp_path / "store.json"))
    _library_store(tmp_path, receipt_key=sk)
    pk = Inspeximus(path=str(tmp_path / "store.json"), receipts=True, receipt_key=sk).receipt_pubkey

    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_KEY_HOME=str(home), INSPEXIMUS_RECEIPT_PUBKEY=pk)
    assert mod._SIGNING["source"] == "key home" and mod._SIGNING["pubkey"] == pk
    _ok(call(mod, "remember", text="the region is frankfurt"), "remember")
    _ok(call(mod, "retention", max_age_days=0, pii_only=False, apply=True, basis="policy"), "retention")
    assert all(e.get("sig") and e.get("pubkey") == pk for e in _chain(tmp_path))
    vw = call(mod, "verify_writes").data
    assert vw["ok"], vw["problems"]


def test_a_key_home_key_is_not_used_on_an_unsigned_chain(monkeypatch, tmp_path, tmp_path_factory):
    """A key found only in the key home is not an instruction to sign. On a chain that is unsigned,
    signing from here on would leave it signed in places, so the server keeps writing it unsigned and
    where_am_i says why."""
    home = tmp_path_factory.mktemp("keyhome")
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(home))
    monkeypatch.delenv("INSPEXIMUS_RECEIPT_KEY", raising=False)
    _library_store(tmp_path)                                  # unsigned
    receipt_key_for(str(tmp_path / "store.json"))             # and a key for that path appears later

    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_KEY_HOME=str(home))
    signing = call(mod, "where_am_i").data["receipt_signing"]
    assert signing["signed"] is False and "NOT used" in signing["note"], signing
    _ok(call(mod, "remember", text="the region is frankfurt"), "remember")
    assert not any("sig" in e for e in _chain(tmp_path))
    assert call(mod, "verify_writes").data["ok"]


# ── keys the server refuses at startup rather than breaking a chain with ──────────────────────────────
def _refused(monkeypatch, tmp_path, match, **env):
    with pytest.raises(ValueError, match=match) as info:
        load_server(monkeypatch, tmp_path, **env)
    assert type(info.value).__name__ == "ReceiptKeyError"
    return str(info.value)


def test_a_configured_key_on_an_unsigned_chain_is_refused(monkeypatch, tmp_path):
    _library_store(tmp_path)
    sk, _pk = new_receipt_keypair()
    before = _chain(tmp_path)
    _refused(monkeypatch, tmp_path, "signed in places", INSPEXIMUS_RECEIPT_KEY=sk)
    assert _chain(tmp_path) == before, "a refused start must not have written anything"


def test_a_key_that_did_not_sign_the_store_is_refused(monkeypatch, tmp_path):
    sk_store, _ = new_receipt_keypair()
    sk_other, _ = new_receipt_keypair()
    _library_store(tmp_path, receipt_key=sk_store)
    _refused(monkeypatch, tmp_path, "two keys", INSPEXIMUS_RECEIPT_KEY=sk_other)


def test_a_key_the_pin_rejects_is_refused(monkeypatch, tmp_path):
    sk, _pk = new_receipt_keypair()
    _other_sk, other_pk = new_receipt_keypair()
    _refused(monkeypatch, tmp_path, "INSPEXIMUS_RECEIPT_PUBKEY", INSPEXIMUS_RECEIPT_KEY=sk,
             INSPEXIMUS_RECEIPT_PUBKEY=other_pk)


def test_an_unreadable_key_file_is_refused(monkeypatch, tmp_path):
    _refused(monkeypatch, tmp_path, "cannot be read", INSPEXIMUS_RECEIPT_KEY_FILE=str(tmp_path / "missing.key"))


def test_a_configured_key_on_a_new_store_signs_it_from_the_start(monkeypatch, tmp_path):
    sk, pk = new_receipt_keypair()
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_RECEIPT_KEY=sk)
    _ok(call(mod, "remember", text="the region is frankfurt"), "remember")
    chain = _chain(tmp_path)
    assert chain and all(e.get("pubkey") == pk for e in chain)
    vw = call(mod, "verify_writes", expected_pubkey=pk).data
    assert vw["ok"], vw["problems"]


# ── one ledger, one key ───────────────────────────────────────────────────────────────────────────────
def test_a_ledger_signed_with_the_writer_key_keeps_it_when_a_receipt_key_arrives(monkeypatch, tmp_path):
    """The ledger is signed with the receipt key when the server has one, else the writer key. A server
    that could not hold a receipt key before signed its ledger with the writer key; giving it the receipt
    key must not put a second key into that chain."""
    from inspeximus.core import new_source_keypair
    wk, _wpk = new_source_keypair()
    sk, _pk = new_receipt_keypair()
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_ACTIONS="1", INSPEXIMUS_WRITER_KEY=wk)
    _ok(call(mod, "record_incident", title="wrong refund", severity="serious", actor="bob"), "record_incident")
    writer_pub = {e["pubkey"] for e in json.loads((tmp_path / "store.json.actions.json").read_text())}
    assert len(writer_pub) == 1

    # the next server holds a receipt key for a NEW store beside the same ledger file name
    (tmp_path / "store.json.receipts.json").unlink(missing_ok=True)
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_ACTIONS="1", INSPEXIMUS_WRITER_KEY=wk,
                      INSPEXIMUS_RECEIPT_KEY=sk, INSPEXIMUS_RECEIPTS="1")
    _ok(call(mod, "record_risk", risk_id="R1", hazard="stale price", harm="safety", source="intended_use",
             actor="bob"), "record_risk")
    ledger = json.loads((tmp_path / "store.json.actions.json").read_text())
    assert {e["pubkey"] for e in ledger} == writer_pub
    av = call(mod, "actions_verify").data
    assert av["ok"], av["problems"]


# ── an unreadable ledger refuses before anything changes ──────────────────────────────────────────────
def _damage_the_ledger(tmp_path):
    p = tmp_path / "store.json.actions.json"
    raw = p.read_text(encoding="utf-8")
    p.write_text(raw[: len(raw) // 2], encoding="utf-8")
    return p.read_text(encoding="utf-8")


def test_the_boundary_refuses_a_tool_call_before_it_runs(monkeypatch, tmp_path):
    """With INSPEXIMUS_ACTIONS=1 every call is recorded, and a call that cannot be recorded is not made:
    refusing only at the end would leave the write done and unrecorded."""
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_ACTIONS="1", INSPEXIMUS_RECEIPTS="1")
    _ok(call(mod, "remember", text="the region is frankfurt"), "remember")
    damaged = _damage_the_ledger(tmp_path)
    n = len(mod._MEM.items)

    res = call(mod, "remember", text="the region is osaka")
    assert res.is_error and "Refusing to append" in res.text, res
    assert len(mod._MEM.items) == n, "the write ran although its ledger entry was refused"
    assert (tmp_path / "store.json.actions.json").read_text(encoding="utf-8") == damaged
    v = call(mod, "actions_verify")
    assert v.is_error, "with the boundary on, the verifier's own call is refused too"


def test_a_rectification_is_refused_before_the_store_changes(monkeypatch, tmp_path):
    """rectify_subject writes the memory and then the rights entry. With a ledger it cannot write, the
    correction used to land and then fail; it is refused before the write now."""
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_RECEIPTS="1")
    _ok(call(mod, "remember", text="alice's phone is +100", key="alice::phone", object="+100",
             source="crm/alice"), "remember")
    _ok(call(mod, "record_incident", title="wrong refund", severity="serious", actor="bob"), "record_incident")
    damaged = _damage_the_ledger(tmp_path)
    n = len(mod._MEM.items)

    res = call(mod, "rectify_subject", key="alice::phone", text="alice's phone is +200", actor="dpo",
               reason="subject asked")
    assert res.is_error or "error" in (res.data or {}), res
    assert len(mod._MEM.items) == n, "the rectification landed although its ledger entry was refused"
    assert (tmp_path / "store.json.actions.json").read_text(encoding="utf-8") == damaged
    v = call(mod, "actions_verify").data
    assert v["ok"] is False and "cannot read" in v["problems"][0], v
