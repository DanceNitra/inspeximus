"""A record written through the product surface has to be reachable by the guarantees the product sells.

The MCP server is how any agent actually uses this library. Its `remember` took text, tags, value, mtype,
key, object, reaffirm and the three scope ids -- and NOT `source` or `derived_from`. Every compliance
guarantee rides on those two:

    forget_subject(subject)   selects by SOURCE, then cascades along derived_from
    erasure_audit(subject)    walks DECLARED derived_from edges; with none it returns `unaudited`
    slash(ids, scope=source)  forfeits the standing of a SOURCE

So a store written purely through the product surface answered `would_erase=0` to every right-to-erasure
request, while the identical write through the library answered 1. Measured, all three phrasings of the
subject, before the fix:

    mcp.remember("alice home address is 5 Elm St", key="addr::alice")
      forget_subject('alice')     would_erase=0
      forget_subject('hr/alice')  would_erase=0
      erasure_audit               verdict 'unaudited', declared_ratio 0.0
    library write with source={'doc': 'hr/alice'}
      forget_subject('hr/alice')  would_erase=1        <- the control that made the finding real

This is the same defect class as the rest of the audit -- a guarantee that reports cleanly about input it
never received -- one layer up, on the surface the product is used through. `erasure_audit` was not lying:
`unaudited` is the honest verdict for a store with no declared edges. The problem was that the product
surface could not produce any other kind of store.

The fix is additive: the parameters exist, and the write result says `attributable` so a caller who omits
them finds out at the moment they could still fix it -- while they still know where the text came from.
It is deliberately NOT a default subject invented by the server; erasing on a subject nobody supplied is
the failure this month already produced twice.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The MCP SDK is an optional extra. Without this guard the whole file ERRORS at setup on a base
# install -- which is exactly how this landed CI red: `mcp_server` raises ImportError by design, and a
# fixture that cannot build is a failure, not a skip.
pytest.importorskip("mcp")

from inspeximus import Inspeximus  # noqa: E402


@pytest.fixture()
def mcp(monkeypatch):
    """A fresh server bound to a throwaway store; the module reads the path at import."""
    monkeypatch.setenv("INSPEXIMUS_PATH", os.path.join(tempfile.mkdtemp(), "m.json"))
    import inspeximus.mcp_server as m
    return importlib.reload(m)


def _store():
    return Inspeximus(path=os.environ["INSPEXIMUS_PATH"], receipts=True)


def test_a_subject_written_through_mcp_can_be_erased(mcp):
    """THE defect: this was 0 for every phrasing of the subject."""
    mcp.remember("alice home address is 5 Elm St", key="addr::alice",
                 object="5 Elm St", source="hr/alice")
    res = _store().forget_subject("hr/alice", request_id="dsar", basis="art17", dry_run=True)
    assert res["would_erase"] == 1


def test_the_lineage_cascade_reaches_derived_records_written_through_mcp(mcp):
    """A summary built from a person's file must go when their file goes -- through this surface too."""
    parent = mcp.remember("alice home address is 5 Elm St", key="addr::alice",
                          object="5 Elm St", source="hr/alice")
    mcp.remember("summary of alice file", source="summary-svc", derived_from=[parent["id"]])
    res = _store().forget_subject("hr/alice", request_id="dsar", basis="art17", dry_run=True)
    assert (res["would_erase"], res["direct"], res["inherited"]) == (2, 1, 1)


def test_the_audit_can_reach_a_verdict_other_than_unaudited(mcp):
    """With no declared edges the audit reports `unaudited` -- correctly. The defect was that the
    product surface could not produce a store where it says anything else."""
    parent = mcp.remember("alice file", source="hr/alice")
    mcp.remember("summary", source="summary-svc", derived_from=[parent["id"]])
    aud = _store().erasure_audit("hr/alice")
    assert aud["coverage"]["declared_ratio"] > 0
    assert aud["verdict"] != "unaudited"


def test_a_write_without_a_source_is_still_unreachable(mcp):
    """CONTROL, and the design decision. The hole closes by letting the caller SUPPLY a subject, never
    by the server inventing one -- erasing on a subject nobody supplied is the defect this month
    already produced twice."""
    mcp.remember("bob home address is 9 Oak St", key="addr::bob", object="9 Oak St")
    res = _store().forget_subject("hr/bob", request_id="d", basis="art17", dry_run=True)
    assert res["would_erase"] == 0


def test_a_ghost_subject_still_reaches_nothing(mcp):
    """CONTROL. Making records reachable must not make them reachable by the wrong subject."""
    mcp.remember("alice file", source="hr/alice")
    res = _store().forget_subject("hr/nobody-here", request_id="g", basis="art17", dry_run=True)
    assert res["would_erase"] == 0


def test_the_write_result_says_whether_the_record_is_attributable(mcp):
    """The caller is the only one who can still fix an unattributable write, and only at the moment of
    writing -- afterwards nobody knows where the text came from. So the result has to say it."""
    assert mcp.remember("with a source", source="hr/alice")["attributable"] is True
    parent = mcp.remember("p", source="hr/alice")
    assert mcp.remember("derived only", derived_from=[parent["id"]])["attributable"] is True
    assert mcp.remember("no provenance at all")["attributable"] is False


# The CLI arms that used to live here have moved to tests/test_cli_provenance.py. They need nothing
# optional, and the `importorskip("mcp")` added at the top of this file to stop the base CI job erroring
# was silently skipping them too -- a fix for one thing narrowing coverage of another. A guard belongs to
# the dependency it guards.


def test_the_result_reports_back_the_provenance_it_recorded(mcp):
    """A surviving mutation found this gap: the result carried `source` and `derived_from` and nothing
    asserted either matched what was passed, so the fields could have been emptied silently.

    They are not decoration. An agent that writes a summary and reads `derived_from` back is confirming
    the lineage edge exists -- the edge the whole erasure cascade travels. A result that reports `[]`
    while the record holds a parent tells the caller the cascade will not reach it when it will, and the
    reverse is worse."""
    parent = mcp.remember("alice file", source="hr/alice")
    child = mcp.remember("summary", source="summary-svc", derived_from=[parent["id"]])
    assert child["derived_from"] == [parent["id"]]
    assert child["source"] == "summary-svc"
    rec = next(r for r in _store().items if r["id"] == child["id"])
    assert rec["derived_from"] == [parent["id"]], "the result must match what was actually stored"
    # CONTROL: absent provenance must report as absent, not as an empty-but-present claim.
    plain = mcp.remember("no provenance")
    assert plain["derived_from"] == [] and plain["source"] is None


def test_the_source_is_stored_in_the_shape_the_erasure_paths_read(mcp):
    """A string passed through unchanged would be stored as a bare source, not {'doc': ...}, and the
    subject resolver reads the doc field -- so this is the join that makes the rest work."""
    mcp.remember("alice file", source="hr/alice")
    rec = next(r for r in _store().items if "alice file" in (r.get("text") or ""))
    assert rec["source"] == {"doc": "hr/alice"}
