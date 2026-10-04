"""AUDIT-A F-5 (3.16.2): a handle loaded before `archive apply` wrote every moved row back on its next save.

The first test is AUDIT-A's, verbatim apart from the import path; it fails on b49d6775. The second was AUDIT-A's
control that the rows come back on that head, which cannot hold once they no longer do. It now puts the moved
rows back into the hot store by hand, the state a 3.16.1 peer left behind, and requires a second apply to heal
it without a duplicate: the path an operator who archived on 3.16.1 needs.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import inspeximus.core as core
from inspeximus import archive
from inspeximus.core import Inspeximus
DAY, T0 = 86400.0, 1790000000.0

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]: monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "kh"))
    return tmp_path

def _build(env, monkeypatch, n=10):
    p = str(env / "coding_memory.json")
    m = Inspeximus(p); ids = []
    for i in range(n):
        monkeypatch.setattr(core.time, "time", lambda i=i: T0 - 40 * DAY + i)
        ids.append(m.remember(f"ran: export number {i}", key=f"cmd:c{i}", tags=["bash"], mtype="episodic"))
    monkeypatch.setattr(core.time, "time", lambda: T0)
    return p, ids

def test_a_long_lived_peer_handle_does_not_bring_archived_rows_back(env, monkeypatch):
    p, ids = _build(env, monkeypatch)
    peer = Inspeximus(p)                                   # e.g. the MCP server, opened before the move
    assert len(peer._items) == 10
    r = archive.apply(Inspeximus(p), 7, now=T0)            # e.g. the CLI
    assert r["applied"] and len(Inspeximus(p)._items) == 0, "control: the move emptied the hot store"
    peer.remember("a new note written by the long-lived peer", key="peer-1")
    hot = [x["id"] for x in Inspeximus(p)._items]
    back = set(hot) & set(ids)
    print("HOT after the peer's write:", len(hot), "archived rows back in hot:", len(back))
    assert not back, f"{len(back)} archived rows were written back to the hot store by a peer's save"

def test_control_a_second_apply_heals_rows_a_3_16_1_peer_wrote_back(env, monkeypatch):
    p, ids = _build(env, monkeypatch)
    archive.apply(Inspeximus(p), 7, now=T0)
    seg_rows = archive.segment_rows(Inspeximus(p))
    assert len(seg_rows) == 10, "control: the move filled the segments"
    m = Inspeximus(p)                                       # the state a 3.16.1 peer's save left behind
    m._items = list(m._items) + [dict(r) for r in seg_rows]
    m._dirty = True
    m.flush()
    assert len({x["id"] for x in Inspeximus(p)._items} & set(ids)) == 10, "control: the rows are back"
    r = archive.apply(Inspeximus(p), 7, now=T0)
    hot = {x["id"] for x in Inspeximus(p)._items}
    segs = sum(len(v["ids"]) for v in archive.listed_segments(p).values())
    assert r["applied"] and not (hot & set(ids)) and segs == 10, (r.get("applied"), len(hot & set(ids)), segs)


def test_an_unreadable_archive_log_never_lets_a_moved_row_back_and_writes_go_on(env, monkeypatch):
    """AUDIT-A F-5b: with the log unreadable, a stale peer's merge re-adds only rows it never saved or edited, so no
    moved row comes back, and its own new write still lands: a damaged log does not stop the hook writes."""
    p, ids = _build(env, monkeypatch)
    writer = Inspeximus(p)                                # loaded before the move
    archive.apply(Inspeximus(p), 7, now=T0)
    open(p + ".archive.json", "wb").write(b"\x00\xff garbage")
    writer.remember("a new note written by the long-lived peer", key="peer-1")
    writer.flush()
    hot = Inspeximus(p)._items
    assert not ({x["id"] for x in hot} & set(ids)), "an archived row came back"
    assert any(x.get("key") == "peer-1" for x in hot), "the peer's own write was lost"
