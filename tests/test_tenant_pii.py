"""Tenant isolation + PII layer (1.6.0). Zero-dependency; run with `python -m pytest tests/test_tenant_pii.py`."""
import os
import tempfile
import time

from inspeximus import Inspeximus, detect_pii, redact_pii


# ── hard tenant isolation ────────────────────────────────────────────────────

def test_recall_never_crosses_tenants():
    # one physical store, two tenant views sharing it (Inspeximus.for_tenant)
    store = Inspeximus()
    a = store.for_tenant("acme")
    b = store.for_tenant("globex")
    a.remember("acme deploy key is ACME-SECRET-123", key="deploy::key", object="ACME-SECRET-123")
    b.remember("globex deploy key is GLOBEX-SECRET-999", key="deploy::key", object="GLOBEX-SECRET-999")
    ra = a.recall("deploy key", k=10)
    rb = b.recall("deploy key", k=10)
    assert len(ra) == 1 and "ACME-SECRET-123" in ra[0]["text"]
    assert len(rb) == 1 and "GLOBEX-SECRET-999" in rb[0]["text"]
    assert all("GLOBEX" not in x["text"] for x in ra)
    assert all("ACME" not in x["text"] for x in rb)
    # one shared items list, no clobber
    assert len(store.items) == 2


def test_bound_constructor_own_file_isolation():
    # the simple model: Inspeximus(tenant=...) with its own file is trivially isolated
    import tempfile, os
    d = tempfile.mkdtemp()
    a = Inspeximus(path=os.path.join(d, "acme.json"), tenant="acme")
    a.remember("acme only")
    got = a.recall("acme", k=5)
    assert len(got) == 1 and got[0].get("text") == "acme only"


def test_same_key_does_not_supersede_across_tenants():
    store = Inspeximus()
    a = store.for_tenant("t1")
    b = store.for_tenant("t2")
    a.remember("plan is pro", key="billing::plan", object="pro")
    b.remember("plan is free", key="billing::plan", object="free")   # must NOT retire t1's active row
    actives = [r for r in store.items if r.get("status") == "active"]
    assert len(actives) == 2
    # within a tenant, keyed supersession still works
    a.remember("plan is enterprise", key="billing::plan", object="enterprise")
    ra = a.recall("plan", k=10)
    assert len(ra) == 1 and "enterprise" in ra[0]["text"]
    rb = b.recall("plan", k=10)
    assert len(rb) == 1 and "free" in rb[0]["text"]           # t2 untouched


def test_unbound_store_is_admin_view():
    store = Inspeximus()
    store.for_tenant("t1").remember("t1 fact alpha")
    store.for_tenant("t2").remember("t2 fact beta")
    seen = store.recall("fact", k=10)                         # unbound parent sees everything
    texts = " ".join(x["text"] for x in seen)
    assert "alpha" in texts and "beta" in texts


def test_forget_subject_is_tenant_scoped():
    store = Inspeximus()
    a = store.for_tenant("t1")
    b = store.for_tenant("t2")
    a.remember("shared-subject data A", source={"doc": "user-42"})
    b.remember("shared-subject data B", source={"doc": "user-42"})
    res = a.forget_subject("user-42")     # only t1's row
    assert res["erased"] == 1
    assert len(store.items) == 1 and store.items[0].get("tenant") == "t2"


def test_consolidation_does_not_cross_tenants():
    # The dream pass (consolidate) links/dedups/supersedes; on a tenant view it must not touch other tenants.
    store = Inspeximus()
    a = store.for_tenant("t1")
    b = store.for_tenant("t2")
    phrase = "restart the api gateway nightly at midnight utc per the runbook"
    a.remember("t1: " + phrase)
    b.remember("t2: " + phrase)            # near-duplicate across tenants -> would link if unscoped
    a.consolidate(dup_threshold=0.5)
    # no t1 row may link to a t2 row
    t2_ids = {r["id"] for r in store.items if r.get("tenant") == "t2"}
    for r in store.items:
        if r.get("tenant") == "t1":
            assert not (set(r.get("links") or []) & t2_ids)
    # and t2's row is untouched (still active, no foreign toggle pointer)
    t2row = [r for r in store.items if r.get("tenant") == "t2"][0]
    assert t2row["status"] == "active"


