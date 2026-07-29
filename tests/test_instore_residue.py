"""An erasure now says whether a SURVIVING record still holds what it just erased.

Measured before this existed:

    remember("alice home address is 5 Elm St", source={"doc": "hr/alice"})
    remember("summary: she lives at 5 Elm St", source={"doc": "svc"})   # not attributable, not derived
    forget_subject("hr/alice")  ->  {"erased": 1, ...}

The survivor holds the erased address verbatim and the erasure had nothing to say about it. `scan_residue`
answered this question for OTHER stores on disk; nothing answered it for THIS one.

IT HAS TO HAPPEN AT ERASURE TIME. Tombstones are content-free by design -- a hash of PII is still PII --
so the values vanish with the rows and a check bolted on afterwards has nothing left to compare. That is
also why `erasure_audit` could never have covered this: by the time it runs, the evidence is gone.

Two properties matter as much as the finding itself, and both are pinned below: the report carries a
FINGERPRINT and never the value (a compliance report gets pasted into tickets), and a search that compared
nothing reports `ok: False`, because an empty search is not a clean result.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402


def _store():
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), receipts=True)


def test_a_survivor_holding_the_erased_value_is_reported():
    """THE case. The row went and the string did not."""
    m = _store()
    m.remember("alice home address is 5 Elm St", key="a::addr", object="5 Elm St",
               source={"doc": "hr/alice"})
    m.remember("summary: she lives at 5 Elm St", source={"doc": "svc"})
    r = m.forget_subject("hr/alice", request_id="D", basis="art17")["residue_in_store"]
    assert r["ok"] is False
    assert len(r["findings"]) == 1
    assert r["findings"][0]["field"] == "text"
    assert r["problems"], "a finding with no explanation is a number without a claim"


def test_a_clean_erasure_reports_clean():
    """CONTROL. A field that says 'incomplete' on every erasure carries no information."""
    m = _store()
    m.remember("alice home address is 5 Elm St", key="a::addr", object="5 Elm St",
               source={"doc": "hr/alice"})
    m.remember("weather is fine", source={"doc": "svc"})
    r = m.forget_subject("hr/alice", request_id="D", basis="art17")["residue_in_store"]
    assert r["ok"] is True and r["findings"] == []


def test_the_report_never_echoes_the_value():
    """A compliance report is pasted into tickets and logs. Reintroducing the erased string there would
    undo part of the erasure the report is certifying."""
    m = _store()
    m.remember("alice ssn is 123-45-6789", key="a::ssn", object="123-45-6789",
               source={"doc": "hr/alice"})
    m.remember("note: her ssn 123-45-6789 was verified", source={"doc": "svc"})
    r = m.forget_subject("hr/alice", request_id="D", basis="art17")["residue_in_store"]
    assert r["findings"], "this test is meaningless unless something was actually found"
    assert "123-45-6789" not in json.dumps(r)
    assert len(r["findings"][0]["fingerprint"]) == 12


def test_a_search_that_compared_nothing_is_not_clean():
    """CONTROL, and the discipline the sibling scanner already had: values under 4 characters are
    skipped (else 'ok' matches everywhere), and skipping them all means nothing was compared."""
    m = _store()
    m.remember("x", key="k", object="ok", source={"doc": "hr/alice"})
    m.remember("this record says ok and x a lot", source={"doc": "svc"})
    r = m.forget_subject("hr/alice", request_id="D", basis="art17")["residue_in_store"]
    assert r["searched_values"] == 0
    assert r["ok"] is False
    assert "not a clean result" in " ".join(r["problems"])


def test_the_check_is_labelled_a_heuristic():
    """A paraphrase carries the fact without the string. A clean result here is evidence, not proof, and
    the surface has to say so where the caller reads it."""
    m = _store()
    m.remember("alice home address is 5 Elm St", key="a", object="5 Elm St",
               source={"doc": "hr/alice"})
    r = m.forget_subject("hr/alice", request_id="D", basis="art17")["residue_in_store"]
    assert "not proof" in r["method"]


def test_every_erasure_path_carries_a_REAL_report():
    """`coverage` had to be carried up by hand to each sibling this morning, and this is the same shape.
    A field that is sometimes absent reads as 'nothing to report' rather than 'nobody looked'.

    ASSERTS CONTENT, NOT PRESENCE. The first version checked only `"residue_in_store" in out`, and a
    mutation that set the field to None SURVIVED -- the key was still there. Every path is given a store
    where residue genuinely exists, so each one has to come back with the finding, not with a shape.

    The `forget_pii` arm uses an SSN, not a street address: `forget_pii` selects on DETECTED PII TYPE,
    not on the `pii=True` flag, so an address it does not recognise made it erase nothing and the arm
    tested the empty-store branch instead of the one it was written for. Each path has to be handed
    input it can actually act on, or the test measures the fixture."""
    for call in ("forget_subject", "forget", "forget_pii"):
        m = _store()
        secret = "123-45-6789" if call == "forget_pii" else "5 Elm St"
        rid = m.remember(f"alice detail is {secret}", key="a::d", object=secret,
                         source={"doc": "hr/alice"}, pii=True)
        m.remember(f"summary: hers is {secret}", source={"doc": "svc"})
        if call == "forget_subject":
            out = m.forget_subject("hr/alice", request_id="D", basis="art17")
        elif call == "forget":
            out = m.forget(ids=[rid], request_id="D", basis="art17")
        else:
            out = m.forget_pii(request_id="D", basis="art17")
        assert (out.get("erased") or out.get("forgotten")), f"{call} erased nothing; fixture is wrong"
        r = out.get("residue_in_store")
        assert isinstance(r, dict), f"{call} does not carry a report ({r!r})"
        assert r["ok"] is False, f"{call} reported clean while the survivor holds the erased value"
        assert r["findings"], f"{call} carried an empty report"


def test_a_path_that_erased_nothing_still_carries_the_field():
    """CONTROL, and the honest distinction: nothing erased means no values existed to search for, which
    is not the same as 'we searched and found nothing'. The field is still present so the caller never
    has to branch on its absence."""
    m = _store()
    m.remember("weather is fine", source={"doc": "svc"})
    out = m.forget_subject("hr/nobody-here", request_id="D", basis="art17")
    r = out["residue_in_store"]
    assert out["erased"] == 0
    assert r["ok"] is True and r["searched_values"] == 0
    assert "nothing was erased" in r["method"]


def test_a_bounded_scan_says_it_was_bounded():
    """Silent truncation would turn a partial scan into a clean report -- the exact defect this surface
    exists to avoid."""
    from inspeximus.erasure_residue import scan_records
    recs = [{"id": str(i), "text": f"record {i}"} for i in range(50)]
    r = scan_records(recs, ["nothing-matches-this"], max_pairs=5)
    assert r["checked_records"] < 50
    assert any("NOT examined" in p for p in r["problems"])
