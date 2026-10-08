"""AUDIT-B B-25 step 2: an erasure reaches the archive segments, or refuses and names what it could not reach.

Archiving moves rows out of the hot store into segments beside it. A GDPR erasure that misses a segment is
the worst outcome this feature can have, so every erasure path selects over the hot rows and the archived
rows as one store, rewrites each segment that held a match (an `amend-intent`, the rewrite, then the
`amend`), and puts the tombstones in the hot store's one chain. A segment that is missing or does not match
the log refuses the erasure before anything is erased. The certificate names every segment and whether the
erased ids were checked absent in it.
"""
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import inspeximus.core as core  # noqa: E402
from inspeximus import archive  # noqa: E402
from inspeximus.core import Inspeximus, verify_erasure_certificate  # noqa: E402

DAY = 86400.0
T0 = 1790000000.0


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)


def _store(d, monkeypatch, n=24, extra=()):
    """n old captures (every 4th attributed to hr/alice, every 6th tagged email), archived at 7 days,
    plus a recent capture attributed to hr/alice that stays hot."""
    p = str(d / "coding_memory.json")
    m = Inspeximus(p)
    ids = []
    for i in range(n):
        monkeypatch.setattr(core.time, "time", lambda i=i: T0 - 40 * DAY + i)
        kw = {"key": f"cmd:c{i:03d}", "tags": ["bash"], "mtype": "episodic"}
        if i % 4 == 0:
            kw["source"] = {"doc": "hr/alice"}
        if i % 6 == 0:
            kw["pii"] = ["email"]
        ids.append(m.remember(f"ran: export number {i} of the payroll batch", **kw))
    for text, kw in extra:
        monkeypatch.setattr(core.time, "time", lambda: T0 - 40 * DAY + 500)
        ids.append(m.remember(text, **kw))
    monkeypatch.setattr(core.time, "time", lambda: T0 - DAY)
    hot = m.remember("ran: export the recent alice batch", key="cmd:recent", tags=["bash"],
                     source={"doc": "hr/alice"})
    m.flush()
    monkeypatch.setattr(core.time, "time", lambda: T0)
    assert archive.apply(Inspeximus(p), 7, now=T0)["applied"]
    return p, ids, hot


def _archived(p):
    return {i for s in archive.listed_segments(p).values() for i in s["ids"]}


def _state(p):
    """Everything an erasure could change: hot ids, tombstones, the log, and each segment's bytes."""
    d = os.path.dirname(p)
    segs = {n: open(os.path.join(d, n), "rb").read() for n in sorted(os.listdir(d)) if ".archive-" in n}
    return (sorted(r["id"] for r in Inspeximus(p)._items), len(Inspeximus(p)._tombstones),
            open(archive.log_path(p), "rb").read(), segs)


def test_forget_subject_erases_in_the_segments_and_the_hot_store(tmp_path, monkeypatch):
    p, ids, hot = _store(tmp_path, monkeypatch)
    alice = {ids[i] for i in range(0, 24, 4)}
    assert alice <= _archived(p), "the control: alice's old records were archived"
    r = Inspeximus(p).forget_subject("hr/alice", request_id="dsar-1")
    assert set(r["ids"]) == alice | {hot} and r["erased"] == 7
    assert r["archive"]["segments"][0]["state"] == "rewritten"
    assert not (alice & _archived(p)) and hot not in {x["id"] for x in Inspeximus(p)._items}
    assert {t["memory_id"] for t in Inspeximus(p)._tombstones} == alice | {hot}, "one chain, in the hot store"
    assert archive.verify_log(archive.read_log(p))[0] and archive.check_segments(p) == []
    found = Inspeximus(p).recall("export payroll batch alice", k=50, include_archive=True)
    assert not ({h["id"] for h in found} & (alice | {hot}))
    cert = Inspeximus(p).erasure_certificate("dsar-1")
    v = verify_erasure_certificate(cert, store_path=p)
    assert v["valid"] and v["checks"]["archive_levels"][0]["content"] == "absent (checked)"


