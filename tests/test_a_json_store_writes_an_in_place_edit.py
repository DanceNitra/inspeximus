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


def _edit_inside_the_write(monkeypatch, h, fmt, edit, at):
    """Run `edit` after the save has serialised the records and before it returns: right after the serialisation or
    at the whole-file replace for JSON and encrypted stores, after the row writer's commit for the row store."""
    if fmt == "db":
        real = h._save_rows_retrying

        def rows(*a, **k):
            res = real(*a, **k)
            edit()
            return res
        monkeypatch.setattr(h, "_save_rows_retrying", rows)
    elif at == "dump":
        real_dump = core._dump_store

        def dump(items):
            data = real_dump(items)
            edit()
            return data
        monkeypatch.setattr(core, "_dump_store", dump)
    else:
        real = core._durable_replace

        def replace(path, payload, *a, **k):
            edit()
            return real(path, payload, *a, **k)
        monkeypatch.setattr(core, "_durable_replace", replace)


@pytest.mark.parametrize("fmt,at", [("json", "dump"), ("json", "replace"),
                                    pytest.param("enc", "dump", marks=pytest.mark.skipif(not _CRYPTO, reason="crypto")),
                                    pytest.param("enc", "replace", marks=pytest.mark.skipif(not _CRYPTO, reason="crypto")),
                                    ("db", "commit")])
def test_an_edit_made_while_a_save_writes_is_written_by_the_next_save(tmp_path, fmt, at, monkeypatch):
    """AUDIT-A F-1 (3.19): the save cleared every edited id after its write, so an edit that landed between the
    serialisation and the clear was dropped: the store read clean and the next flush() wrote nothing. The save now
    clears only the ids it serialised. The window is forced: the edit runs inside the write."""
    p = _store(tmp_path, fmt)
    h = _open(p, fmt)
    _rec(h, "deploy")["text"] = "the release slot is monday"          # the edit this save writes
    fired = []

    def late():
        if not fired:
            fired.append(1)
            _rec(h, "decision::release-day")["status"] = "superseded"   # lands after serialisation
    _edit_inside_the_write(monkeypatch, h, fmt, late, at)
    h.flush()
    assert fired, "CONTROL: the forced window was reached"
    assert _rec(_open(p, fmt), "deploy")["text"] == "the release slot is monday", "CONTROL: the first edit was written"
    assert _rec(_open(p, fmt), "decision::release-day")["status"] != "superseded", \
        "CONTROL: the late edit missed the serialised records, so the window was real"
    monkeypatch.undo()
    rid = _rec(h, "decision::release-day")["id"]
    assert h._dirty and rid in h._touched, "the save cleared an edit it did not write"
    h.flush()
    assert _rec(_open(p, fmt), "decision::release-day")["status"] == "superseded", \
        "an edit made while a save wrote was lost"

@pytest.mark.parametrize("fmt,at", [("json", "dump"), ("json", "replace"),
                                    pytest.param("enc", "dump", marks=pytest.mark.skipif(not _CRYPTO, reason="crypto")),
                                    pytest.param("enc", "replace", marks=pytest.mark.skipif(not _CRYPTO, reason="crypto")),
                                    ("db", "commit")])
def test_a_second_edit_of_a_record_the_save_is_writing_is_written_by_the_next_save(tmp_path, fmt, at, monkeypatch):
    """AUDIT-A F-9 (3.19): the save copied the edited IDS, not their versions. An id that the save was already writing
    and that was edited again after the serialisation was still cleared, so the second edit was lost. Each mark of an
    id now moves its edit generation, and the save clears an id only if its generation did not move."""
    p = _store(tmp_path, fmt)
    h = _open(p, fmt)
    _rec(h, "deploy")["text"] = "the release slot is monday"          # the first edit, which this save writes
    fired = []

    def again():
        if not fired:
            fired.append(1)
            _rec(h, "deploy")["text"] = "the release slot is tuesday"   # the same id, after the serialisation
    _edit_inside_the_write(monkeypatch, h, fmt, again, at)
    h.flush()
    assert fired, "CONTROL: the forced window was reached"
    assert _rec(_open(p, fmt), "deploy")["text"] == "the release slot is monday",         "CONTROL: the save wrote the first edit and missed the second, so the window was real"
    monkeypatch.undo()
    rid = _rec(h, "deploy")["id"]
    assert h._dirty and rid in h._touched, "the save cleared an id whose record was edited again during the write"
    h.flush()
    assert _rec(_open(p, fmt), "deploy")["text"] == "the release slot is tuesday",         "a second edit of a record the save was writing was lost"


