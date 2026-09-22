"""The two published copies of the verification key, and every way they can drift apart.

A check that only ever sees the agreeing case has measured nothing, so every test here breaks one
copy and requires the check to say so. The offline half is the one that runs on every push: it needs
no network, and it catches a README edited by hand, because the four-byte key id must recompute from
the key sitting beside it in the same line.
"""
from __future__ import annotations

import base64
import os
import sys

import pytest

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
sys.path.insert(0, TOOLS)

import check_published_key as chk                                      # noqa: E402

from inspeximus import checkpoint as cp                                # noqa: E402
from inspeximus.core import new_receipt_keypair                        # noqa: E402

pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")

NAME = "example.test/log"


def _line(name: str, pub_hex: str) -> str:
    return cp.vkey(name, pub_hex)


def _readme(tmp_path, line: str, name="README.md") -> str:
    path = str(tmp_path / name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# a readme\n\nprose\n\n%s\n```\n%s\n```\n%s\n\nmore prose\n"
                 % (chk.BEGIN, line, chk.END))
    return path


@pytest.fixture()
def keypair():
    sk_hex, pub_hex = new_receipt_keypair()
    return sk_hex, pub_hex


def test_the_published_line_is_read_from_between_the_markers(tmp_path, keypair):
    line = _line(NAME, keypair[1])
    assert chk.vkey_from_readme(_readme(tmp_path, line)) == line


def test_a_readme_with_no_block_is_refused(tmp_path):
    path = str(tmp_path / "bare.md")
    open(path, "w", encoding="utf-8").write("# nothing here\n")
    with pytest.raises(SystemExit):
        chk.vkey_from_readme(path)


def test_two_lines_in_the_block_are_refused(tmp_path, keypair):
    """Two keys in the block is the shape a careless rotation leaves behind."""
    a = _line(NAME, keypair[1])
    b = _line(NAME, new_receipt_keypair()[1])
    path = str(tmp_path / "two.md")
    open(path, "w", encoding="utf-8", newline="\n").write(
        "%s\n```\n%s\n%s\n```\n%s\n" % (chk.BEGIN, a, b, chk.END))
    with pytest.raises(SystemExit):
        chk.vkey_from_readme(path)


def test_a_self_consistent_line_passes_offline(keypair):
    assert chk.offline(_line(NAME, keypair[1])) == []


def test_one_flipped_character_in_the_key_is_caught_offline(keypair):
    """The mutation the offline check exists for: somebody edits the key and leaves the id."""
    line = _line(NAME, keypair[1])
    name, kid, b64 = line.split("+", 2)
    raw = bytearray(base64.b64decode(b64))
    raw[-1] ^= 0x01
    broken = "%s+%s+%s" % (name, kid, base64.b64encode(bytes(raw)).decode())
    assert broken != line, "the mutation changed nothing, so this test proves nothing"
    problems = chk.offline(broken)
    assert problems and "derives" in problems[0]


def test_a_changed_name_is_caught_offline(keypair):
    """The id covers the NAME as well as the key, so renaming the log breaks it."""
    line = _line(NAME, keypair[1])
    _name, kid, b64 = line.split("+", 2)
    renamed = "%s+%s+%s" % ("attacker.test/log", kid, b64)
    assert chk.offline(renamed), "a renamed log must not keep its key id"


def test_a_truncated_key_is_refused_rather_than_compared(keypair):
    line = _line(NAME, keypair[1])
    name, kid, b64 = line.split("+", 2)
    short = base64.b64encode(base64.b64decode(b64)[:20]).decode()
    with pytest.raises(SystemExit):
        chk.parse("%s+%s+%s" % (name, kid, short))


def test_the_fingerprint_changes_with_the_line(keypair):
    a = _line(NAME, keypair[1])
    b = _line(NAME, new_receipt_keypair()[1])
    assert chk.fingerprint(a) != chk.fingerprint(b)
    assert len(chk.fingerprint(a)) == 64


# -- the online half, with the network replaced so the cases are reachable ----------------------
def _served(monkeypatch, vkey_line: str, note: str):
    def fake(url):
        if url.endswith("checkpoint.vkey"):
            return vkey_line.encode("utf-8") + b"\n"
        if url.endswith("/checkpoint"):
            return note.encode("utf-8")
        raise AssertionError("unexpected fetch of %s" % url)
    monkeypatch.setattr(chk, "fetch", fake)


def _note(sk_hex: str, pub_hex: str, name=NAME, size=3):
    return cp.signed_checkpoint(name, size, b"\x11" * 32, sk_hex, pub_hex)


def test_the_host_and_the_readme_agreeing_passes_online(monkeypatch, keypair):
    sk_hex, pub_hex = keypair
    line = _line(NAME, pub_hex)
    _served(monkeypatch, line, _note(sk_hex, pub_hex))
    assert chk.online(line, "https://example.test/log") == []


def test_a_host_serving_a_different_key_is_caught(monkeypatch, keypair):
    """The attack the second copy exists for: the host swaps its own key and signs with it."""
    other_sk, other_pub = new_receipt_keypair()
    line = _line(NAME, keypair[1])
    attacker_line = _line(NAME, other_pub)
    assert attacker_line != line
    _served(monkeypatch, attacker_line, _note(other_sk, other_pub))
    problems = chk.online(line, "https://example.test/log")
    assert problems and "publishes" in problems[0]


def test_a_matching_key_that_did_not_sign_the_checkpoint_is_caught(monkeypatch, keypair):
    """Byte-identical strings are not enough: the key must be the one signing the live head."""
    other_sk, other_pub = new_receipt_keypair()
    line = _line(NAME, keypair[1])
    _served(monkeypatch, line, _note(other_sk, other_pub))
    problems = chk.online(line, "https://example.test/log")
    assert problems, "a checkpoint signed by another key must not pass"
