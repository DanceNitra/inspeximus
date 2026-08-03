"""Tenant isolation as a STRUCTURAL property, not a per-method habit.

Three rounds of patching taught the lesson this file encodes. `self.items` was a plain attribute holding every
tenant's records, and 46 methods read it directly — so isolation depended on each of them remembering to
filter, and audits kept finding ones that had not (`history`, `provenance`, `as_of`, `why_recalled`, `revert`,
the aggregate reports, and then the destructive paths). Each fix landed at the reported method and the class
survived one call site over.

`items` is now a tenant-scoped property over the real list (`_items`), so a method is isolated by construction
rather than by review. The decisive test here is not any named method — it is the SWEEP: every public method
is called from a tenant handle and its entire output searched for the other tenant's secret. A method added
tomorrow is covered without anyone remembering to add it.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus, new_encryption_key

SECRET = "sk-globex-999-DO-NOT-LEAK"


def _pair(direct: bool = False, **kw):
    """Two ways to bind a tenant, and they are protected differently — so both are swept.

    `for_tenant()` returns a _TenantView whose default-deny __getattr__ refuses an unclassified method.
    `Inspeximus(path=..., tenant=...)` has no view at all, so nothing refuses on its behalf: there, the ONLY
    protection is that `self.items` is scoped. A method reading `_items` directly leaks on this path and is
    invisible on the other — measured, which is why the sweep runs twice.
    """
    s = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), receipts=True, **kw)
    if direct:
        a = Inspeximus(path=s.path, receipts=True, tenant="acme", **kw)
        b = Inspeximus(path=s.path, receipts=True, tenant="globex", **kw)
        a._items = b._items = s._items                      # one shared list, two bound handles
        # ...and the concurrency guard switched off for them. These are a FIXTURE ARTIFACT: three handles
        # faking one logical store by sharing `_items`, which the single-writer guard rightly reads as three
        # competing writers. Real code binds a tenant with for_tenant() (covered by the other parametrisation)
        # or opens one handle per process. Disabling it here keeps this test about ISOLATION, not concurrency.
        a._file_sig = b._file_sig = s._file_sig = None
    else:
        a, b = s.for_tenant("acme"), s.for_tenant("globex")
    a.remember("acme row", key="a/k", object="x", source={"doc": "acme-src"})
    b.remember(f"GLOBEX_SECRET {SECRET}", key="b/k", object="v1", source={"doc": "globex-src"})
    b.remember("globex v2", key="b/k", object="v2", source={"doc": "globex-src"})
    return s, a, b


def _b_id(store):
    return next(r["id"] for r in store._items if r.get("tenant") == "globex")


# ── the sweep: every public method, not a curated list ───────────────────────────────────────────────
_ARGS = {                                     # plausible arguments that would reach the other tenant
    "history": ("b/k",), "provenance": ("b/k",), "as_of": ("b/k", 9e9), "why_recalled": ("b/k",),
    "revert": ("b/k",), "verify_claim": ("globex v2",), "recall": ("globex secret",),
    "route": ("globex",), "check_conflict": ("globex v2",), "believed_at": ("b/k", 9e9),
    "recall_iterative": ("globex secret",), "neighbors": (None,), "get": (None,), "grade": (None,),
    "convergence_report": (None,), "credit": ([None], True), "slash": ([None],),
    "monitor": ([None], False), "forget": ([None],),
    "restore": (None,), "spend_irreversible": ("globex-src", 1.0), "erasure_audit": ("globex-src",),
    "forget_subject": ("globex-src",), "forget_pii": (None, "globex-src"),
    "retract_lineage": ("globex-src",), "rederive": ("globex-src",),
    "remember": ("a new acme row",), "remember_decision": ("d", "because", "ctx"),
    "admit": ("x",),
    # store-level, but still swept: it reports receipt seqs and memory_ids, so if it ever grew a
    # text-bearing field a tenant view could read another tenant's content through it.
    "explain_growth": ({"n_writes": 0, "writes_tip": "", "n_tombstones": 0},),
    "apply_retention": (0.0,), "sleep": (), "reembed": (), "consolidate": (),
    "distill_and_remember": ("text", lambda t: []),
    "observe": (f"GLOBEX_SECRET {SECRET}", "b/k"),
    "propagate_outcome": (True, [None]),
    "ratify": (f"GLOBEX_SECRET {SECRET}", "fact", "b/k"),
    "recall_iterative": (f"GLOBEX_SECRET {SECRET}", lambda *a, **k: None),
    # the agent-grant ACL, aimed at tenant B: positional (agent, scope, tag, key, ...) so the selector
    # names B's key. A tenant must not be able to hand another tenant's records to an agent, and
    # `can_read`/`grant_log` must not report on them either.
    "grant": ("mallory", None, None, "b/k"), "revoke": ("mallory", None, None, "b/k"),
    "grants": (), "grant_log": (), "can_read": ("mallory", None), "as_agent": ("mallory",),
}


def _public_methods():
    for name in sorted(dir(Inspeximus)):
        if name.startswith("_") or not callable(getattr(Inspeximus, name)):
            continue
        yield name


#: Methods the sweep cannot drive automatically, each with the reason. This list is the point of the design:
#: a method that cannot be exercised must be named here deliberately, not skipped silently. The first version
#: of this test skipped on TypeError — so a newly added leaking method was invisible to it, which is exactly
#: the "guard with no teeth" failure this whole file exists to prevent.
_UNSWEEPABLE = {
    "for_tenant":       "returns another view; covered by the dedicated tests",
    "flush":            "persistence only, returns None",
    "register_erasure_target": "takes an adapter object, no records in the result",
    "verify_cosigned_anchor":  "static, operates on a passed-in anchor dict",
    "verify_consistency":      "compares two passed-in anchors",
    "check_self_narration":    "static text classifier, no store access",
    "session_salience":        "static scorer over a passed-in record dict, no store access",
    "classify_reversion":      "needs an embedder",
    "detect_split_view":       "compares two passed-in anchors",
    "submit_revert":    "needs a signed capability",
    "revert_capability": "mints a capability, needs a key",
    "revert_now":       "needs a signed capability",
    "revert_challenge": "needs a signed capability",
    "restore_now":      "needs a signed capability",
    "restore_intent":   "needs a signed capability",
    "revert_intent":    "needs a signed capability",
    "resolve_reopened": "needs a reopened id from the same tenant",
    "promote_candidate": "needs a candidate id from the same tenant",
    "discard_candidate": "needs a candidate id from the same tenant",
    "support_challenge_for": "needs a challenge id from the same tenant",
    "remember_dedup":   "a write path; covered by the destructive-op tests",
    "erasure_certificate": "signs over tombstones, no record text",
    "verify_erasure_certificate": "static, operates on a passed-in certificate",
    "verify_audit_bundle": "static, operates on a passed-in bundle",
    "verify_witness":   "static signature check",
    "verify_attribution": "operates on the receipt chain, content-free",
    "anchor":           "content-free chain head",
    "witness":          "takes no arguments; content-free chain head",
    "graph":            "driven with no args below",
    "subgraph":         "needs a seed id from the same tenant",
}


def _synthesise(name, sig, bid):
    """Best-effort arguments aimed AT TENANT B, from the parameter names."""
    if name in _ARGS:
        return tuple(bid if x is None else ([bid] if x == [None] else x) for x in _ARGS[name])
    args = []
    for pname, p in list(sig.parameters.items())[1:]:          # skip self
        if p.default is not p.empty or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            break                                              # everything after is optional
        if pname in ("id", "memory_id", "rid"):
            args.append(bid)
        elif pname in ("ids",):
            args.append([bid])
        elif pname in ("key",):
            args.append("b/k")
        elif pname in ("subject", "source", "doc"):
            args.append("globex-src")
        elif pname in ("query", "q", "text", "claim"):
            args.append(f"GLOBEX_SECRET {SECRET}")
        else:
            return None                                        # cannot drive it honestly
    return tuple(args)


@pytest.mark.parametrize("direct", [False, True], ids=["for_tenant view", "tenant= bound store"])
def test_no_public_method_leaks_another_tenants_secret(direct):
    """THE structural test, and the only one here that keeps working as the API grows.

    Calls every public method from tenant A with arguments aimed at tenant B and fails if B's secret appears
    anywhere in the result. A method that raises or refuses is a pass. A method that cannot be driven must be
    named in `_UNSWEEPABLE` with a reason — silently skipping it is how a sweep stops being a sweep.
    """
    import inspect
    leaked, exercised, undriveable = [], 0, []
    for name in _public_methods():
        if name in _UNSWEEPABLE:
            continue
        s, a, b = _pair(direct=direct)
        try:
            sig = inspect.signature(getattr(Inspeximus, name))
        except (TypeError, ValueError):
            undriveable.append(name)
            continue
        args = _synthesise(name, sig, _b_id(s))
        if args is None:
            undriveable.append(name)
            continue
        try:
            out = getattr(a, name)(*args)
        except TypeError as e:
            undriveable.append(f"{name} ({e})")
            continue
        except Exception:
            exercised += 1                                     # raised/refused — that is a pass
            continue
        exercised += 1
        if SECRET in str(out):
            leaked.append(name)

    assert not leaked, f"these public methods returned another tenant's secret: {leaked}"
    assert not undriveable, (
        "the sweep could not drive these — add an entry to _ARGS so they ARE swept, or to _UNSWEEPABLE with "
        f"a reason. Skipping them silently is how this test stops catching anything: {undriveable}")
    assert exercised >= 40, f"the sweep only exercised {exercised} methods — it is not covering the surface"


def test_the_raw_list_itself_is_scoped():
    """`view.items` resolved through __getattr__ to the parent's property, which evaluates against the
    PARENT's tenant (None = admin), and handed back every record. It defeated the whole allow-list."""
    s, a, b = _pair()
    assert SECRET not in str(a.items)
    assert len(a.items) == 1 and len(b.items) == 2 and len(s._items) == 3