@pytest.mark.parametrize("fmt", ["json", pytest.param("enc", marks=pytest.mark.skipif(not _CRYPTO, reason="crypto"))])
def test_an_edit_between_the_record_copies_and_the_lock_is_written_by_the_next_save(tmp_path, fmt, monkeypatch):
    """AUDIT-A F-10 (3.19): a whole-file save copies each record into `slim`, and it took the edit-generation snapshot
    after the copies. An edit in between moved the generation before the snapshot, so the snapshot held the new
    generation while the file got the copy from before the edit, and the save cleared it. Forced here: the edit runs at
    the save's stat-signature check, after the copies (inside the store lock since F-11) and before the write."""
    p = _store(tmp_path, fmt)
    h = _open(p, fmt)
    _rec(h, "deploy")["text"] = "the release slot is monday"
    fired = []
    real_sig = Inspeximus._stat_sig

    def editing_sig(self, *a, **k):
        if self is h and not fired:
            fired.append(1)
            _rec(h, "deploy")["text"] = "the release slot is tuesday"   # after `slim`, before the write
        return real_sig(self, *a, **k)
    monkeypatch.setattr(Inspeximus, "_stat_sig", editing_sig)
    h.flush()
    assert fired, "CONTROL: the forced window was reached"
    assert _rec(_open(p, fmt), "deploy")["text"] == "the release slot is monday", \
        "CONTROL: the file got the copy from before the second edit, so the window was real"
    monkeypatch.undo()
    rid = _rec(h, "deploy")["id"]
    assert h._dirty and rid in h._touched, "the save cleared an edit made after it copied the records"
    h.flush()
    assert _rec(_open(p, fmt), "deploy")["text"] == "the release slot is tuesday", \
        "an edit made between the record copies and the lock was lost"


@pytest.mark.parametrize("fmt", FORMATS)
def test_an_editor_and_a_saver_on_two_threads_lose_no_edit(tmp_path, fmt):
    """AUDIT-A's p_race319.py, bounded: one thread re-edits four hot records while another flushes in a loop, with a
    short switch interval so the threads interleave inside the save. After a final flush, the last edit of every hot
    record is on disk. On eeeab6e2 the JSON store lost 4 of 4 in 2 of 3 runs of 4 s; the forced test above is the
    deterministic one, this is the shape it comes from."""
    import threading
    import time
    p = _store(tmp_path, fmt, n=20)
    h = _open(p, fmt)
    h._save_min_s = 0.0
    hot = [r for r in h._items if str(r.get("text", "")).startswith("filler")][:4]
    assert len(hot) == 4, "CONTROL: four hot records"
    last, stop, errs, counts = {}, threading.Event(), [], {"edits": 0, "saves": 0}

    def editor():
        n = 0
        while not stop.is_set():
            n += 1
            r = hot[n % 4]
            v = "edit %d" % n
            r["text"] = v
            last[r["id"]] = v
            counts["edits"] += 1

    def saver():
        while not stop.is_set():
            try:
                h.flush()
                counts["saves"] += 1
            except Exception as e:                      # noqa: BLE001 -- reported below
                errs.append(repr(e)[:200])
                return
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-5)
    try:
        ts = [threading.Thread(target=editor), threading.Thread(target=saver)]
        [t.start() for t in ts]
        time.sleep(2.0)
        stop.set()
        [t.join(30) for t in ts]
    finally:
        sys.setswitchinterval(old)
    assert not errs, errs
    assert counts["saves"] >= 2 and counts["edits"] >= 4, "CONTROL: the threads ran: %r" % counts
    h.flush()
    got = {r["id"]: r.get("text") for r in _open(p, fmt)._items}
    lost = [rid for rid, v in last.items() if got.get(rid) != v]
    assert not lost, "%d of %d hot records lost their last edit (%r)" % (len(lost), len(last), counts)


