"""Submit a document, then prove it is in the log without asking the server anything.

THE DEFECT THIS EXISTS FOR. A seal that prints "transparency log root X" beside a document is
misleading unless X commits to that document. The root a log publishes today covers the entries it
already holds; a document stamped afterwards is not in it. The number is true and the sentence
beside it is not, which is the class we keep finding: a check that reports something other than what
its label says.

So the path here is submit, publish, and then verify INCLUSION locally, and the last step is the
one that matters. A submission that ends at "the server said 201" has proved nothing a reader can
repeat.

The two host-shaped steps are replaced: the SSH hop becomes a local HTTP call to a service this test
starts, and the publisher runs in-process. Everything between them is the shipped code.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time

import pytest

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
sys.path.insert(0, TOOLS)

from inspeximus import merkle, scrapi, scitt                           # noqa: E402
from inspeximus.core import new_receipt_keypair                        # noqa: E402
from inspeximus.transparency import RegistrationPolicy, TransparencyService  # noqa: E402

import publish_static_log as publisher                                 # noqa: E402
import submit_to_log                                                   # noqa: E402

pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")

#: NO FIXED PORT. The first version hardcoded 9878, and under twelve xdist workers two of them bound
#: it at once: a test on one worker submitted its statement into ANOTHER worker's transparency
#: service, read back an index from that service, and then looked for the proof in its own site
#: directory. It failed with `assert 6 == 5` and a missing `5.proof.json`, which reads like a defect
#: in the submission code and is not one. Port 0 lets the OS pick, and the test reads back what it
#: got, so two of these can never reach each other's log.


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A running service with three entries, a site directory, and the two host steps redirected."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk_hex, pub = new_receipt_keypair()
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(sk_hex))
    service = TransparencyService(str(tmp_path / "reg.log"), RegistrationPolicy("t"),
                                  sk.sign, lambda *_: True, service_pubkey=pub)
    for i in range(3):
        service.register(scitt.signed_statement(b"seed %d" % i, "urn:seed", "s:%d" % i, sk.sign))

    server = scrapi.make_server(service, "127.0.0.1", 0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.3)

    site = str(tmp_path / "site")
    published = {"count": 0}

    def fake_ssh(host, command, stdin=None, identity=None):
        import urllib.error
        import urllib.request
        if "publish" in command:
            publisher.build(service, site, "file:///x", "t", "no witness")
            published["count"] += 1
            return subprocess.CompletedProcess([], 0, b"", b"")
        if command.startswith("cat " + submit_to_log.SITE + "/"):
            # The read-back goes over SSH now, because the host serves nothing to the internet.
            name = command[len("cat " + submit_to_log.SITE + "/"):]
            try:
                return subprocess.CompletedProcess([], 0, open(os.path.join(site, name), "rb").read(), b"")
            except FileNotFoundError:
                return subprocess.CompletedProcess([], 1, b"", b"No such file")
        req = urllib.request.Request("http://127.0.0.1:%d/entries" % port, data=stdin,
                                     headers={"Content-Type": "application/cose"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                head = "HTTP/1.1 %d OK\r\n" % r.status + "".join(
                    "%s: %s\r\n" % (k, v) for k, v in r.headers.items())
                return subprocess.CompletedProcess([], 0, head.encode(), b"")
        except urllib.error.HTTPError as e:
            return subprocess.CompletedProcess([], 0, ("HTTP/1.1 %d\r\n\r\n" % e.code).encode(), b"")

    monkeypatch.setattr(submit_to_log, "_ssh", fake_ssh)
    # Nothing may be fetched over HTTP any more; a call here means the read-back regressed.
    monkeypatch.setattr(submit_to_log, "_get",
                        lambda url: pytest.fail("submit_to_log fetched %s over HTTP" % url))
    try:
        yield {"site": site, "service": service, "tmp": tmp_path, "published": published}
    finally:
        server.shutdown()


def _document(tmp_path, text=b'{"kind": "recompute-receipt", "claims": 3}'):
    path = str(tmp_path / "receipt.json")
    with open(path, "wb") as fh:
        fh.write(text)
    return path


def _key(tmp_path):
    path = str(tmp_path / "submitter.secret")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_receipt_keypair()[0])
    return path


def test_a_submitted_document_is_in_the_published_root(world):
    tmp = world["tmp"]
    out = submit_to_log.submit(_document(tmp), "urn:agora:builder", "urn:agora:seal:test",
                               _key(tmp), base="file:///log", host="local")
    # Entry 0 is the registration policy the service writes for itself, so three seeds end at 3.
    assert out["index"] == 4, "the new entry follows the policy entry and three seeds"
    assert out["tree_size"] == 5
    assert out["inclusion_verified"] is True
    assert world["published"]["count"] == 1, "the publisher must run, or no reader can see it"


def test_the_log_carries_the_digest_and_not_the_document(world):
    """The log is public. A hash commits to the bytes without publishing them."""
    tmp = world["tmp"]
    secret = b'{"kind": "receipt", "customer": "a name that must not be published"}'
    out = submit_to_log.submit(_document(tmp, secret), "urn:agora:builder", "urn:s", _key(tmp),
                               base="file:///log", host="local")
    published = open(os.path.join(world["site"], "log.jsonl"), "rb").read()
    assert b"must not be published" not in published
    assert out["document_sha256"].encode() in out["payload"].encode()
    assert out["payload_sha256"].encode() in published
    assert out["payload_matches_published_leaf"] is True


def test_CONTROL_the_proof_fails_on_a_tampered_leaf(world):
    """Without this, `inclusion_verified` could be arithmetic that accepts anything."""
    tmp = world["tmp"]
    out = submit_to_log.submit(_document(tmp), "urn:agora:builder", "urn:s", _key(tmp),
                               base="file:///log", host="local")
    p = json.load(open(os.path.join(world["site"], "entries", "%d.proof.json" % out["index"]),
                       encoding="utf-8"))
    leaf = bytearray(open(os.path.join(world["site"], p["leaf"]), "rb").read())
    leaf[10] ^= 0x01
    assert not merkle.verify_inclusion(bytes(leaf), p["index"], p["tree_size"],
                                       [bytes.fromhex(h) for h in p["audit_path"]],
                                       bytes.fromhex(out["root"]))


def test_CONTROL_an_earlier_root_does_not_contain_a_later_document(world):
    """The defect in one line. The root before a submission cannot commit to it, and the proof
    against that root must fail rather than quietly pass."""
    tmp = world["tmp"]
    publisher.build(world["service"], world["site"], "file:///x", "t", "no witness")
    before = json.load(open(os.path.join(world["site"], "head.json"), encoding="utf-8"))["writes_tip"]

    out = submit_to_log.submit(_document(tmp), "urn:agora:builder", "urn:s", _key(tmp),
                               base="file:///log", host="local")
    assert out["root"] != before, "the root must move when an entry is added"

    p = json.load(open(os.path.join(world["site"], "entries", "%d.proof.json" % out["index"]),
                       encoding="utf-8"))
    leaf = open(os.path.join(world["site"], p["leaf"]), "rb").read()
    assert not merkle.verify_inclusion(leaf, p["index"], p["tree_size"],
                                       [bytes.fromhex(h) for h in p["audit_path"]],
                                       bytes.fromhex(before))


def test_a_refused_statement_stops_rather_than_reporting_an_index(world, monkeypatch):
    """A submitter that reads an index out of a refusal would stamp a document on an entry that does
    not exist."""
    tmp = world["tmp"]
    real = submit_to_log._ssh

    def refuse(host, command, stdin=None, identity=None):
        if "publish" in command:
            return real(host, command, stdin, identity)
        return subprocess.CompletedProcess([], 0, b"HTTP/1.1 400 Bad Request\r\n\r\n", b"")

    monkeypatch.setattr(submit_to_log, "_ssh", refuse)
    with pytest.raises(SystemExit) as e:
        submit_to_log.submit(_document(tmp), "urn:a", "urn:s", _key(tmp),
                             base="file:///log", host="local")
    assert "refused" in str(e.value)


def test_the_submission_record_says_what_it_does_not_prove(world):
    tmp = world["tmp"]
    out = submit_to_log.submit(_document(tmp), "urn:agora:builder", "urn:s", _key(tmp),
                               base="file:///log", host="local")
    assert "does not say the document is true" in out["scope"]
    assert "history shown to somebody else" in out["scope"]
