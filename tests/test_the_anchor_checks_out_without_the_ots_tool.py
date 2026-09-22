"""The OpenTimestamps verifier, against a real anchor of our own log.

WHY A REAL PROOF RATHER THAN ONE THE TESTS BUILD. A verifier tested only on proofs it generated
itself agrees with its own misreading of the format. The fixtures beside this file are one published
head of our hosted log, the receipt stamped over it, one calendar's upgrade response, and the header
of Bitcoin block 968177. Everything here runs offline from those bytes.

The controls are the point. A verifier that cannot say MISMATCH is a verifier that says ANCHORED to
anything, so every case below breaks one input and requires the verdict to change: a byte of the
data, a byte of the block header, a missing header, a truncated proof.
"""
from __future__ import annotations

import hashlib
import os

import pytest

from inspeximus.opentimestamps import (MAGIC, Malformed, attestations_from_upgrade, block_hash_of,
                                       merkle_root_of, parse, upgrade, verify)

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "ots")
BLOCK = 968177
BLOCK_HASH = "00000000000000000000da19d18bc39596d5c8bf846954485ec7cf8c1cdaab74"


def _b(name: str) -> bytes:
    with open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


@pytest.fixture()
def data():
    return _b("head.json")


@pytest.fixture()
def proof():
    return _b("head.json.ots")


@pytest.fixture()
def header():
    with open(os.path.join(FIX, "block968177.header.hex"), encoding="utf-8") as fh:
        return bytes.fromhex(fh.read().strip())


@pytest.fixture()
def anchored(proof):
    """The Bitcoin attestation one calendar supplied, walked from the proof's own commitment."""
    first = parse(proof)["attestations"][0]
    return attestations_from_upgrade(first["digest"], _b("upgrade.alice.bin"))


# -- reading the proof ---------------------------------------------------------------------------
def test_the_proof_names_every_calendar_it_was_sent_to(proof):
    """A proof is a tree. Reading only the first branch is how an anchored proof reads as pending."""
    p = parse(proof)
    assert p["algorithm"] == "sha256" and p["version"] == 1
    calendars = [a["uri"] for a in p["attestations"] if a["kind"] == "pending"]
    assert len(calendars) == 4, calendars
    assert len(set(calendars)) == 4


def test_the_proof_is_about_the_head_we_published(data, proof):
    assert parse(proof)["file_digest"] == hashlib.sha256(data).hexdigest()


def test_a_file_that_is_not_a_proof_is_refused():
    with pytest.raises(Malformed):
        parse(b"this is not an ots file at all")


def test_a_truncated_proof_is_refused_rather_than_half_read(proof):
    with pytest.raises(Malformed):
        parse(proof[:len(MAGIC) + 20])


def test_an_unknown_operation_is_refused():
    """An op byte we do not implement must stop the walk, not be skipped. A skipped operation
    changes the digest and the proof then lands on a root that belongs to nothing.

    Built by hand rather than by mutating the fixture. The first attempt flipped the fixture's last
    byte, which lands inside a calendar URL, so the proof stayed valid and the test passed for a
    reason that had nothing to do with operations.
    """
    body = MAGIC + bytes([0x01, 0x08]) + bytes([0xAA]) * 32 + bytes([0x7E])
    with pytest.raises(Malformed) as e:
        parse(body)
    assert "0x7e" in str(e.value)


def test_a_proof_whose_digest_algorithm_is_not_a_hash_is_refused():
    with pytest.raises(Malformed):
        parse(MAGIC + bytes([0x01, 0xF0]) + bytes([0xAA]) * 32)


# -- the verdicts --------------------------------------------------------------------------------
def test_a_proof_with_only_calendar_promises_is_PENDING_not_OK(data, proof):
    out = verify(data, proof)
    assert out["verdict"] == "PENDING"
    assert "no block has been mined" in out["why"]


def test_the_real_anchor_verifies_against_the_real_block_header(data, proof, header, anchored):
    out = verify(data, proof, block_header=header, extra_attestations=anchored)
    assert out["verdict"] == "ANCHORED"
    assert out["height"] == BLOCK
    assert out["block_hash"] == BLOCK_HASH
    assert out["merkle_root"] == merkle_root_of(header)


def test_CONTROL_one_changed_byte_of_data_is_a_MISMATCH(data, proof, header, anchored):
    bad = bytearray(data)
    bad[120] ^= 0x01
    assert bytes(bad) != data, "the mutation changed nothing, so this test proves nothing"
    out = verify(bytes(bad), proof, block_header=header, extra_attestations=anchored)
    assert out["verdict"] == "MISMATCH"
    assert "different bytes" in out["why"]


def test_CONTROL_one_changed_byte_of_the_block_header_is_a_MISMATCH(data, proof, header, anchored):
    """The other half of the control: the proof is honest and the block is not the right one."""
    bad = bytearray(header)
    bad[40] ^= 0x01
    assert bytes(bad) != header
    out = verify(data, proof, block_header=bytes(bad), extra_attestations=anchored)
    assert out["verdict"] == "MISMATCH"
    assert out["height"] == BLOCK


def test_no_block_header_is_INCOMPLETE_and_never_OK(data, proof, anchored):
    out = verify(data, proof, extra_attestations=anchored)
    assert out["verdict"] == "INCOMPLETE"
    assert out["expected_merkle_root"] and out["height"] == BLOCK