# THE MARK COMES AFTER THE WRITE (3.19, CI on fa655235). The tracked containers declared an edit BEFORE they stored it:
# `_fire` moved the edit generation, and only then did `dict.__setitem__` run. A save in between copied the new
# generation, serialised the OLD value, saw the generation unchanged after its write and cleared the id, so the edit was
# never written. CI lost 1 of 4 hot records' last edits on ubuntu-latest, Python 3.11 row store and Python 3.9 encrypted
# store; Windows and WSL had not shown it. Each case below runs a whole save right after the declaration, the window CI hit, so the
# order is tested on every OS without timing.
_EDITS = {
    "set": lambda r: r.__setitem__("text", "EDITED"),
    "update": lambda r: r.update(text="EDITED"),
    "ior": lambda r: r.__ior__({"text": "EDITED"}),
    "setdefault_new": lambda r: r.setdefault("xnew", "EDITED"),
    "del": lambda r: r.__delitem__("xtra"),
    "pop": lambda r: r.pop("xtra"),
    "nested_set": lambda r: r["meta"].__setitem__("note", "EDITED"),
    "nested_del": lambda r: r["meta"].__delitem__("seed"),
    "nested_popitem": lambda r: r["meta"].popitem(),          # `seed` is the last key set, so it is the one removed
    "nested_clear": lambda r: r["meta"].clear(),
    "list_append": lambda r: r["tags"].append("c"),
    "list_extend": lambda r: r["tags"].extend(["c"]),
    "list_insert": lambda r: r["tags"].insert(0, "c"),
    "list_setitem": lambda r: r["tags"].__setitem__(0, "c"),
    "list_delitem": lambda r: r["tags"].__delitem__(0),
    "list_iadd": lambda r: r["tags"].__iadd__(["c"]),
    "list_imul": lambda r: r["tags"].__imul__(2),
    "list_remove": lambda r: r["tags"].remove("a"),
    "list_pop": lambda r: r["tags"].pop(),
    "list_clear": lambda r: r["tags"].clear(),
    "list_sort": lambda r: r["tags"].sort(reverse=True),
    "list_reverse": lambda r: r["tags"].reverse(),
}


def _save_inside_the_mark(monkeypatch, h, rid):
    """Run one whole flush() right after the mark of `rid`, before the caller's write returns to it."""
    real = core._mark_touched
    hits = []

    def mark_then_save(store, ids):
        real(store, ids)
        if store is h and rid in ids and not hits:
            hits.append(1)
            h.flush()
    monkeypatch.setattr(core, "_mark_touched", mark_then_save)
    return hits


def _save_after_each_declaration(monkeypatch, h):
    """Run one whole flush() right after a tracked container's whole declaration (`_fire`: the mark, the dirty flag and
    the content revision), which is where a saver on another thread can land before the container's own write."""
    hits = []
    for cls in (core._TrackedDict, core._TrackedList):
        real = cls._fire

        def fire_then_save(self, *a, _real=real, **k):
            _real(self, *a, **k)
            if not hits:
                hits.append(1)
                h.flush()
        monkeypatch.setattr(cls, "_fire", fire_then_save)
    return hits


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("edit", sorted(_EDITS))
def test_a_save_between_the_mark_and_the_write_loses_no_edit(tmp_path, fmt, edit, monkeypatch):
    p = _store(tmp_path, fmt)
    h = _open(p, fmt)
    rec = _rec(h, "deploy")
    rec["xtra"] = "x"
    rec.setdefault("meta", {})["seed"] = "s"
    rec["tags"] = ["a", "b"]
    h.flush()
    hits = _save_after_each_declaration(monkeypatch, h)
    _EDITS[edit](rec)
    assert hits, "CONTROL: no save ran inside the mark, so the window was never opened"
    want = core._plain(rec)
    monkeypatch.undo()
    h.flush()
    got = _rec(_open(p, fmt), "deploy")
    for field in ("text", "xtra", "xnew", "meta", "tags"):
        w, g = want.get(field), got.get(field)
        if field == "meta":
            w, g = {k: w.get(k) for k in ("note", "seed")}, {k: g.get(k) for k in ("note", "seed")}
        assert g == w, "%s: the save inside the mark cleared the edit of %r (on disk %r, edited %r)" % (edit, field, g, w)


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_save_between_the_forget_mark_and_the_removal_does_not_bring_the_record_back(tmp_path, fmt, monkeypatch):
    p = _store(tmp_path, fmt)
    h = _open(p, fmt)
    rid = _rec(h, "deploy")["id"]
    hits = _save_inside_the_mark(monkeypatch, h, rid)
    h.forget(rid)
    assert hits, "CONTROL: no save ran inside the forget's mark"
    monkeypatch.undo()
    h.flush()
    assert not [r for r in _open(p, fmt)._items if r.get("id") == rid], "the forgotten record is back on disk"


