"""AUDIT-A re-review of B25-R1's fix: an erasure stopped before its commit whose temp is then lost is
finished by rebuilding the temp from the segment. The rebuilt segment must verify like any other, also
when the recovery runs through a tenant view."""
import glob
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from test_audit_b_erasure_reaches_archive_segments import DAY, T0, _archived, _no_env  # noqa: E402,F401
import inspeximus.core as core  # noqa: E402
from inspeximus import archive  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


class Stop(Exception):
    pass


def _tenant_store(tmp_path, monkeypatch):
    p = str(tmp_path / "coding_memory.json")
    m = Inspeximus(p, receipts=True)
    ids = {}
    for i in range(8):
        monkeypatch.setattr(core.time, "time", lambda i=i: T0 - 40 * DAY + i)
        for name in ("acme", "globex"):
            ids.setdefault(name, []).append(m.for_tenant(name).remember(
                f"ran: {name} export number {i}", key=f"cmd:{name}{i}", tags=["bash"], mtype="episodic"))
    m.flush()
    monkeypatch.setattr(core.time, "time", lambda: T0)
    assert archive.apply(Inspeximus(p, receipts=True), 7, now=T0)["applied"]
    return p, ids


@pytest.mark.parametrize("via_view", [False, True])
def test_a_rebuilt_segment_verifies_and_holds_nothing_erased(tmp_path, monkeypatch, via_view):
    p, ids = _tenant_store(tmp_path, monkeypatch)
    target = ids["acme"][1:3]
    assert set(target) <= _archived(p), "control: the targets are archived"
    with monkeypatch.context() as mp:
        mp.setattr(archive, "commit_erasure", lambda *a, **k: (_ for _ in ()).throw(Stop()))
        with pytest.raises(Stop):
            Inspeximus(p, receipts=True).for_tenant("acme").forget(ids=target, request_id="crash")
    temps = glob.glob(os.path.join(os.path.dirname(p), "*.archive-*.tmp*"))
    if not temps:
        pytest.fail("control: the stopped erasure left no temp, so nothing was lost")
    for t in temps:
        os.unlink(t)
    m = Inspeximus(p, receipts=True)
    done = archive.recover(m.for_tenant("acme") if via_view else m)
    assert any(a == "replaced" for _, a in done), done
    arch = _archived(p)
    assert not set(target) & arch, "the rebuilt segment still holds the erased rows"
    assert set(ids["globex"]) <= arch, "the rebuild dropped another tenant's rows"
    assert set(ids["acme"]) - set(target) <= arch, "the rebuild dropped rows that were not erased"
    for s in archive.listed_segments(p):
        ok, problems = archive.verify_segment(os.path.join(os.path.dirname(p), s))
        assert ok, (s, problems)
    ok, problems = Inspeximus(p, receipts=True).verify_writes()
    assert ok, problems