def test_forget_by_id_by_predicate_and_by_pii_tag_reach_the_segments(tmp_path, monkeypatch):
    p, ids, hot = _store(tmp_path, monkeypatch)
    assert Inspeximus(p).forget(ids=[ids[1]])["forgotten"] == 1 and ids[1] not in _archived(p)
    r = Inspeximus(p).forget(where=lambda x: "number 2" in (x.get("text") or ""))
    assert set(r["ids"]) == {ids[2], ids[20], ids[21], ids[22], ids[23]}
    assert not (set(r["ids"]) & _archived(p))
    r = Inspeximus(p).forget_pii(types=["email"])
    assert set(r["ids"]) == {ids[0], ids[6], ids[12], ids[18]}, "every email-tagged record, in any month"
    assert not (set(r["ids"]) & _archived(p))


def test_a_missing_segment_refuses_a_subject_erasure_and_nothing_is_erased(tmp_path, monkeypatch):
    p, ids, hot = _store(tmp_path, monkeypatch)
    seg = sorted(archive.listed_segments(p))[0]
    shutil.move(str(tmp_path / seg), str(tmp_path / "moved-away.bin"))
    before = _state(p)
    with pytest.raises(archive.SegmentsUnreachable, match=seg):
        Inspeximus(p).forget_subject("hr/alice")
    with pytest.raises(archive.SegmentsUnreachable):
        Inspeximus(p).forget(where=lambda x: True)
    with pytest.raises(archive.SegmentsUnreachable):
        Inspeximus(p).forget(ids=[ids[0]])
    assert _state(p) == before, "a refusal erased nothing, anywhere"
    assert Inspeximus(p).forget(ids=[hot])["forgotten"] == 1, "an id the segment does not hold needs no segment"


def test_an_altered_segment_refuses(tmp_path, monkeypatch):
    p, ids, hot = _store(tmp_path, monkeypatch)
    seg = tmp_path / sorted(archive.listed_segments(p))[0]
    with open(seg, "ab") as fh:
        fh.write(b"\0")
    with pytest.raises(archive.SegmentsUnreachable, match="altered"):
        Inspeximus(p).forget_subject("hr/alice")


def test_a_stopped_erasure_is_finished_by_the_next_one(tmp_path, monkeypatch):
    p, ids, hot = _store(tmp_path, monkeypatch)
    real_commit = archive.commit_erasure
    monkeypatch.setattr(archive, "commit_erasure", lambda *a, **k: (_ for _ in ()).throw(SystemExit("crash")))
    with pytest.raises(SystemExit):
        Inspeximus(p).forget(ids=[ids[1]])
    monkeypatch.setattr(archive, "commit_erasure", real_commit)
    assert archive._pending_intents(archive.read_log(p)), "the control: the intent is logged, not committed"
    assert archive.segment_files(p)["temps"], "the prepared segment is waiting beside the store"
    archive.sweep_temps(p)
    assert archive.segment_files(p)["temps"], "a temp a pending intent names is never swept"
    Inspeximus(p).forget(ids=[ids[2]])
    assert not archive._pending_intents(archive.read_log(p))
    assert not ({ids[1], ids[2]} & _archived(p)) and archive.check_segments(p) == []


def test_a_stale_segment_temp_is_swept_and_an_unlisted_copy_is_erased(tmp_path, monkeypatch):
    p, ids, hot = _store(tmp_path, monkeypatch)
    seg = sorted(archive.listed_segments(p))[0]
    stale = tmp_path / (seg + ".tmp.99999")
    shutil.copy(tmp_path / seg, stale)
    rows_copy = tmp_path / seg.replace(".1.", ".9.")
    # an unlisted segment: a copy of the hot capture, as a run that stopped before its log entry leaves
    archive._write_segment_file(rows_copy, [dict(r) for r in Inspeximus(p)._items if r["id"] == hot],
                                {"kind": archive.SEGMENT_KIND})
    assert archive.segment_files(p)["unlisted"] == [rows_copy.name]
    r = Inspeximus(p).forget(ids=[hot])
    assert not stale.exists(), "the stale temp, a copy nothing accounts for, is gone"
    from inspeximus import sqlite_store as rows
    assert hot not in {x["id"] for x in rows.load(rows_copy)}, "the unlisted copy was rewritten too"
    assert r["forgotten"] == 1


