"""An edit drops a record's derived caches only when it touches a field the index reads (3.18, perf gate sleep arm).

R-5 (AUDIT-B) made every edit drop the per-id caches (`_tok_cache`, `_sig_cache`, `_tc_cache`), so a held handle stops
matching a record by its words before an in-place edit. That was right for `text`, `key` and `meta`, the fields
`_index_text` reads, and more than needed for the rest: sleep() appends 2,960 `links` and sets `status` and `good`, and
then reads the same records' tokens again. The perf gate measured it as +80 regex calls and +160 tracked gets.

Each container under a record now carries the record's top-level field. These tests check both directions: an edit
outside the index keeps the cached tokens, and an edit inside it (a nested one included, and a container moved into
`meta` from another field) still drops them, so a held handle answers from the record as it is now.
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

#: A ROW-STORE-ONLY EXPECTATION, NOT A SKIP (AUDIT-A F-2, F-2b). The whole-file JSON format does not track its records:
#: an in-place edit is not declared, so it drops no cache and a flush does not write it (3.17.1 the same, measured
#: 2026-10-10). Strict: the JSON case must fail, and passes the day that format starts tracking, which then needs a look.
ROW_ONLY = pytest.mark.xfail(strict=True, reason="row store only: the whole-file JSON format does not track its records")
FORMATS = [pytest.param("json", marks=ROW_ONLY), "db"]


_MADE = []          # (path, format) of every store this test created, checked when the test ends


@pytest.fixture(autouse=True)
def _keep_the_format():
    """AUDIT-A F-2: a "json" case must run on the whole-file JSON format. The variable has to stay set for the whole
    test, because an open without it converts a JSON store to rows; at the end every store this test made must still
    carry its format's header."""
    old = os.environ.get("INSPEXIMUS_STORE_FORMAT")
    del _MADE[:]
    yield
    if old is None:
        os.environ.pop("INSPEXIMUS_STORE_FORMAT", None)
    else:
        os.environ["INSPEXIMUS_STORE_FORMAT"] = old
    for p, fmt in _MADE:
        _assert_format(p, fmt)


def _pin(fmt):
    """Pin the format for the rest of the test: "json" is the whole-file JSON format, "db" the row store."""
    if fmt == "json":
        os.environ["INSPEXIMUS_STORE_FORMAT"] = "json"
    else:
        os.environ.pop("INSPEXIMUS_STORE_FORMAT", None)


def _assert_format(p, fmt):
    with open(p, "rb") as fh:
        head = fh.read(16)
    want = b"[" if fmt == "json" else b"SQLite format 3"
    assert head.startswith(want), "CONTROL: %s is not in the %s format (%r)" % (p, fmt, head)


def _held(tmp_path, fmt):
    p = str(tmp_path / ("s." + fmt))
    _pin(fmt)
    m = Inspeximus(path=p)
    for i in range(6):
        m.remember("filler note number %d about gardening" % i)
    m.remember("the deploy window is friday", key="deploy")
    m.remember_decision("we ship on fridays", because="the team is in", topic="release-day",
                        context="quarterly planning")
    m.flush()
    _assert_format(p, fmt)
    _MADE.append((p, fmt))
    h = Inspeximus(path=p)
    h._ix_held = True
    for _ in range(3):
        h.recall("deploy window", k=5)
        h.recall("release day", k=5)
    return h


def _rec(h, key):
    rec = [r for r in h._items if r.get("key") == key][0]
    h._rec_tokens(rec)                              # the cache holds this record's tokens before the edit
    assert rec["id"] in h._tok_cache, "CONTROL: the record's tokens are cached before the edit"
    return rec


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("edit", ["links", "status", "good", "links_nested"])
def test_an_edit_outside_the_index_keeps_the_cached_tokens(tmp_path, fmt, edit):
    h = _held(tmp_path, fmt)
    rec = _rec(h, "deploy")
    rev = h._rev
    if edit == "links":
        rec.setdefault("links", [])
        rec["links"].append("someid")
    elif edit == "links_nested":
        rec["links"] = [{"to": "a"}]
        h._rec_tokens(rec)
        rec["links"][0]["to"] = "b"
    elif edit == "status":
        rec["status"] = "active"
    else:
        rec["good"] = 2
    assert h._rev > rev, "CONTROL: the edit was seen as an edit"
    assert rec["id"] in h._tok_cache, "an edit to %s, which the index does not read, dropped the cached tokens" % edit


