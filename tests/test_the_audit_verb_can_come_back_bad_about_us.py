"""`inspeximus audit` is the only number we publish that is about the caller's data, not ours.

WHY IT EXISTS. A curator declined this project in Snseam/awesome-agent-memory#19 with the reason
attached: "GitHub-only or vendor/self-claimed benchmark signals". He was right from where he stood.
Every figure in the README is ours, measured on our store, and no amount of adding figures answers
that. `audit_the_audits` does, because it corrupts a copy of the READER's records and reports which
of our verification surfaces noticed. It had no CLI verb, so the only way to reach it was to write
Python against library internals, which is not a thing a stranger evaluating a package does.

THE CONTRACT THESE TESTS PIN, and it is the part that makes the command worth trusting:

  * The exit code is non-zero ONLY when a surface MISSED a corruption or was blind on a fixture.
    Both are defects in inspeximus. A store that can demonstrate few surfaces is a fact about that
    store, and punishing the caller for it would turn an honest report into a scolding.
  * The two rows that are about US print even when they are zero. A report that shows a row only
    when it is non-zero teaches the reader that no row means nothing to see, and silence is then
    indistinguishable from absence.
  * The live store is never written.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus
from inspeximus import cli as _cli


def _store(n=4, **kw):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "s.json")
    ix = Inspeximus(path=path, receipts=True, **kw)
    for i in range(n):
        ix.remember("a fact numbered %d" % i, key="k%d" % i, object=str(i))
    ix.flush()
    return path


def _run(path, *globals_):
    """Run the verb, capture stdout, return (exit_code, text).

    `--json` is a GLOBAL flag on this CLI and has to precede the subcommand; passing it after `audit`
    is an argparse error, not a missing feature. Worth pinning here, because the natural guess is the
    other order and the failure message names the flag rather than its position.
    """
    argv, out = sys.argv, sys.stdout
    sys.argv = ["inspeximus", "--path", path, *globals_, "audit"]
    sys.stdout = io.StringIO()
    try:
        code = _cli.main()
        return int(code or 0), sys.stdout.getvalue()
    finally:
        sys.argv, sys.stdout = argv, out


def test_the_verb_exists_and_reports_on_the_callers_own_store():
    code, text = _run(_store())
    assert "verification surfaces" in text, text
    assert "demonstrated on your data" in text, text
    assert code == 0, text


def test_the_two_rows_about_us_print_even_at_zero():
    """The point of the row is that a reader can see it said zero, not that it vanished."""
    _code, text = _run(_store())
    assert "MISSED a corruption" in text, text
    assert "blind even on a fixture" in text, text
    assert "  0 MISSED" in text.replace("    0 MISSED", "  0 MISSED"), text


def test_json_mode_returns_the_whole_report():
    code, text = _run(_store(), "--json")
    doc = json.loads(text)
    assert doc["surfaces"]["available"] >= 20
    assert "missed" in doc and "blind_even_on_a_fixture" in doc
    assert code == 0


def test_a_store_that_can_demonstrate_little_still_exits_zero():
    """CONTROL, and the whole design decision. A tiny store with no receipts demonstrates far fewer
    surfaces. That is a fact about the store; the command must not treat it as a failure."""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "bare.json")
    ix = Inspeximus(path=path)                      # no receipts, no embedder
    ix.remember("one fact", key="k", object="1")
    ix.flush()
    code, text = _run(path)
    assert code == 0, text
    assert "cannot exercise" in text or "unanswerable" in text or "fixture" in text, text


def test_it_exits_non_zero_when_a_surface_MISSES(monkeypatch):
    """CONTROL in the other direction. Without this the zero above proves only that nothing failed,
    never that a failure would be reported. A surface that always says clean must fail the command."""
    monkeypatch.setattr(Inspeximus, "verify_writes", lambda self: (True, []))
    code, text = _run(_store())
    assert code == 1, text
    assert "MISSED a corruption" in text


def test_the_live_store_is_not_written():
    path = _store()
    before = io.open(path, "rb").read()
    _run(path)
    assert io.open(path, "rb").read() == before, "the audit wrote to the caller's store"
