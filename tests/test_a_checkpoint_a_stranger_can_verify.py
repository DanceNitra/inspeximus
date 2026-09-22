"""The checkpoint has to be verifiable by somebody who has only the spec and our public key.

So every test here re-derives the answer from c2sp.org/signed-note and c2sp.org/tlog-checkpoint
rather than from our own helpers. The key id is recomputed by hand, the signature is checked against
the note text with a plain Ed25519 verifier, and the root is compared with what merkle.py produced
for the same leaves. A test that calls our verifier to check our signer proves only that the two
agree with each other.

The control that matters is `test_a_note_whose_text_changed_does_not_verify`: if the signature
survives an edit to the text, the checkpoint is decoration.
"""
from __future__ import annotations

import base64
import hashlib

import pytest

from inspeximus import checkpoint as cp
from inspeximus import merkle
from inspeximus.core import new_receipt_keypair

ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")

ORIGIN = "example.com/log42"


@pytest.fixture()
def keys():
    return new_receipt_keypair()                                # (secret hex, public hex)


def _root(n):
    return merkle.root([b"entry %d" % i for i in range(n)])


def test_the_key_id_is_the_one_the_spec_prescribes(keys):
    """SHA-256(name || 0x0A || 0x01 || pubkey)[:4], recomputed here rather than imported."""
    _sk, pub = keys
    expected = hashlib.sha256(ORIGIN.encode() + b"\x0a" + b"\x01" + bytes.fromhex(pub)).digest()[:4]
    assert cp.key_id(ORIGIN, pub) == expected


def test_the_note_text_is_origin_size_root_and_ends_with_a_newline(keys):
    root = _root(281)
    text = cp.checkpoint_text(ORIGIN, 281, root)
    assert text.endswith("\n")
    lines = text.split("\n")[:-1]
    assert lines[0] == ORIGIN
    assert lines[1] == "281"                                    # decimal, no leading zeroes
    assert base64.b64decode(lines[2]) == root                   # standard base64, RFC 4648 section 4


def test_a_stranger_verifies_the_signature_with_only_the_public_key(keys):
    sk, pub = keys
    note = cp.signed_checkpoint(ORIGIN, 9, _root(9), sk, pub)
    text, rest = note.rpartition("\n\n")[0] + "\n", note.rpartition("\n\n")[2]
    line = rest.strip()
    assert line.startswith("— " + ORIGIN + " ")            # em dash, space, key name, space
    blob = base64.b64decode(line.split(" ")[2])
    assert blob[:4] == cp.key_id(ORIGIN, pub)
    ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(blob[4:], text.encode())


def test_a_note_whose_text_changed_does_not_verify(keys):
    """The control. A checkpoint whose signature survives an edit is decoration."""
    sk, pub = keys
    note = cp.signed_checkpoint(ORIGIN, 9, _root(9), sk, pub)
    tampered = note.replace("\n9\n", "\n10\n", 1)
    assert tampered != note
    with pytest.raises(Exception):
        cp.parse_checkpoint(tampered, {ORIGIN: pub}, required=[ORIGIN])


def test_a_signature_from_an_unknown_key_is_ignored_not_an_error(keys):
    """The spec says ignore. A stranger's cosignature must not make our own note unreadable."""
    sk, pub = keys
    other_sk, other_pub = new_receipt_keypair()
    note = cp.signed_checkpoint(ORIGIN, 9, _root(9), sk, pub)
    extra = cp.sign_note(cp.split_note(note)[0], "someone.else/w", other_sk, other_pub)
    both = note.rstrip("\n") + "\n" + extra.rpartition("\n\n")[2]
    text, verified = cp.verify_note(both, {ORIGIN: pub}, required=[ORIGIN])
    assert verified == [ORIGIN]
    assert cp.parse_checkpoint(both, {ORIGIN: pub})["size"] == 9


def test_a_known_name_carrying_a_different_key_is_not_us(keys):
    """Same name, different key: the id does not match, so the signature is somebody else's."""
    sk, pub = keys
    _other_sk, other_pub = new_receipt_keypair()
    note = cp.signed_checkpoint(ORIGIN, 9, _root(9), sk, pub)
    with pytest.raises(ValueError):
        cp.verify_note(note, {ORIGIN: other_pub}, required=[ORIGIN])


def test_the_root_matches_the_tree_the_log_already_publishes(keys):
    """The checkpoint restates our own head. If it disagrees with merkle.py it is a second truth."""
    sk, pub = keys
    leaves = [b"entry %d" % i for i in range(41)]
    note = cp.signed_checkpoint(ORIGIN, len(leaves), merkle.root(leaves), sk, pub)
    parsed = cp.parse_checkpoint(note, {ORIGIN: pub}, required=[ORIGIN])
    assert parsed["root"] == merkle.root(leaves)
    assert parsed["size"] == len(leaves)


def test_an_empty_tree_is_size_zero_and_still_signs(keys):
    sk, pub = keys
    note = cp.signed_checkpoint(ORIGIN, 0, merkle.root([]), sk, pub)
    assert cp.parse_checkpoint(note, {ORIGIN: pub}, required=[ORIGIN])["size"] == 0


@pytest.mark.parametrize("bad", ["", "has space", "has+plus", "two\nlines"])
def test_an_origin_the_spec_forbids_is_refused(bad, keys):
    with pytest.raises(ValueError):
        cp.checkpoint_text(bad, 1, _root(1))


def test_the_text_boundary_is_the_last_blank_line(keys):
    """A note text may contain empty lines, so the split cannot take the first one."""
    sk, pub = keys
    text = cp.checkpoint_text(ORIGIN, 3, _root(3), extensions=["x 1"])
    note = cp.sign_note(text, ORIGIN, sk, pub)
    assert cp.split_note(note)[0] == text
    assert cp.parse_checkpoint(note, {ORIGIN: pub}, required=[ORIGIN])["extensions"] == ["x 1"]


def test_the_vkey_carries_the_type_byte_and_the_key(keys):
    """Split on the first two plus signs only: a key name cannot contain one, base64 can.

    Found on the live log rather than here: the first reader of the published vkey split on every
    plus and crashed on a key whose base64 happened to contain one. Go's note package uses
    SplitN(vkey, "+", 3) for the same reason, and the test now reads it the way that reader does.
    """
    _sk, pub = keys
    name, kid, blob = cp.vkey(ORIGIN, pub).split("+", 2)
    assert name == ORIGIN and kid == cp.key_id(ORIGIN, pub).hex()
    raw = base64.b64decode(blob)
    assert raw[0] == cp.ED25519_SIGNATURE_TYPE and raw[1:].hex() == pub


def test_a_vkey_whose_base64_contains_a_plus_still_parses(keys):
    """The control for the line above: a key that produces no plus sign proves nothing."""
    plussed = [k for k in (new_receipt_keypair() for _ in range(40))
               if "+" in base64.b64encode(bytes([cp.ED25519_SIGNATURE_TYPE]) + bytes.fromhex(k[1])).decode()]
    assert plussed, "no key in 40 produced a plus sign, so this control did not run"
    _sk, pub = plussed[0]
    name, kid, blob = cp.vkey(ORIGIN, pub).split("+", 2)
    assert name == ORIGIN and base64.b64decode(blob)[1:].hex() == pub