def _erase_forget(h):
    h.forget(_rec(h, "deploy")["id"])


def _erase_subject(h):
    h.forget_subject("crm/alice")


def _erase_pii(h):
    h.forget_pii(["email"])


def _erase_retention(h):
    h.apply_retention(0.0)


def _erase_capacity(h):
    h.capacity = len([r for r in h._items if r.get("status", "active") == "active"])
    h.remember("one more note about gardening")


def _erase_import(h):
    cs = h.export_changeset()
    cs["records"], cs["tombstones"] = [], [{"memory_id": _rec(h, "deploy")["id"]}]
    h.import_changeset(cs)


_ERASURES = {"forget": _erase_forget, "forget_subject": _erase_subject, "forget_pii": _erase_pii,
             "apply_retention": _erase_retention, "capacity": _erase_capacity, "import_changeset": _erase_import}


@pytest.mark.parametrize("entry", sorted(_ERASURES))
@pytest.mark.parametrize("fmt", FORMATS)
def test_an_erasure_that_races_a_save_from_another_thread_does_not_come_back(tmp_path, fmt, entry, monkeypatch):
    """AUDIT-A F-11 (3.19): a save copied the records before it waited for the store lock. A flush from another thread of
    the same handle that queued behind an erasure wrote its copy from before the erasure after it, so the erased record
    was back on disk and in a fresh handle, in every format (3.18 had it in the row store). Forced here at exactly that
    position: as the erasure replaces the record list, another thread edits a record and flushes, and its save is held
    at the store lock until the erasure has returned. Every entry point that removes records is run."""
    import threading
    p = str(tmp_path / ("s." + fmt))
    m = _open(p, fmt)
    for i in range(6):
        m.remember("filler note number %d about gardening" % i)
    m.remember("the deploy window is friday", key="deploy", source={"doc": "crm/alice"}, pii=["email"])
    m.flush()
    h = _open(p, fmt)
    arrived, go, dropped = threading.Event(), threading.Event(), []
    other = [r for r in h._items if r.get("key") != "deploy"][0]

    def edit_and_flush():
        other["text"] = "an edit on another thread"          # so the flush has something to write
        h.flush()
    real_lock, prop = core._StoreLock, Inspeximus.__dict__["_items"]

    class HeldLock(real_lock):
        def __enter__(self):
            if threading.current_thread().name == "saver" and not arrived.is_set():
                arrived.set()
                go.wait(30)
            return super().__enter__()

    def fset(self, v):
        if self is h and not dropped:
            gone = {r.get("id") for r in prop.fget(self)} - {r.get("id") for r in v}
            if gone:
                dropped.extend(gone)
                threading.Thread(target=edit_and_flush, name="saver", daemon=True).start()
                assert arrived.wait(30), "CONTROL: the other thread's flush did not reach the store lock"
        prop.fset(self, v)
    monkeypatch.setattr(core, "_StoreLock", HeldLock)
    monkeypatch.setattr(Inspeximus, "_items", property(prop.fget, fset))
    try:
        _ERASURES[entry](h)
    finally:
        go.set()
    saver = [t for t in threading.enumerate() if t.name == "saver"]
    for t in saver:
        t.join(60)
    monkeypatch.undo()
    assert dropped, "CONTROL: %s removed no record" % entry
    assert saver and not saver[0].is_alive(), "CONTROL: the other thread's flush did not finish"
    h.flush()
    back = {r.get("id") for r in _open(p, fmt)._items} & set(dropped)
    assert not back, "%s: an erased record is back on disk after a save that raced it (%s)" % (entry, sorted(back))


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_forgetter_and_a_saver_on_two_threads_bring_no_record_back(tmp_path, fmt):
    """The shape F-11 comes from, bounded: one thread writes a record and erases it in a loop while another flushes in a
    loop, with a short switch interval so the threads interleave inside the save. After a final flush, no erased record
    is on disk. Before the copy moved inside the store lock, a flush that queued behind an erasure wrote the records from
    before it; the forced test above is the deterministic one."""
    import threading
    import time
    p = _store(tmp_path, fmt, n=20)
    h = _open(p, fmt)
    h._save_min_s = 0.0
    erased, stop, errs, counts = [], threading.Event(), [], {"erasures": 0, "saves": 0}

    def forgetter():
        n = 0
        while not stop.is_set():
            n += 1
            try:
                rid = h.remember("a short-lived note %d" % n)
                h.forget(rid)
            except Exception as e:                      # noqa: BLE001 -- reported below
                errs.append(repr(e)[:200])
                return
            erased.append(rid)
            counts["erasures"] += 1

    def saver():
        while not stop.is_set():
            try:
                h.flush()
                counts["saves"] += 1
            except Exception as e:                      # noqa: BLE001 -- reported below
                errs.append(repr(e)[:200])
                return
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-5)
    try:
        ts = [threading.Thread(target=forgetter), threading.Thread(target=saver)]
        [t.start() for t in ts]
        time.sleep(2.0)
        stop.set()
        [t.join(30) for t in ts]
    finally:
        sys.setswitchinterval(old)
    assert not errs, errs
    assert counts["saves"] >= 2 and counts["erasures"] >= 2, "CONTROL: the threads ran: %r" % counts
    h.flush()
    back = set(erased) & {r.get("id") for r in _open(p, fmt)._items}
    assert not back, "%d of %d erased records are back on disk (%r)" % (len(back), len(erased), counts)