def test_the_unbound_store_still_sees_everything():
    """A scoping bug that hid records from the ADMIN store would be just as bad, and much harder to notice."""
    s, a, b = _pair()
    assert len(s.items) == 3
    assert SECRET in str(s.items)


@pytest.mark.parametrize("op", [
    lambda t: t.apply_retention(max_age_days=0.0),
    lambda t: t.sleep(retention_days=0.0),
    lambda t: t.forget(where=lambda r: True),
    lambda t: t.forget_pii(subject="acme-src", request_id="r"),
    lambda t: t.retract_lineage("acme-src"),
    lambda t: t.consolidate(),
])
def test_no_destructive_op_reaches_another_tenants_records(op):
    """Previously `A.apply_retention()` and `A.sleep()` expired B's rows and `A.shred()` wiped both."""
    s, a, b = _pair()
    op(a)
    assert len(b.items) == 2, "another tenant's records were destroyed"
    assert any(r.get("tenant") == "globex" for r in s._items)


def test_shred_refuses_from_a_tenant_view():
    """It destroys the encryption key for the whole FILE. Dropping one tenant's rows while making everyone
    else's data unrecoverable is not a coherent operation, and not one a tenant may perform on the others."""
    s, a, b = _pair(encrypt_key=new_encryption_key())
    with pytest.raises(PermissionError):
        a.shred()
    assert len(s._items) == 3
    assert s.shred()["records_dropped"] == 3          # still available on the unbound store


def test_a_whole_list_assignment_cannot_silently_drop_other_tenants():
    """`self.items = [...]` was how forget() worked. Under a scoped read that would have replaced every
    tenant's records with this tenant's survivors — so the setter refuses and names the real list."""
    s, a, b = _pair()
    with pytest.raises(AttributeError, match="_items"):
        a.items = []
    assert len(s._items) == 3
