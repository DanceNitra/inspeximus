"""The value the store SERVES was outside every commitment.

`_write_commit` hashed text+key (`immutable_sha256`), mtype, and the canonical sources. Never `object` --
the field supersession, the echo guard, `revert()`, `check_conflict` and `_obj_sig` all treat as
authoritative. So:

    remember("retention policy is 90 days", key="policy::retention", object="90d")
    # edit rec["object"] to "30d" on disk
    verify_writes()            -> True
    audit-verify --store       -> "content checked ... VERDICT: PASS"
    store now serves           -> "30d"

Text and key were untouched, and nothing hashed the value. The receipts were faithful about everything
except the answer.

`value_sha256` is a SEPARATE commit field rather than `object` folded into `immutable_sha256`: changing
that hash would make every receipt ever written mismatch, so an upgrade would raise a tamper alarm on every
honest store. Pre-1.82 receipts simply lack it -- and are NAMED for it, because a record that cannot be
checked reporting exactly like one that passed is the defect this whole audit kept finding.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402
from inspeximus.audit_bundle import bind_content, build_bundle  # noqa: E402
from inspeximus.core import _canon, _sha256_hex  # noqa: E402


def _store(**kw):
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), receipts=True, **kw)


def _pre_182_commit(rec, *_later_args):
    """Exactly what 1.81.0 committed: no `value_sha256`.

    `*_later_args` swallows arguments the CALLER gained after 1.81 (2.10.2 passes `retires`). The
    point of this double is the SHAPE OF THE COMMIT DICT it returns, not the arity of the function
    -- an old version could not have received those arguments at all. Without this, adding a
    parameter to `_write_commit` breaks seven tests that have nothing to say about it.
    """
    return {"id": rec["id"],
            "content_sha256": _sha256_hex(_canon({"text": rec.get("text"), "key": rec.get("key"),
                                                  "mtype": rec.get("mtype")})),
            "immutable_sha256": _sha256_hex(_canon({"text": rec.get("text"), "key": rec.get("key")})),
            "mtype": rec.get("mtype"),
            "attrib_sha256": _sha256_hex(_canon(sorted(Inspeximus._rec_sources(rec))))}


def _legacy_store():
    """A store genuinely written by a pre-1.82 version -- its receipt HASHES were computed without the new
    field, so it cannot be faked by deleting a key (that would break the chain link, which is the point)."""
    new = Inspeximus.__dict__["_write_commit"]
    d = tempfile.mkdtemp()
    p = os.path.join(d, "m.json")
    try:
        Inspeximus._write_commit = staticmethod(_pre_182_commit)
        s = Inspeximus(path=p, receipts=True)
        rid = s.remember("retention policy is 90 days", key="policy::retention", object="90d")
        s.remember("a note carrying no value at all")
        s.flush()
    finally:
        Inspeximus._write_commit = new
    return Inspeximus(path=p, receipts=True), rid


def test_editing_the_served_value_is_now_caught():
    """THE defect. Text and key untouched; only the answer changed."""
    s = _store()
    rid = s.remember("retention policy is 90 days", key="policy::retention", object="90d")
    s.flush()
    assert s.verify_writes()[0] is True

    next(x for x in s._items if x["id"] == rid)["object"] = "30d"
    s._save(force=True)

    ok, problems = s.verify_writes()
    assert ok is False, "the store now serves 30d and the receipts said nothing"
    assert problems


def test_the_audit_bundle_binds_the_value_too():
    """The auditor's copy of the same question. bind_content compares only the fields a bundle carries, so
    this had to be added there as well or the fix would have survived one call site over -- the shape this
    repository meets most often."""
    s = _store()
    rid = s.remember("retention policy is 90 days", key="policy::retention", object="90d")
    s.flush()
    bundle = build_bundle(s)
    assert bind_content(bundle, list(s.items))["ok"] is True

    next(x for x in s._items if x["id"] == rid)["object"] = "30d"
    s._save(force=True)
    res = bind_content(bundle, list(s.items))
    assert res["ok"] is False, res
    assert any(m["field"] == "value_sha256" for m in res["mismatched"]), res["mismatched"]


def test_an_honest_store_does_not_alarm():
    """A commitment that fires on untouched data is worse than none."""
    s = _store()
    s.remember("retention policy is 90 days", key="policy::retention", object="90d")
    s.remember("a plain note")
    s.flush()
    assert s.verify_writes()[0] is True
    assert bind_content(build_bundle(s), list(s.items))["ok"] is True


def test_supersession_and_revert_still_verify():
    """The value is immutable PER RECORD; correcting a fact writes a new one. If that were not true, this
    commitment would alarm on ordinary use."""
    s = _store()
    s.remember("retention policy is 90 days", key="policy::retention", object="90d")
    s.remember("retention policy is 30 days", key="policy::retention", object="30d")
    s.flush()
    assert s.verify_writes()[0] is True
    s.submit_revert("restore:policy::retention=90d#a1b2c3")
    s.flush()
    assert s.verify_writes()[0] is True


# ── upgrading a store written before 1.82 ───────────────────────────────────────────────────────────
def test_a_pre_182_store_does_not_raise_a_false_alarm_about_text():
    s, rid = _legacy_store()
    assert "value_sha256" not in (s._receipts[0].get("commit") or {}), "fixture must be genuinely legacy"
    assert s.verify_writes(value_strict=False)[0] is True, \
        "an untouched legacy store must verify; an upgrade that alarms on honest data gets ignored"

    next(x for x in s._items if x["id"] == rid)["text"] = "retention policy is 30 days"
    s._save(force=True)
    assert s.verify_writes(value_strict=False)[0] is False, "text was always committed and still binds"


def test_a_pre_182_store_is_TOLD_its_values_are_not_covered():
    """The trap in my own fix: applying the new check only where the field happens to be present would
    make an unverifiable record read exactly like a verified one -- the defect this entire audit found six
    times. So it is named, on the same terms as the pre-1.68 case: fail closed, explain, offer the opt-out."""
    s, _ = _legacy_store()
    ok, problems = s.verify_writes()
    assert ok is False
    note = next(p for p in problems if "PRE-1.82" in p)
    assert "do not commit `object`" in note
    assert "value_strict=False" in note, "a warning with no way to act on it just gets silenced wholesale"

    assert s.verify_writes(value_strict=False)[0] is True, "and the opt-out must actually work"


def test_only_records_that_HAVE_a_value_are_flagged():
    """The legacy fixture holds two records and only one carries an `object`. Flagging the other would be
    a false alarm about a record with nothing to protect."""
    s, rid = _legacy_store()
    note = next(p for p in s.verify_writes()[1] if "PRE-1.82" in p)
    assert note.startswith("1 record(s)"), note
    assert rid in note


def test_the_remedy_the_message_names_actually_works():
    """The first version of that message said "re-writing a record upgrades its receipt". It does not:
    `slash()` appends a receipt only for a GRADUATED memory, so an ordinary record had no upgrade path at
    all and the advice was useless. `recommit()` was built rather than the sentence reworded."""
    s, rid = _legacy_store()
    assert s.verify_writes()[0] is False
    assert "recommit(ids=[...])" in next(p for p in s.verify_writes()[1] if "PRE-1.82" in p)

    res = s.recommit(ids=[rid])
    assert res["recommitted"] == [rid], res
    assert s.verify_writes()[0] is True

    latest = max((r for r in s._receipts if r["memory_id"] == rid), key=lambda r: r.get("seq", 0))
    assert "value_sha256" in (latest.get("commit") or {})


def test_recommit_is_idempotent_and_does_not_pad_the_chain():
    """A no-op that appends a receipt every time would turn the chain into noise and make growth
    unexplainable — explain_growth exists precisely to reconcile it."""
    s, rid = _legacy_store()
    s.recommit(ids=[rid])
    n = len(s._receipts)
    again = s.recommit(ids=[rid])
    assert again["recommitted"] == [] and again["skipped"] == [rid]
    assert len(s._receipts) == n


def test_recommit_binds_the_current_state_and_says_so():
    """Its honest scope, asserted rather than only documented: it commits what is there NOW. Run on an
    already-tampered record it makes the store verify clean, which is exactly why it takes explicit ids."""
    s, rid = _legacy_store()
    next(x for x in s._items if x["id"] == rid)["object"] = "30d"
    s._save(force=True)
    s.recommit(ids=[rid])
    assert s.verify_writes()[0] is True, "this is the documented consequence, not a bug"
    # Whitespace-normalised: a docstring wraps, and asserting on raw text made this fail once already for
    # no reason but a line break.
    import re
    assert "not a validation of the past" in re.sub(r"\s+", " ", Inspeximus.recommit.__doc__)

    # and from here on the value IS covered
    next(x for x in s._items if x["id"] == rid)["object"] = "7d"
    s._save(force=True)
    assert s.verify_writes()[0] is False


def test_recommit_touches_only_the_ids_it_was_given():
    s, rid = _legacy_store()
    other = next(r["id"] for r in s.items if r["id"] != rid)
    s.recommit(ids=[rid])
    covered = {r["memory_id"] for r in s._receipts
               if "value_sha256" in ((r.get("commit")) or {})}
    assert rid in covered and other not in covered


def test_recommit_cannot_reach_another_tenants_records():
    """A new public method reaching the shared store as tenant=None (admin) is how 54 of 79 methods once
    leaked. The isolation guard refused to let `recommit` exist unclassified, and this pins the resolution:
    it sweeps `_tenant_rows()`, so a tenant view can only ever re-commit its own."""
    parent = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), receipts=True)
    a, b = parent.for_tenant("A"), parent.for_tenant("B")
    a.remember("A's value", key="k", object="av")
    b.remember("B's value", key="k", object="bv")
    parent.flush()

    b_ids = {r["id"] for r in b._tenant_rows()}
    touched = set(a.recommit()["recommitted"]) | set(a.recommit()["skipped"])
    assert not (touched & b_ids), f"tenant A reached B's records: {touched & b_ids}"
    assert len(a._tenant_rows()) == 1 and len(parent.items) == 2
