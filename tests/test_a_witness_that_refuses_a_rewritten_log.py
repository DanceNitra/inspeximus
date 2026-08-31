"""A witness earns its keep by REFUSING, so every test here is about the refusals.

The witness watches a static log it does not operate. It remembers the head it last accepted and, on
the next visit, rebuilds that head's root from the leaves the log publishes now. A publisher who
rewrote an entry you had already seen cannot produce the old root from the new leaves, and that is
the whole mechanism.

The subtle one is `test_a_refusal_does_not_overwrite_what_the_witness_remembers`. A witness that
updates its memory while refusing forgets the thing it just caught, and its very next run reports
EXTENDS on the rewritten log. That turns the one instrument that can catch equivocation into an
instrument that launders it.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from inspeximus import new_receipt_keypair, scitt
from inspeximus.transparency import RegistrationPolicy, TransparencyService

ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")


def _module(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, os.path.join(TOOLS, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def witness():
    return _module("witness_static_log")


@pytest.fixture(scope="module")
def publisher():
    return _module("publish_static_log")


def _publish(publisher, tmp_path, name, statements):
    """A published static log with `statements` entries, plus the policy entry."""
    secret, _ = new_receipt_keypair()
    sk = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret))
    service = TransparencyService(str(tmp_path / (name + ".log")), RegistrationPolicy("w-test"),
                                  sk.sign, lambda *_: True,
                                  service_pubkey=sk.public_key().public_bytes_raw().hex())
    for i in range(statements):
        service.register(scitt.signed_statement(b"entry %d" % i, issuer="urn:t:i",
                                                subject="s:%d" % i, sign=sk.sign))
    site = tmp_path / name
    publisher.build(service, str(site), "file:///x", "t", "no witness")
    return site, service


def _read(witness, site):
    """What the witness would fetch, read off disk rather than over HTTP.

    The network is not what these tests are about, and a local HTTP server would add a failure mode
    that has nothing to do with the judgement being checked.
    """
    head = json.loads((site / "head.json").read_text(encoding="utf-8"))
    mtl = [bytes.fromhex(json.loads(line)["leaf_hash"])
           for line in (site / "log.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return head, mtl


def _remember(head, mtl):
    return {"n_writes": len(mtl), "writes_tip": head["writes_tip"], "sth_hash": head["sth_hash"]}


# -- the accepting cases ------------------------------------------------------------------------------
def test_a_log_never_seen_before_is_a_baseline_and_says_so(witness, publisher, tmp_path):
    site, _ = _publish(publisher, tmp_path, "a", 3)
    verdict, why = witness.judge(*_read(witness, site), remembered=None)
    assert verdict == "FIRST_CONTACT"
    assert "proves nothing yet" in why


def test_an_appended_log_extends(witness, publisher, tmp_path):
    site, service = _publish(publisher, tmp_path, "b", 3)
    head, mtl = _read(witness, site)
    seen = _remember(head, mtl)

    secret, _ = new_receipt_keypair()
    sk = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret))
    service.register(scitt.signed_statement(b"later", issuer="urn:t:i", subject="s:later",
                                            sign=sk.sign))
    publisher.build(service, str(site), "file:///x", "t", "no witness")

    verdict, why = witness.judge(*_read(witness, site), remembered=seen)
    assert verdict == "EXTENDS", why
    assert "unchanged" in why


# -- the refusals -------------------------------------------------------------------------------------
def test_a_rewritten_early_entry_is_a_fork(witness, publisher, tmp_path):
    """The case the witness exists for. The log keeps its length and changes something old."""
    site, _ = _publish(publisher, tmp_path, "c", 4)
    head, mtl = _read(witness, site)
    seen = _remember(head, mtl)

    forked = list(mtl)
    forked[1] = bytes.fromhex("ab" * 32)
    forged_head = dict(head, writes_tip=witness._root(forked).hex())
    forged_head["sth_hash"] = witness.sth_hash_of(forged_head)

    verdict, why = witness.judge(forged_head, forked, remembered=seen)
    assert verdict == "FORK", why
    assert "no longer produce the root signed before" in why


def test_a_shorter_log_is_a_rollback(witness, publisher, tmp_path):
    site, _ = _publish(publisher, tmp_path, "d", 5)
    head, mtl = _read(witness, site)
    seen = _remember(head, mtl)

    short = mtl[:3]
    shorter_head = dict(head, n_writes=len(short), writes_tip=witness._root(short).hex())
    shorter_head["sth_hash"] = witness.sth_hash_of(shorter_head)

    verdict, why = witness.judge(shorter_head, short, remembered=seen)
    assert verdict == "ROLLBACK", why
    assert "now publishes 3" in why


@pytest.mark.parametrize("break_it, expected", [
    ("count", "the head claims"),
    ("root", "not the root of the published leaves"),
    ("sth", "sth_hash does not follow"),
])
def test_a_log_that_contradicts_itself_is_malformed(witness, publisher, tmp_path, break_it,
                                                    expected):
    """A witness must not sign a document that fails on its own terms, whether or not it extends."""
    site, _ = _publish(publisher, tmp_path, "e", 3)
    head, mtl = _read(witness, site)
    if break_it == "count":
        head = dict(head, n_writes=head["n_writes"] + 1)
    elif break_it == "root":
        head = dict(head, writes_tip="cd" * 32)
    else:
        head = dict(head, sth_hash="ef" * 32)

    verdict, why = witness.judge(head, mtl, remembered=None)
    assert verdict == "MALFORMED", (verdict, why)
    assert expected in why


def test_the_root_is_recomputed_and_never_taken_from_the_head(witness, publisher, tmp_path):
    """Signing the publisher's own number would make this a rubber stamp.

    The head handed in below is INTERNALLY CONSISTENT: its sth_hash is recomputed to match its
    invented root, so every check that reads only the head passes. It is caught solely because the
    witness derives the root from the leaves. A version that trusted `writes_tip` would sign it.
    """
    site, service = _publish(publisher, tmp_path, "f", 4)
    head, mtl = _read(witness, site)
    assert witness._root(mtl).hex() == head["writes_tip"] == service.head()["writes_tip"]

    invented = dict(head, writes_tip="de" * 32)
    invented["sth_hash"] = witness.sth_hash_of(invented)
    verdict, why = witness.judge(invented, mtl, remembered=None)
    assert verdict == "MALFORMED", (verdict, why)
    assert "not the root of the published leaves" in why


# -- the memory, which is the whole guarantee ---------------------------------------------------------
def test_a_refusal_does_not_overwrite_what_the_witness_remembers(witness, publisher, tmp_path,
                                                                 monkeypatch, capsys):
    """The failure that would turn this tool into a laundering machine.

    A witness that saves state while refusing forgets the head it was comparing against, so the very
    next run compares the rewritten log to itself and reports EXTENDS.
    """
    site, _ = _publish(publisher, tmp_path, "g", 4)
    head, mtl = _read(witness, site)
    state_path = tmp_path / "state.json"
    url = "file:///" + str(site).replace(os.sep, "/").lstrip("/")
    witness.save_state(str(state_path), {url: _remember(head, mtl)})
    before = json.loads(state_path.read_text(encoding="utf-8"))

    forked = list(mtl)
    forked[0] = bytes.fromhex("cc" * 32)
    forged = dict(head, writes_tip=witness._root(forked).hex())
    forged["sth_hash"] = witness.sth_hash_of(forged)
    monkeypatch.setattr(witness, "read_log", lambda *a, **k: (forged, forked))

    code = witness.main(["--url", url, "--state", str(state_path)])
    assert code == 2, "a fork must exit non-zero"
    assert "REFUSING" in capsys.readouterr().out
    assert json.loads(state_path.read_text(encoding="utf-8")) == before, \
        "the witness overwrote the head it had caught the log rewriting"


def test_an_accepted_run_does_record_what_it_saw(witness, publisher, tmp_path, monkeypatch):
    """The other direction: a witness that never records is a witness that can never refuse."""
    site, _ = _publish(publisher, tmp_path, "h", 3)
    head, mtl = _read(witness, site)
    state_path = tmp_path / "state2.json"
    url = "file:///t/h"
    monkeypatch.setattr(witness, "read_log", lambda *a, **k: (head, mtl))

    assert witness.main(["--url", url, "--state", str(state_path)]) == 0
    saved = json.loads(state_path.read_text(encoding="utf-8"))[url]
    assert saved["n_writes"] == len(mtl)
    assert saved["writes_tip"] == head["writes_tip"]


def test_the_cosignature_verifies_with_the_key_it_names(witness, publisher, tmp_path, monkeypatch):
    site, _ = _publish(publisher, tmp_path, "i", 3)
    head, mtl = _read(witness, site)
    secret, _ = new_receipt_keypair()
    key_file = tmp_path / "wkey"
    key_file.write_text(secret, encoding="utf-8")
    monkeypatch.setattr(witness, "read_log", lambda *a, **k: (head, mtl))

    out = tmp_path / "cosig"
    assert witness.main(["--url", "file:///t/i", "--state", str(tmp_path / "s3.json"),
                         "--out", str(out), "--key-file", str(key_file), "--name", "tester"]) == 0
    files = list(out.glob("*.json"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8"))

    pub = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(record["witness_pubkey"]))
    pub.verify(bytes.fromhex(record["signature"]), record["sth_hash"].encode("ascii"))
    with pytest.raises(Exception):
        pub.verify(bytes.fromhex(record["signature"]), b"a head it never saw")
    assert record["witness_name"] == "tester"
    assert "does NOT say any entry is true" in record["scope"]


def test_without_a_key_it_remembers_but_signs_nothing(witness, publisher, tmp_path, monkeypatch,
                                                      capsys):
    """Watching without a key is useful and must not look like attestation."""
    site, _ = _publish(publisher, tmp_path, "j", 2)
    head, mtl = _read(witness, site)
    monkeypatch.delenv("INSPEXIMUS_WITNESS_SECRET", raising=False)
    monkeypatch.setattr(witness, "read_log", lambda *a, **k: (head, mtl))
    out = tmp_path / "cosig2"

    assert witness.main(["--url", "file:///t/j", "--state", str(tmp_path / "s4.json"),
                         "--out", str(out)]) == 0
    assert "NOT signed" in capsys.readouterr().out
    assert not out.exists() or not list(out.glob("*.json"))


# -- what the PUBLISHER does with a co-signature it is handed -----------------------------------------
def _cosign(tmp_path, head, name="w"):
    secret, _ = new_receipt_keypair()
    sk = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret))
    pub = sk.public_key().public_bytes_raw().hex()
    return {"kind": "static-log-cosignature", "log_url": "file:///t", "verdict": "EXTENDS",
            "n_writes": head["n_writes"], "writes_tip": head["writes_tip"],
            "sth_hash": head["sth_hash"], "witness_pubkey": pub, "witness_name": name,
            "signature": sk.sign(head["sth_hash"].encode("ascii")).hex(),
            "observed_utc": "2026-08-31T00:00:00Z", "scope": "test"}


def test_a_valid_cosignature_is_shown_and_marked_current(publisher, tmp_path):
    site, service = _publish(publisher, tmp_path, "k", 3)
    head = service.head()
    cosdir = tmp_path / "cos"
    cosdir.mkdir()
    (cosdir / "a.json").write_text(json.dumps(_cosign(tmp_path, head, "alice")), encoding="utf-8")

    got = publisher.build(service, str(site), "file:///x", "t", "none", str(cosdir))
    assert len(got["cosignatures"]) == 1
    record = got["cosignatures"][0]
    assert record["valid"] and record["current"] and record["witness_name"] == "alice"
    page = (site / "index.html").read_text(encoding="utf-8")
    assert "alice" in page and "current head" in page
    assert (site / "cosignatures" / "a.json").exists()
    assert (site / "cosignatures" / "index.json").exists()


def test_a_forged_cosignature_is_rejected_rather_than_displayed(publisher, tmp_path):
    """An unchecked co-signature is a claim that somebody vouched for us, made by us. Anyone can
    write that file; only the key holder can make it verify."""
    site, service = _publish(publisher, tmp_path, "l", 3)
    head = service.head()
    forged = _cosign(tmp_path, head, "mallory")
    forged["signature"] = ("00" * 64)
    cosdir = tmp_path / "cos2"
    cosdir.mkdir()
    (cosdir / "bad.json").write_text(json.dumps(forged), encoding="utf-8")

    got = publisher.build(service, str(site), "file:///x", "t", "none", str(cosdir))
    assert got["cosignatures"][0]["valid"] is False
    page = (site / "index.html").read_text(encoding="utf-8")
    assert "mallory" not in page, "a forged co-signature must not be shown as a witness"
    assert "signature did not verify" in page


def test_a_cosignature_of_an_older_head_is_labelled_as_such(publisher, tmp_path):
    """Still real evidence, and presenting it as current would not be."""
    site, service = _publish(publisher, tmp_path, "m", 3)
    old_head = service.head()
    cosdir = tmp_path / "cos3"
    cosdir.mkdir()
    (cosdir / "a.json").write_text(json.dumps(_cosign(tmp_path, old_head, "bob")), encoding="utf-8")

    secret, _ = new_receipt_keypair()
    sk = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret))
    service.register(scitt.signed_statement(b"newer", issuer="urn:t:i", subject="s:newer",
                                            sign=sk.sign))
    got = publisher.build(service, str(site), "file:///x", "t", "none", str(cosdir))
    assert got["cosignatures"][0]["valid"] is True
    assert got["cosignatures"][0]["current"] is False
    assert "an earlier head" in (site / "index.html").read_text(encoding="utf-8")


def test_with_no_cosignatures_the_page_says_so_in_words(publisher, tmp_path):
    """A blank space reads as "fine" to a scanning reader. The absence has to be stated."""
    site, service = _publish(publisher, tmp_path, "n", 2)
    got = publisher.build(service, str(site), "file:///x", "t",
                          "No witness has co-signed this log yet.", None)
    assert got["cosignatures"] == []
    assert "No witness has co-signed this log yet." in (site / "index.html").read_text(
        encoding="utf-8")
