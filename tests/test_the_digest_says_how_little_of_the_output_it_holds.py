"""A digest states its own coverage, so a caller cannot read 5 percent as the whole output.

The header says how many sections exist and how many are listed. It never said how much TEXT came
back. Measured 2026-10-29 on real command output: 12,933 bytes arrived as a 666-byte digest, 5.1
percent, and nothing in the text said so. A truncation leaves a visible cut; a digest reads as
finished, so the warning has to be written.

These tests check the claim the line makes, not merely that a line is present: the character count
it prints must equal the text actually quoted above it. A line that says "about 4%" while quoting
something else is worse than no line.
"""
from __future__ import annotations

import pytest

from inspeximus.context_saver import PASSTHROUGH_BYTES, PREVIEW, TOP, _title, chunks_capped, save

MARK = "This digest holds about"


def _long_output(n=320):
    rows = ["section head %d" % i for i in range(1)]
    for i in range(n):
        rows.append("row %d: a line of ordinary output about unrelated matters" % i)
    rows.insert(n // 2, "rotation_key: 7f3a9c2b")
    return "\n".join(rows) + "\n"


def _digest_of(text, intent="read the deploy configuration"):
    r = save(text, intent, without_reranker="hybrid", ranking="lexical")
    assert r["mode"] == "digest", "this fixture must digest, not %s" % r["mode"]
    return r


def test_a_digest_says_what_share_of_the_output_it_holds():
    r = _digest_of(_long_output())
    line = [ln for ln in r["text"].splitlines() if ln.startswith(MARK)]
    assert line, "a digest that holds a fraction of the output must say so:\n%s" % r["text"][:400]
    assert "read it rather than concluding" in line[0], (
        "the line must tell the caller what to do, not only state a number")


def test_the_share_it_states_matches_the_text_it_quoted():
    """The number has to be about THIS digest, or it is decoration.

    Recomputed from the shipped constants rather than copied from the line: a change to PREVIEW or
    TOP that leaves the sentence stale fails here.
    """
    text = _long_output()
    r = _digest_of(text)
    line = next(ln for ln in r["text"].splitlines() if ln.startswith(MARK))
    inside = line.split("(", 1)[1].split(" characters", 1)[0]   # "562 of 12929"
    stated, total = (int(x) for x in inside.split(" of "))

    chunks = chunks_capped(text)
    kept = sum(len(_title(chunks[i])) + len(chunks[i][:PREVIEW]) for i in r["order"][:TOP])
    assert stated == kept, "the line states %d characters; the quoted sections are %d" % (stated, kept)
    assert total == sum(len(c) for c in chunks), "the total is not the output it digested"
    assert 0 < stated < total, "a digest that holds everything or nothing is not a digest"


def test_a_short_output_gets_no_such_line():
    """CONTROL. An output the saver passes through whole must carry no coverage warning.

    Without this the test above passes on a line that is printed unconditionally, which would warn
    about a digest that is in fact the entire output.
    """
    small = "alpha: 1\n" + "x" * 40 + "\nomega: 2\n"
    r = save(small, "alpha", without_reranker="hybrid", ranking="lexical")
    assert r["mode"] == "passthrough", "fixture must be under the passthrough size"
    assert len(small) < PASSTHROUGH_BYTES
    assert MARK not in r["text"]


@pytest.mark.parametrize("intent", ["rotation_key", "read the deploy configuration", "unrelated"])
def test_the_line_is_there_whatever_the_intent_ranked(intent):
    """The warning is about coverage, not about the ranking, so no intent may remove it."""
    r = _digest_of(_long_output(), intent)
    assert any(ln.startswith(MARK) for ln in r["text"].splitlines())