def test_the_certificate_names_an_absent_segment_and_git_history(tmp_path, monkeypatch):
    p, ids, hot = _store(tmp_path, monkeypatch)
    Inspeximus(p).forget_subject("hr/alice", request_id="dsar-2")
    cert = Inspeximus(p).erasure_certificate("dsar-2")
    assert cert["self_check"]["verified"]
    seg = cert["archive"]["segments"][0]["segment"]
    shutil.move(str(tmp_path / seg), str(tmp_path / "moved-away.bin"))
    v = verify_erasure_certificate(cert, store_path=p)
    assert not v["valid"] and v["checks"]["archive_levels"][0]["content"] == "not checked (segment absent)"
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(core.time, "time", lambda: T0)
    m = Inspeximus(str(repo / "coding_memory.json"))
    monkeypatch.setattr(core.time, "time", lambda: T0 - 40 * DAY)
    m.remember("ran: export for bob", key="cmd:bob", tags=["bash"], source={"doc": "hr/bob"})
    m.flush()
    monkeypatch.setattr(core.time, "time", lambda: T0)
    archive.apply(Inspeximus(str(repo / "coding_memory.json")), 7, now=T0, allow_git_tracked=True)
    Inspeximus(str(repo / "coding_memory.json")).forget_subject("hr/bob", request_id="dsar-3")
    cert = Inspeximus(str(repo / "coding_memory.json")).erasure_certificate("dsar-3")
    assert cert["archive"]["segments"][0]["git"] == archive.GIT_HISTORY
    assert not cert["self_check"]["verified"]
    assert any("history not reachable" in x for x in cert["self_check"]["problems"])


def test_erase_past_copies_neither_touches_segments_nor_calls_them_unreached(tmp_path, monkeypatch):
    p, ids, hot = _store(tmp_path, monkeypatch)
    before = _state(p)
    r = Inspeximus(p).erase_past_copies(apply=True)
    assert r["siblings_not_reached"] == [], r["siblings_not_reached"]
    assert _state(p)[2:] == before[2:], "the log and every segment are untouched"


def test_a_subject_that_collides_across_the_hot_store_and_a_segment_is_ambiguous(tmp_path, monkeypatch):
    """Two raw sources with one canonical form: one only in the hot store, one only in a segment. Each
    store alone sees no collision; the pooled selection sees both and refuses."""
    extra = [("ran: export for the other hr alice", {"key": "cmd:other", "tags": ["bash"],
                                                     "source": {"doc": "HR/Alice"}})]
    p, ids, hot = _store(tmp_path, monkeypatch, extra=extra)
    assert core.Inspeximus._canon_source("HR/Alice") == core.Inspeximus._canon_source("hr/alice")
    before = _state(p)
    with pytest.raises(core.AmbiguousSubject):
        Inspeximus(p).forget_subject("hr/alice")
    assert _state(p) == before


def test_erasure_audit_reads_the_segment_rows(tmp_path, monkeypatch):
    p, ids, hot = _store(tmp_path, monkeypatch)
    Inspeximus(p).forget(ids=[ids[1]])
    audit = Inspeximus(p).erasure_audit(values=["payroll batch"])
    flagged = {a.get("id") for a in audit["advisory"]}
    assert set(ids) - {ids[1]} <= flagged, "every archived record still holding the value is reported"
    assert ids[1] not in flagged


def test_a_log_cut_by_one_whole_entry_is_a_named_gap(tmp_path, monkeypatch):
    """The hot store records the log's entry count and head. A log cut at an entry boundary is still a
    valid chain on its own; against that record it is a gap in verify_writes, the certificate, erasure
    and erasure_audit. The untouched copy is the control."""
    cut = tmp_path / "cut"
    ctl = tmp_path / "control"
    for d in (cut, ctl):
        d.mkdir()
        p = str(d / "coding_memory.json")
        m = Inspeximus(p)
        for i in range(8):
            monkeypatch.setattr(core.time, "time", lambda i=i: T0 - (40 if i % 2 else 75) * DAY + i)
            m.remember(f"ran: step {i}", key=f"cmd:s{i}", tags=["bash"], source={"doc": "hr/alice"})
        m.flush()
        monkeypatch.setattr(core.time, "time", lambda: T0)
        archive.apply(Inspeximus(p), 7, now=T0)
    p, q = str(cut / "coding_memory.json"), str(ctl / "coding_memory.json")
    entries = archive.read_log(p)
    assert len(entries) == 2
    archive.log_path(p).write_text(json.dumps({"kind": archive.LOG_KIND, "entries": entries[:1]}), encoding="utf-8")
    assert archive.verify_log(archive.read_log(p))[0], "the cut log is a valid chain on its own"
    gap = "1 entry missing"
    assert any(gap in x for x in Inspeximus(p).verify_writes()[1])
    assert not any(gap in x for x in Inspeximus(q).verify_writes()[1]), "the control"
    assert Inspeximus(p).erasure_audit(values=["step"])["archive_log"]["ok"] is False
    assert Inspeximus(q).erasure_audit(values=["step"])["archive_log"]["ok"] is True
    with pytest.raises(archive.SegmentsUnreachable, match="entry missing"):
        Inspeximus(p).forget_subject("hr/alice")
    Inspeximus(q).forget_subject("hr/alice", request_id="r")
    assert Inspeximus(q).erasure_certificate("r")["self_check"]["verified"], "the control erases and verifies"


