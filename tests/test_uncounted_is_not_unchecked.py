"""Three verifiers that reported health about records they never counted.

An adversarial audit found the same shape in three more places. The common error is not a wrong
comparison; it is a set that the thing being looked for cannot enter. A record with no receipt could not
land in `relabeled` OR in `uncommitted`; survivors with no receipt could not raise a coverage violation
because coverage was two integers; another component's memories could not be spared by a `clear()` that
selected on `status` alone.

Uncounted reads exactly like checked-and-clean. That is why each of these survived a green suite.
"""
import asyncio
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402
from inspeximus.compliance import compliance_check  # noqa: E402


def _store(**kw):
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), **kw)


# ── verify_attribution ──────────────────────────────────────────────────────────────────────────────
def test_attribution_on_an_unreceipted_store_is_not_ok():
    """The same store, the same instant: verify_writes said False with exactly the right words while its
    sibling said {'ok': True, 'uncommitted': []} -- and every `source` label had been rewritten on disk.
    Records with no receipt never entered the loop, so they were not unchecked, they were uncounted."""
    m = _store()                                    # receipts OFF, which is the default
    rid = m.remember("quarterly revenue is 100M", source={"doc": "finance-report"})
    m.flush()
    next(x for x in m._items if x["id"] == rid)["source"] = {"doc": "attacker-blog"}
    m._save(force=True)

    res = m.verify_attribution()
    assert res["ok"] is False, "an attribution that cannot be checked is not a verified one"
    assert res["problems"], res
    assert "DISABLED" in res["problems"][0]
    assert m.verify_writes()[0] is False, "the sibling gate must still agree, or one of them is lying"


def test_an_unreceipted_record_is_reported_as_uncommitted():
    """What the docstring already promised: 'the memory was never receipted' appears in `uncommitted`."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "m.json")
    m = Inspeximus(path=p, receipts=True)
    m.remember("receipted", source={"doc": "a"})
    m.flush()
    m.receipts_enabled = False
    later = m.remember("written while receipts were off", source={"doc": "b"})
    m.flush()

    res = m.verify_attribution()
    assert later in res["uncommitted"], res
    assert res["ok"] is False


def test_a_clean_receipted_store_still_verifies():
    """The fix must not buy safety by refusing everything."""
    m = _store(receipts=True)
    m.remember("quarterly revenue is 100M", source={"doc": "finance-report"})
    m.flush()
    assert m.verify_attribution()["ok"] is True


def test_a_relabel_is_still_caught():
    m = _store(receipts=True)
    rid = m.remember("quarterly revenue is 100M", source={"doc": "finance-report"})
    m.flush()
    next(x for x in m._items if x["id"] == rid)["source"] = {"doc": "attacker-blog"}
    m._save(force=True)
    res = m.verify_attribution()
    assert res["relabeled"] == [rid] and res["ok"] is False


# ── compliance_check receipt coverage ───────────────────────────────────────────────────────────────
def _survivors_without_receipts():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "m.json")
    m = Inspeximus(path=p, receipts=True)
    ids = [m.remember(f"receipted {i}") for i in range(4)]
    m.flush()
    m.forget(ids=ids)                     # our own Art.17 path: rows go, the write chain stays
    m.flush()
    m2 = Inspeximus(path=p, receipts=True)
    m2.receipts_enabled = False
    m2.remember("survivor A")
    m2.remember("survivor B")
    m2.flush()
    return m2


def test_coverage_is_per_record_not_a_count_of_receipts():
    """`n_records > n_receipts` compares two integers, so a store holding MORE receipts than records passed
    while none of the survivors was covered by any of them.

    HONEST NOTE: this store was already failing overall, via `integrity_failed` -- the audit finding said
    it returned ok=True with no violations at all, and that did NOT reproduce. The coverage check really
    was blind; it was not the last line of defence. Fixed because a gate that cannot see its own subject is
    worth fixing either way, and because the violation now NAMES the records."""
    m = _survivors_without_receipts()
    res = compliance_check(m)
    codes = [v["code"] for v in res["violations"]]
    assert "receipts_partial" in codes, codes

    detail = next(v["detail"] for v in res["violations"] if v["code"] == "receipts_partial")
    assert "2 of 2" in detail, detail
    for rec in [r for r in m.items if r.get("status") == "active"]:
        assert rec["id"][:8] in detail or "more" in detail


def test_a_fully_receipted_store_still_passes_the_gate():
    m = _store(receipts=True)
    m.remember("fully receipted")
    m.flush()
    assert compliance_check(m)["ok"] is True, "a gate that always fires is not a gate"


# ── the AutoGen adapter's clear() ───────────────────────────────────────────────────────────────────
class _Content:
    def __init__(self, text):
        self.content, self.metadata, self.mime_type = text, {}, "text/plain"


def _autogen(store, **kw):
    pytest.importorskip("autogen_core", reason="the AutoGen adapter needs autogen-core")
    from inspeximus.integrations.autogen import InspeximusMemory
    return InspeximusMemory(store=store, **kw)


def test_clear_erases_this_memory_and_not_the_whole_store():
    """`forget()` is the irreversible operation. clear() selected every active record in the store and
    called it, so a store shared with any other component lost all of it on a call whose contract is
    "clear MY memory". Measured before the fix: 3 records in, 0 out, 2 of them someone else's. The
    LangChain, CrewAI and OpenAI-Agents adapters all scope theirs."""
    store = _store()
    store.remember("the finance team owns the cloud budget")
    store.remember("escalation contact is Petra")
    mem = _autogen(store, source="agent-a")
    asyncio.run(mem.add(_Content("agent-a scratch note")))
    assert len([r for r in store.items if r.get("status") == "active"]) == 3

    asyncio.run(mem.clear())
    survivors = [r["text"] for r in store.items if r.get("status") == "active"]
    assert len(survivors) == 2, survivors
    assert "the finance team owns the cloud budget" in survivors


def test_clear_still_erases_its_own_records():
    """Scoping must not turn clear() into a no-op -- that would be the other way to fail this."""
    store = _store()
    mem = _autogen(store, source="agent-a")
    asyncio.run(mem.add(_Content("one")))
    asyncio.run(mem.add(_Content("two")))
    asyncio.run(mem.clear())
    assert [r for r in store.items if r.get("status") == "active"] == []


def test_two_adapters_on_one_store_do_not_erase_each_other():
    store = _store()
    a, b = _autogen(store, source="agent-a"), _autogen(store, source="agent-b")
    asyncio.run(a.add(_Content("a's note")))
    asyncio.run(b.add(_Content("b's note")))
    asyncio.run(a.clear())
    survivors = [r["text"] for r in store.items if r.get("status") == "active"]
    assert survivors == ["b's note"], survivors
