"""ComplianceMixin: an integration store that holds an inspeximus in self.store gets the EU AI Act evidence
ops on the same object — report/check/audit/retention — by pure delegation. LangGraph wired + tested when the
lib is present; the mixin itself is framework-free and always tested."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus.core import Inspeximus
from inspeximus.integrations.governance import ComplianceMixin


class _Holder(ComplianceMixin):
    """Minimal framework-free store that just holds an inspeximus — proves the mixin needs nothing else."""
    def __init__(self, receipts=True):
        self.store = Inspeximus(path=None, receipts=receipts)


def test_mixin_delegates_all_ops():
    h = _Holder()
    h.store.remember("retention is 90d", key="p::ret", object="90d")
    h.store.remember("retention is 30d", key="p::ret", object="30d")    # correction
    rep = h.compliance_report()
    assert len(rep["controls"]) == 21 and rep["summary"]["writes"] == 2, rep["summary"]
    assert h.compliance_check()["ok"]
    b = h.audit_bundle()
    assert b["anchor"]["n_writes"] == 2
    assert ComplianceMixin.verify_audit_bundle(b)["ok"]


def test_mixin_retention_enforces():
    h = _Holder()
    h.store.remember("old a@b.com", key="c::email", object="a@b.com", pii=["email"])
    ts = h.store.items[-1]["ts"]
    future = ts + 100 * 86400                                            # evaluate 100 days later
    dry = h.retention(30, apply=False, now_ts=future)
    assert dry["eligible"] == 1 and not dry["applied"]
    applied = h.retention(30, apply=True, now_ts=future)
    assert applied["erased"] == 1 and len(h.store._tombstones) == 1


def test_mixin_check_flags_no_receipts():
    h = _Holder(receipts=False)
    h.store.remember("x", key="k", object="1")
    assert not h.compliance_check()["ok"]                                # records but no receipts -> violation


def test_langgraph_store_is_compliance_aware():
    try:
        from inspeximus.integrations.langgraph import InspeximusStore
    except Exception:
        return                                                          # langgraph not installed -> skip
    s = InspeximusStore(receipts=True)
    s.put(("user", "42"), "pref", {"lang": "sk"})
    s.put(("user", "42"), "pref", {"lang": "en"})                       # supersedes
    assert isinstance(s.compliance_report()["controls"], list)
    assert s.compliance_check()["ok"]
    assert InspeximusStore.verify_audit_bundle(s.audit_bundle())["ok"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS {fn.__name__}"); p += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{p}/{len(fns)} passed")
    sys.exit(0 if p == len(fns) else 1)
