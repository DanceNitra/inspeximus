"""A write whose text ends in tool-call frame markup is a malformed CALL, not a memory.

Measured 2026-08-26. An agent serialised `topic` and `source` inside the `decision` string, so three
records landed carrying a literal ``</decision><topic>...</topic><source>...</source></invoke>``
tail. Each returned a normal id. Each reported ``topic=None`` and ``attributable=False``, and the
caller read those nulls as a server fault rather than as its own defect: it retried the same
malformed call twice more before checking the stored record.

Nothing about the store was broken. It did the right thing with what it was handed. The defect is
that being handed garbage and answering "ok" is indistinguishable from working, and the cost is
specific: `key` never arrived, so keyed supersession was silently off, so those decisions could not
be corrected later by topic. That is the one property this product sells.

The guard is deliberately narrow, and the controls below are the point of this file: a memory that
QUOTES the markup (like the docstring above) must still be storable, or the guard would make its own
bug undocumentable.
"""
from __future__ import annotations

import pytest

from inspeximus.core import Inspeximus, _reject_frame_markup, _FRAME_TAIL, _SMUGGLED_PARAMS

NL = chr(10)
SMUGGLED = ("DECISION: we chose the wire capture." + NL
            + "<topic>82056-exact-constant</topic>" + NL
            + "<source>anthropics/claude-code#82056</source>" + NL
            + "</invoke>")
SMUGGLED_NO_TAIL = "DECISION: we chose the wire capture.</decision>" + NL + "<topic>x</topic>"


def test_the_patterns_are_alive():
    """A dead regex fails OPEN, and this one was dead once.

    The backreference in _SMUGGLED_PARAMS was written as a literal chr(2) by a heredoc that ate the
    backslash. The pattern compiled, matched nothing, and the guard reported every smuggled write as
    clean. Assert behaviour, never the spelling.
    """
    assert _FRAME_TAIL.search("x</invoke>")
    assert _SMUGGLED_PARAMS.search(SMUGGLED_NO_TAIL)
    assert chr(2) not in _SMUGGLED_PARAMS.pattern


# The shape that ESCAPED the first version of this guard, four hours after it shipped. The frame
# terminator never arrives and the smuggled tag is never closed, because the real </parameter> was
# eaten as the delimiter. Kept as a named constant so it can never quietly drop out of the suite.
ESCAPED = ("DECISION: MEMORY.md trimmed.</decision>" + NL
           + '<parameter name="topic">memory-index-window-fit')


@pytest.mark.parametrize("text", [
    SMUGGLED,
    SMUGGLED_NO_TAIL,
    ESCAPED,
    "x.</decision>" + NL + "<topic>slug-with-no-closing-tag",
    "body</parameter>",
    "body</invoke>",
    "a decision.</decision>" + NL + "<because>reasons</because>" + NL + "<context>here</context>",
])
def test_frame_markup_is_detected(text):
    assert _reject_frame_markup(text) is not None


def test_the_escaped_shape_is_the_one_the_first_guard_missed():
    """Regression, and the reason it is worth its own test.

    The first guard had two patterns: a frame terminator at the end, and a closing tag followed by
    COMPLETE sibling pairs. This payload has neither, so it stored cleanly with topic=None and
    keyed supersession silently off. Fixing the reported instance while the class survives is a
    failure this codebase has recorded before; assert the class.
    """
    assert not _FRAME_TAIL.search(ESCAPED)
    assert not _SMUGGLED_PARAMS.search(ESCAPED)
    assert _reject_frame_markup(ESCAPED) == "ends inside an unclosed parameter tag"


@pytest.mark.parametrize("text", [
    # THE CONTROLS. Each of these is a real memory somebody would legitimately write, and a guard
    # that rejects any of them is worse than the bug it fixes.
    "plain memory",
    "use </div> to close the element",
    "a snippet that ends </div> then <br>",   # unclosed HTML tail must NOT trip it

    "the caller must emit </parameter> from outside the value, not inside it",
    "A memory about the bug: the text carried </decision><topic>t</topic> in it, which is why the "
    "guard exists at all.",
    "```xml" + NL + "<root><child>v</child></root>" + NL + "```" + NL + "That is the shipped shape.",
    "",
])
def test_legitimate_text_is_untouched(text):
    assert _reject_frame_markup(text) is None


def test_remember_refuses_and_names_the_remedy(tmp_path):
    m = Inspeximus(path=str(tmp_path / "s.json"))
    with pytest.raises(ValueError) as e:
        m.remember(SMUGGLED)
    msg = str(e.value)
    # The error has to tell the caller what to DO. A refusal that only says "invalid" sends an agent
    # into a retry loop, which is exactly what happened: the same malformed call went out three times.
    assert "one parameter block per argument" in msg
    assert "malformed" in msg


def test_remember_decision_refuses_the_real_payload(tmp_path):
    m = Inspeximus(path=str(tmp_path / "s.json"))
    with pytest.raises(ValueError):
        m.remember_decision(SMUGGLED)
    assert m.recall("wire capture", k=5) == [] or all(
        "</invoke>" not in r.get("text", "") for r in m.recall("wire capture", k=5))


def test_a_correct_call_still_keys_and_supersedes(tmp_path):
    """The control that proves the guard did not break the path it protects."""
    m = Inspeximus(path=str(tmp_path / "s.json"))
    first = m.remember_decision("we use the wire capture", topic="82056-instrument",
                                because="bytes sent cannot be confused with bytes computed")
    second = m.remember_decision("we use the wire capture AND a single-line fixture",
                                 topic="82056-instrument", because="whole-line truncation quantizes")
    assert first != second
    active = [r for r in m.items if r.get("status") == "active"
              and r.get("key") == "decision::82056-instrument"]
    assert len(active) == 1, "keyed supersession must leave exactly one active decision per topic"
    assert "single-line fixture" in active[0]["text"]
