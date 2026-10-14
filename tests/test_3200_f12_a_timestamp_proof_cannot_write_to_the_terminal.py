"""3.20, F12 (Claude Security scan, LOW): a timestamp proof cannot write terminal control sequences into the verdict.

THE DEFECT. A pending attestation's URI was decoded with no check and printed in the verdict, so a proof could carry
escape sequences that erase the PENDING line and print a fake one. Measured on v3.19.1 (AUDIT-A's ots_repro.py):
`inspeximus ots verify` on a pending proof whose URI held ESC[2K ESC[1A ESC[2K "verdict : ANCHORED  block 800000"
wrote the raw ESC byte and the fake line to stdout; only the exit code (3) still told the truth.

WHAT IS PINNED HERE. A pending URI outside the OpenTimestamps URI alphabet, or over 1,000 bytes, makes the proof
malformed at parse time, as in the reference client. And everything the ots commands print from a proof is escaped,
so a terminal never executes it.
"""
import hashlib
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import opentimestamps as ots  # noqa: E402

FAKE = "http://x/\x1b[2K\x1b[1A\x1b[2Kverdict : ANCHORED  block 800000"


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


def _vb(b):
    return _varu(len(b)) + b


def _pending(uri: bytes, digest=b"\x11" * 32):
    return ots.MAGIC + b"\x01" + b"\x08" + digest + b"\x00" + ots.PENDING_TAG + _vb(_vb(uri))


def test_a_uri_with_an_escape_sequence_is_a_malformed_proof():
    with pytest.raises(ots.Malformed, match="outside the OpenTimestamps URI alphabet"):
        ots.parse(_pending(FAKE.encode()))


def test_a_uri_over_the_length_bound_is_a_malformed_proof():
    with pytest.raises(ots.Malformed, match="over the 1000"):
        ots.parse(_pending(b"https://a.example/" + b"a" * 1000))


def test_the_cli_prints_no_escape_byte_and_no_fake_verdict(tmp_path):
    data = tmp_path / "data.bin"
    data.write_bytes(b"x")
    proof = tmp_path / "p.ots"
    proof.write_bytes(_pending(FAKE.encode(), hashlib.sha256(b"x").digest()))
    cp = subprocess.run([sys.executable, "-m", "inspeximus", "ots", "verify", str(data), "--proof", str(proof)],
                        capture_output=True, timeout=60, cwd=str(tmp_path),
                        env=dict(os.environ, PYTHONPATH=ROOT))
    out = cp.stdout + cp.stderr
    assert b"\x1b" not in out, "the verdict carried a raw ESC byte: %r" % out[:200]
    assert b"verdict : ANCHORED" not in out
    assert cp.returncode == 1, (cp.returncode, out[:200])              # a malformed proof, said so


def test_the_real_proof_still_parses_and_its_calendars_are_unchanged():
    p = ots.parse(open(os.path.join(ROOT, "tests", "fixtures", "ots", "head.json.ots"), "rb").read())
    assert sorted(a["uri"] for a in p["attestations"] if a["kind"] == "pending") == sorted(ots.DEFAULT_CALENDARS)


def test_printed_text_from_a_proof_is_escaped():
    from inspeximus.cli import _shown
    assert _shown("a\x1b[2Kb\x07c") == "a\\x1b[2Kb\\x07c"
    assert _shown("plain text, unchanged") == "plain text, unchanged"
