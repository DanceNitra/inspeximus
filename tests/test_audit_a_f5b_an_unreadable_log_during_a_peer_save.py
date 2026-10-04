"""AUDIT-A on fix/redact-event-ids-on-erasure f4b0d4bf: an archive log that cannot be read during a peer's save
re-opens F-5. `_archived_ids()` returns an empty set, so `_merge_with_disk` re-adds every moved row."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core  # noqa: E402
from inspeximus import archive  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

DAY, T0 = 86400.0, 1790000000.0


def test_f5b_an_unreadable_log_does_not_bring_archived_rows_back(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    p = str(tmp_path / "coding_memory.json")
    m = Inspeximus(p)
    ids = []
    for i in range(10):
        monkeypatch.setattr(core.time, "time", lambda i=i: T0 - 40 * DAY + i)
        ids.append(m.remember(f"ran: export number {i}", key=f"cmd:c{i}", tags=["bash"], mtype="episodic"))
    monkeypatch.setattr(core.time, "time", lambda: T0)
    peer = Inspeximus(p)
    assert archive.apply(Inspeximus(p), 7, now=T0)["applied"]
    good = open(p + ".archive.json", "rb").read()
    open(p + ".archive.json", "wb").write(b"\x00garbage")
    try:
        peer.remember("a note written while the log is unreadable", key="n1")
    except Exception:                                  # a named refusal is one allowed answer
        pass
    open(p + ".archive.json", "wb").write(good)
    back = {r["id"] for r in Inspeximus(p)._items} & set(ids)
    assert not back, f"{len(back)} archived rows were written back to the hot store"
