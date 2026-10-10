"""A held handle (the MCP server, the prompt daemon) answers from the record as it is now, after any change to it.

AUDIT-B's 3.18 integration review, measured on feat-318 and on rc-317 alike:

- R-3: a record replaced through an alias by another version with the same id (`a[i] = dict(a[i], status=...)`) went on
  answering from a warm handle. The replacement removed no id, so nothing was pruned and the recall index kept the old
  version.
- R-5: the per-id token cache was never invalidated, so a held handle matched a record by its words before an in-place
  edit. And `refresh()` did not adopt a peer's retire, credit or in-place edit: a recall's reader note marked the
  record as this handle's edit, and the merge keeps this handle's copy of an edited record over the disk's.

Each test warms the handle past the recall index's build threshold first, so the cached state exists when the change
lands, and checks the answer against a fresh process's.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus.core import Inspeximus  # noqa: E402

FORMATS = ["json", "db"]
#: A ROW-STORE-ONLY EXPECTATION, NOT A SKIP (AUDIT-A F-2, F-2b). The whole-file JSON format does not track its records:
#: an in-place edit is not declared, so it drops no cache and a flush does not write it (3.17.1 the same, measured
#: 2026-10-10). Strict: the JSON case must fail, and passes the day that format starts tracking, which then needs a look.
ROW_ONLY = pytest.mark.xfail(strict=True, reason="row store only: the whole-file JSON format does not track its records")
FORMATS_ROW_ONLY = [pytest.param("json", marks=ROW_ONLY), "db"]


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


def _store(tmp_path, fmt):
    p = str(tmp_path / ("s." + fmt))
    _pin(fmt)
    m = Inspeximus(path=p)
    for i in range(6):
        m.remember("filler note number %d about gardening" % i)
    m.remember("the deploy window is friday", key="deploy")
    m.flush()
    _assert_format(p, fmt)
    _MADE.append((p, fmt))
    return p


def _warm(p, fmt="db"):
    m = Inspeximus(path=p)
    m._ix_held = True
    for _ in range(3):
        assert any("friday" in r["text"] for r in m.recall("deploy window", k=5)), "CONTROL: the record answers"
    if fmt == "db":                                 # the whole-file JSON format builds no recall index
        assert m._recall_ix and m._tok_cache, "CONTROL: the handle holds an index entry and cached tokens"
    return m


def _deploy(m):
    return [r for r in m._items if "deploy" in (r.get("key") or "")][0]


def _texts(m, q):
    return [r["text"] for r in m.recall(q, k=5)]


@pytest.mark.parametrize("change", [{"status": "superseded"}, {"status": "retired"}, {"text": "[redacted]"},
                                    {"text": ""}])
def test_a_same_id_replacement_through_an_alias_is_seen(tmp_path, change):
    """AUDIT-B's probe: `a[i] = other version of the record`, through an alias of the list."""
    m = _warm(_store(tmp_path, "db"))
    a = m._items
    i = a.index(_deploy(m))
    a[i] = dict(a[i], **change)
    assert not any("friday" in t for t in _texts(m, "deploy window")), "a replaced record still answers"


def test_an_in_place_edit_answers_by_its_new_words(tmp_path):
    m = _warm(_store(tmp_path, "db"))
    _deploy(m)["text"] = "the release slot is monday"
    assert "the release slot is monday" in _texts(m, "release slot monday"), "the new words do not find the record"
    assert "the release slot is monday" not in _texts(m, "deploy window friday"), "the old words still find it"


@pytest.mark.parametrize("fmt", FORMATS)
def test_refresh_adopts_a_peers_retire(tmp_path, fmt):
    p = _store(tmp_path, fmt)
    m = _warm(p, fmt)
    peer = Inspeximus(path=p)
    peer.retire("deploy", "obsolete")
    peer.flush()
    assert m.refresh()["changed"], "CONTROL: the handle sees the file moved"
    assert _deploy(m).get("status") == _deploy(Inspeximus(path=p)).get("status") == "superseded"
    assert not any("friday" in t for t in _texts(m, "deploy window")), "a retired record still answers"


@pytest.mark.parametrize("fmt", FORMATS_ROW_ONLY)
def test_refresh_adopts_a_peers_in_place_edit(tmp_path, fmt):
    p = _store(tmp_path, fmt)
    m = _warm(p, fmt)
    peer = Inspeximus(path=p)
    _deploy(peer)["text"] = "the release slot is monday"
    peer.flush()
    m.refresh()
    assert _deploy(m)["text"] == "the release slot is monday"
    assert "the release slot is monday" in _texts(m, "release slot monday"), "the new words do not find the record"
    assert not any("friday" in t for t in _texts(m, "deploy window friday")), "the old words still answer"


@pytest.mark.parametrize("fmt", FORMATS)
def test_refresh_adopts_a_peers_credit(tmp_path, fmt):
    p = _store(tmp_path, fmt)
    m = _warm(p, fmt)
    peer = Inspeximus(path=p)
    peer.credit([_deploy(peer)["id"]], "good")
    peer.flush()
    m.refresh()
    fresh = _deploy(Inspeximus(path=p))
    assert fresh.get("good"), "CONTROL: the peer's credit is on disk"
    assert _deploy(m).get("good") == fresh.get("good"), "the held handle kept its own copy"


def test_a_readers_note_is_not_an_edit(tmp_path):
    """The cause of the refresh() half: `_stale_derived`, written on every candidate of every recall, marked the record
    as this handle's edit. It is never stored, so it marks nothing."""
    m = Inspeximus(path=_store(tmp_path, "db"))
    rev = m._rev
    m.recall("deploy window", k=5)
    assert "_stale_derived" in _deploy(m), "CONTROL: the recall wrote its note on the record"
    assert not m._touched and m._rev == rev, "a reader's note counted as an edit"