def test_either_byte_order_of_the_merkle_root_is_accepted(data, proof, header, anchored):
    """Explorers print the root reversed from the order the proof computes. A caller who pastes the
    one their source gave them must not get a MISMATCH for it."""
    printed = merkle_root_of(header)
    internal = bytes.fromhex(printed)[::-1].hex()
    assert printed != internal
    for root in (printed, internal):
        out = verify(data, proof, merkle_root=root, extra_attestations=anchored)
        assert out["verdict"] == "ANCHORED", root


def test_the_wrong_height_is_not_silently_matched(data, proof, header, anchored):
    out = verify(data, proof, block_header=header, height=BLOCK + 1, extra_attestations=anchored)
    assert out["verdict"] in ("PENDING", "INCOMPLETE")
    assert out["verdict"] != "ANCHORED"


# -- the line-ending trap, which our own published anchor walked into ------------------------------
def test_a_CRLF_copy_is_a_MISMATCH_that_names_its_cause(data, proof):
    """Measured on our own published receipt: it was stamped in Linux CI over LF bytes, and the same
    file checked out on Windows has CRLF. An honest file then reports MISMATCH, and the first
    customer to try it concludes tampering."""
    crlf = data.replace(b"\n", b"\r\n")
    assert crlf != data
    out = verify(crlf, proof)
    assert out["verdict"] == "MISMATCH", "the bytes really do differ, so the verdict must not soften"
    assert "line endings" in out["line_endings"]
    assert "not evidence of tampering" in out["line_endings"]
    assert "not a pass either" in out["line_endings"]


def test_CONTROL_a_real_change_gets_no_line_ending_excuse(data, proof):
    bad = bytearray(data)
    bad[120] ^= 0x01
    out = verify(bytes(bad), proof)
    assert out["verdict"] == "MISMATCH"
    assert "line_endings" not in out, "a tampered file must not be handed an explanation"


# -- the block header helpers ----------------------------------------------------------------------
def test_the_header_hashes_to_the_block_it_claims_to_be(header):
    assert block_hash_of(header) == BLOCK_HASH


def test_a_header_of_the_wrong_length_is_refused():
    for n in (0, 79, 81, 160):
        with pytest.raises(Malformed):
            merkle_root_of(bytes(n))


# -- upgrade, with the network replaced --------------------------------------------------------------
def test_upgrade_asks_every_calendar_and_reports_each_one(proof):
    body = _b("upgrade.alice.bin")
    asked = []

    def fake(url):
        asked.append(url)
        if "alice" in url:
            return body
        raise OSError("this calendar is down")

    out = upgrade(proof, fetch=fake)
    assert len(asked) == 4, "every calendar in the proof must be asked"
    assert out["heights"] == [BLOCK]
    answered = [r for r in out["calendars"] if r.get("bitcoin")]
    failed = [r for r in out["calendars"] if r.get("error")]
    assert len(answered) == 1 and len(failed) == 3
    assert "this calendar is down" in failed[0]["error"]


def test_a_calendar_that_is_down_is_not_a_verdict(proof):
    """All four unreachable must leave the proof pending, not failed. A network problem that reads
    as a bad anchor is the same defect as a TLS error that reads as a fork."""
    out = upgrade(proof, fetch=lambda url: (_ for _ in ()).throw(OSError("no network")))
    assert out["heights"] == []
    assert all(r.get("error") for r in out["calendars"])


# -- the command a customer actually runs ----------------------------------------------------------
def _cli(argv):
    from inspeximus.cli import main
    return main(argv)


def test_the_cli_exit_codes_are_the_contract(tmp_path, data, proof, header, capsys):
    """0 ANCHORED, 1 MISMATCH, 3 PENDING. PENDING is neither an error nor a pass, so it must not
    share a code with either: a caller that treats 3 as failure alerts on a healthy new anchor, and
    one that treats it as success calls a promise a proof."""
    head = tmp_path / "head.json"
    head.write_bytes(data)
    (tmp_path / "head.json.ots").write_bytes(proof)

    # no header, no upgrade: only calendar promises are in the file
    assert _cli(["ots", "verify", str(head)]) == 3
    assert "PENDING" in capsys.readouterr().out

    bad = tmp_path / "bad.json"
    bad.write_bytes(bytes(bytearray(data)[:120] + bytes([data[120] ^ 1]) + data[121:]))
    (tmp_path / "bad.json.ots").write_bytes(proof)
    assert _cli(["ots", "verify", str(bad)]) == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_the_cli_reports_a_proof_it_cannot_read(tmp_path, data, capsys):
    head = tmp_path / "head.json"
    head.write_bytes(data)
    (tmp_path / "head.json.ots").write_bytes(b"not a proof")
    assert _cli(["ots", "verify", str(head)]) == 1
    assert "could not read" in capsys.readouterr().out


def test_the_cli_upgrade_prints_every_calendar(tmp_path, proof, monkeypatch, capsys):
    path = tmp_path / "head.json.ots"
    path.write_bytes(proof)
    body = _b("upgrade.alice.bin")
    import inspeximus.opentimestamps as ots_mod
    real = ots_mod.upgrade
    monkeypatch.setattr(ots_mod, "upgrade",
                        lambda o, **k: real(o, fetch=lambda url: body if "alice" in url
                                            else (_ for _ in ()).throw(OSError("down"))))
    assert _cli(["ots", "upgrade", str(path)]) == 0
    printed = capsys.readouterr().out
    assert printed.count("could not be reached") == 3
    assert "block 968177" in printed
