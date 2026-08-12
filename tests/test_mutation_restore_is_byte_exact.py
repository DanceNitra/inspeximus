"""The gate must put back the BYTES it took, not an equivalent rendering of them.

`mutation_check` writes a mutant over a source file and restores the original in a `finally`. Both used
text mode, and on Windows text mode turns every "\\n" into "\\r\\n" -- so restoring an LF file rewrote it
as CRLF. The content was identical and `git diff` showed nothing, but `git status` reported the file
modified, and the gate's own end-of-run check flagged it as collateral it could not restore:

    !! 1 tracked file(s) were dirtied by this run and are OUTSIDE the restore allowlist: README.md

It hid for a long time because most sources here are CRLF on checkout, which makes the round trip an
accidental identity. README.md is LF -- one mutation targets it -- so it was the one that surfaced. The
allowlist deliberately refuses to `git checkout` anything but probe receipts (a tool that can delete work
it did not write is worse than one that leaves a mess), so the file simply stayed dirty after every run.

These tests assert BEHAVIOUR, not the spelling of the fix: give the round trip each line-ending style and
require the bytes back unchanged.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import mutation_check  # noqa: E402

import pytest

#: Drives the in-place source-mutation harness. NEVER under xdist -- it edits the very tree
#: the other workers are importing. Measured as a parallel-only failure that passes alone.
#: Run this set with:  pytest tests/ -n 0 -m mutation
pytestmark = pytest.mark.mutation


CASES = {
    "lf": b"alpha\nbeta\ngamma\n",
    "crlf": b"alpha\r\nbeta\r\ngamma\r\n",
    "mixed": b"alpha\r\nbeta\ngamma\r\n",
    "no-trailing-newline": b"alpha\nbeta",
    "cr-only": b"alpha\rbeta\r",
}


def _roundtrip(tmp_path, raw: bytes) -> bytes:
    p = tmp_path / "f.txt"
    p.write_bytes(raw)
    mutation_check._write_exact(str(p), mutation_check._read_exact(str(p)))
    return p.read_bytes()


def test_every_line_ending_style_survives_the_round_trip(tmp_path):
    for name, raw in CASES.items():
        assert _roundtrip(tmp_path, raw) == raw, f"{name}: bytes changed"


def test_an_lf_file_does_not_come_back_as_crlf(tmp_path):
    """THE defect, named on its own so a regression says what broke."""
    raw = b"one\ntwo\n"
    assert b"\r" not in _roundtrip(tmp_path, raw)


def test_the_mutated_write_preserves_endings_too(tmp_path):
    """Restoring byte-exactly is not enough if applying the mutant rewrites the endings first: the
    tests then run against a file that differs from the real one in every line."""
    p = tmp_path / "f.txt"
    p.write_bytes(b"keep\nTARGET\nkeep\n")
    src = mutation_check._read_exact(str(p))
    mutation_check._write_exact(str(p), src.replace("TARGET", "MUTANT", 1))
    assert p.read_bytes() == b"keep\nMUTANT\nkeep\n"
    mutation_check._write_exact(str(p), src)
    assert p.read_bytes() == b"keep\nTARGET\nkeep\n"


def test_a_spec_written_with_lf_still_matches_a_crlf_file():
    """The consequence of reading byte-exactly, and it bit within minutes of the fix landing.

    Specs are authored with "\\n" so ONE mutations.json serves a Windows checkout (CRLF) and Linux CI
    (LF). Once the reader stopped translating, a multi-line target written with "\\n" could no longer
    match a CRLF file -- and that does not fail quietly, it becomes a SKIP, and a skip fails this gate.
    """
    crlf_src = 'def f():\r\n    with open(p) as fh:\r\n        return fh.read()\r\n'
    spec = '    with open(p) as fh:\n        return fh.read()'
    assert spec not in crlf_src, "the raw spec must NOT match -- otherwise this test proves nothing"
    assert mutation_check._match_endings(spec, crlf_src) in crlf_src


def test_an_lf_file_leaves_the_spec_alone():
    """CONTROL. Converting unconditionally would break the LF case it was meant to leave working."""
    lf_src = 'a\nb\nc\n'
    spec = 'a\nb'
    assert mutation_check._match_endings(spec, lf_src) == spec
    assert mutation_check._match_endings(spec, lf_src) in lf_src


def test_a_single_line_spec_is_unaffected_either_way():
    for src in ('x = 1\r\ny = 2\r\n', 'x = 1\ny = 2\n'):
        assert mutation_check._match_endings('x = 1', src) == 'x = 1'


def test_the_helper_would_notice_a_translating_writer(tmp_path):
    """CONTROL. If text mode were harmless on this platform the tests above would pass for the wrong
    reason -- they would be asserting nothing. This documents what the broken writer actually did here,
    and is skipped where the platform does not translate."""
    import io
    p = tmp_path / "f.txt"
    with io.open(p, "w", encoding="utf-8") as fh:      # deliberately WITHOUT newline=""
        fh.write("one\ntwo\n")
    translated = p.read_bytes() == b"one\r\ntwo\r\n"
    if not translated:
        import pytest
        pytest.skip("this platform does not translate newlines in text mode; nothing to guard against")
    assert translated, "text mode translated -- which is exactly why the helpers must not use it"
