"""What scan_residue() can and CANNOT see — pinned, because the gap is the part that matters.

scan_residue() answers "did the bytes actually go?", and a caller with an erasure obligation reads
`ok=True` as an all-clear. The match is a literal, case-sensitive substring search, so a store that
normalises case on write, re-wraps text, or keeps a base64/hex copy holds residue this reports clean.

Elsewhere the library discloses only that "a paraphrase is NOT caught". A change of case is not a
paraphrase. These tests make the real scope executable so it cannot quietly drift, in EITHER direction:

  - the FOUND cases guard the recall we have (a regression that stopped finding an exact match would
    turn this tool into one that always says clean, which is the worst possible failure for it)
  - the MISSED cases document the known limitation. If someone later makes matching case- or
    whitespace-insensitive, these fail LOUDLY and force the module docstring and any public claim to be
    updated in the same change, instead of the docs and the behaviour drifting apart.

A MISSED case failing is therefore not necessarily a bug — it may be an improvement that has not
updated its own documentation yet. The assertion message says so.
"""
import base64
import json
import os

import pytest

from inspeximus.erasure_residue import scan_residue

SECRET = "Ludwig Wittgenstein"

FOUND = {
    "exact": SECRET,
    "json_quoted": json.dumps(SECRET),
}
MISSED = {
    "lowercased": SECRET.lower(),
    "uppercased": SECRET.upper(),
    "double_space": SECRET.replace(" ", "  "),
    "newline_between": SECRET.replace(" ", "\n"),
    "base64": base64.b64encode(SECRET.encode()).decode(),
    "hex": SECRET.encode().hex(),
}


def _scan_one(tmp_path, name, content):
    """Plant exactly one encoding in its own directory, so a finding is unambiguously about it."""
    d = tmp_path / name
    d.mkdir()
    (d / "data.txt").write_text(content, encoding="utf-8")
    return scan_residue(str(d), [SECRET], skip_dirs=set())


@pytest.mark.parametrize("name", sorted(FOUND))
def test_these_encodings_are_detected(tmp_path, name):
    res = _scan_one(tmp_path, name, FOUND[name])
    assert res["findings"], (
        f"REGRESSION: scan_residue no longer detects the {name!r} form of a planted secret. "
        f"A residue scanner that stops finding residue reports every store as clean.")
    assert res["ok"] is False, "a store containing the value must never report ok=True"


@pytest.mark.parametrize("name", sorted(MISSED))
def test_these_encodings_are_known_to_be_missed(tmp_path, name):
    res = _scan_one(tmp_path, name, MISSED[name])
    assert not res["findings"], (
        f"scan_residue now DETECTS the {name!r} form, which it did not before. That is very likely an "
        f"improvement — but this test exists so the improvement cannot land silently: update the "
        f"MATCHING SCOPE table in inspeximus/erasure_residue.py, and anywhere the library states its "
        f"erasure-detection limits, in this same change.")


def test_a_clean_directory_is_not_a_false_positive(tmp_path):
    """The control. Without it, a scanner that flagged everything would pass every test above."""
    d = tmp_path / "clean"
    d.mkdir()
    (d / "data.txt").write_text("nothing sensitive here at all", encoding="utf-8")
    res = scan_residue(str(d), [SECRET], skip_dirs=set())
    assert not res["findings"]
    assert res["ok"] is True, "a genuinely clean directory must report ok=True, else the tool is noise"


def test_an_unsearched_location_is_not_reported_as_clean(tmp_path):
    """Fail-closed behaviour the module already implements — pinned because it is load-bearing.

    'I did not look' and 'I looked and it was clean' must never be the same answer.
    """
    missing = scan_residue(str(tmp_path / "does-not-exist"), [SECRET])
    assert missing["ok"] is False, "a non-existent root must not read as clean"
    assert missing["problems"], "it must say WHY it could not answer"

    empty_values = scan_residue(str(tmp_path), [])
    assert empty_values["ok"] is False, "searching for nothing must not read as clean"
    assert empty_values["problems"]
