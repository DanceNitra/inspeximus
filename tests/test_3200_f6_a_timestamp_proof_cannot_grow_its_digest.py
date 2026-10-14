"""3.20, F6 (Claude Security scan, LOW): an OpenTimestamps proof cannot make the verifier grow its digest or run without end.

THE DEFECT. `hexlify` (0xF3) doubles the digest, and nothing bounded it: a 98-byte proof with 22 of them peaked at
403 MB in `parse` (measured on v3.19.1, AUDIT-A's ots_repro.py), about 30 kill the process, and `attestations_from_upgrade`
walks a calendar's response the same way. Nor was the number of operations bounded, so a long proof of cheap
operations on a large digest cost hashing in proportion to both.

WHAT IS PINNED HERE. The digest is refused once it passes MAX_DIGEST_BYTES (4,096) after any operation, and a proof,
every fork counted together, is refused past MAX_OPERATIONS (10,000). Each refusal is a `Malformed`, not a verdict, and
comes with a peak allocation under 8 MB. The control: the real proof in tests/fixtures/ots still parses.
"""
import os
import sys
import tracemalloc

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import opentimestamps as ots  # noqa: E402

PEAK_BOUND = 8 * 1024 * 1024
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "ots", "head.json.ots")


def _varu(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _proof(ops: bytes) -> bytes:
    """A sha256 proof of a fixed digest, then `ops`, then a bitcoin attestation."""
    return (ots.MAGIC + b"\x01" + b"\x08" + b"\x22" * 32 + ops + b"\x00" + ots.BITCOIN_TAG
            + _varu(len(_varu(1))) + _varu(1))


def _peak(fn):
    tracemalloc.start()
    try:
        fn()
    finally:
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return peak


def test_hexlify_cannot_double_the_digest_past_the_bound():
    blob = _proof(b"\xf3" * 22)                         # 32 * 2^22 = 134 MB if nothing stops it
    assert len(blob) < 120, "CONTROL: the proof is tiny"

    def call():
        with pytest.raises(ots.Malformed, match="over the 4096-byte bound"):
            ots.parse(blob)
    assert _peak(call) < PEAK_BOUND


def test_a_calendar_response_is_bounded_the_same_way():
    body = b"\xf3" * 22 + b"\x00" + ots.BITCOIN_TAG + _varu(1) + _varu(1)

    def call():
        with pytest.raises(ots.Malformed, match="over the 4096-byte bound"):
            ots.attestations_from_upgrade("22" * 32, body)
    assert _peak(call) < PEAK_BOUND


def test_appends_cannot_grow_the_digest_past_the_bound():
    chunk = b"\xf0" + _varu(1000) + b"a" * 1000      # append 1,000 bytes, five times
    with pytest.raises(ots.Malformed, match="over the 4096-byte bound"):
        ots.parse(_proof(chunk * 5))


def test_a_proof_with_more_operations_than_the_bound_is_refused():
    with pytest.raises(ots.Malformed, match="more than 10000 operations"):
        ots.parse(_proof(b"\x08" * (ots.MAX_OPERATIONS + 1)))
    assert ots.parse(_proof(b"\x08" * ots.MAX_OPERATIONS))["attestations"], "CONTROL: the bound itself is allowed"


def test_the_operation_bound_counts_every_fork_together():
    """Two forks of 6,000 operations each: each fork alone is under the bound, the proof is not."""
    half = b"\x08" * 6000
    branch = half + b"\x00" + ots.BITCOIN_TAG + _varu(1) + _varu(1)
    blob = ots.MAGIC + b"\x01" + b"\x08" + b"\x22" * 32 + b"\xff" + branch + branch
    with pytest.raises(ots.Malformed, match="more than 10000 operations"):
        ots.parse(blob)


def test_the_real_proof_still_parses():
    p = ots.parse(open(FIXTURE, "rb").read())
    assert len(p["attestations"]) == 4, p["attestations"]
