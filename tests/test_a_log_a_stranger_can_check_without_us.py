"""The static log has to be checkable by someone who does not trust us and does not run our code.

That is the whole claim of publishing it, so the tests here are about the STANDALONE verifier the
publisher writes out, not about the publisher. It ships next to the log under a heading inviting the
reader to distrust us, which makes a wrong verifier worse than none: it would report FAILED on an
honest log, or OK on a rewritten one.

The first draft did the first of those. It hashed leaves as plain SHA-256 and combined them in
pairwise levels, which agrees with RFC 6962 only when the entry count is a power of two. On three
entries it called an honest log corrupt. Both halves of that bug have a test below: a non-power-of-two
count, and a cross-check that the standalone root function agrees with the library's.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from inspeximus import merkle, new_receipt_keypair, scitt
from inspeximus.transparency import RegistrationPolicy, TransparencyService

ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")


def _publisher():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "publish_static_log", os.path.join(TOOLS, "publish_static_log.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _service(path, entries):
    secret, _ = new_receipt_keypair()
    sk = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret))
    isec, _ = new_receipt_keypair()
    isk = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(isec))
    service = TransparencyService(path, RegistrationPolicy("static-test"), sk.sign,
                                  lambda *_: True,
                                  service_pubkey=sk.public_key().public_bytes_raw().hex())
    for i in range(entries):
        service.register(scitt.signed_statement(b"entry %d" % i, issuer="urn:t:i",
                                                subject="s:%d" % i, sign=isk.sign))
    return service


def _site(tmp_path, entries):
    """Publish a log of `entries` statements (plus the policy entry) and return the site directory."""
    out = tmp_path / "site"
    module = _publisher()
    service = _service(str(tmp_path / "log"), entries)
    module.build(service, str(out), "https://example.test/t", "test log", "no witness")
    return out, service


def _run_verifier(site):
    r = subprocess.run([sys.executable, "verify.py"], cwd=str(site), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# -- the positive case, at a size the first draft got wrong ------------------------------------------
@pytest.mark.parametrize("statements", [0, 1, 2, 4, 6])
def test_the_published_verifier_accepts_an_honest_log(tmp_path, statements):
    """Sizes chosen to straddle powers of two. With the policy entry the totals are 1, 2, 3, 5 and 7,
    so three of the five rows exercise the RFC 6962 split that pairwise hashing gets wrong."""
    site, service = _site(tmp_path, statements)
    code, out = _run_verifier(site)
    assert code == 0, out
    assert "OK: %d entries" % service.size() in out, out


def test_the_standalone_root_agrees_with_the_library(tmp_path):
    """A cross-check, because the two implementations exist separately on purpose: the verifier has
    to run with no dependency on us. Separate implementations drift, and this is the test that says
    so out loud rather than waiting for a reader to find it."""
    site, service = _site(tmp_path, 6)
    namespace = {}
    exec(compile(open(site / "verify.py", encoding="utf-8").read(), "verify.py", "exec"), namespace)

    leaves = [service.entry_leaf(i) for i in range(service.size())]
    mine = namespace["root_of"]([merkle.leaf_hash(leaf) for leaf in leaves]).hex()
    theirs = merkle.root(leaves).hex()
    assert mine == theirs == service.head()["writes_tip"]


def test_the_verifier_derives_the_head_hash_the_same_way(tmp_path):
    site, _service = _site(tmp_path, 3)
    namespace = {}
    exec(compile(open(site / "verify.py", encoding="utf-8").read(), "verify.py", "exec"), namespace)
    head = json.loads((site / "head.json").read_text(encoding="utf-8"))
    assert namespace["sth_hash_of"](head) == head["sth_hash"]


# -- the negative cases, each a different lie ---------------------------------------------------------
def _tamper(site, tmp_path, name, mutate):
    copy = tmp_path / ("tampered-" + name)
    shutil.copytree(site, copy)
    mutate(copy)
    return copy


def test_a_rewritten_root_is_caught(tmp_path):
    site, _ = _site(tmp_path, 4)

    def mutate(d):
        head = json.loads((d / "head.json").read_text(encoding="utf-8"))
        head["writes_tip"] = "ff" * 32
        (d / "head.json").write_text(json.dumps(head, indent=2, sort_keys=True), encoding="utf-8")

    code, out = _run_verifier(_tamper(site, tmp_path, "root", mutate))
    assert code == 1 and "not the root of the published leaves" in out, out


def test_a_rewritten_entry_is_caught(tmp_path):
    site, _ = _site(tmp_path, 4)

    def mutate(d):
        rows = [json.loads(x) for x in (d / "log.jsonl").read_text(encoding="utf-8").splitlines() if x]
        rows[1]["leaf_hash"] = "ab" * 32
        (d / "log.jsonl").write_text(
            "\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", encoding="utf-8")

    code, out = _run_verifier(_tamper(site, tmp_path, "leaf", mutate))
    assert code == 1 and "not the root of the published leaves" in out, out


def test_a_dropped_entry_is_caught(tmp_path):
    """Truncating the log is the cheapest rewrite there is, and the count catches it before the root
    does, which is why the count is checked separately and reported separately."""
    site, _ = _site(tmp_path, 4)

    def mutate(d):
        rows = [x for x in (d / "log.jsonl").read_text(encoding="utf-8").splitlines() if x][:-1]
        (d / "log.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    code, out = _run_verifier(_tamper(site, tmp_path, "drop", mutate))
    assert code == 1 and "log.jsonl holds" in out, out


def test_a_rewritten_head_hash_is_caught(tmp_path):
    """The head hash is what a witness signs. An operator who edits the entry count and keeps the old
    signature is exactly the attack `anchor_binds_its_fields` exists for, and the static reader has to
    be able to see it too."""
    site, _ = _site(tmp_path, 4)

    def mutate(d):
        head = json.loads((d / "head.json").read_text(encoding="utf-8"))
        head["n_writes"] = head["n_writes"] + 7
        (d / "head.json").write_text(json.dumps(head, indent=2, sort_keys=True), encoding="utf-8")

    code, out = _run_verifier(_tamper(site, tmp_path, "count", mutate))
    assert code == 1, out
    assert "sth_hash does not follow" in out or "log.jsonl holds" in out, out


# -- what the site must actually contain --------------------------------------------------------------
def test_the_site_carries_everything_the_instructions_ask_for(tmp_path):
    """index.html tells a reader to fetch three files and run one. A page inviting a check that 404s
    is worse than a page that invites nothing."""
    site, service = _site(tmp_path, 3)
    for name in ("head.json", "log.jsonl", "verify.py", "keys.json", "keys.cbor", "index.html"):
        assert (site / name).exists(), name
    assert (site / "entries").is_dir()
    assert len(list((site / "entries").glob("*.cose"))) == service.size()

    page = (site / "index.html").read_text(encoding="utf-8")
    for name in ("head.json", "log.jsonl", "verify.py"):
        assert name in page, name
    # The page must not promise more than the verifier delivers.
    assert "does not prove any entry is TRUE" in page
    assert "witness" in page.lower()


def test_the_key_the_site_publishes_is_the_one_that_signed(tmp_path):
    site, service = _site(tmp_path, 2)
    published = json.loads((site / "keys.json").read_text(encoding="utf-8"))
    assert published["kid"] == service.service_pubkey
    assert published["x_hex"] == service.service_pubkey
    assert published["crv"] == "Ed25519"


# -- the entry TEXTS, which the log itself only commits to by digest ----------------------------------
def _with_payloads(tmp_path, texts):
    """Publish a log plus a payloads.json holding the text of each entry."""
    out = tmp_path / "site"
    module = _publisher()
    secret, _ = new_receipt_keypair()
    sk = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret))
    service = TransparencyService(str(tmp_path / "log"), RegistrationPolicy("payload-test"),
                                  sk.sign, lambda *_: True,
                                  service_pubkey=sk.public_key().public_bytes_raw().hex())
    published = {}
    for i, text in enumerate(texts):
        subject = "s:%d" % i
        service.register(scitt.signed_statement(text.encode("utf-8"), issuer="urn:t:i",
                                                subject=subject, sign=sk.sign))
        published[subject] = text
    module.build(service, str(out), "https://example.test/t", "test log", "no witness")
    (out / "payloads.json").write_text(json.dumps(published, indent=1, sort_keys=True),
                                       encoding="utf-8")
    return out


def test_the_verifier_checks_the_published_texts_against_the_log(tmp_path):
    """Without this the log proves WHEN something was recorded and never WHAT it said, which is the
    half a reader came for."""
    site = _with_payloads(tmp_path, ["first claim", "second claim", "third claim"])
    code, out = _run_verifier(site)
    assert code == 0, out
    assert "3 published entry texts hash to what the log recorded" in out, out


def test_an_edited_text_is_caught_and_named(tmp_path):
    """The control. Editing a published claim while keeping the log is the exact move this exists to
    make visible, and the message has to say WHICH claim so a reader can go and look."""
    site = _with_payloads(tmp_path, ["first claim", "second claim"])
    payloads = json.loads((site / "payloads.json").read_text(encoding="utf-8"))
    payloads["s:1"] = "second claim, quietly reworded"
    (site / "payloads.json").write_text(json.dumps(payloads, indent=1, sort_keys=True),
                                        encoding="utf-8")
    code, out = _run_verifier(site)
    assert code == 1, out
    assert "s:1" in out and "does not hash to what the log recorded" in out, out


def test_a_missing_text_is_reported_rather_than_skipped(tmp_path):
    """An absent entry must not read as a passing one. A verifier that only checks what it was handed
    reports OK on a file with everything inconvenient removed."""
    site = _with_payloads(tmp_path, ["first claim", "second claim"])
    payloads = json.loads((site / "payloads.json").read_text(encoding="utf-8"))
    del payloads["s:0"]
    (site / "payloads.json").write_text(json.dumps(payloads, indent=1, sort_keys=True),
                                        encoding="utf-8")
    code, out = _run_verifier(site)
    assert code == 1 and "has nothing for s:0" in out, out


def test_without_payloads_the_verifier_says_what_it_did_not_check(tmp_path):
    """Publishing without the texts is allowed and must not look like a full check."""
    site, _ = _site(tmp_path, 2)
    assert not (site / "payloads.json").exists()
    code, out = _run_verifier(site)
    assert code == 0, out
    assert "entry texts hash" not in out, "a run with no payloads must not claim it checked them"


def test_the_log_entries_carry_the_subject_and_not_only_a_hash(tmp_path):
    """log.jsonl publishes the entry record, so a reader sees issuer and subject. Publishing only
    leaf hashes would let them confirm the tree and learn nothing about what is in it."""
    site, service = _site(tmp_path, 3)
    rows = [json.loads(x) for x in (site / "log.jsonl").read_text(encoding="utf-8").splitlines() if x]
    assert len(rows) == service.size()
    subjects = [r.get("entry", {}).get("subject") for r in rows if r.get("entry")]
    assert "s:0" in subjects and "s:2" in subjects, subjects
    for r in rows:
        assert "leaf_hash" in r
