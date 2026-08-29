""""The source is gone" and "I am not standing where it is" are different facts.

Found on our own store on 2026-08-29, while verifying a migration. `check_sources` reported 24
ORPHANED records from one directory and 0 from another -- same store file, same records, same
minute. Every one of the 24 documents existed. The verdict was resolving a RELATIVE locator such
as `CLAUDE.md` against `os.getcwd()`, so it described the caller's working directory rather than
the world.

ORPHANED is not a soft word. It is the report's claim that the evidence behind a memory no longer
exists, and on the AI Act evidence path it is the finding an auditor acts on. A claim that flips on
`cd` is not a measurement.

The library already knows this failure mode: `where_am_i` exists because an MCP stdio server does
not choose its own working directory, the host does, and a cwd-relative store path is "half of my
memories disappeared". The same reasoning was never applied to source resolution.

THE FIX IS NARROWING, NOT SILENCING. An ABSOLUTE path that does not exist is still ORPHANED -- that
verdict means the same thing from every directory. A custom `resolver` still owns its own None.
Only the default resolver's relative-path case moves, into a bucket that says what it knows.
"""
from __future__ import annotations

import hashlib
import os
import tempfile

import pytest

from inspeximus import Inspeximus


def _store(d, **kw):
    return Inspeximus(path=os.path.join(d, "store.json"), **kw)


def _record_with_relative_source(ix, d, name, body):
    """Write a record whose locator is RELATIVE, fingerprinted while cwd makes it resolve."""
    p = os.path.join(d, name)
    open(p, "wb").write(body)
    here = os.getcwd()
    os.chdir(d)
    try:
        ix.remember("a claim resting on a document", key="k", object="1",
                    source={"doc": name})
    finally:
        os.chdir(here)
    ix.flush()
    return p


# ───────────────────────────────────────────── the defect
def test_the_same_store_gives_the_same_verdict_from_two_directories():
    """The headline. `cd` must not create or destroy evidence."""
    d = tempfile.mkdtemp()
    elsewhere = tempfile.mkdtemp()
    ix = _store(d, receipts=True)
    _record_with_relative_source(ix, d, "SOURCE.md", b"the body we fingerprinted")

    here = os.getcwd()
    try:
        os.chdir(d)
        from_home = ix.check_sources()["counts"]
        os.chdir(elsewhere)
        from_away = ix.check_sources()["counts"]
    finally:
        os.chdir(here)

    assert from_away["ORPHANED"] == from_home["ORPHANED"], (
        "check_sources called %d record(s) ORPHANED from %r and %d from %r -- the document exists "
        "in both cases and the only thing that changed was the caller's directory"
        % (from_away["ORPHANED"], elsewhere, from_home["ORPHANED"], d))


def test_an_unresolvable_relative_locator_says_what_it_actually_knows():
    """It is not gone. It is not verified either. Neither word may be borrowed from the other."""
    d = tempfile.mkdtemp()
    elsewhere = tempfile.mkdtemp()
    ix = _store(d, receipts=True)
    _record_with_relative_source(ix, d, "SOURCE.md", b"the body we fingerprinted")

    here = os.getcwd()
    try:
        os.chdir(elsewhere)
        r = ix.check_sources()
    finally:
        os.chdir(here)

    assert r["counts"].get("UNRESOLVED_HERE") == 1, (
        "a relative locator that does not resolve from this base needs its own bucket; "
        "counts were %r" % (r["counts"],))
    assert r["counts"]["FRESH"] == 0, "nothing was verified, so nothing may be called FRESH"
    assert r["checked"] == 0, (
        "an unresolved locator must not inflate `checked` -- it verified nothing")


def test_the_report_names_the_base_its_verdicts_were_measured_against():
    """A base-dependent verdict must carry its base, or a reader cannot audit it."""
    d = tempfile.mkdtemp()
    ix = _store(d, receipts=True)
    _record_with_relative_source(ix, d, "SOURCE.md", b"body")

    here = os.getcwd()
    try:
        os.chdir(d)
        r = ix.check_sources()
    finally:
        os.chdir(here)

    assert os.path.realpath(r["resolution_base"]) == os.path.realpath(d), (
        "the report must say which directory its relative locators resolved against; got %r"
        % (r.get("resolution_base"),))
    assert r["relative_locators"] == 1, (
        "a reader needs to know how much of this report is base-dependent")


# ───────────────────────────────────────────── the controls
def test_a_source_that_is_really_gone_is_still_orphaned():
    """THE CONTROL. If this passes only because ORPHANED stopped being reachable, the fix is a lie.

    An absolute path that does not exist means the same thing from every directory, so it keeps the
    verdict. This test must pass BEFORE and AFTER the change.
    """
    d = tempfile.mkdtemp()
    elsewhere = tempfile.mkdtemp()
    ix = _store(d, receipts=True)
    body = b"soon to be deleted"
    p = os.path.join(d, "GONE.md")
    open(p, "wb").write(body)
    ix.remember("rests on a document", key="k", object="1",
                source={"doc": p, "observed_sha256": hashlib.sha256(body).hexdigest()})
    ix.flush()
    os.remove(p)

    here = os.getcwd()
    try:
        os.chdir(elsewhere)
        c = ix.check_sources()["counts"]
    finally:
        os.chdir(here)

    assert c["ORPHANED"] == 1, (
        "a deleted absolute source is genuinely gone and must keep the ORPHANED verdict; got %r"
        % (c,))
    assert c.get("UNRESOLVED_HERE", 0) == 0, "an absolute path is not base-dependent"


def test_a_custom_resolver_still_owns_its_own_verdict():
    """CONTROL. A caller that supplies a resolver has said how to fetch; None from it means gone."""
    d = tempfile.mkdtemp()
    ix = _store(d, receipts=True)
    _record_with_relative_source(ix, d, "SOURCE.md", b"body")

    c = ix.check_sources(resolver=lambda doc: None)["counts"]
    assert c["ORPHANED"] == 1, (
        "with an explicit resolver the library must not second-guess a None; got %r" % (c,))


def test_a_resolvable_relative_source_still_reports_fresh():
    """CONTROL. The fix must not turn the working case into an absence."""
    d = tempfile.mkdtemp()
    ix = _store(d, receipts=True)
    _record_with_relative_source(ix, d, "SOURCE.md", b"body")

    here = os.getcwd()
    try:
        os.chdir(d)
        c = ix.check_sources()["counts"]
    finally:
        os.chdir(here)

    assert c["FRESH"] == 1, "a locator that resolves and matches is still FRESH; got %r" % (c,)
    assert c.get("UNRESOLVED_HERE", 0) == 0
