"""AUDIT-A review of B-25: the conversion backup, a long-lived handle, and tenant views."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from test_audit_b_erasure_reaches_archive_segments import DAY, T0, _archived, _no_env, _store  # noqa: E402,F401
import inspeximus.core as core  # noqa: E402
from inspeximus import archive  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


def test_an_erasure_that_reaches_only_archived_rows_removes_the_conversion_backup(tmp_path, monkeypatch):
    """AUDIT-B's rebase merge: `if target or _seg_ids: self._drop_pre_rows_backup()`."""
    p = str(tmp_path / "coding_memory.json")
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    m = Inspeximus(p)
    ids = []
    for i in range(12):
        monkeypatch.setattr(core.time, "time", lambda i=i: T0 - 40 * DAY + i)
        ids.append(m.remember(f"ran: export number {i} of the payroll batch", key=f"cmd:c{i:03d}",
                              tags=["bash"], mtype="episodic"))
    m.flush()
    monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT")
    monkeypatch.setattr(core.time, "time", lambda: T0)
    Inspeximus(p).flush()                                   # converts to rows, leaves <store>.pre-rows.bak
    bak = p + ".pre-rows.bak"
    if not os.path.exists(bak):
        pytest.skip("this build did not leave a conversion backup, so the case does not arise")
    assert archive.apply(Inspeximus(p), 7, now=T0)["applied"]
    if not {ids[3]} <= _archived(p):
        pytest.fail("control: the target was not archived")
    assert ids[3].encode() in open(bak, "rb").read(), "control: the backup holds the archived row"
    Inspeximus(p).forget(ids=[ids[3]], request_id="r")
    assert not os.path.exists(bak), "an erasure of an archived row left the conversion backup holding it"


def test_a_long_lived_handle_does_not_bring_erased_rows_back(tmp_path, monkeypatch):
    p, ids, hot = _store(tmp_path, monkeypatch)
    m = Inspeximus(p)
    m.forget_subject("hr/alice", request_id="r")
    m.remember("ran: something after the erasure", key="cmd:after", tags=["bash"])
    m.flush()
    other = Inspeximus(p)
    other.remember("ran: a second handle writes", key="cmd:second", tags=["bash"])
    other.flush()
    m.remember("ran: the first handle writes again", key="cmd:again", tags=["bash"])
    m.flush()
    alice = {ids[i] for i in range(0, 24, 4)} | {hot}
    back = alice & ({r["id"] for r in Inspeximus(p)._items} | _archived(p))
    assert not back, f"erased rows came back: {sorted(back)}"


def test_a_tenant_view_erasure_reaches_only_its_own_archived_rows(tmp_path, monkeypatch):
    p = str(tmp_path / "coding_memory.json")
    m = Inspeximus(p)
    acme, globex = m.for_tenant("acme"), m.for_tenant("globex")
    ids = {}
    for i in range(8):
        monkeypatch.setattr(core.time, "time", lambda i=i: T0 - 40 * DAY + i)
        for name, view in (("acme", acme), ("globex", globex)):
            ids.setdefault(name, []).append(view.remember(f"ran: {name} export number {i}", key=f"cmd:{name}{i}",
                                                          tags=["bash"], mtype="episodic",
                                                          source={"doc": "hr/alice"}))
    m.flush()
    monkeypatch.setattr(core.time, "time", lambda: T0)
    assert archive.apply(Inspeximus(p), 7, now=T0)["applied"]
    if not set(ids["acme"] + ids["globex"]) <= _archived(p):
        pytest.fail("control: both tenants' rows were archived")
    Inspeximus(p).for_tenant("acme").forget_subject("hr/alice", request_id="acme-dsar")
    arch = _archived(p)
    assert not set(ids["acme"]) & arch, "acme's archived rows survived acme's erasure"
    assert set(ids["globex"]) <= arch, "acme's erasure removed globex's archived rows"


def test_an_objected_archived_record_stays_out_of_recall_with_the_archive(tmp_path, monkeypatch):
    extra = (("ran: rotate the zebra-quartz credential", {"key": "cmd:zq", "tags": ["bash"], "mtype": "episodic",
                                                         "source": {"doc": "hr/bob"}}),)
    p, ids, hot = _store(tmp_path, monkeypatch, extra=extra)
    target = ids[-1]
    assert target in _archived(p)
    q = "rotate the zebra-quartz credential"
    if target not in [h["id"] for h in Inspeximus(p).recall(q, k=5, include_archive=True)]:
        pytest.fail("control: recall with the archive does not return the archived record at all")
    Inspeximus(p).object_processing("hr/bob", actor="dpo", ground="own_situation")
    hits = Inspeximus(p).recall(q, k=5, include_archive=True)
    assert target not in [h["id"] for h in hits], "an objected archived record was returned by recall"


def test_a_write_inside_a_pooled_read_is_not_silently_dropped(tmp_path, monkeypatch):
    p, ids, hot = _store(tmp_path, monkeypatch)
    m = Inspeximus(p)
    with archive.pooled(m):
        rid = m.remember("ran: written while the archive was pooled", key="cmd:during", tags=["bash"])
        m.flush()
    m.flush()
    assert rid in {r["id"] for r in Inspeximus(p)._items}, "a write made during a pooled read never reached the store"