@pytest.mark.parametrize("fmt", FORMATS)
def test_an_edit_to_the_text_drops_the_tokens_and_recall_follows(tmp_path, fmt):
    h = _held(tmp_path, fmt)
    rec = _rec(h, "deploy")
    rec["text"] = "the deploy window is monday"
    assert rec["id"] not in h._tok_cache
    assert any("monday" in r["text"] for r in h.recall("monday deploy", k=5))


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_nested_edit_to_a_decisions_context_drops_the_tokens(tmp_path, fmt):
    """`meta.context` is part of a decision's index text: a nested edit there must reach the cache."""
    h = _held(tmp_path, fmt)
    rec = _rec(h, "decision::release-day")
    rec["meta"]["context"] = "zeppelin hangar logistics"
    assert rec["id"] not in h._tok_cache, "a nested edit to meta.context left the old tokens cached"
    assert "zeppelin" in h._rec_tokens(rec)


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_container_moved_into_meta_drops_the_tokens_on_its_next_edit(tmp_path, fmt):
    """A tracked dict read under one field and assigned under `meta` must not keep reporting its old field."""
    h = _held(tmp_path, fmt)
    rec = _rec(h, "decision::release-day")
    rec["aux"] = {"context": "unused"}
    moved = rec["aux"]                               # tracked, under "aux"
    rec["meta"] = moved                              # now under "meta"
    h._rec_tokens(rec)
    assert rec["id"] in h._tok_cache, "CONTROL: tokens cached after the move"
    moved["context"] = "zeppelin hangar logistics"
    assert rec["id"] not in h._tok_cache, "a container moved into meta kept the field it was read under"


def test_the_field_set_names_every_field_the_index_text_reads():
    """`_INDEX_FIELDS` must cover what `_index_text` reads, or an edit to a newly read field would keep stale tokens."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(core._index_text)))
    read = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "rec"
                and node.args and isinstance(node.args[0], ast.Constant)):
            read.add(node.args[0].value)
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "rec"
                and isinstance(node.slice, ast.Constant)):
            read.add(node.slice.value)
    assert read, "CONTROL: the scan found the fields _index_text reads"
    assert read <= core._INDEX_FIELDS, "_index_text reads %s, which _INDEX_FIELDS does not name" % (read - core._INDEX_FIELDS)


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_meta_key_that_is_not_the_context_keeps_the_tokens(tmp_path, fmt):
    """A state toggle sets `meta.superseded_by_toggle`: the index does not read it, so the tokens stay."""
    h = _held(tmp_path, fmt)
    rec = _rec(h, "decision::release-day")
    rev = h._rev
    rec["meta"]["superseded_by_toggle"] = "someid"
    assert h._rev > rev, "CONTROL: the edit was seen as an edit"
    assert rec["id"] in h._tok_cache, "an edit to meta.superseded_by_toggle dropped the cached tokens"
    rec["meta"].pop("context")
    assert rec["id"] not in h._tok_cache, "removing meta.context left the old tokens cached"
    assert "quarterly" not in h._rec_tokens(rec)


@pytest.mark.parametrize("fmt", FORMATS)
def test_touch_drops_the_tokens_only_when_it_cannot_see_the_edit(tmp_path, fmt):
    """`_touch` on a tracked record adds nothing: its edits already reached the cache. On an id, it drops them."""
    h = _held(tmp_path, fmt)
    rec = _rec(h, "deploy")
    h._touch(rec)
    assert rec["id"] in h._tok_cache, "_touch on a tracked record dropped tokens no edit had made stale"
    h._touch(rec["id"])
    assert rec["id"] not in h._tok_cache, "_touch by id, which cannot say what changed, kept the cached tokens"
