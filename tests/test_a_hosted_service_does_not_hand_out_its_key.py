"""What a hosted transparency service must not leak, and what it must publish.

A log the audited party runs is not evidence against that party, so the service is worth hosting only
if two things hold. Somebody else can verify its receipts using nothing but what it publishes, and
its signing key is not readable by anyone standing next to it.

Both were broken, and neither raised anything:

  the key set carried the public key hex in `kid` and had no `x` at COSE label -2, so a client
  following RFC 9052 found a plausible 200 response with nothing in it to verify with;

  `--secret` put the SIGNING key in the process table. Measured 2026-08-31 on a running server: a
  second process read it out of `Win32_Process.CommandLine`, and `ps` is no different.
"""
from __future__ import annotations

import os

import pytest

from inspeximus import cose, new_receipt_keypair
from inspeximus.scrapi import _cose_key, _service_secret
from inspeximus.witness_server import _witness_secret

ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")


class _Args:
    """The parsed-arguments shape both secret readers take, with nothing set unless a test sets it."""

    def __init__(self, **kw):
        self.secret = kw.get("secret")
        self.secret_file = kw.get("secret_file")


@pytest.fixture(autouse=True)
def _no_ambient_secret(monkeypatch):
    """A secret left in this process's environment would make every "reads the environment" test pass
    whether or not the code reads it."""
    monkeypatch.delenv("INSPEXIMUS_SERVICE_SECRET", raising=False)
    monkeypatch.delenv("INSPEXIMUS_WITNESS_SECRET", raising=False)


# -- the published key must be able to verify a receipt ---------------------------------------------
def test_the_published_key_set_carries_the_key_and_not_only_its_name():
    secret, pub = new_receipt_keypair()
    key = _cose_key(pub)
    assert key[1] == 1, "kty must be OKP"
    assert key[3] == -8, "alg must be EdDSA"
    assert key[-1] == 6, "crv must be Ed25519"
    assert key[-2] == bytes.fromhex(pub), "x must be the public key itself"
    assert key[2] == pub.encode("ascii"), "kid must still address it the way the URL does"
    assert "problem" not in key


def test_a_stranger_can_verify_a_signature_using_only_the_published_key():
    """The property the endpoint exists for. Signing here, verifying from the key set alone."""
    secret, pub = new_receipt_keypair()
    signer = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret))
    message = b"a head this service committed to"
    signature = signer.sign(message)

    published = cose.decode(cose.encode(_cose_key(pub)))          # through the wire encoding
    verifier = ed25519.Ed25519PublicKey.from_public_bytes(published[-2])
    verifier.verify(signature, message)                            # raises if it cannot

    with pytest.raises(Exception):
        verifier.verify(signature, b"a head it did not commit to")


def test_an_unusable_key_says_so_instead_of_publishing_zeros():
    """A key set carrying a WRONG key is worse than one carrying none: the first makes a verifier
    reject good receipts, and the operator is the last to hear about it."""
    for bad in ("", "not-hex", "aabb"):
        key = _cose_key(bad)
        assert -2 not in key, bad
        assert "problem" in key, bad


# -- the signing key must not arrive on a command line ----------------------------------------------
def test_the_service_reads_its_key_from_the_environment(monkeypatch):
    secret, _ = new_receipt_keypair()
    monkeypatch.setenv("INSPEXIMUS_SERVICE_SECRET", secret)
    assert _service_secret(_Args()) == secret


def test_the_witness_reads_its_key_from_the_environment(monkeypatch):
    secret, _ = new_receipt_keypair()
    monkeypatch.setenv("INSPEXIMUS_WITNESS_SECRET", secret)
    assert _witness_secret(_Args()) == secret


@pytest.mark.parametrize("read", [_service_secret, _witness_secret])
def test_a_file_beats_the_environment(monkeypatch, tmp_path, read):
    """A file is preferred because an environment is inherited by every child process and turns up
    in a crash report."""
    from_file, _ = new_receipt_keypair()
    from_env, _ = new_receipt_keypair()
    path = tmp_path / "key.hex"
    path.write_text(from_file + "\n", encoding="utf-8")
    monkeypatch.setenv("INSPEXIMUS_SERVICE_SECRET", from_env)
    monkeypatch.setenv("INSPEXIMUS_WITNESS_SECRET", from_env)
    assert from_file != from_env
    assert read(_Args(secret_file=str(path))) == from_file


@pytest.mark.parametrize("read", [_service_secret, _witness_secret])
def test_the_flag_still_works_and_says_what_it_costs(capsys, read):
    """Removing the flag silently would break a running deployment. Leaving it silent would keep the
    leak. It works and it warns."""
    secret, _ = new_receipt_keypair()
    assert read(_Args(secret=secret)) == secret
    warning = capsys.readouterr().err
    assert "process table" in warning and "readable by any local user" in warning


@pytest.mark.parametrize("read", [_service_secret, _witness_secret])
def test_no_key_anywhere_is_none_rather_than_an_empty_string(read):
    """An empty string is a value, and `from_private_bytes(b"")` fails somewhere far from here. None
    is what both callers check for before minting a fresh key."""
    assert read(_Args()) is None


@pytest.mark.parametrize("read", [_service_secret, _witness_secret])
def test_a_blank_environment_variable_is_not_a_key(monkeypatch, read):
    monkeypatch.setenv("INSPEXIMUS_SERVICE_SECRET", "   ")
    monkeypatch.setenv("INSPEXIMUS_WITNESS_SECRET", "   ")
    assert read(_Args()) is None


def test_the_deployment_files_keep_the_witness_a_separate_container():
    """A witness inside the service's own container shares its disk, its operator and its fate, so it
    co-signs whatever it is shown. The separation is the guarantee, so it is asserted rather than
    left to a reader of the compose file."""
    root = os.path.join(os.path.dirname(__file__), "..", "deploy")
    compose = open(os.path.join(root, "compose.yaml"), encoding="utf-8").read()
    assert "Dockerfile.witness" in compose
    assert "INSPEXIMUS_SERVICE_SECRET" in compose and "INSPEXIMUS_WITNESS_SECRET" in compose
    for name in ("Dockerfile", "Dockerfile.witness"):
        text = open(os.path.join(root, name), encoding="utf-8").read()
        assert "--secret" not in text, (
            "%s passes a signing key on the command line, which is the leak these files exist to "
            "avoid" % name)
        assert "USER " in text, "%s runs as root" % name
