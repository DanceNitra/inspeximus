"""`verify_writes` walked the receipts, so a record no receipt named was never looked at.

THE SHAPE. The check iterates `self._receipts` and looks each one's record up. A record PRESENT in
the store that no receipt mentions was not unchecked — it was UNCOUNTED. The attacker holds no key
and forges nothing: they APPEND, and the check is built to notice editing.

Measured 2026-08-15 on 2.10.1, with a positive control:

    append one JSON object to the store file
    verify_writes      -> (True, [])          and recall served the fabricated memory
    CONTROL: edit an EXISTING record's text -> (False, ['... no longer matches its write receipt'])

`verify_attribution` was the one surface that caught it, and ONE WORD defeated that too: its sweep
is `status == "active"`, while `_RECALLABLE` includes `superseded` and `as_of()`/`history()` serve
it. Same attack, one field different, opposite verdict from the only verifier that saw it. So the
sweep here runs over EVERY status.

THE SAME BLINDNESS, WEAPONISED: drop the trailing receipt and that record's text and value can then
be rewritten freely, because nothing names it any more — `verify_writes` went from catching the edit
to reporting clean. One sweep closes both.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from inspeximus import Inspeximus

FORGED = "the deploy key rotates every 3650 days"


def _mk(n=2):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.json")
    ix = Inspeximus(path=p, receipts=True)
    for i in range(n):
        ix.remember(f"honest record {i} about the deploy key", key=f"k{i}", object=f"v{i}")
    ix.flush()
    return p, ix


def _edit(p, fn):
    rows = json.load(open(p, encoding="utf-8"))
    fn(rows)
    json.dump(rows, open(p, "w", encoding="utf-8"), ensure_ascii=False)


@pytest.mark.parametrize("status", ["active", "superseded", "hub", "provisional"])
def test_an_injected_record_is_caught_whatever_status_it_wears(status):
    """Parametrised on the evasion, not on a guess about which status an attacker would pick. The
    `superseded` case is the measured one: it flipped verify_attribution from ok=False to ok=True
    while as_of() and history() still served the value."""
    p, _ = _mk()
    _edit(p, lambda rows: rows.append(
        {"id": "f0rged" + status[:4], "text": FORGED, "ts": rows[0]["ts"], "status": status,
         "mtype": "semantic", "key": "rot-legacy", "object": "3650d"}))
    ok, problems = Inspeximus(path=p, receipts=True).verify_writes()
    assert not ok and any("NO write receipt" in x for x in problems), problems


def test_dropping_a_receipt_no_longer_launders_an_edit():
    """The composition. Without the sweep this was: delete the trailing receipt, then rewrite that
    record's text AND value, and verify_writes goes from False to True."""
    p, _ = _mk(3)
    rp = p + ".receipts.json"
    rec = json.load(open(rp, encoding="utf-8"))
    rows = rec if isinstance(rec, list) else rec.get("receipts")
    victim = rows[-1]["memory_id"]
    del rows[-1]
    json.dump(rec, open(rp, "w", encoding="utf-8"))
    _edit(p, lambda rr: [r.update(text="rewritten out of band", object="evil")
                         for r in rr if r["id"] == victim])
    ok, problems = Inspeximus(path=p, receipts=True).verify_writes()
    assert not ok and any("NO write receipt" in x for x in problems), problems


def test_control_the_same_edit_with_the_receipt_left_in_place_was_always_caught():
    """POSITIVE CONTROL. If this did not fire, the test above would be showing that receipts are
    broken generally rather than that the sweep closed a specific hole."""
    p, _ = _mk(3)
    rows = json.load(open(p, encoding="utf-8"))
    victim = rows[-1]["id"]
    _edit(p, lambda rr: [r.update(text="rewritten out of band") for r in rr if r["id"] == victim])
    ok, problems = Inspeximus(path=p, receipts=True).verify_writes()
    assert not ok and any("write receipt" in x for x in problems), problems


def test_an_honest_store_stays_clean():
    """The must-not-cry-wolf control. A sweep that fires on every store closes nothing; it just
    trains the operator to ignore it."""
    _p, ix = _mk(6)
    assert ix.verify_writes() == (True, [])


def test_a_store_that_enabled_receipts_part_way_can_say_so():
    """The honest case this fires on, with a NAMED opt-out rather than a silent exemption -- the
    shape `legacy_strict` and `value_strict` already set."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.json")
    plain = Inspeximus(path=p)                       # receipts OFF: these get none
    plain.remember("an early record", key="e1")
    plain.flush()
    later = Inspeximus(path=p, receipts=True)
    later.remember("a later record", key="e2")
    later.flush()

    ok, problems = later.verify_writes()
    assert not ok and any("NO write receipt" in x for x in problems)
    assert any("coverage_strict=False" in x for x in problems), \
        "the message must name the remedy it wants"
    assert later.verify_writes(coverage_strict=False)[0] is True, \
        "the remedy the message names does not work"
