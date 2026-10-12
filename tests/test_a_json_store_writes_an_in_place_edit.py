"""A whole-file JSON store writes an in-place edit on flush(), and a held handle sees it (3.19, backlog item 7).

Measured on 3.17.1 and 3.18.0 (2026-10-10): with `INSPEXIMUS_STORE_FORMAT=json`, `rec["text"] = ...` left the store
clean, so `flush()` wrote nothing, not even after `_touch(rec)`, and the edit was gone at the next open. The row store
tracked its records and wrote the edit. 3.19 tracks the records of every format, so the edit marks the store, the
whole file is written once at the next save, and the set of edited ids is cleared by that save as a row save clears it.

Each case runs on the JSON format, on an encrypted store (also a whole-file format) and on the row store as the control
that already held on 3.18.0.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

try:
    import cryptography  # noqa: F401
    _CRYPTO = True
except Exception:                                   # the crypto extra is optional
    _CRYPTO = False

FORMATS = ["json", pytest.param("enc", marks=pytest.mark.skipif(not _CRYPTO, reason="needs inspeximus[crypto]")), "db"]
_PW = "correct horse"


@pytest.fixture(autouse=True)
def _keep_the_format():
    old = os.environ.get("INSPEXIMUS_STORE_FORMAT")
    yield
    if old is None:
        os.environ.pop("INSPEXIMUS_STORE_FORMAT", None)
    else:
        os.environ["INSPEXIMUS_STORE_FORMAT"] = old


def _open(p, fmt):
    if fmt == "json":
        os.environ["INSPEXIMUS_STORE_FORMAT"] = "json"
    else:
        os.environ.pop("INSPEXIMUS_STORE_FORMAT", None)
    return Inspeximus(path=p, encrypt_passphrase=_PW) if fmt == "enc" else Inspeximus(path=p)


def _assert_format(p, fmt):
    with open(p, "rb") as fh:
        head = fh.read(16)
    want = {"json": b"[", "enc": core._INSPEXIMUS_ENC_MAGIC, "db": b"SQLite format 3"}[fmt]
    assert head.startswith(want), "CONTROL: %s is not in the %s format (%r)" % (p, fmt, head)


def _store(tmp_path, fmt, n=6):
    p = str(tmp_path / ("s." + fmt))
    m = _open(p, fmt)
    for i in range(n):
        m.remember("filler note number %d about gardening" % i)
    m.remember("the deploy window is friday", key="deploy")
    m.remember_decision("we ship on fridays", because="the team is in", topic="release-day",
                        context="quarterly planning")
    m.flush()
    _assert_format(p, fmt)
    return p


def _rec(m, key):
    return [r for r in m._items if r.get("key") == key][0]


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("edit", ["text", "status", "meta_context", "tags_append", "ior"])
def test_an_in_place_edit_reaches_the_file_on_flush(tmp_path, fmt, edit):
    p = _store(tmp_path, fmt)
    h = _open(p, fmt)
    rec = _rec(h, "deploy") if edit != "meta_context" else _rec(h, "decision::release-day")
    assert not h._dirty, "CONTROL: a freshly opened handle has nothing to write"
    if edit == "text":
        rec["text"] = "the release slot is monday"
    elif edit == "status":
        rec["status"] = "superseded"
    elif edit == "meta_context":
        rec["meta"]["context"] = "zeppelin hangar logistics"
    elif edit == "tags_append":
        rec.setdefault("tags", [])
        rec["tags"].append("zeppelin")
    else:
        rec |= {"text": "the release slot is monday"}
    h.flush()
    fresh = _open(p, fmt)
    got = _rec(fresh, "deploy") if edit != "meta_context" else _rec(fresh, "decision::release-day")
    want = {"text": ("text", "the release slot is monday"), "ior": ("text", "the release slot is monday"),
            "status": ("status", "superseded")}
    if edit in want:
        f, v = want[edit]
        assert got.get(f) == v, "flush() did not write an in-place edit of %s" % f
    elif edit == "meta_context":
        assert got["meta"]["context"] == "zeppelin hangar logistics", "flush() did not write a nested meta edit"
    else:
        assert "zeppelin" in (got.get("tags") or []), "flush() did not write a nested list append"
    _assert_format(p, fmt)


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_flushed_edit_does_not_pin_this_handles_copy_over_a_peers_later_edit(tmp_path, fmt):
    """The save clears the edited ids. Left set, a refresh would keep this handle's copy over the peer's newer one."""
    p = _store(tmp_path, fmt)
    h = _open(p, fmt)
    _rec(h, "deploy")["text"] = "edited once by the held handle"
    h.flush()
    assert not h._touched, "the save left the edited ids set"
    peer = _open(p, fmt)
    assert _rec(peer, "deploy")["text"] == "edited once by the held handle", "CONTROL: the first edit is on disk"
    _rec(peer, "deploy")["text"] = "edited later by a peer"
    peer.flush()
    h.refresh()
    assert _rec(h, "deploy")["text"] == "edited later by a peer", "the held handle kept its own older copy"


@pytest.mark.parametrize("fmt", ["json", "db"])
def test_a_readers_note_leaves_a_json_store_clean(tmp_path, fmt):
    """A recall writes `_stale_derived` on its candidates. That note is never stored, so it must not mark the store."""
    p = _store(tmp_path, fmt)
    h = _open(p, fmt)
    rec = _rec(h, "deploy")
    rec["_stale_derived"] = True
    assert not h._dirty and not h._touched, "a reader's note marked the store for a write"


def _count_whole_file_writes(monkeypatch):
    n = {"dump": 0, "replace": 0}
    real_dump, real_replace = core._dump_store, core._durable_replace

    def dump(items):
        n["dump"] += 1
        return real_dump(items)

    def replace(path, payload, *a, **k):
        n["replace"] += 1
        return real_replace(path, payload, *a, **k)
    monkeypatch.setattr(core, "_dump_store", dump)
    monkeypatch.setattr(core, "_durable_replace", replace)
    return n


@pytest.mark.parametrize("fmt", ["json", pytest.param("enc", marks=pytest.mark.skipif(not _CRYPTO, reason="crypto"))])
def test_an_edit_loop_writes_the_whole_file_once(tmp_path, fmt, monkeypatch):
    """Tracking marks the store; it does not save. A loop that edits and declares every record writes the file once,
    at the flush, and a second flush with nothing new writes nothing."""
    p = _store(tmp_path, fmt, n=200)
    h = _open(p, fmt)
    n = _count_whole_file_writes(monkeypatch)
    for r in list(h._items):
        r["good"] = (r.get("good") or 0) + 1
        h._touch(r)
    assert n == {"dump": 0, "replace": 0}, "an edit or a _touch wrote the file: %r" % n
    h.flush()
    assert n == {"dump": 1, "replace": 1}, "one flush after the loop must write the file once: %r" % n
    h.flush()
    assert n == {"dump": 1, "replace": 1}, "a flush with nothing new wrote the file again: %r" % n
    fresh = _open(p, fmt)
    assert all((r.get("good") or 0) >= 1 for r in fresh._items), "CONTROL: the loop's edits are on disk"


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_same_id_replacement_through_an_alias_reaches_the_file(tmp_path, fmt):
    """`a[i] = dict(a[i], ...)` through an alias of the list. On 3.18.0 the caches saw it (AUDIT-B R-3) and the file did
    not, in the row store as well: the new version was a plain dict whose id was already in the baseline."""
    p = _store(tmp_path, fmt)
    h = _open(p, fmt)
    a = h._items
    i = a.index(_rec(h, "deploy"))
    a[i] = dict(a[i], text="the release slot is monday")
    h.flush()
    assert _rec(_open(p, fmt), "deploy")["text"] == "the release slot is monday", "flush() lost a same-id replacement"
    _rec(h, "deploy")["text"] = "and then tuesday"           # the new version is tracked from here
    h.flush()
    assert _rec(_open(p, fmt), "deploy")["text"] == "and then tuesday", "an edit to the replacement was not written"


@pytest.mark.parametrize("fmt", FORMATS)
def test_touch_declares_an_edit_tracking_could_not_see(tmp_path, fmt):
    """An edit made past the wrapper (`dict.__setitem__`) is declared with `_touch(id)`, and flush() writes it."""
    p = _store(tmp_path, fmt)
    h = _open(p, fmt)
    rec = _rec(h, "deploy")
    dict.__setitem__(rec, "text", "the release slot is monday")
    assert not h._dirty, "CONTROL: the edit went past the wrapper"
    h._touch(rec["id"])
    h.flush()
    assert _rec(_open(p, fmt), "deploy")["text"] == "the release slot is monday", "flush() ignored a declared edit"