class _KeyThatAddsAField(str):
    """A field name whose comparison adds a field to its record once armed: another thread's write, landing while code
    iterates the record's fields. `__hash__` stays `str`'s, so the record finds the key as usual."""
    __hash__ = str.__hash__
    armed, record, fired = False, None, []

    def _add(self):
        if _KeyThatAddsAField.armed and not _KeyThatAddsAField.fired:
            _KeyThatAddsAField.fired.append(1)
            _KeyThatAddsAField.record["added_by_another_thread"] = "yes"

    def __eq__(self, other):
        self._add()
        return str.__eq__(self, other)

    def __ne__(self, other):
        self._add()
        return str.__ne__(self, other)


_READERS = {"flush": lambda h: h.flush(), "export_changeset": lambda h: h.export_changeset(),
            "verify_writes": lambda h: h.verify_writes()}


# verify_writes compares the records with the rows only on a row store with nothing unsaved; elsewhere it reads none.
@pytest.mark.parametrize("fmt,reader", [
    pytest.param(f, r, id="%s-%s" % (f, r),
                 marks=pytest.mark.skipif(f == "enc" and not _CRYPTO, reason="needs inspeximus[crypto]"))
    for f in ("json", "enc", "db") for r in sorted(_READERS) if r != "verify_writes" or f == "db"])
def test_a_field_added_while_a_record_is_copied_does_not_fail_the_call(tmp_path, fmt, reader, monkeypatch):
    """G-1 (3.19): a save copied each record by iterating its live fields, and so did export_changeset and verify_writes.
    A field another thread of the handle added meanwhile raised "dictionary changed size during iteration": the save
    failed and flush() raised OSError. Forced here: a field is added while the record's fields are being iterated."""
    p = _store(tmp_path, fmt)
    h = _open(p, fmt)
    r = _rec(h, "deploy")
    r[_KeyThatAddsAField("zz_marker")] = 1
    r["text"] = "the deploy window is monday"
    if reader != "flush":
        h.flush()                                        # verify_writes reads the records only when nothing is unsaved
    if reader == "verify_writes":
        # A record that differs from its row, unseen by the tracker: the comparison must report it. Before G-1 the error
        # was swallowed by verify_writes' own guard, so the comparison was skipped and the audit read clean.
        dict.__setitem__([x for x in h._items if x is not r][0], "text", "changed behind the tracker")
    monkeypatch.setattr(_KeyThatAddsAField, "armed", True)
    monkeypatch.setattr(_KeyThatAddsAField, "record", r)
    monkeypatch.setattr(_KeyThatAddsAField, "fired", [])
    out = _READERS[reader](h)
    assert _KeyThatAddsAField.fired, "CONTROL: no field was added during the call"
    if reader == "verify_writes":
        assert "do not match the store file" in repr(out), (
            "verify_writes did not compare the records with the rows, so it could not see the unsaved one: %r" % (out,))
    monkeypatch.undo()
    h.flush()
    got = _rec(_open(p, fmt), "deploy")
    assert got.get("added_by_another_thread") == "yes" and got["text"] == "the deploy window is monday", \
        "the field added during the call was not written by the next save"
