"""A verifier of third-party bytes returns its verdict for malformed input; it never raises (audit A-20).

`scitt.verify_transparent_statement` and `transparency.verify_registered_statement` document "Returns
{ok, ..., problems}". Handed a malformed statement they raised from the CBOR decoder instead: 394 and 396
of 400 random byte strings, as ValueError, UnicodeDecodeError, IndexError or TypeError. The decoder now
raises one type, `cose.CoseDecodeError`, and both verifiers turn it into ok=False with the problem named.
"""
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import cose, scitt, transparency

ACCEPT = lambda *a, **k: True                  # noqa: E731  a verifier callback that accepts everything
VERIFIERS = [scitt.verify_transparent_statement, transparency.verify_registered_statement]
MALFORMED = [
    b"\x18",                                   # truncated: a one-byte length with no byte
    b"\x61\xff",                               # a text string holding invalid UTF-8
    b"\xa1\x01",                               # a map with its value missing
    b"\x80\x00",                               # a complete item followed by trailing bytes
    b"\xd2\x80",                               # tag 18 around an empty array: the wrong COSE shape
    b"\xd2\x83\x40\xa0\x40",                   # tag 18 around three items instead of four
]


@pytest.mark.parametrize("fn", VERIFIERS, ids=["scitt", "transparency"])
@pytest.mark.parametrize("blob", MALFORMED, ids=[b.hex() for b in MALFORMED])
def test_a_malformed_statement_gets_a_verdict_not_an_exception(fn, blob):
    r = fn(blob, ACCEPT, ACCEPT, b"leaf", b"\x00" * 32)
    assert isinstance(r, dict) and r["ok"] is False and r["problems"], r


@pytest.mark.parametrize("fn", VERIFIERS, ids=["scitt", "transparency"])
def test_neither_verifier_raises_on_random_bytes(fn):
    rnd = random.Random(20260927)
    for _ in range(400):
        blob = bytes(rnd.randrange(256) for _ in range(rnd.randrange(1, 120)))
        r = fn(blob, ACCEPT, ACCEPT, b"leaf", b"\x00" * 32)
        assert r["ok"] is False, (blob.hex(), r)


def test_the_decoder_raises_one_type_and_it_is_still_a_value_error():
    for blob in MALFORMED[:4]:
        with pytest.raises(cose.CoseDecodeError):
            cose.decode(blob)
    assert issubclass(cose.CoseDecodeError, ValueError), "code that caught the old ValueError must still"


def test_a_well_formed_item_still_decodes():
    assert cose.decode(cose.encode({1: "a", 2: [b"x", 3]})) == {1: "a", 2: [b"x", 3]}
