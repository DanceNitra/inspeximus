"""`provenance()` and `verify_writes()` disagreed about the same store, and provenance was wrong.

provenance() answers "why do you hold this, and how far does that answer bind" -- so its integrity
block is read as a verdict. It compared `content_sha256`, the PRE-1.68 composite of text+key+mtype.
Since 1.68 the binding fields are `immutable_sha256`, `value_sha256`, `status_sha256`, `time_sha256`
and `attrib_sha256`, so editing `object` -- the value the store SERVES, and what supersession,
revert() and the echo guard all key on -- left provenance reporting `content_matches_receipt: True`
while verify_writes on the same store said False.

It also read `mine[-1]`, the LATEST receipt. verify_writes carries a long comment about why that is a
laundering path: tamper out of band, append a well-formed amendment, and the latest receipt commits
to the forged content. I could NOT make that land through this surface in the fixture I built, so it
is fixed as a structure rather than reported as a reproduced exploit -- the first receipt is what
binds, and later ones forgive only the fields they DECLARE.

THE TEST THAT MATTERS is the last one: the two surfaces must never disagree again, whatever field is
touched. Checking a list of fields I happened to think of is how the first version stayed wrong for
three releases.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from inspeximus import Inspeximus

MUTATIONS = {
    "object": lambda rows: rows[0].update(object="30d"),
    "text": lambda rows: rows[0].update(text="the retention policy is 3650 days"),
    "key": lambda rows: rows[0].update(key="other"),
    "valid_from": lambda rows: rows[0].update(valid_from=1.0),
    "status": lambda rows: rows[0].update(status="provisional"),
    "source": lambda rows: rows[0].update(source={"doc": "someone-else"}),
}


def _store():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.json")
    ix = Inspeximus(path=p, receipts=True)
    ix.remember("the retention policy is 90 days", key="ret", object="90d",
                source={"doc": "policy-handbook"})
    ix.flush()
    return p, ix.items[0]["id"]


def _tamper(mut):
    p, rid = _store()
    rows = json.load(open(p, encoding="utf-8"))
    mut(rows)
    json.dump(rows, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    ix = Inspeximus(path=p, receipts=True)
    return ix, rid


def test_an_untouched_store_is_clean_on_both_surfaces():
    p, rid = _store()
    ix = Inspeximus(path=p, receipts=True)
    g = ix.provenance(id=rid)["integrity"]
    assert g["content_matches_receipt"] is True and g["attribution_matches_receipt"] is True
    assert ix.verify_writes() == (True, [])


def test_editing_the_served_value_is_caught_by_provenance_too():
    """The measured disagreement: 90d -> 30d on disk, one surface caught it and the other did not."""
    ix, rid = _tamper(MUTATIONS["object"])
    g = ix.provenance(id=rid)["integrity"]
    assert g["content_matches_receipt"] is False
    assert g["content_mismatch_fields"] == ["value_sha256"], g


@pytest.mark.parametrize("field", sorted(MUTATIONS))
def test_the_two_surfaces_never_disagree(field):
    """THE test. Not "does provenance check value_sha256" -- that pins the instance. Every mutation
    that verify_writes reports must also make provenance report, and vice versa. A field added to the
    commitment in a year is covered by this without anyone remembering to come here."""
    ix, rid = _tamper(MUTATIONS[field])
    g = ix.provenance(id=rid)["integrity"]
    clean_here = g["content_matches_receipt"] and (g["attribution_matches_receipt"] is not False)
    clean_there = ix.verify_writes()[0]
    assert clean_here == clean_there, (
        f"editing `{field}`: provenance says clean={clean_here}, verify_writes says {clean_there}"
        f" -- integrity={g}")


def test_the_mismatching_field_is_named():
    """A verdict of "something no longer matches" sends an operator to diff everything. Both surfaces
    now say which field moved."""
    ix, rid = _tamper(MUTATIONS["valid_from"])
    assert ix.provenance(id=rid)["integrity"]["content_mismatch_fields"] == ["time_sha256"]


def test_a_declared_amendment_is_forgiven_exactly_once():
    """`slash()` legitimately rewrites `mtype` and declares it. Provenance must forgive that field and
    no other -- the same rule verify_writes enforces, rather than a second opinion about it."""
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    ix.remember("deployments always require two approvers", key="pol", object="two",
                source={"doc": "handbook"})
    rid = ix.items[0]["id"]
    ix.slash([rid], scope="memory", reason="reclassified after review")
    assert ix.provenance(id=rid)["integrity"]["content_matches_receipt"] is True
    assert ix.verify_writes() == (True, [])