def _two_entry_store(d, monkeypatch):
    d.mkdir()
    p = str(d / "coding_memory.json")
    m = Inspeximus(p)
    for i in range(8):
        monkeypatch.setattr(core.time, "time", lambda i=i: T0 - (40 if i % 2 else 75) * DAY + i)
        m.remember(f"ran: step {i}", key=f"cmd:s{i}", tags=["bash"], source={"doc": "hr/alice"})
    m.flush()
    monkeypatch.setattr(core.time, "time", lambda: T0)
    archive.apply(Inspeximus(p), 7, now=T0)
    return p


def _cut_log_and_the_stores_record(p):
    """What someone with write access to the store's directory does: cut the log by one entry AND make
    the store's own record agree with the cut."""
    import sqlite3
    entries = archive.read_log(p)[:1]
    archive.log_path(p).write_text(json.dumps({"kind": archive.LOG_KIND, "entries": entries}), encoding="utf-8")
    con = sqlite3.connect(p)
    con.execute("UPDATE meta SET v=? WHERE k='archive_log_head'",
                (json.dumps({"count": 1, "head": entries[0]["hash"]}),))
    con.commit()
    con.close()


def test_the_head_kept_outside_the_store_catches_a_cut_the_store_was_edited_to_match(tmp_path, monkeypatch, user_config):
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    p = _two_entry_store(tmp_path / "a", monkeypatch)
    _cut_log_and_the_stores_record(p)
    assert any("head kept outside the store" in x for x in Inspeximus(p).verify_writes()[1])
    with pytest.raises(archive.SegmentsUnreachable, match="head kept outside"):
        Inspeximus(p).forget_subject("hr/alice")
    user_config(INSPEXIMUS_HEADS="0")
    q = _two_entry_store(tmp_path / "b", monkeypatch)
    _cut_log_and_the_stores_record(q)
    assert not any("archive log" in x for x in Inspeximus(q).verify_writes()[1]), \
        "the control: without the outside head, a cut that both files agree on is not seen"


def test_a_handle_keeps_working_after_it_archived_and_after_it_erased(tmp_path, monkeypatch):
    """Every path that writes the hot file outside the normal save (the log head, the VACUUM) re-syncs the
    handle, or its next save reads the write as another writer's and merges the moved or erased rows
    back from disk."""
    p = _two_entry_store(tmp_path / "a", monkeypatch)
    moved = {i for s in archive.listed_segments(p).values() for i in s["ids"]}
    m = Inspeximus(p)
    m.remember("a note written after the move", tags=["note"])
    m.flush()
    assert not ({r["id"] for r in Inspeximus(p)._items} & moved)
    q = str(tmp_path / "b" / "coding_memory.json")
    os.makedirs(os.path.dirname(q))
    h = Inspeximus(q)
    for i in range(6):
        monkeypatch.setattr(core.time, "time", lambda i=i: T0 - 40 * DAY + i)
        h.remember(f"ran: step {i}", key=f"cmd:s{i}", tags=["bash"])
    h.flush()
    monkeypatch.setattr(core.time, "time", lambda: T0)
    same = Inspeximus(q)
    archive.apply(same, 7, now=T0)
    same.remember("written by the handle that archived", tags=["note"])
    same.flush()
    assert len(Inspeximus(q)._items) == 1, "the archiving handle did not bring the moved rows back"
    gone = sorted(archive.listed_segments(q).values(), key=lambda s: s["ids"])[0]["ids"][0]
    e = Inspeximus(q)
    e.forget(ids=[gone])
    e.remember("written by the handle that erased", tags=["note"])
    e.flush()
    assert gone not in {i for s in archive.listed_segments(q).values() for i in s["ids"]}
    assert gone not in {r["id"] for r in Inspeximus(q)._items}