def test_unbound_consolidate_links_across_when_no_tenants():
    # severe-test control: without tenants, the SAME corpus DOES link (proves the guard above prevents a real leak)
    m = Inspeximus()
    phrase = "restart the api gateway nightly at midnight utc per the runbook"
    m.remember("alpha: " + phrase)
    m.remember("beta: " + phrase)
    m.consolidate(dup_threshold=0.5)
    assert sum(len(r.get("links") or []) for r in m.items) > 0


def test_legacy_unbound_is_byte_identical():
    # No tenant anywhere -> no `tenant` key stamped, legacy supersession intact.
    m = Inspeximus()
    m.remember("timeout setting is short", key="cfg::timeout", object="short")
    m.remember("timeout setting is long", key="cfg::timeout", object="long")
    got = m.recall("timeout setting", k=10)
    assert len(got) == 1 and got[0]["text"].endswith("long")
    assert all("tenant" not in r for r in m.items)


# ── PII detection + redaction ────────────────────────────────────────────────

def test_detect_pii_types():
    d = detect_pii("mail me at jane.doe@acme.io or call 555-123-4567, ssn 123-45-6789")
    assert "email" in d and "jane.doe@acme.io" in d["email"]
    assert "ssn" in d and "123-45-6789" in d["ssn"]
    assert "phone" in d


def test_ssn_not_eaten_by_phone():
    # specific pattern (SSN) must claim the span before the broad phone pattern
    d = detect_pii("ssn 123-45-6789")
    assert d.get("ssn") == ["123-45-6789"]
    assert "phone" not in d


def test_redact_pii_masks_and_counts():
    masked, counts = redact_pii("write to bob@x.com now")
    assert "bob@x.com" not in masked and "[EMAIL]" in masked
    assert counts.get("email") == 1


def test_remember_tags_pii_when_detect_on():
    m = Inspeximus(pii_detect=True)
    mid = m.remember("customer email is carol@corp.com")
    rec = [r for r in m.items if r["id"] == mid][0]
    assert rec.get("pii") == ["email"]


def test_remember_pii_override():
    m = Inspeximus()
    mid = m.remember("no obvious pii here", pii=["custom_id"])
    rec = [r for r in m.items if r["id"] == mid][0]
    assert rec.get("pii") == ["custom_id"]
    # pii=False suppresses even with detect on
    m2 = Inspeximus(pii_detect=True)
    mid2 = m2.remember("email a@b.com", pii=False)
    rec2 = [r for r in m2.items if r["id"] == mid2][0]
    assert "pii" not in rec2


def test_recall_redact_pii_masks_return_not_store():
    m = Inspeximus(pii_detect=True)
    m.remember("the account owner is dave@bank.com")
    got = m.recall("account owner", k=5, redact_pii=True)
    assert got and "dave@bank.com" not in got[0]["text"] and "[EMAIL]" in got[0]["text"]
    assert got[0].get("pii_masked", {}).get("email") == 1
    # stored record is untouched
    assert any("dave@bank.com" in r["text"] for r in m.items)


def test_pii_report_and_forget_pii():
    m = Inspeximus(pii_detect=True)
    m.remember("email one: a@x.com")
    m.remember("email two: b@y.com")
    m.remember("no pii in this one at all")
    rep = m.pii_report()
    assert rep["records_with_pii"] == 2 and rep["by_type"]["email"] == 2
    res = m.forget_pii(types=["email"])
    assert res["erased"] == 2 and res["tombstones"] == 2
    assert m.pii_report()["records_with_pii"] == 0
    # non-PII record survives
    assert any("no pii" in r["text"] for r in m.items)


def test_forget_pii_is_tenant_scoped():
    store = Inspeximus(pii_detect=True)
    a = store.for_tenant("t1")
    b = store.for_tenant("t2")
    a.remember("t1 email a@x.com")
    b.remember("t2 email b@y.com")
    res = a.forget_pii()
    assert res["erased"] == 1
    assert len(store.items) == 1 and store.items[0].get("tenant") == "t2"
    # t2's PII view is intact
    assert b.pii_report()["records_with_pii"] == 1


def test_tenant_view_pii_report_isolated():
    store = Inspeximus(pii_detect=True)
    store.for_tenant("t1").remember("t1 a@x.com")
    store.for_tenant("t2").remember("t2 b@y.com and c@z.com")
    assert store.for_tenant("t1").pii_report()["records_with_pii"] == 1
    assert store.for_tenant("t2").pii_report()["by_type"]["email"] == 1  # one record, tagged email


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok", fn.__name__)
    print(f"\n{len(fns)} passed")


