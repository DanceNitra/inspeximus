"""A store whose receipts are signed by a KMS, and the checks that make that claim worth anything.

The sentence this buys on a first review is "the key that signs the records is not on the disk that
holds them". Two things have to be true for it, and both are tested here:

1. a store signed through the KMS verifies against the public key ALONE, with the key PINNED. The
   pinning is not a detail: an unpinned check reads the key out of the receipt it is checking, so a
   rewriter who swaps both passes. `verify_writes(warn_unpinned=True)` says so in words, and the
   test holds that wording down.
2. nothing that looks like a private key is lying in the store's directory, proved by a scanner that
   is itself proved able to see one.

The Vault tests run against a REAL transit engine when `VAULT_ADDR` and `VAULT_TOKEN` are set, and
skip otherwise. They are not written against a mock on purpose: a mocked KMS tests the mock. The
parsing tests below use a local stub, because those check OUR handling of malformed answers rather
than anybody's cryptography.
"""
from __future__ import annotations

import http.server
import json
import os
import socketserver
import threading

import pytest

from inspeximus import Inspeximus
from inspeximus.core import new_receipt_keypair
from inspeximus.signers import FileSigner, VaultTransitSigner

pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")

VAULT_ADDR = os.environ.get("VAULT_ADDR")
VAULT_TOKEN = os.environ.get("VAULT_TOKEN")
VAULT_KEY = os.environ.get("VAULT_TRANSIT_KEY", "inspeximus-receipts")
needs_vault = pytest.mark.skipif(not (VAULT_ADDR and VAULT_TOKEN),
                                 reason="set VAULT_ADDR and VAULT_TOKEN to run against a real transit engine")


# -- the file signer, which is what the KMS path replaces ----------------------------------------
def test_a_store_signed_by_the_file_signer_verifies_against_the_pinned_key(tmp_path):
    sk, pub = new_receipt_keypair()
    signer = FileSigner(sk)
    assert signer.public_key_hex == pub and signer.exportable is True
    path = str(tmp_path / "mem.json")
    m = Inspeximus(path, receipts=True, receipt_signer=signer, receipt_pubkey=pub)
    m.remember("a decision", key="k")
    m.flush()
    assert Inspeximus(path, receipts=True).verify_writes(expected_pubkey=pub) == (True, [])


def test_an_unpinned_check_is_reported_rather_than_passed(tmp_path):
    """The check that makes 'verifies with the public key' mean something."""
    sk, pub = new_receipt_keypair()
    path = str(tmp_path / "mem.json")
    m = Inspeximus(path, receipts=True, receipt_signer=FileSigner(sk), receipt_pubkey=pub)
    m.remember("a decision", key="k")
    m.flush()
    ok, problems = Inspeximus(path, receipts=True).verify_writes(warn_unpinned=True)
    assert ok is False
    assert any("not pinned" in p for p in problems)


def test_another_key_is_refused(tmp_path):
    """The control. A verifier pinned to a stranger's key must not accept our receipts."""
    sk, pub = new_receipt_keypair()
    _other_sk, other_pub = new_receipt_keypair()
    path = str(tmp_path / "mem.json")
    m = Inspeximus(path, receipts=True, receipt_signer=FileSigner(sk), receipt_pubkey=pub)
    m.remember("a decision", key="k")
    m.flush()
    ok, problems = Inspeximus(path, receipts=True).verify_writes(expected_pubkey=other_pub)
    assert ok is False and any("unexpected key" in p for p in problems)


def test_holding_both_a_key_and_a_signer_is_refused(tmp_path):
    """Passing both defeats the boundary the signer exists to create, so it is an error."""
    sk, _pub = new_receipt_keypair()
    with pytest.raises(ValueError):
        Inspeximus(str(tmp_path / "m.json"), receipts=True, receipt_key=sk,
                   receipt_signer=FileSigner(sk))


# -- the KMS, against a real one ------------------------------------------------------------------
@needs_vault
def test_a_store_signed_through_vault_verifies_with_the_public_key_alone(tmp_path):
    signer = VaultTransitSigner(VAULT_ADDR, VAULT_TOKEN, VAULT_KEY)
    path = str(tmp_path / "mem.json")
    m = Inspeximus(path, receipts=True, receipt_signer=signer, receipt_pubkey=signer.public_key_hex)
    for i in range(3):
        m.remember("decision %d" % i, key="k%d" % i)
    m.flush()

    fresh = Inspeximus(path, receipts=True)                      # no signer, no secret, nothing
    assert fresh.verify_writes(expected_pubkey=signer.public_key_hex) == (True, [])
    _sk, other = new_receipt_keypair()
    ok, problems = fresh.verify_writes(expected_pubkey=other)
    assert ok is False and any("unexpected key" in p for p in problems)


