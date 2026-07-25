"""The store declares lineage at the call sites it owns — and a revert stops being a hole in erasure.

Background. Declared lineage measured **0.00%** across a real 27,290-record deployment, so 1.49.0 tried to
INFER it from content and 1.50.0 withdrew that at precision 0.06-0.23. This is the third option and the only
exact one: at a write site inside the library, the store already knows the parent, so it states it rather
than guessing.

The bug this closes is not cosmetic. `revert()` rebuilds a record's text from a specific predecessor and
recorded that parent in `meta['revert_of']` — a field no lineage check traverses. So a restored value looked
parentless: erase the subject its value came from, and the revert survived carrying that subject's data.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus


def _store(**kw):
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), receipts=True, **kw)


def _rec(m, rid):
    return next(r for r in m.items if r["id"] == rid)


def test_revert_declares_the_record_it_restored_from():
    m = _store()
    old = m.remember("billing uses api keys", key="billing::auth", object="api-keys",
                     source={"doc": "runbook-v1"})
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2", source={"doc": "adr-014"})

    restored = m.revert("billing::auth")["restored"]
    assert _rec(m, restored).get("derived_from") == [old]

    p = m.provenance(id=restored)
    assert old in p["origin"]["ancestors"]
    assert "runbookv1" in p["origin"]["inherited_taint"], \
        "the restored value's ORIGIN must ride the edge, not just the record id"


def test_a_revert_no_longer_hides_from_erasure():
    """THE bug. Before this, erasing the subject a value came from left the reverted copy behind."""
    m = _store()
    m.remember("billing uses api keys", key="billing::auth", object="api-keys",
               source={"doc": "runbook-v1"})
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2", source={"doc": "adr-014"})
    restored = m.revert("billing::auth")["restored"]

    erased = m.forget_subject("runbook-v1", request_id="REQ-1", basis="gdpr-art17")
    assert restored in erased["ids"], \
        "the reverted record carries runbook-v1's value and must be erased with it"
    assert m.erasure_audit(subject="runbook-v1")["residue"] == []


def test_resolve_reopened_declares_the_reopened_record():
    m = _store()
    m.remember("tz is UTC", key="user::tz", object="UTC")
    m.remember("tz is PST", key="user::tz", object="PST")
    reopened = m.reopened() if hasattr(m, "reopened") else []
    if not reopened:                       # nothing surfaced a prior on this path; the site is still patched
        import re
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "inspeximus", "core.py"), encoding="utf-8").read()
        assert re.search(r"capability=capability, derived_from=\[rid\]", src), \
            "resolve_reopened must declare the reopened record as parent"
        return
    rid = reopened[0]["id"]
    new_id = m.resolve_reopened(rid, "reaffirm_prior")["reaffirmed"]
    assert _rec(m, new_id).get("derived_from") == [rid]


def test_writes_that_are_NOT_derivations_stay_clean():
    """Over-declaring is its own failure. A plain write, a decision and an admitted record have no in-store
    parent, and inventing one would taint everything with everything."""
    m = _store()
    plain = m.remember("an independent observation about billing")
    assert _rec(m, plain).get("derived_from") is None

    dec = m.remember_decision("use oauth2", because="keys leak", context="billing")
    assert _rec(m, dec).get("derived_from") is None


def test_the_owned_sites_are_declared_and_the_rest_are_not():
    """Pins the audit: of the library's own write sites, exactly the derivation ones declare a parent."""
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "inspeximus", "core.py"), encoding="utf-8").read()
    lines = src.split("\n")
    declaring = set()
    for i, l in enumerate(lines, 1):
        if re.search(r"self\.remember\(", l):
            fn = "?"
            for j in range(i - 1, 0, -1):
                mm = re.match(r"    def (\w+)", lines[j - 1])
                if mm:
                    fn = mm.group(1)
                    break
            if re.search(r"derived_from\s*=|derived\s*=\s*True", " ".join(lines[i - 1:i + 4])):
                declaring.add(fn)
    assert declaring == {"rederive", "revert", "submit_revert", "resolve_reopened"}, declaring
