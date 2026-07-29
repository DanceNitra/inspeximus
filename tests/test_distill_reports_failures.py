"""`distill_and_remember` counted a failed write as nothing at all.

Found by passing a `source` in the wrong shape -- a bare string where `remember()` requires a dict. Every
item raised, a bare `except Exception: continue` swallowed it, and the call returned:

    {"captured": 0, "decisions": 0, "facts": 0, "dropped": 0, "ids": []}

which reads as "the transcript had nothing worth keeping". The truth was "everything was extracted and
then thrown away". Same class as the rest of this month's audit -- a surface reporting a clean-looking
result about work that failed -- on the one path where the caller has no other way to find out.

`dropped` alone would still not say why, and the two reasons are OPPOSITE: an unsupported item is the
hallucination guard working as designed; an errored one is a bug in the caller's arguments. So failures
are counted separately and NAMED.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402

TEXT = "alice said she prefers 9OakAve"


def _store():
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), receipts=True)


def _one_supported_fact(prompt, text):
    return [{"type": "fact", "text": "alice prefers 9OakAve", "topic": "alice::pref", "support": TEXT}]


def test_a_write_that_raised_is_reported_not_silent():
    """THE defect. A wrong-shaped source made every item raise and the call said nothing."""
    m = _store()
    out = m.distill_and_remember(TEXT, _one_supported_fact, source="hr/alice")   # str, not dict
    assert out["captured"] == 0
    assert out["failed"] == 1
    assert any("must be a dict" in e for e in out["errors"]), out
    assert "ValueError" in out["errors"][0]


def test_the_correct_shape_still_captures_and_stays_erasable():
    """CONTROL. A change that reported failures on everything would pass the test above."""
    m = _store()
    out = m.distill_and_remember(TEXT, _one_supported_fact, source={"doc": "hr/alice"})
    assert out["captured"] == 1 and out["facts"] == 1
    assert "errors" not in out and "failed" not in out, "a clean run must not carry an error block"
    assert m.forget_subject("hr/alice", request_id="D", basis="art17")["erased"] == 1


def test_an_unsupported_item_is_dropped_not_failed():
    """CONTROL, and the distinction that makes the field worth having: `dropped` is the hallucination
    guard doing its job, `failed` is the caller's bug. Collapsing them would hide one behind the other."""
    m = _store()
    out = m.distill_and_remember(
        "an unrelated transcript",
        lambda p, t: [{"type": "fact", "text": "invented claim", "topic": "x",
                       "support": "not in the text"}],
        source={"doc": "hr/alice"})
    assert out["dropped"] == 1
    assert "failed" not in out and "errors" not in out


def test_the_cli_distill_produces_records_that_can_be_erased(tmp_path, monkeypatch, capsys):
    """END TO END through the CLI, because a structural check on the argparse tree let a real defect
    survive: my first version of this fix passed `a.source` straight through, and `remember()` requires a
    dict. Every item would have raised, the command would have printed `captured: 0`, and a test that only
    asserted the flag EXISTS would have stayed green.

    `default_distiller()` is imported inside the branch, so it can be replaced without a live LLM
    endpoint -- which is the difference between a testable command and one that is only ever tested by
    hand."""
    import inspeximus
    import inspeximus.cli as cli

    monkeypatch.setattr(inspeximus, "default_distiller", lambda: _one_supported_fact, raising=False)
    src = tmp_path / "transcript.txt"
    src.write_text(TEXT, encoding="utf-8")
    store = tmp_path / "s.json"

    rc = cli.main(["--path", str(store), "distill", "--file", str(src), "--source", "hr/alice"])
    assert rc == 0, capsys.readouterr()

    m = Inspeximus(path=str(store), receipts=True)
    assert any("distilled" in (r.get("tags") or []) for r in m.items), "nothing was captured"
    assert m.forget_subject("hr/alice", request_id="D", basis="art17")["erased"] >= 1, \
        "a transcript distilled through the CLI is not reachable by the subject it is about"


def test_the_cli_distill_without_a_source_stays_unreachable(tmp_path, monkeypatch, capsys):
    """CONTROL, and the design decision: the CLI never invents a subject the caller did not supply."""
    import inspeximus
    import inspeximus.cli as cli

    monkeypatch.setattr(inspeximus, "default_distiller", lambda: _one_supported_fact, raising=False)
    src = tmp_path / "transcript.txt"
    src.write_text(TEXT, encoding="utf-8")
    store = tmp_path / "s.json"

    assert cli.main(["--path", str(store), "distill", "--file", str(src)]) == 0, capsys.readouterr()
    m = Inspeximus(path=str(store), receipts=True)
    assert m.forget_subject("hr/alice", request_id="D", basis="art17")["erased"] == 0


def test_every_item_fails_together_because_the_bad_argument_is_shared():
    """Two items, both lost, and the count says two -- the error block must not collapse to one line and
    lose how much went.

    WHY THERE IS NO PARTIAL-FAILURE TEST, stated rather than quietly omitted. Two attempts to build one
    failed for the same instructive reason: distill coerces each item's text with `str(...)` and passes
    no `value`, so a per-item payload cannot make `remember()` raise. `max_text` truncates rather than
    raising; a dict `text` is stringified. The realistic failure in this path is the SHARED argument --
    a wrong-shaped `source` -- which takes every item with it. Contriving an artificial per-item raise
    would have tested a mode that does not occur."""
    def two_facts(prompt, text):
        return [{"type": "fact", "text": "alice prefers 9OakAve", "topic": "alice::pref",
                 "support": TEXT},
                {"type": "fact", "text": "alice also prefers mornings", "topic": "alice::when",
                 "support": TEXT}]

    m = _store()
    out = m.distill_and_remember(TEXT, two_facts, source="hr/alice")
    assert out["captured"] == 0
    assert out["failed"] == 2, "the count must say how many were lost, not merely that something was"
    assert len(out["errors"]) == 2
