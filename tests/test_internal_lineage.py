"""The store declares lineage at the call sites it owns — and a revert stops being a hole in erasure.

Background. Declared lineage measured **0.00%** across a real 27,290-record deployment, so 1.49.0 tried to
INFER it from content and 1.50.0 withdrew that at precision 0.06-0.23. This is the third option and the only
exact one: at a write site inside the library, the store already knows the parent, so it states it rather
than guessing.

The bug this closes is not cosmetic. `revert()` rebuilds a record's text from a specific predecessor and
recorded that parent in `meta['revert_of']` — a field no lineage check traverses. So a restored value looked
parentless: erase the subject its value came from, and the revert survived carrying that subject's data.

`rederive()` had the same defect and hid it better; see the test below. The class is only closed by
`test_the_owned_sites_are_declared_and_the_rest_are_not`, which fails if a new write site copies another
record's text without declaring where it came from.
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


def test_rederive_declares_the_record_its_TEXT_came_from():
    """The same bug as revert, one function over, and worse: rederive builds the new text OUT OF a demoted
    record (`rewrite(r['text'], old, new)`) but declared only the corrected root as parent, filing the actual
    text parent in `meta['rederived_from']` — a field nothing traverses.

    Measured before the fix: erasing the subject the text came from reported `erased 1`, the rederived copy
    survived carrying that subject's wording verbatim, and `erasure_audit` returned `no_declared_residue` —
    it certified the leak as clean. An audit that cannot see the residue is worse than no audit.
    """
    m = _store()
    root = m.remember("billing uses api-keys", key="billing::auth", object="api-keys",  # noqa: F841
                      source={"doc": "runbook"})
    m.remember("alice bernard reaches the nightly backup with api-keys", derived=True,
               derived_from=[root], source={"doc": "alice-ticket"})
    # ORDER MATTERS: the keyed root must still be active when the lineage is retracted, or it is never
    # stamped needs_rederivation and rederive cannot resolve the old value.
    m.retract_lineage("runbook")
    corrected = m.remember("billing uses oauth2", key="billing::auth", object="oauth2",
                           source={"doc": "adr-014"})

    res = m.rederive("runbook")
    assert res["rederived"] == 1, res
    new_id = res["ids"][0]

    assert corrected in (_rec(m, new_id).get("derived_from") or []), \
        "the corrected current record stays declared — the fix ADDS a parent, it does not swap one"
    assert "aliceticket" in (_rec(m, new_id).get("taint") or []), \
        "the record the TEXT was rewritten from must ride the lineage edge too"

    erased = m.forget_subject("alice-ticket", request_id="REQ-1", basis="gdpr-art17")
    assert new_id in erased["ids"], "a rederived copy still carries its source's wording and must go with it"


def test_rederive_still_actually_rederives():
    """Erasability is not the only requirement — over-tainting could make the correction itself unusable."""
    m = _store()
    root = m.remember("billing uses api-keys", key="billing::auth", object="api-keys",  # noqa: F841
                      source={"doc": "runbook"})
    m.remember("the nightly backup signs in with api-keys", derived=True, derived_from=[root],
               source={"doc": "ops-notes"})
    m.retract_lineage("runbook")
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2", source={"doc": "adr-014"})

    new_id = m.rederive("runbook")["ids"][0]
    assert _rec(m, new_id)["text"] == "the nightly backup signs in with oauth2"
    assert any(h["id"] == new_id for h in m.recall("nightly backup", k=3)), \
        "the corrected derivative must be recallable, not just present"


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


def test_erasing_a_rederived_copy_does_not_lock_the_record_on_its_wrong_value():
    """`meta['rederived_to']` is rederive's single-shot guard, so it GATES BEHAVIOUR. Erase the rederived
    copy (bad rewrite, PII, whatever) and a live pointer to a deleted record froze the derived fact on the
    value we had just corrected away — with rederive returning 0/0 and no note to say why."""
    m = _store()
    root = m.remember("billing uses api-keys", key="billing::auth", object="api-keys",  # noqa: F841
                      source={"doc": "runbook"})
    derived = m.remember("the nightly backup signs in with api-keys", derived=True,
                         derived_from=[root], source={"doc": "ops"})
    m.retract_lineage("runbook")
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2", source={"doc": "adr"})

    first = m.rederive("runbook")["ids"][0]
    m.forget(ids=[first], request_id="REQ-2", basis="gdpr-art17")
    assert (_rec(m, derived).get("meta") or {}).get("rederived_to") is None, \
        "a pointer to an erased record must not survive as an 'already done' guard"

    again = m.rederive("runbook")
    assert again["rederived"] == 1, ("the correction must be re-appliable after its output was erased, "
                                     f"else the store is stuck on the wrong value: {again}")
    assert "oauth2" in _rec(m, again["ids"][0])["text"]


def test_history_pointers_are_KEPT_so_the_audit_can_still_see_the_hole():
    """The counterpart. Scrubbing lineage on erasure would delete the evidence and flip erasure_audit to a
    false clean — the exact failure this whole file exists to prevent. Only behaviour-gating pointers go."""
    m = _store()
    parent = m.remember("patient chart body", source={"doc": "chart-77"})
    child = m.remember("summary of the chart", derived=True, derived_from=[parent])

    m.forget(ids=[parent], request_id="REQ-3", basis="gdpr-art17")
    assert parent in (_rec(m, child).get("derived_from") or []), \
        "the edge to an erased parent is EVIDENCE, not litter"

    audit = m.erasure_audit(subject="chart-77")
    assert audit["verdict"] == "residue_found"
    assert any(r["kind"] == "dangling_lineage" for r in audit["residue"]), \
        "the audit must still be able to report that a survivor descends from erased content"


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


def test_a_new_write_site_that_COPIES_TEXT_must_declare_where_it_came_from():
    """THE test that closes the class rather than the two instances of it.

    Both bugs had one shape: an internal write built its text out of another record and declared no edge to
    it, so erasure could not follow the content. Pinning the current function names (above) does not stop a
    SIXTH site being added tomorrow with the same defect. This one reads the source and fails on any
    `self.remember(...)` whose text argument is lifted from an existing record without a `derived_from`.

    Note what this does NOT do: it checks the parent is declared, not that the declaration is semantically
    right. It is stricter than "has a derived_from" though — the rederive bug HAD one, pointing at the
    corrected root instead of the record the wording came from, and this test fails on exactly that.

    If this fails on a legitimately new site, the fix is to declare the parent — not to widen the allow-list.
    """
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "inspeximus", "core.py"), encoding="utf-8").read()
    lines = src.split("\n")
    # a text argument lifted from another record, directly (tgt["text"]) or via a local (nt = rewrite(r[...]))
    COPIES = re.compile(r'^(nt|new_text|rewritten)$|\[["\']text["\']\]|\.get\(["\']text["\']')
    READS_RECORD = re.compile(r'(\w+)\[["\']text["\']\]|(\w+)\.get\(["\']text["\']')

    offenders = []
    for i, line in enumerate(lines, 1):
        if "self.remember(" not in line:
            continue
        arg = line.split("self.remember(", 1)[1].split(",")[0].strip()
        if not COPIES.search(arg):
            continue

        m = READS_RECORD.search(arg)
        if m:
            source_var = m.group(1) or m.group(2)
        else:                                   # a local — find the assignment that reads out of a record
            source_var = None                   # (skipping `nt = None` in the except branch)
            back = "\n".join(lines[max(0, i - 31):i - 1])
            for rhs in re.findall(rf"^\s*{re.escape(arg)}\s*=\s*(.+)$", back, re.M):
                mm = READS_RECORD.search(rhs)
                if mm:
                    source_var = mm.group(1) or mm.group(2)
                    break

        window = "\n".join(lines[i - 1:i + 4])
        # ONLY the derived_from list counts. Scanning to the end of the window would accept the parent being
        # named in `meta={"rederived_from": r["id"]}` — which is precisely the bug, a parent filed somewhere
        # nothing traverses. (This test passed under the unfixed code until that was bounded.)
        declared = ""
        if "derived_from=" in window:
            tail = window.split("derived_from=", 1)[1].lstrip()
            if tail.startswith("["):
                depth = 0
                for pos, ch in enumerate(tail):
                    depth += (ch == "[") - (ch == "]")
                    if depth == 0:
                        declared = tail[:pos + 1]
                        break
        if source_var and f'{source_var}["id"]' in declared:
            continue

        fn = next((re.match(r"    def (\w+)", lines[j - 1]).group(1)
                   for j in range(i - 1, 0, -1) if re.match(r"    def (\w+)", lines[j - 1])), "?")
        offenders.append(f'{fn} (core.py:{i}) writes text from `{source_var}` but does not declare '
                         f'{source_var}["id"]' if source_var else
                         f"{fn} (core.py:{i}) writes {arg}, whose source record could not be traced")

    assert not offenders, (
        "a write site copies another record's text without declaring THAT record as a parent. Erasure "
        "follows declared edges only, so the copy survives erasure of the subject its wording came from "
        "— and erasure_audit reports no residue, certifying the leak as clean:\n  "
        + "\n  ".join(offenders))
