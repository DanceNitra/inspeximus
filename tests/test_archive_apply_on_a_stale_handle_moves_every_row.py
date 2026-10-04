"""3.16.2: archive.apply() on a handle that another process wrote past.

Measured on 3.16.1 on a copy of a 79,313-row project store: a handle was opened, a hook in another process
wrote one capture, and apply() on the first handle wrote every segment and the log and returned
applied=True, while the hot store still held all 79,314 rows. The save saw the peer's write and merged
the disk back in, and a merge cannot re-apply a removal that has no tombstone. The next apply() finished
the move, so nothing was lost, but the return value said done when it was not.

apply() now reads the rows again under the store lock and saves inside that lock, and it reads the hot
file back afterwards: a moved id still there makes it return applied=False with the ids.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core  # noqa: E402
from inspeximus import archive  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

DAY = 86400.0


def _store(tmp_path, monkeypatch, n=12):
    p = tmp_path / "coding_memory.json"
    t0 = time.time()
    m = Inspeximus(str(p))
    for i in range(n):
        monkeypatch.setattr(core.time, "time", lambda i=i: t0 - 40 * DAY + i)
        m.remember("ran: make target %d" % i, key="cmd:%d" % i, mtype="episodic")
    m.flush()
    monkeypatch.undo()
    return p


def _hot_ids(p):
    return {r["id"] for r in Inspeximus(str(p))._items}


def _logged(p):
    return {i for e in archive.read_log(str(p)) for i in e["ids"]}


def test_a_peer_write_between_open_and_apply_does_not_strand_the_move(tmp_path, monkeypatch):
    p = _store(tmp_path, monkeypatch)
    stale = Inspeximus(str(p))
    peer = Inspeximus(str(p))
    peer_id = peer.remember("a capture another hook wrote", key="cmd:peer", mtype="episodic")
    peer.flush()
    assert len(_hot_ids(p)) == 13, "control: the peer's row is on disk before the archive runs"
    res = archive.apply(stale, 7)
    assert res["applied"] is True and not res.get("stranded"), res
    assert len(_logged(p)) == 12
    assert not (_logged(p) & _hot_ids(p)), "every row the log gives to a segment left the hot store"
    assert _hot_ids(p) == {peer_id}, "the peer's row is kept and nothing else is"


def test_a_save_that_cannot_remove_the_rows_is_reported_and_the_next_run_finishes(tmp_path, monkeypatch):
    p = _store(tmp_path, monkeypatch)
    m = Inspeximus(str(p))
    monkeypatch.setattr(m, "_save", lambda force=False: None)
    monkeypatch.setattr(m, "flush", lambda: None)
    res = archive.apply(m, 7)
    assert res["applied"] is False, res
    assert sorted(res["stranded"]) == sorted(_logged(p)) and len(res["stranded"]) == 12
    monkeypatch.undo()
    again = archive.apply(Inspeximus(str(p)), 7)
    assert again["applied"] is True and not again.get("stranded"), again
    assert _hot_ids(p) == set() and len(_logged(p)) == 12