# ─────────────────────── the detector fired on shapes it names, and a sweep DELETED them
import pytest
from inspeximus.core import detect_pii


NOT_PII = [
    ("2026-07-29", "an ISO date read as a phone number"),
    ("release 20260731-191913", "a build timestamp read as a credit card"),
    ("ORCID 0009-0009-4792-1433", "an ORCID read as a credit card"),
    ("server at 127.0.0.1", "loopback is not a person"),
    ("bind 0.0.0.0", "the unspecified address is not a person"),
    ("broadcast 255.255.255.255", "broadcast is not a person"),
    ("link local 169.254.1.1", "link-local is not a person"),
    ("arXiv 2604.04089", "an arXiv id read as a phone number"),
    ("upgraded 2.0.11 0.20", "version strings read as a phone number"),
    ("the 2025-2026 season", "a year range read as a phone number"),
    ("logged 2026-07-21 07", "a date plus an hour read as a phone number"),
]

STILL_PII = [
    "alice@example.com",
    "+421 903 123 456",
    "(555) 123-4567",
    "4111 1111 1111 1111",
    "5500 0000 0000 0004",
    "123-45-6789",
    "159.100.251.128",
]


@pytest.mark.parametrize("text,why", NOT_PII)
def test_shapes_that_cannot_be_pii_are_not_tagged(text, why):
    """Measured on our own 616-record decision store BEFORE this fix: 478 of 576 active records
    'matched', overwhelmingly dates and build timestamps. The count was not the damage."""
    assert detect_pii(text) == {}, why


@pytest.mark.parametrize("text", STILL_PII)
def test_real_pii_is_still_detected(text):
    """The other direction, and the one that matters more: on this path a false negative is
    undeleted personal data. Every rejection above is a shape that CANNOT be the labelled type,
    never one that merely looks unlikely."""
    assert detect_pii(text), "a validator traded a false positive for a false negative"


def test_a_gdpr_sweep_does_not_hard_delete_a_record_whose_only_pii_is_a_date():
    """THE defect, asserted where it hurt. `pii_detect=True` tagged '2026-07-29' as a phone, and
    `forget_pii()` hard-deletes every tagged record -- so a data-minimization sweep erased an
    ordinary record. Verified against the pre-fix code before this was written."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), pii_detect=True)
    ix.remember("the meeting is on 2026-07-29", key="meeting", object="cal")
    ix.remember("carol@example.com signed", key="signer", object="contract")
    ix.flush()
    out = ix.forget_pii()
    survivors = [r["text"] for r in ix.items if r.get("status") == "active"]
    assert "the meeting is on 2026-07-29" in survivors, "a date was erased as PII"
    assert out["erased"] == 1, out


def test_pii_report_says_when_its_zero_means_nobody_looked():
    """`records_with_pii: 0` read identically for 'nothing here' and 'detection was never on', and a
    record written while it was off is never backfilled by turning it on."""
    path = os.path.join(tempfile.mkdtemp(), "s.json")
    off = Inspeximus(path=path)
    off.remember("dave@example.com wrote in", object="x")
    off.flush()
    rep = off.pii_report()
    assert rep["records_with_pii"] == 0
    assert rep["coverage"]["untagged_matches"] == 1, rep
    assert "forget_pii" in rep["coverage"]["problem"]

    on = Inspeximus(path=path, pii_detect=True)          # the retrofit trap
    assert on.pii_report()["coverage"]["untagged_matches"] == 1, "enabling detection backfilled?"


def test_a_clean_store_is_not_given_an_invented_gap():
    """The negative control. A store with detection on and nothing to find must stay quiet."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), pii_detect=True)
    ix.remember("the rate is 5 percent", object="x")
    ix.flush()
    cov = ix.pii_report()["coverage"]
    assert cov["untagged_matches"] == 0 and "problem" not in cov, cov


def test_forget_pii_names_what_it_could_not_sweep():
    """A destructive op that reports success over a partial pass is what a DSAR cannot afford."""
    path = os.path.join(tempfile.mkdtemp(), "s.json")
    Inspeximus(path=path).remember("erin@example.com early", object="x")
    early = Inspeximus(path=path)
    early.flush()
    late = Inspeximus(path=path, pii_detect=True)
    late.remember("frank@example.com later", object="x")
    late.flush()
    out = late.forget_pii()
    assert out["erased"] == 1, out
    assert out["unswept_matches"]["count"] == 1, out
    assert "PARTIAL" in out["unswept_matches"]["problem"]