@needs_vault
def test_the_signer_describes_where_the_key_lives_without_revealing_it():
    signer = VaultTransitSigner(VAULT_ADDR, VAULT_TOKEN, VAULT_KEY)
    described = signer.describe()
    assert described["private_key_in_process"] is False and described["exportable"] is False
    assert described["public_key"] == signer.public_key_hex
    assert VAULT_TOKEN not in json.dumps(described)


# -- our handling of a KMS that answers badly ----------------------------------------------------
def _stub(routes):
    """A tiny HTTP server returning canned JSON, to test OUR parsing rather than anyone's crypto."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def _reply(self):
            # Read the request body before answering. Without this the client's POST is reset
            # mid-write and the test fails on a connection error rather than on what it is about.
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length:
                self.rfile.read(length)
            body = json.dumps(routes.get(self.path.split("?")[0], {"data": {}})).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = lambda self: self._reply()            # noqa: E731,N815
        def log_message(self, *a):                               # noqa: D401 - quiet
            pass

    class Threaded(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    httpd = Threaded(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % httpd.server_address[1], httpd.shutdown


def test_a_key_that_is_not_ed25519_is_refused_at_construction():
    import base64
    addr, stop = _stub({"/v1/transit/keys/k": {"data": {"keys": {"1": {"public_key":
                        base64.b64encode(b"x" * 270).decode()}}}}})
    try:
        with pytest.raises(RuntimeError) as e:
            VaultTransitSigner(addr, "t", "k")
        assert "not Ed25519" in str(e.value)
    finally:
        stop()


def test_a_signature_from_another_key_version_is_refused():
    """A rotated key signs with a new version, and a verifier holding the old public key would read
    every later receipt as forged. Better to fail where the operator can see why."""
    import base64
    pub = base64.b64encode(bytes.fromhex(new_receipt_keypair()[1])).decode()
    addr, stop = _stub({
        "/v1/transit/keys/k": {"data": {"keys": {"1": {"public_key": pub}}}},
        "/v1/transit/sign/k": {"data": {"signature": "vault:v2:" + base64.b64encode(b"z" * 64).decode()}},
    })
    try:
        signer = VaultTransitSigner(addr, "t", "k")
        with pytest.raises(RuntimeError) as e:
            signer("aa" * 32)
        assert "key version" in str(e.value)
    finally:
        stop()


def test_a_missing_signature_raises_rather_than_returning_nothing():
    """A signer that quietly returns None leaves every receipt unsigned while the store reports
    success, which is the exact failure this module exists to prevent."""
    import base64
    pub = base64.b64encode(bytes.fromhex(new_receipt_keypair()[1])).decode()
    addr, stop = _stub({"/v1/transit/keys/k": {"data": {"keys": {"1": {"public_key": pub}}}},
                        "/v1/transit/sign/k": {"data": {}}})
    try:
        signer = VaultTransitSigner(addr, "t", "k")
        with pytest.raises(RuntimeError):
            signer("bb" * 32)
    finally:
        stop()


# -- the scanner that backs the claim about the disk ----------------------------------------------
def test_the_key_scanner_finds_a_planted_key_and_clears_a_clean_tree(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "keyscan", os.path.join(os.path.dirname(__file__), "..", "probes",
                                "no_private_key_bytes_beside_the_store.py"))
    keyscan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(keyscan)

    sk, pub = new_receipt_keypair()
    store = tmp_path / "store"
    store.mkdir()
    m = Inspeximus(str(store / "mem.json"), receipts=True, receipt_signer=FileSigner(sk),
                   receipt_pubkey=pub)
    m.remember("a decision", key="k")
    m.flush()
    publics = keyscan.public_keys_in(str(store))
    assert pub in publics, "the tree must publish the public key for the decisive test to run"
    findings, read = keyscan.scan_tree(str(store), hex_anywhere=False, publics=publics)
    assert read > 0 and findings == []

    (store / "receipt.key").write_text(sk + "\n", encoding="utf-8")
    findings, _read = keyscan.scan_tree(str(store), hex_anywhere=False, publics=publics)
    assert any("private half" in f["why"] or "key-shaped" in f["why"] for f in findings), findings


def test_the_scanner_does_not_cry_wolf_over_hashes(tmp_path):
    """The first version flagged every 64-hex token, which on our own store meant five false hits."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "keyscan", os.path.join(os.path.dirname(__file__), "..", "probes",
                                "no_private_key_bytes_beside_the_store.py"))
    keyscan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(keyscan)

    (tmp_path / "hashes.json").write_text(json.dumps({"hash": "ab" * 32, "prev": "cd" * 32}),
                                          encoding="utf-8")
    findings, read = keyscan.scan_tree(str(tmp_path), hex_anywhere=False, publics=set())
    assert read == 1 and findings == []
