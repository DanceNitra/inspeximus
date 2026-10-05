"""3.16.3, AUDIT-A F-7: a write tagged for a closed partition is refused on every path that writes tags.

Membership of a partition is the `partition:<name>` tag, and the write receipt commits that tag. The partition
handle refused a write after the close; a plain `remember(tags=["partition:x"])` did not, so the record joined
the closed partition and `verify_writes` reported it clean. The refusal now sits where a record is created,
`remember()`, which every tag-carrying wrapper calls, and in `import_changeset()`, which appends a peer's records.
A write tagged for an open partition still goes through (each case below has that control), and a write with no
partition tag never reads the registry.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core  # noqa: E402
from inspeximus.core import Inspeximus, SidecarMalformed  # noqa: E402
from inspeximus.partitions import Partitions  # noqa: E402


def _store(tmp_path):
    p = str(tmp_path / "memory.json")
    m = Inspeximus(p, receipts=True)
    parts = Partitions(m)
    parts.open("closed-1", kind="context", max_age_days=1, max_records=10, agent="bot").remember("in", key="a")
    parts.close("closed-1", actor="bot", disposition="retained")
    parts.open("open-1", kind="context", max_age_days=1, max_records=10, agent="bot")
    return p, m


def _members(p, name):
    return {r["id"] for r in Partitions(Inspeximus(p, receipts=True))._records(name)}


PATHS = {
    "remember": lambda m, tag: m.remember("x", key="k-remember", tags=[tag]),
    "remember provisional": lambda m, tag: m.remember("x", key="k-prov", tags=[tag], provisional=True),
    "remember_decision": lambda m, tag: m.remember_decision("decided x", because="y", tags=[tag]),
    "remember_dedup": lambda m, tag: m.remember_dedup("x dedup", tags=[tag]),
    "admit": lambda m, tag: m.admit("a customer asked about invoice 4471 again today", tags=[tag]),
    "tenant view": lambda m, tag: m.for_tenant("t1").remember("x", key="k-tenant", tags=[tag]),
    "agent view": lambda m, tag: m.as_agent("agent-1").remember("x", key="k-agent", tags=[tag]),
    "import_changeset": lambda m, tag: m.import_changeset(_peer_changeset(m, tag)),
}


def _peer_changeset(m, tag):
    """A real changeset from a second store whose one record carries `tag` (that store has no registry)."""
    peer = Inspeximus(str(m.path) + ".peer.json")
    peer.remember("a record written by a peer process", key="k-peer", tags=[tag])
    peer.flush()
    return peer.export_changeset()


@pytest.mark.parametrize("path", sorted(PATHS))
def test_a_write_tagged_for_a_closed_partition_is_refused(tmp_path, path):
    p, m = _store(tmp_path)
    before = _members(p, "closed-1")
    with pytest.raises(ValueError) as err:
        PATHS[path](m, "partition:closed-1")
    assert "closed-1" in str(err.value) and "Open a new partition" in str(err.value), str(err.value)
    assert _members(p, "closed-1") == before, "a refused write joined the closed partition"


@pytest.mark.parametrize("path", sorted(PATHS))
def test_control_the_same_write_tagged_for_an_open_partition_goes_through(tmp_path, path):
    p, m = _store(tmp_path)
    PATHS[path](m, "partition:open-1")
    m.flush()
    tagged = [r for r in Inspeximus(p)._items if "partition:open-1" in (r.get("tags") or [])]
    assert tagged, "control: a write tagged for an open partition lands with the tag"


def test_audit_a_f7_a_plain_write_cannot_join_a_closed_partition(tmp_path):
    """AUDIT-A's test_f7, changed by AUDIT-A to expect the refusal (option A)."""
    p = str(tmp_path / "memory.json")
    m = Inspeximus(p, receipts=True)
    parts = Partitions(m)
    ctx = parts.open("triage-1", kind="context", max_age_days=1, max_records=2, agent="triage-bot")
    ctx.remember("customer asked about invoice 4471", key="ctx::invoice")
    parts.close("triage-1", actor="triage-bot", disposition="erased")
    with pytest.raises(ValueError):
        ctx.remember("late write through the handle", key="late")      # control: the handle refuses
    with pytest.raises(ValueError):
        m.remember("injected after the close", key="spoof", tags=["partition:triage-1"])
    assert not _members(p, "triage-1")


def test_an_untagged_write_never_reads_the_registry(tmp_path, monkeypatch):
    p, m = _store(tmp_path)
    calls = {"n": 0}
    real = core._partition_registry

    def counted(path):
        calls["n"] += 1
        return real(path)

    monkeypatch.setattr(core, "_partition_registry", counted)
    for i in range(2000):
        m.remember("plain write %d" % i, tags=["bash"])
    assert calls["n"] == 0, calls
    m.remember("tagged", tags=["partition:open-1"])
    assert calls["n"] == 1, "control: a tagged write reads it once"


@pytest.mark.parametrize("damage", [b"{not json", b"\x00\xff", b'{"v": 1}', b"[]"])
def test_a_damaged_registry_refuses_a_tagged_write_and_leaves_plain_writes_alone(tmp_path, damage):
    p, m = _store(tmp_path)
    open(p + ".partitions.json", "wb").write(damage)
    with pytest.raises(SidecarMalformed) as err:
        m.remember("tagged", tags=["partition:open-1"])
    assert "partitions" in str(err.value) and err.value.remedy, str(err.value)
    m.remember("untagged write is not affected", key="plain")
