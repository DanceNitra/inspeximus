"""A-34: an operation whose written proof cannot be stored changes nothing, and says so.

Before 3.15.4 a failed write of a tombstone chain, an objection, a spend against the irreversible budget, or
a receipt was recorded in `_sidecar_errors`, and the operation carried on and reported success. forget()
deleted the rows and answered `tombstones: 1` while the disk held none; after a reload, verify_writes()
called the deliberate erasure "deleted out-of-band". Each test below fails one proof write, reloads, and
checks that nothing the proof would have covered changed on disk, and that the caller was told.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core  # noqa: E402
from inspeximus import ProofNotWritten  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


@pytest.fixture(autouse=True)
def _no_env(monkeypatch, tmp_path):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keys"))


def _full_disk_for(monkeypatch, suffix):
    real_aw = Inspeximus._atomic_write
    real_wt = type(core.Path("x")).write_text

    def aw(path, *a, **k):
        if str(path).endswith(suffix):
            raise OSError(28, "No space left on device")
        return real_aw(path, *a, **k)

    def wt(self, *a, **k):
        if str(self).endswith(suffix):
            raise OSError(28, "No space left on device")
        return real_wt(self, *a, **k)

    monkeypatch.setattr(Inspeximus, "_atomic_write", staticmethod(aw))
    monkeypatch.setattr(type(core.Path("x")), "write_text", wt)
    # 3.16.4 (F-24): the store's sidecars are written by `_safewrite.write_atomic`, which refuses a link. A full disk
    # there must fail the same way, or this fixture stops reaching the write it exists to break.
    from inspeximus import _safewrite
    real_sw = _safewrite.write_atomic

    def sw(path, *a, **k):
        if str(path).endswith(suffix):
            raise OSError(28, "No space left on device")
        return real_sw(path, *a, **k)
    monkeypatch.setattr(_safewrite, "write_atomic", sw)
    if suffix == ".receipts.json":
        # The snapshot-plus-tail format writes the sidecar through `_durable_replace` and the tail through
        # `receipts_tail.append`; a full disk refuses both.
        real_dr = core._durable_replace

        def dr(path, *a, **k):
            if str(path).endswith((".receipts.json", ".receipts.tail.jsonl")):
                raise OSError(28, "No space left on device")
            return real_dr(path, *a, **k)

        def app(tail, *a, **k):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(core, "_durable_replace", dr)
        import inspeximus.receipts_tail as _rt
        monkeypatch.setattr(_rt, "append", app)


def _store(tmp_path):
    p = str(tmp_path / "s.json")
    m = Inspeximus(p, receipts=True)
    a = m.remember("alice lives at 5 Elm St", source={"doc": "hr/alice"})
    m.remember("an unrelated record")
    m.flush()
    return p, m, a


def _disk(p):
    n = Inspeximus(p, receipts=True)
    return sorted(r["id"] for r in n._items), [t.get("hash") for t in n._tombstones], n


@pytest.mark.parametrize("how", ["ids", "subject", "pii"])
def test_an_erasure_whose_tombstones_cannot_be_written_erases_nothing(tmp_path, monkeypatch, how):
    p, m, a = _store(tmp_path)
    if how == "pii":
        m.remember("mail alice at alice@example.com", pii=["email"], source={"doc": "hr/alice"})
        m.flush()
    before = _disk(p)[:2]
    with monkeypatch.context() as mp:
        _full_disk_for(mp, ".tombstones.json")
        with pytest.raises(ProofNotWritten, match="nothing was erased"):
            if how == "ids":
                m.forget(ids=[a], request_id="dsar-1")
            elif how == "subject":
                m.forget_subject("hr/alice", request_id="dsar-1")
            else:
                m.forget_pii(types=["email"], request_id="dsar-1")
    assert any(r["id"] == a for r in m._items), "the handle dropped the rows it did not erase"
    assert not m._tombstones, "the handle kept tombstones that were never written"
    m.flush()
    assert _disk(p)[:2] == before, "the store changed without the erasure's proof"
    ok, problems = _disk(p)[2].verify_writes()
    assert ok, problems
    assert m.forget(ids=[a], request_id="dsar-1")["forgotten"] == 1, "the same handle cannot erase afterwards"
    assert len(_disk(p)[1]) == 1


def test_an_out_of_band_declaration_whose_tombstone_cannot_be_written_records_nothing(tmp_path, monkeypatch):
    p, m, a = _store(tmp_path)
    m._items = [r for r in m._items if r["id"] != a]      # the row left the store outside inspeximus
    m._touched.add(a)
    m._save(force=True)
    with monkeypatch.context() as mp:
        _full_disk_for(mp, ".tombstones.json")
        with pytest.raises(ProofNotWritten):
            m.declare_out_of_band_deletion(a, actor="ops", reason="restored from an old backup")
    assert not m._tombstones
    assert not _disk(p)[1], "a tombstone reached the disk"
    assert m.declare_out_of_band_deletion(a, actor="ops", reason="retried")["memory_id"] == a


def test_an_objection_that_cannot_be_written_is_not_recorded(tmp_path, monkeypatch):
    p, m, a = _store(tmp_path)
    with monkeypatch.context() as mp:
        _full_disk_for(mp, ".objections.json")
        with pytest.raises(ProofNotWritten, match="not recorded"):
            m.object_processing("hr/alice", actor="dpo", ground="own_situation")
    assert not m.objections(), "the handle holds an objection the disk does not"
    assert not Inspeximus(p, receipts=True).objections()
    m.object_processing("hr/alice", actor="dpo", ground="own_situation")
    assert len(Inspeximus(p, receipts=True).objections()) == 1


def test_a_resolution_that_cannot_be_written_leaves_the_objection_standing(tmp_path, monkeypatch):
    p, m, a = _store(tmp_path)
    m.object_processing("hr/alice", actor="dpo", ground="own_situation")
    with monkeypatch.context() as mp:
        _full_disk_for(mp, ".objections.json")
        with pytest.raises(ProofNotWritten):
            m.resolve_objection("hr/alice", actor="dpo", outcome="upheld")
    assert [o["status"] for o in m.objections()] == ["standing"]
    assert [o["status"] for o in Inspeximus(p, receipts=True).objections()] == ["standing"]


def test_a_spend_that_cannot_be_written_is_not_allowed(tmp_path, monkeypatch):
    p, m, a = _store(tmp_path)
    with monkeypatch.context() as mp:
        _full_disk_for(mp, ".irrev.json")
        with pytest.raises(ProofNotWritten, match="not allowed"):
            m.spend_irreversible([a], amount=1.0, budget=1.0)
    assert m.spend_irreversible([a], amount=1.0, budget=1.0)["allowed"], \
        "the failed spend was counted against the budget in memory"
    assert not m.spend_irreversible([a], amount=1.0, budget=1.0)["allowed"], "the budget did not bind"
    assert not Inspeximus(p, receipts=True).spend_irreversible([a], amount=1.0, budget=1.0)["allowed"],         "the one allowed spend was not persisted"


def test_a_receipted_write_does_not_report_success_without_its_receipt(tmp_path, monkeypatch):
    p, m, a = _store(tmp_path)
    with monkeypatch.context() as mp:
        _full_disk_for(mp, ".receipts.json")
        with pytest.raises(ProofNotWritten, match="was saved, and its write receipt could not be written") as ei:
            m.remember("bob lives at 9 Oak Rd")
    bob = str(ei.value).split("record ")[1].split(" ")[0]
    ok, problems = Inspeximus(p, receipts=True).verify_writes()
    assert not ok and any(bob in x for x in problems), "the record saved without its receipt is not named"
    m.remember("a later write")                          # the next receipt carries the pending one
    ok, problems = Inspeximus(p, receipts=True).verify_writes()
    assert ok, problems


def test_enabling_receipts_that_cannot_be_written_leaves_receipts_off(tmp_path, monkeypatch):
    p = str(tmp_path / "s.json")
    m = Inspeximus(p)
    m.remember("a record written before receipts")
    m.flush()
    with monkeypatch.context() as mp:
        _full_disk_for(mp, ".receipts.json")
        with pytest.raises(ProofNotWritten):
            m.enable_receipts(reason="turning receipts on")
    assert not m.receipts_enabled and not m._receipts
    assert not os.path.exists(p + ".receipts.json")


def test_a_monitor_whose_statistic_cannot_be_written_says_so(tmp_path, monkeypatch):
    p, m, a = _store(tmp_path)
    with monkeypatch.context() as mp:
        _full_disk_for(mp, ".cusum.json")
        out = m.monitor([a], outcome=1.0)
    assert "not_persisted" in out, out
    assert "not_persisted" not in m.monitor([a], outcome=1.0), "a later successful write still reports a failure"
